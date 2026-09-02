"""Realised P&L computed from actual fills.

The first version of the outcome log marked positions against quoted mids and
reported -$515 while the account had moved -$202. Both numbers were wrong in
the same direction and for the same reason: a mid is an opinion about what
something is worth, not a record of what was paid.

Every figure here comes from the broker's own FILL activity feed -- the price
and quantity of each execution -- so P&L is arithmetic on cash that actually
moved. Partial fills are summed rather than averaged, because an order filled
in three pieces at three prices has one true cost and it is their sum.

Sign convention: cash received is positive. Selling an option is an inflow;
buying one is an outflow. A symbol whose net quantity is zero has been fully
closed, and its net cash *is* its realised P&L.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import requests

from .config import Config

CONTRACT_MULTIPLIER = 100.0


@dataclass
class Leg:
    symbol: str
    net_qty: int = 0          # positive long, negative short
    net_cash: float = 0.0     # cash received minus cash paid
    fills: int = 0

    @property
    def closed(self) -> bool:
        return self.net_qty == 0

    @property
    def realised(self) -> float:
        """Only meaningful once the leg is flat."""
        return self.net_cash if self.closed else 0.0


@dataclass
class Book:
    legs: dict[str, Leg] = field(default_factory=dict)
    _quantities: list[int] = field(default_factory=list)

    @property
    def realised(self) -> float:
        return sum(leg.realised for leg in self.legs.values())

    @property
    def open_legs(self) -> list[Leg]:
        return [leg for leg in self.legs.values() if not leg.closed]

    @property
    def closed_legs(self) -> list[Leg]:
        return [leg for leg in self.legs.values() if leg.closed and leg.fills]

    @property
    def contracts_traded(self) -> int:
        """Total contract-legs executed, used to size the expected fee."""
        return sum(abs(q) for q in self._quantities)

    def underlyings(self) -> dict[str, float]:
        """Realised P&L grouped by underlying root, parsed from the OCC symbol."""
        from .termstructure import parse_occ

        out: dict[str, float] = {}
        for leg in self.legs.values():
            if not leg.closed or not leg.fills:
                continue
            parsed = parse_occ(leg.symbol)
            root = parsed[0] if parsed else leg.symbol
            out[root] = out.get(root, 0.0) + leg.realised
        return out


PAGE_SIZE = 100          # the API's maximum
MAX_PAGES = 50           # a hard stop so a paging bug cannot loop forever


def fetch_fills(cfg: Config) -> list[dict]:
    """Every execution, paged. A missed page is a wrong P&L, so page properly."""
    out: list[dict] = []
    page_token: str | None = None

    for _ in range(MAX_PAGES):
        params: dict[str, object] = {"page_size": PAGE_SIZE}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(
            f"{cfg.base_url}/v2/account/activities/FILL",
            headers=cfg.headers(),
            params=params,
            timeout=20,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"fills unavailable: {resp.status_code} {resp.text[:200]}")
        page = resp.json()
        if not isinstance(page, list) or not page:
            break
        out.extend(page)
        if len(page) < PAGE_SIZE:
            break
        # Activities page by the id of the last row returned.
        page_token = str(page[-1].get("id") or "")
        if not page_token:
            break
    return out


def build_book(fills: list[dict]) -> Book:
    """Fold every execution into one position per contract."""
    book = Book()
    for f in fills:
        symbol = str(f.get("symbol") or "")
        if not symbol:
            continue
        try:
            price = float(f.get("price"))
            qty = int(float(f.get("qty")))
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue

        side = str(f.get("side") or "").lower()
        # "sell" and "sell_short" are both inflows; "buy" and "buy_to_cover" outflows.
        direction = 1 if side.startswith("sell") else -1

        leg = book.legs.setdefault(symbol, Leg(symbol=symbol))
        leg.net_qty += -direction * qty          # selling reduces the position
        leg.net_cash += direction * price * qty * CONTRACT_MULTIPLIER
        leg.fills += 1
        book._quantities.append(qty)
    return book


def realised_pnl(cfg: Config) -> tuple[float, Book]:
    book = build_book(fetch_fills(cfg))
    return book.realised, book


def reconcile(book: Book, equity: float, starting_equity: float) -> dict:
    """Check the fill arithmetic against the account itself.

    These will not match to the cent. Alpaca's paper engine deducts a
    per-contract regulatory fee that appears in cash but not in the activity
    feed -- roughly $0.025 a contract. That residual is reported as its own
    line rather than folded into P&L, because a number that quietly absorbs
    whatever is left over is not a measurement.

    The account is the authority on *how much* was made or lost. The fills are
    the authority on *where* it came from.
    """
    account_change = equity - starting_equity
    open_cash = sum(leg.net_cash for leg in book.open_legs)
    explained = book.realised + open_cash
    residual = account_change - explained

    contracts = book.contracts_traded or 1
    per_contract = abs(residual) / contracts
    # A residual consistent with a small per-contract fee is expected; anything
    # larger means the arithmetic itself is wrong.
    plausible_fee = per_contract <= 0.10

    return {
        "realised_from_fills": book.realised,
        "open_position_cash": open_cash,
        "account_change": account_change,
        "residual": residual,
        "contracts": contracts,
        "residual_per_contract": per_contract,
        "explained_by_fees": plausible_fee,
    }


def report(cfg: Config, *, starting_equity: float = 100_000.0) -> str:
    from . import execute

    try:
        pnl, book = realised_pnl(cfg)
    except RuntimeError as exc:
        return f"P&L unavailable: {exc}"

    try:
        account = execute.account(cfg) if execute.cli_available() else {}
        equity = float(account.get("equity") or starting_equity)
    except Exception:  # noqa: BLE001
        equity = starting_equity

    rec = reconcile(book, equity, starting_equity)

    lines = [
        "REALISED P&L (from fills, not marks)",
        f"  closed contracts   {len(book.closed_legs)}",
        f"  open contracts     {len(book.open_legs)}",
        f"  realised on fills  ${pnl:,.2f}",
        "",
        "RECONCILIATION -- the account is the authority on the total",
        f"  account change     ${rec['account_change']:,.2f}",
        f"  explained by fills ${rec['realised_from_fills'] + rec['open_position_cash']:,.2f}",
        f"  residual           ${rec['residual']:,.2f}  "
        f"({rec['residual_per_contract']:.3f}/contract over {rec['contracts']})",
        f"  residual verdict   "
        + ("consistent with a per-contract fee"
           if rec["explained_by_fees"] else "TOO LARGE -- arithmetic is wrong"),
    ]

    by_under = book.underlyings()
    if by_under:
        lines += ["", "BY UNDERLYING"]
        for root, amount in sorted(by_under.items(), key=lambda kv: kv[1]):
            lines.append(f"  {root:<8}${amount:>12,.2f}")
    return "\n".join(lines)
