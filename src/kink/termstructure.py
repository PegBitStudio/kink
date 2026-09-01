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
    score: float            # relative richness vs the neighbour-implied curve
    expected_iv: float

    def describe(self) -> str:
        return (
            f"{self.underlying}: {self.rich.dte}d IV {self.rich.atm_iv:.1%} vs curve "
            f"{self.expected_iv:.1%} (+{self.score:.1%}) -- sell {self.rich.dte}d / "
            f"buy {self.hedge.dte}d"
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


def find_kinks(underlying: str, points: list[TermPoint], *, min_score: float) -> list[Kink]:
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
            Kink(underlying=underlying, rich=mid, hedge=right, score=score, expected_iv=expected)
        )
    return sorted(kinks, key=lambda k: k.score, reverse=True)
