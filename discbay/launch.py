"""What a disc launches, and how.

A launch target is exactly one of:

  app     = "org.kde.kate"            installed app by its desktop ID: native, Flatpak,
                                      Snap, and Steam games with desktop shortcuts
  exec    = "~/Apps/Foo.AppImage"     a program file on this computer (+ optional args = [...])
  steam   = 367520                    a Steam game by app ID
  url     = "https://example.com"     anything xdg-open can open (a URL or a file)
  command = "retroarch -L core.so x"  a full command line, for anything else

DiscBay never runs programs stored *on* the disc. Discs only point at
software that is already installed, which keeps a random disc from carrying
its own payload.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from dataclasses import dataclass, field

KINDS = ("app", "exec", "steam", "url", "command")

# Field codes a .desktop Exec line may contain (freedesktop Desktop Entry spec).
_FIELD_CODE = re.compile(r"%[fFuUdDnNickvm]")


HANDOFF_PROGRAMS = ("steam", "xdg-open", "gio", "gtk-launch", "kde-open", "kioclient")


def is_handoff(argv: list[str]) -> bool:
    """Programs that pass the job to something already running and exit immediately."""
    return bool(argv) and os.path.basename(argv[0]) in HANDOFF_PROGRAMS


class LaunchError(Exception):
    """The target can't be started (not installed, missing file...)."""


@dataclass(frozen=True)
class LaunchSpec:
    kind: str
    value: str
    args: tuple[str, ...] = field(default_factory=tuple)
    detached_override: bool | None = None

    # ---------------------------------------------------------- describing
    def describe(self) -> str:
        """Human-readable, shown to the user when asking for trust."""
        extra = (" " + " ".join(shlex.quote(a) for a in self.args)) if self.args else ""
        if self.kind == "app":
            return f"app {self.value}{extra}"
        if self.kind == "steam":
            return f"Steam game {self.value}"
        if self.kind == "url":
            return f"open {self.value}"
        if self.kind == "exec":
            return f"{self.value}{extra}"
        return self.value

    @property
    def detached(self) -> bool:
        """True when the command hands off and exits at once, so there is no process to track."""
        if self.detached_override is not None:
            return self.detached_override
        if self.kind in ("steam", "url"):
            return True
        if self.kind == "command":
            return is_handoff(shlex.split(self.value))
        return False


    # ---------------------------------------------------------- resolving
    def to_argv(self) -> list[str]:
        if self.kind == "command":
            argv = shlex.split(os.path.expanduser(self.value))
            if not argv:
                raise LaunchError("Empty command")
            return argv
        if self.kind == "exec":
            path = os.path.expanduser(self.value)
            if not os.path.isabs(path):
                found = shutil.which(path)
                if not found:
                    raise LaunchError(f"{self.value} was not found")
                path = found
            if not os.path.isfile(path):
                raise LaunchError(f"{self.value} does not exist")
            if not os.access(path, os.X_OK):
                raise LaunchError(f"{self.value} is not executable (chmod +x it)")
            return [path, *self.args]
        if self.kind == "steam":
            return ["steam", f"steam://rungameid/{self.value}"]
        if self.kind == "url":
            return ["xdg-open", os.path.expanduser(self.value)]
        if self.kind == "app":
            return [*resolve_desktop_app(self.value), *self.args]
        raise LaunchError(f"Unknown launch kind {self.kind!r}")


def strip_field_codes(exec_line: str) -> list[str]:
    """Turn a .desktop Exec= value into argv, dropping %f/%U-style placeholders."""
    argv = []
    for part in shlex.split(exec_line):
        part = part.replace("%%", "\0")
        if _FIELD_CODE.fullmatch(part):
            continue
        part = _FIELD_CODE.sub("", part).replace("\0", "%")
        if part:
            argv.append(part)
    return argv


def resolve_desktop_app(app_id: str) -> list[str]:
    """Find an installed app by desktop ID and return its command line."""
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    desktop_id = app_id if app_id.endswith(".desktop") else f"{app_id}.desktop"
    try:
        info = Gio.DesktopAppInfo.new(desktop_id)
    except TypeError:  # PyGObject raises when the constructor returns NULL
        info = None
    if info is None:
        # Gio also skips desktop files whose program is missing, so this covers
        # "uninstalled" as well as "never installed".
        raise LaunchError(f"App '{app_id}' is not installed")
    commandline = info.get_commandline()
    if not commandline:
        raise LaunchError(f"App '{app_id}' has no command line")
    argv = strip_field_codes(commandline)
    if not argv:
        raise LaunchError(f"App '{app_id}' has an empty command line")
    return argv


def parse_launch(table: dict, where: str) -> LaunchSpec | None:
    """Read the launch keys from a disc table. Returns None if it has none."""
    present = [k for k in KINDS if k in table]
    if not present:
        return None
    if len(present) > 1:
        raise ValueError(f"{where}: use only one of {', '.join(KINDS)} (found {', '.join(present)})")
    kind = present[0]
    raw = table[kind]

    if kind == "steam":
        if isinstance(raw, bool) or not isinstance(raw, (int, str)) or not str(raw).isdigit():
            raise ValueError(f"{where}: steam must be a numeric app ID")
        value = str(raw)
    else:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{where}: {kind} must be a non-empty string")
        value = raw.strip()

    args = table.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError(f"{where}: args must be a list of strings")
    if args and kind not in ("app", "exec"):
        raise ValueError(f"{where}: args only works with app or exec")

    detached = table.get("detached")
    if detached is not None and not isinstance(detached, bool):
        raise ValueError(f"{where}: detached must be true or false")
    return LaunchSpec(kind=kind, value=value, args=tuple(args), detached_override=detached)
