"""Social image for post 5: the closing scorecard.

The argument here is honesty as the pitch -- a stat grid, not a bar chart,
because this one is a tally rather than a comparison. Mixed colour coding:
the operational stats read as passes, the return reads as what it is.

1600x900, matching the established visual system.
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
REFUSE = (199, 106, 99)
PASS = (63, 168, 124)
CARD = (24, 28, 35)

FONTS = pathlib.Path("C:/Windows/Fonts")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size * S)


STATS = [
    ("93", "autonomous cycles", PASS),
    ("21", "trades opened, zero approvals\nfrom a human", PASS),
    ("153 / 153", "tests passing", PASS),
    ("-5.2%", "account return over 3 days", REFUSE),
]


def build() -> Image.Image:
    img = Image.new("RGB", (W * S, H * S), GROUND)
    d = ImageDraw.Draw(img)

    f_head = font("georgiab.ttf", 54)
    f_sub = font("segoeui.ttf", 26)
    f_stat = font("georgiab.ttf", 64)
    f_label = font("segoeui.ttf", 21)
    f_small = font("consola.ttf", 18)
    f_kick = font("consolab.ttf", 25)
    f_foot = font("consola.ttf", 19)

    x = 92 * S

    d.text((x, 64 * S), "ALPACA  ×  LABLAB.AI", font=f_small, fill=INK_FAINT)
    d.text((x, 104 * S), "Three days. Full autonomy.", font=f_head, fill=INK)
    d.text((x, 168 * S), "Here's the honest scorecard.", font=f_head, fill=INK)
    d.text((x, 240 * S),
           "It decided what to trade, sized it, and exited on its own.",
           font=f_sub, fill=INK_SOFT)
    d.text((x, 276 * S),
           "Not everything it decided was right.",
           font=f_sub, fill=INK_SOFT)

    # --- the 2x2 stat grid ---------------------------------------------------
    grid_top = 350 * S
    card_w = 660 * S
    card_h = 150 * S
    gap = 30 * S
    positions = [
        (x, grid_top),
        (x + card_w + gap, grid_top),
        (x, grid_top + card_h + gap),
        (x + card_w + gap, grid_top + card_h + gap),
    ]

    for (px, py), (value, label, colour) in zip(positions, STATS):
        d.rounded_rectangle([px, py, px + card_w, py + card_h],
                            radius=12 * S, fill=CARD, outline=RULE, width=1 * S)
        d.text((px + 28 * S, py + 22 * S), value, font=f_stat, fill=colour)
        # wrap label onto up to two lines
        lines = label.split("\n")
        ly = py + 100 * S
        for line in lines:
            d.text((px + 30 * S, ly), line, font=f_label, fill=INK_SOFT)
            ly += 26 * S

    # --- the kicker ------------------------------------------------------------
    ky = 752 * S
    d.rounded_rectangle([x, ky, x + (W - 184) * S, ky + 62 * S],
                        radius=10 * S, outline=IDIO, width=2 * S)
    d.text((x + 24 * S, ky + 8 * S),
           "It failed legibly. Every decision logged, every mistake findable.",
           font=f_kick, fill=IDIO)

    d.line([(x, 848 * S), ((W - 92) * S, 848 * S)], fill=RULE, width=1 * S)
    d.text((x, 866 * S),
           "Kink  ·  an options agent that grades its own predictions on Alpaca paper trading",
           font=f_foot, fill=INK_FAINT)

    return img.resize((W, H), Image.LANCZOS)


if __name__ == "__main__":
    out = pathlib.Path(__file__).resolve().parents[1] / "docs" / "social-scorecard.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    build().save(out, "PNG", optimize=True)
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
