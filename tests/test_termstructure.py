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
    Contract, Kink, TermPoint, find_kinks, parse_occ, build_term_structure, to_contracts,
)

# Chosen so none of the tenors used below land on a third Friday: these
# tests are about cohort and curve logic, not expiration type.
TODAY = dt.date(2026, 9, 2)


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


# --- cross-sectional filtering ---------------------------------------------

from kink.termstructure import apply_cross_section, find_kinks  # noqa: E402


def _kinks_for(name: str, ivs: dict[int, float]):
    pts = [_point(d, iv) for d, iv in sorted(ivs.items())]
    return find_kinks(name, pts)


def test_market_wide_bump_is_stripped_to_zero():
    """A +5% bump at 17d in every name is an event date, not an edge."""
    kinks = []
    for name in ("SPY", "QQQ", "IWM", "AAPL"):
        kinks += _kinks_for(name, {13: 0.20, 17: 0.221, 24: 0.22})
    adjusted = apply_cross_section(kinks)
    assert len(adjusted) == 4
    # every name shared the same bump, so nothing idiosyncratic survives
    for k in adjusted:
        assert abs(k.score) < 1e-9
        assert k.raw_score > 0.02


def test_lone_outlier_survives_the_cohort_subtraction():
    kinks = []
    for name in ("SPY", "QQQ", "IWM"):
        kinks += _kinks_for(name, {13: 0.20, 17: 0.221, 24: 0.22})
    kinks += _kinks_for("MSFT", {13: 0.20, 17: 0.28, 24: 0.22})  # much richer
    adjusted = apply_cross_section(kinks)
    top = adjusted[0]
    assert top.underlying == "MSFT"
    assert top.score > 0.20          # idiosyncratic richness remains
    assert top.cohort_score > 0.02   # and the shared component was identified


def test_thin_cohort_is_left_alone():
    """With two names the median is not a trustworthy common-component estimate."""
    kinks = _kinks_for("SPY", {13: 0.20, 17: 0.26, 24: 0.22})
    adjusted = apply_cross_section(kinks, min_cohort=3)
    assert adjusted[0].cohort_score == 0.0
    assert adjusted[0].score == adjusted[0].raw_score


def test_cohort_never_manufactures_a_signal():
    """A universe that is cheap at an expiration must not make a name look rich."""
    kinks = []
    for name in ("SPY", "QQQ", "IWM"):
        kinks += _kinks_for(name, {13: 0.20, 17: 0.209, 24: 0.22})   # below curve
    adjusted = apply_cross_section(kinks)
    for k in adjusted:
        assert k.cohort_score >= 0.0
        assert k.score <= k.raw_score + 1e-12


def test_cohort_groups_by_exact_expiration_not_bucket():
    """13d dips and 17d bumps must not be averaged together."""
    kinks = []
    for name in ("SPY", "QQQ", "IWM"):
        kinks += _kinks_for(name, {8: 0.20, 13: 0.19, 17: 0.24, 24: 0.22})
    adjusted = apply_cross_section(kinks)
    at17 = [k for k in adjusted if k.rich.dte == 17]
    at13 = [k for k in adjusted if k.rich.dte == 13]
    assert at17 and at13
    # the rich 17d cohort is identified and stripped ...
    assert all(abs(k.score) < 1e-9 for k in at17)
    # ... while the cheap 13d expiration is clamped, not credited
    assert all(k.cohort_score == 0.0 for k in at13)


def test_cohort_does_not_pool_across_asset_classes():
    """Gold must not dilute the equity macro estimate, or vice versa."""
    kinks = []
    for name in ("SPY", "QQQ", "IWM", "DIA"):
        kinks += _kinks_for(name, {13: 0.20, 17: 0.24, 24: 0.22})   # equities bump
    for name in ("GLD", "SLV", "USO"):
        kinks += _kinks_for(name, {13: 0.20, 17: 0.20, 24: 0.22})   # commodities flat
    adjusted = apply_cross_section(kinks)

    equities = [k for k in adjusted if k.underlying in ("SPY", "QQQ", "IWM", "DIA")
                and k.rich.dte == 17]
    commodities = [k for k in adjusted if k.underlying in ("GLD", "SLV", "USO")
                   and k.rich.dte == 17]

    # The equity bump is recognised as shared and stripped ...
    assert equities and all(k.cohort_score > 0.02 for k in equities)
    # ... without being imposed on commodities, which never bumped.
    assert commodities and all(k.cohort_score < 0.02 for k in commodities)


def test_vol_points_do_not_flatter_low_vol_names():
    """A bond ETF's 1-point wobble must not outrank an index's 1-point wobble."""
    bond = _point(38, 0.053)
    bond_kink = Kink("HYG", bond, _point(45, 0.045), raw_score=0.230,
                     expected_iv=0.043, cohort_score=0.0, cohort_estimated=True)
    index = _point(17, 0.176)
    index_kink = Kink("IWM", index, _point(24, 0.170), raw_score=0.110,
                      expected_iv=0.159, cohort_score=0.044, cohort_estimated=True)

    # HYG wins on percentage by a mile ...
    assert bond_kink.score > index_kink.score * 3
    # ... but they are within a rounding error in actual vol points.
    assert abs(bond_kink.vol_points - index_kink.vol_points) < 0.005


def test_cohort_estimated_flag_tracks_peer_count():
    thin = _kinks_for("GLD", {13: 0.20, 17: 0.26, 24: 0.22})
    assert not apply_cross_section(thin, min_cohort=3)[0].cohort_estimated

    thick = []
    for name in ("SPY", "QQQ", "IWM", "DIA"):
        thick += _kinks_for(name, {13: 0.20, 17: 0.26, 24: 0.22})
    assert all(k.cohort_estimated for k in apply_cross_section(thick, min_cohort=3)
               if k.rich.dte == 17)
