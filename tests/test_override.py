"""Standing close requests: a decision made after hours must survive the night."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from kink import override as O  # noqa: E402


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(O, "REQUESTS", tmp_path / "flatten.json")


def test_a_request_survives_being_reloaded(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    O.request(["SLV260916C00059000"], reason="built by a bug")
    assert "SLV260916C00059000" in O.pending()


def test_symbols_are_normalised(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    O.request([" slv260916c00059000 "])
    assert "SLV260916C00059000" in O.pending()


def test_a_request_for_something_not_held_clears_itself(monkeypatch, tmp_path):
    """Already flat is the outcome the request wanted."""
    _fresh(monkeypatch, tmp_path)
    O.request(["GONE"])
    assert O.execute_pending(cfg=None, held={}, live=True) == []
    assert O.pending() == {}


def test_dry_run_does_not_close(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    O.request(["HELD"])
    done = O.execute_pending(cfg=None, held={"HELD": -10}, live=False)
    assert done == ["HELD (dry run)"]
    assert "HELD" in O.pending()      # still queued


def test_a_failed_close_stays_queued(monkeypatch, tmp_path):
    """Rejected outside market hours; the next cycle must try again."""
    _fresh(monkeypatch, tmp_path)
    O.request(["HELD"])

    def boom(*a, **k):
        raise RuntimeError("options market orders are only allowed during market hours")

    import kink.execute as X
    monkeypatch.setattr(X, "run_cli", boom)
    assert O.execute_pending(cfg=None, held={"HELD": -10}, live=True) == []
    assert "HELD" in O.pending()


def test_a_successful_close_clears_the_request(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    O.request(["HELD"])
    import kink.execute as X
    monkeypatch.setattr(X, "run_cli", lambda *a, **k: {})
    assert O.execute_pending(cfg=None, held={"HELD": -10}, live=True) == ["HELD"]
    assert O.pending() == {}


def test_corrupt_file_does_not_crash(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    (tmp_path / "flatten.json").write_text("{broken", encoding="utf-8")
    assert O.pending() == {}
