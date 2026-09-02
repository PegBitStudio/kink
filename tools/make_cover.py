"""Render the project cover image.

The cover has one job: show what the project trades, in a glance, at thumbnail
size. So the picture is the trade itself -- a term structure with the
neighbour-implied level drawn beside the actual one and the gap between them
filled. Someone scrolling a gallery of forty agents sees a shape they have not
seen on the other thirty-nine.

Drawn at 3x and downsampled, because Pillow does not antialias lines.
"""
from __future__ import annotations

import math
import pathlib

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
S = 3                      # supersampling factor

GROUND = (13, 16, 21)
INK = (231, 234, 239)
INK_SOFT = (154, 164, 177)
INK_FAINT = (109, 119, 132)
RULE = (39, 45, 55)
IDIO = (215, 141, 43)
MACRO = (123, 132, 148)

FONTS = pathlib.Path("C:/Windows/Fonts")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size * S)


def dashed(draw, p0, p1, *, fill, width, dash=14, gap=10):
    """Pillow has no dashed stroke, so walk the segment."""
    (x0, y0), (x1, y1) = p0, p1
    total = math.hypot(x1 - x0, y1 - y0)
    if total == 0:
        return
    ux, uy = (x1 - x0) / total, (y1 - y0) / total
    pos = 0.0
    while pos < total:
        end = min(pos + dash, total)
        draw.line(
            [(x0 + ux * pos, y0 + uy * pos), (x0 + ux * end, y0 + uy * end)],
            fill=fill, width=width,
        )
        pos = end + gap


def build() -> Image.Image:
    img = Image.new("RGB", (W * S, H * S), GROUND)
    d = ImageDraw.Draw(img)

    f_title = font("georgiab.ttf", 96)
    f_lede = font("segoeui.ttf", 26)
    f_mono = font("consola.ttf", 16)
    f_mono_sm = font("consola.ttf", 14)
    f_edge = font("consolab.ttf", 15)

    # --- left: the words ---------------------------------------------------
    x = 78 * S
    d.text((x, 150 * S), "ALPACA  ×  LABLAB.AI", font=f_mono_sm, fill=INK_FAINT)
    d.text((x, 190 * S), "Kink", font=f_title, fill=INK)
    d.text(
        (x, 330 * S),
        "Trading the shape of the",
        font=f_lede, fill=INK_SOFT,
    )
    d.text((x, 368 * S), "volatility surface.", font=f_lede, fill=INK_SOFT)

    # A single line of substance, not a tagline.
    d.text(
        (x, 440 * S),
        "Sells an expiration richer than its neighbours imply —",
        font=f_mono_sm, fill=INK_FAINT,
    )
    d.text(
        (x, 464 * S),
        "minus what every peer shows at the same date.",
        font=f_mono_sm, fill=INK_FAINT,
    )

    # --- right: the trade --------------------------------------------------
    # A term structure in sqrt-time with one expiration standing above the
    # line its neighbours imply. That gap is the entire strategy.
    gx0, gx1 = 660 * S, 1122 * S
    gy0, gy1 = 170 * S, 470 * S

    for frac in (0.0, 0.5, 1.0):
        y = gy0 + (gy1 - gy0) * frac
        d.line([(gx0, y), (gx1, y)], fill=RULE, width=1 * S)

    # fy is the implied-vol level: larger means richer, so larger must sit
    # HIGHER on screen. A term structure normally rises with time, and the one
    # rich expiration spikes above the line its neighbours imply.
    pts_norm = [
        (0.00, 0.14), (0.14, 0.26), (0.27, 0.38),
        (0.40, 0.84),                                  # the rich expiration
        (0.53, 0.50), (0.68, 0.58), (0.84, 0.66), (1.00, 0.74),
    ]
    pts = [
        (gx0 + (gx1 - gx0) * fx, gy1 - (gy1 - gy0) * fy)
        for fx, fy in pts_norm
    ]
    d.line(pts, fill=INK, width=3 * S, joint="curve")

    kink = pts[3]
    left, right = pts[2], pts[4]
    # where the neighbours say that expiration should sit
    expected_y = (left[1] + right[1]) / 2

    dashed(d, (kink[0] - 70 * S, expected_y), (kink[0] + 48 * S, expected_y),
           fill=MACRO, width=3 * S, dash=13 * S, gap=9 * S)

    # The rich expiration sits above the implied level, so its y is the smaller
    # of the two in screen coordinates. Order the pair rather than assuming.
    top, bottom = sorted((kink[1], expected_y))
    d.rectangle([kink[0] - 5 * S, top, kink[0] + 5 * S, bottom], fill=IDIO)
    r = 8 * S
    d.ellipse([kink[0] - r, kink[1] - r, kink[0] + r, kink[1] + r], fill=IDIO)

    # Labels sit clear of the line: the edge above the peak, the implied level
    # out to the left of its own dash so neither crosses the curve.
    d.text((kink[0] + 22 * S, kink[1] - 12 * S), "the edge", font=f_edge, fill=IDIO)
    d.text((kink[0] - 208 * S, expected_y - 11 * S), "neighbours imply",
           font=f_mono_sm, fill=MACRO)

    d.text((gx0, gy1 + 22 * S), "7d", font=f_mono_sm, fill=INK_FAINT)
    d.text((gx1 - 26 * S, gy1 + 22 * S), "90d", font=f_mono_sm, fill=INK_FAINT)

    # --- footer ------------------------------------------------------------
    d.line([(x, 545 * S), ((W - 78) * S, 545 * S)], fill=RULE, width=1 * S)
    d.text((x, 566 * S),
           "defined-risk calendars  ·  the model can only veto  ·  every decision journalled",
           font=f_mono, fill=INK_FAINT)

    return img.resize((W, H), Image.LANCZOS)


if __name__ == "__main__":
    out = pathlib.Path(__file__).resolve().parents[1] / "docs" / "cover.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    build().save(out, "PNG", optimize=True)
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
