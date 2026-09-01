"""What the agent believed when it opened a position.

Exits compare today's edge against the edge at entry, so the entry thesis has to
survive a process restart. The journal is append-only and meant for auditing;
this is the small mutable file the agent actually reads back.

Keyed by the pair of OCC symbols, which uniquely identifies a calendar.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import asdict, dataclass

STATE_PATH = pathlib.Path(__file__).resolve().parents[2] / "journal" / "open_trades.json"


@dataclass
class OpenTrade:
    underlying: str
    short_symbol: str
    long_symbol: str
    qty: int
    entry_debit: float
    entry_edge: float
    entry_raw_edge: float
    entry_cohort: float
    opened_at: str
    client_order_id: str
    adjudicator_reason: str = ""

    @property
    def key(self) -> str:
        return f"{self.short_symbol}|{self.long_symbol}"


def _load_raw() -> dict[str, dict]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def load() -> dict[str, OpenTrade]:
    return {k: OpenTrade(**v) for k, v in _load_raw().items()}


def _write(trades: dict[str, OpenTrade]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps({k: asdict(v) for k, v in trades.items()}, indent=2),
        encoding="utf-8",
    )


def record_open(trade: OpenTrade) -> None:
    trades = load()
    trades[trade.key] = trade
    _write(trades)


def reconcile(held_symbols: set[str]) -> list[str]:
    """Drop tracked trades whose position never actually opened.

    An entry is recorded when the order is submitted, not when it fills -- and
    a limit order that the market walked away from never fills. Without this,
    the agent believes it holds a calendar it does not hold, and tries to exit
    a position that was never opened.
    """
    trades = load()
    dropped = [
        key for key, tr in trades.items()
        if tr.short_symbol not in held_symbols and tr.long_symbol not in held_symbols
    ]
    for key in dropped:
        trades.pop(key, None)
    if dropped:
        _write(trades)
    return dropped


def record_closed(key: str) -> OpenTrade | None:
    trades = load()
    trade = trades.pop(key, None)
    _write(trades)
    return trade
