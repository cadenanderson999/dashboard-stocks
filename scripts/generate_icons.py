#!/usr/bin/env python3
"""Generate PWA / home-screen PNG icons from the Buy Side Signals egg logo.

Draws the fried-egg-with-chart-arrow mark (cream bg, amber yolk, dark trend
arrow) at the sizes phones and browsers want. Run once; commit the PNGs.

    python scripts/generate_icons.py
"""

import os
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")

BG = (42, 33, 24, 255)          # dark warm brown backdrop (icon pops on it)
WHITE = (255, 253, 248, 255)
BROWN = (51, 41, 31, 255)       # --text / arrow
YOLK = (232, 163, 61, 255)      # amber
ORANGE = (223, 122, 43, 255)    # --accent-2


def draw_icon(size, pad=0.0):
    """pad: extra inset (fraction) so maskable icons keep content in safe zone."""
    img = Image.new("RGBA", (size, size), BG)
    d = ImageDraw.Draw(img)
    s = size

    def sc(x):  # scale a 0..1 fraction to pixels, honoring pad
        return (pad + x * (1 - 2 * pad)) * s

    # Egg white (ellipse blob) — high contrast against the dark backdrop.
    d.ellipse([sc(0.15), sc(0.22), sc(0.85), sc(0.74)], fill=WHITE)
    # Yolk.
    cx, cy, r = sc(0.5), sc(0.49), (0.165 * (1 - 2 * pad)) * s
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=YOLK, outline=ORANGE,
              width=max(2, int(s * 0.014)))
    # Chart trend line + arrowhead (inside the yolk).
    pts = [(sc(0.42), sc(0.56)), (sc(0.48), sc(0.49)),
           (sc(0.52), sc(0.52)), (sc(0.60), sc(0.43))]
    d.line(pts, fill=BROWN, width=max(2, int(s * 0.022)), joint="curve")
    head = [(sc(0.60), sc(0.43)), (sc(0.535), sc(0.435)), (sc(0.595), sc(0.50))]
    d.line([head[1], head[0]], fill=BROWN, width=max(2, int(s * 0.022)))
    d.line([head[0], head[2]], fill=BROWN, width=max(2, int(s * 0.022)))
    return img


def main():
    specs = [
        ("icon-192.png", 192, 0.0),
        ("icon-512.png", 512, 0.0),
        ("icon-maskable-512.png", 512, 0.10),  # safe-zone padding for maskable
        ("apple-touch-icon.png", 180, 0.0),
    ]
    for name, size, pad in specs:
        img = draw_icon(size, pad)
        path = os.path.join(OUT, name)
        img.save(path, "PNG")
        print(f"wrote {os.path.relpath(path)} ({size}x{size})")


if __name__ == "__main__":
    main()
