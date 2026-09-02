"""Expiration typing and historical normalisation.

Both exist to stop the same failure: a persistent structural quirk being
re-discovered as a fresh signal every single scan.
"""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from kink.baseline import (  # noqa: E402
    Baseline, MIN_HISTORY, annotate, baseline_key, build,
)
from kink.learning import Observation  # noqa: E402
from kink.termstructure import (  # noqa: E402
    Contract, Kink, TermPoint, expiration_type, find_kinks,
)


# --- expiration typing ------------------------------------------------------

def test_third_friday_is_monthly():
    assert expiration_type(dt.date(2026, 9, 18)) == "monthly"
    assert expiration_type(dt.date(2026, 10, 16)) == "monthly"
    assert expiration_type(dt.date(2026, 11, 20)) == "monthly"


def test_other_fridays_are_weekly():
    assert expiration_type(dt.date(2026, 9, 11)) == "weekly"
    assert expiration_type(dt.date(2026, 9, 25)) == "weekly"


def test_a_friday_early_in_the_month_is_not_monthly():
    assert expiration_type(dt.date(2026, 9, 4)) == "weekly"


def _pt(exp: dt.date, iv: float, today: dt.date):
    c = Contract("X", "X", exp, "C", 100.0, iv, 0.5, 1.0, 1.1)
    return TermPoint(expiration=exp, dte=(exp - today).days, atm_iv=iv, call=c, put=c)


def test_monthly_is_not_scored_against_weeklies():
    """The bias being removed: a monthly bracketed by weeklies always looks rich."""
    today = dt.date(2026, 9, 1)
    points = [
        _pt(dt.date(2026, 9, 11), 0.20, today),   # weekly
        _pt(dt.date(2026, 9, 18), 0.22, today),   # monthly, structurally richer
        _pt(dt.date(2026, 9, 25), 0.20, today),   # weekly
    ]
    # No same-type neighbours exist for any point, so nothing is scored at all
    # rather than the monthly being flagged against the weeklies around it.
    assert find_kinks("SPY", points) == []


def test_monthly_scored_against_monthlies():
    today = dt.date(2026, 9, 1)
    points = [
        _pt(dt.date(2026, 9, 18), 0.20, today),
        _pt(dt.date(2026, 10, 16), 0.30, today),   # genuinely rich vs other monthlies
        _pt(dt.date(2026, 11, 20), 0.22, today),
    ]
    kinks = find_kinks("SPY", points)
    assert len(kinks) == 1
    assert kinks[0].exp_type == "monthly"
    assert kinks[0].rich.expiration == dt.date(2026, 10, 16)
    assert kinks[0].hedge.expiration == dt.date(2026, 11, 20)


# --- historical baselines ---------------------------------------------------

def _obs(sym, exp, raw):
    return Observation(
        ts="2026-09-01T14:00:00+00:00", underlying=sym, expiration=exp, dte=17,
        atm_iv=0.2, expected_iv=0.19, raw_score=raw, cohort_score=0.0,
        idio_score=raw, vol_points=0.01,
    )


def test_no_z_score_without_enough_history():
    base = build([_obs("SPY", "2026-09-18", 0.01) for _ in range(5)])
    assert not base[baseline_key("SPY", "monthly")].usable
    assert base[baseline_key("SPY", "monthly")].z(0.05) is None


def test_persistent_bump_scores_as_normal():
    """A name whose monthly always runs +1.5% must not signal at +1.5%."""
    history = [_obs("SPY", "2026-09-18", 0.015 + (i % 3) * 0.001)
               for i in range(MIN_HISTORY + 5)]
    base = build(history)[baseline_key("SPY", "monthly")]
    z = base.z(0.015)
    assert z is not None
    assert abs(z) < 1.0          # ordinary for this name


def test_genuine_anomaly_still_scores():
    history = [_obs("SPY", "2026-09-18", 0.015 + (i % 3) * 0.001)
               for i in range(MIN_HISTORY + 5)]
    base = build(history)[baseline_key("SPY", "monthly")]
    assert base.z(0.06) > 2.0


def test_flat_history_cannot_manufacture_huge_z():
    """A constant history would otherwise divide by ~zero."""
    history = [_obs("GLD", "2026-09-18", 0.02) for _ in range(MIN_HISTORY + 1)]
    base = build(history)[baseline_key("GLD", "monthly")]
    assert base.z(0.025) <= 1.1   # floored dispersion keeps this sane


def test_monthly_and_weekly_histories_are_separate():
    history = (
        [_obs("SPY", "2026-09-18", 0.02) for _ in range(MIN_HISTORY)]
        + [_obs("SPY", "2026-09-11", 0.00) for _ in range(MIN_HISTORY)]
    )
    base = build(history)
    assert base[baseline_key("SPY", "monthly")].mean > 0.015
    assert base[baseline_key("SPY", "weekly")].mean < 0.005


def test_annotate_leaves_z_none_when_history_is_thin():
    today = dt.date(2026, 9, 1)
    k = Kink(
        underlying="SPY",
        rich=_pt(dt.date(2026, 10, 16), 0.30, today),
        hedge=_pt(dt.date(2026, 11, 20), 0.22, today),
        raw_score=0.09, expected_iv=0.27, exp_type="monthly",
    )
    assert annotate([k], baselines={})[0].z_score is None


# --- hedge selection --------------------------------------------------------

def test_hedge_needs_real_time_between_the_legs():
    """Two options two days apart are almost the same option."""
    today = dt.date(2026, 9, 1)
    points = [
        _pt(dt.date(2026, 9, 14), 0.20, today),
        _pt(dt.date(2026, 9, 16), 0.26, today),   # the rich one
        _pt(dt.date(2026, 9, 18), 0.20, today),   # only 2 days later
        _pt(dt.date(2026, 9, 28), 0.21, today),   # 12 days later
    ]
    kinks = find_kinks("IWM", points)
    assert kinks, "a kink should still be found"
    gap = kinks[0].hedge.dte - kinks[0].rich.dte
    assert gap >= 7, f"hedge only {gap} days out"
    assert kinks[0].hedge.expiration == dt.date(2026, 9, 28)


def test_no_hedge_far_enough_means_no_trade():
    today = dt.date(2026, 9, 1)
    points = [
        _pt(dt.date(2026, 9, 14), 0.20, today),
        _pt(dt.date(2026, 9, 16), 0.26, today),
        _pt(dt.date(2026, 9, 18), 0.20, today),
    ]
    assert find_kinks("IWM", points) == []
