"""The `repeat` item: an existing run config measured again on TRAIN items.

The planner asks for it (`RepeatItem`) when a decision rests on a small
difference: one measurement cannot show how much the same run config scores
differently on the same items between two runs.

**Why a plain re-run does not work.** Kiln stores one result per (eval
config × run config × item) and uses it again. A second job on a measured
run config returns the stored rows and drives nothing, which is all a
`remeasure` item does on such a run config. So code uses the method of the
noise check (`kiln_harness_tuner.noise`): it creates a CLONE of the run
config (the same properties, a new id, named `REPEAT …`) and runs each
named eval's default config on it, narrowed to the sample
(`Analyzer.judge_items`). The clone has no stored row, so every sampled item
is driven and judged again. The run config itself is never edited.

**The sample.** For each eval, the items the run config has a stored train
row for, with a value on every comparable score. `max_items` takes the
first N by a hash of the item id (`noise.sample_items`), so two run configs
with the same stored items give the same sample, and the sample of
`--noise-check N` is the same N items. Without `max_items`: every such item.
Train only: the items come from a train read, and the job names the train
split.

**What it is, and is not.** A repeat is evidence about noise. It is not an
arm: no experiment record, no analysis session, no comparison and no
statistic reads the clone's rows, and the clone is never a parent, a
candidate or the baseline (`clone_refusal`). It changes neither
`train_noise` nor the flip rate of the stop rules: only `--noise-check`
sets those.

**Spend.** Before any spend, code prints an estimate from the run config's
own stored rows (each sampled item's median drive cost once, and each
eval's median judge cost per item). An estimate above the most one
candidate may cost (`budget.hard_max_usd` of the remaining run budget) ends
the item `capped`, with the largest `max_items` that fits. The item holds
its estimate while it runs (`BudgetHolds`), so an arm measured beside it
does not size its cap against the same dollars. The spend is booked as
`spend` source `repeat` (provider lane).

**What comes out.** Per (eval, score), a `RepeatRow`: the items compared,
how many changed between the stored row and the repeat (a pass/fail outcome
at the statistics' pass line, or a rating value), how many of those were
better and how many worse, and the mean score of each of the two
measurements on the compared items. Every later planner of the run gets
the rows (`BatchPlanInput.train_repeats`), an analyst gets the rows on its
arm, the arm's parent and the baseline (`AnalysisInput.train_repeats`), and
the report has the table.

**Where a repeat is stored.** The rows are in Kiln, on the clone, like any
eval run. The clone's provenance names the run config it copies. The
`repeat` event of the wave log holds the `RepeatResult`, with the clone's
id. `repeat_rows` reads the rows back by item: a rule that counts support
across repeats starts there.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from statistics import fmean
from typing import TYPE_CHECKING, Any

from kiln_harness_tuner.analysis import (
    BINARY_SCORE_TYPES,
    AnalysisError,
    ScoreSpec,
    _runs_by_item,
    normalize,
)
from kiln_harness_tuner.budget import hard_max_usd, remaining_run_budget
from kiln_harness_tuner.detectability import comparable_specs
from kiln_harness_tuner.kiln import KilnError
from kiln_harness_tuner.latency import task_cost_of_eval_run
from kiln_harness_tuner.noise import (
    NOISE_NAME_PREFIX,
    _EvalSample,
    _values,
    create_clone,
    estimate_usd,
    sample_items,
)
from kiln_harness_tuner.provider_errors import ProviderQuotaError
from kiln_harness_tuner.schemas import (
    PROVIDER_LANE,
    RepeatItem,
    RepeatResult,
    RepeatRow,
)

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

logger = logging.getLogger(__name__)

REPEAT_NAME_PREFIX = "REPEAT "
SPEND_SOURCE = "repeat"
_NOTE_MAX_CHARS = 300


def _made_by_code_prefixes() -> tuple[str, ...]:
    from kiln_harness_tuner.orchestrator import PROBE_RC_PREFIX

    return (REPEAT_NAME_PREFIX, NOISE_NAME_PREFIX, PROBE_RC_PREFIX)


async def clone_refusal(ctx: Ctx, rc_id: str) -> str | None:
    """Why `rc_id` may not be built on or repeated, when it is a run config
    that code made only to measure or to probe: a `REPEAT …` or `NOISE …`
    clone, or a `PROBE …` fork. None for every other run config, and when
    the run configs cannot be read."""
    from kiln_harness_tuner.probes import task_run_configs

    try:
        rows = await task_run_configs(ctx)
    except KilnError:
        return None
    for row in rows:
        if str(row.get("id")) != rc_id:
            continue
        name = str(row.get("name") or "")
        if name.startswith(_made_by_code_prefixes()):
            return (
                f"run config {rc_id} ({name!r}) is a clone or a fork that code"
                " made to measure or to probe. It is never a parent, a"
                " candidate or the subject of a repeat: name the run config"
                " it was made from"
            )
    return None


async def _refusal(ctx: Ctx, rc_id: str) -> str | None:
    from kiln_harness_tuner.analysis import ModelPolicyRefused
    from kiln_harness_tuner.orchestrator import require_policy
    from kiln_harness_tuner.probes import task_run_configs

    try:
        rows = await task_run_configs(ctx)
    except KilnError as exc:
        return f"the run configs could not be read ({exc})"[:_NOTE_MAX_CHARS]
    if not any(str(row.get("id")) == rc_id for row in rows):
        return f"the task has no run config {rc_id}"
    made = await clone_refusal(ctx, rc_id)
    if made is not None:
        return made
    # The analyzer's guard: the model policy, and a model the preflight
    # flagged this run. The clone is exempt from it, so it is asked here.
    try:
        await require_policy(ctx, rc_id)
    except ModelPolicyRefused as exc:
        return str(exc)
    except KilnError as exc:
        return f"the model policy could not be checked ({exc})"[:_NOTE_MAX_CHARS]
    return None


async def _samples(ctx: Ctx, item: RepeatItem) -> tuple[list[_EvalSample], list[str]]:
    """One sample per eval of the item that has a stored train row to
    compare with, and a note for each eval that has none."""
    samples: list[_EvalSample] = []
    notes: list[str] = []
    for eval_id in item.eval_ids:
        try:
            eval_def = await ctx.analyzer.fetch_eval(eval_id)
            config_id = ctx.analyzer.default_eval_config_id(eval_def)
            specs, _notes = comparable_specs(eval_def)
            if not specs:
                notes.append(f"{eval_id}: no score that a comparison can read")
                continue
            runs = await ctx.analyzer.stored_results(
                eval_id, config_id, item.rc_id, "train"
            )
        except (KilnError, AnalysisError) as exc:
            notes.append(f"{eval_id}: could not be read ({exc})"[:_NOTE_MAX_CHARS])
            continue
        by_item = _runs_by_item(runs)
        covered = [
            i
            for i, row in by_item.items()
            if all(_values(row, s.key) is not None for s in specs)
        ]
        items = sample_items(covered, item.max_items or len(covered))
        if not items:
            notes.append(
                f"{eval_id}: run config {item.rc_id} has no stored train row."
                " A repeat needs a first measurement: plan a `remeasure`"
            )
            continue
        samples.append(_EvalSample(eval_def, config_id, specs, by_item, items))
    return samples, notes


def items_key(items: list[str]) -> str:
    """A short name for a set of items: equal on two rows when, and only
    when, the same items were compared."""
    return hashlib.sha256("\n".join(sorted(items)).encode()).hexdigest()[:8]


def compare(
    spec: ScoreSpec,
    stored: dict[str, dict[str, Any]],
    repeat: dict[str, dict[str, Any]],
    items: list[str],
) -> dict[str, Any]:
    """The stored rows against the repeat's rows, item by item, on one
    score: the `RepeatRow` fields that code counts. A pass/fail outcome is
    read at the statistics' pass line, a rating by its value. Better and
    worse are in the score's good direction."""
    compared: list[str] = []
    improved = worsened = 0
    diffs: list[float] = []
    before: list[float] = []
    after: list[float] = []
    binary = spec.type in BINARY_SCORE_TYPES
    for item in items:
        a = _values(stored.get(item), spec.key)
        b = _values(repeat.get(item), spec.key)
        if a is None or b is None:
            continue
        compared.append(item)
        before.append(a)
        after.append(b)
        good_a, good_b = normalize(a, spec), normalize(b, spec)
        if binary:
            good_a, good_b = float(good_a >= 0.5), float(good_b >= 0.5)
        else:
            diffs.append(abs(b - a))
        improved += int(good_b - good_a > 1e-9)
        worsened += int(good_a - good_b > 1e-9)
    n = len(compared)
    changed = improved + worsened
    return {
        "items": n,
        "items_key": items_key(compared) if compared else "",
        "changed": changed,
        "change_rate": changed / n if n else None,
        "improved": improved,
        "worsened": worsened,
        "mean_abs_change": fmean(diffs) if diffs else None,
        "stored_score": fmean(before) if before else None,
        "repeat_score": fmean(after) if after else None,
    }


def _score(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def repeat_line(row: RepeatRow) -> str:
    rate = "—" if row.change_rate is None else f"{row.change_rate:.0%}"
    text = (
        f"repeat {row.item_id}: {row.rc_id} on {row.eval_id}/{row.score_key} —"
        f" {row.changed} of {row.items} changed ({rate}: {row.worsened} worse,"
        f" {row.improved} better"
    )
    if row.mean_abs_change is not None:
        text += f", mean |Δ| {row.mean_abs_change:.2f}"
    return (
        f"{text}); stored {_score(row.stored_score)}, repeat {_score(row.repeat_score)}"
    )


def rows_for(
    rows: list[RepeatRow], rc_ids: set[str], eval_ids: set[str]
) -> list[RepeatRow]:
    """The repeat rows an analyst reads beside one arm's table: those on
    the given run configs (the arm's, its parent, the baseline) and evals."""
    return [r for r in rows if r.rc_id in rc_ids and r.eval_id in eval_ids]


def repeat_clones(ctx: Ctx, rc_id: str, eval_id: str) -> list[str]:
    """The clones that hold a repeat of (`rc_id`, `eval_id`) this run,
    oldest first."""
    out: list[str] = []
    for result in ctx.repeat_results:
        if result.rc_id != rc_id or not result.clone_rc_id:
            continue
        if any(row.eval_id == eval_id for row in result.rows):
            out.append(result.clone_rc_id)
    return out


async def repeat_rows(
    ctx: Ctx, rc_id: str, eval_id: str
) -> list[dict[str, dict[str, Any]]]:
    """Each repeat of (`rc_id`, `eval_id`) this run, oldest first, as item
    id -> the repeat's stored row. The first measurement is the run
    config's own stored rows. Nothing in the loop reads this yet: it is
    where a rule that counts support across repeats starts."""
    eval_def = await ctx.analyzer.fetch_eval(eval_id)
    config_id = ctx.analyzer.default_eval_config_id(eval_def)
    out: list[dict[str, dict[str, Any]]] = []
    for clone in repeat_clones(ctx, rc_id, eval_id):
        runs = await ctx.analyzer.stored_results(eval_id, config_id, clone, "train")
        out.append(_runs_by_item(runs))
    return out


def estimate(samples: list[_EvalSample]) -> tuple[float, float, float]:
    """(total, drive per request, judge per item) from the run config's
    stored rows, as `noise.estimate_usd`, but with one drive for each
    (eval, item), not one for each item. Evals that share a V2 trace drive
    an item once, so this is an upper bound there; a legacy judge config
    drives inside its own job, so one drive per item would be too low."""
    _total, drive, judge = estimate_usd(samples, set())
    judges = _total - len({i for s in samples for i in s.items}) * drive
    drives = sum(len(s.items) for s in samples) * drive
    return drives + judges, drive, judge


def _largest_fit(samples: list[_EvalSample], total: float, cap: float) -> int:
    """About how many items for each eval fit `cap`, at this estimate's
    price per item."""
    largest = max(len(s.items) for s in samples)
    return int(largest * cap / total) if total > 0 else largest


async def run_repeat(
    ctx: Ctx, item: RepeatItem, baseline_rc_id: str, wave: int
) -> RepeatResult:
    """The whole item (module doc). Returns the result, and adds its rows
    to `ctx.train_repeats` for every later planner and analyst."""
    result = RepeatResult(
        item_id=item.id,
        wave=wave,
        rc_id=item.rc_id,
        is_baseline=item.rc_id == baseline_rc_id,
        max_items=item.max_items,
    )
    refusal = await _refusal(ctx, item.rc_id)
    if refusal is not None:
        result.skipped = refusal
        return _finish(ctx, result)
    samples, result.notes = await _samples(ctx, item)
    if not samples:
        result.skipped = "; ".join(result.notes)[:_NOTE_MAX_CHARS]
        return _finish(ctx, result)
    total, drive, judge = estimate(samples)
    result.estimate_usd = total
    unique = len({i for s in samples for i in s.items})
    ctx.log.event(
        "repeat_estimate",
        item_id=item.id,
        estimate_usd=total,
        line=(
            f"repeat {item.id}: {unique} train item(s) driven again on a clone"
            f" of {item.rc_id} and judged on {len(samples)} eval(s) — estimate"
            f" ${total:.2f} (${drive:.4f}/drive, ${judge:.4f}/judgment, from"
            " the stored rows)"
        ),
    )
    left = remaining_run_budget(ctx)
    cap = hard_max_usd(left)
    if total > cap:
        result.over_budget = True
        fit = _largest_fit(samples, total, cap)
        result.skipped = (
            f"the estimate ${total:.2f} is above the most one repeat may cost,"
            f" ${cap:.2f}"
            + (f" (${left:.2f} is left of --budget-usd)" if left is not None else "")
            + (
                f": about {fit} item(s) for each eval fit (`max_items`)"
                if fit >= 1
                else ": no item fits"
            )
        )
        return _finish(ctx, result)
    hold_key = ("repeat", item.id, wave)
    ctx.budget_holds.hold(hold_key, total)
    try:
        await _measure(ctx, item, result, samples)
    finally:
        ctx.budget_holds.release(hold_key)
    return _finish(ctx, result)


async def _measure(
    ctx: Ctx, item: RepeatItem, result: RepeatResult, samples: list[_EvalSample]
) -> None:
    description = (
        f"Kiln Harness Tuner repeat measurement {item.id}: a clone of"
        f" {item.rc_id} with the same properties, so Kiln's item cache does"
        " not answer. Evidence about noise only. Never a parent, a"
        " candidate or the baseline."
    )
    try:
        clone = await create_clone(
            ctx,
            item.rc_id,
            f"{REPEAT_NAME_PREFIX}{item.id} of {item.rc_id}",
            description,
        )
    except Exception as exc:  # the item fails, the wave goes on
        result.skipped = f"the clone run config could not be made ({exc})"[
            :_NOTE_MAX_CHARS
        ]
        return
    result.clone_rc_id = clone
    # The clone is the twin of a run config the policy check passed above:
    # measured whatever the policy reads of a config it has never listed.
    ctx.policy_exempt_rcs.add(clone)
    ctx.log.event("repeat_clone", item_id=item.id, rc_id=clone, parent_rc_id=item.rc_id)

    async def one(s: _EvalSample) -> dict[str, dict[str, Any]]:
        try:
            rows = await ctx.analyzer.judge_items(
                s.eval_id, s.config_id, clone, s.items
            )
        except ProviderQuotaError:
            raise
        except Exception as exc:
            logger.warning("repeat %s failed on eval %s: %s", item.id, s.eval_id, exc)
            result.notes.append(
                f"{s.eval_id}: the repeat's eval job failed ({exc})"[:_NOTE_MAX_CHARS]
            )
            return {}
        return _runs_by_item(rows)

    repeats = await asyncio.gather(*(one(s) for s in samples))
    # Each judgment once, and each drive once however many evals scored it
    # (a V2 trace is shared across a run config's evals).
    spend = 0.0
    drives: dict[str, float] = {}
    for by_item in repeats:
        spend += ctx.analyzer.fresh_judge_cost(by_item.values())
        for item_id, row in by_item.items():
            cost = task_cost_of_eval_run(row)
            if cost is not None:
                drives[str(row.get("scored_run_id") or item_id)] = cost
    result.spend_usd = spend + sum(drives.values())
    ctx.log.event(
        "spend",
        item_id=item.id,
        source=SPEND_SOURCE,
        lane=PROVIDER_LANE,
        cost_usd=result.spend_usd,
        evidence_cost_usd=result.spend_usd,
    )
    for s, after in zip(samples, repeats, strict=True):
        earlier = len(repeat_clones(ctx, item.rc_id, s.eval_id))
        counted = [compare(spec, s.baseline, after, s.items) for spec in s.specs]
        if not any(c["items"] for c in counted) and not any(
            n.startswith(f"{s.eval_id}: the repeat's eval job failed")
            for n in result.notes
        ):
            result.notes.append(f"{s.eval_id}: the repeat stored no row to compare")
        for spec, fields in zip(s.specs, counted, strict=True):
            if not fields["items"]:
                continue
            result.rows.append(
                RepeatRow(
                    item_id=item.id,
                    wave=result.wave,
                    rc_id=item.rc_id,
                    clone_rc_id=clone,
                    is_baseline=result.is_baseline,
                    repeat_index=earlier + 1,
                    eval_id=s.eval_id,
                    eval_name=s.name,
                    score_key=spec.key,
                    score_type=spec.type,
                    direction=spec.direction,
                    **fields,
                )
            )


def _finish(ctx: Ctx, result: RepeatResult) -> RepeatResult:
    ctx.repeat_results.append(result)
    ctx.train_repeats.extend(result.rows)
    if result.skipped:
        line = f"repeat {result.item_id} did not run: {result.skipped}"
    else:
        line = (
            f"repeat {result.item_id}: {result.rc_id} measured again on"
            f" {len(result.rows)} score(s) — ${result.spend_usd:.2f} this run"
        )
        for row in result.rows:
            ctx.log.event("repeat_row", item_id=result.item_id, line=repeat_line(row))
    for note in result.notes:
        ctx.log.event("repeat_note", item_id=result.item_id, line=f"repeat: {note}")
    ctx.log.event("repeat", item_id=result.item_id, line=line, result=result)
    logger.info("%s", line)
    return result
