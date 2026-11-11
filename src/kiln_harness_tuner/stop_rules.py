"""The measurement engine's stop rules.

`kiln_harness_tuner.measure` judges a candidate in chunks. After each chunk it builds
an `Evidence` from everything measured so far and checks every rule
(`StopRule`, schema in `kiln_harness_tuner.schemas`); the first rule that fires stops
the candidate, and the stop is logged with the rule and the numbers:

    stopped at n=24: all evals net −0.21 (se 0.06), p=0.0003 < 0.0167 —
    rule: eval_metric all evals vs baseline below +0.00 at alpha 0.05
    (0.0167 per look over 3 looks) once n >= 12

There are stop rules only. Nothing promotes a candidate early.

**Repeated looks.** A test checked at k looks gives up to about k × alpha
false stops, so each test is run at alpha / looks — a Bonferroni split over a
FIXED, pre-set look schedule. By the union bound the chance that a candidate
that is not worse is stopped at ANY look is at most alpha, whatever the
correlation between looks and however discrete the statistic. The quality
test looks come on a geometric schedule — when the judged items first reach
12, 24, 48, 96, … — so a 240-item measurement has 5 looks (alpha 0.01 each),
not one per chunk. Alpha spending (O'Brien–Fleming, Pocock) would give more
power at the last look, but its boundaries assume a normal statistic with a
known information fraction per look; here the early looks hold a dozen
binary items, a failed drive changes the information of a chunk, and the
last look is a full census anyway, where the train rule decides and no test
is needed. The timing tests look after each timing chunk (15 and 30 pairs).

**The deterministic rules** (`futility`, `targets`, `not_better`, the
budgets, `invalid_output_rate`, and the fix half of `residual`) are not
tests: they can never stop a candidate that could still meet its bar, so
they take no alpha.

**The guard half of `residual`** is a test. The rule reads the
discriminating block when it is judged: up to 8 of the parent's failures
and up to 4 of its passes (the guards). One broken guard does not stop the
arm, because a config equal to the parent also breaks guards by
noise: at a flip rate of 0.17 it breaks at least one of 4 about half the
time. So the guard half fires only when the breaks are clearly above
the noise flip rate: a one-sided exact binomial test of k breaks in the n
guards with a row, against p0, at alpha. p0 is the one of `guard_losses`:
the run's `--noise-check` rate (never below `GUARD_NOISE_FLOOR`), else
`GUARD_NOISE_DEFAULT`. The null is the mean over the guards of max(p0, the
item's fragility); under the quality objective the queue records no
fragility, so the null is p0. The block is judged in the first chunk and
does not change after it, so the test has one look and runs at the full
alpha. At the defaults (p0 0.10, alpha 0.05): 1 of 2, 1 of 3, and 1 or 2
of 4 do not fire; 2 of 2, 2 of 3 and 3 of 4 do. A real regression is still
caught by `eval_metric` over the rest of the split, and by the val gate.

**Quality under speed and cost** (`guard_losses`). Under an
efficiency objective the question is whether the candidate KEEPS what the
parent passes, not whether it fixes anything. So the default quality rule
there counts LOSSES on the judged items the parent passes (the guards), and
nothing else: a one-sided exact binomial test that the loss rate is above
the noise flip rate p0, at the quality looks. A fix of a parent failure
never offsets a loss. An item that failed once is likely to pass on a
re-run, so a fix is noise-level evidence, and it is reported on its own
line. p0 is the run's `--noise-check` re-drive rate (never below
`GUARD_NOISE_FLOOR`), else `GUARD_NOISE_DEFAULT`. The queue puts first the
guards that other run configs often fail (`kiln_harness_tuner.measure`), and such an
item also flips more often by noise. So the null of the test is the mean,
over the judged guards, of max(p0, the item's fragility). A binomial test
at the mean of unequal probabilities is conservative (Hoeffding, 1956).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from kiln_harness_tuner.estimate import (
    FAIL,
    NAMED,
    UNSCORED,
    Judged,
    estimate,
    normal_cdf,
)
from kiln_harness_tuner.schemas import LatencyComparison, StopRule

DEFAULT_ALPHA = 0.05
# The quality looks: the first when this many items are judged, then each
# time the count doubles.
QUALITY_FIRST_LOOK = 12
# The quality test's floor: no fewer judged items with a known parent result.
QUALITY_MIN_N = 6
# The kinds read off the timing drives alone: checked right after a chunk's
# drives, before its items are judged, so a candidate that fails one is
# never paid a judge call for that chunk.
TIMING_KINDS = (
    "latency_ratio",
    "cost_ratio",
    "invalid_output_rate",
    "not_better",
)
# No timing test fires before this many pairs. Two identical
# configs can read 0.75× and 1.18× on 3 items; a paired sign
# test cannot be significant on fewer than 6 (5 of 5 gives p = 0.031 one-
# sided, but at alpha / 2 looks = 0.025 it cannot fire before 6 of 6).
TIMING_MIN_PAIRS = 6
RESIDUAL_MIN_FIXED_SHARE = 0.5
# `guard_losses`: the noise flip rate of the loss test when the
# run has no `--noise-check`. On one multi-turn task, about 1 item in 12
# changed between two runs of the same config (0-17% per eval). 0.10 is
# above that, so an equal candidate on a task that noisy is stopped at most
# alpha of the time.
GUARD_NOISE_DEFAULT = 0.10
# A noise check that saw no change on its sample does not show a rate of
# zero: p0 is never below this.
GUARD_NOISE_FLOOR = 0.02
# The confidence of the loss-rate bound on the screen line and record.
LOSS_BOUND_CONFIDENCE = 0.95


def quality_looks(total_items: int) -> list[int]:
    """The judged-item counts at which the quality test looks."""
    looks = [QUALITY_FIRST_LOOK]
    while looks[-1] * 2 <= total_items:
        looks.append(looks[-1] * 2)
    return [n for n in looks if n <= max(total_items, QUALITY_FIRST_LOOK)]


def noise_flip_rate(
    rows: Iterable[Any], eval_ids: Iterable[str] | None = None
) -> float | None:
    """The pooled re-drive change rate of the `--noise-check` rows
    (`schemas.NoiseRow`) of pass/fail scores on `eval_ids` (every eval when
    None). None when no such row compared an item."""
    from kiln_harness_tuner.analysis import BINARY_SCORE_TYPES

    wanted = None if eval_ids is None else set(eval_ids)
    changed = items = 0
    for row in rows:
        if getattr(row, "score_type", None) not in BINARY_SCORE_TYPES:
            continue
        if wanted is not None and row.eval_id not in wanted:
            continue
        changed += row.redrive_changed
        items += row.items
    return changed / items if items else None


def guard_noise_rate(measured: float | None) -> float:
    """p0 of the `guard_losses` test: the run's measured noise flip rate,
    never below `GUARD_NOISE_FLOOR`, or `GUARD_NOISE_DEFAULT` when the run
    measured none."""
    if measured is None:
        return GUARD_NOISE_DEFAULT
    return min(max(measured, GUARD_NOISE_FLOOR), 0.99)


def default_stop_rules(
    objective: str,
    *,
    full: bool = False,
    residual: bool = False,
    targets: bool = False,
    noise_rate: float | None = None,
) -> list[StopRule]:
    """Code's rules, per objective. `full`: the queue covers the whole
    targeted split (an arm), so the futility bound applies. `residual`,
    `targets`: the arm's discriminating block, or its named items, are a
    gate. `noise_rate`: the run's measured noise flip rate (`--noise-check`),
    the p0 of the `guard_losses` rule and of the guard half of `residual`.

    Under speed and cost the quality rule is `guard_losses`
    (losses on the parent's passes against the noise rate) in place of
    `eval_metric` (the net change), and there is no `residual` rule: both of
    those fold fixes of the parent's failures against losses on its
    passes."""
    efficiency = objective in ("speed", "cost")
    quality = (
        StopRule(
            kind="guard_losses",
            threshold=guard_noise_rate(noise_rate),
            alpha=DEFAULT_ALPHA,
            min_n=QUALITY_MIN_N,
        )
        if efficiency
        else StopRule(
            kind="eval_metric",
            threshold=0.0,
            vs_baseline=True,
            alpha=DEFAULT_ALPHA,
            min_n=QUALITY_MIN_N,
        )
    )
    rules = [StopRule(kind="invalid_output_rate", threshold=0.0, min_n=1), quality]
    if targets:
        rules.append(StopRule(kind="targets"))
    elif residual and not efficiency:
        rules.append(
            StopRule(
                kind="residual",
                threshold=RESIDUAL_MIN_FIXED_SHARE,
                alpha=DEFAULT_ALPHA,
                noise_rate=guard_noise_rate(noise_rate),
            )
        )
    if full:
        rules.append(StopRule(kind="futility"))
    if objective == "speed":
        rules.append(
            StopRule(
                kind="latency_ratio",
                threshold=1.0,
                min_n=TIMING_MIN_PAIRS,
                alpha=DEFAULT_ALPHA,
            )
        )
    if objective == "cost":
        rules.append(
            StopRule(
                kind="cost_ratio",
                threshold=1.0,
                min_n=TIMING_MIN_PAIRS,
                alpha=DEFAULT_ALPHA,
            )
        )
    if objective in ("speed", "cost"):
        rules.append(StopRule(kind="not_better"))
    return rules


def merge_rules(
    defaults: list[StopRule], overrides: Sequence[StopRule]
) -> list[StopRule]:
    """The planner's per-candidate rules over code's defaults: a rule of the
    same kind (and eval) replaces the default; any other is added. A
    `residual` rule without `alpha` or `noise_rate` takes the default's, so
    that its guards are tested against the run's noise rate."""
    keys = {(r.kind, r.eval_id) for r in overrides}
    default_of = {(r.kind, r.eval_id): r for r in defaults}
    merged = []
    for rule in overrides:
        base = default_of.get((rule.kind, rule.eval_id))
        if rule.kind == "residual" and base is not None:
            rule = rule.model_copy(
                update={
                    "alpha": base.alpha if rule.alpha is None else rule.alpha,
                    "noise_rate": (
                        base.noise_rate if rule.noise_rate is None else rule.noise_rate
                    ),
                }
            )
        merged.append(rule)
    return [r for r in defaults if (r.kind, r.eval_id) not in keys] + merged


# ------------------------------------------------------------ exact tests


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return min(
        1.0, sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))
    )


def below_rate_p(passed: int, n: int, rate: float) -> float:
    """One-sided exact p that the true pass rate is below `rate`."""
    return binom_cdf(passed, n, rate)


def above_rate_p(k: int, n: int, rate: float) -> float:
    """One-sided exact p that the true rate is above `rate`, with `k`
    events in `n` trials: P(X >= k) for X ~ Binomial(n, rate)."""
    return 1.0 - binom_cdf(k - 1, n, rate)


def rate_upper_bound(
    k: int, n: int, confidence: float = LOSS_BOUND_CONFIDENCE
) -> float:
    """The one-sided exact (Clopper-Pearson) upper bound on a rate with `k`
    events in `n` trials: the rate under which `k` or fewer events have
    probability 1 - `confidence`. 0 of 24 gives 0.117, 0 of 40 gives 0.072."""
    if n <= 0 or k >= n:
        return 1.0
    lo, hi = k / n, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if binom_cdf(k, n, mid) > 1 - confidence:
            lo = mid
        else:
            hi = mid
    return hi


def worse_sign_p(worse: int, better: int) -> float:
    """One-sided exact sign test that the candidate is WORSE: of the items
    that differ, P(at least `worse` go the bad way) when both ways are
    equally likely."""
    changed = worse + better
    if changed == 0:
        return 1.0
    return 1.0 - binom_cdf(worse - 1, changed, 0.5)


# ------------------------------------------------------------ evidence


@dataclass
class Evidence:
    """Everything measured on one candidate so far."""

    objective: str = "quality"
    judged: list[Judged] = field(default_factory=list)
    # eval -> stratum -> items in the candidate's whole queue
    population: dict[str, dict[str, int]] = field(default_factory=dict)
    # eval -> stratum -> items of the queue not yet judged
    remaining: dict[str, dict[str, int]] = field(default_factory=dict)
    primary_eval: str | None = None
    full: bool = False  # the queue is the whole targeted split
    # timing pairs, item -> value: the candidate, the reference (the run's
    # baseline), and its parent when that is another run config
    latency: dict[str, float] = field(default_factory=dict)
    ref_latency: dict[str, float] = field(default_factory=dict)
    cost: dict[str, float] = field(default_factory=dict)
    ref_cost: dict[str, float] = field(default_factory=dict)
    parent_latency: dict[str, float] | None = None
    parent_cost: dict[str, float] | None = None
    outputs: int = 0
    invalid: int = 0
    spend_usd: float = 0.0
    timing_planned: int = 0
    timing_done: bool = False
    timing_look: bool = False
    timing_looks: int = 1
    quality_look: bool = False
    quality_looks: int = 1
    disc_done: bool = False
    named_done: bool = False
    eval_names: dict[str, str] = field(default_factory=dict)

    @property
    def judged_n(self) -> int:
        return len(self.judged)

    @property
    def no_rows(self) -> bool:
        """Items were asked for, and not one candidate row was stored: an
        error, not a quality verdict. The lines say so instead of counting
        every missing row as a loss."""
        return bool(self.judged) and all(r.cand_pass is None for r in self.judged)

    def fixed_broke(self, eval_id: str | None = None) -> tuple[int, int]:
        rows = [r for r in self.judged if eval_id is None or r.eval_id == eval_id]
        fixed = sum(1 for r in rows if r.d == 1)
        broke = sum(1 for r in rows if r.d == -1)
        return fixed, broke

    @property
    def efficiency(self) -> bool:
        return self.objective in ("speed", "cost")

    def guards(self) -> list[Judged]:
        """The judged items the parent passes."""
        return [r for r in self.judged if r.parent_pass is True]

    def guard_losses(self) -> tuple[int, int, int, int]:
        """(losses, guards judged, fixes, parent failures judged). A missing
        candidate row on a guard is a loss."""
        guards = self.guards()
        fails = [r for r in self.judged if r.parent_pass is False]
        return (
            sum(1 for r in guards if not r.cand_pass),
            len(guards),
            sum(1 for r in fails if r.cand_pass),
            len(fails),
        )

    def loss_bound(self) -> float | None:
        """The upper `LOSS_BOUND_CONFIDENCE` bound on the loss rate over the
        judged guards, or None when no guard is judged."""
        lost, n, _fixed, _fails = self.guard_losses()
        return rate_upper_bound(lost, n) if n else None

    def loss_text(self) -> str:
        """ "lost 1 of 24 baseline passes (no loss rate above 18% at 95%) ·
        fixed 0 of 2 baseline failures (noise level)"."""
        lost, n, fixed, n_fails = self.guard_losses()
        bound = self.loss_bound()
        shown = (
            f" (no loss rate above {bound:.0%} at {LOSS_BOUND_CONFIDENCE:.0%})"
            if bound is not None
            else ""
        )
        return (
            f"lost {lost} of {n} baseline passes{shown} · fixed {fixed} of"
            f" {n_fails} baseline failures (noise level)"
        )


@dataclass
class Stop:
    rule: StopRule
    n: int
    numbers: str
    budget: bool = False  # a budget stop: not a verdict on the candidate

    def line(self, rule_text: str) -> str:
        return f"stopped at n={self.n}: {self.numbers} — rule: {rule_text}"


def rule_text(rule: StopRule, ev: Evidence) -> str:
    """The rule in words, with the per-look alpha."""
    at = f" once n >= {rule.min_n}" if rule.min_n > 1 else ""
    k = rule.kind
    alpha = rule.alpha or DEFAULT_ALPHA

    def per(looks: int) -> str:
        return f" at alpha {alpha:g} ({alpha / looks:.3g} per look over {looks} looks)"

    if k == "max_items":
        return f"max_items {int(rule.threshold)}"
    if k == "max_usd":
        return f"max_usd ${rule.threshold:.2f}"
    if k in ("latency_ratio", "cost_ratio"):
        if rule.alpha:
            return (
                f"{k} clearly above {rule.threshold:.2f} (paired sign test)"
                f"{per(ev.timing_looks)}{at}"
            )
        return f"{k} ({rule.stat}) above {rule.threshold:.2f}{at}"
    if k == "invalid_output_rate":
        return f"invalid_output_rate above {rule.threshold:.2f}{at}"
    if k == "futility":
        return "futility: the train bar is out of reach"
    if k == "residual":
        return (
            f"residual: at least {rule.threshold:.0%} of the parent's failures in"
            " the discriminating block fixed, and the guard breaks not clearly"
            f" above the noise rate {residual_noise(rule):.2f} (one-sided exact"
            f" binomial test at alpha {residual_alpha(rule):g}, one look)"
        )
    if k == "targets":
        return "targets: every named item passes"
    if k == "not_better":
        return "not_better: clearly better than the baseline once the timing is done"
    if k == "guard_losses":
        return (
            f"guard_losses: losses on the baseline's passes clearly above the"
            f" noise rate {rule.threshold:.2f} (or the guards' fragility)"
            f"{per(ev.quality_looks)}{at}"
        )
    name = ev.eval_names.get(rule.eval_id or "", rule.eval_id) or "all evals"
    if rule.vs_baseline:
        return (
            f"eval_metric {name} vs baseline below {rule.threshold:+.2f}"
            f"{per(ev.quality_looks)}{at}"
        )
    return f"eval_metric {name} below {rule.threshold:.2f}{per(ev.quality_looks)}{at}"


def _pairs(num: dict[str, float], den: dict[str, float]) -> list[float]:
    return [num[i] / den[i] for i in num if i in den and den[i] > 0 and num[i] > 0]


def _check_timing_test(
    rule: StopRule, ev: Evidence, values: list[float], shown: str
) -> Stop | None:
    """A sign test that the per-item values are clearly above threshold."""
    n = len(values)
    if n < max(rule.min_n, 1) or not ev.timing_look:
        return None
    above = sum(1 for v in values if v > rule.threshold)
    below = sum(1 for v in values if v < rule.threshold)
    p = worse_sign_p(above, below)
    alpha_look = (rule.alpha or DEFAULT_ALPHA) / max(ev.timing_looks, 1)
    mid = median(values)
    if mid > rule.threshold and p < alpha_look:
        return Stop(
            rule,
            n,
            f"{shown} median {mid:.2f} > {rule.threshold:.2f}, {above} of {n} above,"
            f" p={p:.4f} < {alpha_look:.3g}",
        )
    return None


def inconclusive(ev: Evidence) -> str:
    if ev.efficiency:
        lost, n, _fixed, _fails = ev.guard_losses()
        return f"inconclusive — lost {lost} of {n} baseline passes so far"
    fixed, broke = ev.fixed_broke()
    return f"inconclusive — net +{fixed}/−{broke} so far"


def capped_line(usd: float, ev: Evidence) -> str:
    """ "capped at $4.00 after 17 items: inconclusive — net +2/−0 so far"."""
    return f"capped at ${usd:.2f} after {ev.judged_n} items: {inconclusive(ev)}"


def run_budget_line(left: float, ev: Evidence) -> str:
    """ "capped by the run budget ($1.20 left) after 17 items: inconclusive —
    net +2/−0 so far"."""
    return (
        f"capped by the run budget (${left:.2f} left) after {ev.judged_n} items:"
        f" {inconclusive(ev)}"
    )


def check(rule: StopRule, ev: Evidence) -> Stop | None:
    """The stop this rule calls for on this evidence, or None."""
    k = rule.kind
    if k == "max_items":
        if ev.judged_n >= rule.threshold:
            return Stop(
                rule,
                ev.judged_n,
                f"capped at {int(rule.threshold)} items: {inconclusive(ev)}",
                True,
            )
        return None
    if k == "max_usd":
        if ev.spend_usd >= rule.threshold:
            return Stop(rule, ev.judged_n, capped_line(rule.threshold, ev), True)
        return None
    if k in ("latency_ratio", "cost_ratio"):
        num, den = (
            (ev.latency, ev.ref_latency)
            if k == "latency_ratio"
            else (ev.cost, ev.ref_cost)
        )
        ratios = _pairs(num, den)
        if rule.alpha:
            return _check_timing_test(rule, ev, ratios, k.replace("_", " "))
        n = len(ratios)
        if n < rule.min_n or not ratios:
            return None
        from kiln_harness_tuner.latency import percentile

        value = median(ratios) if rule.stat == "median" else percentile(ratios, 0.9)
        if value > rule.threshold:
            return Stop(
                rule, n, f"{rule.stat} {k} {value:.2f} > {rule.threshold:.2f} over {n}"
            )
        return None
    if k == "invalid_output_rate":
        n = ev.outputs
        if n < rule.min_n or n == 0:
            return None
        rate = ev.invalid / n
        if rate > rule.threshold:
            return Stop(
                rule,
                n,
                f"invalid output: {ev.invalid} of {n} outputs fail the task's"
                f" output schema",
            )
        return None
    if k == "futility":
        return _futility(rule, ev)
    if k == "residual":
        return _residual(rule, ev)
    if k == "targets":
        if not ev.named_done:
            return None
        named = [r for r in ev.judged if r.block == NAMED]
        failing = [r.item_id for r in named if not r.cand_pass]
        if failing:
            return Stop(
                rule,
                ev.judged_n,
                f"{len(named) - len(failing)}/{len(named)} named items pass; still"
                f" failing {', '.join(failing[:5])}",
            )
        return None
    if k == "not_better":
        return _not_better(rule, ev)
    if k == "guard_losses":
        return _guard_losses(rule, ev)
    return _eval_metric(rule, ev)


def _guard_losses(rule: StopRule, ev: Evidence) -> Stop | None:
    """Losses on the judged guards against the noise rate (module doc). A
    fix of a parent failure is never counted."""
    if not ev.quality_look:
        return None
    guards = ev.guards()
    n = len(guards)
    if n < max(rule.min_n, 1):
        return None
    lost = sum(1 for r in guards if not r.cand_pass)
    p0 = sum(max(rule.threshold, r.fragility) for r in guards) / n
    p = above_rate_p(lost, n, p0)
    alpha_look = (rule.alpha or DEFAULT_ALPHA) / max(ev.quality_looks, 1)
    if lost / n > p0 and p < alpha_look:
        _lost, _n, fixed, n_fails = ev.guard_losses()
        return Stop(
            rule,
            n,
            f"lost {lost} of {n} baseline passes ({lost / n:.0%}) against a"
            f" noise rate of {p0:.2f}, p={p:.4f} < {alpha_look:.3g} (fixes not"
            f" counted: {fixed} of {n_fails})",
        )
    return None


def _eval_metric(rule: StopRule, ev: Evidence) -> Stop | None:
    if not ev.quality_look:
        return None
    est = estimate(ev.judged, ev.population, rule.eval_id)
    if est is None or est.n < rule.min_n:
        return None
    name = ev.eval_names.get(rule.eval_id or "", rule.eval_id) or "all evals"
    alpha_look = (rule.alpha or DEFAULT_ALPHA) / max(ev.quality_looks, 1)
    if rule.vs_baseline:
        if est.se <= 0:
            return None
        p = normal_cdf((est.net - 0.0) / est.se)
        if est.net < rule.threshold and p < alpha_look:
            fixed, broke = ev.fixed_broke(rule.eval_id)
            return Stop(
                rule,
                est.n,
                f"{name} net {est.net:+.2f} (se {est.se:.2f}; fixed {fixed},"
                f" broke {broke}), p={p:.4f} < {alpha_look:.3g}",
            )
        return None
    if est.rate is None:
        return None
    n = est.n
    passed = round(est.rate * n)
    p = below_rate_p(passed, n, rule.threshold)
    if est.rate < rule.threshold and p < alpha_look:
        return Stop(
            rule,
            n,
            f"{name} pass rate {est.rate:.2f} below {rule.threshold:.2f},"
            f" p={p:.4f} < {alpha_look:.3g}",
        )
    return None


def _fixable(ev: Evidence, eval_id: str) -> int:
    """Items still to judge on `eval_id` whose result could still become a
    fix: the parent fails them, has no row on them, or the planner named
    them."""
    left = ev.remaining.get(eval_id, {})
    return sum(int(left.get(s, 0)) for s in (FAIL, UNSCORED, NAMED))


def _futility(rule: StopRule, ev: Evidence) -> Stop | None:
    """An EARLY stop only: while items remain, the bar is out of reach even
    if every one of them went the candidate's way. At the end of the queue
    the measurement completes and the train rule decides, with the
    analysis."""
    if not ev.full:
        return None
    if not any(n for per in ev.remaining.values() for n in per.values()):
        return None
    if ev.objective in ("speed", "cost"):
        from kiln_harness_tuner.analysis import row_loss_tolerance

        for eval_id in sorted(ev.population):
            fixed, broke = ev.fixed_broke(eval_id)
            n_items = sum(ev.population[eval_id].values())
            best_loss = broke - fixed - _fixable(ev, eval_id)
            tol = row_loss_tolerance(n_items)
            if best_loss > tol:
                name = ev.eval_names.get(eval_id, eval_id)
                return Stop(
                    rule,
                    ev.judged_n,
                    f"{name} loses at least {best_loss} net items even if every"
                    f" item still to judge were fixed (allowance {tol} of {n_items})",
                )
        return None
    eval_id = ev.primary_eval
    if eval_id is None or eval_id not in ev.population:
        return None
    fixed, broke = ev.fixed_broke(eval_id)
    best = fixed - broke + _fixable(ev, eval_id)
    if best <= 0:
        name = ev.eval_names.get(eval_id, eval_id)
        return Stop(
            rule,
            ev.judged_n,
            f"{name} can end at most {best:+d} net items (fixed {fixed}, broke"
            f" {broke}, {_fixable(ev, eval_id)} fixable left); it must improve",
        )
    return None


def _residual(rule: StopRule, ev: Evidence) -> Stop | None:
    if not ev.disc_done:
        return None
    block = [r for r in ev.judged if r.block == "discriminating"]
    fails = [r for r in block if r.stratum == FAIL]
    guards = [r for r in block if r.stratum != FAIL]
    if not fails:
        return None
    fixed = sum(1 for r in fails if r.cand_pass)
    broke = sum(1 for r in guards if r.cand_pass is False)
    need = max(1, math.ceil(rule.threshold * len(fails) - 1e-9))
    if ev.objective in ("speed", "cost"):
        summary = f"{fixed}/{len(fails)} fixed, {broke}/{len(guards)} guards broke"
        if broke > fixed:
            return Stop(rule, len(block), f"residual: {summary} (a net loss)")
        return None
    test = guard_breaks(rule, guards)
    if fixed < need or test.clear:
        return Stop(
            rule,
            len(block),
            f"residual: {fixed}/{len(fails)} fixed (needed {need}), {test.text}"
            f" against a noise rate of {test.p0:.2f} at alpha {test.alpha:g}",
        )
    return None


def residual_noise(rule: StopRule) -> float:
    """p0 of the guard half of `residual`: the rule's `noise_rate` (floored),
    else `GUARD_NOISE_DEFAULT`."""
    return guard_noise_rate(rule.noise_rate)


def residual_alpha(rule: StopRule) -> float:
    """The alpha of the guard half of `residual`: one look, the full alpha."""
    return rule.alpha or DEFAULT_ALPHA


@dataclass(frozen=True)
class GuardBreaks:
    """The guard half of `residual` on one discriminating block."""

    broke: int
    n: int  # the guards with a row
    p0: float
    p: float
    alpha: float

    @property
    def clear(self) -> bool:
        """The breaks are clearly above noise: the guard half fires."""
        return self.n > 0 and self.broke / self.n > self.p0 and self.p < self.alpha

    @property
    def text(self) -> str:
        """ "1 of 4 guards broke, within noise (p=0.34)"."""
        verdict = "clearly above noise" if self.clear else "within noise"
        return f"{self.broke} of {self.n} guards broke, {verdict} (p={self.p:.2g})"


def guard_breaks(rule: StopRule, guards: Sequence[Judged]) -> GuardBreaks:
    """The test of the guard half of `residual` (module doc): k breaks in
    the n guards with a row, one-sided exact binomial against the mean of
    max(p0, fragility). A guard with no row is neither a break nor
    counted."""
    judged = [r for r in guards if r.cand_pass is not None]
    n = len(judged)
    broke = sum(1 for r in judged if r.cand_pass is False)
    p0 = residual_noise(rule)
    if n:
        p0 = sum(max(p0, r.fragility) for r in judged) / n
    p = above_rate_p(broke, n, p0) if n else 1.0
    return GuardBreaks(broke, n, p0, p, residual_alpha(rule))


def _not_better(rule: StopRule, ev: Evidence) -> Stop | None:
    if ev.objective not in ("speed", "cost") or not ev.timing_done:
        return None
    from kiln_harness_tuner.latency import (
        clearly_slower,
        compare_latencies,
        not_faster_verdict,
        slower_than_parent_verdict,
    )

    metric = "latency_ms" if ev.objective == "speed" else "cost_usd"
    ref, cand = (
        (ev.ref_latency, ev.latency)
        if metric == "latency_ms"
        else (ev.ref_cost, ev.cost)
    )
    c: LatencyComparison = compare_latencies(
        ref,
        cand,
        method="paired_drive",
        split="train",
        planned=ev.timing_planned or None,
        metric=metric,  # type: ignore[arg-type]
    )
    from kiln_harness_tuner.latency import MIN_PAIRS

    lost = max(0, ev.timing_planned - c.n_items)
    if c.incomplete and lost:
        # Too many drives failed for a verdict. None is claimed, but the
        # train speed rule cannot pass either, so the candidate stops here
        # with the note rather than buying a judge for every item.
        from kiln_harness_tuner.latency import incomplete_note

        dropped = {f"#{n}": "errored" for n in range(lost)}
        return Stop(
            rule, c.n_items, incomplete_note(c.n_items, ev.timing_planned, dropped)
        )
    if c.incomplete or c.n_items < MIN_PAIRS:
        return None  # a split too small for a speed verdict: none is claimed
    if not c.clearly_faster:
        return Stop(rule, c.n_items, not_faster_verdict(c).split(" — ")[0])
    parent = ev.parent_latency if metric == "latency_ms" else ev.parent_cost
    if parent is not None:
        vs = compare_latencies(
            parent,
            cand,
            method="paired_drive",
            split="train",
            metric=metric,  # type: ignore[arg-type]
        )
        if clearly_slower(vs):
            return Stop(rule, vs.n_items, slower_than_parent_verdict(vs))
    return None


def first_stop(rules: list[StopRule], ev: Evidence) -> Stop | None:
    """The first rule, in order, that fires — a verdict before a budget."""
    budget: Stop | None = None
    for rule in rules:
        hit = check(rule, ev)
        if hit is None:
            continue
        if not hit.budget:
            return hit
        budget = budget or hit
    return budget
