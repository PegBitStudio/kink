"""The unattended loop.

Nothing here makes a trading decision. It decides only *when* to ask the rest of
the system to decide, and it stays alive across the failures a long-running
process actually meets: a dropped connection, a rate limit, a provider having a
bad afternoon.

Order of operations each cycle is deliberate -- exits before entries. If risk
needs to come off, it comes off before any new risk goes on, even when the same
cycle has found something attractive.

The loop is also the thing that respects the competition deadline. It flattens
on its own and stops, so a forgotten process cannot carry positions past the
point where they can still be closed.
"""
from __future__ import annotations

import datetime as dt
import time
import traceback

from . import alpaca as alpaca_mod
from . import cli as cli_mod
from .config import Config
from .journal import record

# Options open wide and the indicative feed lags; the first minutes of the
# session produce the worst marks of the day. Wait them out.
OPEN_BUFFER_MINUTES = 10

# Stop opening new positions well before the close so exits still have liquidity.
CLOSE_BUFFER_MINUTES = 20


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _parse(ts: str | None) -> dt.datetime | None:
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def session_phase(clock: dict, *, now: dt.datetime | None = None) -> str:
    """One of: closed, warmup, open, closing.

    `warmup` and `closing` both mean "manage positions, do not open new ones".
    """
    now = now or _now()
    if not clock.get("is_open"):
        return "closed"

    next_close = _parse(clock.get("next_close"))
    if next_close and (next_close - now) <= dt.timedelta(minutes=CLOSE_BUFFER_MINUTES):
        return "closing"

    # Alpaca reports next_open as the *following* session while the market is
    # open, so the warmup window is measured from today's open instead.
    #
    # Every timestamp in this payload is Eastern, carrying its own -04:00/-05:00
    # offset -- so the session open is 09:30 in the timestamp's own timezone.
    # Comparing against a UTC hour here is the bug this comment exists to
    # prevent: it made the whole morning look like warmup and would have blocked
    # every entry until late in the session.
    timestamp = _parse(clock.get("timestamp"))
    if timestamp is None:
        return "open"
    opened_today = timestamp.replace(hour=9, minute=30, second=0, microsecond=0)
    since_open = timestamp - opened_today
    if dt.timedelta(0) <= since_open < dt.timedelta(minutes=OPEN_BUFFER_MINUTES):
        return "warmup"

    return "open"


def deadline_reached(cfg: Config, *, now: dt.datetime | None = None) -> bool:
    if cfg.deadline_utc is None:
        return False
    return (now or _now()) >= cfg.deadline_utc


def flatten_window(cfg: Config, *, now: dt.datetime | None = None) -> bool:
    """True once we are close enough to the deadline to start closing out."""
    if cfg.deadline_utc is None:
        return False
    now = now or _now()
    return now >= cfg.deadline_utc - dt.timedelta(minutes=cfg.flatten_before_minutes)


def cycle(cfg: Config, api: alpaca_mod.Alpaca, *, live: bool) -> str:
    """One pass. Returns a short status string; never raises."""
    try:
        clock = api.clock()
    except Exception as exc:  # noqa: BLE001 - the loop must survive anything
        record("error", {"stage": "clock", "error": str(exc)[:300]})
        return f"clock unavailable ({type(exc).__name__})"

    phase = session_phase(clock)

    if flatten_window(cfg):
        cli_mod.manage(cfg, api, live=live, deadline=True)
        return "deadline: flattened"

    if phase == "closed":
        return "market closed"

    # Exits first, always.
    try:
        cli_mod.manage(cfg, api, live=live, deadline=False)
    except Exception as exc:  # noqa: BLE001
        record("error", {"stage": "manage", "error": str(exc)[:300],
                         "traceback": traceback.format_exc()[:800]})

    if phase in ("warmup", "closing"):
        return f"{phase}: managed positions, not opening"

    try:
        cli_mod.trade(cfg, api, live=live)
    except Exception as exc:  # noqa: BLE001
        record("error", {"stage": "trade", "error": str(exc)[:300],
                         "traceback": traceback.format_exc()[:800]})
        return f"trade failed ({type(exc).__name__})"

    return "open: scanned and traded"


def run(cfg: Config, api: alpaca_mod.Alpaca, *, live: bool, interval: int) -> None:
    record("runner_start", {"live": live, "interval": interval,
                            "deadline": str(cfg.deadline_utc)})
    print(f"kink runner: interval {interval}s, live={live}, "
          f"deadline={cfg.deadline_utc}")

    while True:
        started = _now()
        status = cycle(cfg, api, live=live)
        print(f"[{started:%Y-%m-%d %H:%M:%S}Z] {status}")
        record("cycle", {"status": status})

        if deadline_reached(cfg):
            print("deadline passed; runner stopping")
            record("runner_stop", {"reason": "deadline"})
            return

        elapsed = (_now() - started).total_seconds()
        time.sleep(max(5.0, interval - elapsed))
