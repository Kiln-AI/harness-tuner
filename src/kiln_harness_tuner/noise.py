"""The run-to-run noise check (`--noise-check N`, off by default).

One measurement cannot show how much the same run config scores differently
on the same item between two runs, or how much the judge scores the same
output differently. This check measures both, on N TRAIN items per eval,
before wave 1, and compares each with the baseline's stored rows on the same
items.

**Why a plain re-run does not work.** Kiln stores one result per (eval
config × run config × item) and reuses it: a second job on the baseline
returns the stored rows and drives nothing. So:

- **Re-drive (the model and the judge together).** Code creates a CLONE of
  the baseline run config — the same properties, a new id, named `NOISE …`
  — and runs each eval's default config on it, narrowed to the sample
  (`Analyzer.judge_items`). The clone has no stored row, so every item is
  driven and judged again. The clone never becomes a parent, a candidate or
  the baseline, and the baseline run config is never edited.
- **Re-judge (the judge alone).** For an eval whose default config is a V2
  judge, code creates a COPY of that config — the same type, properties
  and model, a new id, never set as the default — and runs it on the
  BASELINE run config over the same sample. A V2 config shares the task's
  stored traces across eval configs (the server's trace index is keyed by
  item and run config), so the copy judges the baseline's own stored
  outputs again and drives nothing. Code then checks that: every copied row
  must name the baseline row's scored task run (or carry the same output).
  A row that does not means the server drove again, and the eval's judge
  noise is reported as not measured. After the pass the copy leaves the
  project (to `runs/.quarantine/…/archived_eval_configs/`), so the eval is
  left as it was. A legacy judge config (`g_eval`, `llm_as_judge`) drives
  inside its own job, and a code eval is deterministic: neither is re-judged,
  and the row says why.

**Spend.** Before any spend, code prints an estimate from the baseline's
stored rows (each sampled item's median drive cost once, and each eval's
median judge cost per item, for both passes). With `--budget-usd`, a check
whose estimate does not fit the remaining run budget is skipped, with the
reason. The spend is booked as `spend` source `noise_check` (provider lane)
and reaches the budget and the report like any other.

**What comes out.** Per (eval, score): how many of the sampled items
changed between the stored row and the re-drive, and between the stored
row and the re-judge (a pass/fail outcome, or a rating value, with the mean
absolute change). Then the comparison with the detectability floor: if the
noise changes about h items each way on a measurement of the whole train
split, a real improvement must carry w wins against h + h noisy pairs; the
check reports that w (a sign-test approximation) and warns when it is above
the best-case floor, or above the items with room. It never refuses. The
rows are TRAIN rows: the planner gets them (`BatchPlanInput.train_noise`).
Under speed and cost, the pooled re-drive change rate of the pass/fail rows
(`stop_rules.noise_flip_rate`) is also the p0 of the `guard_losses` stop
rule. Under quality it is the p0 of the guard half of the
`residual` stop rule.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import fmean, median
from typing import TYPE_CHECKING, Any

from kiln_harness_tuner.analysis import (
    BINARY_SCORE_TYPES,
    SIGNIFICANCE_ALPHA,
    ScoreSpec,
    _runs_by_item,
)
from kiln_harness_tuner.budget import remaining_run_budget
from kiln_harness_tuner.detectability import comparable_specs, fewest_wins, score_room
from kiln_harness_tuner.kiln import KilnError
from kiln_harness_tuner.kiln_names import agent_provenance, kiln_name
from kiln_harness_tuner.latency import task_cost_of_eval_run
from kiln_harness_tuner.provider_errors import ProviderQuotaError
from kiln_harness_tuner.schemas import PROVIDER_LANE, NoiseCheckResult, NoiseRow

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

logger = logging.getLogger(__name__)

NOISE_NAME_PREFIX = "NOISE "
SPEND_SOURCE = "noise_check"


@dataclass
class _EvalSample:
    eval_def: dict[str, Any]
    config_id: str
    specs: list[ScoreSpec]
    baseline: dict[str, dict[str, Any]]  # item id -> the baseline's stored row
    items: list[str]
    train_values: dict[str, list[float]] = field(default_factory=dict)

    @property
    def eval_id(self) -> str:
        return str(self.eval_def["id"])

    @property
    def name(self) -> str:
        return str(self.eval_def.get("name") or self.eval_id)


def sample_items(item_ids: list[str], n: int) -> list[str]:
    """A fixed sample: the first `n` by a hash of the item id, so the same
    items come back every run and evals that share items share drives."""
    return sorted(item_ids, key=lambda i: hashlib.sha256(i.encode()).hexdigest())[:n]


def binomial_two_sided_p(wins: int, losses: int) -> float:
    """The exact two-sided sign-test p for `wins` against `losses`."""
    total = wins + losses
    if total == 0:
        return 1.0
    low = min(wins, losses)
    tail = sum(math.comb(total, i) for i in range(low + 1)) * 0.5**total
    return min(1.0, 2.0 * tail)


def noise_adjusted_wins(
    rate: float, n: int, room: int, alpha: float = SIGNIFICANCE_ALPHA
) -> int | None:
    """The fewest real wins that show through the noise on a measurement of
    `n` items, when a share `rate` of items changes between two runs of the
    same config: about h = rate · n / 2 noisy changes each way, so w wins
    must beat h + w against h (the exact sign test). An approximation: it
    ignores the interval route and treats every change as a sign. None when
    even `room` wins cannot."""
    each_way = math.floor(rate * n / 2.0 + 0.5)
    for w in range(1, room + 1):
        if binomial_two_sided_p(w + each_way, each_way) < alpha:
            return w
    return None


def _values(row: dict[str, Any] | None, key: str) -> float | None:
    if row is None:
        return None
    value = (row.get("scores") or {}).get(key)
    return float(value) if isinstance(value, (int, float)) else None


def changes(
    spec: ScoreSpec,
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    items: list[str],
) -> tuple[int, int, float | None]:
    """(items compared, items changed, mean absolute change or None) — a
    pass/fail outcome at the statistics' pass line, a rating by value."""
    compared = changed = 0
    diffs: list[float] = []
    for item in items:
        a = _values(before.get(item), spec.key)
        b = _values(after.get(item), spec.key)
        if a is None or b is None:
            continue
        compared += 1
        if spec.type in BINARY_SCORE_TYPES:
            changed += int((a >= 0.5) != (b >= 0.5))
        else:
            diffs.append(abs(b - a))
            changed += int(abs(b - a) > 1e-9)
    return compared, changed, (fmean(diffs) if diffs else None)


def _row_output(run: dict[str, Any]) -> Any:
    out = run.get("output")
    return out.get("output") if isinstance(out, dict) else out


def same_outputs(
    baseline: dict[str, dict[str, Any]], copied: dict[str, dict[str, Any]]
) -> bool:
    """Did every copied row judge the baseline row's own output? By the
    scored task run's id where both rows name one, else by the output."""
    if not copied:
        return False
    for item, row in copied.items():
        base = baseline.get(item)
        if base is None:
            return False
        a, b = base.get("scored_run_id"), row.get("scored_run_id")
        if a and b:
            if a != b:
                return False
            continue
        out_a, out_b = _row_output(base), _row_output(row)
        if out_a is None or out_a != out_b:
            return False
    return True


def judge_copy_note(config: dict[str, Any]) -> str | None:
    """None when a copy of this default config re-judges stored outputs;
    else why the eval's judge noise is not measured."""
    props = config.get("properties")
    props = props if isinstance(props, dict) else {}
    kind = config.get("config_type")
    if props.get("type") == "code_eval":
        return "a code eval is deterministic: no judge noise"
    if kind == "v2" or (kind is None and props.get("type")):
        return None
    return (
        f"a legacy judge config ({kind or 'g_eval'}) drives inside its own job,"
        " so a copy would re-drive: judge noise alone cannot be measured"
    )


async def _sample(ctx: Ctx, rc_id: str, n: int) -> list[_EvalSample]:
    from kiln_harness_tuner.orchestrator import _active_evals

    out: list[_EvalSample] = []
    for eval_def, config_id in await _active_evals(ctx):
        specs, _notes = comparable_specs(eval_def)
        if not specs:
            continue
        try:
            runs = await ctx.analyzer.stored_results(
                str(eval_def["id"]), config_id, rc_id, "train"
            )
        except KilnError:
            continue
        by_item = _runs_by_item(runs)
        covered = [
            item
            for item, row in by_item.items()
            if all(_values(row, s.key) is not None for s in specs)
        ]
        items = sample_items(covered, n)
        if not items:
            continue
        sample = _EvalSample(eval_def, config_id, specs, by_item, items)
        sample.train_values = {
            s.key: [
                v for row in by_item.values() if (v := _values(row, s.key)) is not None
            ]
            for s in specs
        }
        out.append(sample)
    return out


def estimate_usd(
    samples: list[_EvalSample], rejudge: set[str]
) -> tuple[float, float, float]:
    """(total, drive per request, judge per item) from the baseline's
    stored rows: each sampled item driven once, each eval's sample judged
    once in the re-drive pass and once more where it is re-judged."""
    drives = [
        c
        for s in samples
        for row in s.baseline.values()
        if (c := task_cost_of_eval_run(row)) is not None
    ]
    drive = median(drives) if drives else 0.0
    unique = {item for s in samples for item in s.items}
    total = len(unique) * drive
    judge_all: list[float] = []
    for s in samples:
        judges = [
            float((row.get("eval_usage") or {}).get("cost") or 0.0)
            for row in s.baseline.values()
        ]
        judge = median(judges) if judges else 0.0
        judge_all.append(judge)
        passes = 2 if s.eval_id in rejudge else 1
        total += passes * len(s.items) * judge
    return total, drive, (median(judge_all) if judge_all else 0.0)


async def _create_clone(ctx: Ctx, rc_id: str) -> str:
    from kiln_harness_tuner.probes import rc_props

    props = await rc_props(ctx, rc_id)
    description = (
        f"Kiln Harness Tuner noise check: a clone of {rc_id} with the same"
        " properties, so Kiln's item cache does not answer. Never a"
        " parent, a candidate or the baseline."
    )
    created = await ctx.kiln.call(
        "POST",
        f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}/run_configs",
        body={
            "name": kiln_name(f"{NOISE_NAME_PREFIX}clone of {rc_id}", 60),
            "description": description,
            "run_config_properties": props,
            "provenance": agent_provenance(description, [rc_id]),
        },
    )
    clone = created.get("id") if isinstance(created, dict) else None
    if not clone:
        raise KilnError(f"run config create returned no id: {created!r}")
    return str(clone)


async def _create_judge_copy(ctx: Ctx, eval_id: str, config: dict[str, Any]) -> str:
    base = f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}"
    props = config.get("properties")
    created = await ctx.kiln.call(
        "POST",
        f"{base}/evals/{eval_id}/create_eval_config",
        body={
            "name": kiln_name(
                f"{NOISE_NAME_PREFIX}copy of {config.get('name') or config.get('id')}",
                60,
            ),
            "type": config.get("config_type") or "v2",
            "properties": props if isinstance(props, dict) else {},
            "model_name": config.get("model_name"),
            "provider": config.get("model_provider"),
            "provenance": agent_provenance(
                "Kiln Harness Tuner noise check: a copy of the default config that"
                " re-judges stored outputs once. Never the default; moved"
                " out of the project after use.",
                [config.get("id")],
            ),
        },
    )
    copy_id = created.get("id") if isinstance(created, dict) else None
    if not copy_id:
        raise KilnError(f"eval config create returned no id: {created!r}")
    return str(copy_id)


def archive_eval_config(ctx: Ctx, eval_id: str, config_id: str) -> bool:
    """Move one eval config's folder out of the project, into the
    quarantine tree (`archived_eval_configs/<stamp>/`, paths kept, so it can
    be put back by hand). Best-effort: a failure is logged, never fatal."""
    if ctx.quarantine_root is None:
        return False
    from kiln_harness_tuner.quarantine import task_dirs

    root = Path(ctx.target.project_path).expanduser().resolve()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for task_dir in task_dirs(root, ctx.target.task_id):
        for folder in task_dir.glob("evals/*/configs/*"):
            try:
                data = json.loads(
                    (folder / "eval_config.kiln").read_text(encoding="utf-8")
                )
                parent = json.loads(
                    (folder.parent.parent / "eval.kiln").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            if str(data.get("id")) != config_id or str(parent.get("id")) != eval_id:
                continue
            target = (
                ctx.quarantine_root
                / "archived_eval_configs"
                / stamp
                / folder.relative_to(root)
            )
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(folder), str(target))
            except OSError as exc:
                logger.warning("could not archive eval config %s: %s", config_id, exc)
                return False
            return True
    return False


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def noise_line(row: NoiseRow) -> str:
    def part(changed: int | None, items: int, mean: float | None) -> str:
        share = changed / items if items and changed is not None else None
        text = f"{changed} of {items} changed ({_pct(share)}"
        if mean is not None:
            text += f", mean |Δ| {mean:.2f}"
        return text + ")"

    redrive = part(row.redrive_changed, row.items, row.redrive_mean_abs_change)
    rejudge = (
        part(row.rejudge_changed, row.rejudge_items, row.rejudge_mean_abs_change)
        if row.rejudge_changed is not None
        else f"not measured — {row.rejudge_note}"
    )
    return (
        f"noise: {row.eval_id}/{row.score_key} — re-drive {redrive}; re-judge {rejudge}"
    )


async def run_noise_check(ctx: Ctx, rc_id: str) -> NoiseCheckResult:
    """The whole check (module doc). Returns the result, and sets
    `ctx.train_noise` to its rows for every planner."""
    n = ctx.noise_check_items
    result = NoiseCheckResult(baseline_rc_id=rc_id, items_per_eval=n)
    samples = await _sample(ctx, rc_id, n)
    if not samples:
        result.skipped = "no stored baseline train row to compare with"
        return _finish(ctx, result)
    configs: dict[str, dict[str, Any]] = {}
    notes: dict[str, str] = {}
    for s in samples:
        try:
            config = await ctx.kiln.call(
                "GET",
                f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}"
                f"/evals/{s.eval_id}/eval_config/{s.config_id}",
            )
        except KilnError as exc:
            notes[s.eval_id] = f"the default config could not be read ({exc})"[:200]
            continue
        config = config if isinstance(config, dict) else {}
        note = judge_copy_note(config)
        if note is None:
            configs[s.eval_id] = config
        else:
            notes[s.eval_id] = note
    total, drive, judge = estimate_usd(samples, set(configs))
    result.estimate_usd = total
    unique = len({item for s in samples for item in s.items})
    ctx.log.event(
        "noise_estimate",
        estimate_usd=total,
        line=(
            f"noise check: {unique} train item(s) re-driven on a clone of {rc_id}"
            f" and judged on {len(samples)} eval(s), {len(configs)} re-judged"
            f" on a copy of the judge — estimate ${total:.2f}"
            f" (${drive:.4f}/drive, ${judge:.4f}/judgment, from the stored rows)"
        ),
    )
    left = remaining_run_budget(ctx)
    if left is not None and total > left:
        result.skipped = (
            f"the estimate ${total:.2f} does not fit the ${left:.2f} left of"
            " --budget-usd"
        )
        return _finish(ctx, result)
    try:
        clone = await _create_clone(ctx, rc_id)
    except Exception as exc:  # the check is opt-in orientation: never fatal
        result.skipped = f"the clone run config could not be made ({exc})"[:300]
        return _finish(ctx, result)
    result.clone_rc_id = clone
    # The clone is the baseline's twin: measured whatever its model, like
    # the baseline chain (`Ctx.policy_exempt_rcs`).
    ctx.policy_exempt_rcs.add(clone)
    ctx.log.event("noise_clone", rc_id=clone, parent_rc_id=rc_id)

    async def redrive(s: _EvalSample) -> dict[str, dict[str, Any]]:
        try:
            rows = await ctx.analyzer.judge_items(
                s.eval_id, s.config_id, clone, s.items
            )
        except ProviderQuotaError:
            raise
        except Exception as exc:
            logger.warning("noise re-drive failed on eval %s: %s", s.eval_id, exc)
            return {}
        return _runs_by_item(rows)

    async def rejudge(s: _EvalSample) -> tuple[dict[str, dict[str, Any]], str]:
        config = configs.get(s.eval_id)
        if config is None:
            return {}, notes.get(s.eval_id, "")
        copy_id: str | None = None
        try:
            copy_id = await _create_judge_copy(ctx, s.eval_id, config)
            rows = await ctx.analyzer.judge_items(s.eval_id, copy_id, rc_id, s.items)
        except ProviderQuotaError:
            raise
        except Exception as exc:
            return {}, f"the re-judge failed ({exc})"[:200]
        finally:
            if copy_id is not None:
                archive_eval_config(ctx, s.eval_id, copy_id)
        by_item = _runs_by_item(rows)
        nonlocal_spend.append(ctx.analyzer.fresh_judge_cost(rows))
        if not same_outputs(s.baseline, by_item):
            return {}, (
                "the copy judged other outputs than the stored ones (the server"
                " drove again), so judge noise alone is not separable here"
            )
        return by_item, ""

    nonlocal_spend: list[float] = []
    redrives, rejudges = await asyncio.gather(
        asyncio.gather(*(redrive(s) for s in samples)),
        asyncio.gather(*(rejudge(s) for s in samples)),
    )
    # The re-drive's spend: each judgment once, and each drive once however
    # many evals scored it (a V2 trace is shared across a run config's evals).
    drive_spend: dict[str, float] = {}
    for by_item in redrives:
        nonlocal_spend.append(ctx.analyzer.fresh_judge_cost(by_item.values()))
        for item, row in by_item.items():
            key = str(row.get("scored_run_id") or item)
            cost = task_cost_of_eval_run(row)
            if cost is not None:
                drive_spend[key] = cost
    result.spend_usd = sum(nonlocal_spend) + sum(drive_spend.values())
    ctx.log.event(
        "spend",
        source=SPEND_SOURCE,
        lane=PROVIDER_LANE,
        cost_usd=result.spend_usd,
        evidence_cost_usd=result.spend_usd,
    )
    for s, after, (judged, note) in zip(samples, redrives, rejudges, strict=True):
        for spec in s.specs:
            compared, changed, mean_abs = changes(spec, s.baseline, after, s.items)
            j_compared, j_changed, j_mean = changes(spec, s.baseline, judged, s.items)
            values = s.train_values.get(spec.key, [])
            room = score_room(values, spec)
            rate = changed / compared if compared else None
            row = NoiseRow(
                eval_id=s.eval_id,
                eval_name=s.name,
                score_key=spec.key,
                score_type=spec.type,
                baseline_rc_id=rc_id,
                items=compared,
                redrive_changed=changed,
                redrive_rate=rate,
                redrive_mean_abs_change=mean_abs,
                rejudge_items=j_compared,
                rejudge_changed=j_changed if j_compared else None,
                rejudge_rate=(j_changed / j_compared) if j_compared else None,
                rejudge_mean_abs_change=j_mean if j_compared else None,
                rejudge_note="" if j_compared else (note or "no re-judged row"),
                train_room=room,
                noise_adjusted_wins=(
                    noise_adjusted_wins(rate, len(values), room)
                    if rate is not None
                    else None
                ),
            )
            result.rows.append(row)
            result.warnings += _warnings(row, spec, len(values))
    return _finish(ctx, result)


def _warnings(row: NoiseRow, spec: ScoreSpec, n: int) -> list[str]:
    """Compare the noise with the detectability floor. Warns, never refuses."""
    if row.redrive_rate is None or not row.redrive_changed or not row.train_room:
        return []
    label = f"{row.eval_id}/{row.score_key}"
    best = fewest_wins(spec.type, n, row.train_room)
    each_way = math.floor(row.redrive_rate * n / 2.0 + 0.5)
    head = (
        f"{label}: {_pct(row.redrive_rate)} of items change between two runs of"
        f" the same config — about {each_way} change(s) each way on the"
        f" {n}-item train split"
    )
    if row.noise_adjusted_wins is None:
        return [
            f"{head}; even fixing all {row.train_room} item(s) with room is not"
            " enough to show a win through that noise"
        ]
    if best is None or row.noise_adjusted_wins > best:
        return [
            f"{head}; a win on it needs about {row.noise_adjusted_wins} wins,"
            f" more than the best-case {best if best is not None else '—'}"
            f" ({row.train_room} item(s) have room)"
        ]
    return []


def _finish(ctx: Ctx, result: NoiseCheckResult) -> NoiseCheckResult:
    ctx.train_noise = list(result.rows)
    if result.skipped:
        line = f"noise check skipped: {result.skipped}"
    else:
        line = (
            f"noise check: {len(result.rows)} score(s) on {result.items_per_eval}"
            f" train item(s) per eval — ${result.spend_usd:.2f} this run"
        )
        for row in result.rows:
            ctx.log.event("noise_row", line=noise_line(row))
        for warning in result.warnings:
            ctx.log.event("noise_warning", line=f"⚠ noise: {warning}")
    ctx.log.event("noise_check", line=line, result=result)
    logger.info("%s", line)
    return result
