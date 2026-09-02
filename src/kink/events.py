"""The other side of the earnings check.

The calendar was built to keep the agent out of expirations containing a
scheduled report. But the same fact supports a second, opposite question: when
an expiration *does* contain a known event, is the market charging for it?

An earnings report reliably moves a stock. The expiration covering it should
therefore be priced above the expirations either side. When it is priced at or
below them, the market has a dated, public, high-impact event in the window and
is asking nothing extra for it. That is a mispricing in the other direction --
and unlike a bump, it is anchored to a fact rather than to a statistical
residual.

The expression is a reverse calendar: buy the event expiration, sell a
neighbour. Which is why this ships detection-only by default. A reverse
calendar is short the far leg, and once the near leg expires that short is
uncovered -- the defined-risk property the rest of this system is built on does
not survive it. Trading this needs a structure with its own risk model
(a long straddle in the event window would do it), and that is not something to
add to a live account two days from a deadline.

So: detect, publish, and let the evidence accumulate.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .earnings import lookup as earnings_lookup
from .termstructure import Kink, TermPoint

# How far below the neighbour-implied level counts as "not charging for it".
# Zero would mean any event expiration priced merely in line; a small negative
# threshold asks for the market to be visibly complacent.
UNDERPRICED_THRESHOLD = -0.02


@dataclass(frozen=True)
class UnderpricedEvent:
    underlying: str
    expiration: dt.date
    dte: int
    atm_iv: float
    expected_iv: float
    event_dates: tuple[dt.date, ...]

    @property
    def shortfall(self) -> float:
        """How far below its neighbours the event expiration is priced."""
        if self.expected_iv <= 0:
            return 0.0
        return (self.atm_iv - self.expected_iv) / self.expected_iv

    def describe(self) -> str:
        when = ", ".join(d.isoformat() for d in self.event_dates)
        return (
            f"{self.underlying}: earnings {when} inside the {self.dte}d "
            f"expiration, priced {self.shortfall:+.1%} against its neighbours "
            f"({self.atm_iv:.1%} vs {self.expected_iv:.1%})"
        )


def _expected_from_neighbours(
    points: list[TermPoint], index: int
) -> float | None:
    """The level the surrounding expirations imply, in sqrt-time."""
    import math

    if index <= 0 or index >= len(points) - 1:
        return None
    left, mid, right = points[index - 1], points[index], points[index + 1]
    x0, x1, x2 = math.sqrt(left.dte), math.sqrt(mid.dte), math.sqrt(right.dte)
    if x2 == x0:
        return None
    w = (x1 - x0) / (x2 - x0)
    return left.atm_iv * (1 - w) + right.atm_iv * w


def find_underpriced(
    underlying: str,
    points: list[TermPoint],
    *,
    today: dt.date | None = None,
    threshold: float = UNDERPRICED_THRESHOLD,
) -> list[UnderpricedEvent]:
    """Expirations that contain a known earnings date and are not priced for it."""
    today = today or dt.date.today()
    out: list[UnderpricedEvent] = []

    for i, point in enumerate(points):
        expected = _expected_from_neighbours(points, i)
        if expected is None or expected <= 0:
            continue
        shortfall = (point.atm_iv - expected) / expected
        if shortfall > threshold:
            continue      # priced in line or above; nothing to say

        info = earnings_lookup(underlying, start=today, end=point.expiration)
        if not info.known or not info.has_event:
            continue      # no confirmed event, so cheapness is just cheapness

        out.append(
            UnderpricedEvent(
                underlying=underlying,
                expiration=point.expiration,
                dte=point.dte,
                atm_iv=point.atm_iv,
                expected_iv=expected,
                event_dates=info.dates,
            )
        )
    return sorted(out, key=lambda e: e.shortfall)


def scan_all(
    curves: dict[str, list[TermPoint]], *, today: dt.date | None = None
) -> list[UnderpricedEvent]:
    found: list[UnderpricedEvent] = []
    for underlying, points in curves.items():
        if len(points) < 3:
            continue
        found.extend(find_underpriced(underlying, points, today=today))
    return sorted(found, key=lambda e: e.shortfall)
