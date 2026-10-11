"""Loading ~/.config/discbay/discs.toml and the manifests that live on discs.

Both use the same keys for a disc, so a manifest is just a disc entry that
travels with the disc instead of living in your config:

    name = "Kate"
    subtitle = "Text editor"
    color = "#3daee9"
    art = "COVER.PNG"          # on a disc: a file on the disc; locally: a path
    app = "org.kde.kate"       # or exec / steam / url / command (see launch.py)
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .launch import KINDS, LaunchSpec, parse_launch

MANIFEST_NAME = "discbay.toml"   # stored on disc as DISCBAY.TOM or DISCBAY.TOML
MANIFEST_POLICIES = ("ask", "always", "never")


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "discbay"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "discbay"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "discbay"


def default_config_path() -> Path:
    return config_dir() / "discs.toml"


@dataclass
class Disc:
    label: str
    name: str
    launch: LaunchSpec | None = None
    art: str | None = None          # local image path (manifest art is extracted to the cache)
    color: str | None = None        # hex colour for the disc theme, e.g. "#7c5cff"
    subtitle: str | None = None
    autostart: bool = True
    countdown: float | None = None  # overrides the global countdown
    on_eject: str = "keep"          # "keep" or "close"
    close_command: str | None = None
    source: str = "local"           # "local" (your discs.toml) or "disc" (manifest on the disc)
    fingerprint: str | None = None  # identifies a manifest for the trust store
    explicit: frozenset = frozenset()  # keys actually written in the table this came from

    def public(self) -> dict:
        """What frontends get to see. The launch target is not included."""
        return {
            "label": self.label,
            "name": self.name,
            "subtitle": self.subtitle,
            "art": os.path.expanduser(self.art) if self.art else None,
            "color": self.color,
            "autostart": self.autostart,
            "source": self.source,
        }


@dataclass
class Settings:
    device: str = "/dev/sr0"
    countdown: float = 5.0
    poll_interval_ms: int = 200
    spinup_timeout: float = 40.0
    # What to do with launch targets that come from a disc's own manifest:
    #   "ask"    show what it wants to run and ask once per disc (default)
    #   "always" trust every disc (only if you burn all your discs yourself)
    #   "never"  use the disc's name and art, but never launch from it
    disc_manifests: str = "ask"


@dataclass
class Config:
    settings: Settings = field(default_factory=Settings)
    discs: dict[str, Disc] = field(default_factory=dict)

    def find(self, label: str | None) -> Disc | None:
        if not label:
            return None
        if label in self.discs:
            return self.discs[label]
        # ISO labels are often upper-cased by burning tools; match loosely.
        lowered = label.casefold()
        for key, disc in self.discs.items():
            if key.casefold() == lowered:
                return disc
        return None


class ConfigError(Exception):
    pass


def _disc_from_table(label: str, entry: dict, where: str, source: str) -> Disc:
    if not isinstance(entry, dict):
        raise ConfigError(f"{where} must be a table")
    on_eject = entry.get("on_eject", "keep")
    if on_eject not in ("keep", "close"):
        raise ConfigError(f"{where}: on_eject must be 'keep' or 'close'")
    countdown = entry.get("countdown")
    try:
        launch = parse_launch(entry, where)
        countdown = float(countdown) if countdown is not None else None
    except (ValueError, TypeError) as exc:
        raise ConfigError(str(exc)) from exc
    for key in ("name", "subtitle", "color", "art", "close_command"):
        if key in entry and not isinstance(entry[key], str):
            raise ConfigError(f"{where}: {key} must be a string")
    return Disc(
        label=label,
        name=str(entry.get("name", label)),
        launch=launch,
        art=entry.get("art"),
        color=entry.get("color"),
        subtitle=entry.get("subtitle"),
        autostart=bool(entry.get("autostart", True)),
        countdown=countdown,
        on_eject=on_eject,
        # A disc may not tell your computer what to run on eject.
        close_command=entry.get("close_command") if source == "local" else None,
        source=source,
        explicit=frozenset(entry),
    )


_OVERRIDABLE = ("name", "subtitle", "color", "art", "autostart", "countdown", "on_eject", "close_command")


def merge_local(manifest: Disc, local: Disc) -> Disc:
    """Apply a local discs.toml entry on top of a disc's own manifest.

    Only keys you actually wrote locally change anything, so a one-line entry
    like `on_eject = "close"` fixes a burned (write-once) disc without having
    to repeat its name, art and launch target.
    """
    changes = {key: getattr(local, key) for key in _OVERRIDABLE if key in local.explicit}
    if local.launch is not None:
        changes["launch"] = local.launch
        changes["source"] = "local"
    return dataclasses.replace(manifest, **changes)


def launch_is_local(local: Disc | None) -> bool:
    return local is not None and any(k in local.explicit for k in KINDS)


def parse(text: str) -> Config:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"discs.toml is not valid TOML: {exc}") from exc

    raw = data.get("settings", {})
    policy = raw.get("disc_manifests", Settings.disc_manifests)
    if policy not in MANIFEST_POLICIES:
        raise ConfigError(f"settings.disc_manifests must be one of {', '.join(MANIFEST_POLICIES)}")
    settings = Settings(
        device=str(raw.get("device", Settings.device)),
        countdown=float(raw.get("countdown", Settings.countdown)),
        poll_interval_ms=int(raw.get("poll_interval_ms", Settings.poll_interval_ms)),
        spinup_timeout=float(raw.get("spinup_timeout", Settings.spinup_timeout)),
        disc_manifests=policy,
    )

    discs = {label: _disc_from_table(label, entry, f"[discs.{label}]", "local")
             for label, entry in data.get("discs", {}).items()}
    return Config(settings=settings, discs=discs)


def manifest_fingerprint(label: str | None, manifest: bytes) -> str:
    """Same label + same manifest = same disc, as far as trust goes."""
    digest = hashlib.sha256((label or "").encode() + b"\0" + manifest).hexdigest()
    return digest[:20]


def parse_manifest(manifest: bytes, label: str | None) -> Disc:
    """Parse the discbay.toml found on a disc."""
    if len(manifest) > 64 * 1024:
        raise ConfigError("Disc manifest is too large")
    try:
        data = tomllib.loads(manifest.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Disc manifest is not valid TOML: {exc}") from exc
    disc = _disc_from_table(label or "", data, "disc manifest", "disc")
    disc.fingerprint = manifest_fingerprint(label, manifest)
    return disc


def load(path: Path | None = None) -> Config:
    path = path or default_config_path()
    if not path.exists():
        return Config()
    return parse(path.read_text(encoding="utf-8"))
