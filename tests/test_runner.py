"""The loop must never open positions at the wrong moment.

Alpaca returns every clock timestamp in Eastern time with its own offset. An
earlier version compared those against a hardcoded UTC hour, which made the
entire morning read as "warmup" and would have blocked every entry until late
in the session. These fixtures use the real payload shape for that reason.
"""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from kink.runner import session_phase  # noqa: E402

ET = dt.timezone(dt.timedelta(hours=-4))


def clock(is_open, *, et_hour, et_min, close_hour=16):
    ts = dt.datetime(2026, 9, 1, et_hour, et_min, tzinfo=ET)
    return {
        "is_open": is_open,
        "timestamp": ts.isoformat(),
        "next_close": dt.datetime(2026, 9, 1, close_hour, 0, tzinfo=ET).isoformat(),
        "next_open": "2026-09-02T09:30:00-04:00",
    }, ts


def test_closed_market():
    c, ts = clock(False, et_hour=2, et_min=0)
    assert session_phase(c, now=ts) == "closed"


def test_first_minutes_are_warmup():
    """Options open wide; the first prints are the worst marks of the day."""
    c, ts = clock(True, et_hour=9, et_min=33)
    assert session_phase(c, now=ts) == "warmup"


def test_warmup_ends_after_ten_minutes():
    c, ts = clock(True, et_hour=9, et_min=41)
    assert session_phase(c, now=ts) == "open"


def test_mid_morning_is_open_not_warmup():
    """The regression: 09:49 ET is trading time, not warmup."""
    c, ts = clock(True, et_hour=9, et_min=49)
    assert session_phase(c, now=ts) == "open"


def test_midday_is_open():
    c, ts = clock(True, et_hour=12, et_min=30)
    assert session_phase(c, now=ts) == "open"


def test_near_close_stops_opening():
    c, ts = clock(True, et_hour=15, et_min=50)
    assert session_phase(c, now=ts) == "closing"


def test_half_day_close_respected():
    """A 13:00 close still gets its full manage-only window."""
    c, ts = clock(True, et_hour=12, et_min=50, close_hour=13)
    assert session_phase(c, now=ts) == "closing"
