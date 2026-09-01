"""When to close a calendar.

The entry thesis was specific: this expiration is richer than its neighbours by
more than the rest of the universe is, and that gap should close. So the exit
rules follow from the thesis rather than from round numbers.

  * The kink collapsed -- the thesis played out. Take it off. This is the exit
    we want to hit, and it can fire well before any profit target does.
  * The kink widened badly against us -- the thesis was wrong, or something is
    happening that we did not price. Leave.
  * The short leg is close to expiry -- gamma on a short option near expiry is
    violent and asymmetric. Time out of the position regardless of P&L.
  * Profit target / stop -- backstops, not the primary logic.

Max loss on a long calendar is the debit paid, so the stop is about not riding a
loser to zero rather than about solvency.

Every rule here is a pure function of numbers passed in: no network, no clock,
no model. That keeps the exit policy auditable and unit-testable, which matters
more than usual because an exit bug loses money silently.
"""
from __future__ import annotations

from dataclasses import dataclass

# Thesis-based
KINK_COLLAPSED_FRACTION = 0.35   # close when <35% of the entry edge remains
KINK_WIDENED_MULTIPLE = 2.0      # close if the gap doubled against us

# Backstops
PROFIT_TARGET = 0.25             # +25% of debit paid
STOP_LOSS = -0.50                # -50% of debit paid
MIN_SHORT_DTE = 7                # never hold a short leg inside a week


@dataclass(frozen=True)
class ExitDecision:
    should_close: bool
    reason: str
    urgency: str = "normal"      # "normal" | "urgent"

    @property
    def is_urgent(self) -> bool:
        return self.urgency == "urgent"


def evaluate_exit(
    *,
    entry_debit: float,
    current_debit: float,
    entry_edge: float,
    current_edge: float,
    short_dte: int,
    deadline_reached: bool = False,
) -> ExitDecision:
    """Decide whether an open calendar should be closed.

    `entry_edge` / `current_edge` are the idiosyncratic kink scores at entry and
    now. `entry_debit` / `current_debit` are what the spread cost and what it is
    marked at.
    """
    if deadline_reached:
        return ExitDecision(True, "competition deadline: flattening", "urgent")

    if short_dte <= MIN_SHORT_DTE:
        return ExitDecision(
            True, f"short leg {short_dte}d from expiry (limit {MIN_SHORT_DTE}d)", "urgent"
        )

    if entry_debit <= 0:
        return ExitDecision(False, "no valid entry debit recorded")

    pnl_fraction = (current_debit - entry_debit) / entry_debit

    if pnl_fraction <= STOP_LOSS:
        return ExitDecision(
            True, f"stop hit: {pnl_fraction:+.0%} of debit", "urgent"
        )

    # The thesis exits. Checked after the stop so a collapsing kink that is also
    # deeply underwater still leaves urgently rather than "successfully".
    if entry_edge > 0:
        remaining = current_edge / entry_edge
        if remaining <= KINK_COLLAPSED_FRACTION:
            return ExitDecision(
                True,
                f"thesis played out: edge {entry_edge:.1%} -> {current_edge:.1%} "
                f"({remaining:.0%} remaining)",
            )
        if current_edge >= entry_edge * KINK_WIDENED_MULTIPLE:
            return ExitDecision(
                True,
                f"thesis broken: edge widened {entry_edge:.1%} -> {current_edge:.1%}",
                "urgent",
            )

    if pnl_fraction >= PROFIT_TARGET:
        return ExitDecision(True, f"profit target: {pnl_fraction:+.0%} of debit")

    return ExitDecision(
        False,
        f"holding: {pnl_fraction:+.0%} of debit, edge {current_edge:.1%} "
        f"of {entry_edge:.1%}, short leg {short_dte}d",
    )


def closing_legs(short_symbol: str, long_symbol: str) -> list[dict[str, str]]:
    """Reverse the opening structure: buy back the short, sell the long."""
    return [
        {
            "symbol": short_symbol,
            "side": "buy",
            "ratio_qty": "1",
            "position_intent": "buy_to_close",
        },
        {
            "symbol": long_symbol,
            "side": "sell",
            "ratio_qty": "1",
            "position_intent": "sell_to_close",
        },
    ]
