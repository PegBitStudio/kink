"""Configuration loaded from the environment.

Everything the agent is allowed to risk is defined here and nowhere else.
The model never sees these values as adjustable -- they are read at startup
and enforced in gates.py.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _parse_deadline(raw: str) -> dt.datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _req(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(f"{name} is not set; copy .env.example to .env and fill it in")
    return val


@dataclass(frozen=True)
class Config:
    key_id: str = field(default_factory=lambda: _req("ALPACA_API_KEY_ID"))
    secret_key: str = field(default_factory=lambda: _req("ALPACA_API_SECRET_KEY"))
    base_url: str = field(
        default_factory=lambda: os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    )
    data_url: str = field(
        default_factory=lambda: os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets")
    )
    option_feed: str = field(default_factory=lambda: os.getenv("OPTION_FEED", "indicative"))

    universe: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            s.strip().upper() for s in os.getenv("UNIVERSE", "SPY").split(",") if s.strip()
        )
    )

    max_concurrent_positions: int = field(
        default_factory=lambda: int(os.getenv("MAX_CONCURRENT_POSITIONS", "5"))
    )
    max_risk_per_trade_usd: float = field(
        default_factory=lambda: float(os.getenv("MAX_RISK_PER_TRADE_USD", "1500"))
    )
    max_total_risk_usd: float = field(
        default_factory=lambda: float(os.getenv("MAX_TOTAL_RISK_USD", "6000"))
    )
    min_kink_score: float = field(
        default_factory=lambda: float(os.getenv("MIN_KINK_SCORE", "0.03"))
    )

    # How far above the mid we will bid to open. Anchoring at the mid is the
    # honest price but it does not fill: on the first live session three orders
    # were placed and none filled, because the market moved before the limit was
    # hit. This is the allowance, and it is bounded -- never through the offer.
    entry_slippage: float = field(
        default_factory=lambda: float(os.getenv("ENTRY_SLIPPAGE", "0.15"))
    )

    # A kink must be material in absolute vol points, not just in percent.
    # Without this, a 1-point wobble on a 5%-IV bond ETF outscores the same
    # wobble on a 17%-IV equity index by 4x.
    min_kink_vol_points: float = field(
        default_factory=lambda: float(os.getenv("MIN_KINK_VOL_POINTS", "0.008"))
    )
    # A calendar is structurally long vega. It cannot be hedged away without
    # uncovered shorts, so it is bounded instead: the loss from an adverse move
    # of VEGA_STRESS_POINTS must stay within this share of the trade's max loss.
    vega_stress_points: float = field(
        default_factory=lambda: float(os.getenv("VEGA_STRESS_POINTS", "2.0"))
    )
    max_vega_stress_fraction: float = field(
        default_factory=lambda: float(os.getenv("MAX_VEGA_STRESS_FRACTION", "0.35"))
    )

    # How unusual a kink must be against the name's own history, once there is
    # enough history to judge. Without this, a name whose monthly always runs
    # rich is selected every single day.
    min_kink_z: float = field(
        default_factory=lambda: float(os.getenv("MIN_KINK_Z", "1.0"))
    )

    # Beyond this, a reading is more likely a broken quote than an opportunity.
    # A live scan produced UNG at 97% implied vol against a 40% curve -- z +16.
    # Nothing in options moves sixteen standard deviations; the feed was wrong.
    max_kink_z: float = field(
        default_factory=lambda: float(os.getenv("MAX_KINK_Z", "8.0"))
    )

    # Refuse when too few peers existed to estimate the shared macro component.
    # An unestimated cohort is not evidence of no macro event -- it is evidence
    # that we could not look.
    require_cohort: bool = field(
        default_factory=lambda: os.getenv("REQUIRE_COHORT", "true").lower()
        in ("1", "true", "yes")
    )

    # Alpaca's corporate-actions feed carries dividends, splits and mergers --
    # but NOT earnings dates. For a single name that is the one event that most
    # matters here, so the dossier is blind exactly where it can hurt most.
    # Broad-market ETFs have no earnings by construction and stay tradeable.
    trade_single_names: bool = field(
        default_factory=lambda: os.getenv("TRADE_SINGLE_NAMES", "false").lower()
        in ("1", "true", "yes")
    )

    # Competition deadline. The runner flattens before it and then stops, so a
    # forgotten process cannot carry positions past the point of no return.
    deadline_utc: dt.datetime | None = field(
        default_factory=lambda: _parse_deadline(os.getenv("DEADLINE_UTC", ""))
    )
    flatten_before_minutes: int = field(
        default_factory=lambda: int(os.getenv("FLATTEN_BEFORE_MINUTES", "45"))
    )

    def headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
            "accept": "application/json",
        }

    def assert_paper(self) -> None:
        """Refuse to run against a live endpoint. Non-negotiable."""
        if "paper-api" not in self.base_url:
            raise RuntimeError(f"refusing to run against non-paper endpoint: {self.base_url}")
