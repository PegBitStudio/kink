"""The kink detector is the one piece that must be provably correct.

These tests pin the two failure modes that matter: reporting a kink on a
perfectly normal upward-sloping curve, and missing a real one.
"""
import datetime as dt
import math
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from kink.termstructure import (  # noqa: E402
    Contract, TermPoint, find_kinks, parse_occ, build_term_structure, to_contracts,
)

TODAY = dt.date(2026, 9, 1)


def _point(dte: int, iv: float) -> TermPoint:
    exp = TODAY + dt.timedelta(days=dte)
    leg = Contract("X", "X", exp, "C", 100.0, iv, 0.5, 1.0, 1.1)
    return TermPoint(expiration=exp, dte=dte, atm_iv=iv, call=leg, put=leg)


def test_parse_occ():
    root, exp, right, strike = parse_occ("SPY260918C00450000")
    assert root == "SPY"
    assert exp == dt.date(2026, 9, 18)
    assert right == "C"
    assert strike == 450.0


def test_parse_occ_rejects_equity_symbol():
    assert parse_occ("SPY") is None


def test_smooth_sqrt_curve_has_no_kinks():
    # IV rising linearly in sqrt(time) is the null hypothesis: never a signal.
    points = [_point(d, 0.10 + 0.01 * math.sqrt(d)) for d in (7, 14, 30, 60, 90)]
    assert find_kinks("SPY", points, min_score=0.05) == []


def test_isolated_rich_expiration_is_detected():
    points = [_point(7, 0.18), _point(30, 0.34), _point(60, 0.22)]
    kinks = find_kinks("SPY", points, min_score=0.10)
    assert len(kinks) == 1
    assert kinks[0].rich.dte == 30
    assert kinks[0].hedge.dte == 60          # buy the longer-dated leg
    assert kinks[0].score > 0.60


def test_threshold_suppresses_marginal_kinks():
    points = [_point(7, 0.20), _point(30, 0.222), _point(60, 0.24)]
    assert find_kinks("SPY", points, min_score=0.15) == []


def test_term_structure_uses_strike_nearest_spot():
    exp = TODAY + dt.timedelta(days=30)
    ymd = exp.strftime("%y%m%d")
    snaps = {
        f"SPY{ymd}C00450000": {"impliedVolatility": 0.20, "greeks": {"delta": 0.5},
                               "latestQuote": {"bp": 1.0, "ap": 1.1}},
        f"SPY{ymd}P00450000": {"impliedVolatility": 0.22, "greeks": {"delta": -0.5},
                               "latestQuote": {"bp": 1.0, "ap": 1.1}},
        f"SPY{ymd}C00600000": {"impliedVolatility": 0.90, "greeks": {"delta": 0.05},
                               "latestQuote": {"bp": 0.1, "ap": 0.2}},
        f"SPY{ymd}P00600000": {"impliedVolatility": 0.95, "greeks": {"delta": -0.95},
                               "latestQuote": {"bp": 0.1, "ap": 0.2}},
    }
    points = build_term_structure(to_contracts(snaps), spot=451.0, today=TODAY)
    assert len(points) == 1
    # 450 strike chosen over 600; ATM IV is the call/put average, not the wing.
    assert abs(points[0].atm_iv - 0.21) < 1e-9


def test_contract_spread_pct():
    c = Contract("X", "X", TODAY, "C", 100.0, 0.2, 0.5, 1.00, 1.20)
    assert c.mid == 1.10
    assert abs(c.spread_pct - 0.20 / 1.10) < 1e-9
