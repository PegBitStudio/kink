"""The loop must never open positions at the wrong moment."""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from kink.runner import session_phase  # noqa: E402

UTC = dt.UTC


def clock(is_open, *, ts, next_close=None):
    return {
        "is_open": is_open,
        "timestamp": ts.isoformat(),
        "next_close": next_close.isoformat() if next_close else None,
    }


def test_closed_market():
    ts = dt.datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
    assert session_phase(clock(False, ts=ts), now=ts) == "closed"


def test_first_minutes_are_warmup():
    """Options open wide; the first prints are the worst marks of the day."""
    ts = dt.datetime(2026, 9, 1, 13, 33, tzinfo=UTC)   # 3 min after the open
    close = dt.datetime(2026, 9, 1, 20, 0, tzinfo=UTC)
    assert session_phase(clock(True, ts=ts, next_close=close), now=ts) == "warmup"


def test_mid_session_is_open():
    ts = dt.datetime(2026, 9, 1, 16, 0, tzinfo=UTC)
    close = dt.datetime(2026, 9, 1, 20, 0, tzinfo=UTC)
    assert session_phase(clock(True, ts=ts, next_close=close), now=ts) == "open"


def test_near_close_stops_opening():
    ts = dt.datetime(2026, 9, 1, 19, 50, tzinfo=UTC)   # 10 min to close
    close = dt.datetime(2026, 9, 1, 20, 0, tzinfo=UTC)
    assert session_phase(clock(True, ts=ts, next_close=close), now=ts) == "closing"


def test_warmup_boundary_opens_at_ten_minutes():
    ts = dt.datetime(2026, 9, 1, 13, 41, tzinfo=UTC)
    close = dt.datetime(2026, 9, 1, 20, 0, tzinfo=UTC)
    assert session_phase(clock(True, ts=ts, next_close=close), now=ts) == "open"
