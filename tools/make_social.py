"""Render the social image for the build-in-public thread.

The thread's argument is one idea: a kink that every ticker shows at the same
expiration is a macro event, not a mispricing. So the image is that
subtraction, drawn twice -- once for a name that survives it and once for a
name that collapses under it.

A screenshot of the dashboard would carry browser chrome and whatever the crop
caught. This is built for the timeline: 1600x900, legible at the size X
actually renders it, and no text small enough to disappear in the feed.
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
PASS = (63, 168, 124)
REFUSE = (199, 106, 99)
TRACK = (32, 38, 47)

FONTS = pathlib.Path("C:/Windows/Fonts")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size * S)


# The two names from the first live scan, with their real decomposition.
ROWS = [
    {
        "name": "IWM",
        "raw": 0.107, "macro": 0.044, "idio": 0.063,
        "verdict": "TRADE", "colour": PASS,
        "note": "richer than its peers — the trade",
    },
    {
        "name": "QQQ",
        "raw": 0.063, "macro": 0.044, "idio": 0.019,
        "verdict": "REFUSED", "colour": REFUSE,
        "note": "the whole tape showed this — it was the calendar",
    },
]


def build() -> Image.Image:
    img = Image.new("RGB", (W * S, H * S), GROUND)
    d = ImageDraw.Draw(img)

    f_head = font("georgiab.ttf", 62)
    f_sub = font("segoeui.ttf", 27)
    f_name = font("consolab.ttf", 34)
    f_num = font("consola.ttf", 25)
    f_lab = font("consola.ttf", 20)
    f_verdict = font("consolab.ttf", 20)
    f_foot = font("consola.ttf", 19)

    x = 96 * S

    d.text((x, 82 * S), "ALPACA  ×  LABLAB.AI", font=f_lab, fill=INK_FAINT)
    d.text((x, 126 * S), "77 mispricings that were", font=f_head, fill=INK)
    d.text((x, 200 * S), "really the FOMC", font=f_head, fill=INK)
    d.text((x, 300 * S),
           "The biggest kinks were rich in every ticker at once.",
           font=f_sub, fill=INK_SOFT)
    d.text((x, 340 * S),
           "So subtract what every peer shows at the same expiration.",
           font=f_sub, fill=INK_SOFT)

    # --- the two bars ------------------------------------------------------
    bar_x0 = x
    bar_w = 1120 * S
    scale = max(r["raw"] for r in ROWS)
    y = 452 * S

    for r in ROWS:
        d.text((bar_x0, y), r["name"], font=f_name, fill=INK)

        # verdict pill, right of the name
        vx = bar_x0 + 110 * S
        vw = (len(r["verdict"]) * 13 + 22) * S
        d.rounded_rectangle([vx, y + 4 * S, vx + vw, y + 36 * S],
                            radius=16 * S, outline=r["colour"], width=2 * S)
        d.text((vx + 11 * S, y + 10 * S), r["verdict"], font=f_verdict, fill=r["colour"])

        by = y + 58 * S
        bh = 34 * S
        d.rounded_rectangle([bar_x0, by, bar_x0 + bar_w, by + bh],
                            radius=8 * S, fill=TRACK)

        mw = int(bar_w * (r["macro"] / scale))
        iw = int(bar_w * (r["idio"] / scale))
        d.rounded_rectangle([bar_x0, by, bar_x0 + mw, by + bh], radius=8 * S, fill=MACRO)
        d.rectangle([bar_x0 + mw - 8 * S, by, bar_x0 + mw + iw, by + bh], fill=IDIO)
        d.rounded_rectangle([bar_x0 + mw, by, bar_x0 + mw + iw, by + bh],
                            radius=8 * S, fill=IDIO)

        d.text((bar_x0 + bar_w + 22 * S, by + 4 * S),
               f"idio {r['idio']:+.1%}", font=f_num, fill=r["colour"])

        d.text((bar_x0, by + bh + 12 * S), r["note"], font=f_lab, fill=INK_FAINT)
        y += 162 * S

    # --- legend ------------------------------------------------------------
    ly = 786 * S
    d.rectangle([x, ly, x + 20 * S, ly + 20 * S], fill=MACRO)
    d.text((x + 32 * S, ly - 2 * S), "macro — shared by the whole asset class",
           font=f_lab, fill=INK_SOFT)
    lx2 = x + 560 * S
    d.rectangle([lx2, ly, lx2 + 20 * S, ly + 20 * S], fill=IDIO)
    d.text((lx2 + 32 * S, ly - 2 * S), "idiosyncratic — specific to this name",
           font=f_lab, fill=INK_SOFT)

    d.line([(x, 838 * S), ((W - 96) * S, 838 * S)], fill=RULE, width=1 * S)
    d.text((x, 856 * S),
           "Kink  ·  a term-structure options agent on Alpaca paper trading",
           font=f_foot, fill=INK_FAINT)

    return img.resize((W, H), Image.LANCZOS)


if __name__ == "__main__":
    out = pathlib.Path(__file__).resolve().parents[1] / "docs" / "social-decomposition.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    build().save(out, "PNG", optimize=True)
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
