"""What a disc launches, and how.

A launch target is exactly one of:

  app     = "org.kde.kate"            installed app by its desktop ID: native, Flatpak,
                                      Snap, and Steam games with desktop shortcuts
  exec    = "~/Apps/Foo.AppImage"     a program file on this computer (+ optional args = [...])
  steam   = 367520                    a Steam game by app ID
  url     = "https://example.com"     anything xdg-open can open (a URL or a file)
  command = "retroarch -L core.so x"  a full command line, for anything else

A website can also open like a game instead of a browser tab:

  url    = "https://www.xbox.com/play"
  window = "fullscreen"               "app" (its own window, no tabs or address bar),
                                      "fullscreen" (that, full screen) or "kiosk"
                                      (full screen with no way out but closing it)

DiscBay then picks a browser itself (a Chromium-based one if there is one,
as only those have an app mode; Firefox otherwise), so the disc works on any
computer. It gets its own DiscBay profile, so it always starts as a separate
browser that ejecting the disc can close; you sign in to the site once there.

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
WINDOWS = ("tab", "app", "fullscreen", "kiosk")   # "tab" is the default: a normal browser tab

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
    window: str | None = None        # url only: "app", "fullscreen" or "kiosk" (None = a tab)

    # ---------------------------------------------------------- describing
    def describe(self) -> str:
        """Human-readable, shown to the user when asking for trust."""
        extra = (" " + " ".join(shlex.quote(a) for a in self.args)) if self.args else ""
        if self.kind == "app":
            return f"app {self.value}{extra}"
        if self.kind == "steam":
            return f"Steam game {self.value}"
        if self.kind == "url" and self.window:
            how = {"app": "in its own window", "fullscreen": "full screen",
                   "kiosk": "full screen (kiosk)"}[self.window]
            return f"open {self.value} {how}"
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
        if self.kind == "url" and self.window:
            return False   # its own browser process, which we keep track of
        if self.kind in ("steam", "url"):
            return True
        if self.kind == "command":
            return is_handoff(shlex.split(self.value))
        return False


    # ---------------------------------------------------------- resolving
    def prepare(self) -> None:
        """Anything that has to exist before starting (the browser profile folder)."""
        if self.kind == "url" and self.window:
            os.makedirs(find_browser().profile, exist_ok=True)

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
        if self.kind == "url" and self.window:
            return web_window_argv(self.value, self.window)
        if self.kind == "url":
            return ["xdg-open", os.path.expanduser(self.value)]
        if self.kind == "app":
            return [*resolve_desktop_app(self.value), *self.args]
        raise LaunchError(f"Unknown launch kind {self.kind!r}")



# ------------------------------------------------------------------ browsers
@dataclass(frozen=True)
class Browser:
    name: str
    argv: tuple[str, ...]     # how to start it
    chromium: bool            # Chromium-based (has --app), or Firefox
    profile: str              # DiscBay's own profile folder for it


# Chromium-based browsers first: only they can open a site as an app window.
_NATIVE = (("chromium-browser", True), ("chromium", True), ("google-chrome-stable", True),
           ("google-chrome", True), ("microsoft-edge-stable", True), ("microsoft-edge", True),
           ("brave-browser", True), ("brave", True), ("vivaldi-stable", True),
           ("firefox", False))
_FLATPAK = (("org.chromium.Chromium", True), ("com.google.Chrome", True), ("com.microsoft.Edge", True),
            ("com.brave.Browser", True), ("io.github.ungoogled_software.ungoogled_chromium", True),
            ("org.mozilla.firefox", False))


def _data_home() -> str:
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def _flatpak_installed(app_id: str) -> bool:
    for root in ("/var/lib/flatpak", os.path.join(_data_home(), "flatpak")):
        if os.path.exists(os.path.join(root, "exports", "bin", app_id)):
            return True
    return False


def find_browser() -> Browser:
    """The browser DiscBay opens web discs in (DISCBAY_BROWSER overrides, e.g. "google-chrome")."""
    wanted = os.environ.get("DISCBAY_BROWSER", "").strip()
    native = _NATIVE
    flatpaks = _FLATPAK
    if wanted:
        native = tuple(c for c in _NATIVE if c[0] == wanted) or ((wanted, "firefox" not in wanted),)
        flatpaks = tuple(c for c in _FLATPAK if c[0] == wanted)
    candidates = []
    for program, chromium in native:
        path = shutil.which(program)
        if path:
            profile = os.path.join(_data_home(), "discbay", "browser", program)
            candidates.append(Browser(program, (path,), chromium, profile))
    for app_id, chromium in flatpaks:
        if _flatpak_installed(app_id):
            # A Flatpak browser can only see its own data folder, so keep the profile there.
            profile = os.path.expanduser(f"~/.var/app/{app_id}/data/discbay-profile")
            candidates.append(Browser(app_id, ("flatpak", "run", app_id), chromium, profile))
    if not candidates:
        raise LaunchError("No web browser found to open it in (install Chromium, Chrome, Edge or Firefox)")
    for browser in candidates:
        if browser.chromium:
            return browser
    return candidates[0]


def web_window_argv(url: str, window: str) -> list[str]:
    browser = find_browser()
    if browser.chromium:
        argv = [*browser.argv, f"--user-data-dir={browser.profile}", "--no-first-run",
                "--no-default-browser-check", f"--app={url}"]
        if window == "fullscreen":
            argv.append("--start-fullscreen")
        elif window == "kiosk":
            argv.append("--kiosk")
        return argv
    # Firefox has no app mode: a window of its own, or kiosk for both full-screen kinds.
    argv = [*browser.argv, "--new-instance", "--profile", browser.profile]
    if window in ("fullscreen", "kiosk"):
        argv.append("--kiosk")
    return [*argv, url]


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

    window = parse_window(table, where)
    if window and (kind != "url" or not re.match(r"https?://", value, re.I)):
        raise ValueError(f"{where}: window only works with a url starting with http:// or https://")
    return LaunchSpec(kind=kind, value=value, args=tuple(args), detached_override=detached, window=window)


def parse_window(table: dict, where: str) -> str | None:
    window = table.get("window")
    if window is None:
        return None
    if window not in WINDOWS:
        raise ValueError(f"{where}: window must be one of {', '.join(WINDOWS)}")
    return None if window == "tab" else window
