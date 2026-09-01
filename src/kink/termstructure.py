"""Build an at-the-money implied-volatility term structure and find kinks.

The thesis: across expirations, ATM implied vol should form a smooth curve in
sqrt(time). Where a single expiration sits materially above the curve implied
by its neighbours, the market is paying up for that specific window -- usually
for a dated catalyst, sometimes for nothing. Selling the rich expiration and
buying an adjacent one is a defined-risk calendar that expresses the *shape*
of the surface rather than a direction.

This module is pure and deterministic. No network, no model, no side effects --
so it can be unit-tested against fixtures and reproduced exactly from the
journal.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass

OCC_RE = re.compile(r"^(?P<root>[A-Z]+)(?P<ymd>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")


@dataclass(frozen=True)
class Contract:
    symbol: str
    root: str
    expiration: dt.date
    right: str          # "C" or "P"
    strike: float
    iv: float | None
    delta: float | None
    bid: float | None
    ask: float | None

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            return None
        return (self.bid + self.ask) / 2.0

    @property
    def spread_pct(self) -> float | None:
        m = self.mid
        if m is None or m == 0 or self.ask is None or self.bid is None:
            return None
        return (self.ask - self.bid) / m


@dataclass(frozen=True)
class TermPoint:
    expiration: dt.date
    dte: int
    atm_iv: float
    call: Contract
    put: Contract


@dataclass(frozen=True)
class Kink:
    underlying: str
    rich: TermPoint         # expiration to sell
    hedge: TermPoint        # adjacent expiration to buy
    raw_score: float        # richness vs this name's own neighbour-implied curve
    expected_iv: float
    cohort_score: float = 0.0   # median raw_score at this tenor across the cohort
    cohort_estimated: bool = False  # whether enough peers existed to estimate it

    @property
    def vol_points(self) -> float:
        """Idiosyncratic richness in implied-vol points, not percent.

        The relative score flatters low-vol instruments: HYG at 5.3% against a
        4.3% curve scores +23%, while IWM at 17.6% against 15.9% scores +11%,
        yet both are about one vol point. Premium, and therefore edge in
        dollars, tracks the absolute gap -- so it is gated separately.
        """
        return self.rich.atm_iv - self.expected_iv * (1 + self.cohort_score)

    @property
    def score(self) -> float:
        """Idiosyncratic richness: what is left after the macro calendar is removed.

        If every name in the universe shows the same bump at the same tenor, the
        market is pricing a scheduled macro event (payrolls, CPI, FOMC) and there
        is no mispricing to harvest -- only event risk to be short of. Subtracting
        the cohort median leaves the part that is specific to this underlying.
        """
        return self.raw_score - self.cohort_score

    def describe(self) -> str:
        return (
            f"{self.underlying}: {self.rich.dte}d IV {self.rich.atm_iv:.1%} vs curve "
            f"{self.expected_iv:.1%} (raw +{self.raw_score:.1%}, cohort "
            f"+{self.cohort_score:.1%}, idio +{self.score:.1%}, "
            f"{self.vol_points * 100:+.2f}pts{'' if self.cohort_estimated else ' NOCOHORT'}) -- sell "
            f"{self.rich.dte}d / buy {self.hedge.dte}d"
        )


def parse_occ(symbol: str) -> tuple[str, dt.date, str, float] | None:
    m = OCC_RE.match(symbol)
    if not m:
        return None
    ymd = m.group("ymd")
    try:
        exp = dt.date(2000 + int(ymd[0:2]), int(ymd[2:4]), int(ymd[4:6]))
    except ValueError:
        return None
    return m.group("root"), exp, m.group("cp"), int(m.group("strike")) / 1000.0


def to_contracts(snapshots: dict[str, dict]) -> list[Contract]:
    """Flatten Alpaca option-chain snapshots into Contract records."""
    out: list[Contract] = []
    for sym, snap in snapshots.items():
        parsed = parse_occ(sym)
        if not parsed:
            continue
        root, exp, right, strike = parsed
        greeks = snap.get("greeks") or {}
        quote = snap.get("latestQuote") or {}
        out.append(
            Contract(
                symbol=sym,
                root=root,
                expiration=exp,
                right=right,
                strike=strike,
                iv=snap.get("impliedVolatility"),
                delta=greeks.get("delta"),
                bid=quote.get("bp"),
                ask=quote.get("ap"),
            )
        )
    return out


def build_term_structure(
    contracts: list[Contract],
    spot: float,
    today: dt.date,
    *,
    min_dte: int = 7,
    max_dte: int = 120,
) -> list[TermPoint]:
    """One ATM point per expiration, using the strike nearest spot.

    ATM IV is the average of the call and put IV at that strike, which cancels
    most of the skew contamination you would get from either leg alone.
    """
    by_exp: dict[dt.date, list[Contract]] = {}
    for c in contracts:
        dte = (c.expiration - today).days
        if dte < min_dte or dte > max_dte:
            continue
        if c.iv is None or c.iv <= 0:
            continue
        by_exp.setdefault(c.expiration, []).append(c)

    points: list[TermPoint] = []
    for exp, group in by_exp.items():
        strikes = sorted({c.strike for c in group})
        if not strikes:
            continue
        atm_strike = min(strikes, key=lambda s: abs(s - spot))
        call = next((c for c in group if c.strike == atm_strike and c.right == "C"), None)
        put = next((c for c in group if c.strike == atm_strike and c.right == "P"), None)
        if call is None or put is None:
            continue
        atm_iv = (call.iv + put.iv) / 2.0  # type: ignore[operator]
        points.append(
            TermPoint(
                expiration=exp,
                dte=(exp - today).days,
                atm_iv=atm_iv,
                call=call,
                put=put,
            )
        )
    return sorted(points, key=lambda p: p.dte)


def find_kinks(underlying: str, points: list[TermPoint], *, min_score: float = -1e9) -> list[Kink]:
    """Score each interior expiration against a sqrt-time interpolation of its neighbours.

    Interpolating in sqrt(dte) rather than dte matters: variance accumulates
    linearly in time, so vol is naturally near-linear in sqrt(time). Using raw
    days would report a kink on every normal curve.
    """
    kinks: list[Kink] = []
    for i in range(1, len(points) - 1):
        left, mid, right = points[i - 1], points[i], points[i + 1]
        x0, x1, x2 = math.sqrt(left.dte), math.sqrt(mid.dte), math.sqrt(right.dte)
        if x2 == x0:
            continue
        w = (x1 - x0) / (x2 - x0)
        expected = left.atm_iv * (1 - w) + right.atm_iv * w
        if expected <= 0:
            continue
        score = (mid.atm_iv - expected) / expected
        if score < min_score:
            continue
        # Sell the rich expiration, buy the longer-dated neighbour: positive
        # theta on the short leg, and the long leg caps the vega risk.
        kinks.append(
            Kink(underlying=underlying, rich=mid, hedge=right, raw_score=score, expected_iv=expected)
        )
    return sorted(kinks, key=lambda k: k.score, reverse=True)


# Listed options share an expiration calendar across underlyings: SPY, QQQ and
# AAPL all have the same third-Friday and weekly dates. So the cohort is grouped
# by the *exact* expiration, not a bucket -- bucketing would average an
# expiration that sits above its curve together with one that sits below it,
# and the median of that mixture means nothing.


def cohort_key(kink: "Kink") -> tuple[str, dt.date]:
    """Compare a name only against others that share its macro calendar.

    Grouping on expiration alone would pool equities with bonds and gold, whose
    term structures move for different reasons. That drags the median down and
    makes ordinary equity richness look idiosyncratic.
    """
    from .universe import asset_class_of

    return (asset_class_of(kink.underlying), kink.rich.expiration)


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    n = len(xs)
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2.0


def apply_cross_section(kinks: list[Kink], *, min_cohort: int = 3) -> list[Kink]:
    """Strip the market-wide component out of every kink.

    For each tenor bucket, the median raw score across the universe is the part
    of the bump that every name shares -- the scheduled macro event. What remains
    is idiosyncratic to the underlying, and that is the only part worth trading.

    Buckets with fewer than `min_cohort` names are left alone: with two
    observations the median is not a reliable estimate of the common component,
    and subtracting a noisy estimate is worse than subtracting nothing.
    """
    by_group: dict[tuple[str, dt.date], list[float]] = {}
    for k in kinks:
        by_group.setdefault(cohort_key(k), []).append(k.raw_score)

    cohort = {
        group: _median(scores)
        for group, scores in by_group.items()
        if len(scores) >= min_cohort
    }

    out: list[Kink] = []
    for k in kinks:
        common = cohort.get(cohort_key(k), 0.0)
        # Only ever remove richness, never add it. A negative cohort means the
        # universe was cheap at that expiration; that is not evidence this name
        # is rich, so it must not manufacture a signal.
        common = max(common, 0.0)
        out.append(
            Kink(
                underlying=k.underlying,
                rich=k.rich,
                hedge=k.hedge,
                raw_score=k.raw_score,
                expected_iv=k.expected_iv,
                cohort_score=common,
                cohort_estimated=cohort_key(k) in cohort,
            )
        )
    return sorted(out, key=lambda k: k.score, reverse=True)
