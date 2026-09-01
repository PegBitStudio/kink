"""Entry point: python -m kink <scan|trade|status>"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from . import alpaca as alpaca_mod
from . import execute, gates, journal, termstructure
from .config import Config


def _spot(api: alpaca_mod.Alpaca, symbol: str) -> float | None:
    q = api.stock_quote(symbol)
    bid, ask = q.get("bp"), q.get("ap")
    if not bid or not ask:
        return None
    return (bid + ask) / 2.0


def scan(cfg: Config, api: alpaca_mod.Alpaca) -> list[termstructure.Kink]:
    today = dt.date.today()
    horizon = (today + dt.timedelta(days=120)).isoformat()
    found: list[termstructure.Kink] = []

    for underlying in cfg.universe:
        spot = _spot(api, underlying)
        if spot is None:
            print(f"  {underlying}: no spot quote, skipping")
            continue
        snaps = api.option_chain(
            underlying,
            expiration_gte=today.isoformat(),
            expiration_lte=horizon,
        )
        contracts = termstructure.to_contracts(snaps)
        points = termstructure.build_term_structure(contracts, spot, today)
        if len(points) < 3:
            print(f"  {underlying}: only {len(points)} expirations with IV, need 3")
            continue
        curve = "  ".join(f"{p.dte}d:{p.atm_iv:.1%}" for p in points)
        print(f"  {underlying} @ {spot:.2f}  {curve}")
        found.extend(termstructure.find_kinks(underlying, points))

    # The macro calendar is common to every name; remove it before judging any
    # single one. This is what separates a mispricing from an event date.
    adjusted = termstructure.apply_cross_section(found)
    survivors = [k for k in adjusted if k.score >= cfg.min_kink_score]

    print()
    print(f"{len(found)} raw kinks -> {len(survivors)} idiosyncratic "
          f"(threshold {cfg.min_kink_score:.1%})")
    for k in adjusted[:8]:
        mark = "TRADE " if k.score >= cfg.min_kink_score else "  --  "
        print(f"  {mark}{k.describe()}")

    journal.record(
        "scan",
        {
            "raw": len(found),
            "survivors": [k.describe() for k in survivors],
            "rejected": [k.describe() for k in adjusted if k.score < cfg.min_kink_score],
        },
    )
    return survivors


def trade(cfg: Config, api: alpaca_mod.Alpaca, *, live: bool) -> None:
    positions = api.positions()
    committed = 0.0
    candidates = scan(cfg, api)
    if not candidates:
        print("\nNo kinks cleared the threshold. Holding.")
        return

    for kink in candidates:
        decision = gates.evaluate(
            kink, cfg, open_positions=len(positions), committed_risk_usd=committed
        )
        if not decision.allowed:
            print(f"\nREFUSED {kink.underlying}: " + "; ".join(decision.reasons))
            journal.record(
                "refusal", {"underlying": kink.underlying, "reasons": decision.reasons}
            )
            continue

        print(
            f"\nAPPROVED {kink.underlying}: {decision.qty}x calendar, "
            f"max loss ${decision.max_loss_usd:.0f}"
        )
        result = execute.submit(cfg, kink, decision, dry_run=not live)
        print(f"  -> {result.get('status', result.get('id', 'submitted'))}")
        committed += decision.max_loss_usd


def status(cfg: Config, api: alpaca_mod.Alpaca) -> None:
    acct = execute.cli_account(cfg) or api.account()
    source = "alpaca CLI" if execute.cli_available() else "REST"
    print(f"account (via {source})")
    print(f"  account id     {acct.get('id')}")
    print(f"  equity         ${float(acct.get('equity', 0)):,.2f}")
    print(f"  last equity    ${float(acct.get('last_equity', 0)):,.2f}")
    print(f"  options level  {acct.get('options_trading_level')}")
    print(f"  buying power   ${float(acct.get('options_buying_power', 0)):,.2f}")

    for p in api.positions():
        print(
            f"  {p['symbol']:<24} qty {p['qty']:>5}  "
            f"P&L ${float(p.get('unrealized_pl', 0)):,.2f}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kink")
    parser.add_argument("command", choices=["scan", "trade", "status", "validate"])
    parser.add_argument(
        "--live",
        action="store_true",
        help="actually submit orders (paper account); default is dry-run",
    )
    args = parser.parse_args(argv)

    cfg = Config()
    api = alpaca_mod.Alpaca(cfg)

    if args.command == "status":
        status(cfg, api)
    elif args.command == "validate":
        candidates = scan(cfg, api)
        if not candidates:
            print()
            print("no candidates to validate against")
            return 1
        kink = candidates[0]
        decision = gates.evaluate(kink, cfg, open_positions=0, committed_risk_usd=0.0)
        print()
        print(f"validating mleg schema with {kink.underlying} "
              f"{kink.rich.dte}d/{kink.hedge.dte}d calendar (unfillable limit)")
        if not decision.allowed:
            print("  gates refused, validating payload shape anyway: "
                  + "; ".join(decision.reasons))
            decision = gates.Decision(allowed=True, reasons=[], qty=1, max_loss_usd=0.0)
        result = execute.validate_payload(cfg, kink, decision)
        print(f"  HTTP {result['status_code']}")
        if result["status_code"] < 300:
            print(f"  accepted, order {result.get('order_id')} "
                  f"cancelled={result.get('cancelled')}")
            print("  -> mleg payload is VALID")
        else:
            print(f"  {result['body']}")
    elif args.command == "scan":
        scan(cfg, api)
    else:
        clock = api.clock()
        if not clock.get("is_open"):
            print(f"market closed (next open {clock.get('next_open')}); scanning only")
            scan(cfg, api)
        else:
            trade(cfg, api, live=args.live)
    return 0


if __name__ == "__main__":
    sys.exit(main())
