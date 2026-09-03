"""Team profile cover for lablab.

Different job from the project cover. That one explains a strategy; this one is
an identity in a grid of other teams' identities, seen small. So it is built as
an emblem: one mark, one name, one line -- and a lot of empty space, because
most team covers are crowded and crowding is what makes them blur together.

The mark is the term structure with its kink, drawn large and bare. Anyone who
has seen the project recognises it instantly; anyone who has not sees a shape
that clearly means something.
"""
from __future__ import annotations

import math
import pathlib

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
S = 3

GROUND = (13, 16, 21)
INK = (231, 234, 239)
INK_SOFT = (154, 164, 177)
INK_FAINT = (109, 119, 132)
RULE = (34, 40, 49)
IDIO = (215, 141, 43)

TEAM_NAME = "Kink"
TAGLINE = "we build systems that argue with us"

FONTS = pathlib.Path("C:/Windows/Fonts")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size * S)


def build() -> Image.Image:
    img = Image.new("RGB", (W * S, H * S), GROUND)
    d = ImageDraw.Draw(img)

    f_name = font("georgiab.ttf", 132)
    f_tag = font("segoeui.ttf", 25)
    f_mono = font("consola.ttf", 17)

    # --- the mark, drawn big and low so the type sits above it -------------
    gx0, gx1 = 120 * S, 1080 * S
    base = 470 * S
    height = 210 * S

    # A rising curve with one expiration standing proud of its neighbours.
    pts_norm = [
        (0.00, 0.10), (0.13, 0.22), (0.26, 0.33),
        (0.39, 0.92),                                   # the kink
        (0.52, 0.46), (0.66, 0.55), (0.82, 0.64), (1.00, 0.72),
    ]
    pts = [
        (gx0 + (gx1 - gx0) * fx, base - height * fy)
        for fx, fy in pts_norm
    ]

    # A faint floor line so the curve has something to stand on.
    d.line([(gx0, base + 8 * S), (gx1, base + 8 * S)], fill=RULE, width=2 * S)

    d.line(pts, fill=INK, width=5 * S, joint="curve")

    kink = pts[3]
    left, right = pts[2], pts[4]
    expected_y = (left[1] + right[1]) / 2

    # the gap: the whole idea, in one vertical bar
    d.rectangle(
        [kink[0] - 7 * S, kink[1], kink[0] + 7 * S, expected_y],
        fill=IDIO,
    )
    r = 12 * S
    d.ellipse([kink[0] - r, kink[1] - r, kink[0] + r, kink[1] + r], fill=IDIO)

    # dashed level the neighbours imply
    dash, gap = 16 * S, 11 * S
    x = kink[0] - 96 * S
    while x < kink[0] + 96 * S:
        d.line([(x, expected_y), (min(x + dash, kink[0] + 96 * S), expected_y)],
               fill=INK_FAINT, width=3 * S)
        x += dash + gap

    # --- the name ----------------------------------------------------------
    d.text((120 * S, 62 * S), TEAM_NAME, font=f_name, fill=INK)
    d.text((127 * S, 226 * S), TAGLINE, font=f_tag, fill=IDIO)

    # --- footer ------------------------------------------------------------
    d.text((120 * S, 540 * S),
           "ALPACA  ×  LABLAB.AI     ·     AI TRADING AGENTS     ·     SEPT 2026",
           font=f_mono, fill=INK_FAINT)

    return img.resize((W, H), Image.LANCZOS)


if __name__ == "__main__":
    out = pathlib.Path(__file__).resolve().parents[1] / "docs" / "team-cover.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    build().save(out, "PNG", optimize=True)
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
