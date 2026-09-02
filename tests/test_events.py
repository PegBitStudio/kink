"""Detecting events the market is not charging for.

The mirror of the earnings gate: same fact, opposite question.
"""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from kink import earnings as E, events  # noqa: E402
from kink.termstructure import Contract, TermPoint  # noqa: E402

TODAY = dt.date(2026, 9, 2)


def _pt(dte, iv):
    exp = TODAY + dt.timedelta(days=dte)
    c = Contract("X", "X", exp, "C", 100.0, iv, 0.5, 1.0, 1.1, 0.5, -0.2)
    return TermPoint(expiration=exp, dte=dte, atm_iv=iv, call=c, put=c)


def _earnings(monkeypatch, dates):
    monkeypatch.setattr(
        events, "earnings_lookup",
        lambda sym, start, end: E.EarningsInfo(sym, True, tuple(dates)),
    )


def test_cheap_expiration_with_earnings_is_flagged(monkeypatch):
    _earnings(monkeypatch, [TODAY + dt.timedelta(days=12)])
    points = [_pt(7, 0.30), _pt(14, 0.26), _pt(21, 0.32)]   # dips where earnings sit
    found = events.find_underpriced("NVDA", points, today=TODAY)
    assert len(found) == 1
    assert found[0].shortfall < 0
    assert "earnings" in found[0].describe()


def test_cheap_expiration_without_earnings_is_ignored(monkeypatch):
    """Cheapness alone is not a signal; the event is what anchors it."""
    monkeypatch.setattr(
        events, "earnings_lookup",
        lambda sym, start, end: E.EarningsInfo(sym, True, ()),
    )
    points = [_pt(7, 0.30), _pt(14, 0.26), _pt(21, 0.32)]
    assert events.find_underpriced("NVDA", points, today=TODAY) == []


def test_expiration_already_priced_for_the_event_is_ignored(monkeypatch):
    """The market charging for it is the normal case, not an opportunity."""
    _earnings(monkeypatch, [TODAY + dt.timedelta(days=12)])
    points = [_pt(7, 0.26), _pt(14, 0.34), _pt(21, 0.28)]   # bumps at earnings
    assert events.find_underpriced("NVDA", points, today=TODAY) == []


def test_unknown_calendar_yields_nothing(monkeypatch):
    """An unreachable calendar must not become evidence of an unpriced event."""
    monkeypatch.setattr(
        events, "earnings_lookup",
        lambda sym, start, end: E.EarningsInfo(sym, False, ()),
    )
    points = [_pt(7, 0.30), _pt(14, 0.26), _pt(21, 0.32)]
    assert events.find_underpriced("NVDA", points, today=TODAY) == []


def test_endpoints_have_no_neighbours_to_compare_against(monkeypatch):
    _earnings(monkeypatch, [TODAY + dt.timedelta(days=5)])
    points = [_pt(7, 0.20), _pt(14, 0.30), _pt(21, 0.31)]
    found = events.find_underpriced("NVDA", points, today=TODAY)
    assert all(e.dte != 7 for e in found)


def test_scan_all_skips_short_curves(monkeypatch):
    _earnings(monkeypatch, [TODAY + dt.timedelta(days=12)])
    assert events.scan_all({"NVDA": [_pt(7, 0.3), _pt(14, 0.26)]}, today=TODAY) == []
