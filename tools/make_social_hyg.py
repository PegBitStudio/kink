"""Social image for the "my best trade was untradeable" post.

The post's argument is that the same two candidates swap places depending on
which ruler you use. So the image is one dataset scored twice, side by side,
with the ranking visibly flipping between the panels -- and the spread that
made the winner untradeable at any price sitting underneath.

1600x900, built for the feed rather than cropped from a dashboard.
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


# The same two candidates, scored two ways. Real numbers from the first scan.
PANELS = [
    {
        "title": "Scored in percent",
        "sub": "how it looked",
        "rows": [("HYG", 23.0, "+23.0%", IDIO), ("IWM", 11.0, "+11.0%", MACRO)],
        "max": 24.0,
    },
    {
        "title": "Scored in volatility points",
        "sub": "what it actually was",
        "rows": [("HYG", 0.99, "0.99 pts", MACRO), ("IWM", 1.04, "1.04 pts", PASS)],
        "max": 1.15,
    },
]


def build() -> Image.Image:
    img = Image.new("RGB", (W * S, H * S), GROUND)
    d = ImageDraw.Draw(img)

    f_head = font("georgiab.ttf", 60)
    f_sub = font("segoeui.ttf", 26)
    f_panel = font("segoeui.ttf", 23)
    f_panel_sub = font("consola.ttf", 17)
    f_name = font("consolab.ttf", 30)
    f_val = font("consola.ttf", 22)
    f_kick = font("consolab.ttf", 27)
    f_foot = font("consola.ttf", 19)

    x = 92 * S
    d.text((x, 74 * S), "ALPACA  ×  LABLAB.AI", font=f_panel_sub, fill=INK_FAINT)
    d.text((x, 116 * S), "My best-scoring trade", font=f_head, fill=INK)
    d.text((x, 186 * S), "was untradeable", font=f_head, fill=INK)
    d.text((x, 276 * S),
           "Same two candidates. Two ways of measuring. Opposite answers.",
           font=f_sub, fill=INK_SOFT)

    # --- the two panels ----------------------------------------------------
    panel_w = 660 * S
    gap = 76 * S
    top = 372 * S

    for i, panel in enumerate(PANELS):
        px = x + i * (panel_w + gap)
        d.text((px, top), panel["title"], font=f_panel, fill=INK)
        d.text((px, top + 36 * S), panel["sub"], font=f_panel_sub, fill=INK_FAINT)
        d.line([(px, top + 68 * S), (px + panel_w, top + 68 * S)], fill=RULE, width=1 * S)

        by = top + 96 * S
        for name, value, label, colour in panel["rows"]:
            d.text((px, by), name, font=f_name, fill=INK)

            bar_x = px + 92 * S
            bar_w = panel_w - 92 * S - 132 * S
            bh = 26 * S
            d.rounded_rectangle([bar_x, by + 5 * S, bar_x + bar_w, by + 5 * S + bh],
                                radius=7 * S, fill=TRACK)
            fill_w = int(bar_w * (value / panel["max"]))
            d.rounded_rectangle([bar_x, by + 5 * S, bar_x + fill_w, by + 5 * S + bh],
                                radius=7 * S, fill=colour)
            d.text((bar_x + bar_w + 18 * S, by + 6 * S), label, font=f_val, fill=colour)
            by += 68 * S

    # A one-line note that names the flip, so the picture is not a puzzle.
    d.text((x, top + 246 * S),
           "One ruler says HYG wins by 2x. The other says they are the same trade.",
           font=f_panel_sub, fill=INK_FAINT)

    # --- the kicker --------------------------------------------------------
    ky = 736 * S
    d.rounded_rectangle([x, ky, x + (W - 184) * S, ky + 66 * S],
                        radius=10 * S, outline=REFUSE, width=2 * S)
    d.text((x + 24 * S, ky + 19 * S),
           "HYG's bid/ask spread: 187%   —   untradeable at any price",
           font=f_kick, fill=REFUSE)

    d.line([(x, 846 * S), ((W - 92) * S, 846 * S)], fill=RULE, width=1 * S)
    d.text((x, 864 * S),
           "Kink  ·  a term-structure options agent on Alpaca paper trading",
           font=f_foot, fill=INK_FAINT)

    return img.resize((W, H), Image.LANCZOS)


if __name__ == "__main__":
    out = pathlib.Path(__file__).resolve().parents[1] / "docs" / "social-two-rulers.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    build().save(out, "PNG", optimize=True)
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
