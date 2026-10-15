"""The DiscBay state machine.

Pure logic: no D-Bus, no GTK, no threads. It is fed drive observations and
user actions, and it calls ``on_change(snapshot)`` whenever something
happens. This is what makes it easy to test and easy to reuse.

The engine owns the autostart countdown, so every frontend shows the same
timer and a frontend crashing never leaves a launch half-decided.

Where a disc's details come from, in order:
  1. your discs.toml, matched by volume label (always trusted, always wins)
  2. a discbay.toml manifest on the disc itself (needs trust, once per disc)
  3. nothing: the disc is "unknown"
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from typing import Callable, Protocol

from . import drive, protocol as P, session
from .config import Config, Disc, launch_is_local, merge_local
from .launch import LaunchError, LaunchSpec, is_handoff
from .trust import MemoryTrust, TrustStore


class Process(Protocol):
    pid: int
    detached: bool

    def poll(self) -> int | None: ...


class Launcher(Protocol):
    def start(self, spec: LaunchSpec) -> Process: ...
    def stop(self, proc: Process) -> None: ...
    def run(self, command: str) -> None: ...


class SubprocessLauncher:
    """Starts each app in its own session so it can be stopped as a group.

    Every child is kept until it has exited and been reaped, including
    detached hand-offs like Steam, so the daemon never leaves zombies behind.
    """

    KILL_GRACE = 3.0  # seconds between SIGTERM and SIGKILL

    def __init__(self):
        self._children: list[list] = []  # [proc, kill_deadline or None]

    def session_ready(self) -> bool:
        """Is there a desktop to open the app's window on yet?"""
        return session.is_graphical(session.environment())

    def start(self, spec: LaunchSpec) -> subprocess.Popen:
        argv = spec.to_argv()
        spec.prepare()
        # The desktop session's environment, not the one the daemon started
        # with at login (which has no display: see session.py).
        proc = subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,
                                env=session.environment(fresh=True))
        # An app ID can resolve to a hand-off command (Steam shortcuts do), so
        # decide on the real command line, not just the kind.
        proc.detached = spec.detached or (spec.detached_override is None and is_handoff(argv))
        # A web disc runs a separate copy of the browser with DiscBay's own
        # profile: "flatpak kill" would close your everyday browser too, so
        # only its own processes are stopped.
        proc.flatpak_id = None if spec.window else flatpak_app_id(argv)
        self._children.append([proc, None])
        return proc

    def stop(self, proc: subprocess.Popen) -> None:
        flatpak_id = getattr(proc, "flatpak_id", None)
        if flatpak_id:
            # The game runs inside a sandbox in its own process tree, so
            # signalling our "flatpak run" process alone doesn't reach it.
            self.run(f"flatpak kill {shlex.quote(flatpak_id)}")
        self._signal(proc, signal.SIGTERM)
        for child in self._children:
            if child[0] is proc:
                child[1] = time.monotonic() + self.KILL_GRACE

    def run(self, command: str) -> None:
        proc = subprocess.Popen(shlex.split(os.path.expanduser(command)), start_new_session=True,
                                stdin=subprocess.DEVNULL, env=session.environment())
        self._children.append([proc, None])

    def reap(self) -> None:
        """Collect finished children; force-kill apps that ignored SIGTERM."""
        now = time.monotonic()
        alive = []
        for proc, deadline in self._children:
            if proc.poll() is not None:
                continue
            if deadline is not None and now >= deadline:
                self._signal(proc, signal.SIGKILL)
                deadline = None
            alive.append([proc, deadline])
        self._children = alive

    @staticmethod
    def _signal(proc: subprocess.Popen, sig: int) -> None:
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


def flatpak_app_id(argv: list[str]) -> str | None:
    """The app ID in a `flatpak run [options] APP_ID [args]` command line, if it is one."""
    if not argv or os.path.basename(argv[0]) != "flatpak" or "run" not in argv[1:]:
        return None
    rest = argv[argv.index("run", 1) + 1:]
    for arg in rest:
        if not arg.startswith("-"):
            return arg
    return None


class Engine:
    # An app that exits with an error this soon after starting didn't really
    # start: say so, instead of quietly going back to idle.
    QUICK_EXIT = 5.0
    # Once the desktop appears, give the frontends a moment to come up
    # before a disc that was already in the drive starts counting down.
    SESSION_SETTLE = 3.0
    WAITING = "Waiting for the desktop"

    def __init__(
        self,
        config: Config,
        launcher: Launcher | None = None,
        on_change: Callable[[dict], None] | None = None,
        clock: Callable[[], float] = time.time,
        trust: TrustStore | None = None,
    ):
        self.config = config
        self.launcher = launcher or SubprocessLauncher()
        self.on_change = on_change or (lambda snapshot: None)
        self.clock = clock
        self.trust_store = trust if trust is not None else MemoryTrust()

        self.state = P.EMPTY
        self.seq = 0
        self.disc: Disc | None = None
        self.manifest: Disc | None = None   # the disc's own manifest, even if overridden locally
        self.trusted = False
        self.label: str | None = None
        self.kind: str | None = None
        self.message: str | None = None
        self.inserted_at: float | None = None
        self.countdown_total: float | None = None
        self.countdown_ends_at: float | None = None
        self.proc: Process | None = None
        self.last_event = P.EV_SYNC
        # Leases from tools like DiscBay Studio (unix times; 0 = none).
        self.hold_until = 0.0        # don't autostart or prompt
        self.busy_until = 0.0        # a burn is running: also don't touch the drive
        self._was_held = self._was_busy = False
        self.launched_at: float | None = None
        self._session_since: float | None = None

    # ------------------------------------------------------------------ output
    def _public_disc(self) -> dict | None:
        if self.disc is not None:
            data = self.disc.public()
            if self.state == P.UNTRUSTED and self.disc.launch is not None:
                # The one time a frontend sees the launch target: so the user
                # knows exactly what they're agreeing to run.
                data["launch"] = self.disc.launch.describe()
                data["fingerprint"] = self.disc.fingerprint
            return data
        if self.label is not None and self.state in (P.UNKNOWN, P.IDLE, P.ERROR):
            return {"label": self.label, "name": self.label, "subtitle": None, "art": None,
                    "color": None, "autostart": False, "source": None}
        return None

    def snapshot(self, event: str | None = None) -> dict:
        remaining = None
        if self.state == P.COUNTDOWN and self.countdown_ends_at is not None:
            remaining = max(0.0, self.countdown_ends_at - self.clock())
        return {
            "protocol": P.PROTOCOL_VERSION,
            "seq": self.seq,
            "event": event or P.EV_SYNC,
            "state": self.state,
            "disc": self._public_disc(),
            "kind": self.kind,
            "message": self.message,
            "countdown": {
                "total": self.countdown_total,
                "remaining": remaining,
                "ends_at": self.countdown_ends_at,  # unix seconds, for smooth local animation
            } if self.state == P.COUNTDOWN else None,
            "can_launch": self._can_launch(),
            "held": self.held,
            "burning": self.busy,
            "pid": self.proc.pid if self.proc and self.state == P.RUNNING else None,
            "tracked": self.proc is not None,
            "time": self.clock(),
        }

    def _emit(self, event: str) -> None:
        self.seq += 1
        self.last_event = event
        self.on_change(self.snapshot(event))

    def _reset_disc(self) -> None:
        self.disc = None
        self.manifest = None
        self.trusted = False
        self.label = None
        self.kind = None
        self.message = None
        self.inserted_at = None
        self.countdown_total = None
        self.countdown_ends_at = None

    @property
    def held(self) -> bool:
        return self.clock() < max(self.hold_until, self.busy_until)

    @property
    def busy(self) -> bool:
        return self.clock() < self.busy_until

    def set_hold(self, seconds: float, exclusive: bool) -> None:
        until = self.clock() + seconds if seconds > 0 else 0.0
        if exclusive:
            self.busy_until = until
        else:
            self.hold_until = until
        self._check_hold_change()

    def _check_hold_change(self) -> None:
        held, busy = self.held, self.busy
        if (held, busy) == (self._was_held, self._was_busy):
            return
        released = self._was_held and not held
        self._was_held, self._was_busy = held, busy
        self._emit(P.EV_BUSY)
        if released and self.state == P.IDLE and self.message == "Paused while DiscBay Studio is open":
            # Studio closed: make the disc that's in the drive usable again, but
            # never surprise-launch it: offer Start (or the trust prompt) instead.
            self._choose_disc()
            if self.state == P.COUNTDOWN:
                self.countdown_total = self.countdown_ends_at = None
                self.state, self.message = P.IDLE, "Ready"
            self._emit(P.EV_IDENTIFIED)

    def _same_disc(self, info: drive.DiscInfo | None) -> bool:
        if info is None:
            return True
        new_fp = getattr(info.manifest, "fingerprint", None)
        old_fp = self.manifest.fingerprint if self.manifest is not None else None
        return info.label == self.label and new_fp == old_fp

    def _can_launch(self) -> bool:
        return bool(self.disc and self.disc.launch and self.trusted
                    and self.state in (P.COUNTDOWN, P.IDLE, P.ERROR))

    # ------------------------------------------------------------------ input: drive
    def drive_update(self, status: str, info: drive.DiscInfo | None = None) -> None:
        """Feed one observation from the drive (called on every status change)."""
        if status in (drive.ABSENT, drive.UNAVAILABLE):
            if self.state != P.EMPTY:
                self._handle_removed()
            return

        if status == drive.READY and self.state not in (P.EMPTY, P.SPINNING) and not self._same_disc(info):
            # The disc changed without leaving the drive (rewritten while we
            # weren't looking): treat it as a fresh insert.
            self._handle_removed()

        if self.state == P.EMPTY:
            # A disc appeared. This fires as soon as the drive reports the
            # disc, before any data is readable: the moment for the beep.
            self.state = P.SPINNING
            self.inserted_at = self.clock()
            self._emit(P.EV_INSERTED)

        if status == drive.READY and self.state == P.SPINNING:
            self._identify(info or drive.DiscInfo(kind="unknown", label=None))

    def _identify(self, info: drive.DiscInfo) -> None:
        self.kind = info.kind
        self.label = info.label
        self.manifest = info.manifest
        self._choose_disc(info.manifest_error)
        self._emit(P.EV_IDENTIFIED)
        if self.state == P.COUNTDOWN and self.countdown_total is not None and self.countdown_total <= 0:
            self._launch()

    def _choose_disc(self, manifest_error: str | None = None) -> None:
        """Decide which description of the disc to use, and the state that follows."""
        local = self.config.find(self.label)
        policy = self.config.settings.disc_manifests
        self.message = None

        if local is not None and self.manifest is not None:
            self.disc = merge_local(self.manifest, local)
        elif local is not None:
            self.disc = local
        elif self.manifest is not None:
            self.disc = self.manifest

        if self.disc is not None:
            if self.disc.launch is None or launch_is_local(local):
                self.trusted = True            # nothing to run, or you wrote the target yourself
            elif policy == "always":
                self.trusted = True
            elif policy == "never":
                self.trusted = False
            else:
                self.trusted = self.trust_store.is_trusted(self.disc.fingerprint)
        else:
            self.disc, self.trusted = None, False
            if manifest_error:
                self.state, self.message = P.ERROR, manifest_error
            else:
                self.state = P.UNKNOWN
                if self.kind == "audio":
                    self.message = "Audio CD"
                elif self.kind == "blank":
                    self.message = "Blank disc"
                elif self.label is None:
                    self.message = "This disc has no label"
                else:
                    self.message = "Not registered on this computer"
            return

        disc = self.disc
        if disc.launch is None:
            self.state, self.message = P.IDLE, "Nothing to launch"
        elif not self.trusted and policy == "never":
            self.state, self.message = P.IDLE, "Launching from disc manifests is turned off"
        elif self.held:
            # A tool like DiscBay Studio is open (maybe to rewrite this very
            # disc): don't start anything or ask anything.
            self.state, self.message = P.IDLE, "Paused while DiscBay Studio is open"
        elif not self.trusted:
            self.state, self.message = P.UNTRUSTED, "New disc"
        elif disc.autostart and not self._session_ready():
            # Typically: the computer booted with the disc in, and the desktop
            # isn't up yet. Count down once it is (see tick).
            self.state, self.message = P.IDLE, self.WAITING
            self._session_since = None
        elif disc.autostart:
            self._start_countdown()
        else:
            self.state, self.message = P.IDLE, "Ready"

    def _session_ready(self) -> bool:
        check = getattr(self.launcher, "session_ready", None)
        return True if check is None else bool(check())

    def _start_countdown(self) -> None:
        total = self.disc.countdown if self.disc.countdown is not None else self.config.settings.countdown
        self.countdown_total = total
        self.countdown_ends_at = self.clock() + total
        self.state = P.COUNTDOWN

    def _handle_removed(self) -> None:
        disc, proc = self.disc, self.proc
        if disc and self.state == P.RUNNING and disc.on_eject == "close":
            if disc.close_command:
                self.launcher.run(disc.close_command)
            elif proc is not None:
                self.launcher.stop(proc)
        self.proc = None
        self._reset_disc()
        self.state = P.EMPTY
        self._emit(P.EV_EJECTED)

    # ------------------------------------------------------------------ input: time
    def tick(self) -> None:
        reap = getattr(self.launcher, "reap", None)
        if reap is not None:
            reap()
        self._check_hold_change()
        now = self.clock()
        if self.state == P.COUNTDOWN and self.countdown_ends_at is not None and now >= self.countdown_ends_at:
            self._launch()
        elif self.state == P.SPINNING and self.inserted_at is not None:
            if now - self.inserted_at > self.config.settings.spinup_timeout:
                self.state = P.ERROR
                self.message = "Disc could not be read"
                self._emit(P.EV_FAILED)

        if self.state == P.IDLE and self.message == self.WAITING and not self.held:
            if not self._session_ready():
                self._session_since = None
            elif self._session_since is None:
                self._session_since = now
            elif now - self._session_since >= self.SESSION_SETTLE:
                self._start_countdown()
                self.message = None
                self._emit(P.EV_IDENTIFIED)

        if self.proc is not None and self.proc.poll() is not None:
            code = self.proc.poll()
            self.proc = None
            if self.state == P.RUNNING:
                quick = self.launched_at is not None and now - self.launched_at < self.QUICK_EXIT
                if quick and code not in (0, None):
                    self.state = P.ERROR
                    self.message = f"Closed straight away (exit code {code})"
                    self._emit(P.EV_FAILED)
                else:
                    self.state = P.IDLE
                    self.message = None
                    self._emit(P.EV_APP_EXITED)

    # ------------------------------------------------------------------ input: user
    def cancel(self) -> bool:
        if self.state == P.COUNTDOWN:
            self.countdown_total = None
            self.countdown_ends_at = None
            self.state, self.message = P.IDLE, "Autostart cancelled"
        elif self.state == P.UNTRUSTED:
            self.state, self.message = P.IDLE, "Not trusted"
        else:
            return False
        self._emit(P.EV_CANCELLED)
        return True

    def trust(self) -> bool:
        """The user agreed to what an untrusted disc wants to run. Remember it and start."""
        if self.state != P.UNTRUSTED or not self.disc or not self.disc.launch or not self.disc.fingerprint:
            return False
        self.trust_store.add(self.disc.fingerprint, self.label, self.disc.name, self.disc.launch.describe())
        self.trusted = True
        self.message = None
        self._launch()
        return True

    def launch_now(self) -> bool:
        if self.busy or not self._can_launch():
            return False
        self._launch()
        return True

    def reload(self, config: Config) -> None:
        self.config = config
        if self.label is not None and self.state in (P.UNKNOWN, P.IDLE, P.UNTRUSTED, P.RUNNING):
            # Apply the new config to the disc that's already in, without re-inserting.
            before = (self.disc, self.state, self.trusted)
            running = self.state == P.RUNNING
            self._choose_disc()
            if running:
                self.state, self.message = P.RUNNING, None  # never interrupt a running app
            elif self.state == P.COUNTDOWN:
                # Don't surprise-launch on reload; offer Start instead.
                self.countdown_total = self.countdown_ends_at = None
                self.state, self.message = P.IDLE, "Ready"
            if (self.disc, self.state, self.trusted) != before:
                self._emit(P.EV_IDENTIFIED)

    def _launch(self) -> None:
        assert self.disc is not None and self.disc.launch is not None and self.trusted
        self.countdown_total = None
        self.countdown_ends_at = None
        if not self._session_ready():
            self.state, self.message = P.ERROR, "No desktop to open it on yet"
            self._emit(P.EV_FAILED)
            return
        try:
            proc = self.launcher.start(self.disc.launch)
        except (LaunchError, OSError, ValueError) as exc:
            self.state = P.ERROR
            self.message = f"Could not start: {exc}"
            self._emit(P.EV_FAILED)
            return
        detached = getattr(proc, "detached", self.disc.launch.detached)
        self.proc = None if detached else proc
        self.launched_at = self.clock()
        self.state = P.RUNNING
        self.message = None
        self._emit(P.EV_LAUNCHED)
