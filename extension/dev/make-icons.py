#!/usr/bin/env python3
"""Generate the extension icons (a soft accent-coloured disc with a pause bar) as PNGs.

Stdlib only. Run from anywhere: `python3 extension/dev/make-icons.py`.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

ACCENT = (0x7A, 0xA2, 0xF7)
INK = (0x1A, 0x1B, 0x26)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def render(size: int) -> bytes:
    """Return PNG bytes for a size×size RGBA icon."""
    cx = cy = (size - 1) / 2
    radius = size / 2 - 0.5
    bar_w = max(1.0, size * 0.11)
    bar_h = size * 0.34
    gap = size * 0.09
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            cover = min(1.0, max(0.0, radius - d + 0.5))
            if cover <= 0:
                row += bytes((0, 0, 0, 0))
                continue
            dx, dy = abs(x - cx), abs(y - cy)
            in_bar = gap / 2 <= dx <= gap / 2 + bar_w and dy <= bar_h / 2
            r, g, b = INK if in_bar else ACCENT
            row += bytes((r, g, b, int(255 * cover)))
        rows.append(bytes(row))
    raw = b"".join(rows)
    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += _chunk(b"IDAT", zlib.compress(raw, 9))
    png += _chunk(b"IEND", b"")
    return png


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "icons"
    out.mkdir(exist_ok=True)
    for size in (16, 48, 128):
        (out / f"icon{size}.png").write_bytes(render(size))
        print(f"wrote {out / f'icon{size}.png'}")


if __name__ == "__main__":
    main()
