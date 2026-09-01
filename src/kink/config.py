"""Configuration loaded from the environment.

Everything the agent is allowed to risk is defined here and nowhere else.
The model never sees these values as adjustable -- they are read at startup
and enforced in gates.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


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
        default_factory=lambda: float(os.getenv("MIN_KINK_SCORE", "0.15"))
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
