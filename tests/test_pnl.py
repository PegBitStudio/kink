"""P&L must be arithmetic on cash that moved, not an opinion about value.

The bug these guard: the first outcome log marked against quoted mids and
reported -$515 while the account had moved -$202.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from kink.pnl import build_book, reconcile  # noqa: E402


def fill(symbol, side, qty, price):
    return {"symbol": symbol, "side": side, "qty": str(qty), "price": str(price)}


# The real SPY calendar, as executed on 1 Sep.
CALENDAR = [
    fill("SPY260918C00763000", "sell_short", 10, 8.28),
    fill("SPY260925C00763000", "buy", 10, 9.62),
    fill("SPY260925C00763000", "sell", 3, 10.30),
    fill("SPY260918C00763000", "buy", 3, 9.07),
    fill("SPY260918C00763000", "buy", 7, 9.07),
    fill("SPY260925C00763000", "sell", 7, 10.30),
]


def test_round_trip_realises_actual_cash():
    book = build_book(CALENDAR)
    # short leg: +8280 sold, -9070 bought back = -790
    # long leg:  -9620 bought, +10300 sold     = +680
    assert abs(book.realised - (-110.0)) < 1e-6
    assert all(leg.closed for leg in book.legs.values())


def test_partial_fills_are_summed_not_averaged():
    """An order filled in two pieces has one true cost: their sum."""
    book = build_book(CALENDAR)
    long_leg = book.legs["SPY260925C00763000"]
    assert long_leg.fills == 3
    assert long_leg.net_qty == 0
    expected = (-9.62 * 10 + 10.30 * 3 + 10.30 * 7) * 100
    assert abs(long_leg.net_cash - expected) < 1e-6


def test_open_position_is_not_counted_as_realised():
    book = build_book([fill("SPY260918C00763000", "sell_short", 10, 8.28)])
    assert book.realised == 0.0
    assert len(book.open_legs) == 1


def test_selling_is_an_inflow_and_buying_an_outflow():
    book = build_book([fill("X", "sell_short", 1, 5.00), fill("X", "buy", 1, 3.00)])
    assert book.realised == 200.0          # sold at 5, bought back at 3
    book2 = build_book([fill("X", "buy", 1, 5.00), fill("X", "sell", 1, 3.00)])
    assert book2.realised == -200.0


def test_contracts_traded_counts_every_leg():
    assert build_book(CALENDAR).contracts_traded == 40


def test_small_residual_is_reported_as_a_fee_not_hidden():
    book = build_book(CALENDAR)
    # -110 of trading plus a ~$1.10 fee on 40 contracts
    rec = reconcile(book, equity=99_888.90, starting_equity=100_000.0)
    assert rec["explained_by_fees"]
    assert abs(rec["residual"]) > 0        # surfaced, never silently absorbed


def test_large_residual_fails_loudly():
    """If the arithmetic is wrong, say so rather than calling it fees."""
    book = build_book(CALENDAR)
    rec = reconcile(book, equity=95_000.0, starting_equity=100_000.0)
    assert not rec["explained_by_fees"]


def test_underlying_root_is_parsed_from_the_occ_symbol():
    """Not string-sliced -- 'SPYC' was the bug."""
    assert set(build_book(CALENDAR).underlyings()) == {"SPY"}


def test_malformed_rows_are_skipped_not_fatal():
    book = build_book(CALENDAR + [{"symbol": "", "side": "buy"},
                                  {"symbol": "X", "side": "buy", "qty": "n/a",
                                   "price": "1"}])
    assert abs(book.realised - (-110.0)) < 1e-6


def test_open_positions_do_not_look_like_an_arithmetic_error():
    """The check cried wolf the moment the agent held anything."""
    book = build_book([fill("SPY260918C00763000", "sell_short", 10, 8.28),
                       fill("SPY260925C00763000", "buy", 10, 9.62)])
    # paid 1340 to open; the position is now worth 1339, so equity is down 1
    rec = reconcile(book, equity=99_999.0, starting_equity=100_000.0,
                    open_market_value=1339.0)
    assert rec["explained_by_fees"], rec


def test_a_genuine_error_still_fails_loudly():
    book = build_book(CALENDAR)
    rec = reconcile(book, equity=80_000.0, starting_equity=100_000.0,
                    open_market_value=0.0)
    assert not rec["explained_by_fees"]
