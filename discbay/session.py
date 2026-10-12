"""The desktop session's environment, for the apps DiscBay starts.

discbayd is a systemd user service, and those start at login, before the
desktop has told systemd where its display is. So the daemon's own
environment has no WAYLAND_DISPLAY or DISPLAY, and a game started with it
can't open a window (Chromium-based apps say "The platform failed to
initialize" and quit).

The desktop does hand those variables to the user's systemd manager once it
is up (Plasma and GNOME both do), so we ask the manager each time we start
something, rather than trusting the environment we were born with.
"""

from __future__ import annotations

import codecs
import os
import subprocess
import time

CACHE_SECONDS = 2.0

_cache: tuple[float, dict[str, str]] | None = None


def _unquote(value: str) -> str:
    # systemd prints values containing odd characters as $'...' (C escapes).
    if value.startswith("$'") and value.endswith("'") and len(value) >= 3:
        return codecs.decode(value[2:-1].encode("latin-1", "backslashreplace"), "unicode_escape")
    return value


def parse_show_environment(text: str) -> dict[str, str]:
    env = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key and key.replace("_", "").isalnum():
            env[key] = _unquote(value)
    return env


def manager_environment() -> dict[str, str]:
    """What `systemctl --user show-environment` says, or {} if we can't ask."""
    try:
        out = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True,
                             text=True, timeout=3, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_show_environment(out.stdout) if out.returncode == 0 else {}


def environment(fresh: bool = False) -> dict[str, str]:
    """Our environment, updated with the desktop session's (cached for a moment)."""
    global _cache
    now = time.monotonic()
    if fresh or _cache is None or now - _cache[0] > CACHE_SECONDS:
        env = dict(os.environ)
        env.update(manager_environment())
        _cache = (now, env)
    return dict(_cache[1])


def is_graphical(env: dict[str, str]) -> bool:
    """Whether there's a display to open windows on (and it's still there:
    after a logout the variables can linger in the manager)."""
    wayland = env.get("WAYLAND_DISPLAY")
    if wayland:
        path = wayland if os.path.isabs(wayland) else os.path.join(env.get("XDG_RUNTIME_DIR", ""), wayland)
        if os.path.exists(path):
            return True
    display = env.get("DISPLAY", "")
    if display.startswith(":"):
        number = display[1:].split(".")[0]
        return os.path.exists(f"/tmp/.X11-unix/X{number}")
    return bool(display)  # a remote or unusual DISPLAY: take its word for it
