"""What each symbol is, and which names it should be compared against.

Two facts about the universe drive the rest of the system:

**Asset class decides the cohort.** The cross-sectional subtraction assumes the
names in a cohort share a macro calendar. Equities bump together for payrolls
and the FOMC; gold and long bonds do not respond to the same events in the same
way. Pooling them would drag the median down and make ordinary equity richness
look idiosyncratic -- which would cause the agent to trade noise. So cohorts are
formed within an asset class.

**Concentration decides tradeability.** A broad index ETF has no earnings by
construction, so the missing earnings feed cannot hurt us. A sector ETF is a
different animal: SMH is largely NVDA, XLK is largely AAPL and MSFT, and when a
dominant constituent reports, the fund's implied vol bumps for that expiration
exactly as a single name would. Treating those as earnings-free would reintroduce
the blindness the single-name guard exists to prevent.
"""
from __future__ import annotations

from dataclasses import dataclass

EQUITY = "equity"
RATES = "rates"
COMMODITY = "commodity"

BROAD = "broad"          # diversified index: no meaningful single-name earnings
SECTOR = "sector"        # concentrated: inherits constituent earnings
NON_EQUITY = "non_equity"  # no equity earnings exist at all
SINGLE = "single"        # an actual company


@dataclass(frozen=True)
class Instrument:
    symbol: str
    asset_class: str
    kind: str

    @property
    def earnings_exposed(self) -> bool:
        """Whether a missing earnings feed is a real risk for this symbol."""
        return self.kind in (SINGLE, SECTOR)


_TABLE: dict[str, tuple[str, str]] = {
    # Diversified equity indices -- earnings are averaged away across hundreds
    # of names, so no single report moves the fund's term structure much.
    "SPY": (EQUITY, BROAD),
    "QQQ": (EQUITY, BROAD),
    "IWM": (EQUITY, BROAD),
    "DIA": (EQUITY, BROAD),
    "VTI": (EQUITY, BROAD),
    "VOO": (EQUITY, BROAD),
    "EFA": (EQUITY, BROAD),
    "EEM": (EQUITY, BROAD),
    "MDY": (EQUITY, BROAD),
    "RSP": (EQUITY, BROAD),
    # Sector funds -- liquid and useful for the cohort estimate, but too
    # concentrated to sell premium on without an earnings feed.
    "XLF": (EQUITY, SECTOR),
    "XLE": (EQUITY, SECTOR),
    "XLK": (EQUITY, SECTOR),
    "XLV": (EQUITY, SECTOR),
    "XLI": (EQUITY, SECTOR),
    "SMH": (EQUITY, SECTOR),
    "XBI": (EQUITY, SECTOR),
    "XRT": (EQUITY, SECTOR),
    # Rates: driven by the same macro calendar as equities, but their own
    # supply events (auctions, refunding) do not touch stocks.
    "TLT": (RATES, NON_EQUITY),
    "IEF": (RATES, NON_EQUITY),
    "SHY": (RATES, NON_EQUITY),
    "HYG": (RATES, NON_EQUITY),
    "LQD": (RATES, NON_EQUITY),
    # Commodities and FX: no equity earnings exist here at all.
    "GLD": (COMMODITY, NON_EQUITY),
    "SLV": (COMMODITY, NON_EQUITY),
    "USO": (COMMODITY, NON_EQUITY),
    "UNG": (COMMODITY, NON_EQUITY),
    "FXI": (EQUITY, BROAD),
}


def classify(symbol: str) -> Instrument:
    """Unknown symbols are assumed to be single names -- the cautious default."""
    sym = symbol.upper()
    asset_class, kind = _TABLE.get(sym, (EQUITY, SINGLE))
    return Instrument(symbol=sym, asset_class=asset_class, kind=kind)


def asset_class_of(symbol: str) -> str:
    return classify(symbol).asset_class


def is_tradeable_without_earnings_feed(symbol: str) -> bool:
    """Whether a symbol is safe even with no earnings calendar at all.

    Only diversified funds qualify. Single names and concentrated sector funds
    need the calendar consulted -- see earnings.py, which now provides it. This
    function answers "can we skip the check", not "can we trade this".
    """
    return not classify(symbol).earnings_exposed


# A default universe wide enough to give each cohort several names at every
# expiration, which is what makes the median a usable estimate of the shared
# component. Sector and single names are scanned for cohort purposes even
# though they will not be traded.
DEFAULT_UNIVERSE = (
    "SPY", "QQQ", "IWM", "DIA", "EFA", "EEM",          # broad equity
    "XLF", "XLE", "XLK", "XLV", "SMH",                 # equity sectors
    "TLT", "IEF", "HYG",                               # rates
    "GLD", "SLV", "USO",                               # commodities
)
