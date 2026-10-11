"""Talking to the optical drive directly, through Linux CD-ROM ioctls.

No mounting and no udisks: we ask the kernel for the drive status (which
reports "not ready" while a fresh disc spins up, long before any data can be
read) and then read the ISO 9660 volume label straight off the raw device.
"""

from __future__ import annotations

import fcntl
import os
import re
import stat
import struct
from dataclasses import dataclass
from typing import Any

# <linux/cdrom.h>
CDROMEJECT = 0x5309
CDROMMULTISESSION = 0x5310
CDROM_LBA = 0x01
CDROM_DRIVE_STATUS = 0x5326
CDROM_DISC_STATUS = 0x5327
CDROM_LOCKDOOR = 0x5329
CDSL_CURRENT = 0x7FFFFFFF

CDS_NO_INFO = 0
CDS_NO_DISC = 1
CDS_TRAY_OPEN = 2
CDS_DRIVE_NOT_READY = 3
CDS_DISC_OK = 4

CDS_AUDIO = 100
CDS_DATA_1 = 101
CDS_DATA_2 = 102
CDS_XA_2_1 = 103
CDS_XA_2_2 = 104
CDS_MIXED = 105

# Simplified status the rest of DiscBay works with.
ABSENT = "absent"
SPINNING = "spinning"
READY = "ready"
UNAVAILABLE = "unavailable"   # no drive, no permission, etc.

ISO_PVD_OFFSET = 16 * 2048     # Primary Volume Descriptor lives in sector 16
ISO_LABEL_OFFSET = ISO_PVD_OFFSET + 40
ISO_LABEL_LEN = 32


@dataclass(frozen=True)
class DiscInfo:
    kind: str                     # "data", "audio", "mixed" or "unknown"
    label: str | None             # ISO 9660 volume identifier, if any
    manifest: Any = None          # parsed config.Disc from the disc's discbay.toml
    manifest_error: str | None = None


def _open(device: str) -> int:
    # O_NONBLOCK lets us open the drive even when it is empty.
    return os.open(device, os.O_RDONLY | os.O_NONBLOCK)


def drive_status(device: str) -> str:
    """Return ABSENT, SPINNING, READY or UNAVAILABLE. Never raises."""
    try:
        fd = _open(device)
    except OSError:
        return UNAVAILABLE
    try:
        code = fcntl.ioctl(fd, CDROM_DRIVE_STATUS, CDSL_CURRENT)
    except OSError:
        return UNAVAILABLE
    finally:
        os.close(fd)

    if code == CDS_DISC_OK:
        return READY
    if code == CDS_DRIVE_NOT_READY:
        return SPINNING
    if code in (CDS_NO_DISC, CDS_TRAY_OPEN):
        return ABSENT
    return UNAVAILABLE


def disc_kind(device: str) -> str:
    try:
        fd = _open(device)
        try:
            code = fcntl.ioctl(fd, CDROM_DISC_STATUS, 0)
        finally:
            os.close(fd)
    except OSError:
        return "unknown"
    if code == CDS_AUDIO:
        return "audio"
    if code == CDS_MIXED:
        return "mixed"
    if code in (CDS_DATA_1, CDS_DATA_2, CDS_XA_2_1, CDS_XA_2_2):
        return "data"
    return "unknown"


def last_session_lba(device: str) -> int:
    """Where the newest session starts on a multisession disc (0 for single-session).

    A CD-R burned more than once holds one ISO 9660 filesystem per session;
    the newest one is the current view of the disc. Regular files (.iso
    images) always count as single-session.
    """
    try:
        if not stat.S_ISBLK(os.stat(device).st_mode):
            return 0
        fd = _open(device)
    except OSError:
        return 0
    try:
        # struct cdrom_multisession { union { msf; int lba; } addr; u8 xa_flag; u8 addr_format; }
        request = struct.pack("=iBB2x", 0, 0, CDROM_LBA)
        reply = fcntl.ioctl(fd, CDROMMULTISESSION, request)
        lba, xa_flag, _fmt = struct.unpack("=iBB2x", reply)
        return lba if xa_flag and lba > 0 else 0
    except OSError:
        return 0
    finally:
        os.close(fd)


def parse_iso_label(pvd_sector: bytes) -> str | None:
    """Extract the volume label from a 2048-byte Primary Volume Descriptor."""
    if len(pvd_sector) < 72:
        return None
    # Byte 0: descriptor type (1 = primary). Bytes 1-5: "CD001".
    if pvd_sector[0] != 1 or pvd_sector[1:6] != b"CD001":
        return None
    raw = pvd_sector[40:40 + ISO_LABEL_LEN]
    label = raw.decode("ascii", errors="replace").strip(" \x00")
    return label or None


def read_label(device: str, session_lba: int | None = None) -> str | None:
    """Read the ISO 9660 label of the newest session, without mounting."""
    if session_lba is None:
        session_lba = last_session_lba(device)
    try:
        fd = os.open(device, os.O_RDONLY)
    except OSError:
        return None
    try:
        os.lseek(fd, session_lba * 2048 + ISO_PVD_OFFSET, os.SEEK_SET)
        return parse_iso_label(os.read(fd, 2048))
    except OSError:
        return None
    finally:
        os.close(fd)


# ---------------------------------------------------------------- root directory
SECTOR = 2048
ROOT_RECORD_OFFSET = 156       # root directory record inside the PVD
MAX_ROOT_DIR = 256 * 1024


def _iso_name_variants(name: str) -> set[str]:
    """How a long file name may appear in the plain ISO 9660 directory."""
    upper = re.sub(r"[^A-Z0-9_.]", "_", name.upper())
    stem, dot, ext = upper.rpartition(".")
    if not dot:
        stem, ext = upper, ""
    variants = {upper, f"{stem[:8]}.{ext[:3]}" if ext else stem[:8]}
    return variants


def _normalise_record_name(raw: bytes) -> str:
    name = raw.decode("ascii", errors="replace").upper()
    name = name.split(";", 1)[0]
    return name.rstrip(".")


def list_root(read_at, session_lba: int = 0) -> list[tuple[str, int, int, bool]]:
    """List (name, lba, size, is_dir) in the root directory of one session.

    ``read_at(offset, length)`` reads from the disc or image, so this works on
    a device and on a plain .iso file alike. Extent addresses in a later
    session are absolute (they may point back into older sessions), so only
    the volume descriptor's position depends on ``session_lba``.
    """
    pvd = read_at(session_lba * SECTOR + ISO_PVD_OFFSET, SECTOR)
    if len(pvd) < SECTOR or pvd[0] != 1 or pvd[1:6] != b"CD001":
        return []
    rec = pvd[ROOT_RECORD_OFFSET:ROOT_RECORD_OFFSET + 34]
    root_lba = int.from_bytes(rec[2:6], "little")
    root_size = min(int.from_bytes(rec[10:14], "little"), MAX_ROOT_DIR)
    data = read_at(root_lba * SECTOR, root_size)

    entries, pos = [], 0
    while pos < len(data):
        length = data[pos]
        if length == 0:                       # padding up to the next sector
            pos = (pos // SECTOR + 1) * SECTOR
            continue
        if length < 34 or pos + length > len(data):
            break
        record = data[pos:pos + length]
        name_len = record[32]
        raw_name = record[33:33 + name_len]
        if raw_name not in (b"\x00", b"\x01"):  # skip "." and ".."
            entries.append((
                _normalise_record_name(raw_name),
                int.from_bytes(record[2:6], "little"),
                int.from_bytes(record[10:14], "little"),
                bool(record[25] & 0x02),
            ))
        pos += length
    return entries


def read_root_file(device: str, name: str, max_size: int = 8 * 1024 * 1024,
                   session_lba: int | None = None) -> bytes | None:
    """Read one file from the root directory of the disc's newest session, without mounting."""
    if session_lba is None:
        session_lba = last_session_lba(device)
    try:
        fd = os.open(device, os.O_RDONLY)
    except OSError:
        return None

    def read_at(offset: int, length: int) -> bytes:
        os.lseek(fd, offset, os.SEEK_SET)
        chunks, remaining = [], length
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    try:
        wanted = _iso_name_variants(name)
        for entry_name, lba, size, is_dir in list_root(read_at, session_lba):
            if not is_dir and entry_name in wanted:
                if size > max_size:
                    return None
                return read_at(lba * SECTOR, size)
        return None
    except OSError:
        return None
    finally:
        os.close(fd)


def root_names(device: str, session_lba: int | None = None) -> list[str]:
    """Names of the files in the root of the disc's newest session."""
    if session_lba is None:
        session_lba = last_session_lba(device)
    try:
        fd = os.open(device, os.O_RDONLY)
    except OSError:
        return []

    def read_at(offset: int, length: int) -> bytes:
        os.lseek(fd, offset, os.SEEK_SET)
        return os.read(fd, length)

    try:
        return [name for name, _lba, _size, is_dir in list_root(read_at, session_lba) if not is_dir]
    except OSError:
        return []
    finally:
        os.close(fd)


def media_state(device: str) -> str | None:
    """Ask udev's database whether the disc is "blank", "appendable" or "complete".

    The kernel's disc-status ioctl can't tell a blank disc from an unreadable
    one, but udev probes the media on every insert and records the answer.
    """
    try:
        st = os.stat(device)
        path = f"/run/udev/data/b{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("E:ID_CDROM_MEDIA_STATE="):
                    return line.split("=", 1)[1].strip() or None
    except (OSError, ValueError):
        pass
    return None


def probe(device: str) -> DiscInfo:
    kind = disc_kind(device)
    label = read_label(device) if kind in ("data", "mixed", "unknown") else None
    return DiscInfo(kind=kind, label=label)


def eject(device: str) -> bool:
    try:
        fd = _open(device)
    except OSError:
        return False
    try:
        try:
            fcntl.ioctl(fd, CDROM_LOCKDOOR, 0)
        except OSError:
            pass
        fcntl.ioctl(fd, CDROMEJECT, 0)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)
