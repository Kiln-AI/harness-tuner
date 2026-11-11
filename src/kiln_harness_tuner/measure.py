"""One measurement path with early stopping.

`measure(ctx, candidates, spec)` is the only way the loop measures a
candidate run config on train. It replaces five special cases: the viability
stage, the 30-item judge-free timing pass, the residual-minibatch gate, the
planner-named target trial, and the screening round's own measurement. An
arm's measurement that COMPLETES is its train measurement; nothing measures
it again. The val gate stays separate (the held-out decision).

**The queue**, drawn once per candidate set from TRAIN only, seeded and
recorded (`queue_drawn` event):

1. the items the planner named (`target_items`), in its order;
2. the DISCRIMINATING block: up to `DISC_FAILS` items the parent fails on the
   targeted evals and up to `DISC_GUARDS` items it passes, round-robin across
   the evals — the parent's failures are where a targeted change shows, and
   the passes are the guards where a regression shows;
3. the REMAINDER: per eval the rest of the eval's train items, the parent's
   failures, passes and unscored items interleaved in proportion (each
   stratum in a seeded random order), round-robin across the evals.

Every stratum is consumed in its own random order, so the judged items of a
stratum are always a simple random sample of it, and the stratified estimate
(`kiln_harness_tuner.estimate`) is unbiased at any stopping point. The hard-first
order is for early detection only; no pass-rate figure is a plain mean over
the queue.

**Under speed and cost** the question is whether the candidate
keeps what the parent passes. So the discriminating block is GUARD-FIRST:
up to `DISC_GUARDS_EFFICIENCY` items the parent passes, then up to
`DISC_FAILS` it fails, round-robin across the evals. The passes are taken
most FRAGILE first: an item's fragility is the share of the other run
configs with a stored train row on it (this eval config, from the project
on disk) that fail it, with one passing config added so that an item one
config judged does not outrank one twenty configs judged. Ties keep the
seeded order, and with no stored row of another config the order is the
seeded one. The fragility is recorded in `queue_drawn`. The pass stratum is
then not a simple random sample at an early stop, so a stopped
measurement's `net` leans toward a loss; the verdict there reads the losses
(`stop_rules.guard_losses`), not `net`. A candidate can carry `min_guards`
(a model or provider swap in a screening round): its own per-eval cap is
raised until it holds that many of the parent's passes (`guard_cap`), and
the other candidates of the round keep the round's size.

**Chunks.** Each chunk judges the next items of the queue (the first chunk
is the named items and the discriminating block, the later ones
`JUDGE_CHUNK` items): an eval job per targeted eval narrowed to those items
(`Analyzer.trial`), rows stored and reused by any later measurement. The
parent's judgments are its stored rows — never re-driven. When timing or
cost matter (a speed or cost objective, a screening round, a model-level
swap whose lever needs checking), each chunk also drives the next
`TIMING_CHUNK` items judge-free, up to `TIMING_MAX` pairs: the candidate,
the reference (the run's baseline) and the candidate's parent when that is
another run config, back to back per item in one window. Under the quality
objective any other arm is not timed (there is no per-request ceiling).

**Rules** (`kiln_harness_tuner.stop_rules`) after every chunk: the objective's
defaults merged with the planner's per-candidate rules. The first that
fires stops the candidate with its numbers. One progress line per candidate
per chunk (`measure_chunk`):

    w7-1: chunk 3/9 · 24 judged · +3/−0 vs baseline · 0.88× latency · continue

**Outcomes.** `stopped` (a rule fired: its kind and line), `capped` (a
budget or a screening round's size ran out: not a verdict), `failed` (no
row at all — infra), or `completed` (every item of the queue judged).
"""

from __future__ import annotations

import asyncio
import json
import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean
from typing import TYPE_CHECKING, Any

from kiln_harness_tuner.analysis import (
    AnalysisError,
    MeasurementFailed,
    _run_cost,
    _runs_by_item,
    expand_targets,
    normalize,
)
from kiln_harness_tuner.budget import (
    Budget,
    booked_spend,
    expected_full_usd,
    held_budget,
    median_or_none,
    remaining_run_budget,
    resolve_budget,
)
from kiln_harness_tuner.estimate import (
    FAIL,
    NAMED,
    PASS,
    UNSCORED,
    Estimate,
    Judged,
    estimate,
)
from kiln_harness_tuner.latency import (
    SPEED_SAMPLE_ITEMS,
    compare_latencies,
    output_valid,
    split_inputs,
    task_cost_of_eval_run,
    task_output_schema,
    timed_drives,
)
from kiln_harness_tuner.objective import is_efficiency
from kiln_harness_tuner.schemas import (
    ErrorInfo,
    EvalTarget,
    LatencyComparison,
    ResidualGateResult,
    ResidualItem,
    StopRule,
    dump_or_none,
)
from kiln_harness_tuner.stop_rules import (
    RESIDUAL_MIN_FIXED_SHARE,
    TIMING_KINDS,
    Evidence,
    Stop,
    capped_line,
    first_stop,
    guard_breaks,
    quality_looks,
    rule_text,
    run_budget_line,
)

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

JUDGE_CHUNK = 12
DISC_FAILS = 8
DISC_GUARDS = 4
# The guards of the discriminating block under speed and cost.
# As many as a model-level swap's minimum (`viability.SWAP_MIN_GUARDS`), so
# that a capped screen is all guards.
DISC_GUARDS_EFFICIENCY = 24
TIMING_CHUNK = 15
TIMING_MAX = SPEED_SAMPLE_ITEMS  # 30 pairs
BLOCK_NAMED, BLOCK_DISC, BLOCK_REST = "named", "discriminating", "remainder"


# ------------------------------------------------------------ the queue


@dataclass(frozen=True)
class QItem:
    eval_id: str
    item_id: str
    stratum: str
    block: str
    parent_pass: bool | None
    fragility: float = 0.0


@dataclass
class Queue:
    seed: str
    parent_rc_id: str
    items: list[QItem] = field(default_factory=list)
    # eval -> stratum -> items of the eval's whole train split (the
    # estimator's weights, whatever the cap)
    population: dict[str, dict[str, int]] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    targets: dict[str, EvalTarget] = field(default_factory=dict)
    eval_names: dict[str, str] = field(default_factory=dict)
    # The queue's order BEFORE any cap: what the timing drives take, so a
    # small screening round is still timed on enough pairs.
    timing_order: list[str] = field(default_factory=list)
    # The spending cap's prior (`kiln_harness_tuner.budget`): per eval,
    # the parent's median cost per judged item (drive + judge), and its
    # median cost per request.
    item_price: dict[str, float | None] = field(default_factory=dict)
    drive_price: float | None = None
    # A guard-first queue only: eval -> item -> [configs that
    # fail it, configs with a row on it], other run configs' stored train
    # rows, for the items of the discriminating block.
    fragility: dict[str, dict[str, list[int]]] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "parent_rc_id": self.parent_rc_id,
            "population": self.population,
            "skipped": self.skipped,
            "items": [[q.eval_id, q.item_id, q.stratum, q.block] for q in self.items],
        }


def _train_ids(ctx: Ctx, eval_id: str) -> set[str] | None:
    """The eval's train item ids from the project on disk, or None."""
    from kiln_harness_tuner.quarantine import membership, task_dirs

    try:
        ids: set[str] = set()
        for task_dir in task_dirs(Path(ctx.target.project_path), ctx.target.task_id):
            member, _loaded = membership(task_dir)
            splits = member.by_eval.get(eval_id) or {}
            ids |= {i for _source, i in splits.get("train", ())}
        return ids or None
    except Exception:
        return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _with_id(folder: Path, filename: str, wanted: str) -> list[Path]:
    """The child folders of `folder` whose `filename` names `wanted` as id."""
    if not folder.is_dir():
        return []
    return [
        child
        for child in sorted(folder.iterdir())
        if (data := _read_json(child / filename)) is not None
        and str(data.get("id")) == wanted
    ]


def stored_rows_on_disk(
    ctx: Ctx, eval_id: str, config_id: str
) -> list[tuple[str, str, dict[str, Any]]]:
    """(run config id, item id, scores) of every stored row of one eval
    config, read from the project on disk. One pass over the
    config's run folders, not one server read per run config. A row file is
    read once per run (`Ctx.eval_run_index`): a stored row does not change."""
    from kiln_harness_tuner.quarantine import task_dirs

    index: dict[str, Any] = getattr(ctx, "eval_run_index", None)
    if index is None:
        index = {}
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for task_dir in task_dirs(Path(ctx.target.project_path), ctx.target.task_id):
        for eval_dir in _with_id(task_dir / "evals", "eval.kiln", eval_id):
            for config_dir in _with_id(
                eval_dir / "configs", "eval_config.kiln", config_id
            ):
                runs = config_dir / "runs"
                if not runs.is_dir():
                    continue
                for run_dir in runs.iterdir():
                    key = str(run_dir / "eval_run.kiln")
                    if key not in index:
                        data = _read_json(run_dir / "eval_run.kiln")
                        if data is None:
                            continue  # not written yet, or unreadable: next time
                        item = data.get("dataset_id") or data.get("eval_input_id")
                        scores = data.get("scores")
                        index[key] = (
                            (str(data.get("task_run_config_id")), str(item), scores)
                            if item
                            and data.get("task_run_config_id")
                            and isinstance(scores, dict)
                            and not data.get("eval_config_eval")
                            else None
                        )
                    if index[key] is not None:
                        rows.append(index[key])
    return rows


def fragility_counts(
    rows: Sequence[tuple[str, str, dict[str, Any]]],
    spec: Any,
    items: set[str],
    exclude: set[str],
) -> dict[str, list[int]]:
    """item -> [run configs that fail it, run configs with a row on it],
    over `items` (the eval's train items) and every run config not in
    `exclude`. A config's replicates on one item count once, at their mean."""
    values: dict[str, dict[str, list[float]]] = {}
    for rc_id, item_id, scores in rows:
        value = scores.get(spec.key)
        if rc_id in exclude or item_id not in items or value is None:
            continue
        values.setdefault(item_id, {}).setdefault(rc_id, []).append(float(value))
    return {
        item_id: [
            sum(1 for v in by_rc.values() if normalize(fmean(v), spec) < 0.5),
            len(by_rc),
        ]
        for item_id, by_rc in values.items()
    }


def fragility(counts: Sequence[int] | None) -> float:
    """The share of the configs that fail an item, with one passing config
    added: 1 of 1 reads 0.50, 9 of 20 reads 0.43, no row reads 0."""
    if not counts:
        return 0.0
    failed, configs = counts
    return failed / (configs + 1)


def _interleave(strata: dict[str, list[str]]) -> list[tuple[str, str]]:
    """(stratum, item) with each stratum's share of every prefix as close to
    its share of the whole as possible (largest remainder)."""
    total = sum(len(v) for v in strata.values())
    taken = {s: 0 for s in strata}
    out: list[tuple[str, str]] = []
    for t in range(total):
        best = max(
            (s for s in strata if taken[s] < len(strata[s])),
            key=lambda s: len(strata[s]) * (t + 1) / total - taken[s],
        )
        out.append((best, strata[best][taken[best]]))
        taken[best] += 1
    return out


def _round_robin(per_eval: dict[str, list[Any]]) -> list[Any]:
    queues = {e: list(v) for e, v in sorted(per_eval.items())}
    out: list[Any] = []
    while any(queues.values()):
        for e in sorted(queues):
            if queues[e]:
                out.append(queues[e].pop(0))
    return out


async def draw_queue(
    ctx: Ctx,
    parent_rc_id: str,
    targets: Sequence[EvalTarget],
    seed: str,
    *,
    named: Sequence[str] = (),
    per_eval_cap: int | None = None,
    hard_only: bool = False,
    discriminating: bool = True,
    objective: str = "quality",
    exclude_rcs: Sequence[str] = (),
) -> Queue:
    """The queue (see the module doc). The parent's train rows are measured
    first when they are not all stored yet (`ensure_measured`: a stored row
    is never re-driven); an eval that cannot be read is skipped, with why.
    Under speed and cost the discriminating block is guard-first, by
    fragility over the stored rows of every run config but the parent and
    `exclude_rcs` (the candidates)."""
    from kiln_harness_tuner.orchestrator import _active_evals

    guard_first = is_efficiency(objective)
    queue = Queue(seed=seed, parent_rc_id=parent_rc_id)
    frag: dict[tuple[str, str], float] = {}
    counts: dict[str, dict[str, list[int]]] = {}
    configs = {str(e["id"]): c for e, c in await _active_evals(ctx)}
    strata: dict[str, dict[str, list[str]]] = {}
    drive_costs: list[float] = []
    parent_of: dict[tuple[str, str], bool | None] = {}
    for target in targets:
        eval_id = target.eval_id
        if eval_id not in configs:
            queue.skipped[eval_id] = "not an active eval with a current config"
            continue
        try:
            eval_def = await ctx.analyzer.fetch_eval(eval_id)
            spec = expand_targets(eval_def, target)[0]
            rows, _health = await ctx.analyzer.ensure_measured(
                eval_id, configs[eval_id], parent_rc_id, "train"
            )
            scored: dict[str, bool] = {}
            for item_id, row in _runs_by_item(rows).items():
                value = (row.get("scores") or {}).get(spec.key)
                if value is not None:
                    scored[item_id] = normalize(float(value), spec) >= 0.5
        except (AnalysisError, MeasurementFailed) as exc:
            # e.g. a score with no pass/fail mapping: the eval is skipped,
            # and the reason goes on the outcome.
            queue.skipped[eval_id] = f"unreadable ({exc})"[:200]
            continue
        queue.targets[eval_id] = target
        queue.eval_names[eval_id] = str(eval_def.get("name") or eval_id)
        parent_rows = _runs_by_item(rows).values()
        queue.item_price[eval_id] = median_or_none([_run_cost(r) for r in parent_rows])
        drive_costs += [
            c for r in parent_rows if (c := task_cost_of_eval_run(r)) is not None
        ]
        # The split on disk, plus any row the server stored on train (a
        # row is the server's word that the item is in the split).
        universe = (_train_ids(ctx, eval_id) or set()) | set(scored)
        named_here = [i for i in named if i in universe]
        rng = random.Random(f"{seed}:{eval_id}")
        groups = {
            FAIL: sorted(i for i in universe if scored.get(i) is False),
            PASS: sorted(i for i in universe if scored.get(i) is True),
            UNSCORED: sorted(i for i in universe if i not in scored),
        }
        for s in (FAIL, PASS, UNSCORED):
            groups[s] = [i for i in groups[s] if i not in named_here]
            rng.shuffle(groups[s])
        if guard_first:
            # Most fragile pass first; a stable sort keeps the seeded order
            # among equals. A disk that cannot be read leaves the seeded
            # order, which is the order with no other config's row.
            try:
                counts[eval_id] = fragility_counts(
                    stored_rows_on_disk(ctx, eval_id, configs[eval_id]),
                    spec,
                    set(universe),
                    {parent_rc_id, *exclude_rcs},
                )
            except Exception as exc:
                counts[eval_id] = {}
                ctx.log.event(
                    "fragility_unavailable", eval_id=eval_id, error=repr(exc)[:200]
                )
            for i in groups[PASS]:
                frag[(eval_id, i)] = fragility(counts[eval_id].get(i))
            groups[PASS].sort(key=lambda i: -frag[(eval_id, i)])  # noqa: B023
        strata[eval_id] = groups
        for i in universe:
            parent_of[(eval_id, i)] = scored.get(i)
        queue.population[eval_id] = {
            FAIL: len(groups[FAIL]),
            PASS: len(groups[PASS]),
            UNSCORED: len(groups[UNSCORED]),
            NAMED: len(named_here),
        }
        for item_id in named_here:
            queue.items.append(
                QItem(eval_id, item_id, NAMED, BLOCK_NAMED, scored.get(item_id))
            )
    # The discriminating block: under quality the parent's failures, then a
    # few passes; under speed and cost the passes first, then the failures.
    if discriminating:
        block = (
            ((PASS, DISC_GUARDS_EFFICIENCY), (FAIL, DISC_FAILS))
            if guard_first
            else ((FAIL, DISC_FAILS), (PASS, DISC_GUARDS))
        )
        for stratum, cap in block:
            taken = 0
            while taken < cap and any(strata[e][stratum] for e in strata):
                for e in sorted(strata):
                    if taken < cap and strata[e][stratum]:
                        item_id = strata[e][stratum].pop(0)
                        queue.items.append(
                            QItem(
                                e,
                                item_id,
                                stratum,
                                BLOCK_DISC,
                                stratum == PASS,
                                frag.get((e, item_id), 0.0),
                            )
                        )
                        taken += 1
                        if guard_first and item_id in counts.get(e, {}):
                            queue.fragility.setdefault(e, {})[item_id] = counts[e][
                                item_id
                            ]
    if not hard_only:
        rest = {
            e: [
                QItem(e, i, s, BLOCK_REST, parent_of.get((e, i)), frag.get((e, i), 0.0))
                for s, i in _interleave(groups)
            ]
            for e, groups in strata.items()
        }
        queue.items += _round_robin(rest)
    uncapped = list(queue.items)
    if hard_only and not uncapped:
        uncapped = []
    queue.timing_order = list(dict.fromkeys(q.item_id for q in uncapped))
    if hard_only:
        # Timing is not limited to the discriminating block.
        queue.timing_order += [
            i
            for groups in strata.values()
            for s in (FAIL, PASS, UNSCORED)
            for i in groups[s]
            if i not in queue.timing_order
        ]
    queue.drive_price = median_or_none(drive_costs)
    if per_eval_cap is not None:
        queue.items = cap_items(queue.items, per_eval_cap)
    return queue


def cap_items(items: Sequence[QItem], per_eval_cap: int) -> list[QItem]:
    """The first `per_eval_cap` items of each eval, in queue order."""
    count: dict[str, int] = {}
    capped: list[QItem] = []
    for q in items:
        if count.get(q.eval_id, 0) < per_eval_cap:
            capped.append(q)
            count[q.eval_id] = count.get(q.eval_id, 0) + 1
    return capped


def guard_cap(
    items: Sequence[QItem], base: int, min_guards: int, max_items: int
) -> int:
    """The per-eval cap of a candidate that must be judged on `min_guards`
    of the parent's passes: the smallest cap from `base` up
    whose items hold that many passes, or every pass the queue holds. The
    cap is not raised past `max_items` items; it is never below `base`."""
    want = min(min_guards, sum(1 for q in items if q.parent_pass is True))
    longest = max(
        (sum(1 for q in items if q.eval_id == e) for e in {q.eval_id for q in items}),
        default=0,
    )
    cap = base
    while cap < longest:
        if sum(1 for q in cap_items(items, cap) if q.parent_pass is True) >= want:
            break
        if len(cap_items(items, cap + 1)) > max_items:
            break
        cap += 1
    return cap


# ------------------------------------------------------------ the engine


@dataclass
class Candidate:
    rc_id: str
    label: str
    parent_rc_id: str  # the run config it was forked from
    # The planner's spending cap, and why (`kiln_harness_tuner.budget`);
    # None = code's default.
    max_usd: float | None = None
    budget_reason: str = ""
    # Judge at least this many of the parent's passes, raising
    # this candidate's own per-eval cap (`guard_cap`) up to
    # `min_guards_max_items` items; 0 = the spec's cap. Only a capped queue
    # (a screening round) reads it.
    min_guards: int = 0
    min_guards_max_items: int = 0


@dataclass
class MeasureSpec:
    targets: list[EvalTarget]
    rules: list[StopRule]
    seed: str
    queue_parent_rc_id: str  # whose stored results order the queue
    reference_rc_id: str  # the run's baseline: the timing reference
    objective: str = "quality"
    full: bool = True  # the queue is the whole targeted split
    per_eval_cap: int | None = None
    hard_only: bool = False
    named: list[str] = field(default_factory=list)
    timing: bool = False
    looks: int | None = None  # a fixed number of chunks (a screening round)
    source: str = "measurement"
    item_id: str | None = None  # the plan item, for events
    progress: Callable[[str, str], None] | None = None  # (rc id, line)


@dataclass
class Measured:
    """One candidate's measurement."""

    rc_id: str
    label: str
    status: str  # "completed" | "stopped" | "capped" | "failed"
    evidence: Evidence
    stop_kind: str | None = None
    stop_line: str = ""
    stop: Stop | None = None  # the rule's hit (its n and numbers)
    error: ErrorInfo | None = None
    latency: LatencyComparison | None = None
    cost: LatencyComparison | None = None
    vs_parent: LatencyComparison | None = None
    vs_parent_cost: LatencyComparison | None = None
    lever_note: str = ""
    spend_usd: float = 0.0
    chunks: int = 0
    planned_chunks: int = 0
    estimate: Estimate | None = None
    per_eval: dict[str, Estimate] = field(default_factory=dict)
    residual: ResidualGateResult | None = None
    line: str = ""
    body: str = ""  # the line without its verdict
    budget: Budget | None = None
    cost_per_item_usd: float | None = None  # measured, drive + judge
    expected_full_usd: float | None = None  # the whole queue at that price
    # A `min_guards` candidate's minimum, as its own queue can
    # hold it (min(min_guards, the parent's passes in that queue)).
    guards_required: int | None = None
    # Per eval, the items this candidate set out to judge.
    planned_per_eval: dict[str, int] = field(default_factory=dict)

    @property
    def verdict_stop(self) -> bool:
        """Stopped by a rule on the evidence (not a budget, not infra)."""
        return self.status == "stopped"

    @property
    def overview_detail(self) -> str:
        """The stop for a record overview, short enough to leave the
        hypothesis in the 280-char overview: "stopped at n=8 by not_better:
        <numbers>" (the rule's wording is in the wave log). A `targets` stop
        keeps the pre-engine shape "<k>/<n> targets fixed"."""
        hit = self.stop
        if hit is None:
            return self.stop_line
        if self.stop_kind in ("max_usd", "max_items"):
            return hit.numbers
        if self.stop_kind == "targets":
            named = [r for r in self.evidence.judged if r.block == BLOCK_NAMED]
            fixed = sum(1 for r in named if r.cand_pass)
            return f"{fixed}/{len(named)} targets fixed"
        return f"stopped at n={hit.n} by {hit.rule.kind}: {hit.numbers}"

    @property
    def summary(self) -> str:
        """The verdict first, for a record overview (clipped by memory)."""
        if self.status == "stopped":
            return self.stop_line
        if self.status == "failed" and self.error is not None:
            return f"measurement failed: {self.error.detail}"
        return self.line


@dataclass
class _State:
    cand: Candidate
    ev: Evidence
    status: str = "live"
    stop: Stop | None = None
    stop_kind: str | None = None
    stop_line: str = ""
    error: ErrorInfo | None = None
    spend: float = 0.0
    lever_note: str = ""
    evidence_seen: dict[str, dict[str, Any]] = field(default_factory=dict)
    lever_checked: bool = False
    budget: Budget | None = None
    cap_rule: StopRule | None = None
    item_costs: list[float] = field(default_factory=list)
    timing_spend: float = 0.0
    pairs_done: int = 0
    chunks: int = 0
    # the run budget left, net of the other candidates' holds, when this
    # candidate's measurement started (`kiln_harness_tuner.budget`, **Held**)
    remaining: float | None = None
    hold_key: tuple[object, int] = (None, 0)
    # The judged rows a `measure_chunk` event has already carried as cells.
    reported_n: int = 0

    @property
    def live(self) -> bool:
        return self.status == "live"


def _bounds(n_items: int, first: int, looks: int | None) -> list[int]:
    """Cumulative judged counts at the end of each chunk."""
    if n_items <= 0:
        return []
    if looks:
        size = max(1, math.ceil(n_items / looks))
        return [
            min(n_items, size * k)
            for k in range(1, looks + 1)
            if size * (k - 1) < n_items
        ]
    out = [min(n_items, first or JUDGE_CHUNK)]
    while out[-1] < n_items:
        out.append(min(n_items, out[-1] + JUDGE_CHUNK))
    return out


def _residual_result(
    ev: Evidence, seed: str, rules: Sequence[StopRule] = ()
) -> ResidualGateResult | None:
    """The discriminating block as the `residual` rule of `rules` reads it
    (its guard half against the noise rate); with no such rule,
    as the default rule at the default noise rate would."""
    block = [r for r in ev.judged if r.block == BLOCK_DISC]
    if not block:
        return None
    rule = next(
        (r for r in rules if r.kind == "residual"),
        StopRule(kind="residual", threshold=RESIDUAL_MIN_FIXED_SHARE),
    )
    test = guard_breaks(rule, [r for r in block if r.stratum != FAIL])
    items = [
        ResidualItem(
            eval_id=r.eval_id,
            item_id=r.item_id,
            role="failing" if r.stratum == FAIL else "guard",
            score=None if r.cand_pass is None else float(r.cand_pass),
        )
        for r in block
    ]
    fails = [i for i in items if i.role == "failing"]
    guards = [i for i in items if i.role == "guard"]
    fixed = sum(1 for i in fails if i.passes)
    broke = sum(1 for i in guards if i.broke)
    need = max(1, math.ceil(rule.threshold * len(fails) - 1e-9)) if fails else 0
    return ResidualGateResult(
        seed=seed,
        items=items,
        fixed=fixed,
        failing=len(fails),
        broke=broke,
        guards=len(guards),
        guards_unmeasured=sum(1 for i in guards if i.score is None),
        required=need,
        passed=(broke <= fixed if ev.efficiency else fixed >= need and not test.clear),
        noise_rate=test.p0,
        alpha=test.alpha,
        guard_p=test.p,
        guards_clear=test.clear,
    )


def progress_line(
    label: str, chunk: int, planned: int, ev: Evidence, decision: str
) -> str:
    """ "w7-1: chunk 3/9 · 24 judged · +3/−0 vs baseline · 0.88× latency ·
    continue"; under speed and cost the losses on the baseline's passes,
    and the fixes apart: "… · lost 1/20 passes, fixed 0/4 · …"."""
    fixed, broke = ev.fixed_broke()
    parts = [f"{label}: chunk {chunk}/{planned}", f"{ev.judged_n} judged"]
    if ev.no_rows:
        parts[1] = f"no row stored for {ev.judged_n} item(s)"
    elif ev.efficiency:
        lost, n, fixes, n_fails = ev.guard_losses()
        parts.append(f"lost {lost}/{n} passes, fixed {fixes}/{n_fails}")
    else:
        parts.append(f"+{fixed}/−{broke} vs baseline")
    for name, num, den in (
        ("latency", ev.latency, ev.ref_latency),
        ("cost", ev.cost, ev.ref_cost),
    ):
        ratios = [num[i] / den[i] for i in num if i in den and den[i] > 0]
        if ratios and (name == "latency" or ev.objective == "cost"):
            from statistics import median

            parts.append(f"{median(ratios):.2f}× {name}")
    parts.append(decision)
    return " · ".join(parts)


def chunk_counts(ev: Evidence, since: int) -> dict[str, Any]:
    """The counts a `measure_chunk` event carries beside its line: the
    judged items so far, and those the candidate fixed and broke against
    its queue parent. `cells` are the rows judged after the first `since`
    (this chunk's), in judging order, as `[eval_id, item_id, parent_pass,
    cand_pass]`. They tell an item that held from one that still fails.
    Under speed and cost also `guard_losses()`: `lost`, `guards`, `fixes`,
    `fails`."""
    fixed, broke = ev.fixed_broke()
    counts: dict[str, Any] = {
        "judged": ev.judged_n,
        "fixed": fixed,
        "broke": broke,
        "cells": [
            [r.eval_id, r.item_id, r.parent_pass, r.cand_pass]
            for r in ev.judged[since:]
        ],
    }
    if ev.efficiency:
        lost, guards, fixes, fails = ev.guard_losses()
        counts.update(lost=lost, guards=guards, fixes=fixes, fails=fails)
    return counts


def arm_estimates(m: Measured, item_id: str, parent_rc_id: str) -> dict[str, Any]:
    """The `arm_estimates` event's fields: an arm's train numbers when its
    measurement ends, however it ends. One `per_eval` row per eval with an
    estimate: the change in pass rate against the parent (`net`, its 95%
    interval), the candidate's pass rate, the items judged and planned,
    and the items fixed and broke. `pooled` is the estimate over every
    eval. The four comparisons are the engine's paired timing: `latency`
    and `cost` against the run's baseline, the `_vs_parent` pair only when
    the parent is another run config."""
    rows = []
    for eval_id, est in m.per_eval.items():
        low, high = est.ci()
        fixed, broke = m.evidence.fixed_broke(eval_id)
        rows.append(
            {
                "eval_id": eval_id,
                "eval_name": m.evidence.eval_names.get(eval_id, eval_id),
                "net": est.net,
                "ci_low": low,
                "ci_high": high,
                "rate": est.rate,
                "n": est.n,
                "complete": est.complete,
                "judged": sum(1 for r in m.evidence.judged if r.eval_id == eval_id),
                "planned": m.planned_per_eval.get(eval_id, 0),
                "fixed": fixed,
                "broke": broke,
            }
        )
    pooled = None
    if m.estimate is not None:
        low, high = m.estimate.ci()
        pooled = {
            "net": m.estimate.net,
            "ci_low": low,
            "ci_high": high,
            "n": m.estimate.n,
        }
    return {
        "item_id": item_id,
        "rc_id": m.rc_id,
        "parent_rc_id": parent_rc_id,
        "status": m.status,
        "stop_kind": m.stop_kind,
        "per_eval": rows,
        "pooled": pooled,
        "latency": dump_or_none(m.latency),
        "cost": dump_or_none(m.cost),
        "latency_vs_parent": dump_or_none(m.vs_parent),
        "cost_vs_parent": dump_or_none(m.vs_parent_cost),
        "spend_usd": m.spend_usd,
        "cap_usd": m.budget.usd if m.budget is not None else None,
    }


async def measure(
    ctx: Ctx, candidates: Sequence[Candidate], spec: MeasureSpec
) -> list[Measured]:
    """Measure `candidates` on the same queue, chunk by chunk (module doc).

    A candidate holds the unspent part of its budget while it is measured
    (`kiln_harness_tuner.budget`, **Held**); every hold this call makes is released
    when it returns, however it returns."""
    call = object()
    try:
        return await _measure(ctx, candidates, spec, call)
    finally:
        holds = getattr(ctx, "budget_holds", None)
        if holds is not None:
            for i in range(len(candidates)):
                holds.release((call, i))


async def _measure(
    ctx: Ctx, candidates: Sequence[Candidate], spec: MeasureSpec, call: object
) -> list[Measured]:
    from kiln_harness_tuner.levers import lever_not_applied, lever_verdict
    from kiln_harness_tuner.probes import rc_props

    # A candidate with `min_guards` takes its own, larger per-eval cap: the
    # queue is drawn whole, each candidate's cap is set on it, and the queue
    # is cut at the largest.
    grow = spec.per_eval_cap is not None and any(c.min_guards for c in candidates)
    queue = await draw_queue(
        ctx,
        spec.queue_parent_rc_id,
        spec.targets,
        spec.seed,
        named=spec.named,
        per_eval_cap=None if grow else spec.per_eval_cap,
        hard_only=spec.hard_only,
        objective=spec.objective,
        exclude_rcs=[c.rc_id for c in candidates],
    )
    own_cap: dict[str, int] = {}
    required: dict[str, int] = {}
    if grow:
        assert spec.per_eval_cap is not None
        for c in candidates:
            own_cap[c.rc_id] = (
                guard_cap(
                    queue.items,
                    spec.per_eval_cap,
                    c.min_guards,
                    c.min_guards_max_items or len(queue.items),
                )
                if c.min_guards
                else spec.per_eval_cap
            )
        whole = queue.items
        queue.items = cap_items(whole, max(own_cap.values()))
        for c in candidates:
            if c.min_guards:
                held = cap_items(whole, own_cap[c.rc_id])
                required[c.rc_id] = min(
                    c.min_guards, sum(1 for q in held if q.parent_pass is True)
                )
    ctx.log.event(
        "queue_drawn",
        item_id=spec.item_id,
        seed=spec.seed,
        size=len(queue.items),
        population=queue.population,
        skipped=queue.skipped,
        queue=queue.to_json()["items"],
        **({"fragility": queue.fragility} if is_efficiency(spec.objective) else {}),
        **({"per_eval_caps": own_cap} if own_cap else {}),
    )
    items = queue.items
    # Each candidate's own items (None: the whole queue).
    own_items: dict[str, set[tuple[str, str]] | None] = {
        c.rc_id: (
            {(q.eval_id, q.item_id) for q in cap_items(items, own_cap[c.rc_id])}
            if c.rc_id in own_cap and own_cap[c.rc_id] < max(own_cap.values())
            else None
        )
        for c in candidates
    }

    def mine(s: _State, qs: Sequence[QItem]) -> list[QItem]:
        own = own_items.get(s.cand.rc_id)
        if own is None:
            return list(qs)
        return [q for q in qs if (q.eval_id, q.item_id) in own]

    named_n = sum(1 for q in items if q.block == BLOCK_NAMED)
    disc_n = sum(1 for q in items if q.block == BLOCK_DISC)
    # The first chunk is the named items and the discriminating block. The
    # larger guard-first block (speed, cost) is not judged in one chunk: the
    # first chunk keeps the quality block's size, so a candidate the timing
    # stops is not paid a judge for 32 items first.
    lead = named_n + min(disc_n, DISC_FAILS + DISC_GUARDS)
    bounds = _bounds(len(items), lead, spec.looks)
    q_marks = list(bounds) if spec.looks else quality_looks(len(items))
    q_looks = max(1, len(q_marks))
    schema = await task_output_schema(ctx)
    driveable: dict[str, Any] = {}
    if spec.timing:
        try:
            driveable = {
                i.id: i
                for i in split_inputs(
                    ctx.target.project_path,
                    ctx.target.task_id,
                    "train",
                    list(queue.targets),
                )
            }
        except Exception:
            driveable = {}
    timing_order = [i for i in queue.timing_order if i in driveable]
    timing_planned = min(TIMING_MAX, len(timing_order))
    t_looks = max(1, math.ceil(timing_planned / TIMING_CHUNK)) if timing_planned else 1

    states: list[_State] = []
    for cand in candidates:
        ev = Evidence(
            objective=spec.objective,
            population=queue.population,
            primary_eval=spec.targets[0].eval_id if spec.targets else None,
            full=spec.full,
            timing_planned=timing_planned,
            timing_looks=t_looks,
            quality_looks=q_looks,
            eval_names=queue.eval_names,
            parent_latency={} if cand.parent_rc_id != spec.reference_rc_id else None,
            parent_cost={} if cand.parent_rc_id != spec.reference_rc_id else None,
        )
        state = _State(cand=cand, ev=ev, hold_key=(call, len(states)))
        if ctx.analyzer.rc_guard is not None:
            try:
                await ctx.analyzer.rc_guard(cand.rc_id)
            except AnalysisError as exc:
                state.status = "failed"
                state.error = getattr(exc, "error", None) or ErrorInfo(
                    error_class="provider_error", detail=str(exc)
                )
        states.append(state)
    if not items:
        for s in states:
            if s.live:
                s.status = "failed"
                s.error = ErrorInfo(
                    error_class="provider_error",
                    detail="no train item to measure on the targeted evals"
                    + (
                        f" ({'; '.join(f'{k}: {v}' for k, v in queue.skipped.items())})"
                        if queue.skipped
                        else ""
                    ),
                )
    parent_props: dict[str, dict[str, Any] | None] = {}
    declared: dict[str, dict[str, Any]] = {}
    for s in states:
        for rc in (s.cand.rc_id, s.cand.parent_rc_id):
            if rc not in parent_props:
                try:
                    parent_props[rc] = dict(await rc_props(ctx, rc))
                except Exception:
                    parent_props[rc] = None
        declared[s.cand.rc_id] = parent_props.get(s.cand.rc_id) or {}

    # ---- the spending cap (`kiln_harness_tuner.budget`): priced from the parent's
    # stored rows first, then from the candidate's own measured rows.
    items_per_eval: dict[str, int] = {}
    for q in items:
        items_per_eval[q.eval_id] = items_per_eval.get(q.eval_id, 0) + 1

    def own_per_eval(s: _State) -> dict[str, int]:
        """The candidate's own items per eval: a `min_guards` candidate's
        larger sample is priced in its budget."""
        counts: dict[str, int] = {}
        for q in mine(s, items):
            counts[q.eval_id] = counts.get(q.eval_id, 0) + 1
        return counts

    def per_item_price(s: _State) -> dict[str, float | None]:
        own = median_or_none(s.item_costs)
        return {
            e: own if own is not None else queue.item_price.get(e)
            for e in items_per_eval
        }

    def per_pair_price(s: _State) -> float | None:
        if s.pairs_done:
            return s.timing_spend / s.pairs_done
        return 2 * queue.drive_price if queue.drive_price is not None else None

    def expected(s: _State) -> float | None:
        return expected_full_usd(
            per_item_price(s), own_per_eval(s), timing_planned, per_pair_price(s)
        )

    def set_budget(s: _State) -> None:
        """A planner's cap is fixed once; code's default is re-priced."""
        if s.budget is not None and s.budget.source == "planner":
            return
        s.budget = resolve_budget(
            s.cand.max_usd, s.cand.budget_reason, expected(s), s.remaining
        )
        s.cap_rule = StopRule(kind="max_usd", threshold=max(s.budget.usd, 1e-9))

    def cap(s: _State) -> None:
        assert s.budget is not None and s.cap_rule is not None
        hit = Stop(s.cap_rule, s.ev.judged_n, capped_line(s.budget.usd, s.ev), True)
        s.stop, s.stop_kind, s.status = hit, "max_usd", "capped"
        s.stop_line = hit.line(rule_text(s.cap_rule, s.ev))

    def cap_run(s: _State, left: float) -> None:
        rule = StopRule(kind="max_usd", threshold=max(left, 1e-9))
        hit = Stop(rule, s.ev.judged_n, run_budget_line(left, s.ev), True)
        s.stop, s.stop_kind, s.status = hit, "max_usd", "capped"
        s.stop_line = hit.line(f"the run budget ${ctx.budget_usd:.2f}")

    holds = getattr(ctx, "budget_holds", None)

    def update_hold(s: _State) -> None:
        if holds is None:
            return
        if s.live and s.budget is not None:
            holds.hold(s.hold_key, s.budget.usd - s.spend)
        else:
            holds.release(s.hold_key)

    # Each candidate sizes its budget against what the candidates before it
    # (in this call and in any measurement running beside it) do not hold,
    # then holds it.
    for s in states:
        s.remaining = remaining_run_budget(ctx)
        set_budget(s)
        update_hold(s)
        assert s.budget is not None
        line = f"{s.cand.label}: {s.budget.line}"
        ctx.log.event(
            "candidate_budget",
            item_id=spec.item_id,
            rc_id=s.cand.rc_id,
            usd=s.budget.usd,
            source=s.budget.source,
            line=line,
        )
        if spec.progress is not None:
            spec.progress(s.cand.rc_id, line)

    timed = 0
    q_mark_i = 0
    judged_so_far = 0
    for k, end in enumerate(bounds, start=1):
        live = [s for s in states if s.live]
        if not live:
            break
        started = list(live)
        for s in started:
            s.chunks += 1
        chunk = items[judged_so_far:end]
        judged_so_far = end
        chunk_spend = 0.0
        # The last chunk drives every timing pair still owed, so a queue with
        # fewer chunks than timing chunks is still timed in full.
        upto = timing_planned if end >= len(items) else timed + TIMING_CHUNK
        n_pairs = max(0, min(timing_planned, upto) - timed) if spec.timing else 0
        # ---- the cap, before the chunk is paid: its items at the current
        # price per item, and its pairs at the current price per pair.
        for s in live:
            prices = per_item_price(s)
            projected = sum(
                prices.get(q.eval_id) or 0.0 for q in mine(s, chunk)
            ) + n_pairs * (per_pair_price(s) or 0.0)
            assert s.budget is not None
            if s.spend + projected > s.budget.usd + 1e-9:
                cap(s)
            elif getattr(ctx, "budget_usd", None) is not None:
                # the run as a whole: the booked spend, the other candidates'
                # holds and this chunk stay inside --budget-usd
                left = (
                    ctx.budget_usd
                    - booked_spend(ctx)
                    - held_budget(ctx, excluding=s.hold_key)
                )
                if projected > left + 1e-9:
                    cap_run(s, max(0.0, left))
            update_hold(s)
        live = [s for s in live if s.live]
        # ---- timing: the next pairs, every arm in one window
        timing_look = False
        if n_pairs and live:
            batch = timing_order[timed : timed + n_pairs]
            timed += len(batch)
            timing_look = True
            parents = sorted(
                {s.cand.parent_rc_id for s in live} - {spec.reference_rc_id}
            )
            arms = [spec.reference_rc_id, *parents, *(s.cand.rc_id for s in live)]
            drives = await timed_drives(
                ctx,
                list(dict.fromkeys(arms)),
                [driveable[i] for i in batch],
                f"{(spec.item_id or 'measure').replace('-', '_')}_t{k}",
            )
            chunk_spend += drives.cost
            costs = drives.costs or {}
            outputs = drives.outputs or {}
            # Attribution (`kiln_harness_tuner.budget`): each candidate's own drives,
            # an equal share of the baseline's among the live candidates, an
            # equal share of its parent's among that parent's candidates,
            # and an equal share of what no arm's rows account for.
            own = {
                s.cand.rc_id: sum(costs.get(s.cand.rc_id, {}).values()) for s in live
            }
            ref_total = sum(costs.get(spec.reference_rc_id, {}).values())
            parent_total = {p: sum(costs.get(p, {}).values()) for p in parents}
            sharers = {
                p: sum(1 for s in live if s.cand.parent_rc_id == p) for p in parents
            }
            rest = max(
                0.0,
                drives.cost
                - sum(own.values())
                - ref_total
                - sum(parent_total.values()),
            )
            for s in live:
                ev = s.ev
                share = own[s.cand.rc_id] + (ref_total + rest) / len(live)
                if s.cand.parent_rc_id in parent_total:
                    share += (
                        parent_total[s.cand.parent_rc_id] / sharers[s.cand.parent_rc_id]
                    )
                s.spend += share
                s.timing_spend += share
                s.pairs_done += len(batch)
                ev.latency.update(drives.latencies.get(s.cand.rc_id, {}))
                ev.cost.update(costs.get(s.cand.rc_id, {}))
                ev.ref_latency.update(drives.latencies.get(spec.reference_rc_id, {}))
                ev.ref_cost.update(costs.get(spec.reference_rc_id, {}))
                if ev.parent_latency is not None:
                    ev.parent_latency.update(
                        drives.latencies.get(s.cand.parent_rc_id, {})
                    )
                    ev.parent_cost.update(costs.get(s.cand.parent_rc_id, {}))  # type: ignore[union-attr]
                produced = outputs.get(s.cand.rc_id, {})
                ev.outputs += len(produced)
                ev.invalid += sum(
                    1 for out in produced.values() if not output_valid(out, schema)
                )
                s.evidence_seen.update((drives.evidence or {}).get(s.cand.rc_id) or {})
                if not s.lever_checked:
                    s.lever_checked = True
                    parent_ev = (drives.evidence or {}).get(s.cand.parent_rc_id) or {}
                    reason = lever_not_applied(
                        declared[s.cand.rc_id],
                        s.evidence_seen,
                        parent_props.get(s.cand.parent_rc_id),
                        parent_ev,
                    )
                    if reason:
                        s.status = "stopped"
                        s.stop_kind = "lever_not_applied"
                        s.stop_line = reason
                        ctx.log.event(
                            "lever_not_applied",
                            item_id=spec.item_id,
                            rc_id=s.cand.rc_id,
                            line=f"{s.cand.label}: {reason} — stopped",
                        )
                        continue
                    verdict = lever_verdict(
                        declared[s.cand.rc_id],
                        s.evidence_seen,
                        parent_props.get(s.cand.parent_rc_id),
                        parent_ev,
                    )
                    if verdict.status == "unverified":
                        # Neither signal shows on these drives: logged, and
                        # the candidate goes on (the drives cannot tell).
                        s.lever_note = verdict.reason
                        ctx.log.event(
                            "lever_unverified",
                            item_id=spec.item_id,
                            rc_id=s.cand.rc_id,
                            line=f"{s.cand.label}: {verdict.reason}",
                        )
        # ---- the timing rules first: a candidate they stop is never judged
        # on this chunk's items.
        if timing_look:
            for s in [s for s in states if s.live]:
                ev = s.ev
                ev.spend_usd = s.spend
                ev.timing_look = True
                ev.quality_look = False
                ev.timing_done = timed >= timing_planned
                hit = first_stop([r for r in spec.rules if r.kind in TIMING_KINDS], ev)
                if hit is not None and not hit.budget:
                    s.stop = hit
                    s.stop_kind = hit.rule.kind
                    s.stop_line = hit.line(rule_text(hit.rule, ev))
                    s.status = "stopped"
        # ---- judging: the chunk's items, per eval, one narrowed job each
        live = [s for s in states if s.live]
        by_eval: dict[str, list[QItem]] = {}
        for q in chunk:
            by_eval.setdefault(q.eval_id, []).append(q)

        async def judge(s: _State, eval_id: str, qs: list[QItem]):
            try:
                trial = await ctx.analyzer.trial(
                    queue.targets[eval_id], s.cand.rc_id, [q.item_id for q in qs]
                )
            except MeasurementFailed:
                return s, eval_id, qs, None
            return s, eval_id, qs, trial

        asks = [
            (s, e, own)
            for s in live
            for e, qs in by_eval.items()
            if (own := mine(s, qs))
        ]
        results = await asyncio.gather(*(judge(s, e, qs) for s, e, qs in asks))
        asked = {s.cand.rc_id for s, _e, _qs in asks}
        stored_any: dict[str, bool] = {s.cand.rc_id: False for s in live}
        for s, _e, qs, trial in results:
            ev = s.ev
            if trial is not None:
                stored_any[s.cand.rc_id] = True
                chunk_spend += trial.spend_usd
                s.spend += trial.spend_usd
                s.item_costs += [
                    trial.item_costs[q.item_id]
                    for q in qs
                    if q.item_id in trial.item_costs
                ]
                for q in qs:
                    out = trial.outputs.get(q.item_id)
                    if out is not None:
                        ev.outputs += 1
                        ev.invalid += 0 if output_valid(out, schema) else 1
            for q in qs:
                score = trial.scores.get(q.item_id) if trial is not None else None
                ev.judged.append(
                    Judged(
                        eval_id=q.eval_id,
                        item_id=q.item_id,
                        stratum=q.stratum,
                        block=q.block,
                        parent_pass=q.parent_pass,
                        cand_pass=None if score is None else score >= 0.5,
                        fragility=q.fragility,
                    )
                )
        if chunk_spend:
            ctx.log.event(
                "spend",
                item_id=spec.item_id,
                source=spec.source,
                lane="provider",
                cost_usd=chunk_spend,
                evidence_cost_usd=chunk_spend,
            )
        # ---- rules
        quality_look = False
        while q_mark_i < len(q_marks) and end >= q_marks[q_mark_i]:
            quality_look = True
            q_mark_i += 1
        rest = items[end:]
        remaining: dict[str, dict[str, int]] = {}
        for q in rest:
            remaining.setdefault(q.eval_id, {}).setdefault(q.stratum, 0)
            remaining[q.eval_id][q.stratum] += 1
        for s in live:
            ev = s.ev
            if k == 1 and s.cand.rc_id in asked and not stored_any.get(s.cand.rc_id):
                s.status = "failed"
                s.error = ErrorInfo(
                    error_class="provider_error",
                    detail=f"the first chunk of {len(chunk)} item(s) stored no row"
                    f" for {s.cand.rc_id}",
                )
                continue
            ev.remaining = remaining
            ev.spend_usd = s.spend
            ev.quality_look = quality_look
            ev.timing_look = timing_look
            ev.timing_done = timed >= timing_planned
            ev.disc_done = all(q.block not in (BLOCK_DISC,) for q in rest)
            ev.named_done = all(q.block != BLOCK_NAMED for q in rest)
            last = end >= len(items)
            if last:
                # The queue ends here: every final rule looks now.
                ev.timing_done = True
                ev.timing_look = ev.timing_look or bool(ev.latency or ev.cost)
                ev.quality_look = True
            set_budget(s)  # the default, re-priced at the candidate's own cost
            hit = first_stop(spec.rules, ev)
            if hit is not None:
                s.stop = hit
                s.stop_kind = hit.rule.kind
                s.stop_line = hit.line(rule_text(hit.rule, ev))
                s.status = "capped" if hit.budget else "stopped"
            elif last:
                s.status = "completed" if spec.full else "capped"
        for s in started:
            decision = (
                "continue"
                if s.live
                else "done"
                if s.status == "completed"
                else f"stopped: {s.stop_kind}"
                if s.status == "stopped"
                else s.status
            )
            update_hold(s)
            line = progress_line(s.cand.label, k, len(bounds), s.ev, decision)
            ctx.log.event(
                "measure_chunk",
                item_id=spec.item_id,
                rc_id=s.cand.rc_id,
                chunk=k,
                chunks=len(bounds),
                line=line,
                planned=sum(own_per_eval(s).values()),
                spend_usd=s.spend,
                cap_usd=s.budget.usd if s.budget is not None else None,
                decision=decision,
                **chunk_counts(s.ev, s.reported_n),
            )
            s.reported_n = s.ev.judged_n
            if spec.progress is not None:
                spec.progress(s.cand.rc_id, line)

    out: list[Measured] = []
    for s in states:
        if s.live:  # the loop ran out of chunks without a verdict
            s.status = "completed" if spec.full else "capped"
        ev = s.ev
        m = Measured(
            rc_id=s.cand.rc_id,
            label=s.cand.label,
            status=s.status,
            evidence=ev,
            stop_kind=s.stop_kind,
            stop_line=s.stop_line,
            stop=s.stop,
            error=s.error,
            lever_note=s.lever_note,
            spend_usd=s.spend,
            chunks=s.chunks,
            planned_chunks=len(bounds),
            estimate=estimate(ev.judged, queue.population),
            per_eval={
                e: est
                for e in queue.population
                if (est := estimate(ev.judged, queue.population, e)) is not None
            },
            residual=_residual_result(ev, queue.seed, spec.rules),
            budget=s.budget,
            cost_per_item_usd=median_or_none(s.item_costs),
            expected_full_usd=expected(s),
            guards_required=required.get(s.cand.rc_id),
            planned_per_eval=own_per_eval(s),
        )
        if ev.latency or ev.cost:
            planned = timing_planned or None
            m.latency = compare_latencies(
                ev.ref_latency,
                ev.latency,
                method="paired_drive",
                split="train",
                planned=planned,
            )
            m.cost = compare_latencies(
                ev.ref_cost,
                ev.cost,
                method="paired_drive",
                split="train",
                planned=planned,
                metric="cost_usd",
            )
            if ev.parent_latency is not None:
                m.vs_parent = compare_latencies(
                    ev.parent_latency, ev.latency, method="paired_drive", split="train"
                )
                m.vs_parent_cost = compare_latencies(
                    ev.parent_cost or {},
                    ev.cost,
                    method="paired_drive",
                    split="train",
                    metric="cost_usd",
                )
        m.body = measured_body(m)
        m.line = measured_line(m)
        out.append(m)
    return out


def measured_line(m: Measured) -> str:
    """One plain line for a candidate's measurement, verdict last."""
    verdict = {
        "completed": "completed",
        "capped": f"capped — {m.stop.numbers}" if m.stop is not None else "capped",
        "failed": "failed",
    }.get(m.status, f"stopped — {m.stop_line}")
    return f"{measured_body(m)} → {verdict}"


def measured_body(m: Measured) -> str:
    ev = m.evidence
    fixed, broke = ev.fixed_broke()
    if ev.no_rows:
        parts = [f"{m.label}: no row stored for {ev.judged_n} item(s)"]
    elif ev.efficiency:
        # The losses on the baseline's passes with their bound,
        # the fixes apart, and no net: the net folds fixes against losses.
        parts = [f"{m.label}: {ev.judged_n} judged, {ev.loss_text()}"]
    else:
        parts = [f"{m.label}: {ev.judged_n} judged, +{fixed}/−{broke} vs baseline"]
    if m.estimate is not None and not ev.efficiency:
        lo, hi = m.estimate.ci()
        parts.append(f"net {m.estimate.net:+.2f} [{lo:+.2f}..{hi:+.2f}]")
    for name, c in (("latency", m.latency), ("cost", m.cost)):
        if c is not None and c.median_ratio is not None:
            parts.append(
                f"{name} {c.median_ratio:.2f}× ({c.faster_items}/{c.n_items}"
                f" {'faster' if name == 'latency' else 'cheaper'})"
            )
    for name, c in (("latency", m.vs_parent), ("cost", m.vs_parent_cost)):
        if c is not None and c.median_ratio is not None and name in _shown(m):
            parts.append(
                f"{name} vs its parent {c.median_ratio:.2f}× ({c.faster_items}/"
                f"{c.n_items} {'faster' if name == 'latency' else 'cheaper'})"
            )
    if m.lever_note:
        parts.append(m.lever_note)
    return " · ".join(parts)


def _shown(m: Measured) -> tuple[str, ...]:
    """The vs-parent figure the objective decides on."""
    return ("cost",) if m.evidence.objective == "cost" else ("latency",)
