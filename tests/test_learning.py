"""Learning must refuse to conclude things the sample cannot support."""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from kink.learning import (  # noqa: E402
    Bucket, Observation, ScoredPrediction, calibration, score_predictions,
    signal_is_monotonic, suggest_threshold,
)

# Clear of the real scoring-rule boundary (2026-09-01T14:30Z), so these
# fixtures exercise pairing logic rather than the version guard.
T0 = dt.datetime(2026, 9, 10, 14, 0, tzinfo=dt.UTC)


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


# --- execution learning -----------------------------------------------------

from kink import learning as L  # noqa: E402


def _attempt(coid, agg, outcome):
    return {"client_order_id": coid, "aggressiveness": agg, "outcome": outcome}


def test_no_fills_still_produce_a_finding():
    """0 for 3 at the mid is evidence, not a null result."""
    rows = [_attempt(f"a{i}", 0.05, "unfilled") for i in range(3)]
    buckets = L.fill_calibration(rows)
    tight = next(b for b in buckets if b["lo"] == 0.05)
    assert tight["n"] == 3
    assert tight["fill_rate"] == 0.0


def test_thin_evidence_yields_no_slippage_suggestion():
    rows = [_attempt(f"a{i}", 0.05, "unfilled") for i in range(3)]
    slip, why = L.suggest_slippage(L.fill_calibration(rows))
    assert slip is None
    assert "need" in why


def test_cheapest_filling_bucket_is_preferred():
    """Not the surest price -- the cheapest one that actually transacts."""
    rows = (
        [_attempt(f"t{i}", 0.02, "unfilled") for i in range(6)]      # too tight
        + [_attempt(f"m{i}", 0.08, "filled") for i in range(5)]      # fills
        + [_attempt(f"w{i}", 0.25, "filled") for i in range(5)]      # also fills, dearer
    )
    slip, why = L.suggest_slippage(L.fill_calibration(rows))
    assert slip == 0.05          # the 5%-12% bucket, not the 20%+ one
    assert "cheapest bucket that fills" in why


def test_never_fills_reports_crossing_needed():
    rows = [_attempt(f"a{i}", 0.05, "unfilled") for i in range(14)]
    slip, why = L.suggest_slippage(L.fill_calibration(rows))
    assert slip is None
    assert "cross" in why


def test_pending_attempts_are_excluded_from_fill_rate():
    rows = [_attempt("a", 0.08, "filled"), _attempt("b", 0.08, "pending")]
    bucket = next(b for b in L.fill_calibration(rows) if b["lo"] == 0.05)
    assert bucket["n"] == 1
    assert bucket["fill_rate"] == 1.0



# --- scoring-rule versioning -------------------------------------------------

def test_pairs_across_a_scoring_change_are_refused():
    """The bug this prevents: reporting our own deployment as market movement.

    The first calibration table paired readings from before and after the
    monthly-expiration fix and concluded the thesis had failed. What had moved
    was the ruler.
    """
    before = obs(0, 0.08)
    after = obs(24, 0.02)
    object.__setattr__(before, "version", 1) if False else setattr(before, "version", 1)
    setattr(after, "version", 2)
    assert score_predictions([before, after]) == []


def test_pairs_within_one_scoring_rule_still_score():
    a, b = obs(0, 0.08), obs(24, 0.02)
    setattr(a, "version", 2)
    setattr(b, "version", 2)
    assert len(score_predictions([a, b])) == 1


def test_tiny_edges_are_excluded_from_the_ratio():
    """An edge of 0.05% moving to -0.1% scores as 300% decay. Not a measurement."""
    assert score_predictions([obs(0, 0.0005), obs(24, -0.001)]) == []


def test_median_is_immune_to_a_single_absurd_ratio():
    from kink.learning import Bucket
    normal = [ScoredPrediction("A", "e", 24, 0.05, 0.01, False) for _ in range(9)]
    absurd = ScoredPrediction("B", "e", 24, 0.0001, -0.05, False)   # ~500x
    b = Bucket("x", 0.0, 1.0, normal + [absurd])
    assert b.mean_decay > 5           # the mean is destroyed
    assert 0.7 < b.median_decay < 0.9  # the median is not
