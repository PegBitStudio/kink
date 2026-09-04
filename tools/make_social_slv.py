"""Social image for post 4: the SLV concentration finding.

The argument is proportion -- one name ate half the book -- paired with the
reason why: a persistent gap the model kept re-scoring as fresh, and a
comparison group too thin to catch it.

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
MACRO = (123, 132, 148)
REFUSE = (199, 106, 99)
PASS = (63, 168, 124)
TRACK = (32, 38, 47)

FONTS = pathlib.Path("C:/Windows/Fonts")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size * S)


def build() -> Image.Image:
    img = Image.new("RGB", (W * S, H * S), GROUND)
    d = ImageDraw.Draw(img)

    f_head = font("georgiab.ttf", 56)
    f_sub = font("segoeui.ttf", 27)
    f_name = font("consola.ttf", 26)
    f_val = font("consolab.ttf", 30)
    f_small = font("consola.ttf", 18)
    f_kick = font("consolab.ttf", 25)
    f_foot = font("consola.ttf", 19)
    f_big = font("georgiab.ttf", 96)

    x = 92 * S

    d.text((x, 66 * S), "ALPACA  ×  LABLAB.AI", font=f_small, fill=INK_FAINT)
    d.text((x, 106 * S), "One name was half of", font=f_head, fill=INK)
    d.text((x, 172 * S), "everything my bot traded", font=f_head, fill=INK)

    # --- the big proportion stat --------------------------------------------
    d.text((x, 250 * S), "10 / 21", font=f_big, fill=IDIO)
    d.text((x + 320 * S, 285 * S), "approved trades\nwere the same\nsilver ETF",
           font=f_sub, fill=INK_SOFT)

    # --- the recurring gap: three days, same shape --------------------------
    y = 470 * S
    d.text((x, y), "SAME GAP, THREE DAYS STRAIGHT", font=f_small, fill=INK_FAINT)
    y += 34 * S

    rows = [
        ("Sep 1", 41.3, 37.6),
        ("Sep 2", 40.6, 37.5),
        ("Sep 3", 40.7, 38.5),
    ]
    bar_x = x + 110 * S
    bar_w = 760 * S
    lo, hi = 36.0, 43.0
    for label, iv, curve in rows:
        d.text((x, y + 4 * S), label, font=f_name, fill=INK)
        d.rounded_rectangle([bar_x, y, bar_x + bar_w, y + 34 * S], radius=8 * S, fill=TRACK)
        curve_x = bar_x + int(bar_w * (curve - lo) / (hi - lo))
        iv_x = bar_x + int(bar_w * (iv - lo) / (hi - lo))
        d.rounded_rectangle([bar_x, y, curve_x, y + 34 * S], radius=8 * S, fill=MACRO)
        d.rectangle([curve_x, y, iv_x, y + 34 * S], fill=IDIO)
        d.text((iv_x + 16 * S, y + 4 * S), f"{iv:.0f}% vs {curve:.0f}%",
               font=f_val, fill=IDIO)
        y += 56 * S

    d.text((x, y + 8 * S),
           "The gap never closed. The model kept scoring it as news anyway.",
           font=f_small, fill=INK_FAINT)

    # --- the kicker ----------------------------------------------------------
    ky = 752 * S
    d.rounded_rectangle([x, ky, x + (W - 184) * S, ky + 62 * S],
                        radius=10 * S, outline=REFUSE, width=2 * S)
    d.text((x + 24 * S, ky + 8 * S),
           "0 of 4 closed trades converged  ·  comparison group: 3 other names",
           font=f_kick, fill=REFUSE)

    d.line([(x, 848 * S), ((W - 92) * S, 848 * S)], fill=RULE, width=1 * S)
    d.text((x, 866 * S),
           "Kink  ·  an options agent that grades its own predictions on Alpaca paper trading",
           font=f_foot, fill=INK_FAINT)

    return img.resize((W, H), Image.LANCZOS)


if __name__ == "__main__":
    out = pathlib.Path(__file__).resolve().parents[1] / "docs" / "social-slv.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    build().save(out, "PNG", optimize=True)
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
