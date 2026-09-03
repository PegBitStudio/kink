"""Standing instructions to close specific positions at the next opportunity.

Options market orders are rejected outside market hours, so a decision made in
the evening cannot be carried out until the bell. Without somewhere to put that
decision it has to be remembered by a human and executed by hand at the open --
which is exactly the kind of thing that gets forgotten on the morning it
matters.

A request lives in a file, survives a restart, and is honoured by the first
cycle that finds the market open. It is also a manual override: anything in
here is closed regardless of what the exit rules think, because the reason for
closing may be one the rules do not model -- a position built by a bug, for
instance, whose structure the exits were never written for.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib

from .journal import JOURNAL_DIR, record

REQUESTS = JOURNAL_DIR / "flatten_requests.json"


def _load() -> dict[str, str]:
    if not REQUESTS.exists():
        return {}
    try:
        return json.loads(REQUESTS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, str]) -> None:
    REQUESTS.parent.mkdir(parents=True, exist_ok=True)
    REQUESTS.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")


def request(symbols: list[str], reason: str = "") -> list[str]:
    """Queue one or more option symbols to be closed at the next open."""
    data = _load()
    added = []
    for s in symbols:
        sym = s.strip().upper()
        if not sym:
            continue
        data[sym] = reason or "manual request"
        added.append(sym)
    if added:
        _save(data)
        record("flatten_request", {"symbols": added, "reason": reason})
    return added


def pending() -> dict[str, str]:
    return _load()


def clear(symbol: str) -> None:
    data = _load()
    if data.pop(symbol.upper(), None) is not None:
        _save(data)


def execute_pending(cfg, held: dict[str, int], *, live: bool) -> list[str]:
    """Close any held position that has a standing request against it.

    `held` maps option symbol to signed quantity. Returns what was actioned.
    """
    from . import execute

    data = _load()
    if not data:
        return []

    done: list[str] = []
    for symbol, reason in list(data.items()):
        if symbol not in held:
            # Already flat -- the request is satisfied whether we did it or not.
            clear(symbol)
            continue
        if not live:
            done.append(f"{symbol} (dry run)")
            continue
        try:
            execute.run_cli(
                cfg,
                ["position", "close", "--symbol-or-asset-id", symbol, "--quiet"],
                journal_as="command",
            )
            record(
                "flatten_executed",
                {"symbol": symbol, "reason": reason,
                 "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")},
            )
            clear(symbol)
            done.append(symbol)
        except RuntimeError as exc:
            # Leave the request in place; the next cycle tries again.
            record("error", {"stage": "flatten", "symbol": symbol,
                             "error": str(exc)[:200]})
    return done
