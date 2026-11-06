#!/usr/bin/env python3
"""Generate Witchbeat's install icons with Python's standard library only."""
from pathlib import Path
import struct
import zlib

ROOT = Path(__file__).resolve().parent


def mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


class Canvas:
    def __init__(self, size):
        self.size = size
        self.pixels = bytearray(size * size * 4)
        top = (33, 26, 46)
        bottom = (14, 13, 20)
        for y in range(size):
            t = y / max(1, size - 1)
            color = mix(top, bottom, t)
            row = y * size * 4
            for x in range(size):
                i = row + x * 4
                self.pixels[i:i + 4] = bytes((*color, 255))

    def rounded_rect(self, x, y, width, height, radius, color, outline=None, line=1):
        size = self.size
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(size, int(x + width)), min(size, int(y + height))
        for py in range(y0, y1):
            for px in range(x0, x1):
                near_x = min(max(px + .5, x + radius), x + width - radius)
                near_y = min(max(py + .5, y + radius), y + height - radius)
                dx, dy = px + .5 - near_x, py + .5 - near_y
                if dx * dx + dy * dy > radius * radius:
                    continue
                edge = (px < x + line or px >= x + width - line or
                        py < y + line or py >= y + height - line)
                chosen = outline if outline and edge else color
                if chosen is not None:
                    i = (py * size + px) * 4
                    self.pixels[i:i + 4] = bytes((*chosen, 255))

    def line(self, x0, y0, x1, y1, color, width):
        steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0))))
        radius = width / 2
        for step in range(steps + 1):
            t = step / steps
            cx = x0 + (x1 - x0) * t
            cy = y0 + (y1 - y0) * t
            self.circle(cx, cy, radius, color)

    def circle(self, cx, cy, radius, color):
        size = self.size
        r = int(radius + 1)
        for y in range(max(0, int(cy) - r), min(size, int(cy) + r + 1)):
            for x in range(max(0, int(cx) - r), min(size, int(cx) + r + 1)):
                if (x + .5 - cx) ** 2 + (y + .5 - cy) ** 2 <= radius * radius:
                    i = (y * size + x) * 4
                    self.pixels[i:i + 4] = bytes((*color, 255))

    def png(self, path):
        raw = bytearray()
        stride = self.size * 4
        for y in range(self.size):
            raw.append(0)  # PNG filter: None
            raw.extend(self.pixels[y * stride:(y + 1) * stride])

        def chunk(kind, data):
            return (struct.pack(">I", len(data)) + kind + data +
                    struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))

        header = struct.pack(">IIBBBBB", self.size, self.size, 8, 6, 0, 0, 0)
        data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) +
                chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
        Path(path).write_bytes(data)


def make_icon(size):
    c = Canvas(size)
    s = size
    c.rounded_rect(s * .055, s * .055, s * .89, s * .89, s * .2,
                   (22, 19, 30), outline=(75, 66, 88), line=max(1, int(s * .008)))
    c.rounded_rect(s * .14, s * .14, s * .72, s * .69, s * .13,
                   (18, 16, 24), outline=(52, 46, 62), line=max(1, int(s * .006)))

    cell = s * .09
    gap = s * .036
    grid = 4 * cell + 3 * gap
    start_x = (s - grid) / 2
    start_y = s * .235
    active = {(0, 0), (0, 2), (1, 1), (2, 0), (2, 2), (3, 1), (3, 3)}
    colors = [(210, 255, 103), (255, 113, 139), (100, 217, 208), (189, 154, 255)]
    off = (54, 49, 63)
    for row in range(4):
        for col in range(4):
            x = start_x + col * (cell + gap)
            y = start_y + row * (cell + gap)
            color = colors[(row + col) % len(colors)] if (row, col) in active else off
            c.rounded_rect(x, y, cell, cell, s * .026, color)
            if (row, col) in active:
                c.rounded_rect(x + cell * .22, y + cell * .22, cell * .56, cell * .56,
                               s * .018, tuple(int(channel * .45) for channel in color))

    # A tiny three-beat meter under the step pads.
    meter_y = s * .79
    for index, height in enumerate((.025, .048, .034, .06, .03)):
        x = s * (.32 + index * .09)
        c.rounded_rect(x, meter_y - s * height, s * .038, s * height,
                       s * .018, colors[index % len(colors)])
    c.circle(s * .79, s * .205, s * .018, (210, 255, 103))
    return c


(ROOT / "icons").mkdir(parents=True, exist_ok=True)
base = make_icon(512)
base.png(ROOT / "icons" / "icon-512.png")
base.png(ROOT / "icons" / "icon-512-maskable.png")
make_icon(192).png(ROOT / "icons" / "icon-192.png")
make_icon(192).png(ROOT / "icons" / "icon-192-maskable.png")
make_icon(180).png(ROOT / "apple-touch-icon.png")
print("Witchbeat icons generated in", ROOT)
