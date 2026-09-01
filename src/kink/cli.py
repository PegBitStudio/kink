"""Entry point: python -m kink <scan|trade|status>"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from . import alpaca as alpaca_mod
from . import adjudicator, evidence, execute, exits, gates, journal, state, termstructure
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

        # Gates passed. Only now spend a model call, and only to look for a
        # reason NOT to trade -- the model cannot turn a refusal into a trade.
        ev = evidence.gather(cfg, kink.underlying, through=kink.rich.expiration)

        # No earnings-date source exists in the dossier, so a single name could
        # carry a catalyst nothing in this system can see. Refuse rather than
        # pretend the evidence is complete.
        if not ev.is_broad_etf and not cfg.trade_single_names:
            reason = ("single name with no earnings-date source; "
                      "set TRADE_SINGLE_NAMES=true to override")
            print()
            print(f"REFUSED {kink.underlying}: {reason}")
            journal.record(
                "refusal",
                {"underlying": kink.underlying, "stage": "evidence", "reasons": [reason]},
            )
            continue

        ruling = adjudicator.adjudicate(
            kink, today=dt.date.today().isoformat(), ev=ev
        )
        if not ruling.allows_trade:
            print(f"\nVETOED {kink.underlying} by adjudicator ({ruling.verdict}): "
                  f"{ruling.reason}")
            journal.record(
                "refusal",
                {
                    "underlying": kink.underlying,
                    "stage": "adjudicator",
                    "verdict": ruling.verdict,
                    "reasons": [ruling.reason],
                },
            )
            continue

        print(
            f"\nAPPROVED {kink.underlying}: {decision.qty}x calendar, "
            f"max loss ${decision.max_loss_usd:.0f}"
        )
        print(f"  adjudicator: {ruling.reason}")
        result = execute.submit(cfg, kink, decision, dry_run=not live)
        print(f"  -> {result.get('status', result.get('id', 'submitted'))}")
        if live:
            state.record_open(
                state.OpenTrade(
                    underlying=kink.underlying,
                    short_symbol=kink.rich.call.symbol,
                    long_symbol=kink.hedge.call.symbol,
                    qty=decision.qty,
                    entry_debit=execute.entry_limit(kink) or 0.0,
                    entry_edge=kink.score,
                    entry_raw_edge=kink.raw_score,
                    entry_cohort=kink.cohort_score,
                    opened_at=dt.datetime.now(dt.UTC).isoformat(),
                    client_order_id=str(result.get("client_order_id", "")),
                    adjudicator_reason=ruling.reason,
                )
            )
        committed += decision.max_loss_usd


def _live_view(cfg: Config, api: alpaca_mod.Alpaca, underlying: str):
    """Current contracts by symbol, and current idiosyncratic edge by expiration."""
    today = dt.date.today()
    spot = _spot(api, underlying)
    if spot is None:
        return {}, {}
    snaps = api.option_chain(
        underlying,
        expiration_gte=today.isoformat(),
        expiration_lte=(today + dt.timedelta(days=120)).isoformat(),
    )
    contracts = termstructure.to_contracts(snaps)
    by_symbol = {c.symbol: c for c in contracts}
    points = termstructure.build_term_structure(contracts, spot, today)
    kinks = termstructure.find_kinks(underlying, points)
    edge_by_exp = {k.rich.expiration: k.raw_score for k in kinks}
    return by_symbol, edge_by_exp


def manage(cfg: Config, api: alpaca_mod.Alpaca, *, live: bool, deadline: bool) -> None:
    trades = state.load()
    if not trades:
        print("no open calendars tracked")
        return

    for key, tr in trades.items():
        by_symbol, edge_by_exp = _live_view(cfg, api, tr.underlying)
        short = by_symbol.get(tr.short_symbol)
        long_ = by_symbol.get(tr.long_symbol)

        if short is None or long_ is None:
            print(f"{tr.underlying}: contracts not found in chain; skipping")
            continue

        short_mid, long_mid = short.mid, long_.mid
        current_debit = (
            long_mid - short_mid if short_mid is not None and long_mid is not None else None
        )
        current_edge = edge_by_exp.get(short.expiration, 0.0)
        short_dte = (short.expiration - dt.date.today()).days

        if current_debit is None:
            print(f"{tr.underlying}: no two-sided quote; cannot mark, holding")
            continue

        decision = exits.evaluate_exit(
            entry_debit=tr.entry_debit,
            current_debit=current_debit,
            entry_edge=tr.entry_edge,
            current_edge=current_edge,
            short_dte=short_dte,
            deadline_reached=deadline,
        )

        pnl = (current_debit - tr.entry_debit) * 100 * tr.qty
        long_dte = (long_.expiration - dt.date.today()).days
        print()
        print(f"{tr.underlying} {tr.qty}x {short_dte}d/{long_dte}d")
        print(f"  debit {tr.entry_debit:.2f} -> {current_debit:.2f}  (P&L ${pnl:,.0f})")
        print(f"  edge  {tr.entry_edge:.1%} -> {current_edge:.1%}")
        print(f"  {'CLOSE' if decision.should_close else 'HOLD '}: {decision.reason}")

        journal.record(
            "manage",
            {
                "underlying": tr.underlying,
                "entry_debit": tr.entry_debit,
                "current_debit": current_debit,
                "entry_edge": tr.entry_edge,
                "current_edge": current_edge,
                "short_dte": short_dte,
                "should_close": decision.should_close,
                "reason": decision.reason,
                "unrealised_pnl": pnl,
            },
        )

        if not decision.should_close:
            continue

        # Urgent exits go market; a discretionary exit can wait for the mid.
        limit = None if decision.is_urgent else round(current_debit * 0.97, 2)
        result = execute.close(
            cfg,
            short_symbol=tr.short_symbol,
            long_symbol=tr.long_symbol,
            qty=tr.qty,
            limit_price=limit,
            dry_run=not live,
        )
        print(f"  -> {result.get('id', result.get('status', 'submitted'))}")
        if live:
            state.record_closed(key)


def status(cfg: Config, api: alpaca_mod.Alpaca) -> None:
    if execute.cli_available():
        acct, source = execute.account(cfg), "alpaca CLI"
    else:
        acct, source = api.account(), "REST (CLI not found)"
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
    parser.add_argument(
        "command",
        choices=["scan", "trade", "manage", "flatten", "run", "status", "validate"],
    )
    parser.add_argument(
        "--interval", type=int, default=900,
        help="seconds between runner cycles (default 900)",
    )
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
    elif args.command == "run":
        from . import runner
        runner.run(cfg, api, live=args.live, interval=args.interval)
    elif args.command in ("manage", "flatten"):
        manage(cfg, api, live=args.live, deadline=args.command == "flatten")
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
        if result.get("order_id"):
            print(f"  accepted, order {result['order_id']} "
                  f"cancelled={result.get('cancelled')}")
            print("  -> mleg payload is VALID via the Alpaca CLI")
        else:
            print(f"  no order id returned: {result}")
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
