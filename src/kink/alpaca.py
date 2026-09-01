"""Thin REST client for the Alpaca trading and options-data APIs.

Deliberately dependency-light: `requests` only. Order *submission* does not
live here -- it goes through the Alpaca CLI in execute.py so that every
state-changing action leaves a reproducible command in the journal.
"""
from __future__ import annotations

import time
from typing import Any

import requests

from .config import Config


class AlpacaError(RuntimeError):
    pass


class Alpaca:
    def __init__(self, cfg: Config) -> None:
        cfg.assert_paper()
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update(cfg.headers())

    def _get(self, url: str, params: dict[str, Any] | None = None, tries: int = 3) -> dict:
        last: Exception | None = None
        for attempt in range(tries):
            try:
                resp = self.session.get(url, params=params, timeout=20)
                if resp.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    raise AlpacaError(f"{resp.status_code} {url}: {resp.text[:300]}")
                return resp.json()
            except requests.RequestException as exc:
                last = exc
                time.sleep(1.0 * (attempt + 1))
        raise AlpacaError(f"GET failed after {tries} tries: {url} ({last})")

    # --- account -------------------------------------------------------
    def account(self) -> dict:
        return self._get(f"{self.cfg.base_url}/v2/account")

    def positions(self) -> list[dict]:
        data = self._get(f"{self.cfg.base_url}/v2/positions")
        return data if isinstance(data, list) else []

    def clock(self) -> dict:
        return self._get(f"{self.cfg.base_url}/v2/clock")

    # --- options data --------------------------------------------------
    def option_chain(
        self,
        underlying: str,
        *,
        limit: int = 1000,
        expiration_gte: str | None = None,
        expiration_lte: str | None = None,
    ) -> dict[str, dict]:
        """Snapshots keyed by OCC contract symbol, including greeks and IV."""
        params: dict[str, Any] = {"feed": self.cfg.option_feed, "limit": limit}
        if expiration_gte:
            params["expiration_date_gte"] = expiration_gte
        if expiration_lte:
            params["expiration_date_lte"] = expiration_lte

        out: dict[str, dict] = {}
        url = f"{self.cfg.data_url}/v1beta1/options/snapshots/{underlying}"
        page_token: str | None = None
        while True:
            if page_token:
                params["page_token"] = page_token
            data = self._get(url, params)
            out.update(data.get("snapshots") or {})
            page_token = data.get("next_page_token")
            if not page_token:
                break
        return out

    def stock_quote(self, symbol: str) -> dict:
        data = self._get(f"{self.cfg.data_url}/v2/stocks/{symbol}/quotes/latest")
        return data.get("quote") or {}
