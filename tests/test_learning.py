"""Learning must refuse to conclude things the sample cannot support."""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from kink.learning import (  # noqa: E402
    Bucket, Observation, ScoredPrediction, calibration, score_predictions,
    signal_is_monotonic, suggest_threshold,
)

T0 = dt.datetime(2026, 9, 1, 14, 0, tzinfo=dt.UTC)


def obs(hours, edge, sym="SPY", exp="2026-09-18", traded=False):
    return Observation(
        ts=(T0 + dt.timedelta(hours=hours)).isoformat(),
        underlying=sym, expiration=exp, dte=17, atm_iv=0.18,
        expected_iv=0.16, raw_score=edge, cohort_score=0.0,
        idio_score=edge, vol_points=0.01, traded=traded,
    )


def test_prediction_needs_time_to_be_answered():
    """Two quotes an hour apart are the same observation, not a result."""
    assert score_predictions([obs(0, 0.06), obs(1, 0.02)]) == []


def test_converged_edge_scores_as_decay():
    s = score_predictions([obs(0, 0.08), obs(24, 0.02)])
    assert len(s) == 1
    assert abs(s[0].decay - 0.75) < 1e-9


def test_widening_edge_scores_as_negative_decay():
    s = score_predictions([obs(0, 0.04), obs(24, 0.08)])
    assert s[0].decay < 0


def test_each_observation_is_answered_once():
    """One answer per prediction -- the first reading far enough ahead.

    Three observations make two predictions (t0 answered by t24, t24 answered by
    t48), not three and not one: each is a distinct call made on a distinct day.
    """
    s = score_predictions([obs(0, 0.08), obs(24, 0.04), obs(48, 0.01)])
    assert len(s) == 2
    assert abs(s[0].decay - 0.50) < 1e-9   # 0.08 -> 0.04
    assert abs(s[1].decay - 0.75) < 1e-9   # 0.04 -> 0.01


def test_refused_candidates_are_scored_too():
    """The whole point: untraded predictions carry most of the sample."""
    s = score_predictions([obs(0, 0.05, traded=False), obs(24, 0.01)])
    assert len(s) == 1
    assert s[0].traded is False


def test_thin_sample_yields_no_threshold():
    t, why = suggest_threshold(calibration([
        ScoredPrediction("SPY", "e", 24, 0.05, 0.01, False) for _ in range(5)
    ]))
    assert t is None
    assert "need" in why


def test_backwards_signal_is_refused_loudly():
    """If bigger edges converge less, the answer is not a lower threshold."""
    preds = (
        [ScoredPrediction("A", "e", 24, 0.02, 0.001, False) for _ in range(40)]  # small: decays
        + [ScoredPrediction("B", "e", 24, 0.20, 0.30, False) for _ in range(40)]  # big: widens
    )
    t, why = suggest_threshold(calibration(preds))
    assert t is None
    assert "not behaving as the thesis predicts" in why


def test_monotonic_signal_detected():
    preds = (
        [ScoredPrediction("A", "e", 24, 0.02, 0.018, False) for _ in range(30)]
        + [ScoredPrediction("B", "e", 24, 0.20, 0.02, False) for _ in range(30)]
    )
    assert signal_is_monotonic(calibration(preds)) is True


def test_empty_buckets_do_not_crash():
    assert signal_is_monotonic([Bucket("x", 0, 1)]) is None
