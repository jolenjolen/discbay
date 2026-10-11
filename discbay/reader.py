"""Reading everything DiscBay needs off a disc: label, manifest and cover art.

Runs on the poller thread, since raw disc reads can take a moment.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import drive
from .config import MANIFEST_NAME, ConfigError, Disc, cache_dir, parse_manifest

ART_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
MAX_ART = 8 * 1024 * 1024


def _art_extension(name: str) -> str | None:
    ext = os.path.splitext(name)[1].lower()
    return ext if ext in ART_EXTENSIONS else None


def cache_art(fingerprint: str, art_name: str, data: bytes) -> str | None:
    """Store a disc's cover art in ~/.cache/discbay/art so frontends can show it."""
    ext = _art_extension(art_name)
    if not ext or not data or len(data) > MAX_ART:
        return None
    folder = cache_dir() / "art"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{fingerprint}{ext}"
    if not path.exists():
        tmp = path.with_suffix(ext + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    return str(path)


def read_disc(device: str) -> drive.DiscInfo:
    info = drive.probe(device)
    if info.kind == "audio":
        return info
    if info.label is None and drive.media_state(device) == "blank":
        return drive.DiscInfo(kind="blank", label=None)

    raw = drive.read_root_file(device, MANIFEST_NAME, max_size=64 * 1024)
    if raw is None:
        return info
    try:
        disc = parse_manifest(raw, info.label)
    except ConfigError as exc:
        return drive.DiscInfo(kind=info.kind, label=info.label, manifest_error=str(exc))

    if disc.art:
        # Only files from the disc's root folder, never a path on this computer.
        art_name = os.path.basename(disc.art)
        art = drive.read_root_file(device, art_name, max_size=MAX_ART)
        disc.art = cache_art(disc.fingerprint, art_name, art) if art else None
    return drive.DiscInfo(kind=info.kind, label=info.label, manifest=disc)


def manifest_from_text(text: str, label: str, base_dir: str | None = None) -> drive.DiscInfo:
    """Simulator helper: pretend a disc carries this manifest (art resolved on this computer)."""
    try:
        disc: Disc = parse_manifest(text.encode("utf-8"), label or None)
    except ConfigError as exc:
        return drive.DiscInfo(kind="data", label=label or None, manifest_error=str(exc))
    if disc.art and base_dir:
        path = Path(base_dir, os.path.expanduser(disc.art))
        disc.art = str(path) if path.exists() else None
    elif disc.art:
        disc.art = None
    return drive.DiscInfo(kind="data", label=label or None, manifest=disc)
