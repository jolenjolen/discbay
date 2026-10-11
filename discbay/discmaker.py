"""Making launcher discs: manifests, ISO images and burning.

Shared by `discbay-ctl` and DiscBay Studio, so both behave identically.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import drive
from .config import ConfigError, Disc, parse_manifest
from .launch import KINDS, LaunchError

LABEL_RE = re.compile(r"^[A-Z0-9_]{1,32}$")
SESSION_OVERHEAD = 6900 * 2048    # lead-in + lead-out of one extra CD-R session, about 13 MB
COVER_NAMES = ("COVER.PNG", "COVER.JPG", "COVER.WEBP", "COVER.SVG")


class DiscError(Exception):
    """Something the user needs to fix (bad label, closed disc, missing tool...)."""


@dataclass
class DiscSpec:
    label: str
    name: str
    target_kind: str                 # one of launch.KINDS
    target: str
    args: list[str] = field(default_factory=list)
    subtitle: str | None = None
    color: str | None = None
    art_path: str | None = None      # image file on this computer, copied onto the disc
    countdown: float | None = None
    close_on_eject: bool = False
    autostart: bool = True


def label_from_name(name: str) -> str:
    """"OpenTTD 14" -> "OPENTTD_14", a valid volume label."""
    label = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
    return (label or "DISC")[:32]


def art_filename(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return "COVER.JPG" if ext in (".jpg", ".jpeg") else f"COVER{ext.upper()}"


def _toml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # JSON strings are valid TOML basic strings


def build_manifest(spec: DiscSpec) -> str:
    lines = [f"name = {_toml_str(spec.name)}"]
    if spec.subtitle:
        lines.append(f"subtitle = {_toml_str(spec.subtitle)}")
    if spec.color:
        lines.append(f"color = {_toml_str(spec.color)}")
    if spec.art_path:
        lines.append(f"art = {_toml_str(art_filename(spec.art_path))}")
    if spec.target_kind == "steam":
        lines.append(f"steam = {int(spec.target)}")
    else:
        lines.append(f"{spec.target_kind} = {_toml_str(spec.target)}")
    if spec.args:
        lines.append("args = [" + ", ".join(_toml_str(a) for a in spec.args) + "]")
    if spec.countdown is not None:
        lines.append(f"countdown = {spec.countdown:g}")
    if spec.close_on_eject:
        lines.append('on_eject = "close"')
    if not spec.autostart:
        lines.append("autostart = false")
    return "\n".join(lines) + "\n"


def prepare(spec: DiscSpec) -> tuple[Disc, str, list[str]]:
    """Validate a spec. Returns (parsed disc, manifest text, warnings)."""
    spec.label = spec.label.upper()
    if not LABEL_RE.match(spec.label):
        raise DiscError("The label must be 1-32 characters of A-Z, 0-9 or _")
    if not spec.name.strip():
        raise DiscError("Give the disc a name")
    if spec.target_kind not in KINDS:
        raise DiscError(f"Unknown launch type {spec.target_kind!r}")
    if spec.target_kind == "steam" and not str(spec.target).isdigit():
        raise DiscError("A Steam app ID is a number")
    if spec.art_path and not os.path.isfile(os.path.expanduser(spec.art_path)):
        raise DiscError(f"Cover image {spec.art_path} not found")
    if spec.args and spec.target_kind not in ("app", "exec"):
        raise DiscError("Arguments only work with an app or a program file")

    manifest = build_manifest(spec)
    try:
        disc = parse_manifest(manifest.encode(), spec.label)
    except ConfigError as exc:
        raise DiscError(str(exc)) from exc

    warnings = []
    # Warn, don't fail: the disc may be meant for another computer.
    if disc.launch is not None:
        try:
            disc.launch.to_argv()
        except LaunchError as exc:
            warnings.append(f"On this computer, {exc}")
    return disc, manifest, warnings


def stage(folder: Path, spec: DiscSpec, manifest: str) -> None:
    (folder / "DISCBAY.TOML").write_text(manifest, encoding="utf-8")
    (folder / "README.TXT").write_text(
        f"{spec.name}\n\nThis is a DiscBay launcher disc. It holds no software, only a\n"
        "pointer to an app installed on your computer.\n", encoding="utf-8")
    if spec.art_path:
        shutil.copyfile(os.path.expanduser(spec.art_path), folder / art_filename(spec.art_path))


def build_iso(folder: Path, label: str, out: Path) -> None:
    for tool in ("xorriso", "genisoimage", "mkisofs"):
        exe = shutil.which(tool)
        if not exe:
            continue
        cmd = [exe, "-as", "mkisofs"] if tool == "xorriso" else [exe]
        cmd += ["-quiet", "-V", label, "-R", "-J", "-o", str(out), str(folder)]
        subprocess.run(cmd, check=True, capture_output=True)
        return
    try:
        import pycdlib  # optional pure-Python fallback
    except ImportError:
        raise DiscError("No ISO tool found. On Fedora: sudo dnf install xorriso") from None
    iso = pycdlib.PyCdlib()
    iso.new(vol_ident=label, joliet=3, rock_ridge="1.09")
    for file in sorted(folder.iterdir()):
        stem, ext = os.path.splitext(file.name)
        iso.add_file(str(file), f"/{stem[:8].upper()}{ext[:4].upper()};1",
                     joliet_path=f"/{file.name}", rr_name=file.name)
    iso.write(str(out))
    iso.close()


def make_iso(spec: DiscSpec, out: Path) -> tuple[Disc, list[str]]:
    disc, manifest, warnings = prepare(spec)
    with tempfile.TemporaryDirectory(prefix="discbay-") as tmp:
        stage(Path(tmp), spec, manifest)
        build_iso(Path(tmp), spec.label, out)
    return disc, warnings


# ---------------------------------------------------------------- burning
@dataclass
class BurnResult:
    disc: Disc
    updated: bool                  # True when a new session was added to an existing disc
    final: bool
    free_bytes: int | None
    warnings: list[str]

    @property
    def rewrites_left(self) -> int | None:
        if self.final or self.free_bytes is None:
            return None
        return max(0, self.free_bytes // (SESSION_OVERHEAD + 1024 * 1024))


def _is_image(device: str) -> bool:
    return os.path.isfile(device) or not os.path.exists(device)


def describe_media(device: str) -> dict:
    """What's in the drive, as far as burning goes. Never raises."""
    device = os.path.expanduser(device)
    if _is_image(device):
        exists = os.path.exists(device)
        return {"present": True, "writable": True, "state": "appendable" if exists else "blank",
                "label": drive.read_label(device) if exists else None,
                "discbay": bool(exists and drive.read_root_file(device, "discbay.toml"))}
    status = drive.drive_status(device)
    if status == drive.UNAVAILABLE:
        return {"present": False, "writable": False, "state": "no-drive", "label": None, "discbay": False}
    if status != drive.READY:
        return {"present": status == drive.SPINNING, "writable": False,
                "state": "spinning" if status == drive.SPINNING else "empty", "label": None, "discbay": False}
    state = drive.media_state(device)          # blank / appendable / complete / None (pressed or unknown)
    label = drive.read_label(device) if state != "blank" else None
    return {"present": True, "writable": state in ("blank", "appendable"), "state": state or "complete",
            "label": label, "discbay": bool(label and drive.read_root_file(device, "discbay.toml"))}


def media_space(device: str) -> int | None:
    """Free bytes on a writable disc, asked from xorriso (spins the drive briefly)."""
    xorriso = shutil.which("xorriso")
    if not xorriso or _is_image(device):
        return None
    try:
        out = subprocess.run([xorriso, "-outdev", device, "-tell_media_space"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"Media space\s*:\s*(\d+)s", out.stdout + out.stderr)
    return int(match.group(1)) * 2048 if match else None


def burn(spec: DiscSpec, device: str = "/dev/sr0", final: bool = False,
         on_line: Callable[[str], None] | None = None) -> BurnResult:
    """Burn (or rewrite) a launcher disc as a new session. Leaves it open unless final."""
    xorriso = shutil.which("xorriso")
    if not xorriso:
        raise DiscError("Burning needs xorriso. On Fedora: sudo dnf install xorriso")
    disc, manifest, warnings = prepare(spec)
    device = os.path.expanduser(device)
    is_image = _is_image(device)

    state = None if is_image else drive.media_state(device)
    if state == "complete":
        raise DiscError("This disc is closed (finalised), so it can't be written again. "
                        "Use a blank CD-R, or wipe a CD-RW with: "
                        f"xorriso -outdev {device} -blank as_needed")

    existing = drive.root_names(device) if (is_image and os.path.exists(device)) or state == "appendable" else []
    updating = bool(existing)

    with tempfile.TemporaryDirectory(prefix="discbay-") as tmp:
        folder = Path(tmp)
        stage(folder, spec, manifest)
        new_names = {p.name.upper() for p in folder.iterdir()}

        # Abort before anything is written if xorriso hits even a minor problem:
        # a mistake must never cost a session on a write-once disc.
        cmd = [xorriso, "-abort_on", "SORRY", "-dev", f"stdio:{device}" if is_image else device,
               "-joliet", "on", "-volid", spec.label]
        # Drop cover art the new version no longer uses (other files on the disc are left alone).
        for name in existing:
            if name.upper() in COVER_NAMES and name.upper() not in new_names:
                cmd += ["-rm", f"/{name}", "--"]  # -rm takes a list, ended by "--"
        for path in sorted(folder.iterdir()):
            cmd += ["-map", str(path), f"/{path.name}"]
        cmd += ["-close", "on" if final else "off", "-commit", "-tell_media_space"]
        if not is_image:
            cmd += ["-eject", "all"]

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        output = []
        for line in proc.stdout:
            line = line.rstrip("\n")
            output.append(line)
            if on_line:
                on_line(line)
        proc.wait()

    free_sectors = None
    for line in output:
        match = re.search(r"Media space\s*:\s*(\d+)s", line)
        if match:
            free_sectors = int(match.group(1))
    if proc.returncode != 0:
        tail = "\n".join(l for l in output if "SORRY" in l or "FAILURE" in l or "FATAL" in l)[-600:]
        raise DiscError("Burning failed. " + (tail or "\n".join(output[-6:])))
    return BurnResult(disc=disc, updated=updating, final=final,
                      free_bytes=None if is_image or free_sectors is None else free_sectors * 2048,
                      warnings=warnings)
