"""discbayd: the daemon. Watches the drive, runs the engine, publishes on D-Bus.

Threading model:
  * one background thread polls the drive (ioctls can stall on a spinning disc),
  * everything else (engine, D-Bus, timers) runs on the GLib main loop,
  * the poll thread hands observations over with GLib.idle_add.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import config as cfg, drive, protocol as P, reader  # noqa: E402
from .engine import Engine  # noqa: E402
from .trust import TrustStore  # noqa: E402

log = logging.getLogger("discbay")


class DrivePoller(threading.Thread):
    """Polls the real drive and reports status *changes* to the main loop."""

    def __init__(self, device: str, interval: float, report, paused=lambda: False):
        super().__init__(name="discbay-poller", daemon=True)
        self.device = device
        self.interval = interval
        self.report = report
        self.paused = paused
        self._halt = threading.Event()
        self._last: str | None = None

    def run(self) -> None:
        was_paused = False
        while not self._halt.is_set():
            if self.paused():
                # A burn is running: stay off the drive entirely.
                was_paused = True
                self._halt.wait(self.interval)
                continue
            if was_paused:
                was_paused = False
                self._last = None   # look again from scratch: the disc may have changed
            status = drive.drive_status(self.device)
            if status != self._last:
                info = reader.read_disc(self.device) if status == drive.READY else None
                log.debug("drive %s -> %s %s", self._last, status, info)
                self._last = status
                GLib.idle_add(self.report, status, info)
            self._halt.wait(self.interval)

    def stop(self) -> None:
        self._halt.set()


class Service:
    def __init__(self, config_path, simulate: bool):
        self.config_path = config_path
        self.simulate = simulate
        self.config = self._load_config()
        self.engine = Engine(self.config, on_change=self._broadcast, trust=TrustStore())
        self.connection: Gio.DBusConnection | None = None
        self.poller: DrivePoller | None = None
        self.loop = GLib.MainLoop()
        self._sim_timer = 0

    # ----------------------------------------------------------- config
    def _load_config(self) -> cfg.Config:
        try:
            return cfg.load(self.config_path)
        except cfg.ConfigError as exc:
            log.error("%s (using an empty config)", exc)
            return cfg.Config()

    # ----------------------------------------------------------- engine glue
    def _report(self, status, info):
        self.engine.drive_update(status, info)
        return GLib.SOURCE_REMOVE

    def _tick(self):
        self.engine.tick()
        return GLib.SOURCE_CONTINUE

    def _broadcast(self, snapshot: dict) -> None:
        log.info("%-10s %-10s %s", snapshot["event"], snapshot["state"],
                 (snapshot.get("disc") or {}).get("name", ""))
        if self.connection is None:
            return
        self.connection.emit_signal(
            None, P.OBJECT_PATH, P.INTERFACE, "StateChanged",
            GLib.Variant("(s)", (json.dumps(snapshot),)),
        )

    # ----------------------------------------------------------- simulator
    def _sim_insert(self, label: str, manifest: str = "", base_dir: str = "") -> None:
        if self._sim_timer:
            GLib.source_remove(self._sim_timer)
            self._sim_timer = 0
        if self.engine.state != P.EMPTY:
            self.engine.drive_update(drive.ABSENT)  # a drive holds one disc at a time
        self.engine.drive_update(drive.SPINNING)

        def ready():
            self._sim_timer = 0
            if manifest.strip():
                info = reader.manifest_from_text(manifest, label, base_dir or None)
            else:
                info = drive.DiscInfo(kind="data", label=label or None)
            self.engine.drive_update(drive.READY, info)
            return GLib.SOURCE_REMOVE

        # Real drives take roughly 1-3 s to spin up and read the label.
        self._sim_timer = GLib.timeout_add(1600, ready)

    def _sim_remove(self) -> None:
        if self._sim_timer:
            GLib.source_remove(self._sim_timer)
            self._sim_timer = 0
        self.engine.drive_update(drive.ABSENT)

    # ----------------------------------------------------------- D-Bus
    def _on_method(self, conn, sender, path, iface, method, params, invocation):
        try:
            if method == "GetState":
                invocation.return_value(GLib.Variant("(s)", (json.dumps(self.engine.snapshot()),)))
                return
            if method == "Cancel":
                self.engine.cancel()
            elif method == "Trust":
                self.engine.trust()
            elif method == "Hold":
                seconds, exclusive = params.unpack()
                self.engine.set_hold(min(int(seconds), 600), bool(exclusive))
            elif method == "LaunchNow":
                self.engine.launch_now()
            elif method == "Eject":
                if self.simulate:
                    self._sim_remove()
                else:
                    drive.eject(self.config.settings.device)
            elif method == "ReloadConfig":
                self.config = self._load_config()
                self.engine.reload(self.config)
            elif method in ("SimulateInsert", "SimulateRemove"):
                if not self.simulate:
                    invocation.return_dbus_error(
                        f"{P.INTERFACE}.Error.NotSimulating",
                        "Start discbayd with --simulate to use the simulator")
                    return
                if method == "SimulateInsert":
                    self._sim_insert(*params.unpack())
                else:
                    self._sim_remove()
            invocation.return_value(None)
        except Exception as exc:  # never let a frontend crash the daemon
            log.exception("method %s failed", method)
            invocation.return_dbus_error(f"{P.INTERFACE}.Error.Failed", str(exc))

    def _on_get_property(self, conn, sender, path, iface, name):
        if name == "ProtocolVersion":
            return GLib.Variant("u", P.PROTOCOL_VERSION)
        return None

    def _on_bus_acquired(self, conn, name):
        self.connection = conn
        node = Gio.DBusNodeInfo.new_for_xml(P.INTROSPECTION_XML)
        conn.register_object(P.OBJECT_PATH, node.interfaces[0],
                             self._on_method, self._on_get_property, None)

    def _on_name_lost(self, conn, name):
        log.error("Could not own %s; is another discbayd running?", name)
        self.loop.quit()

    # ----------------------------------------------------------- lifecycle
    def run(self) -> int:
        Gio.bus_own_name(Gio.BusType.SESSION, P.BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                         self._on_bus_acquired, None, self._on_name_lost)
        GLib.timeout_add(100, self._tick)

        if self.simulate:
            log.info("simulator mode: use 'discbay-ctl sim insert LABEL'")
        else:
            settings = self.config.settings
            self.poller = DrivePoller(settings.device, settings.poll_interval_ms / 1000, self._report,
                                      paused=lambda: time.time() < self.engine.busy_until)
            self.poller.start()
            log.info("watching %s", settings.device)

        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGHUP, self._reload)
        self.loop.run()
        return 0

    def _quit(self):
        if self.poller:
            self.poller.stop()
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def _reload(self):
        self.config = self._load_config()
        self.engine.reload(self.config)
        log.info("config reloaded")
        return GLib.SOURCE_CONTINUE


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="discbayd", description="DiscBay disc-launcher daemon")
    ap.add_argument("--config", type=lambda s: __import__("pathlib").Path(s).expanduser(),
                    help="path to discs.toml (default: ~/.config/discbay/discs.toml)")
    ap.add_argument("--simulate", action="store_true", help="no real drive; insert discs with discbay-ctl sim")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    return Service(args.config, args.simulate).run()


if __name__ == "__main__":
    sys.exit(main())
