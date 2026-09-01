"""Order construction and submission, routed through the Alpaca CLI.

Every state-changing action the agent takes is a literal shell command, logged
verbatim to the journal before it runs. Anyone auditing this account can replay
what the agent did without reading a line of Python -- which is the point.

The CLI is also the right tool for the job here: it is built for long-running
agent sessions and cron, returns structured JSON on stdout, and carries a
`--dry-run` that renders the request body without sending it.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import uuid

from .config import Config
from .gates import Decision
from .journal import record
from .termstructure import Kink


def find_cli() -> str | None:
    """PATH first, then the vendored copy alongside the repo."""
    explicit = os.getenv("ALPACA_CLI", "").strip()
    if explicit and pathlib.Path(explicit).exists():
        return explicit
    on_path = shutil.which("alpaca")
    if on_path:
        return on_path
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        for candidate in (parent / "tools" / "alpaca.exe", parent / "tools" / "alpaca"):
            if candidate.exists():
                return str(candidate)
    return None


CLI = find_cli()


class CLIUnavailable(RuntimeError):
    pass


def cli_env(cfg: Config) -> dict[str, str]:
    """The CLI uses ALPACA_API_KEY / ALPACA_SECRET_KEY, not the APCA-* names."""
    return {
        **os.environ,
        "ALPACA_API_KEY": cfg.key_id,
        "ALPACA_SECRET_KEY": cfg.secret_key,
    }


def run_cli(cfg: Config, args: list[str], *, journal_as: str | None = None) -> dict | list:
    if not CLI:
        raise CLIUnavailable(
            "alpaca CLI not found; set ALPACA_CLI or place it in tools/"
        )
    cmd = [CLI, *args]
    printable = "alpaca " + " ".join(
        (a if " " not in a else f"'{a}'") for a in args
    )
    if journal_as:
        record(journal_as, {"command": printable})

    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=60, env=cli_env(cfg)
    )
    if proc.returncode != 0:
        record("error", {"command": printable, "stderr": proc.stderr[:600]})
        raise RuntimeError(f"alpaca CLI failed: {proc.stderr[:300]}")
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"raw": proc.stdout[:600]}


def cli_available() -> bool:
    return CLI is not None


def account(cfg: Config) -> dict:
    return run_cli(cfg, ["account", "get", "--quiet"])  # type: ignore[return-value]


def positions(cfg: Config) -> list:
    return run_cli(cfg, ["position", "list", "--quiet"])  # type: ignore[return-value]


def open_orders(cfg: Config) -> list:
    return run_cli(cfg, ["order", "list", "--quiet"])  # type: ignore[return-value]


def cancel(cfg: Config, order_id: str) -> dict:
    """The CLI takes --order-id, not a positional argument."""
    return run_cli(  # type: ignore[return-value]
        cfg, ["order", "cancel", "--order-id", order_id, "--quiet"], journal_as="command"
    )


def build_legs(kink: Kink) -> list[dict[str, str]]:
    """A long calendar: sell the rich near-dated call, buy the longer-dated one.

    Both legs are calls at the same strike, so the position is close to
    delta-neutral at entry -- the exposure is to the term structure, not to
    where the underlying goes.
    """
    return [
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
    ]


def submit_args(
    kink: Kink,
    decision: Decision,
    *,
    limit_price: float,
    client_order_id: str,
    dry_run: bool,
) -> list[str]:
    args = [
        "order", "submit",
        "--order-class", "mleg",
        "--qty", str(decision.qty),
        "--type", "limit",
        "--limit-price", f"{limit_price:.2f}",
        "--time-in-force", "day",
        "--legs", json.dumps(build_legs(kink)),
        "--client-order-id", client_order_id,
        "--quiet",
    ]
    if dry_run:
        args.append("--dry-run")
    return args


def entry_limit(kink: Kink, *, slippage: float = 0.05) -> float | None:
    """Pay the mid plus a small allowance, never the offer.

    On the free indicative feed the quotes are modified and delayed, so paying
    the full offer would systematically overpay by an unknown amount. A limit
    anchored to the mid means a bad quote costs us a missed fill, not money.
    """
    short_mid = kink.rich.call.mid
    long_mid = kink.hedge.call.mid
    if short_mid is None or long_mid is None:
        return None
    debit = long_mid - short_mid
    if debit <= 0:
        return None
    return round(debit * (1 + slippage), 2)


def submit(cfg: Config, kink: Kink, decision: Decision, *, dry_run: bool = True) -> dict:
    limit = entry_limit(kink)
    if limit is None:
        raise RuntimeError("no usable mid for one of the legs")

    client_order_id = f"kink-{uuid.uuid4().hex[:16]}"
    args = submit_args(
        kink, decision, limit_price=limit, client_order_id=client_order_id, dry_run=dry_run
    )

    record(
        "intent",
        {
            "underlying": kink.underlying,
            "rationale": kink.describe(),
            "raw_score": kink.raw_score,
            "cohort_score": kink.cohort_score,
            "idio_score": kink.score,
            "qty": decision.qty,
            "max_loss_usd": decision.max_loss_usd,
            "limit_price": limit,
            "client_order_id": client_order_id,
            "dry_run": dry_run,
        },
    )

    result = run_cli(cfg, args, journal_as="command")
    record("submission", {"underlying": kink.underlying, "result": result})
    return result  # type: ignore[return-value]


def validate_payload(cfg: Config, kink: Kink, decision: Decision) -> dict:
    """Prove the mleg schema is accepted without risking a fill.

    Submits the real leg structure at a price the market cannot reach, then
    cancels it. A failure tells us the payload is wrong; an accepted order tells
    us the schema, the symbols and the permissions are all good.
    """
    client_order_id = f"kink-validate-{uuid.uuid4().hex[:12]}"
    args = submit_args(
        kink, decision, limit_price=0.01, client_order_id=client_order_id, dry_run=False
    )
    result = run_cli(cfg, args, journal_as="command")
    order_id = result.get("id") if isinstance(result, dict) else None

    cancelled = False
    if order_id:
        try:
            cancel(cfg, str(order_id))
            cancelled = True
        except RuntimeError:
            cancelled = False

    out = {"order_id": order_id, "cancelled": cancelled, "result": result}
    record("validate", out)
    return out


def close(
    cfg: Config,
    *,
    short_symbol: str,
    long_symbol: str,
    qty: int,
    limit_price: float | None = None,
    dry_run: bool = True,
) -> dict:
    """Close a calendar by reversing its legs.

    Closes market by default. An exit that does not fill is worse than an exit
    at a slightly worse price -- particularly for the urgent reasons (stop,
    short leg near expiry, deadline), where the whole point is to be out.
    """
    from .exits import closing_legs

    client_order_id = f"kink-x-{uuid.uuid4().hex[:16]}"
    args = [
        "order", "submit",
        "--order-class", "mleg",
        "--qty", str(qty),
        "--time-in-force", "day",
        "--legs", json.dumps(closing_legs(short_symbol, long_symbol)),
        "--client-order-id", client_order_id,
        "--quiet",
    ]
    if limit_price is not None:
        args += ["--type", "limit", "--limit-price", f"{limit_price:.2f}"]
    else:
        args += ["--type", "market"]
    if dry_run:
        args.append("--dry-run")

    record(
        "exit_intent",
        {
            "short_symbol": short_symbol,
            "long_symbol": long_symbol,
            "qty": qty,
            "limit_price": limit_price,
            "client_order_id": client_order_id,
            "dry_run": dry_run,
        },
    )
    result = run_cli(cfg, args, journal_as="command")
    record("exit_submission", {"short_symbol": short_symbol, "result": result})
    return result  # type: ignore[return-value]


def cancel_stale_entries(cfg: Config, *, max_age_minutes: int = 5) -> list[str]:
    """Cancel our own working entry orders that the market has walked away from.

    An entry limit is derived from a quote at one instant. If the market moves,
    the order rests forever at a price that no longer means anything, and the
    agent silently stops trading while believing it has positions coming. So
    stale entries are cancelled and the next cycle re-derives everything --
    fresh scan, fresh gates, fresh size, fresh limit -- rather than chasing
    with a price that was never re-justified.
    """
    import datetime as _dt

    cancelled: list[str] = []
    try:
        orders = open_orders(cfg)
    except RuntimeError:
        return cancelled
    if not isinstance(orders, list):
        return cancelled

    now = _dt.datetime.now(_dt.UTC)
    for o in orders:
        coid = str(o.get("client_order_id") or "")
        if not coid.startswith("kink-"):
            continue  # never touch an order this agent did not place
        submitted = o.get("submitted_at") or o.get("created_at") or ""
        try:
            age = (now - _dt.datetime.fromisoformat(
                str(submitted).replace("Z", "+00:00")
            )).total_seconds() / 60.0
        except ValueError:
            age = max_age_minutes + 1
        if age < max_age_minutes:
            continue
        oid = str(o.get("id") or "")
        if not oid:
            continue
        try:
            cancel(cfg, oid)
            cancelled.append(oid)
            record("stale_cancel", {"order_id": oid, "age_minutes": round(age, 1),
                                    "client_order_id": coid})
        except RuntimeError:
            pass
    return cancelled
