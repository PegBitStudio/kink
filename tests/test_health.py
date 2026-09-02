"""Silence is the failure mode that logging never catches.

Two outages went unnoticed: a crash that sat live for ten minutes, and a runner
that simply died. A stale dashboard looks exactly like a quiet market, so the
heartbeat has to make the difference visible.
"""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from kink import health as H  # noqa: E402


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(H, "HEALTH_PATH", tmp_path / "health.json")


def test_one_failure_is_not_an_alert(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    h = H.record_cycle("trade failed", interval=600, failed_stage="trade", error="boom")
    assert h.consecutive_failures == 1
    assert not h.degraded
    assert h.alerts == []


def test_two_in_a_row_escalates(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    H.record_cycle("trade failed", interval=600, failed_stage="trade", error="boom")
    h = H.record_cycle("trade failed", interval=600, failed_stage="trade", error="boom")
    assert h.degraded
    assert h.alerts and "consecutive failures" in h.alerts[0]


def test_a_clean_cycle_clears_the_run(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    H.record_cycle("f", interval=600, failed_stage="trade", error="boom")
    H.record_cycle("f", interval=600, failed_stage="trade", error="boom")
    h = H.record_cycle("open: scanned and traded", interval=600)
    assert h.consecutive_failures == 0
    assert not h.degraded
    # but the history survives, so a transient fault is still visible
    assert h.alerts


def test_a_fresh_heartbeat_is_not_stale(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    h = H.record_cycle("market closed", interval=600)
    assert not H.is_stale(h)


def test_a_missed_heartbeat_is_stale(monkeypatch, tmp_path):
    """The dead-runner case: no error was ever logged, because nothing ran."""
    _fresh(monkeypatch, tmp_path)
    h = H.record_cycle("market closed", interval=600)
    later = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=600 * 4)
    assert H.is_stale(h, now=later)


def test_never_having_run_counts_as_stale(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    assert H.is_stale(H.Health())


def test_corrupt_health_file_does_not_crash(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    (tmp_path / "health.json").write_text("{not json", encoding="utf-8")
    assert H.load().cycles == 0


def test_summary_names_the_dead_case(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    h = H.record_cycle("market closed", interval=600)
    later = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=600 * 5)
    assert "may be dead" in H.summary(h, now=later)


def test_alert_history_is_capped(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    for i in range(30):
        H.record_cycle("f", interval=600, failed_stage=f"stage{i}", error="e")
    assert len(H.load().alerts) <= 10
