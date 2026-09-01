"""Classification decides both what we compare against and what we may trade."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from kink.universe import (  # noqa: E402
    asset_class_of, classify, is_tradeable_without_earnings_feed,
)


def test_broad_equity_etfs_are_tradeable():
    for sym in ("SPY", "QQQ", "IWM", "DIA"):
        assert is_tradeable_without_earnings_feed(sym), sym


def test_sector_etfs_are_not_tradeable_without_earnings_feed():
    """SMH is largely NVDA; a constituent report bumps the fund's term structure."""
    for sym in ("SMH", "XLK", "XLF", "XBI"):
        assert not is_tradeable_without_earnings_feed(sym), sym


def test_non_equity_etfs_are_tradeable():
    for sym in ("GLD", "TLT", "USO", "SLV"):
        assert is_tradeable_without_earnings_feed(sym), sym


def test_unknown_symbols_default_to_single_name():
    """The cautious default: anything unrecognised is treated as a company."""
    inst = classify("ZZZZ")
    assert inst.kind == "single"
    assert inst.earnings_exposed
    assert not is_tradeable_without_earnings_feed("ZZZZ")


def test_single_names_are_not_tradeable():
    for sym in ("NVDA", "MSFT", "AAPL"):
        assert not is_tradeable_without_earnings_feed(sym), sym


def test_asset_classes_separate_cohorts():
    assert asset_class_of("SPY") == "equity"
    assert asset_class_of("TLT") == "rates"
    assert asset_class_of("GLD") == "commodity"
    assert asset_class_of("SPY") != asset_class_of("GLD")


def test_classification_is_case_insensitive():
    assert classify("spy").kind == classify("SPY").kind
