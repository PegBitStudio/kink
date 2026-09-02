# Kink — results

Living record of what the system has actually done. Updated as trading proceeds.

**Account:** `e32dc9cd-0913-4e42-8b9e-1b30dd28716a` · Alpaca paper · $100,000 start
**Last updated:** 2026-09-01 14:17 UTC — *first session, live*

---

## Headline

> **Session 1: two trades, both closed, both losses. −$202.**
>
> Both were entered on a signal that turned out to be an artifact of the
> options calendar rather than a mispricing, and the fix that identified the
> artifact is what closed them. The loss is the cost of a bias we found and
> removed.

| | |
|---|---|
| Equity | $99,798.00 |
| **Realised P&L** | **−$202.00** |
| Positions opened / closed | 2 / 2 |
| Currently open | none |
| Observations recorded | 3,108 |

### How the number is computed

From the broker's own fill feed — the price and quantity of every execution —
not from quoted mids. The first version of this document would have said
−$515, because it marked positions against mids rather than counting cash. A
mid is an opinion about what something is worth; a fill is a record of what was
paid.

```
realised on fills   -$200.00
account change      -$202.00
residual            -$2.00   (0.025/contract over 80 contracts)
verdict             consistent with a per-contract fee
```

The $2 gap is Alpaca's per-contract regulatory fee, which moves cash without
appearing in the activity feed. It is reported as its own line rather than
folded into P&L: a number that quietly absorbs whatever is left over is not a
measurement.

### What actually happened

Both trades were SPY calendars on the **18 September** expiration — a third
Friday, a standard monthly. They were entered while the scanner still compared
each expiration against its immediate neighbours, which for a monthly means
comparing it against weeklies. Monthlies are structurally richer than weeklies
(measured across 1,586 observations: +0.99% against −0.03%), so the "8.0% edge"
was mostly calendar mechanics.

Type-aware scoring was deployed mid-session. Under the corrected measure the
edge was 0.0%, the exit rule read that as convergence, and both positions were
closed. The trades were bad; the fix removed them; the cost was $202.

Two things this exposes, both recorded rather than smoothed over:

1. **The exit reason was misreported.** "Thesis played out" is what the log
   says. The thesis did not play out — the measuring stick changed underneath
   an open position.
2. **A long calendar is net long vega.** It is not purely a shape trade. If the
   overall level of volatility falls, the position loses even when the kink
   converges exactly as predicted.

## How to read the P&L when it exists

This account will have traded for roughly **3.2 sessions**. That is not a sample
from which skill can be distinguished from luck in any options strategy, and no
claim to the contrary is made here.

Whatever the terminal equity shows, it is one draw from a wide distribution. The
evidence offered by this project is the **decision process** — every candidate,
every refusal and every order journalled and reproducible — not the number at
the bottom.

The measurement that *would* be meaningful with enough data is in
[Calibration](#calibration): does a larger edge actually predict more
convergence? That question is answerable from hundreds of observations rather
than a handful of trades, and it is reported honestly including when the answer
is "not yet" or "no".

---

## Scanner behaviour

From the 1 Sep pre-market scan across 21 symbols:

| Stage | Count |
|---|---|
| Raw kinks found | 159 |
| Cleared the idiosyncratic threshold (3%) | 12 |
| Cleared every gate and tradeable | 3 |

**Currently tradeable:**

| Symbol | Structure | Idio edge | Vol points | Class |
|---|---|---|---|---|
| IWM | sell 17d / buy 24d | +6.6% | +1.04 | equity / broad |
| SLV | sell 17d / buy 24d | +3.1% | +1.21 | commodity |
| UNG | sell 31d / buy 38d | +3.6% | +1.46 | commodity |

**Measured cohort (macro) components** — the share of richness every name in an
asset class showed at the same expiration, and therefore discarded:

| Asset class | Cohort at 17d |
|---|---|
| Equity | +4.4% |
| Rates | +1.3% |
| Commodity | +0.7% |

That equity number is the September macro calendar being priced in. Subtracting
it is what separates QQQ's apparent +6.3% signal (which collapses to +1.9% and
is refused) from IWM's +11.0% (which survives at +6.6%).

---

## Refusals — the system saying no

Refusals are recorded as carefully as fills, because most of what an agent does
is decline.

| Symbol | Refused because |
|---|---|
| HYG | 187% bid/ask spread on the short leg; long leg mid $0.10 below floor; not a debit calendar |
| XLV | sector ETF, no earnings-date source; 73.7% spread |
| XLK | sector ETF, no earnings-date source; 31.2% spread |
| XLF | sector ETF; edge 0.71 vol points below the 0.80 floor |
| IEF | edge 0.30 vol points below floor; not a debit calendar |
| EFA | edge 0.39 vol points below floor |
| QQQ | richness was macro, not idiosyncratic |

**HYG is the instructive one.** It ranked *first* on relative score at +23%, and
is untradeable at any price. Without the absolute vol-point floor and the
liquidity gates, it would have been the agent's largest position.

---

## Calibration

Does a bigger edge predict more convergence? This is the only question about the
strategy that a three-day window can begin to answer, because it is scored
against **every candidate observed**, not just those traded.

| | |
|---|---|
| Observations recorded | 162 (one scan) |
| Scored predictions | 0 — none is yet 18h old |
| Threshold suggestion | **none** |
| Reason | 0 scored predictions; 60 required before a suggestion means anything |

*Table populates from 2 Sep once observations are old enough to score.*

| Edge at entry | n | Hit rate | Mean decay |
|---|---|---|---|
| 0–3% | — | — | — |
| 3–6% | — | — | — |
| 6–10% | — | — | — |
| 10%+ | — | — | — |

**Reading this table:** *hit rate* is the share of predictions where the edge
closed at all; *mean decay* is the average fraction of the edge that closed. If
the thesis holds, both should rise down the table. If they fall, the signal is
backwards, and this document will say so.

---

## Realised trades

*None closed yet.*

| Symbol | Qty | Held | Exit reason | P&L |
|---|---|---|---|---|
| — | — | — | — | — |

---

## System verification

| Check | Status |
|---|---|
| Test suite | 64 passing |
| `mleg` order schema against live API | Verified — HTTP 200, order accepted and cancelled |
| Options trading level | 3 (multi-leg confirmed) |
| Alpaca CLI | v0.0.14, SHA-256 verified against published checksums |
| Paper-only guard | `assert_paper()` refuses any non-paper endpoint |
| Adjudicator fail-closed | 10 tests covering every refusal path |
| Prompt-injection resistance | Hostile "approve everything" string cannot produce a fill |

---

## Reproducing any of this

```bash
python -m pytest tests -q      # the claims that need no account
python -m kink scan            # term structures and the decomposition
python -m kink learn           # calibration and threshold evidence
python -m kink status          # account, level, positions
```

Every order the agent has placed appears in `journal/command-*.jsonl` as the
literal shell command that placed it.
