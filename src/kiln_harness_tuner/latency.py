"""Latency: the speed objective's measurement, all in code.

The speed objective is "much faster if possible", with no target, and quality
must not get meaningfully worse. What counts is **stable
wall-clock time per request** — never throughput or token counts. A strong
model that finishes in fewer tokens is fast even at a low token rate.

The metric is per-item `usage.total_llm_latency_ms`: the sum of the task
run's LLM call latencies, which for a single-turn task is the wall-clock time
of the call. Eval results carry it as `task_run_usage.total_llm_latency_ms`
(the server resolves it from the scored task run).

**The comparison is paired.** Latency moves with provider load and with the
loop's own concurrency, so a new arm is never compared with a number stored
hours ago. Both arms are compared on the SAME items, item by item:

- `median_ratio` — the median over items of candidate / baseline;
- the **sign test** — exact, two-sided, on how many items got faster and how
  many got slower (ties dropped). It asks only "which way on most items", so
  a few slow outliers cannot carry it;
- **the tail** — the p90 of each arm and the ratio of the two, with a 95%
  paired bootstrap interval on that ratio (items resampled as pairs,
  `BOOTSTRAP_RESAMPLES` draws, a fixed seed so the same data gives the same
  interval).

`clearly_faster` needs all three: the sign test significant at
`SIGNIFICANCE_ALPHA` with more items faster than slower, a median ratio below
1, and NO significant p90 regression — the bootstrap interval of the p90
ratio lying entirely above 1 blocks it. A candidate that is faster in the
middle but slower in the tail is not clearly faster.

`paired_drive_pass` is the fair way to get the pairs: code drives the
baseline and each candidate on the same items in the same window,
alternating which arm goes first per item. `compare_eval_runs` is the passive
way — latencies read off the eval runs already stored — which is cheap but
compares drives made at different times, so its `method` says so.
`compare_eval_run_costs` is the same passive read for the per-request cost
of the task call.

A drive goes through `POST …/run`, or, when the task needs an OpenEnv episode
or a simulated user, through a one-item eval job (`kiln_harness_tuner.job_drives`).
The pairing is the same on both routes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING, Any, Literal

from kiln_harness_tuner.schemas import LatencyComparison

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

SIGNIFICANCE_ALPHA = 0.05
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20260924
# Below this many pairs the sign test cannot reach significance anyway
# (n=5 all one way gives p=0.0625), so no verdict is claimed.
MIN_PAIRS = 6
# How many items the paired drive pass drives per arm. 30 pairs detect a
# consistent shift (the sign test needs 21 of 30 one way for p<0.05; 20 of
# 30 gives p=0.099) at a cost of 30 drives per arm, no judge.
SPEED_SAMPLE_ITEMS = 30
MAX_CONCURRENT_LATENCY_DRIVES = 4
# A pass that paired fewer than this share of its planned items is
# `incomplete`, never a verdict. Two thirds — 20 of 30: at n = 20 the sign
# test still reaches p < 0.05 at 15 of 20 one way, so a real speed-up stays
# detectable; below it the survivors are the items that did NOT fail, and
# failures are not random (long, slow items time out first), so a verdict on
# the remainder would be biased toward "faster". Never below MIN_PAIRS.
SPEED_PAIRED_FRACTION = 2 / 3
# An arm forked from a parent that is NOT the run's baseline. The objective's
# question is asked against the BASELINE; against the parent the arm only must not be
# CLEARLY SLOWER: at least MIN_PAIRS pairs (so never on 3 items), the sign
# test two-sided p < SIGNIFICANCE_ALPHA with more items slower than faster,
# and a median ratio above PARENT_SLOWER_MIN_RATIO. Simulated with the
# noise model above (40,000 draws): on 8 pairs it stops a
# flat arm 0.0% of the time, a true 1.3× slower one 4.6%, 1.5× 24%, 2× 71%;
# on the 30-item pass 1.3× 2.9%, 1.5× 50%, 1.8× 99%. Without the 1.5 floor
# a 1.3× slower arm is stopped 14% (8 items) and 90% (30 items) — the
# floor keeps the parent check to what the arm gives away, not to noise.
PARENT_SLOWER_MIN_RATIO = 1.5
DRIVE_RETRY_SLEEP_SECS = 5.0
LATENCY_RUN_TAG = "kiln_harness_tuner_latency"
DRIVE_TIMEOUT_SECS = 600.0


def _latency_in(usage: Any) -> float | None:
    if isinstance(usage, Mapping):
        value = usage.get("total_llm_latency_ms")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value > 0:
                return float(value)
    return None


def latency_of_task_run(run: Mapping[str, Any]) -> float | None:
    """A TASK run's wall-clock latency in ms: its own `usage` — the call of
    the run config being optimised. None when absent (older records)."""
    return _latency_in(run.get("usage"))


def latency_of_eval_run(run: Mapping[str, Any]) -> float | None:
    """An EVAL run's task latency in ms: `task_run_usage` and nothing else.
    The rule: speed is the task call, never judge time. An eval
    run's `eval_usage` is the judge's; a `usage` key on an eval-run record,
    were a server ever to add one, is not the task's either — neither is
    read here."""
    return _latency_in(run.get("task_run_usage"))


def task_cost_of_eval_run(run: Mapping[str, Any]) -> float | None:
    """An EVAL run's task-call provider cost in USD: `task_run_usage.cost`
    and nothing else (the judge's cost is `eval_usage`, never read here)."""
    usage = run.get("task_run_usage")
    value = usage.get("cost") if isinstance(usage, Mapping) else None
    return float(value) if isinstance(value, (int, float)) else None


def item_latencies(runs: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """item id -> latency, from eval runs. Several records for one item
    (replicates) give their median."""
    return _per_item(runs, latency_of_eval_run)


def item_task_costs(runs: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """item id -> the task call's provider cost per request, from eval runs.
    Several records for one item (replicates) give their median."""
    return _per_item(runs, task_cost_of_eval_run)


def _per_item(
    runs: Iterable[Mapping[str, Any]],
    value_of: Callable[[Mapping[str, Any]], float | None],
) -> dict[str, float]:
    by_item: dict[str, list[float]] = {}
    for run in runs:
        item = run.get("dataset_id") or run.get("eval_input_id")
        value = value_of(run)
        if item is None or value is None:
            continue
        by_item.setdefault(str(item), []).append(value)
    return {item: median(values) for item, values in by_item.items()}


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile (the 'inclusive' definition: p0 is the
    minimum, p100 the maximum)."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile of no values")
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def sign_test_p(faster: int, slower: int) -> float:
    """Exact two-sided sign test (binomial, p=1/2), ties already dropped."""
    n = faster + slower
    if n == 0:
        return 1.0
    k = min(faster, slower)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def _p90_ratio_ci(
    pairs: Sequence[tuple[float, float]], resamples: int, seed: int
) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(pairs)
    ratios: list[float] = []
    for _ in range(resamples):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        base = percentile([a for a, _b in sample], 0.9)
        cand = percentile([b for _a, b in sample], 0.9)
        ratios.append(cand / base)
    return percentile(ratios, 0.025), percentile(ratios, 0.975)


# The word for "the candidate is better" on each metric, and its opposite.
BETTER_WORSE = {"latency_ms": ("faster", "slower"), "cost_usd": ("cheaper", "dearer")}


def _fmt_value(value: float | None, metric: str) -> str:
    if value is None:
        return "?"
    if metric == "cost_usd":
        return f"${value:.4f}"
    return f"{value / 1000:.1f} s"


def paired_floor(planned: int) -> int:
    """The fewest pairs a pass of `planned` items may yield and still give a
    verdict: max(MIN_PAIRS, ceil(SPEED_PAIRED_FRACTION × planned))."""
    return max(MIN_PAIRS, math.ceil(SPEED_PAIRED_FRACTION * planned - 1e-9))


def incomplete_note(n: int, planned: int, dropped: Mapping[str, str]) -> str:
    """ "speed incomplete: 8 of 30 pairs (22 errored)"."""
    return f"speed incomplete: {n} of {planned} pairs ({len(dropped)} errored)"


def compare_latencies(
    baseline: Mapping[str, float],
    candidate: Mapping[str, float],
    *,
    method: Literal["paired_drive", "eval_runs"],
    split: str,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    planned: int | None = None,
    dropped: Mapping[str, str] | None = None,
    metric: Literal["latency_ms", "cost_usd"] = "latency_ms",
) -> LatencyComparison:
    """The paired comparison over the items both sides carry. With
    `planned` (a drive pass), fewer pairs than `paired_floor(planned)` make
    the result `incomplete`: numbers, never a verdict."""
    items = sorted(
        i
        for i in set(baseline) & set(candidate)
        if baseline[i] > 0 and candidate[i] > 0
    )
    pairs = [(baseline[i], candidate[i]) for i in items]
    n = len(pairs)
    dropped = dict(dropped or {})
    incomplete = planned is not None and n < paired_floor(planned)
    better, worse = BETTER_WORSE[metric]
    extra = {
        "metric": metric,
        "planned_items": planned,
        "dropped_items": {k: v[:200] for k, v in sorted(dropped.items())[:60]},
        "incomplete": incomplete,
    }
    if n == 0:
        return LatencyComparison(
            method=method,
            split=split,
            n_items=0,
            clearly_faster=False,
            p90_regressed=False,
            note=(
                incomplete_note(0, planned, dropped)
                if incomplete and planned
                else "no item carries a latency on both sides"
            ),
            **extra,
        )
    base = [a for a, _b in pairs]
    cand = [b for _a, b in pairs]
    ratios = [b / a for a, b in pairs]
    faster = sum(1 for a, b in pairs if b < a)
    slower = sum(1 for a, b in pairs if b > a)
    p = sign_test_p(faster, slower)
    p90_base = percentile(base, 0.9)
    p90_cand = percentile(cand, 0.9)
    ci_low = ci_high = None
    if n >= MIN_PAIRS:
        ci_low, ci_high = _p90_ratio_ci(pairs, resamples, seed)
    p90_regressed = ci_low is not None and ci_low > 1.0
    median_ratio = median(ratios)
    clearly_faster = (
        n >= MIN_PAIRS
        and not incomplete
        and faster > slower
        and p < SIGNIFICANCE_ALPHA
        and median_ratio < 1.0
        and not p90_regressed
    )
    if incomplete and planned is not None:
        note = incomplete_note(n, planned, dropped)
    elif n < MIN_PAIRS:
        note = f"only {n} paired item(s); no speed verdict below {MIN_PAIRS}"
    elif p90_regressed:
        note = f"{better} or not in the middle, the p90 is worse beyond doubt"
    elif clearly_faster:
        note = f"{better} on most items, and the tail is not worse beyond doubt"
    else:
        note = f"not clearly {better}"
    return LatencyComparison(
        method=method,
        split=split,
        n_items=n,
        baseline_median_ms=median(base),
        candidate_median_ms=median(cand),
        baseline_p90_ms=p90_base,
        candidate_p90_ms=p90_cand,
        baseline_iqr_ms=percentile(base, 0.75) - percentile(base, 0.25),
        candidate_iqr_ms=percentile(cand, 0.75) - percentile(cand, 0.25),
        median_ratio=median_ratio,
        faster_items=faster,
        slower_items=slower,
        sign_test_p=p,
        p90_ratio=p90_cand / p90_base,
        p90_ratio_ci_low=ci_low,
        p90_ratio_ci_high=ci_high,
        clearly_faster=clearly_faster,
        p90_regressed=p90_regressed,
        note=note,
        **extra,
    )


def compare_eval_runs(
    baseline_runs: Iterable[Mapping[str, Any]],
    candidate_runs: Iterable[Mapping[str, Any]],
    split: str,
) -> LatencyComparison | None:
    """The passive comparison from stored eval runs; None when neither side
    carries a latency at all (an older server, or an empty read)."""
    base = item_latencies(baseline_runs)
    cand = item_latencies(candidate_runs)
    if not base and not cand:
        return None
    return compare_latencies(base, cand, method="eval_runs", split=split)


def compare_eval_run_costs(
    baseline_runs: Iterable[Mapping[str, Any]],
    candidate_runs: Iterable[Mapping[str, Any]],
    split: str,
) -> LatencyComparison | None:
    """The passive per-request cost comparison of the task call
    from stored eval runs (`task_run_usage.cost`, metric `cost_usd`). It
    needs no drive, so an arm's cost ratio is visible under every objective.
    None when neither side carries a cost."""
    base = item_task_costs(baseline_runs)
    cand = item_task_costs(candidate_runs)
    if not base and not cand:
        return None
    return compare_latencies(
        base, cand, method="eval_runs", split=split, metric="cost_usd"
    )


def single_arm(
    latencies: Mapping[str, float],
    *,
    method: Literal["paired_drive", "eval_runs"],
    split: str,
    note: str,
) -> LatencyComparison | None:
    """One arm's median, p90 and spread (p75 − p25) on the `baseline_*`
    fields; the `candidate_*` fields stay unset. None with no values."""
    values = list(latencies.values())
    if not values:
        return None
    return LatencyComparison(
        method=method,
        split=split,
        n_items=len(values),
        baseline_median_ms=percentile(values, 0.5),
        baseline_p90_ms=percentile(values, 0.9),
        baseline_iqr_ms=percentile(values, 0.75) - percentile(values, 0.25),
        note=note,
    )


def arm_line(label: str, c: LatencyComparison | None) -> str:
    """One arm in seconds for the setup log: "baseline speed: median 33.1 s,
    p90 62.8 s, IQR 12.0 s over 30 train items"."""
    if c is None or c.baseline_median_ms is None:
        return f"{label}: no latency measured"
    return (
        f"{label}: median {c.baseline_median_ms / 1000:.1f} s, p90"
        f" {(c.baseline_p90_ms or 0) / 1000:.1f} s, IQR"
        f" {(c.baseline_iqr_ms or 0) / 1000:.1f} s over {c.n_items} {c.split}"
        " items"
    )


def output_valid(output: Any, schema: Mapping[str, Any] | None) -> bool:
    """Is one drive's output usable? With a task output schema: it parses as
    JSON and validates against the schema. Without one: it is non-empty."""
    if not isinstance(output, str) or not output.strip():
        return False
    if schema is None:
        return True
    try:
        value = json.loads(output)
    except ValueError:
        return False
    try:
        import jsonschema
    except ImportError:  # pragma: no cover — a kiln-ai dependency
        return True
    try:
        jsonschema.validate(value, dict(schema))
    except jsonschema.ValidationError:
        return False
    except jsonschema.SchemaError:
        return True  # the task's schema itself is not checkable here
    return True


def run_output(run: Mapping[str, Any]) -> Any:
    """A task run's final output string (`output.output`)."""
    output = run.get("output")
    return output.get("output") if isinstance(output, Mapping) else None


async def task_output_schema(ctx: Ctx) -> dict[str, Any] | None:
    """The task's output JSON schema, or None for a plaintext task."""
    from kiln_harness_tuner.kiln import KilnError

    try:
        task = await ctx.kiln.call(
            "GET", f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}"
        )
    except KilnError:
        return None
    raw = task.get("output_json_schema") if isinstance(task, dict) else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, dict) else None


def clearly_slower(c: LatencyComparison | None) -> bool:
    """The parent check of an arm forked from a non-baseline parent — see
    PARENT_SLOWER_MIN_RATIO. `c` is parent (baseline side) vs the arm."""
    return (
        c is not None
        and not c.incomplete
        and c.n_items >= MIN_PAIRS
        and c.median_ratio is not None
        and c.median_ratio > PARENT_SLOWER_MIN_RATIO
        and c.slower_items > c.faster_items
        and sign_test_p(c.faster_items, c.slower_items) < SIGNIFICANCE_ALPHA
    )


def slower_than_parent_verdict(c: LatencyComparison) -> str:
    """ "clearly slower than its parent: median ratio 2.10 over 8 items,
    sign p=0.0078"."""
    _better, worse = BETTER_WORSE[c.metric]
    return (
        f"clearly {worse} than its parent: median ratio {c.median_ratio:.2f} over"
        f" {c.n_items} items, sign p={sign_test_p(c.faster_items, c.slower_items):.2g}"
    )


def not_faster_verdict(c: LatencyComparison | None) -> str:
    """The plain verdict of an arm stopped at the timing stage: "not faster:
    median ratio 0.97, sign p=0.4 — quality not measured"."""
    tail = " — quality not measured"
    if c is None:
        return "unmeasured: no train input to drive" + tail
    better, _worse = BETTER_WORSE[c.metric]
    if c.incomplete or c.median_ratio is None or c.sign_test_p is None:
        return c.note + tail
    if c.p90_regressed:
        return (
            f"not {better}: median ratio {c.median_ratio:.2f} but the p90 is worse"
            f" (ratio {c.p90_ratio:.2f})" + tail
        )
    return (
        f"not {better}: median ratio {c.median_ratio:.2f}, sign"
        f" p={c.sign_test_p:.2g}" + tail
    )


def latency_line(label: str, c: LatencyComparison | None) -> str:
    """One human line — for the logs, the memory record and the report."""
    if c is None:
        return f"{label}: no latency measured"
    if c.n_items == 0 or c.median_ratio is None:
        return f"{label}: {c.note}"
    ci = (
        f" [{c.p90_ratio_ci_low:.2f}..{c.p90_ratio_ci_high:.2f}]"
        if c.p90_ratio_ci_low is not None and c.p90_ratio_ci_high is not None
        else ""
    )
    better, worse = BETTER_WORSE[c.metric]
    if c.metric == "cost_usd":
        med = f"${c.baseline_median_ms:.4f}→${c.candidate_median_ms:.4f} per request"
        p90 = f"${c.baseline_p90_ms:.4f}→${c.candidate_p90_ms:.4f}"
    else:
        med = f"{c.baseline_median_ms:.0f}→{c.candidate_median_ms:.0f} ms"
        p90 = f"{c.baseline_p90_ms:.0f}→{c.candidate_p90_ms:.0f} ms"
    return (
        f"{label} ({c.split}, {c.method}, n={c.n_items}): median {med},"
        f" per-item ratio {c.median_ratio:.2f}, {better} on {c.faster_items}"
        f" {worse} on {c.slower_items} (sign p={c.sign_test_p:.3g}); p90"
        f" {p90}, ratio {c.p90_ratio:.2f}{ci} — {c.note}"
    )


# ------------------------------------------------------------ paired drives


def split_inputs(
    project_path: str, task_id: str, split: str, eval_ids: Iterable[str] | None = None
) -> list[Any]:
    """The drivable eval inputs in `split` of the named evals (every eval
    when None), read from disk by the orchestrator's code — never by an
    agent. Returns probe `TrainInput`s (the drive helper's input shape),
    each with the first eval (by id) it was found in."""
    from dataclasses import replace

    from kiln_harness_tuner.probes import input_from_doc
    from kiln_harness_tuner.quarantine import membership, task_dirs

    wanted = set(eval_ids) if eval_ids is not None else None
    out: dict[str, Any] = {}
    for task_dir in task_dirs(Path(project_path), task_id):
        member, loaded = membership(task_dir)
        for eval_id, splits in sorted(member.by_eval.items()):
            if wanted is not None and eval_id not in wanted:
                continue
            for source, item_id in splits.get(split, set()):
                if source != "eval_input" or item_id in out:
                    continue
                doc = loaded["eval_inputs"].get(item_id)
                inp = input_from_doc(doc) if doc else None
                if inp is not None:
                    out[item_id] = replace(inp, eval_id=eval_id)
    return [out[i] for i in sorted(out)]


def sample_inputs(inputs: Sequence[Any], n: int = SPEED_SAMPLE_ITEMS) -> list[Any]:
    """A fixed sample: the same items every call, spread across evals — by a
    hash of the item id, never by position."""
    return sorted(inputs, key=lambda i: hashlib.sha256(i.id.encode()).hexdigest())[:n]


@dataclass
class ArmTimings:
    """One arm's judge-free timings on a sample: item id -> ms, item id ->
    $/request, item id -> why that drive has no timing."""

    latencies: dict[str, float] = field(default_factory=dict)
    costs: dict[str, float] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)

    def covers(self, item_ids: Sequence[str]) -> bool:
        return all(i in self.latencies or i in self.failures for i in item_ids)

    def add(self, drives: DrivePass, rc_id: str) -> None:
        self.latencies.update(drives.latencies.get(rc_id, {}))
        self.costs.update((drives.costs or {}).get(rc_id, {}))
        self.failures.update(drives.failures.get(rc_id, {}))


def timings_of(drives: DrivePass, rc_id: str) -> ArmTimings:
    arm = ArmTimings()
    arm.add(drives, rc_id)
    return arm


def compare_arms(
    reference: ArmTimings, candidate: ArmTimings, item_ids: Sequence[str], split: str
) -> tuple[LatencyComparison, LatencyComparison]:
    """(latency, cost) of `candidate` against `reference` on `item_ids`,
    each knowing its planned count and why every missing pair is missing."""
    ids = set(item_ids)
    dropped = {
        item: reference.failures.get(item)
        or candidate.failures.get(item)
        or "no latency on the returned run"
        for item in item_ids
        if item not in reference.latencies or item not in candidate.latencies
    }

    def only(values: Mapping[str, float]) -> dict[str, float]:
        return {k: v for k, v in values.items() if k in ids}

    latency = compare_latencies(
        only(reference.latencies),
        only(candidate.latencies),
        method="paired_drive",
        split=split,
        planned=len(item_ids),
        dropped=dropped,
    )
    cost = compare_latencies(
        only(reference.costs),
        only(candidate.costs),
        method="paired_drive",
        split=split,
        planned=len(item_ids),
        dropped=dropped,
        metric="cost_usd",
    )
    return latency, cost


@dataclass
class PairedPass:
    """A paired drive pass's result: per candidate, the latency comparison
    and the per-request cost comparison (same drives), and the spend."""

    latency: dict[str, LatencyComparison]
    cost: dict[str, LatencyComparison]
    spend: float


async def paired_drive_pass(
    ctx: Ctx,
    baseline_rc_id: str,
    candidate_rc_ids: Sequence[str],
    inputs: Sequence[Any],
    split: str,
    label: str,
) -> PairedPass:
    """Drive the baseline and every candidate on the same items in the same
    window: per item, the arms run back to back, and the order rotates from
    item to item so neither arm always goes first. Per candidate, the
    latency AND the per-request cost of the task call are compared, from the
    same runs. No judge runs. Each comparison knows its planned count and
    why every missing pair is missing; under `paired_floor` it is
    `incomplete`."""
    drives = await timed_drives(
        ctx, [baseline_rc_id, *candidate_rc_ids], inputs, label, split
    )
    latency: dict[str, LatencyComparison] = {}
    cost: dict[str, LatencyComparison] = {}
    ids = [i.id for i in inputs]
    reference = timings_of(drives, baseline_rc_id)
    for rc in candidate_rc_ids:
        latency[rc], cost[rc] = compare_arms(
            reference, timings_of(drives, rc), ids, split
        )
    return PairedPass(latency, cost, drives.cost)


@dataclass
class DrivePass:
    """What a judge-free drive pass returned: rc id -> item id -> task
    latency (ms), rc id -> item id -> why that drive has no latency (after
    its one retry), the provider cost, and how many items were planned."""

    latencies: dict[str, dict[str, float]]
    failures: dict[str, dict[str, str]]
    cost: float
    planned: int
    outputs: dict[str, dict[str, Any]] | None = None  # rc -> item -> output
    costs: dict[str, dict[str, float]] | None = None  # rc -> item -> $/request
    # rc -> item -> `levers.lever_evidence` of that drive
    evidence: dict[str, dict[str, dict[str, Any]]] | None = None


def _drive_error_text(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    status = getattr(exc, "status_code", None)
    if isinstance(body, Mapping):
        text = str(body.get("message") or body.get("detail") or body)
        kind = body.get("error_type")
        text += f" ({kind})" if kind else ""
    else:
        text = str(body if body is not None else exc)
    return f"HTTP {status}: {text}" if status else text


async def timed_drives(
    ctx: Ctx,
    arms: Sequence[str],
    inputs: Sequence[Any],
    label: str,
    split: str = "train",
) -> DrivePass:
    """Judge-free drives of every arm (a saved run config) on every input,
    the arms back to back per item with the order rotating. A failed drive
    is retried ONCE; one still failing is recorded with its reason.

    When the task needs it (`EvalJobDriver.needed`: a multi-turn task, an
    OpenEnv tool, an input with an `openenv_reset`), each drive is a one-item
    eval job on the input's eval and `split`, and its usage is read from
    the task run the job drove. The job's errors carry the provider's
    text, so no diagnosis job follows.

    Quota and billing failures stop the run (`ProviderQuotaError`): a drive
    error whose text names one raises at once, never retried. The studio
    server answers a failed run with a generic 500 ("An unexpected error
    occurred.") and keeps the provider's text in its log, so when drives
    still fail after the retry, one failed (rc, item) is re-run as a one-item
    eval job — whose job errors DO carry the provider's text — and that is
    classified the same way (`Analyzer.diagnose_drive_failure`)."""
    from kiln_harness_tuner.job_drives import driver_for, eval_of_input
    from kiln_harness_tuner.kiln import KilnError
    from kiln_harness_tuner.levers import lever_evidence
    from kiln_harness_tuner.probes import rc_props
    from kiln_harness_tuner.provider_errors import (
        ProviderQuotaError,
        classify_provider_message,
    )
    from kiln_harness_tuner.schemas import ErrorInfo

    arms = list(arms)
    props = {rc: await rc_props(ctx, rc) for rc in arms}
    driver = driver_for(ctx)
    via_jobs = await driver.needed(props.values(), inputs)
    latencies: dict[str, dict[str, float]] = {rc: {} for rc in arms}
    failures: dict[str, dict[str, str]] = {rc: {} for rc in arms}
    cost = 0.0
    sem = asyncio.Semaphore(MAX_CONCURRENT_LATENCY_DRIVES)

    outputs: dict[str, dict[str, Any]] = {rc: {} for rc in arms}
    costs: dict[str, dict[str, float]] = {rc: {} for rc in arms}
    evidence: dict[str, dict[str, dict[str, Any]]] = {rc: {} for rc in arms}

    def stop_on_quota(
        rc: str, inp: Any, text: str, cause: Exception | None = None
    ) -> None:
        """Quota and billing failures stop the run, never retried."""
        error_class = classify_provider_message(text)
        if error_class in ("quota_exhausted", "auth"):
            raise ProviderQuotaError(
                ErrorInfo(
                    error_class=error_class,  # type: ignore[arg-type]
                    detail=f"drive of rc {rc} on {inp.id}: {text}",
                )
            ) from cause

    async def run_by_job(rc: str, inp: Any) -> tuple[dict[str, Any] | None, float, str]:
        eval_id = await eval_of_input(ctx, inp, split)
        if eval_id is None:
            return None, 0.0, f"{inp.id} is in no eval's {split} split"
        outcome = await driver.drive(rc, inp.id, eval_id, split)
        if outcome.run is None:
            text = outcome.error or "the eval job returned no drive"
            stop_on_quota(rc, inp, text)
            return None, outcome.overhead_cost, text
        return outcome.run, outcome.overhead_cost, ""

    async def drive_once(rc: str, inp: Any) -> tuple[float | None, float, str]:
        if via_jobs:
            run, overhead, text = await run_by_job(rc, inp)
            if run is None:
                return None, overhead, text
            return record(rc, inp, run, overhead)
        body: dict[str, Any] = {
            "run_config_properties": props[rc],
            "tags": [LATENCY_RUN_TAG, f"{LATENCY_RUN_TAG}_{label}"],
            "task_run_config_id": rc,
        }
        if inp.structured is not None:
            body["structured_input"] = inp.structured
        else:
            body["plaintext_input"] = inp.plaintext or ""
        try:
            run = await ctx.kiln.call(
                "POST",
                f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}/run",
                body=body,
                timeout_secs=DRIVE_TIMEOUT_SECS,
                retries=False,
            )
        except KilnError as exc:
            text = _drive_error_text(exc)
            stop_on_quota(rc, inp, text, cause=exc)
            return None, 0.0, text
        return record(rc, inp, run if isinstance(run, dict) else {}, 0.0)

    def record(
        rc: str, inp: Any, run: dict[str, Any], overhead: float
    ) -> tuple[float | None, float, str]:
        """Keep one drive's output, evidence and per-request cost. The
        spend is the task call's cost plus `overhead` (a synthetic user's
        or a judge's), which is never the per-request metric."""
        value = latency_of_task_run(run)
        outputs[rc][inp.id] = run_output(run)
        evidence[rc][inp.id] = lever_evidence(run)
        if run_cost(run) > 0:
            costs[rc][inp.id] = run_cost(run)
        why = "" if value else "no latency on the returned run"
        return value, run_cost(run) + overhead, why

    async def drive(rc: str, inp: Any) -> None:
        nonlocal cost
        value, spent, why = await drive_once(rc, inp)
        cost += spent
        if value is None:
            await ctx.sleep(DRIVE_RETRY_SLEEP_SECS)
            value, spent, why = await drive_once(rc, inp)
            cost += spent
        if value is None:
            failures[rc][inp.id] = f"{why} (after one retry)"
        else:
            latencies[rc][inp.id] = value

    async def one_item(index: int, inp: Any) -> None:
        order = arms[index % len(arms) :] + arms[: index % len(arms)]
        async with sem:
            for rc in order:
                await drive(rc, inp)

    await asyncio.gather(*(one_item(i, inp) for i, inp in enumerate(inputs)))
    failed = [(rc, item) for rc in arms for item in failures[rc]]
    if failed and not via_jobs:
        rc, item = failed[0]
        diagnosis = await ctx.analyzer.diagnose_drive_failure(
            ctx.target.project_path, rc, item
        )
        if diagnosis:
            for rc_failures in failures.values():
                for key in rc_failures:
                    rc_failures[key] += f"; diagnosed on {item}: {diagnosis[:160]}"
    return DrivePass(latencies, failures, cost, len(inputs), outputs, costs, evidence)


def run_cost(run: Mapping[str, Any]) -> float:
    """A task run's provider cost from its own `usage`."""
    usage = run.get("usage")
    value = usage.get("cost") if isinstance(usage, Mapping) else None
    return float(value) if isinstance(value, (int, float)) else 0.0
