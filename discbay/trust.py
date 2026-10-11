"""Remembering which disc manifests you've allowed to launch things.

Stored in ~/.local/state/discbay/trusted.json, keyed by manifest fingerprint.
The file is re-read on every check, so `discbay-ctl untrust` takes effect
without restarting the daemon.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .config import state_dir


class TrustStore:
    def __init__(self, path: Path | None = None):
        self.path = path or state_dir() / "trusted.json"

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def is_trusted(self, fingerprint: str | None) -> bool:
        return bool(fingerprint) and fingerprint in self._read()

    def add(self, fingerprint: str, label: str | None, name: str, launch: str) -> None:
        data = self._read()
        data[fingerprint] = {"label": label, "name": name, "launch": launch,
                             "trusted_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        self._write(data)

    def remove(self, fingerprint: str) -> bool:
        data = self._read()
        if fingerprint not in data:
            return False
        del data[fingerprint]
        self._write(data)
        return True

    def all(self) -> dict:
        return self._read()


class MemoryTrust(TrustStore):
    """In-memory store for tests and the simulator."""

    def __init__(self):
        self._data: dict = {}

    def _read(self) -> dict:
        return dict(self._data)

    def _write(self, data: dict) -> None:
        self._data = dict(data)
