"""Exit bugs lose money silently, so every rule is pinned."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from kink.exits import evaluate_exit, closing_legs  # noqa: E402

BASE = dict(entry_debit=1.00, current_debit=1.00, entry_edge=0.06,
            current_edge=0.06, short_dte=17)


def ex(**kw):
    return evaluate_exit(**{**BASE, **kw})


def test_holds_when_nothing_has_happened():
    d = ex()
    assert not d.should_close
    assert "holding" in d.reason


def test_thesis_played_out_closes():
    d = ex(current_edge=0.015)          # 25% of entry edge remains
    assert d.should_close
    assert "thesis played out" in d.reason


def test_thesis_played_out_can_fire_before_profit_target():
    """The edge closing is the signal; the mark may not have caught up yet."""
    d = ex(current_edge=0.01, current_debit=1.05)   # only +5% P&L
    assert d.should_close
    assert "thesis played out" in d.reason


def test_edge_widening_against_us_closes_urgently():
    d = ex(current_edge=0.13)
    assert d.should_close
    assert d.is_urgent
    assert "thesis broken" in d.reason


def test_stop_loss_closes_urgently():
    d = ex(current_debit=0.45)
    assert d.should_close
    assert d.is_urgent
    assert "stop hit" in d.reason


def test_stop_takes_precedence_over_collapsed_edge():
    """Underwater and collapsing must leave urgently, not report success."""
    d = ex(current_debit=0.40, current_edge=0.005)
    assert d.is_urgent
    assert "stop hit" in d.reason


def test_profit_target_closes():
    d = ex(current_debit=1.30)
    assert d.should_close
    assert "profit target" in d.reason


def test_short_leg_near_expiry_closes_urgently():
    d = ex(short_dte=5)
    assert d.should_close
    assert d.is_urgent
    assert "expiry" in d.reason


def test_short_dte_beats_a_healthy_position():
    """Gamma risk is not negotiable, however well the trade is going."""
    d = ex(short_dte=3, current_debit=1.20, current_edge=0.06)
    assert d.should_close
    assert d.is_urgent


def test_deadline_flattens_everything():
    d = ex(deadline_reached=True, current_debit=1.10)
    assert d.should_close
    assert d.is_urgent
    assert "deadline" in d.reason


def test_missing_entry_debit_does_not_crash():
    d = ex(entry_debit=0.0)
    assert not d.should_close


def test_closing_legs_reverse_the_position():
    legs = closing_legs("SHORTSYM", "LONGSYM")
    assert legs[0]["symbol"] == "SHORTSYM"
    assert legs[0]["side"] == "buy"
    assert legs[0]["position_intent"] == "buy_to_close"
    assert legs[1]["side"] == "sell"
    assert legs[1]["position_intent"] == "sell_to_close"
