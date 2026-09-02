"""Gather the facts the adjudicator needs, so it never has to rely on memory.

The first version of the adjudicator asked a model whether it knew of a dated
event in a future window. It correctly answered ABSTAIN almost every time: no
model's training data covers next month. Fail-closed then meant never trading,
which is safe and useless.

The fix is not a better prompt. It is to stop asking the model to remember, and
start handing it evidence retrieved at decision time -- corporate actions with
real dates, and recent headlines for the name. The model's job narrows to
reading what it is given and saying whether anything in it is a dated,
company-specific event inside the window. That is a job a model can actually do.

Both endpoints are free on the paper plan.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import requests

from .config import Config
from .earnings import EarningsInfo, lookup as earnings_lookup
from .universe import classify, is_tradeable_without_earnings_feed


NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
CORPORATE_ACTIONS_URL = "https://data.alpaca.markets/v1/corporate-actions"


@dataclass(frozen=True)
class Evidence:
    symbol: str
    is_broad_etf: bool
    earnings: EarningsInfo | None = None
    corporate_actions: list[str] = field(default_factory=list)
    headlines: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Whether retrieval succeeded. Partial evidence must not become a TRADE."""
        return not self.errors

    @property
    def earnings_clear(self) -> bool:
        """True only when the calendar was consulted and came back empty."""
        if self.is_broad_etf:
            return True          # a diversified fund has no earnings of its own
        return self.earnings is not None and self.earnings.safe_to_trade

    def render(self) -> str:
        lines = [f"Ticker: {self.symbol}"]
        inst = classify(self.symbol)
        lines.append(f"Instrument type: {inst.asset_class}/{inst.kind}")
        if self.is_broad_etf:
            lines.append(
                "This is a diversified fund with no company-specific calendar: "
                "no earnings, FDA decisions, or merger votes."
            )
        if self.earnings is not None:
            lines.append(f"Earnings calendar: {self.earnings.describe()}")
        lines.append("")
        lines.append("Dated corporate actions on file for this window:")
        lines.extend(f"  - {c}" for c in self.corporate_actions or ["  (none found)"])
        lines.append("")
        lines.append("Recent headlines:")
        lines.extend(f"  - {h}" for h in self.headlines or ["  (none found)"])
        return "\n".join(lines)


def _headers(cfg: Config) -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": cfg.key_id,
        "APCA-API-SECRET-KEY": cfg.secret_key,
        "accept": "application/json",
    }


def fetch_corporate_actions(
    cfg: Config, symbol: str, *, start: dt.date, end: dt.date
) -> tuple[list[str], str | None]:
    try:
        resp = requests.get(
            CORPORATE_ACTIONS_URL,
            params={
                "symbols": symbol,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
            headers=_headers(cfg),
            timeout=20,
        )
    except requests.RequestException as exc:
        return [], f"corporate actions unreachable ({type(exc).__name__})"
    if resp.status_code >= 400:
        return [], f"corporate actions HTTP {resp.status_code}"

    out: list[str] = []
    actions = (resp.json() or {}).get("corporate_actions") or {}
    for kind, items in actions.items():
        for item in items or []:
            date = (
                item.get("ex_date")
                or item.get("effective_date")
                or item.get("process_date")
                or "date unknown"
            )
            detail = kind.replace("_", " ")
            if item.get("rate") is not None:
                detail += f" rate {item['rate']}"
            out.append(f"{date}: {detail}")
    return sorted(out), None


def fetch_headlines(
    cfg: Config, symbol: str, *, lookback_days: int = 10, limit: int = 12
) -> tuple[list[str], str | None]:
    start = (dt.date.today() - dt.timedelta(days=lookback_days)).isoformat()
    try:
        resp = requests.get(
            NEWS_URL,
            params={"symbols": symbol, "start": start, "limit": limit},
            headers=_headers(cfg),
            timeout=20,
        )
    except requests.RequestException as exc:
        return [], f"news unreachable ({type(exc).__name__})"
    if resp.status_code >= 400:
        return [], f"news HTTP {resp.status_code}"

    out = []
    for item in (resp.json() or {}).get("news") or []:
        when = (item.get("created_at") or "")[:10]
        headline = (item.get("headline") or "").strip()
        if headline:
            out.append(f"{when}: {headline}")
    return out, None


def gather(cfg: Config, symbol: str, *, through: dt.date) -> Evidence:
    today = dt.date.today()
    errors: list[str] = []

    actions, err = fetch_corporate_actions(cfg, symbol, start=today, end=through)
    if err:
        errors.append(err)
    headlines, err = fetch_headlines(cfg, symbol)
    if err:
        errors.append(err)

    earnings = None
    if not is_tradeable_without_earnings_feed(symbol):
        # Only single names and concentrated funds need the calendar; a broad
        # index fund has no earnings date of its own.
        earnings = earnings_lookup(symbol, start=today, end=through)
        if not earnings.known:
            errors.append("earnings calendar unavailable")

    return Evidence(
        symbol=symbol,
        earnings=earnings,
        is_broad_etf=is_tradeable_without_earnings_feed(symbol),
        corporate_actions=actions,
        headlines=headlines,
        errors=errors,
    )
