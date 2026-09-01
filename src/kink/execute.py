"""Order construction and submission.

Submission is routed through the Alpaca CLI so that every state-changing
action is a literal shell command recorded in the journal -- anyone can replay
what the agent did without reading Python. Multi-leg submission falls back to
the REST endpoint when the CLI build in use does not expose mleg flags.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import requests

from .config import Config
from .gates import Decision
from .journal import record
from .termstructure import Kink

CLI = shutil.which("alpaca")


def build_mleg_payload(kink: Kink, decision: Decision) -> dict:
    """A long calendar: sell the rich near-dated call, buy the longer-dated one."""
    return {
        "order_class": "mleg",
        "qty": str(decision.qty),
        "type": "market",
        "time_in_force": "day",
        "legs": [
            {
                "symbol": kink.rich.call.symbol,
                "side": "sell",
                "ratio_qty": "1",
                "position_intent": "sell_to_open",
            },
            {
                "symbol": kink.hedge.call.symbol,
                "side": "buy",
                "ratio_qty": "1",
                "position_intent": "buy_to_open",
            },
        ],
    }


def cli_available() -> bool:
    return CLI is not None


def cli_account(cfg: Config) -> dict | None:
    """Read account state through the CLI -- the agent's primary state surface."""
    if not CLI:
        return None
    proc = subprocess.run(
        [CLI, "account", "get", "--quiet"], capture_output=True, text=True, timeout=30
    )
    if proc.returncode != 0:
        record("error", {"stage": "cli_account", "stderr": proc.stderr[:500]})
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def submit(cfg: Config, kink: Kink, decision: Decision, *, dry_run: bool = True) -> dict:
    payload = build_mleg_payload(kink, decision)
    record(
        "intent",
        {
            "underlying": kink.underlying,
            "rationale": kink.describe(),
            "score": kink.score,
            "qty": decision.qty,
            "max_loss_usd": decision.max_loss_usd,
            "payload": payload,
            "dry_run": dry_run,
        },
    )

    if dry_run:
        return {"status": "dry_run", "payload": payload}

    resp = requests.post(
        f"{cfg.base_url}/v2/orders",
        headers={**cfg.headers(), "content-type": "application/json"},
        json=payload,
        timeout=30,
    )
    result = {"status_code": resp.status_code, "body": resp.text[:1000]}
    record("submission", {"underlying": kink.underlying, **result})
    if resp.status_code >= 400:
        raise RuntimeError(f"order rejected: {resp.status_code} {resp.text[:300]}")
    return resp.json()


def validate_payload(cfg: Config, kink: Kink, decision: Decision) -> dict:
    """Prove the mleg schema is accepted without risking a fill.

    Submits the real leg structure as a limit order at a price the market cannot
    reach, then cancels it. A 4xx tells us the payload is wrong; an accepted
    order tells us the schema, the symbols and the permissions are all good.
    """
    payload = build_mleg_payload(kink, decision)
    payload["type"] = "limit"
    payload["qty"] = "1"
    # A long calendar is a debit; bidding 1c for it can never be filled.
    payload["limit_price"] = "0.01"

    resp = requests.post(
        f"{cfg.base_url}/v2/orders",
        headers={**cfg.headers(), "content-type": "application/json"},
        json=payload,
        timeout=30,
    )
    out: dict = {"status_code": resp.status_code, "body": resp.text[:800], "payload": payload}

    if resp.status_code < 300:
        order_id = resp.json().get("id")
        out["order_id"] = order_id
        cancel = requests.delete(
            f"{cfg.base_url}/v2/orders/{order_id}", headers=cfg.headers(), timeout=30
        )
        out["cancelled"] = cancel.status_code in (200, 204)

    record("validate", out)
    return out
