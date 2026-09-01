# Kink — a term-structure options agent

Most options agents trade **direction** (will it go up?) or **magnitude** (will it
move?). Kink trades **shape**: the relationship between implied volatility at
different expirations on the same underlying.

## The thesis

Across expirations, at-the-money implied vol should form a smooth curve in
`sqrt(time)` — variance accumulates linearly in time, so vol is near-linear in
its square root. When one expiration sits materially above the curve implied by
its immediate neighbours, the market is paying up for that specific window.
Sometimes there is a dated catalyst that justifies it. Sometimes there isn't.

Kink finds those points, asks a model whether the richness is explained by a
known catalyst, and — if not — sells the rich expiration against a longer-dated
one as a defined-risk calendar. Max loss is the net debit, always.

This is deliberately **direction-agnostic**. Over a three-day competition window
nobody has a directional edge, and a strategy whose P&L is a coin flip on SPY is
a strategy whose P&L proves nothing.

## Honest statement about results

Three trading days is not enough to distinguish skill from luck in any options
strategy. Whatever P&L this account shows at the deadline is a single draw from
a wide distribution. The evidence offered here is the reproducibility of the
decision process — every candidate, every refusal, and every order lives in
`journal/` — not the terminal equity number.

## Architecture

    chain snapshot (IV + greeks, free indicative feed)
             |
    build_term_structure   -> one ATM point per expiration
             |
    find_kinks             -> sqrt-time interpolation vs neighbours   [pure, tested]
             |
    apply_cross_section    -> strip the market-wide component         [pure, tested]
             |
    gates.evaluate         -> deterministic veto + position sizing    [pure, tested]
             |
    execute.submit         -> mleg calendar, journalled

## The cross-section is the whole idea

The first live scan found 77 raw kinks and they were worthless, because the big
ones were not mispricings at all. The 10-day and 17-day expirations were rich
in *every* name simultaneously -- payrolls and the FOMC, priced into the
expiration that contains them. Selling those is selling event premium in front
of a scheduled catalyst.

So the cohort median at each **exact expiration** is subtracted before anything
is judged. Listed options share an expiration calendar across underlyings, so
the shared component is measurable directly. What remains is the part specific
to one name.

    77 raw kinks -> 2 idiosyncratic (threshold 3.0%)
      TRADE IWM:  17d raw +10.7%, cohort +4.4%, idio +6.3%
      TRADE MSFT: 80d raw  +9.3%, cohort +3.6%, idio +5.7%
        --  QQQ:  17d raw  +6.3%, cohort +4.4%, idio +1.9%   <- macro, refused
        --  AMD:  10d raw  +7.8%, cohort +5.2%, idio +2.6%   <- macro, refused

The cohort adjustment can only ever *remove* richness, never add it: a universe
that is cheap at some expiration is not evidence that any one name is rich.
`test_cohort_never_manufactures_a_signal` pins that.

`termstructure.py` and `gates.py` are pure functions: no network, no model, no
clock. They are unit-tested against fixtures, so the two claims that matter —
"a normal curve never triggers a trade" and "risk limits cannot be exceeded" —
are checkable without an account.

The model's only job is the one thing code cannot do: judge whether an IV
inversion has a *narrative* explanation. It cannot size a position, cannot widen
a limit, and cannot place an order.

## Setup

```bash
python -m pip install -r requirements.txt
cp .env.example .env   # fill in a FRESH paper account's keys
python -m pytest tests -q
```

## Usage

```bash
python -m kink status          # account, options level, open positions
python -m kink scan            # print term structures and any kinks found
python -m kink trade           # dry run: shows the order it would place
python -m kink trade --live    # actually submit (paper account only)
```

`config.assert_paper()` refuses to start against any non-paper endpoint.
