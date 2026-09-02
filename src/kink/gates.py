"""Deterministic risk gates.

Every gate is a pure function returning a reason string when it blocks. The
model has no way to reach these -- it proposes a candidate, and this module
decides whether an order is allowed to exist. Gates can only shrink or refuse
a trade; none of them can enlarge one.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .termstructure import Kink

MAX_SPREAD_PCT = 0.25       # refuse contracts whose quoted spread exceeds this
MIN_OPTION_MID = 0.20       # sub-20c options are noise on the indicative feed
MAX_SHORT_LEG_DTE = 45


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reasons: list[str]
    qty: int = 0
    max_loss_usd: float = 0.0


def _quote_gates(kink: Kink) -> list[str]:
    problems: list[str] = []
    for label, leg in (("short", kink.rich.call), ("long", kink.hedge.call)):
        mid = leg.mid
        if mid is None:
            problems.append(f"{label} leg {leg.symbol} has no usable two-sided quote")
            continue
        if mid < MIN_OPTION_MID:
            problems.append(f"{label} leg mid {mid:.2f} below {MIN_OPTION_MID:.2f} floor")
        sp = leg.spread_pct
        if sp is not None and sp > MAX_SPREAD_PCT:
            problems.append(f"{label} leg spread {sp:.1%} exceeds {MAX_SPREAD_PCT:.0%} cap")
    return problems


def evaluate(
    kink: Kink,
    cfg: Config,
    *,
    open_positions: int,
    committed_risk_usd: float,
) -> Decision:
    reasons: list[str] = []

    if open_positions >= cfg.max_concurrent_positions:
        reasons.append(
            f"already holding {open_positions} positions (cap {cfg.max_concurrent_positions})"
        )

    if kink.score < cfg.min_kink_score:
        reasons.append(f"kink score {kink.score:.1%} below {cfg.min_kink_score:.1%} threshold")

    if kink.vol_points < cfg.min_kink_vol_points:
        reasons.append(
            f"edge {kink.vol_points * 100:.2f} vol points below "
            f"{cfg.min_kink_vol_points * 100:.2f} floor"
        )

    # A name with a persistent structural bump scores against its neighbours
    # every day. Once there is enough history to say what is normal for it, the
    # reading must also be unusual -- not merely positive.
    if kink.z_score is not None and kink.z_score < cfg.min_kink_z:
        reasons.append(
            f"z {kink.z_score:+.1f} below {cfg.min_kink_z:+.1f}: normal for this name"
        )

    # Too good to be true, usually is. A reading this far from the name's own
    # history is evidence about the data feed, not about the market.
    if kink.z_score is not None and kink.z_score > cfg.max_kink_z:
        reasons.append(
            f"z {kink.z_score:+.1f} exceeds {cfg.max_kink_z:+.1f}: implausible, "
            "treating as a bad quote"
        )

    if cfg.require_cohort and not kink.cohort_estimated:
        reasons.append(
            "too few peers at this expiration to estimate the macro component"
        )

    if kink.rich.dte > MAX_SHORT_LEG_DTE:
        reasons.append(f"short leg {kink.rich.dte}d beyond {MAX_SHORT_LEG_DTE}d limit")

    if kink.hedge.dte <= kink.rich.dte:
        reasons.append("hedge leg must expire after the short leg")

    reasons.extend(_quote_gates(kink))

    # Size off the price we would actually pay, not the mid we would prefer.
    entry = kink.entry_debit(cfg.entry_slippage)
    if entry is None:
        reasons.append("long leg is not richer than short leg; not a debit calendar")
        return Decision(allowed=False, reasons=reasons or ["missing quotes"])

    # A long calendar is a net debit; the debit is the entire max loss.
    debit_per_contract = entry * 100.0
    if debit_per_contract <= 0:
        # Belt and braces: sizing divides by this, and a crash here takes the
        # whole cycle down rather than skipping one candidate.
        reasons.append("entry debit rounds to zero; not a tradeable price")
        return Decision(allowed=False, reasons=reasons)

    qty = int(cfg.max_risk_per_trade_usd // debit_per_contract)
    if qty < 1:
        reasons.append(
            f"one contract costs ${debit_per_contract:.0f}, over the "
            f"${cfg.max_risk_per_trade_usd:.0f} per-trade cap"
        )

    max_loss = qty * debit_per_contract

    # Bound the volatility-level exposure the structure carries by construction.
    # A kink can converge exactly as predicted and still lose money if the whole
    # surface falls; this caps how much of the trade that could cost.
    net_vega = kink.net_vega
    if net_vega is None:
        reasons.append("no vega available; cannot bound volatility exposure")
    elif qty > 0 and max_loss > 0:
        stress = abs(net_vega) * qty * cfg.vega_stress_points
        if stress > max_loss * cfg.max_vega_stress_fraction:
            reasons.append(
                f"a {cfg.vega_stress_points:.0f}-point vol move costs "
                f"${stress:,.0f}, over {cfg.max_vega_stress_fraction:.0%} of the "
                f"${max_loss:,.0f} at risk"
            )

    if committed_risk_usd + max_loss > cfg.max_total_risk_usd:
        reasons.append(
            f"${max_loss:.0f} would take book risk past the "
            f"${cfg.max_total_risk_usd:.0f} ceiling"
        )

    if reasons:
        return Decision(allowed=False, reasons=reasons, qty=0, max_loss_usd=0.0)

    return Decision(allowed=True, reasons=[], qty=qty, max_loss_usd=max_loss)
