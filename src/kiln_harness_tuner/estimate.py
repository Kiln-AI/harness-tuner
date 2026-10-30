"""The measurement engine's estimator: an unbiased estimate of a
candidate's quality change from a queue that is NOT a random sample.

The engine judges items hard-first (`kiln_harness_tuner.measure`): the parent's
failures and a few of its passes first, then the rest. That order is for
early detection. A plain mean over the judged items would be biased — it
over-weights the parent's failures, where a candidate can only fix, and
under-weights its passes, where it can only break.

So every item of an eval belongs to one STRATUM by the parent's own stored
result: `fail` (the parent fails it), `pass` (the parent passes it),
`unscored` (the parent has no row), and `named` (an item the planner named;
all are judged first, so it is a census). The population of each stratum is
known exactly from the parent's rows. Within a stratum the queue takes items
in a seeded random order, so at any point the judged items of a stratum are
a simple random sample of it. The estimate of the change in pass rate is

    net_e = sum over strata s of  W_s * mean_s(d)      W_s = N_s / N_known

where d = candidate − parent per item (+1 fixed, −1 broke, 0 same) and
N_known counts the items whose parent result is known. Its variance is

    var_e = sum over s of  W_s^2 * v_s / n_s * (1 − n_s / N_s)

with the finite-population correction, and v_s a Bernoulli variance
smoothed toward the middle so that a stratum whose few items all agree does
not claim zero spread: p = (k + 2) / (n + 4), the Agresti–Coull "two
successes and two failures". Simulated (`tests/test_measure.py`,
`sim_estimator`: 60 items, a stop after 24 of the hard-first order), the
95% interval covers the truth 98% of the time with it, 93% with
(k + 1) / (n + 2), and 87% with (k + 1/2) / (n + 1): a rare break in the
pass stratum that no judged item shows yet is the case the smoothing is
for. A stratum with items
but none judged yet contributes a mean of 0 with the largest Bernoulli
variance (1/4): the estimate then says it is incomplete, and a test on it is
conservative. Pooled over evals, each eval weighs its N_known. When every
item has been judged the estimate is exact: the plain full-split result.

A candidate row that is missing (the drive or judge failed) counts as a
fail — never as a pass.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

FAIL, PASS, UNSCORED, NAMED = "fail", "pass", "unscored", "named"
KNOWN_STRATA = (FAIL, PASS, NAMED)


@dataclass(frozen=True)
class Judged:
    """One judged item of one candidate."""

    eval_id: str
    item_id: str
    stratum: str
    block: str  # "named" | "discriminating" | "remainder"
    parent_pass: bool | None
    cand_pass: bool | None  # None: no row stored
    # The share of other run configs' stored train rows on the
    # item that fail (`measure.fragility`); read by `guard_losses` only.
    fragility: float = 0.0

    @property
    def d(self) -> int | None:
        if self.parent_pass is None:
            return None
        return int(bool(self.cand_pass)) - int(self.parent_pass)


@dataclass(frozen=True)
class Estimate:
    net: float  # estimated change in pass rate against the parent
    se: float
    rate: float | None  # estimated candidate pass rate
    n: int  # judged items with a known parent result
    complete: bool  # every stratum with items has judged items

    def ci(self, z: float = 1.96) -> tuple[float, float]:
        return self.net - z * self.se, self.net + z * self.se


def _smoothed_var(k: int, n: int) -> float:
    p = (k + 2) / (n + 4)
    return p * (1 - p)


def _stratum(
    values: list[int], n_pop: int
) -> tuple[float, float, bool]:  # mean, var of the mean, sampled
    n = len(values)
    if n_pop <= 0:
        return 0.0, 0.0, True
    if n == 0:
        return 0.0, 0.25, False
    mean = sum(values) / n
    k = sum(1 for v in values if v != 0)
    spread = max(_sample_var(values), _smoothed_var(k, n))
    fpc = max(0.0, 1 - n / n_pop)
    return mean, spread / n * fpc, True


def _sample_var(values: list[int]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    m = sum(values) / n
    return sum((v - m) ** 2 for v in values) / (n - 1)


def eval_estimate(
    judged: Iterable[Judged], population: Mapping[str, int]
) -> Estimate | None:
    """One eval's estimate; None when the eval has no item with a known
    parent result."""
    rows = list(judged)
    known_pop = {s: int(population.get(s, 0)) for s in KNOWN_STRATA}
    n_known = sum(known_pop.values())
    if n_known == 0:
        return None
    net = var = 0.0
    complete = True
    for s in KNOWN_STRATA:
        values = [r.d for r in rows if r.stratum == s and r.d is not None]
        mean, v, sampled = _stratum(values, known_pop[s])  # type: ignore[arg-type]
        w = known_pop[s] / n_known
        net += w * mean
        var += w * w * v
        complete = complete and sampled
    # The candidate's pass rate, over every stratum (unscored included).
    all_pop = {**known_pop, UNSCORED: int(population.get(UNSCORED, 0))}
    total = sum(all_pop.values())
    rate = 0.0
    for s, n_pop in all_pop.items():
        values = [int(bool(r.cand_pass)) for r in rows if r.stratum == s]
        if n_pop and values:
            rate += n_pop / total * sum(values) / len(values)
        elif n_pop:
            rate = math.nan
    return Estimate(
        net=net,
        se=math.sqrt(var),
        rate=None if math.isnan(rate) else rate,
        n=sum(1 for r in rows if r.d is not None),
        complete=complete,
    )


def estimate(
    judged: Iterable[Judged],
    population: Mapping[str, Mapping[str, int]],
    eval_id: str | None = None,
) -> Estimate | None:
    """The estimate on `eval_id`, or pooled over every eval (each weighing
    its items with a known parent result)."""
    rows = list(judged)
    evals = [eval_id] if eval_id is not None else sorted(population)
    parts: list[tuple[int, Estimate]] = []
    for e in evals:
        est = eval_estimate((r for r in rows if r.eval_id == e), population.get(e, {}))
        if est is not None:
            parts.append((sum(int(population[e].get(s, 0)) for s in KNOWN_STRATA), est))
    if not parts:
        return None
    total = sum(w for w, _e in parts)
    net = sum(w / total * e.net for w, e in parts)
    se = math.sqrt(sum((w / total) ** 2 * e.se**2 for w, e in parts))
    rates = [(w, e.rate) for w, e in parts]
    rate = (
        None
        if any(r is None for _w, r in rates)
        else sum(w / total * r for w, r in rates)  # type: ignore[operator]
    )
    return Estimate(
        net=net,
        se=se,
        rate=rate,
        n=sum(e.n for _w, e in parts),
        complete=all(e.complete for _w, e in parts),
    )


def normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))
