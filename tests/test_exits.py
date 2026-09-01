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


# --- state reconciliation --------------------------------------------------

from kink import state as state_mod  # noqa: E402


def _trade(sym="QQQ", short="S1", long_="L1"):
    return state_mod.OpenTrade(
        underlying=sym, short_symbol=short, long_symbol=long_, qty=9,
        entry_debit=1.73, entry_edge=0.05, entry_raw_edge=0.08,
        entry_cohort=0.03, opened_at="2026-09-01T13:50:39+00:00",
        client_order_id="kink-abc",
    )


def test_reconcile_drops_a_trade_that_never_filled(tmp_path, monkeypatch):
    """A limit the market walked away from must not be managed as a position."""
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "open.json")
    state_mod.record_open(_trade())
    assert len(state_mod.load()) == 1

    dropped = state_mod.reconcile(held_symbols=set())
    assert dropped == ["S1|L1"]
    assert state_mod.load() == {}


def test_reconcile_keeps_a_filled_trade(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "open.json")
    state_mod.record_open(_trade())
    assert state_mod.reconcile(held_symbols={"S1", "L1"}) == []
    assert len(state_mod.load()) == 1


def test_reconcile_keeps_a_partially_recognised_trade(tmp_path, monkeypatch):
    """One leg showing is still a real position; do not discard it."""
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "open.json")
    state_mod.record_open(_trade())
    assert state_mod.reconcile(held_symbols={"S1"}) == []


# --- sizing must use the price we actually pay ------------------------------

import datetime as _dt  # noqa: E402

from kink.config import Config as _Config  # noqa: E402
from kink.gates import evaluate as _evaluate  # noqa: E402
from kink.termstructure import (  # noqa: E402
    Contract as _C, Kink as _K, TermPoint as _TP,
)


def _calendar(short_bid, short_ask, long_bid, long_ask):
    """A calendar priced so the mid and the payable limit differ."""
    today = _dt.date.today()
    s = _C("S", "X", today + _dt.timedelta(days=10), "C", 58.5, 0.30, 0.5,
           short_bid, short_ask)
    l = _C("L", "X", today + _dt.timedelta(days=17), "C", 58.5, 0.30, 0.5,
           long_bid, long_ask)
    return _K(
        underlying="SLV",
        rich=_TP(s.expiration, 10, 0.40, s, s),
        hedge=_TP(l.expiration, 17, 0.38, l, l),
        raw_score=0.09, expected_iv=0.36, cohort_score=0.01,
        cohort_estimated=True,
    )


def test_entry_debit_is_bounded_by_the_offer():
    """Never pay through the offer -- that liquidity is already there."""
    k = _calendar(short_bid=1.00, short_ask=1.10, long_bid=1.30, long_ask=1.42)
    # mid debit 0.31; +15% would be 0.36; crossing is 1.42-1.00 = 0.42
    assert k.entry_debit(0.15) == 0.36
    # a huge allowance still stops at the crossing price
    assert k.entry_debit(5.0) == 0.42


def test_sizing_uses_the_payable_price_not_the_mid(monkeypatch):
    """The regression: 45 lots at a 0.33 mid is $1,485; at a 0.38 fill it is $1,710."""
    monkeypatch.setenv("ALPACA_API_KEY_ID", "k")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "s")
    monkeypatch.setenv("MAX_RISK_PER_TRADE_USD", "1500")
    monkeypatch.setenv("ENTRY_SLIPPAGE", "0.15")
    monkeypatch.setenv("MIN_KINK_VOL_POINTS", "0")
    monkeypatch.setenv("MIN_KINK_SCORE", "0")
    cfg = _Config()

    k = _calendar(short_bid=1.00, short_ask=1.06, long_bid=1.30, long_ask=1.40)
    payable = k.entry_debit(cfg.entry_slippage)
    d = _evaluate(k, cfg, open_positions=0, committed_risk_usd=0.0)

    assert d.allowed, d.reasons
    # every contract is costed at the price we might actually pay
    assert d.max_loss_usd <= cfg.max_risk_per_trade_usd
    assert d.qty == int(cfg.max_risk_per_trade_usd // (payable * 100))
