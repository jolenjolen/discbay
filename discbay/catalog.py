"""What's installed on this computer, for DiscBay Studio's pickers.

* desktop apps (native, Flatpak, Snap, and Steam games with shortcuts), with icons
* Steam games straight from Steam's library folders, with their cover art
* AppImages in the usual folders
"""

from __future__ import annotations

import glob
import os
import re
from functools import lru_cache
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

HOME = Path.home()
ICON_SIZES = ("512x512", "256x256", "192x192", "128x128", "scalable", "96x96", "64x64", "48x48", "32x32")
ICON_EXTS = (".png", ".svg")


def _data_dirs() -> list[Path]:
    home_data = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share")
    dirs = [home_data, HOME / ".local/share/flatpak/exports/share", Path("/var/lib/flatpak/exports/share")]
    dirs += [Path(d) for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":") if d]
    seen, out = set(), []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


# ---------------------------------------------------------------- desktop apps
@lru_cache(maxsize=4096)
def find_icon(name: str) -> str | None:
    """Resolve a themed icon name to a file, preferring big hicolor PNGs."""
    if not name:
        return None
    if os.path.isabs(name):
        return name if os.path.isfile(name) else None
    for base in _data_dirs():
        for theme in ("hicolor", "breeze", "Adwaita"):
            for size in ICON_SIZES:
                for ext in ICON_EXTS:
                    path = base / "icons" / theme / size / "apps" / f"{name}{ext}"
                    if path.is_file():
                        return str(path)
        for ext in ICON_EXTS + (".xpm",):
            path = base / "pixmaps" / f"{name}{ext}"
            if path.is_file() and ext != ".xpm":
                return str(path)
    return None


def _icon_path(info: Gio.AppInfo) -> str | None:
    icon = info.get_icon()
    if icon is None:
        return None
    if isinstance(icon, Gio.FileIcon):
        path = icon.get_file().get_path()
        return path if path and os.path.isfile(path) else None
    if isinstance(icon, Gio.ThemedIcon):
        for name in icon.get_names():
            found = find_icon(name)
            if found:
                return found
    return None


def list_apps() -> list[dict]:
    apps = []
    for info in Gio.AppInfo.get_all():
        if not info.should_show():
            continue
        app_id = (info.get_id() or "").removesuffix(".desktop")
        if not app_id:
            continue
        categories = ""
        if isinstance(info, Gio.DesktopAppInfo):
            categories = info.get_categories() or ""
        commandline = info.get_commandline() or ""
        apps.append({
            "id": app_id,
            "name": info.get_display_name() or info.get_name() or app_id,
            "description": info.get_description() or "",
            "game": "Game" in categories.split(";") or "steam://rungameid" in commandline,
            "flatpak": "flatpak run" in commandline,
            "has_icon": _icon_path(info) is not None,
        })
    apps.sort(key=lambda a: a["name"].casefold())
    return apps


def app_icon(app_id: str) -> str | None:
    try:
        info = Gio.DesktopAppInfo.new(f"{app_id}.desktop")
    except TypeError:
        info = None
    return _icon_path(info) if info is not None else None


# ---------------------------------------------------------------- Steam
STEAM_ROOTS = (
    HOME / ".local/share/Steam",
    HOME / ".steam/steam",
    HOME / ".var/app/com.valvesoftware.Steam/.local/share/Steam",   # Flatpak Steam
)
_NOT_GAMES = re.compile(r"Proton|Steam Linux Runtime|Steamworks Common|Redistributables", re.I)


def _steam_libraries() -> list[Path]:
    libraries = []
    for root in STEAM_ROOTS:
        if not (root / "steamapps").is_dir():
            continue
        libraries.append(root / "steamapps")
        vdf = root / "steamapps" / "libraryfolders.vdf"
        try:
            for path in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(errors="replace")):
                libraries.append(Path(path.replace("\\\\", "\\")) / "steamapps")
        except OSError:
            pass
    seen, out = set(), []
    for lib in libraries:
        key = lib.resolve() if lib.exists() else lib
        if key not in seen and lib.is_dir():
            seen.add(key)
            out.append(lib)
    return out


def list_steam_games() -> list[dict]:
    games = {}
    for lib in _steam_libraries():
        for manifest in lib.glob("appmanifest_*.acf"):
            try:
                text = manifest.read_text(errors="replace")
            except OSError:
                continue
            appid = re.search(r'"appid"\s+"(\d+)"', text)
            name = re.search(r'"name"\s+"([^"]+)"', text)
            if not appid or not name or _NOT_GAMES.search(name.group(1)):
                continue
            games[appid.group(1)] = {"id": appid.group(1), "name": name.group(1),
                                     "has_art": steam_art(appid.group(1)) is not None}
    return sorted(games.values(), key=lambda g: g["name"].casefold())


def steam_art(appid: str) -> str | None:
    """Steam's own cover art for a game, if the client has cached it."""
    if not appid.isdigit():
        return None
    names = ("library_600x900.jpg", "library_600x900_2x.jpg", "header.jpg", "library_hero.jpg")
    for root in STEAM_ROOTS:
        cache = root / "appcache" / "librarycache"
        for name in names:
            for path in (cache / appid / name, cache / f"{appid}_{name}"):
                if path.is_file():
                    return str(path)
        # Newer clients keep art in hashed sub-folders.
        for found in glob.glob(str(cache / appid / "*" / "library_600x900.jpg")):
            return found
    return None


# ---------------------------------------------------------------- AppImages
APPIMAGE_DIRS = ("Applications", "Apps", "AppImages", "Games", "Downloads", ".local/bin", "Desktop")


def list_appimages() -> list[dict]:
    found = {}
    for folder in APPIMAGE_DIRS:
        base = HOME / folder
        if not base.is_dir():
            continue
        for path in list(base.glob("*.AppImage")) + list(base.glob("*.appimage")) + list(base.glob("*/*.AppImage")):
            if path.is_file():
                name = re.sub(r"[-_.](x86_64|amd64|aarch64|linux|\d[\w.]*).*$", "", path.stem, flags=re.I)
                found[str(path)] = {"path": "~/" + str(path.relative_to(HOME)), "name": name or path.stem,
                                    "executable": os.access(path, os.X_OK)}
    return sorted(found.values(), key=lambda a: a["name"].casefold())
