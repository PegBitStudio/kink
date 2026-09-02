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

# Decay is a ratio, and a ratio with a near-zero denominator is not a
# measurement. An edge of 0.05% that moves to -0.1% scores as 300% "decay";
# one such prediction in this sample scored 56,770%. Those cases are also
# irrelevant -- the agent will not trade below a 3% edge -- so they are
# excluded rather than winsorised. Their only effect was to make small edges
# look like the best converging bucket and invert the whole conclusion.
MIN_EDGE_TO_SCORE = 0.01

# The version of the scoring definition an observation was recorded under.
# Bump this whenever the meaning of idio_score changes, because a pair whose
# two ends were measured differently reports the code change as market
# movement. That is not hypothetical: the first calibration table compared
# pre- and post- the monthly-expiration fix and concluded the thesis had
# failed, when what had actually moved was the ruler.
#   1 = neighbour-of-any-type scoring (pre 2026-09-02)
#   2 = same-expiration-type scoring, z-scored against per-name history
SCORING_VERSION = 2

# Observations written before versioning existed carry no version of their own,
# so the boundary is reconstructed from when the change actually shipped --
# commit d485e78, 2026-09-01T14:30Z, which switched scoring to same-expiration-
# type comparison. Applied at read time rather than by rewriting the log: an
# append-only journal should not be edited, and the correction belongs in code
# where it can be audited.
SCORING_BOUNDARIES = ((dt.datetime(2026, 9, 1, 14, 30, tzinfo=dt.UTC), 2),)


def _version_of(o: "Observation") -> int:
    """Reconstruct which scoring rule produced a reading."""
    if o.version:
        return o.version      # explicitly recorded; never second-guess it
    try:
        ts = dt.datetime.fromisoformat(o.ts)
    except ValueError:
        return 1
    version = 1
    for boundary, v in SCORING_BOUNDARIES:
        if ts >= boundary:
            version = v
    return version


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
    version: int = 0          # 0 = unrecorded; reconstructed from the timestamp

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
                version=SCORING_VERSION,
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
                if earlier.idio_score < MIN_EDGE_TO_SCORE:
                    break     # denominator too small for the ratio to mean anything
                if _version_of(earlier) != _version_of(later):
                    # Measured with different rulers; the difference between
                    # them is our code, not the market.
                    break
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
    def median_decay(self) -> float:
        """The headline number. A mean over ratios is at the mercy of its tail."""
        if not self.n:
            return 0.0
        vals = sorted(p.decay for p in self.predictions)
        mid = self.n // 2
        return vals[mid] if self.n % 2 else (vals[mid - 1] + vals[mid]) / 2.0

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
    decays = [b.median_decay for b in populated]
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

    versions = {}
    for o in obs:
        v = _version_of(o)
        versions[v] = versions.get(v, 0) + 1
    lines = [
        "OBSERVATIONS",
        f"  recorded         {len(obs)}",
        f"  by scoring rule  " + ", ".join(f"v{v}:{n}" for v, n in sorted(versions.items())),
        f"  scored           {len(scored)}  (paired >={MIN_HOURS_TO_SCORE}h apart, "
        f"same scoring rule)",
        "",
        "CALIBRATION -- does a bigger edge decay more?",
        f"  {'edge at entry':<16}{'n':>5}{'hit rate':>11}{'median':>10}{'mean':>10}",
    ]
    for b in buckets:
        if b.n:
            lines.append(
                f"  {b.label:<16}{b.n:>5}{b.hit_rate:>10.0%}"
                f"{b.median_decay:>10.0%}{b.mean_decay:>10.0%}"
            )
        else:
            lines.append(f"  {b.label:<16}{'-':>5}{'-':>11}{'-':>10}{'-':>10}")

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
            "CLOSED TRADES (marked at mid -- see the P&L section for cash)",
            f"  closed           {len(outcomes)}",
            f"  won              {wins}",
            f"  mark-based P&L   ${total:,.2f}  <- an estimate, not the account",
        ]
        for o in outcomes[-8:]:
            lines.append(
                f"    {o['underlying']:<6} ${o['pnl']:>9,.2f}  "
                f"{o['held_hours']:.0f}h  {o['reason'][:52]}"
            )
    else:
        lines += ["", "REALISED TRADES", "  none closed yet"]

    return "\n".join(lines) + "\n" + fill_report()


# --- execution learning -----------------------------------------------------
#
# Price prediction is only half of what this agent does. The other half is
# getting filled, and on the first live session it went 0 for 3: every order
# was priced at the mid plus 5% and the market moved before the limit was
# reached. A no-fill is not a null result -- it is evidence about how
# aggressive the entry has to be, and it is available on every attempt whether
# or not a position ever opens.

ATTEMPTS = JOURNAL_DIR / "attempts.jsonl"

# Fill rate is only meaningful once a bucket has some attempts behind it.
MIN_ATTEMPTS_TO_SUGGEST = 12


def record_entry_attempt(
    *,
    client_order_id: str,
    underlying: str,
    limit: float,
    mid_debit: float,
    crossing_debit: float | None,
) -> None:
    """Log an entry the moment it is submitted, before its fate is known."""
    ATTEMPTS.parent.mkdir(parents=True, exist_ok=True)
    aggressiveness = (limit / mid_debit - 1.0) if mid_debit > 0 else 0.0
    with ATTEMPTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": dt.datetime.now(dt.UTC).isoformat(),
            "client_order_id": client_order_id,
            "underlying": underlying,
            "limit": limit,
            "mid_debit": mid_debit,
            "crossing_debit": crossing_debit,
            "aggressiveness": round(aggressiveness, 4),
            "outcome": "pending",
        }) + "\n")


def resolve_entry_attempt(client_order_id: str, outcome: str) -> None:
    """Record whether an attempt filled. Appends -- the journal stays immutable."""
    if not client_order_id:
        return
    ATTEMPTS.parent.mkdir(parents=True, exist_ok=True)
    with ATTEMPTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": dt.datetime.now(dt.UTC).isoformat(),
            "client_order_id": client_order_id,
            "outcome": outcome,
        }) + "\n")


def load_attempts() -> list[dict]:
    """Fold the append-only log into one final record per attempt."""
    if not ATTEMPTS.exists():
        return []
    merged: dict[str, dict] = {}
    for line in ATTEMPTS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        coid = row.get("client_order_id")
        if not coid:
            continue
        if coid in merged:
            merged[coid].update({k: v for k, v in row.items() if v is not None})
        else:
            merged[coid] = row
    return list(merged.values())


AGGRESSION_BUCKETS = ((0.0, 0.05), (0.05, 0.12), (0.12, 0.20), (0.20, 10.0))


def fill_calibration(attempts: list[dict] | None = None) -> list[dict]:
    """Fill rate by how far above the mid we bid."""
    rows = attempts if attempts is not None else load_attempts()
    out = []
    for lo, hi in AGGRESSION_BUCKETS:
        group = [
            a for a in rows
            if a.get("outcome") in ("filled", "unfilled")
            and lo <= float(a.get("aggressiveness") or 0) < hi
        ]
        filled = sum(1 for a in group if a["outcome"] == "filled")
        out.append({
            "label": f"mid+{lo:.0%} to +{hi:.0%}" if hi < 10 else f"mid+{lo:.0%} and up",
            "lo": lo,
            "n": len(group),
            "filled": filled,
            "fill_rate": (filled / len(group)) if group else 0.0,
        })
    return out


def suggest_slippage(buckets: list[dict] | None = None) -> tuple[float | None, str]:
    """Recommend an entry allowance, or say why the evidence cannot support one.

    Deliberately biased toward the *least* aggressive bucket that actually
    fills. Paying more than necessary is a permanent cost on every trade; the
    point is to find the cheapest price that transacts, not the surest one.
    """
    buckets = buckets if buckets is not None else fill_calibration()
    total = sum(b["n"] for b in buckets)
    if total < MIN_ATTEMPTS_TO_SUGGEST:
        return None, (
            f"{total} resolved entry attempts; need {MIN_ATTEMPTS_TO_SUGGEST} "
            "before a slippage suggestion means anything"
        )
    for b in buckets:
        if b["n"] >= 5 and b["fill_rate"] >= 0.5:
            return b["lo"], (
                f"cheapest bucket that fills: {b['label']}, "
                f"{b['filled']}/{b['n']} filled ({b['fill_rate']:.0%})"
            )
    return None, "no bucket reached a 50% fill rate; entries may need to cross"


def fill_report() -> str:
    attempts = load_attempts()
    buckets = fill_calibration(attempts)
    resolved = [a for a in attempts if a.get("outcome") in ("filled", "unfilled")]
    pending = [a for a in attempts if a.get("outcome") == "pending"]

    lines = [
        "",
        "EXECUTION -- how aggressive does the entry have to be?",
        f"  attempts         {len(attempts)}  ({len(resolved)} resolved, "
        f"{len(pending)} still working)",
        f"  {'bid above mid':<20}{'n':>5}{'filled':>8}{'fill rate':>12}",
    ]
    for b in buckets:
        if b["n"]:
            lines.append(
                f"  {b['label']:<20}{b['n']:>5}{b['filled']:>8}{b['fill_rate']:>11.0%}"
            )
        else:
            lines.append(f"  {b['label']:<20}{'-':>5}{'-':>8}{'-':>12}")

    slip, why = suggest_slippage(buckets)
    lines += [
        "",
        f"  suggested allowance  {slip if slip is not None else 'none'}",
        f"  reason               {why}",
    ]
    return "\n".join(lines)
