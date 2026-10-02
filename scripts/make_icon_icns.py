#!/usr/bin/env python3
"""Build a macOS ``icon.icns`` from the PNGs already in the repo — stdlib only.

Why this exists: the owner's MacBook proved a claim in docs/PORTING.md a lie.
Every .sh ran fine (the LF fix held), the venv built, PyInstaller analysed and
collected everything -- and then died at the very last step::

    FileNotFoundError: Icon input file .../build-desktop/icon.icns not found

``build-mac.sh`` used to print "a missing .icns only costs the pretty icon",
which was wrong: the spec passed ``icon=`` unconditionally and PyInstaller
raises. So the fix is two-sided -- generate the real .icns (this script), and
make the spec refuse to die over a cosmetic asset (``scripts/make_icon_icns.py``).

The ICNS container is simple enough to write by hand: an ``icns`` magic + the
total file length, then one entry per icon slot, each a 4-byte OSType + a
4-byte big-endian length (header included) + the payload. Modern macOS
accepts raw PNG payloads in the high slots, so no .icns-specific re-encoding is
needed.

    python scripts/make_icon_icns.py            # write build-desktop/icon.icns
    python scripts/make_icon_icns.py --check    # verify an existing file
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ICNS = REPO / "build-desktop" / "icon.icns"

#: (OSType, source png, pixel size that OSType means). PyInstaller/macOS read
#: the slot type, not the pixel count, but a mismatch is still a bad citizen so
#: every size is verified against the PNG header before it is written.
SLOTS = [
    (b"ic07", "icon-128.png", 128),
    (b"ic08", "icon-256.png", 256),
    (b"ic09", "icon-512.png", 512),
    # The @2x retina slots reuse the same artwork at the same pixel sizes.
    (b"ic13", "icon-256.png", 256),
    (b"ic14", "icon-512.png", 512),
]

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def png_size(data: bytes) -> tuple[int, int]:
    """(width, height) straight out of the PNG IHDR chunk."""
    if not data.startswith(PNG_MAGIC):
        raise ValueError("not a PNG (bad magic)")
    if data[12:16] != b"IHDR":
        raise ValueError("PNG has no leading IHDR chunk")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def build_icns(sources: dict[bytes, tuple[str, int]]) -> bytes:
    entries = bytearray()
    for ostype, (png_bytes, declared) in sources.items():
        entries += ostype
        entries += struct.pack(">I", len(png_bytes) + 8)
        entries += png_bytes
    total = 8 + len(entries)
    return b"icns" + struct.pack(">I", total) + bytes(entries)


def parse_icns(data: bytes) -> dict[bytes, int]:
    """Entry OSType -> declared length, so --check can prove a file round-trips."""
    if data[:4] != b"icns":
        raise ValueError("missing 'icns' magic")
    total = struct.unpack(">I", data[4:8])[0]
    if total != len(data):
        raise ValueError(f"header says {total} bytes, file is {len(data)}")
    out: dict[bytes, int] = {}
    offset = 8
    while offset < len(data):
        ostype = data[offset:offset + 4]
        length = struct.unpack(">I", data[offset + 4:offset + 8])[0]
        if length < 8 or offset + length > len(data):
            raise ValueError(f"bad entry length {length} for {ostype!r}")
        out[ostype] = length - 8
        offset += length
    return out


def collect() -> dict[bytes, tuple[str, int]]:
    """Read + validate every source PNG. Missing sizes are skipped, not fatal."""
    chosen: dict[bytes, tuple[str, int]] = {}
    for ostype, filename, declared in SLOTS:
        path = REPO / "build-desktop" / filename
        if not path.is_file():
            print(f"[icns] skip {ostype.decode()}: {filename} is missing")
            continue
        data = path.read_bytes()
        width, height = png_size(data)
        if (width, height) != (declared, declared):
            raise SystemExit(
                f"[icns] {filename} is {width}x{height} but the "
                f"{ostype.decode()} slot means {declared}x{declared}")
        chosen[ostype] = (data, declared)
    if not chosen:
        raise SystemExit("[icns] no source PNGs in build-desktop/ — cannot build an icon")
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description="Build build-desktop/icon.icns")
    parser.add_argument("--check", action="store_true",
                        help="validate the existing file instead of writing")
    args = parser.parse_args()

    if args.check:
        if not ICNS.is_file():
            print(f"[icns] MISSING: {ICNS}")
            return 1
        entries = parse_icns(ICNS.read_bytes())
        print(f"[icns] OK {ICNS.name}: {len(entries)} slots, {ICNS.stat().st_size} bytes")
        for ostype, length in entries.items():
            print(f"        {ostype.decode()} -> {length} bytes")
        return 0

    chosen = collect()
    blob = build_icns(chosen)
    parsed = parse_icns(blob)  # never write a file we cannot read back
    ICNS.write_bytes(blob)
    print(f"[icns] wrote {ICNS} ({len(blob)} bytes, {len(parsed)} slots)")
    for ostype, length in parsed.items():
        print(f"        {ostype.decode()} -> {length} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())