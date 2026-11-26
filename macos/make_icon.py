#!/usr/bin/env python3
"""Generate the Music Suite .app icon as a PNG (stdlib only)."""

import math
import struct
import sys
import zlib

SIZE = 1024


def chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def pixel(x: int, y: int) -> tuple[int, int, int, int]:
    # Rounded dark background with a soft vertical gradient.
    edge = 150
    dx = max(edge - x, 0, x - (SIZE - 1 - edge))
    dy = max(edge - y, 0, y - (SIZE - 1 - edge))
    if dx and dy and math.hypot(dx, dy) > edge:
        return (0, 0, 0, 0)

    t = y / (SIZE - 1)
    r = int(11 + 12 * t)
    g = int(18 + 26 * t)
    b = int(34 + 40 * t)

    # Five cyan waveform bars, centred and symmetric.
    bar_centres = [300, 410, 520, 630, 740]
    bar_heights = [150, 300, 430, 260, 120]
    for cx, height in zip(bar_centres, bar_heights):
        if abs(x - cx) <= 34 and abs(y - SIZE // 2) <= height:
            return (34, 211, 238, 255)
    return (r, g, b, 255)


def main() -> None:
    output = sys.argv[1] if len(sys.argv) > 1 else "AppIcon.png"
    raw = bytearray()
    for y in range(SIZE):
        raw.append(0)
        for x in range(SIZE):
            raw.extend(pixel(x, y))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    with open(output, "wb") as handle:
        handle.write(png)
    print(output)


if __name__ == "__main__":
    main()
