"""A gentle D-Bus client for tools that work with or without the daemon.

Unlike discbay-ctl's own calls, nothing here fails if discbayd isn't
running: burning a disc doesn't need the daemon, it's just nicer with it.
"""

from __future__ import annotations

import contextlib
import json
import threading

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import protocol as P  # noqa: E402


def _call(method: str, args: GLib.Variant | None = None, timeout_ms: int = 2000):
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(P.BUS_NAME, P.OBJECT_PATH, P.INTERFACE, method, args,
                              None, Gio.DBusCallFlags.NO_AUTO_START, timeout_ms, None)
        return reply.unpack() if reply is not None else None
    except GLib.Error:
        return None


def state() -> dict | None:
    reply = _call("GetState")
    return json.loads(reply[0]) if reply else None


def hold(seconds: int, exclusive: bool = False) -> bool:
    """Ask the daemon not to autostart discs for a while (exclusive: also stop touching the drive).

    The two leases are separate: a plain hold never shortens an exclusive one.
    Pass seconds=0 to release that lease early.
    """
    return _call("Hold", GLib.Variant("(ub)", (max(0, int(seconds)), exclusive))) is not None


@contextlib.contextmanager
def hold_drive(exclusive: bool = True, lease: int = 15):
    """Hold the daemon for as long as the block runs, renewing the lease in the background.

    The lease expires on its own, so a crash never leaves the daemon stuck.
    """
    stop = threading.Event()
    hold(lease, exclusive)

    def renew():
        while not stop.wait(lease / 3):
            hold(lease, exclusive)

    renewer = threading.Thread(target=renew, daemon=True)
    renewer.start()
    try:
        yield
    finally:
        stop.set()
        renewer.join(timeout=1)
        hold(0, exclusive)
