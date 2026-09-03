"""Social image for the third post: the evidence against our own idea.

The argument is a single shape -- convergence falling as the gap grows, when
the whole premise says it should rise. So the picture is four bars getting
shorter while the label above them gets bigger, which is the wrong way round
and looks it.

1600x900, built for the timeline.
"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

W, H = 1600, 900
S = 2

GROUND = (13, 16, 21)
INK = (231, 234, 239)
INK_SOFT = (154, 164, 177)
INK_FAINT = (109, 119, 132)
RULE = (39, 45, 55)
IDIO = (215, 141, 43)
MACRO = (123, 132, 148)
REFUSE = (199, 106, 99)
PASS = (63, 168, 124)
TRACK = (32, 38, 47)

FONTS = pathlib.Path("C:/Windows/Fonts")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size * S)


# The live calibration table, on 233 scored predictions.
ROWS = [
    ("small gaps",  0.81, "81%", 174, PASS),
    ("medium gaps", 0.75, "75%",  39, PASS),
    ("large gaps",  0.42, "42%",  12, MACRO),
    ("huge gaps",   0.10, "10%",   8, REFUSE),
]


def build() -> Image.Image:
    img = Image.new("RGB", (W * S, H * S), GROUND)
    d = ImageDraw.Draw(img)

    f_head = font("georgiab.ttf", 58)
    f_sub = font("segoeui.ttf", 27)
    f_name = font("consola.ttf", 26)
    f_val = font("consolab.ttf", 30)
    f_small = font("consola.ttf", 18)
    f_kick = font("consolab.ttf", 26)
    f_foot = font("consola.ttf", 19)

    x = 92 * S
    d.text((x, 70 * S), "ALPACA  ×  LABLAB.AI", font=f_small, fill=INK_FAINT)
    d.text((x, 112 * S), "My trading program says", font=f_head, fill=INK)
    d.text((x, 180 * S), "my idea might be wrong", font=f_head, fill=INK)
    d.text((x, 268 * S),
           "How much of the mispricing actually closed, a day later.",
           font=f_sub, fill=INK_SOFT)
    d.text((x, 306 * S),
           "The whole idea says bigger gaps should close more.",
           font=f_sub, fill=INK_SOFT)

    # --- the four bars -----------------------------------------------------
    bar_x = x + 230 * S
    bar_w = 900 * S
    y = 392 * S

    for label, frac, text, n, colour in ROWS:
        d.text((x, y + 2 * S), label, font=f_name, fill=INK)

        bh = 40 * S
        d.rounded_rectangle([bar_x, y, bar_x + bar_w, y + bh], radius=9 * S, fill=TRACK)
        fill_w = max(int(bar_w * frac), 12 * S)
        d.rounded_rectangle([bar_x, y, bar_x + fill_w, y + bh], radius=9 * S, fill=colour)

        d.text((bar_x + bar_w + 26 * S, y + 4 * S), text, font=f_val, fill=colour)
        d.text((bar_x + bar_w + 122 * S, y + 12 * S), f"n={n}", font=f_small, fill=INK_FAINT)
        y += 74 * S

    d.text((x, y + 12 * S),
           "Backwards. The big obvious opportunities are the ones that never come back.",
           font=f_small, fill=INK_FAINT)

    # --- the kicker --------------------------------------------------------
    ky = 752 * S
    d.rounded_rectangle([x, ky, x + (W - 184) * S, ky + 62 * S],
                        radius=10 * S, outline=IDIO, width=2 * S)
    d.text((x + 24 * S, ky + 17 * S),
           "9,500 readings  —  including every trade it refused to take",
           font=f_kick, fill=IDIO)

    d.line([(x, 848 * S), ((W - 92) * S, 848 * S)], fill=RULE, width=1 * S)
    d.text((x, 866 * S),
           "Kink  ·  an options agent that grades its own predictions on Alpaca paper trading",
           font=f_foot, fill=INK_FAINT)

    return img.resize((W, H), Image.LANCZOS)


if __name__ == "__main__":
    out = pathlib.Path(__file__).resolve().parents[1] / "docs" / "social-calibration.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    build().save(out, "PNG", optimize=True)
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
