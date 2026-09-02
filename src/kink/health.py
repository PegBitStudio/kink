"""Notice when the agent is broken, and when it has gone quiet.

Two outages so far were silent. A ZeroDivisionError killed a live cycle and sat
unnoticed for ten minutes; twice the runner died outright and nothing said so.
Logging faithfully is not the same as being watched.

The dangerous failure is silence, not a stack trace. A crash at least leaves a
record; a dead process leaves nothing at all, and a stale dashboard looks
exactly like a quiet market. So the health record carries a heartbeat, and
anything reading it can tell the difference between "nothing to do" and
"nobody home".
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
from dataclasses import asdict, dataclass, field

HEALTH_PATH = pathlib.Path(__file__).resolve().parents[2] / "docs" / "health.json"

# Two in a row is a pattern; one is an accident.
ESCALATE_AFTER = 2
# A cycle takes well under a minute, so three missed intervals means trouble.
STALE_MULTIPLIER = 3


@dataclass
class Health:
    last_cycle_at: str = ""
    last_status: str = ""
    interval_seconds: int = 600
    cycles: int = 0
    consecutive_failures: int = 0
    failing_stage: str = ""
    last_error: str = ""
    alerts: list[str] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return self.consecutive_failures >= ESCALATE_AFTER


def load() -> Health:
    if not HEALTH_PATH.exists():
        return Health()
    try:
        return Health(**json.loads(HEALTH_PATH.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, TypeError):
        return Health()


def save(h: Health) -> None:
    HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_PATH.write_text(json.dumps(asdict(h), indent=1), encoding="utf-8")


def is_stale(h: Health, *, now: dt.datetime | None = None) -> bool:
    """Has the heartbeat stopped? This is the check that catches a dead process."""
    if not h.last_cycle_at:
        return True
    try:
        last = dt.datetime.fromisoformat(h.last_cycle_at)
    except ValueError:
        return True
    now = now or dt.datetime.now(dt.UTC)
    return (now - last).total_seconds() > h.interval_seconds * STALE_MULTIPLIER


def record_cycle(
    status: str,
    *,
    interval: int,
    failed_stage: str = "",
    error: str = "",
) -> Health:
    """Update the heartbeat and escalate a run of failures into an alert."""
    h = load()
    h.last_cycle_at = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    h.last_status = status
    h.interval_seconds = interval
    h.cycles += 1

    if failed_stage:
        h.consecutive_failures += 1
        h.failing_stage = failed_stage
        h.last_error = error[:300]
        if h.consecutive_failures >= ESCALATE_AFTER:
            alert = (
                f"{h.consecutive_failures} consecutive failures in "
                f"'{failed_stage}': {h.last_error[:120]}"
            )
            if alert not in h.alerts:
                h.alerts.append(alert)
    else:
        # A clean cycle clears the run, but the alert history is kept so a
        # transient fault is still visible after it resolves.
        h.consecutive_failures = 0
        h.failing_stage = ""
        h.last_error = ""

    h.alerts = h.alerts[-10:]
    save(h)
    return h


def summary(h: Health | None = None, *, now: dt.datetime | None = None) -> str:
    h = h or load()
    lines = ["AGENT HEALTH"]
    if not h.last_cycle_at:
        lines.append("  no cycle recorded yet")
        return "\n".join(lines)

    stale = is_stale(h, now=now)
    try:
        age = (
            (now or dt.datetime.now(dt.UTC))
            - dt.datetime.fromisoformat(h.last_cycle_at)
        ).total_seconds() / 60
    except ValueError:
        age = -1

    state = "STALE -- the agent may be dead" if stale else (
        "DEGRADED" if h.degraded else "ok")
    lines += [
        f"  state            {state}",
        f"  last cycle       {h.last_cycle_at}  ({age:.0f} min ago)",
        f"  last status      {h.last_status}",
        f"  cycles           {h.cycles}",
        f"  failures in a row{h.consecutive_failures:>4}",
    ]
    if h.failing_stage:
        lines.append(f"  failing stage    {h.failing_stage}: {h.last_error[:110]}")
    if h.alerts:
        lines.append("  alerts:")
        lines += [f"    - {a}" for a in h.alerts[-4:]]
    return "\n".join(lines)
