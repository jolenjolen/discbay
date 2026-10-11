"""discbay-ctl: talk to discbayd from the terminal (or from any frontend).

  discbay-ctl state                 print the current state as JSON
  discbay-ctl watch                 print every state change as one JSON line
  discbay-ctl cancel | start | trust | eject | reload
  discbay-ctl probe [DEVICE]        read the disc in the drive and show what DiscBay sees
  discbay-ctl burn ...              burn a launcher disc you can rewrite later (see burn -h)
  discbay-ctl make-disc ...         build a launcher ISO file instead (see make-disc -h)
  discbay-ctl trusted               list discs you've trusted
  discbay-ctl untrust FINGERPRINT   forget one
  discbay-ctl sim insert LABEL [--manifest FILE] / sim remove   (daemon run with --simulate)

`watch` is the escape hatch for frontends that don't speak D-Bus: anything
that can read lines from a subprocess (eww, Quickshell, AGS, a shell script,
a Rust app...) can build a UI on top of it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import client, discmaker, drive, protocol as P, reader  # noqa: E402
from .config import ConfigError, cache_dir, parse_manifest  # noqa: E402
from .launch import LaunchError  # noqa: E402
from .trust import TrustStore  # noqa: E402

def _bus() -> Gio.DBusConnection:
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def call(method: str, args: GLib.Variant | None = None):
    try:
        reply = _bus().call_sync(P.BUS_NAME, P.OBJECT_PATH, P.INTERFACE, method, args,
                                 None, Gio.DBusCallFlags.NONE, 3000, None)
    except GLib.Error as exc:
        msg = exc.message
        if "ServiceUnknown" in msg or "was not provided" in msg:
            msg = "discbayd is not running (start it with: systemctl --user start discbayd)"
        sys.exit(f"discbay-ctl: {msg}")
    return reply.unpack() if reply is not None else None


# ------------------------------------------------------------------ watch
def cmd_watch() -> int:
    bus = _bus()

    def on_signal(conn, sender, path, iface, signal_name, params):
        print(params.unpack()[0], flush=True)

    bus.signal_subscribe(None, P.INTERFACE, "StateChanged", P.OBJECT_PATH, None,
                         Gio.DBusSignalFlags.NONE, on_signal)
    # Start with a full snapshot so the consumer never begins blind.
    print(call("GetState")[0], flush=True)
    try:
        GLib.MainLoop().run()
    except KeyboardInterrupt:
        pass
    return 0


# ------------------------------------------------------------------ probe
def cmd_probe(device: str) -> int:
    device = os.path.expanduser(device)
    status = drive.READY if os.path.isfile(device) else drive.drive_status(device)  # .iso files work too
    if status == drive.UNAVAILABLE:
        sys.exit(f"discbay-ctl: cannot open {device} (no drive, or no permission)")
    if status != drive.READY:
        sys.exit(f"discbay-ctl: drive is {status}; insert a disc and wait for it to spin up")
    info = reader.read_disc(device)
    if info.kind == "blank":
        print("# This disc is blank. Turn it into a launcher disc:")
        print("#   discbay-ctl make-disc --label MY_APP --name \"My App\" --app org.kde.kate")
        print(f"#   xorriso -as cdrecord -v dev={device} -eject my_app.iso")
        return 1
    print(f"# kind:  {info.kind}")
    print(f"# label: {info.label or '(none)'}")
    if info.manifest_error:
        print(f"# manifest: INVALID - {info.manifest_error}")
    elif info.manifest is not None:
        disc = info.manifest
        print(f"# manifest: {disc.name} -> {disc.launch.describe() if disc.launch else 'nothing to launch'}")
        print(f"# fingerprint: {disc.fingerprint}")
        return 0
    if not info.label:
        print("# This disc has no label, so DiscBay cannot identify it. Make one with: discbay-ctl make-disc")
        return 1
    print("# No manifest on the disc. Register it locally by adding this to discs.toml:")
    print(f'[discs."{info.label}"]')
    print('name = "My App"')
    print('app = "org.kde.kate"        # or exec / steam / url / command')
    return 0


# ------------------------------------------------------------------ trust list
def cmd_trusted() -> int:
    entries = TrustStore().all()
    if not entries:
        print("No trusted discs yet.")
        return 0
    for fingerprint, entry in sorted(entries.items(), key=lambda kv: kv[1].get("trusted_at", "")):
        print(f"{fingerprint}  {entry.get('name')}  [{entry.get('label')}]  runs: {entry.get('launch')}"
              f"  (since {entry.get('trusted_at')})")
    return 0


def cmd_untrust(fingerprint: str) -> int:
    if TrustStore().remove(fingerprint):
        print("Removed. The disc will ask again next time it's inserted.")
        return 0
    sys.exit("discbay-ctl: no trusted disc with that fingerprint (see: discbay-ctl trusted)")


# ------------------------------------------------------------------ make-disc
def spec_from_args(args) -> discmaker.DiscSpec:
    for kind in ("app", "exec", "steam", "url", "command"):
        value = getattr(args, kind)
        if value is not None:
            break
    return discmaker.DiscSpec(
        label=args.label, name=args.name, target_kind=kind, target=str(value),
        args=args.arg or [], subtitle=args.subtitle, color=args.color, art_path=args.art,
        countdown=args.countdown, close_on_eject=args.close_on_eject, autostart=not args.no_autostart)


def cmd_make_disc(args) -> int:
    spec = spec_from_args(args)
    out = Path(args.output or f"{spec.label.lower()}.iso").expanduser()
    try:
        disc, warnings = discmaker.make_iso(spec, out)
    except discmaker.DiscError as exc:
        sys.exit(f"discbay-ctl: {exc}")
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"Built {out}  ({out.stat().st_size // 1024} KB)")
    print(f"Disc runs: {disc.launch.describe() if disc.launch else 'nothing'}")
    print("\nTest it without burning:")
    print(f"  discbay-ctl sim insert {spec.label} --iso {out}     (daemon run with --simulate)")
    print("To burn a disc you can rewrite later, use burn with the same options:")
    print("  discbay-ctl burn --label ... --name ... --app ...")
    return 0


def cmd_burn(args) -> int:
    """Burn straight to the drive as a new session, leaving the disc open for rewrites."""
    spec = spec_from_args(args)
    media = discmaker.describe_media(args.device)
    print(("Adding a new session to the disc" if media["state"] == "appendable" else "Burning a new disc")
          + (" and closing it for good" if args.final else " (it stays rewritable)") + "...")
    printer = print if args.verbose else None
    try:
        with client.hold_drive(exclusive=True):  # daemon pauses autostart and drive polling
            result = discmaker.burn(spec, args.device, final=args.final, on_line=printer)
    except discmaker.DiscError as exc:
        sys.exit(f"discbay-ctl: {exc}")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"Done. The disc now launches: {result.disc.launch.describe() if result.disc.launch else 'nothing'}")
    if result.final:
        print("The disc is closed and can't be written again.")
    elif result.free_bytes is not None:
        print(f"Space left: {result.free_bytes // (1024 * 1024)} MB, "
              f"enough for about {result.rewrites_left} more rewrites.")
    if result.updated:
        print("Because the disc changed, DiscBay will ask you to trust it again on next insert.")
    return 0


# ------------------------------------------------------------------ sim
def cmd_sim_insert(label: str, manifest_path: str | None, iso_path: str | None) -> int:
    text, base = "", ""
    if iso_path:
        image = str(Path(iso_path).expanduser())
        label = label or (drive.read_label(image) or "")
        raw = drive.read_root_file(image, "discbay.toml")
        text = raw.decode("utf-8", errors="replace") if raw else ""
        if raw:
            # Unpack the art next to an unchanged copy of the manifest, so the
            # simulated disc has exactly the fingerprint the burned one will.
            try:
                disc = parse_manifest(raw, label or None)
            except ConfigError:
                disc = None
            if disc is not None and disc.art:
                art_name = os.path.basename(disc.art)
                data = drive.read_root_file(image, art_name)
                if data:
                    folder = cache_dir() / "sim" / disc.fingerprint
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / art_name).write_bytes(data)
                    base = str(folder)
    elif manifest_path:
        path = Path(manifest_path).expanduser()
        text, base = path.read_text(encoding="utf-8"), str(path.parent.resolve())
    call("SimulateInsert", GLib.Variant("(sss)", (label, text, base)))
    return 0


# ------------------------------------------------------------------ main
def add_disc_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--label", required=True, help="volume label, e.g. KATE_01 (A-Z 0-9 _)")
    p.add_argument("--name", required=True, help="name shown in the island")
    p.add_argument("--subtitle")
    p.add_argument("--color", help='disc colour, e.g. "#3daee9"')
    p.add_argument("--art", help="cover image (png/jpg/webp/svg) stored on the disc")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--app", help="desktop app ID, e.g. org.kde.kate or org.blender.Blender")
    target.add_argument("--exec", help="program file on this computer, e.g. ~/Apps/Foo.AppImage")
    target.add_argument("--steam", type=int, help="Steam app ID")
    target.add_argument("--url", help="URL or file to open with the default app")
    target.add_argument("--command", help="full command line")
    p.add_argument("--arg", action="append", help="argument for --app/--exec (repeatable)")
    p.add_argument("--countdown", type=float)
    p.add_argument("--close-on-eject", action="store_true", help="close the app when the disc is removed")
    p.add_argument("--no-autostart", action="store_true", help="show a Start button instead of a countdown")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="discbay-ctl", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("state", "watch", "cancel", "start", "trust", "eject", "reload", "trusted"):
        sub.add_parser(name)
    un = sub.add_parser("untrust")
    un.add_argument("fingerprint")
    p = sub.add_parser("probe")
    p.add_argument("device", nargs="?", default="/dev/sr0")

    md = sub.add_parser("make-disc", help="build an ISO with a DiscBay manifest",
                        description="Build a launcher disc image. Pick exactly one launch target.")
    add_disc_options(md)
    md.add_argument("-o", "--output", help="output .iso path (default: <label>.iso)")

    bn = sub.add_parser("burn", help="burn a launcher disc you can rewrite later",
                        description="Burn straight to the drive. The disc stays open, so running burn "
                                    "again on the same disc replaces what it launches (each rewrite "
                                    "uses about 13 MB of a CD-R). Pick exactly one launch target.")
    add_disc_options(bn)
    bn.add_argument("--device", default="/dev/sr0")
    bn.add_argument("--final", action="store_true", help="close the disc so it can never be changed")
    bn.add_argument("-v", "--verbose", action="store_true", help="show all of xorriso's output")

    s = sub.add_parser("sim")
    s_sub = s.add_subparsers(dest="action", required=True)
    s_ins = s_sub.add_parser("insert")
    s_ins.add_argument("label", nargs="?", default="")
    group = s_ins.add_mutually_exclusive_group()
    group.add_argument("--manifest", help="pretend the disc carries this discbay.toml")
    group.add_argument("--iso", help="pretend this .iso image was inserted")
    s_sub.add_parser("remove")
    args = ap.parse_args(argv)

    if args.cmd == "state":
        print(json.dumps(json.loads(call("GetState")[0]), indent=2))
    elif args.cmd == "watch":
        return cmd_watch()
    elif args.cmd == "probe":
        return cmd_probe(args.device)
    elif args.cmd == "make-disc":
        return cmd_make_disc(args)
    elif args.cmd == "burn":
        return cmd_burn(args)
    elif args.cmd == "trusted":
        return cmd_trusted()
    elif args.cmd == "untrust":
        return cmd_untrust(args.fingerprint)
    elif args.cmd == "sim":
        if args.action == "insert":
            return cmd_sim_insert(args.label, args.manifest, args.iso)
        call("SimulateRemove")
    else:
        call({"cancel": "Cancel", "start": "LaunchNow", "trust": "Trust", "eject": "Eject",
              "reload": "ReloadConfig"}[args.cmd])
    return 0


if __name__ == "__main__":
    sys.exit(main())
