"""Social image for the closing LinkedIn post: the compressed runway.

The argument is a timeline, not a bar chart -- seven days of hackathon, three
of them ours. The unused days are drawn hollow; the used ones are filled and
labelled with what actually happened that day. The point is proportion, made
spatially rather than with a stat.

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
TRACK = (32, 38, 47)
GHOST = (24, 28, 35)

FONTS = pathlib.Path("C:/Windows/Fonts")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size * S)


DAYS = [
    ("Aug\n28", "kickoff", False, None),
    ("29", "", False, None),
    ("30", "", False, None),
    ("31", "", False, None),
    ("Sep\n1", "built + first trade", True, "day 1"),
    ("2", "traded", True, "day 2"),
    ("3", "traded", True, "day 3"),
    ("4", "deadline", False, None),
]


def build() -> Image.Image:
    img = Image.new("RGB", (W * S, H * S), GROUND)
    d = ImageDraw.Draw(img)

    f_head = font("georgiab.ttf", 54)
    f_sub = font("segoeui.ttf", 27)
    f_day = font("consolab.ttf", 24)
    f_label = font("consola.ttf", 17)
    f_small = font("consola.ttf", 18)
    f_kick = font("consolab.ttf", 24)
    f_foot = font("consola.ttf", 19)

    x = 92 * S

    d.text((x, 66 * S), "ALPACA  ×  LABLAB.AI", font=f_small, fill=INK_FAINT)
    d.text((x, 106 * S), "We joined four days into", font=f_head, fill=INK)
    d.text((x, 172 * S), "a seven-day hackathon", font=f_head, fill=INK)
    d.text((x, 250 * S),
           "First commit and first live trade, same day: September 1.",
           font=f_sub, fill=INK_SOFT)

    # --- the timeline ---------------------------------------------------
    ty = 400 * S
    n = len(DAYS)
    track_x0, track_x1 = x, x + 1332 * S
    cell_w = (track_x1 - track_x0) / n
    cell_h = 120 * S

    for i, (label, sub, used, tag) in enumerate(DAYS):
        cx0 = track_x0 + i * cell_w
        cx1 = cx0 + cell_w - 8 * S
        colour = IDIO if used else GHOST
        outline = IDIO if used else RULE
        d.rounded_rectangle([cx0, ty, cx1, ty + cell_h], radius=9 * S,
                            fill=(colour if used else GHOST),
                            outline=outline, width=(0 if used else 2 * S))
        txt_colour = GROUND if used else INK_FAINT
        # day label, possibly two lines
        lines = label.split("\n")
        ly = ty + 16 * S
        for line in lines:
            bbox = d.textbbox((0, 0), line, font=f_day)
            lw = bbox[2] - bbox[0]
            d.text((cx0 + (cell_w - 8 * S - lw) / 2, ly), line,
                   font=f_day, fill=txt_colour)
            ly += 30 * S
        if sub:
            for j, word in enumerate(sub.split(" ", 1)):
                pass
            # wrap sub label to fit cell
            words = sub.split(" ")
            wrapped = []
            cur = ""
            for w_ in words:
                trial = (cur + " " + w_).strip()
                if d.textbbox((0, 0), trial, font=f_label)[2] > cell_w - 20 * S:
                    wrapped.append(cur)
                    cur = w_
                else:
                    cur = trial
            if cur:
                wrapped.append(cur)
            sy = ty + cell_h - 18 * S * len(wrapped) - 8 * S
            for line in wrapped:
                bbox = d.textbbox((0, 0), line, font=f_label)
                lw = bbox[2] - bbox[0]
                d.text((cx0 + (cell_w - 8 * S - lw) / 2, sy), line,
                       font=f_label, fill=txt_colour)
                sy += 20 * S

    d.text((x, ty + cell_h + 20 * S),
           "3 of 7 days were ours. The rest of the week already had a runner in it.",
           font=f_small, fill=INK_FAINT)

    # --- the kicker --------------------------------------------------------
    ky = 752 * S
    d.rounded_rectangle([x, ky, x + (W - 184) * S, ky + 62 * S],
                        radius=10 * S, outline=IDIO, width=2 * S)
    d.text((x + 24 * S, ky + 8 * S),
           "The bug I found on day 3 needed a day 4 to prove the fix. There wasn't one.",
           font=f_kick, fill=IDIO)

    d.line([(x, 848 * S), ((W - 92) * S, 848 * S)], fill=RULE, width=1 * S)
    d.text((x, 866 * S),
           "Kink  ·  an options agent that grades its own predictions on Alpaca paper trading",
           font=f_foot, fill=INK_FAINT)

    return img.resize((W, H), Image.LANCZOS)


if __name__ == "__main__":
    out = pathlib.Path(__file__).resolve().parents[1] / "docs" / "social-timeline.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    build().save(out, "PNG", optimize=True)
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
