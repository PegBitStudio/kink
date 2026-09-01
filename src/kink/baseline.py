"""What is normal for this name at this kind of expiration.

The scanner measures richness against neighbours and against peers. Neither
answers the question that actually matters: *is this unusual for this name?*

Some names carry a persistent structural bump. Their monthly always runs rich,
or their 30-day tenor always sits above the curve, for reasons of liquidity and
positioning that have nothing to do with mispricing. Against neighbours they
score every single day. Against peers they score whenever their quirk is larger
than the median quirk. A system without memory cannot tell that apart from a
genuine anomaly, and will keep taking the same bad trade.

So each kink is also scored against the distribution of that same name's own
past kinks. +1.5% on a name that always runs +1.5% is nothing. +4% on a name
that normally sits at +0.5% is the trade.

The history comes from the observation log the scanner already writes, so this
costs no extra data and improves every day the agent runs.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from .learning import Observation, load_observations
from .termstructure import Kink, expiration_type

# Below this, a mean and standard deviation are not worth computing -- the
# z-score would be an opinion dressed as a statistic.
MIN_HISTORY = 20

# A floor on the dispersion estimate. Without it, a name whose history happens
# to be nearly constant produces enormous z-scores from trivial moves.
MIN_STDEV = 0.005


@dataclass(frozen=True)
class Baseline:
    key: str
    n: int
    mean: float
    stdev: float

    @property
    def usable(self) -> bool:
        return self.n >= MIN_HISTORY

    def z(self, value: float) -> float | None:
        if not self.usable:
            return None
        return (value - self.mean) / max(self.stdev, MIN_STDEV)


def baseline_key(underlying: str, exp_type: str) -> str:
    """Grouped by name and expiration type, which is where the structure lives."""
    return f"{underlying}|{exp_type}"


def build(observations: list[Observation] | None = None) -> dict[str, Baseline]:
    obs = observations if observations is not None else load_observations()
    groups: dict[str, list[float]] = {}
    for o in obs:
        try:
            import datetime as dt

            kind = expiration_type(dt.date.fromisoformat(o.expiration))
        except ValueError:
            continue
        groups.setdefault(baseline_key(o.underlying, kind), []).append(o.raw_score)

    out: dict[str, Baseline] = {}
    for key, values in groups.items():
        n = len(values)
        mean = sum(values) / n if n else 0.0
        stdev = statistics.pstdev(values) if n > 1 else 0.0
        out[key] = Baseline(key=key, n=n, mean=mean, stdev=stdev)
    return out


def annotate(kinks: list[Kink], baselines: dict[str, Baseline] | None = None) -> list[Kink]:
    """Attach a z-score where there is enough history to justify one."""
    from dataclasses import replace

    baselines = baselines if baselines is not None else build()
    out: list[Kink] = []
    for k in kinks:
        base = baselines.get(baseline_key(k.underlying, k.exp_type))
        z = base.z(k.raw_score) if base else None
        out.append(replace(k, z_score=z))
    return out


def describe(baselines: dict[str, Baseline], limit: int = 12) -> str:
    """Which names carry a persistent bump, and how big is it."""
    usable = sorted(
        (b for b in baselines.values() if b.usable),
        key=lambda b: b.mean,
        reverse=True,
    )
    lines = [
        "BASELINES -- what is normal for each name",
        f"  {'name / expiry type':<24}{'n':>5}{'mean kink':>12}{'stdev':>10}",
    ]
    if not usable:
        thin = len(baselines)
        lines.append(
            f"  no group has reached {MIN_HISTORY} observations yet "
            f"({thin} groups forming)"
        )
        return "\n".join(lines)
    for b in usable[:limit]:
        lines.append(
            f"  {b.key:<24}{b.n:>5}{b.mean:>11.2%}{b.stdev:>10.2%}"
        )
    return "\n".join(lines)
