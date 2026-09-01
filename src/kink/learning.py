"""Learn from every action, including the ones that were never traded.

A three-day competition produces a handful of fills. Tuning parameters on a
handful of fills is not learning, it is fitting noise, and an agent that claims
otherwise is claiming something it cannot support.

But trades are not the only evidence available. Every kink the scanner scores is
a falsifiable prediction -- *this expiration is richer than it should be, and
that gap should close* -- and the market answers it a day later whether or not
we took the trade. Twenty names times several expirations times a few sessions
is hundreds of scored predictions, and the refused ones are as informative as
the taken ones. That is the sample this module learns from.

Two things are measured:

  **Calibration.** Bucket observations by the edge at the time, then look at how
  much of that edge actually decayed. If the thesis holds, bigger edges should
  decay more. If bigger edges decay *less*, the signal is backwards and the
  honest response is to stop trading it.

  **Realised outcomes.** For positions that were actually opened, what the exit
  reason was and what it earned.

Nothing here adjusts a parameter on its own. It reports what the evidence
supports, and `suggest_threshold` refuses to suggest anything until there are
enough observations for the suggestion to mean something.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
from dataclasses import dataclass, field

from .journal import JOURNAL_DIR
from .termstructure import Kink

OBSERVATIONS = JOURNAL_DIR / "observations.jsonl"
OUTCOMES = JOURNAL_DIR / "outcomes.jsonl"

# Below this, any "learning" is noise dressed up as a finding.
MIN_OBSERVATIONS_TO_SUGGEST = 60
# A prediction needs time to be answered; anything sooner is the same quote twice.
MIN_HOURS_TO_SCORE = 18


@dataclass
class Observation:
    ts: str
    underlying: str
    expiration: str
    dte: int
    atm_iv: float
    expected_iv: float
    raw_score: float
    cohort_score: float
    idio_score: float
    vol_points: float
    traded: bool = False

    @property
    def key(self) -> str:
        return f"{self.underlying}|{self.expiration}"


def record_observations(kinks: list[Kink], traded: set[str] | None = None) -> int:
    """Append every scored kink, traded or not. Refusals are data too."""
    traded = traded or set()
    OBSERVATIONS.parent.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.UTC).isoformat()
    written = 0
    with OBSERVATIONS.open("a", encoding="utf-8") as fh:
        for k in kinks:
            obs = Observation(
                ts=now,
                underlying=k.underlying,
                expiration=str(k.rich.expiration),
                dte=k.rich.dte,
                atm_iv=k.rich.atm_iv,
                expected_iv=k.expected_iv,
                raw_score=k.raw_score,
                cohort_score=k.cohort_score,
                idio_score=k.score,
                vol_points=k.vol_points,
                traded=k.underlying in traded,
            )
            fh.write(json.dumps(obs.__dict__) + "\n")
            written += 1
    return written


def load_observations() -> list[Observation]:
    if not OBSERVATIONS.exists():
        return []
    out = []
    for line in OBSERVATIONS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(Observation(**json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


@dataclass
class ScoredPrediction:
    """One kink, and what happened to it afterwards."""
    underlying: str
    expiration: str
    hours: float
    edge_then: float
    edge_now: float
    traded: bool

    @property
    def decay(self) -> float:
        """Fraction of the edge that closed. 1.0 means it fully converged."""
        if self.edge_then <= 0:
            return 0.0
        return (self.edge_then - self.edge_now) / self.edge_then


def score_predictions(
    observations: list[Observation] | None = None,
    *,
    min_hours: float = MIN_HOURS_TO_SCORE,
) -> list[ScoredPrediction]:
    """Pair each observation with the next one far enough ahead to be an answer."""
    obs = observations if observations is not None else load_observations()
    by_key: dict[str, list[Observation]] = {}
    for o in obs:
        by_key.setdefault(o.key, []).append(o)

    scored: list[ScoredPrediction] = []
    for key, series in by_key.items():
        series.sort(key=lambda o: o.ts)
        for i, earlier in enumerate(series):
            for later in series[i + 1:]:
                hours = _hours_between(earlier.ts, later.ts)
                if hours < min_hours:
                    continue
                scored.append(
                    ScoredPrediction(
                        underlying=earlier.underlying,
                        expiration=earlier.expiration,
                        hours=hours,
                        edge_then=earlier.idio_score,
                        edge_now=later.idio_score,
                        traded=earlier.traded,
                    )
                )
                break  # first valid answer only; later ones are not independent
    return scored


def _hours_between(a: str, b: str) -> float:
    try:
        return (
            dt.datetime.fromisoformat(b) - dt.datetime.fromisoformat(a)
        ).total_seconds() / 3600.0
    except ValueError:
        return 0.0


@dataclass
class Bucket:
    label: str
    lo: float
    hi: float
    predictions: list[ScoredPrediction] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.predictions)

    @property
    def mean_decay(self) -> float:
        return (
            sum(p.decay for p in self.predictions) / self.n if self.n else 0.0
        )

    @property
    def hit_rate(self) -> float:
        """Share of predictions where the edge closed at all."""
        if not self.n:
            return 0.0
        return sum(1 for p in self.predictions if p.decay > 0) / self.n


BUCKET_EDGES = ((0.0, 0.03), (0.03, 0.06), (0.06, 0.10), (0.10, 1.0))


def calibration(scored: list[ScoredPrediction] | None = None) -> list[Bucket]:
    """Does a bigger edge actually predict more convergence?"""
    preds = scored if scored is not None else score_predictions()
    buckets = [
        Bucket(label=f"{lo:.0%}-{hi:.0%}" if hi < 1 else f"{lo:.0%}+", lo=lo, hi=hi)
        for lo, hi in BUCKET_EDGES
    ]
    for p in preds:
        for b in buckets:
            if b.lo <= p.edge_then < b.hi:
                b.predictions.append(p)
                break
    return buckets


def signal_is_monotonic(buckets: list[Bucket]) -> bool | None:
    """True if decay rises with edge, False if it falls, None if too thin to say."""
    populated = [b for b in buckets if b.n >= 5]
    if len(populated) < 2:
        return None
    decays = [b.mean_decay for b in populated]
    return decays[-1] > decays[0]


def suggest_threshold(buckets: list[Bucket] | None = None) -> tuple[float | None, str]:
    """Recommend a minimum edge, or explain why no recommendation is warranted.

    Deliberately conservative. The failure mode this guards against is a system
    that reports a confident number derived from nine observations.
    """
    buckets = buckets if buckets is not None else calibration()
    total = sum(b.n for b in buckets)

    if total < MIN_OBSERVATIONS_TO_SUGGEST:
        return None, (
            f"{total} scored predictions; need {MIN_OBSERVATIONS_TO_SUGGEST} "
            "before a threshold suggestion means anything"
        )

    monotonic = signal_is_monotonic(buckets)
    if monotonic is None:
        return None, "not enough populated buckets to compare"
    if monotonic is False:
        return None, (
            "larger edges did NOT decay more than smaller ones -- the signal is "
            "not behaving as the thesis predicts; do not widen exposure"
        )

    # The lowest bucket that both converges more often than not and has support.
    for b in buckets:
        if b.n >= 10 and b.hit_rate > 0.5 and b.mean_decay > 0:
            return b.lo, (
                f"lowest bucket with support: {b.label}, n={b.n}, "
                f"hit rate {b.hit_rate:.0%}, mean decay {b.mean_decay:+.0%}"
            )
    return None, "no bucket cleared the support and hit-rate bar"


def record_outcome(
    *,
    underlying: str,
    entry_edge: float,
    exit_edge: float,
    entry_debit: float,
    exit_debit: float,
    qty: int,
    reason: str,
    opened_at: str,
) -> None:
    OUTCOMES.parent.mkdir(parents=True, exist_ok=True)
    with OUTCOMES.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": dt.datetime.now(dt.UTC).isoformat(),
            "underlying": underlying,
            "entry_edge": entry_edge,
            "exit_edge": exit_edge,
            "entry_debit": entry_debit,
            "exit_debit": exit_debit,
            "qty": qty,
            "pnl": (exit_debit - entry_debit) * 100 * qty,
            "reason": reason,
            "opened_at": opened_at,
            "held_hours": _hours_between(opened_at, dt.datetime.now(dt.UTC).isoformat()),
        }) + "\n")


def load_outcomes() -> list[dict]:
    if not OUTCOMES.exists():
        return []
    out = []
    for line in OUTCOMES.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def report() -> str:
    """A plain-text summary, printed by `kink learn`."""
    obs = load_observations()
    scored = score_predictions(obs)
    buckets = calibration(scored)
    outcomes = load_outcomes()

    lines = [
        "OBSERVATIONS",
        f"  recorded         {len(obs)}",
        f"  scored           {len(scored)}  (paired >={MIN_HOURS_TO_SCORE}h apart)",
        "",
        "CALIBRATION -- does a bigger edge decay more?",
        f"  {'edge at entry':<16}{'n':>5}{'hit rate':>11}{'mean decay':>13}",
    ]
    for b in buckets:
        if b.n:
            lines.append(
                f"  {b.label:<16}{b.n:>5}{b.hit_rate:>10.0%}{b.mean_decay:>13.0%}"
            )
        else:
            lines.append(f"  {b.label:<16}{'-':>5}{'-':>11}{'-':>13}")

    mono = signal_is_monotonic(buckets)
    lines += [
        "",
        f"  signal monotonic: "
        + {True: "yes -- bigger edges converge more",
           False: "NO -- bigger edges converge LESS; thesis not supported",
           None: "undetermined (too few populated buckets)"}[mono],
    ]

    threshold, why = suggest_threshold(buckets)
    lines += [
        "",
        "THRESHOLD",
        f"  suggestion       {threshold if threshold is not None else 'none'}",
        f"  reason           {why}",
    ]

    if outcomes:
        total = sum(o["pnl"] for o in outcomes)
        wins = sum(1 for o in outcomes if o["pnl"] > 0)
        lines += [
            "",
            "REALISED TRADES",
            f"  closed           {len(outcomes)}",
            f"  won              {wins}",
            f"  net P&L          ${total:,.2f}",
        ]
        for o in outcomes[-8:]:
            lines.append(
                f"    {o['underlying']:<6} ${o['pnl']:>9,.2f}  "
                f"{o['held_hours']:.0f}h  {o['reason'][:52]}"
            )
    else:
        lines += ["", "REALISED TRADES", "  none closed yet"]

    return "\n".join(lines)
