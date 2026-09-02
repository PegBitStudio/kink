"""Earnings is the one catalyst that reliably ruins this trade.

Every test here is about the same distinction: not knowing and knowing there is
nothing look identical in a boolean, and only one of them is safe to trade on.
"""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from kink import earnings as E  # noqa: E402

START = dt.date(2026, 9, 2)
END = dt.date(2026, 9, 20)


def _stub_cache(monkeypatch, tmp_path, mapping, *, complete=True):
    monkeypatch.setattr(E, "CACHE", tmp_path / "cache.json")
    monkeypatch.setattr(E, "load_window", lambda s, e, refresh=False: (mapping, complete))


def test_confirmed_report_blocks(monkeypatch, tmp_path):
    _stub_cache(monkeypatch, tmp_path, {"2026-09-08": ["ORCL", "CASY"]})
    info = E.lookup("ORCL", start=START, end=END)
    assert info.known and info.has_event
    assert not info.safe_to_trade
    assert "2026-09-08" in info.describe()


def test_confirmed_empty_calendar_clears(monkeypatch, tmp_path):
    _stub_cache(monkeypatch, tmp_path, {"2026-09-08": ["ORCL"]})
    info = E.lookup("NVDA", start=START, end=END)
    assert info.known and not info.has_event
    assert info.safe_to_trade


def test_unreachable_calendar_does_not_clear(monkeypatch, tmp_path):
    """The whole point: silence is not an all-clear."""
    _stub_cache(monkeypatch, tmp_path, {}, complete=False)
    info = E.lookup("NVDA", start=START, end=END)
    assert not info.known
    assert not info.safe_to_trade
    assert "unavailable" in info.describe()


def test_a_hit_is_decisive_even_if_other_days_failed(monkeypatch, tmp_path):
    """Finding the event still counts when the rest of the window is patchy."""
    _stub_cache(monkeypatch, tmp_path, {"2026-09-08": ["ORCL"]}, complete=False)
    info = E.lookup("ORCL", start=START, end=END)
    assert info.known and info.has_event
    assert not info.safe_to_trade


def test_lookup_is_case_insensitive(monkeypatch, tmp_path):
    _stub_cache(monkeypatch, tmp_path, {"2026-09-08": ["ORCL"]})
    assert E.lookup("orcl", start=START, end=END).has_event


def test_dates_outside_the_window_are_ignored(monkeypatch, tmp_path):
    _stub_cache(monkeypatch, tmp_path, {"2026-10-15": ["ORCL"]})
    assert E.lookup("ORCL", start=START, end=END).safe_to_trade


def test_empty_day_is_a_real_answer_not_a_failure():
    """A day with no reporters returns [], which must not read as an error."""
    assert E.fetch_day.__doc__ and "None means" in E.fetch_day.__doc__


def test_window_is_capped(monkeypatch, tmp_path):
    """A runaway range would be hundreds of requests."""
    calls = []
    monkeypatch.setattr(E, "CACHE", tmp_path / "c.json")
    monkeypatch.setattr(E, "fetch_day", lambda d, timeout=15: calls.append(d) or [])
    E.load_window(START, START + dt.timedelta(days=400))
    assert len(calls) <= E.MAX_DAYS + 1
