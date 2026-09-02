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

MONTHLY = "monthly"
WEEKLY = "weekly"


def expiration_type(exp: dt.date) -> str:
    """Standard monthlies expire on the third Friday; everything else is weekly.

    This distinction is not cosmetic. Measured across 1,586 live observations,
    monthly expirations carried a mean raw kink of +0.99% against -0.03% for
    weeklies -- roughly a full vol point of structural richness that comes from
    open interest, pinning and dealer positioning, not from mispricing.

    Scoring a monthly against the weeklies on either side of it therefore
    manufactures an edge on every monthly in the chain. Like must be compared
    with like.
    """
    return MONTHLY if exp.weekday() == 4 and 15 <= exp.day <= 21 else WEEKLY


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
    vega: float | None = None      # price change per 1 vol point, per share
    theta: float | None = None     # price change per day, per share

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
    exp_type: str = WEEKLY          # monthly expirations are structurally richer
    z_score: float | None = None    # richness vs this name's own history

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

    @property
    def net_vega(self) -> float | None:
        """Dollars gained per one-point rise in implied vol, per contract pair.

        A calendar is long the far leg and short the near one, and vega grows
        with the square root of time -- so the structure is always net LONG
        vega. It is not a pure shape trade: if the whole surface falls, the
        position loses even when the kink converges exactly as predicted.

        That cannot be neutralised by selling more of the near leg. Doing so
        would leave uncovered shorts and destroy the defined-risk property,
        which is worth more than vega neutrality. So the exposure is measured
        and bounded instead of engineered away.
        """
        near, far = self.rich.call.vega, self.hedge.call.vega
        if near is None or far is None:
            return None
        return (far - near) * 100.0

    @property
    def net_theta(self) -> float | None:
        """Dollars earned per day held. The short near leg decays fastest."""
        near, far = self.rich.call.theta, self.hedge.call.theta
        if near is None or far is None:
            return None
        return (far - near) * 100.0

    def entry_debit(self, slippage: float = 0.15) -> float | None:
        """What one contract of this calendar would actually cost to open.

        Sizing and pricing have to use the same number. Sizing off the mid while
        paying up to a wider limit silently overshoots the per-trade risk cap by
        exactly the slippage allowance -- 45 lots at a 0.33 mid is $1,485, but
        the same 45 lots filled at a 0.38 limit is $1,710 against a $1,500 cap.

        Bounded by the offer-implied debit: buying the long leg at its ask and
        selling the short at its bid. Paying beyond that buys nothing.
        """
        short, long_ = self.rich.call, self.hedge.call
        if short.mid is None or long_.mid is None:
            return None
        mid_debit = long_.mid - short.mid
        if mid_debit <= 0:
            return None
        limit = mid_debit * (1 + slippage)
        if long_.ask is not None and short.bid is not None:
            crossing = long_.ask - short.bid
            if crossing > 0:
                limit = min(limit, crossing)
        return round(limit, 2)

    def describe(self) -> str:
        return (
            f"{self.underlying}: {self.rich.dte}d IV {self.rich.atm_iv:.1%} vs curve "
            f"{self.expected_iv:.1%} (raw +{self.raw_score:.1%}, cohort "
            f"+{self.cohort_score:.1%}, idio +{self.score:.1%}, "
            f"{self.vol_points * 100:+.2f}pts"
            f"{'' if self.z_score is None else f', z {self.z_score:+.1f}'}"
            f"{'' if self.cohort_estimated else ' NOCOHORT'}) -- sell "
            f"{self.rich.dte}d / buy {self.hedge.dte}d"
            f"{'' if self.net_vega is None else f' [vega ${self.net_vega:+.0f}/pt]'}"
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
                vega=greeks.get("vega"),
                theta=greeks.get("theta"),
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
    for i, mid in enumerate(points):
        # Compare each expiration against its nearest neighbours OF THE SAME
        # TYPE. A monthly bracketed by weeklies always looks rich; that is a
        # property of the calendar, not of the price.
        kind = expiration_type(mid.expiration)
        left = next(
            (p for p in reversed(points[:i]) if expiration_type(p.expiration) == kind),
            None,
        )
        right = next(
            (p for p in points[i + 1:] if expiration_type(p.expiration) == kind),
            None,
        )
        if left is None or right is None:
            continue

        # Measuring and hedging are different jobs. The curve must be measured
        # against like expirations, but the leg we actually buy should be the
        # nearest longer one of ANY type: vega grows with the square root of
        # time, so a distant hedge multiplies the exposure to the overall level
        # of volatility. For a monthly, hedging with the next monthly instead of
        # the next weekly triples net vega -- 0.40 against 0.12 on SPY.
        hedge = points[i + 1] if i + 1 < len(points) else right
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
            Kink(
                underlying=underlying,
                rich=mid,
                hedge=hedge,
                raw_score=score,
                expected_iv=expected,
                exp_type=kind,
            )
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
                exp_type=k.exp_type,
                z_score=k.z_score,
            )
        )
    return sorted(out, key=lambda k: k.score, reverse=True)
