"""Which companies report earnings, and when.

This is the gap that kept single names untradeable. Alpaca's corporate-actions
feed carries dividends, splits and mergers but not earnings dates -- and for a
strategy that sells premium on one expiration, an earnings report inside that
window is the single most dangerous thing that can happen. Being blind to it
was why the agent refused every individual stock.

The calendar is fetched per day and cached, because the answer for a given date
does not change. A window of 45 days is 45 requests once, then nothing.

Fail-closed, deliberately: if the calendar cannot be reached, `lookup` reports
that it does not know rather than that there is nothing. Not knowing and
knowing-there-is-nothing look identical in a boolean, and only one of them is
safe to trade on.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
from dataclasses import dataclass

import requests

from .journal import JOURNAL_DIR

CACHE = JOURNAL_DIR / "earnings_cache.json"
CALENDAR_URL = "https://api.nasdaq.com/api/calendar/earnings"

# Nasdaq's public calendar rejects requests without a browser user agent.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
}

MAX_DAYS = 60


@dataclass(frozen=True)
class EarningsInfo:
    symbol: str
    known: bool                      # did we successfully consult the calendar?
    dates: tuple[dt.date, ...] = ()  # reporting dates inside the window

    @property
    def has_event(self) -> bool:
        return bool(self.dates)

    @property
    def safe_to_trade(self) -> bool:
        """Only a confirmed empty calendar clears a name."""
        return self.known and not self.dates

    def describe(self) -> str:
        if not self.known:
            return "earnings calendar unavailable; treating as unknown"
        if not self.dates:
            return "no earnings scheduled in this window"
        return "earnings on " + ", ".join(d.isoformat() for d in self.dates)


def _load_cache() -> dict[str, list[str]]:
    if not CACHE.exists():
        return {}
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict[str, list[str]]) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=0, sort_keys=True), encoding="utf-8")


def fetch_day(day: dt.date, *, timeout: int = 15) -> list[str] | None:
    """Symbols reporting on one date. None means the calendar was unreachable."""
    try:
        resp = requests.get(
            CALENDAR_URL, params={"date": day.isoformat()},
            headers=HEADERS, timeout=timeout,
        )
    except requests.RequestException:
        return None
    if resp.status_code >= 400:
        return None
    try:
        rows = ((resp.json() or {}).get("data") or {}).get("rows") or []
    except ValueError:
        return None
    # A day with no reporters legitimately returns an empty list, which is a
    # real answer and must not be confused with a failure.
    return sorted({str(r.get("symbol", "")).upper() for r in rows if r.get("symbol")})


def load_window(start: dt.date, end: dt.date, *, refresh: bool = False) -> tuple[dict[str, list[str]], bool]:
    """Calendar for a date range, cached. Returns (by_date, complete)."""
    if (end - start).days > MAX_DAYS:
        end = start + dt.timedelta(days=MAX_DAYS)

    cache = _load_cache()
    complete = True
    dirty = False

    day = start
    while day <= end:
        key = day.isoformat()
        # Weekends have no reporters; skip the request rather than spend it.
        if day.weekday() >= 5:
            cache.setdefault(key, [])
        elif refresh or key not in cache:
            symbols = fetch_day(day)
            if symbols is None:
                complete = False
            else:
                cache[key] = symbols
                dirty = True
        day += dt.timedelta(days=1)

    if dirty:
        _save_cache(cache)
    return cache, complete


def lookup(symbol: str, *, start: dt.date, end: dt.date) -> EarningsInfo:
    """Does this symbol report between start and end?"""
    sym = symbol.upper()
    cache, complete = load_window(start, end)

    hits: list[dt.date] = []
    day = start
    while day <= end:
        key = day.isoformat()
        if key in cache and sym in cache[key]:
            hits.append(day)
        day += dt.timedelta(days=1)

    if hits:
        # A confirmed hit is decisive even if other days failed to load.
        return EarningsInfo(symbol=sym, known=True, dates=tuple(hits))
    return EarningsInfo(symbol=sym, known=complete, dates=())


def summary(start: dt.date, end: dt.date) -> str:
    cache, complete = load_window(start, end)
    days = [k for k in cache if start.isoformat() <= k <= end.isoformat()]
    total = sum(len(cache[k]) for k in days)
    return (
        f"earnings calendar: {len(days)} days cached, {total} reports, "
        f"{'complete' if complete else 'INCOMPLETE'}"
    )
