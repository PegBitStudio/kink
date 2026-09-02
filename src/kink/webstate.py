"""Export the agent's state as public JSON for the live dashboard.

The dashboard is a static page on GitHub Pages that fetches this file. That
split matters: the page never holds a credential, never talks to Alpaca, and
cannot place an order. It reads a document the agent chose to publish.

So everything written here is deliberately publishable. Account *id* is in the
submission anyway; keys, order ids and anything that could be replayed are not
included. If a field would be a problem on a billboard, it does not belong in
this file.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import subprocess

from .alpaca import Alpaca
from .config import Config

STATE_PATH = pathlib.Path(__file__).resolve().parents[2] / "docs" / "state.json"
STARTING_EQUITY = 100_000.0


def _curve(points) -> list[dict]:
    return [{"dte": p.dte, "iv": round(p.atm_iv, 4)} for p in points]


def _health_block() -> dict:
    """A heartbeat the page can age, so silence is visible as silence."""
    from . import health

    h = health.load()
    return {
        "last_cycle_at": h.last_cycle_at,
        "last_status": h.last_status,
        "cycles": h.cycles,
        "interval_seconds": h.interval_seconds,
        "consecutive_failures": h.consecutive_failures,
        "degraded": h.degraded,
        "stale": health.is_stale(h),
        "failing_stage": h.failing_stage,
        "alerts": h.alerts[-5:],
    }


def build_state(cfg: Config, api: Alpaca) -> dict:
    from . import baseline, execute, learning, pnl as pnl_mod
    from .dashboard import collect, recent_journal

    rows, curves, account, positions = collect(cfg, api, keep_contracts=True)

    try:
        realised, book = pnl_mod.realised_pnl(cfg)
        equity = float(account.get("equity") or STARTING_EQUITY)
        rec = pnl_mod.reconcile(book, equity, STARTING_EQUITY)
    except Exception:  # noqa: BLE001
        realised, rec = 0.0, {}

    try:
        clock = api.clock()
    except Exception:  # noqa: BLE001
        clock = {}

    obs = learning.load_observations()
    scored = learning.score_predictions(obs)
    buckets = learning.calibration(scored)
    threshold, threshold_why = learning.suggest_threshold(buckets)
    attempts = learning.load_attempts()
    fills = learning.fill_calibration(attempts)
    slip, slip_why = learning.suggest_slippage(fills)
    bases = baseline.build(obs)

    candidates = []
    for r in sorted(rows, key=lambda r: (not r.allowed, -r.kink.score)):
        k = r.kink
        candidates.append({
            "underlying": k.underlying,
            "exp_type": k.exp_type,
            "sell_dte": k.rich.dte,
            "buy_dte": k.hedge.dte,
            "atm_iv": round(k.rich.atm_iv, 4),
            "expected_iv": round(k.expected_iv, 4),
            "raw": round(k.raw_score, 4),
            "cohort": round(k.cohort_score, 4),
            "idio": round(k.score, 4),
            "vol_points": round(k.vol_points, 5),
            "z": None if k.z_score is None else round(k.z_score, 2),
            "cohort_estimated": k.cohort_estimated,
            "net_vega": None if k.net_vega is None else round(k.net_vega, 2),
            "allowed": r.allowed,
            "reasons": r.reasons,
            "qty": r.qty,
            "max_loss": round(r.max_loss, 2),
            "curve": _curve(curves.get(k.underlying, [])),
        })

    from .dashboard import CONTRACTS, SPOTS
    from .earnings import lookup as earnings_lookup
    from .universe import classify, is_tradeable_without_earnings_feed

    # Surface the names a reader would most want to see: whatever cleared the
    # gates, then the strongest refusals, so the picture is never empty.
    featured = [c["underlying"] for c in candidates if c["allowed"]][:2]
    featured += [c["underlying"] for c in candidates if not c["allowed"]
                 and c["underlying"] not in featured][:2]
    surfaces = {}
    for sym in featured:
        surf = build_surface(CONTRACTS.get(sym, []), SPOTS.get(sym, 0), dt.date.today())
        if surf:
            surfaces[sym] = surf

    # Every scanned symbol, so the "why not" search can answer for all of them
    # rather than only for the ones that produced a candidate.
    best = {}
    for c in candidates:
        u = c["underlying"]
        if u not in best or c["idio"] > best[u]["idio"]:
            best[u] = c

    horizon = dt.date.today() + dt.timedelta(days=45)
    universe_status = []
    for sym in cfg.universe:
        inst = classify(sym)
        top = best.get(sym)
        earn = None
        if not is_tradeable_without_earnings_feed(sym):
            try:
                earn = earnings_lookup(sym, start=dt.date.today(), end=horizon)
            except Exception:  # noqa: BLE001
                earn = None
        universe_status.append({
            "symbol": sym,
            "asset_class": inst.asset_class,
            "kind": inst.kind,
            "scanned": sym in curves,
            "expiries": len(curves.get(sym, [])),
            "candidates": sum(1 for c in candidates if c["underlying"] == sym),
            "best_idio": top["idio"] if top else None,
            "allowed": bool(top and top["allowed"]),
            "reasons": top["reasons"] if top else (
                ["no expiration stood above the curve its neighbours imply"]
                if sym in curves else ["no usable option chain on this scan"]
            ),
            "earnings": None if earn is None else earn.describe(),
        })

    return {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "account": {
            "id": str(account.get("id", "")),
            "equity": float(account.get("equity") or 0),
            "starting_equity": STARTING_EQUITY,
            # The account is the authority on how much was made or lost; the
            # fill sum is the authority on where it came from. Publishing the
            # fill number as "realised" would disagree with the account by the
            # per-contract fee, and the results document quotes the account.
            "realised_pnl": round(float(account.get("equity") or STARTING_EQUITY)
                                  - STARTING_EQUITY, 2),
            "realised_from_fills": round(realised, 2),
            "reconciliation": {
                k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in rec.items()
            },
        },
        "market": {
            "is_open": bool(clock.get("is_open")),
            "next_open": clock.get("next_open"),
            "next_close": clock.get("next_close"),
        },
        "deadline_utc": str(cfg.deadline_utc) if cfg.deadline_utc else None,
        "limits": {
            "max_risk_per_trade": cfg.max_risk_per_trade_usd,
            "max_total_risk": cfg.max_total_risk_usd,
            "min_idio": cfg.min_kink_score,
            "min_vol_points": cfg.min_kink_vol_points,
            "min_z": cfg.min_kink_z,
            "max_z": cfg.max_kink_z,
            "entry_slippage": cfg.entry_slippage,
            "vega_stress_points": cfg.vega_stress_points,
            "max_vega_stress_fraction": cfg.max_vega_stress_fraction,
        },
        "positions": [
            {
                "symbol": str(p.get("symbol", "")),
                "qty": int(float(p.get("qty") or 0)),
                "avg_entry": float(p.get("avg_entry_price") or 0),
                "unrealised": float(p.get("unrealized_pl") or 0),
            }
            for p in positions
        ],
        "health": _health_block(),
        "candidates": candidates,
        "surfaces": surfaces,
        "universe_status": universe_status,
        "journal": [
            {"time": t, "kind": k, "message": m} for t, k, m in recent_journal(60)
        ],
        "learning": {
            "observations": len(obs),
            "scored": len(scored),
            "calibration": [
                {
                    "label": b.label, "n": b.n,
                    "hit_rate": round(b.hit_rate, 3),
                    "mean_decay": round(b.mean_decay, 3),
                }
                for b in buckets
            ],
            "monotonic": learning.signal_is_monotonic(buckets),
            "threshold_suggestion": threshold,
            "threshold_reason": threshold_why,
            "fills": fills,
            "slippage_suggestion": slip,
            "slippage_reason": slip_why,
            "baselines": [
                {"key": b.key, "n": b.n,
                 "mean": round(b.mean, 4), "stdev": round(b.stdev, 4)}
                for b in sorted(bases.values(), key=lambda b: -b.mean)
                if b.usable
            ][:16],
        },
        "outcomes": [
            {
                "underlying": o.get("underlying"),
                "pnl": round(float(o.get("pnl") or 0), 2),
                "reason": o.get("reason"),
                "held_hours": round(float(o.get("held_hours") or 0), 1),
            }
            for o in learning.load_outcomes()
        ],
    }


def write_state(cfg: Config, api: Alpaca) -> pathlib.Path:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(build_state(cfg, api), indent=1, default=str), encoding="utf-8"
    )
    return STATE_PATH


def publish(cfg: Config, api: Alpaca, *, push: bool = True) -> str:
    """Write the state file and push it, so the live page updates itself.

    Commits only when the content actually changed -- a cycle that found
    nothing new should not add a commit saying so.
    """
    path = write_state(cfg, api)
    repo = path.parents[1]

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, timeout=90
        )

    changed = git("status", "--porcelain", "--", str(path)).stdout.strip()
    if not changed:
        return "state unchanged"
    if not push:
        return "state written (not pushed)"

    git("add", str(path))
    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M")
    commit = git(
        "-c", "user.name=Kink",
        "-c", "user.email=oluwadamilola.oladunni001@gmail.com",
        "commit", "-q", "-m", f"state: {stamp}Z",
    )
    if commit.returncode != 0:
        return f"commit failed: {commit.stderr[:160]}"
    pushed = git("push", "-q", "origin", "HEAD")
    if pushed.returncode != 0:
        return f"push failed: {pushed.stderr[:160]}"
    return "state published"


# --- the volatility surface -------------------------------------------------
#
# The term structure is one slice through a surface: implied vol as a function
# of both strike and expiration. The agent only trades the slice, but the
# surface is what the slice was cut from, and seeing it makes the kink
# comprehensible in a way a single line does not.

SURFACE_MONEYNESS = 0.12      # strikes within +/-12% of spot
SURFACE_MAX_EXPIRIES = 8
SURFACE_MAX_STRIKES = 13


def build_surface(contracts: list, spot: float, today) -> dict | None:
    """A strike-by-expiration grid of implied vol, averaged across calls and puts.

    Averaging the two cancels most of the skew contamination you get from
    either alone, the same reason the term structure does it.
    """
    if not contracts or not spot:
        return None

    lo, hi = spot * (1 - SURFACE_MONEYNESS), spot * (1 + SURFACE_MONEYNESS)
    cell: dict[tuple[object, float], list[float]] = {}
    for c in contracts:
        if c.iv is None or c.iv <= 0 or not (lo <= c.strike <= hi):
            continue
        dte = (c.expiration - today).days
        if dte < 5 or dte > 120:
            continue
        cell.setdefault((c.expiration, c.strike), []).append(c.iv)

    if not cell:
        return None

    expiries = sorted({e for e, _ in cell})[:SURFACE_MAX_EXPIRIES]
    strikes = sorted({s for _, s in cell})
    # Thin the strikes evenly rather than truncating, so the grid still spans
    # the full moneyness range.
    if len(strikes) > SURFACE_MAX_STRIKES:
        step = len(strikes) / SURFACE_MAX_STRIKES
        strikes = [strikes[int(i * step)] for i in range(SURFACE_MAX_STRIKES)]

    grid = []
    for e in expiries:
        row = []
        for s in strikes:
            vals = cell.get((e, s))
            row.append(round(sum(vals) / len(vals), 4) if vals else None)
        grid.append(row)

    return {
        "spot": round(spot, 2),
        "dtes": [(e - today).days for e in expiries],
        "strikes": [round(s, 2) for s in strikes],
        "iv": grid,
    }
