"""Code-answered probes and the setup reconnaissance battery.

A probe asks one yes/no question about a MECHANISM and answers it from the
traces of a few train drives — no judge, no measurement, no experiment
record. Two kinds:

- a **code** probe (`ProbeItem.check`) is pure code: fork the parent
  run config, attach a throwaway surface, apply property overrides, drive
  the items, count in the traces (tool calls, skill reads, reasoning chars,
  output chars, json validity). About a minute and a cent. This module.
- a **behaviour** probe (no `check`) needs reading — "did the output change
  in the named way" — and goes to the probe agent (`dispatch.probe`).

The reconnaissance battery (`recon_items`) is the standing set of
code probes the loop runs on the baseline at setup, before wave 1, so
the first planner reads facts about this model on this task instead of
assuming them: does it call a tool under its output mode, read a skill,
call a tool under the alternate mode, reason more under thinking high,
emit valid JSON, produce output on the longest inputs.

Train items are read from the project ON DISK (the studio server has no
eval-input listing route): `<project>/tasks/<task_id> - */eval_inputs/*/
eval_input.kiln`, filtered by the tag an active eval's train split names.
A multi-turn input, or one with a `world_reset`, is driven through an eval
job (`kiln_harness_tuner.job_drives`); every other input through `POST …/run`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean
from typing import TYPE_CHECKING, Any

from kiln_harness_tuner.kiln import KilnError
from kiln_harness_tuner.kiln_names import agent_provenance, kiln_name
from kiln_harness_tuner.latency import latency_of_task_run, run_output
from kiln_harness_tuner.provider_errors import (
    ProviderQuotaError,
    classify_provider_message,
)
from kiln_harness_tuner.schemas import (
    BaselineRef,
    ErrorInfo,
    ProbeCheck,
    ProbeItem,
    ProbeResult,
)

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

logger = logging.getLogger(__name__)

PROBE_RUN_TAG = "tuner_probe"
PROBE_TOOL_FUNCTION = "probe_checklist"
PROBE_TOOL_NAME = "PROBE checklist tool"
PROBE_SKILL_NAME = "probe-checklist"
PROBE_TOOL_DESCRIPTION = (
    "ALWAYS call this tool first, before you write any part of your answer:"
    " it returns the checklist you must follow for this task."
)
PROBE_SKILL_DESCRIPTION = (
    "ALWAYS read this skill first, before you write any part of your answer:"
    " it holds the checklist you must follow for this task."
)
PROBE_SKILL_BODY = (
    "# Probe checklist\n\nFollow the task instructions exactly. This skill"
    " exists to check that the model reads skills; it adds no rule.\n"
)
PROBE_TOOL_CODE = (
    'def run():\n    return "Checklist: follow the task instructions exactly."\n'
)
DRIVE_TIMEOUT_SECS = 600.0
MAX_CONCURRENT_DRIVES = 3
# Kiln resource names reject these characters (`.agents/AGENTS.md` conventions).
_RATIO = 1.5
# A moved count is not a quality verdict (reading "fewer reasoning chars" as
# "worse" would drop the thinking lever unmeasured).
_NO_QUALITY = " — moved, direction says nothing about quality; measure it as an arm"


@dataclass(frozen=True)
class TrainInput:
    """One drivable eval input. For a multi-turn input, `plaintext` is the
    opening user message. `needs_eval_job`: only an eval job can drive it
    (multi-turn, or a `world_reset`). `eval_id`: the eval the loader found
    it in, when it read the evals' splits."""

    id: str
    structured: Any | None
    plaintext: str | None
    size: int
    needs_eval_job: bool = False
    multi_turn: bool = False
    eval_id: str | None = None


def _name(text: str, limit: int = 60) -> str:
    return kiln_name(text, limit)


# ---------------------------------------------------------------- inputs


def _task_dirs(project_path: str, task_id: str) -> list[Path]:
    return sorted(
        p for p in Path(project_path).glob(f"tasks/{task_id} - *") if p.is_dir()
    )


def load_train_inputs(
    project_path: str, task_id: str, train_filter: str
) -> list[TrainInput]:
    """Every drivable eval input on disk carrying the train split's tag
    (`tag::<name>` → `<name>`), sorted by id. Structured tasks store the
    input as a JSON string inside `user_message.text`; it is parsed back
    when it is an object or array, else sent as plaintext."""
    tag = train_filter.removeprefix("tag::")
    out: list[TrainInput] = []
    for task_dir in _task_dirs(project_path, task_id):
        for path in sorted(task_dir.glob("eval_inputs/*/eval_input.kiln")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if tag not in (doc.get("tags") or []):
                continue
            inp = input_from_doc(doc)
            if inp is not None:
                out.append(inp)
    return out


def input_from_doc(doc: dict[str, Any]) -> TrainInput | None:
    """One eval input record as a drivable input: single-turn, or
    multi-turn synthetic (its opening message is the text). None for any
    other shape (unreadable data, a multi-turn input with no opening)."""
    from kiln_harness_tuner.job_drives import doc_needs_eval_job

    data = doc.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    if not isinstance(data, dict):
        return None
    if data.get("type") == "multi_turn_synthetic":
        opening = ((data.get("first_message") or {}).get("text")) or ""
        if not opening:
            return None
        return TrainInput(
            id=str(doc.get("id")),
            structured=None,
            plaintext=opening,
            size=len(opening),
            needs_eval_job=True,
            multi_turn=True,
        )
    if data.get("type") != "single_turn":
        return None
    text = ((data.get("user_message") or {}).get("text")) or ""
    structured: Any | None = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, (dict, list)):
            structured = parsed
    except ValueError:
        pass
    return TrainInput(
        id=str(doc.get("id")),
        structured=structured,
        plaintext=None if structured is not None else text,
        size=len(text),
        needs_eval_job=doc_needs_eval_job(doc),
    )


async def train_filter_of(ctx: Ctx) -> str | None:
    from kiln_harness_tuner.setup import (
        read_eval_views,
    )  # lazy: setup imports the items module

    views = await read_eval_views(ctx)
    for view in views:
        if view.is_active and view.train_filter:
            return view.train_filter
    return None


def select_inputs(inputs: list[TrainInput], n: int, select: str) -> list[TrainInput]:
    if select == "longest":
        return sorted(inputs, key=lambda i: (-i.size, i.id))[:n]
    return inputs[:n]


# ------------------------------------------------------------- run configs


def _rows(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, dict):
        for key in ("run_configs", "code_tools", "skills", "items", "results"):
            if isinstance(response.get(key), list):
                response = response[key]
                break
    return (
        [r for r in response if isinstance(r, dict)]
        if isinstance(response, list)
        else []
    )


async def task_run_configs(ctx: Ctx) -> list[dict[str, Any]]:
    """Every run config of the run's task, as the API lists them."""
    return _rows(
        await ctx.kiln.call(
            "GET",
            f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}/run_configs",
        )
    )


async def _parent_props(ctx: Ctx, rc_id: str) -> dict[str, Any]:
    for row in await task_run_configs(ctx):
        if str(row.get("id")) == rc_id:
            props = row.get("run_config_properties")
            if isinstance(props, dict):
                return json.loads(json.dumps(props))  # deep copy
    raise ProbeSetupError(f"run config {rc_id} not found in the task")


rc_props = _parent_props


async def thinking_levels(ctx: Ctx, provider: Any, model: Any) -> list[str] | None:
    """The thinking levels the server offers for (provider, model), in the
    server's order (lowest first), from `GET /api/available_models`. None
    when the model is not listed or the list cannot be read — callers treat
    that as "no level may be probed"."""
    try:
        available = await ctx.kiln.call("GET", "/api/available_models")
    except KilnError:
        return None
    for entry in available if isinstance(available, list) else []:
        if not isinstance(entry, dict) or entry.get("provider_id") != provider:
            continue
        for row in entry.get("models") or []:
            if isinstance(row, dict) and row.get("id") == model:
                levels = row.get("available_thinking_levels")
                if isinstance(levels, dict):
                    return [str(v) for v in levels.values()]
                return None
    return None


class ProbeSetupError(RuntimeError):
    """The probe could not be set up (parent missing, no train inputs, the
    throwaway surface refused). Answered `unclear`, never an infra retry."""


async def ensure_probe_tool(ctx: Ctx) -> str:
    """The throwaway zero-argument code tool every tool-call probe attaches,
    created once per project (reused by function name). Code trust is
    granted first: without it the create route answers 200 with an empty
    body and creates nothing."""
    pid = ctx.target.project_id
    for row in _rows(await ctx.kiln.call("GET", f"/api/projects/{pid}/code_tools")):
        if row.get("tool_function_name") == PROBE_TOOL_FUNCTION and not row.get(
            "is_archived"
        ):
            return str(row["id"])
    await ctx.kiln.call("POST", f"/api/projects/{pid}/add_code_trust")
    description = (
        "Kiln Harness Tuner probe artifact: fixed-string tool for counting calls."
    )
    created = await ctx.kiln.call(
        "POST",
        f"/api/projects/{pid}/code_tools",
        body={
            "name": PROBE_TOOL_NAME,
            "description": description,
            "tool_function_name": PROBE_TOOL_FUNCTION,
            "tool_description": PROBE_TOOL_DESCRIPTION,
            "parameters_schema": {"type": "object", "properties": {}, "required": []},
            "code": PROBE_TOOL_CODE,
            "timeout_seconds": 10,
            "provenance": agent_provenance(description, []),
        },
    )
    tool_id = created.get("id") if isinstance(created, dict) else None
    if not tool_id:
        raise ProbeSetupError(f"code tool create returned no id: {created!r}")
    return str(tool_id)


async def ensure_probe_skill(ctx: Ctx) -> str:
    pid = ctx.target.project_id
    for row in _rows(await ctx.kiln.call("GET", f"/api/projects/{pid}/skills")):
        if row.get("name") == PROBE_SKILL_NAME and not row.get("is_archived"):
            return str(row["id"])
    created = await ctx.kiln.call(
        "POST",
        f"/api/projects/{pid}/skills",
        body={
            "name": PROBE_SKILL_NAME,
            "description": PROBE_SKILL_DESCRIPTION,
            "body": PROBE_SKILL_BODY,
            "provenance": agent_provenance(
                "Kiln Harness Tuner probe artifact: a fixed checklist skill that probes"
                " attach to count reads. Never part of a candidate.",
                [],
            ),
        },
    )
    skill_id = created.get("id") if isinstance(created, dict) else None
    if not skill_id:
        raise ProbeSetupError(f"skill create returned no id: {created!r}")
    return str(skill_id)


async def fork_props(
    ctx: Ctx, parent_props: dict[str, Any], check: ProbeCheck
) -> dict[str, Any]:
    props = json.loads(json.dumps(parent_props))
    props.update(check.overrides)
    if check.attach != "none":
        tool_id = (
            f"kiln_tool::code::{await ensure_probe_tool(ctx)}"
            if check.attach == "probe_tool"
            else f"kiln_tool::skill::{await ensure_probe_skill(ctx)}"
        )
        tools_config = props.get("tools_config")
        if not isinstance(tools_config, dict):
            tools_config = {}
        tools = [t for t in (tools_config.get("tools") or []) if isinstance(t, str)]
        if tool_id not in tools:
            tools.append(tool_id)
        tools_config["tools"] = tools
        props["tools_config"] = tools_config
    return props


async def create_probe_rc(
    ctx: Ctx, item: ProbeItem, props: dict[str, Any], parent: str
) -> str:
    if ctx.model_policy is not None:
        # The model policy binds code as well as agents: a probe
        # fork drives the task on its model, so it must be a model the policy
        # allows — the same check the hook makes on an agent's run config.
        refusal = ctx.model_policy.refusal(
            props.get("model_provider_name"), props.get("model_name")
        )
        if refusal is not None:
            raise ProbeSetupError(refusal)
    description = (
        f"Kiln Harness Tuner probe {item.id} (ephemeral; fork of {parent} — never a"
        f" parent, never measured). Question: {item.question}"
    )
    created = await ctx.kiln.call(
        "POST",
        f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}/run_configs",
        body={
            "name": _name(f"PROBE {item.id} - {item.question}"),
            "description": description,
            "run_config_properties": props,
            "provenance": agent_provenance(description, [parent]),
        },
    )
    rc_id = created.get("id") if isinstance(created, dict) else None
    if not rc_id:
        raise ProbeSetupError(f"run config create returned no id: {created!r}")
    return str(rc_id)


# ------------------------------------------------------------------ drives


async def drive(
    ctx: Ctx, rc_id: str, props: dict[str, Any], item_id: str, inp: TrainInput
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "run_config_properties": props,
        "tags": [PROBE_RUN_TAG, f"probe_{item_id.replace('-', '_')}"],
        "task_run_config_id": rc_id,
    }
    if inp.structured is not None:
        body["structured_input"] = inp.structured
    else:
        body["plaintext_input"] = inp.plaintext or ""
    run = await ctx.kiln.call(
        "POST",
        f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}/run",
        body=body,
        timeout_secs=DRIVE_TIMEOUT_SECS,
        retries=False,
    )
    return run if isinstance(run, dict) else {}


def _tool_calls(msg: dict[str, Any]) -> list[dict[str, Any]]:
    calls = msg.get("tool_calls")
    return [c for c in calls if isinstance(c, dict)] if isinstance(calls, list) else []


def _fn_name(call: dict[str, Any]) -> str:
    fn = call.get("function")
    if isinstance(fn, dict):
        return str(fn.get("name") or "")
    return str(call.get("name") or "")


def count_metric(run: dict[str, Any], count: str) -> float:
    """One trace-level number per drive (see `ProbeCount`)."""
    trace = [m for m in (run.get("trace") or []) if isinstance(m, dict)]
    assistant = [m for m in trace if m.get("role") == "assistant"]
    output = (
        (run.get("output") or {}).get("output")
        if isinstance(run.get("output"), dict)
        else None
    )
    output = output if isinstance(output, str) else ""
    if count == "tool_calls":
        return float(sum(len(_tool_calls(m)) for m in assistant))
    if count == "skill_reads":
        return float(
            sum(1 for m in assistant for c in _tool_calls(m) if _fn_name(c) == "skill")
        )
    if count == "reasoning_chars":
        in_trace = sum(len(m.get("reasoning_content") or "") for m in assistant)
        intermediate = run.get("intermediate_outputs")
        stored = (
            len(str(intermediate.get("reasoning") or ""))
            if isinstance(intermediate, dict)
            else 0
        )
        return float(max(in_trace, stored))
    if count == "output_chars":
        return float(len(output))
    if count == "json_valid":
        try:
            json.loads(output)
        except ValueError:
            return 0.0
        return 1.0
    raise ValueError(f"unknown probe count {count!r}")


def _cost(run: dict[str, Any]) -> float:
    for key in ("cumulative_usage", "usage"):
        usage = run.get(key)
        if isinstance(usage, dict) and isinstance(usage.get("cost"), (int, float)):
            return float(usage["cost"])
    return 0.0


@dataclass
class DriveSet:
    values: list[float]
    item_ids: list[str]
    errors: list[str]
    cost_usd: float = 0.0
    # Item id -> the drive's task latency (its own `usage`), and
    # the ids whose drive failed (for the quota diagnosis).
    latencies: dict[str, float] = field(default_factory=dict)
    failed_ids: list[str] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)  # item -> output


def _stop_on_quota(
    rc_id: str, inp: TrainInput, text: str, cause: Exception | None = None
) -> None:
    error_class = classify_provider_message(text)
    if error_class in ("quota_exhausted", "auth"):
        raise ProviderQuotaError(
            ErrorInfo(
                error_class=error_class,  # type: ignore[arg-type]
                detail=f"probe drive of rc {rc_id} on {inp.id}: {text}",
            )
        ) from cause


async def drive_set(
    ctx: Ctx,
    rc_id: str,
    props: dict[str, Any],
    item_id: str,
    inputs: list[TrainInput],
    count: str,
    via_jobs: bool = False,
) -> DriveSet:
    """Drive `rc_id` once on each input and count `count` in each trace.
    `via_jobs`: each drive is a one-item train eval job
    (`kiln_harness_tuner.job_drives`) — for a task the run endpoint cannot drive."""
    from kiln_harness_tuner.job_drives import driver_for, eval_of_input

    sem = asyncio.Semaphore(MAX_CONCURRENT_DRIVES)
    out = DriveSet(values=[], item_ids=[], errors=[])

    async def by_job(inp: TrainInput) -> tuple[dict[str, Any] | None, float, str]:
        eval_id = await eval_of_input(ctx, inp, "train")
        if eval_id is None:
            return None, 0.0, f"{inp.id}: in no eval's train split"
        outcome = await driver_for(ctx).drive(rc_id, inp.id, eval_id, "train")
        if outcome.run is None:
            text = outcome.error or "the eval job returned no drive"
            _stop_on_quota(rc_id, inp, text)
            return None, outcome.overhead_cost, f"{inp.id}: {text}"[:300]
        return outcome.run, outcome.overhead_cost, ""

    async def one(
        inp: TrainInput,
    ) -> tuple[str, float | None, float, str | None, float | None]:
        async with sem:
            overhead = 0.0
            if via_jobs:
                found, overhead, text = await by_job(inp)
                if found is None:
                    return inp.id, None, overhead, text, None
                run = found
            else:
                try:
                    run = await drive(ctx, rc_id, props, item_id, inp)
                except KilnError as exc:
                    _stop_on_quota(rc_id, inp, str(exc), cause=exc)
                    return inp.id, None, 0.0, f"{inp.id}: {exc}"[:300], None
            out.outputs[inp.id] = run_output(run)
            return (
                inp.id,
                count_metric(run, count),
                _cost(run) + overhead,
                None,
                latency_of_task_run(run),
            )

    for iid, value, cost, err, ms in await asyncio.gather(*(one(i) for i in inputs)):
        out.cost_usd += cost
        if err is not None:
            out.errors.append(err)
            out.failed_ids.append(iid)
            continue
        out.item_ids.append(iid)
        out.values.append(value)  # type: ignore[arg-type]
        if ms is not None:
            out.latencies[iid] = ms
    return out


# ------------------------------------------------------------------ answer


def _fmt(values: list[float]) -> str:
    return "[" + ", ".join(f"{v:g}" for v in values) + "]"


def decide(
    check: ProbeCheck, fork: DriveSet, parent: DriveSet | None
) -> tuple[str, str]:
    """(answer, one sentence). Mechanical, so the planner can trust the
    same words to mean the same thing on every probe."""
    if not fork.values:
        return "unclear", "no drive returned a trace"
    positives = sum(1 for v in fork.values if v > 0)
    if not check.compare_to_parent or parent is None:
        if check.effective_rule == "all":
            answer = "yes" if positives == len(fork.values) else "no"
            return (
                answer,
                f"{check.count} > 0 on {positives}/{len(fork.values)} drives (all)",
            )
        answer = "yes" if positives > 0 else "no"
        return (
            answer,
            f"{check.count} > 0 on {positives}/{len(fork.values)} drives (rule: any)",
        )
    if not parent.values:
        return "unclear", "the parent drives returned no trace to compare against"
    if len(fork.values) < 2 or len(parent.values) < 2:
        return (
            "unclear",
            "fewer than 2 paired drives on a side; the comparison is not settled",
        )
    fm, pm = fmean(fork.values), fmean(parent.values)
    if fm == 0 and pm == 0:
        return "no", f"{check.count} is 0 on both sides"
    if fm == 0 or pm == 0:
        return "yes", f"{check.count} mean moved {pm:g} → {fm:g}{_NO_QUALITY}"
    ratio = fm / pm
    moved = ratio >= _RATIO or ratio <= 1 / _RATIO
    return (
        "yes" if moved else "no",
        f"{check.count} mean {pm:g} → {fm:g} (×{ratio:.2f}; moved = ×≥{_RATIO})"
        + (_NO_QUALITY if moved else ""),
    )


async def run_code_probe(
    ctx: Ctx, item: ProbeItem, baseline: BaselineRef
) -> tuple[ProbeResult, float]:
    """Answer a code probe. Returns (result, provider cost). Setup faults
    (`ProbeSetupError`, a Kiln refusal while forking) are answered
    `unclear` with the reason — an unanswered probe is information, not an
    infra retry."""
    check = item.check
    assert check is not None
    parent_id = item.parent_rc_id or baseline.rc_id
    if check.source == "stored":
        return await _stored_probe(ctx, item, parent_id), 0.0
    chosen: list[TrainInput] = []
    try:
        train_filter = await train_filter_of(ctx)
        if train_filter is None:
            raise ProbeSetupError("no active eval names a train split")
        inputs = load_train_inputs(
            ctx.target.project_path, ctx.target.task_id, train_filter
        )
        if not inputs:
            raise ProbeSetupError(
                f"no drivable eval inputs tagged {train_filter} under"
                f" {ctx.target.project_path}"
            )
        chosen = select_inputs(inputs, item.max_drives, check.select)
        parent_props = await _parent_props(ctx, parent_id)
        props = await fork_props(ctx, parent_props, check)
        rc_id = await create_probe_rc(ctx, item, props, parent_id)
    except (ProbeSetupError, KilnError) as exc:
        return (
            ProbeResult(answer="unclear", evidence=f"probe setup failed: {exc}"[:1500]),
            0.0,
        )
    # A probe answers a mechanism question only: it is never
    # timed — speed and cost are measured by the engine on a screening
    # candidate or an arm. The parent is driven only to compare the count.
    from kiln_harness_tuner.job_drives import driver_for

    via_jobs = await driver_for(ctx).needed([props, parent_props], chosen)
    parent: DriveSet | None = None
    if check.compare_to_parent:
        fork, parent = await asyncio.gather(
            drive_set(ctx, rc_id, props, item.id, chosen, check.count, via_jobs),
            drive_set(
                ctx, parent_id, parent_props, item.id, chosen, check.count, via_jobs
            ),
        )
    else:
        fork = await drive_set(
            ctx, rc_id, props, item.id, chosen, check.count, via_jobs
        )
    failed = [(rc_id, i) for i in fork.failed_ids] + (
        [(parent_id, i) for i in parent.failed_ids] if parent else []
    )
    if failed and not via_jobs:
        # A failed probe drive shows only the server's generic 500; a
        # one-item eval job shows the provider's text, and a quota or auth
        # failure there stops the run. An eval-job drive has
        # that text already.
        await ctx.analyzer.diagnose_drive_failure(
            ctx.target.project_path, failed[0][0], failed[0][1]
        )
    answer, sentence = decide(check, fork, parent if check.compare_to_parent else None)
    change = (
        ", ".join(f"{k}={v!r}" for k, v in check.overrides.items()) or "no overrides"
    )
    evidence = (
        f"{sentence}. Ephemeral rc {rc_id} = fork of {parent_id},"
        f" attach={check.attach},"
        f" {change}; {check.count} per drive {_fmt(fork.values)} on items"
        f" {fork.item_ids}"
    )
    if parent is not None:
        evidence += f"; parent {parent_id} on the same items {_fmt(parent.values)}"
    errors = fork.errors + (parent.errors if parent else [])
    if errors:
        evidence += f"; {len(errors)} drive error(s): " + " | ".join(errors[:3])
    drives = len(fork.values) + (len(parent.values) if parent else 0)
    result = ProbeResult(
        answer=answer,  # type: ignore[arg-type]
        evidence=evidence[:1500],
        drives=drives,
        ephemeral_rc_ids=[rc_id],
        notes=None,
    )
    return result, fork.cost_usd + (parent.cost_usd if parent else 0.0)


async def _stored_probe(ctx: Ctx, item: ProbeItem, parent_id: str) -> ProbeResult:
    """A DATA question, answered by code from stored data: the
    count over every stored train row of the parent on the active evals — no
    fork, no drive, no Claude session. "How many of the baseline's train
    answers call the tool at all?" is read off the rows the run already
    has."""
    from kiln_harness_tuner.analysis import _runs_by_item
    from kiln_harness_tuner.orchestrator import _active_evals

    check = item.check
    assert check is not None
    values: list[float] = []
    ids: list[str] = []
    seen: set[str] = set()
    for eval_def, config_id in await _active_evals(ctx):
        try:
            rows = await ctx.analyzer.stored_results(
                str(eval_def["id"]), config_id, parent_id, "train"
            )
        except KilnError:
            continue
        for item_id, row in sorted(_runs_by_item(rows).items()):
            if item_id in seen:
                continue
            seen.add(item_id)
            ids.append(item_id)
            values.append(count_metric(_as_run(row), check.count))
    if not values:
        return ProbeResult(
            answer="unclear",
            evidence=f"no stored train row of {parent_id} to count {check.count} on",
        )
    answer, sentence = decide(
        check, DriveSet(values=values, item_ids=ids, errors=[]), None
    )
    return ProbeResult(
        answer=answer,  # type: ignore[arg-type]
        evidence=(
            f"{sentence}. Counted {check.count} over {len(values)} stored train"
            f" row(s) of {parent_id} (no drive): {_fmt(values[:40])}"
        )[:1500],
        drives=0,
    )


def _as_run(row: dict[str, Any]) -> dict[str, Any]:
    """An eval run's stored trace and output in the shape `count_metric`
    reads (a task run's)."""
    trace = row.get("task_run_trace") or row.get("trace") or []
    if isinstance(trace, str):
        try:
            trace = json.loads(trace)
        except ValueError:
            trace = []
    output = row.get("output")
    if isinstance(output, str):
        output = {"output": output}
    return {
        "trace": trace if isinstance(trace, list) else [],
        "output": output if isinstance(output, dict) else {},
        "intermediate_outputs": row.get("intermediate_outputs"),
    }


# ----------------------------------------------------------------- recon


def recon_items(baseline_props: dict[str, Any]) -> list[ProbeItem]:
    """The standing reconnaissance battery on the baseline (wave 0, item ids
    `r0-n`): the MECHANISM facts the first planner would otherwise assume.

    Shaped by the task: the tool probes run only when the
    baseline has a tool attached, the skill probe only when it has a skill —
    on a bare baseline they would spend half the battery on a surface the
    task does not use. Thinking levels are not probed here: a thinking level
    is a candidate variant, screened in code on the screening subset
    (`kiln_harness_tuner.viability.initial_screen_request` opens every speed or cost
    run with one such round)."""
    mode = baseline_props.get("structured_output_mode") or "default"
    tools_config = baseline_props.get("tools_config")
    tools = [
        str(t)
        for t in ((tools_config or {}).get("tools") or [])
        if isinstance(tools_config, dict)
    ]
    has_skill = any(t.startswith("kiln_tool::skill::") for t in tools)
    has_tool = any(not t.startswith("kiln_tool::skill::") for t in tools)
    json_mode = mode in (
        "json_schema",
        "json_instructions",
        "json_mode",
        "function_calling",
    )
    items: list[ProbeItem] = []

    def add(n: int, question: str, check: ProbeCheck, drives: int = 3) -> None:
        items.append(
            ProbeItem(
                id=f"r0-{n}",
                type="probe",
                question=question,
                method="code: fork, apply the check, drive train items, count traces",
                max_drives=drives,
                reason="setup reconnaissance battery",
                check=check,
            )
        )

    if has_tool:
        add(
            1,
            f"Does the baseline model call an attached tool under its output mode"
            f" ({mode})?",
            ProbeCheck(attach="probe_tool", count="tool_calls"),
        )
    if has_skill:
        add(
            2,
            f"Does the baseline model read an attached skill under its output mode"
            f" ({mode})?",
            ProbeCheck(attach="probe_skill", count="skill_reads"),
        )
    if has_tool and mode == "json_schema":
        add(
            3,
            "Does the baseline model call an attached tool under json_instructions"
            " (the alternate structured-output mode)?",
            ProbeCheck(
                attach="probe_tool",
                overrides={"structured_output_mode": "json_instructions"},
                count="tool_calls",
            ),
        )
    if json_mode:
        add(
            6,
            "Does every baseline output parse as JSON?",
            ProbeCheck(count="json_valid", rule="all"),
        )
    add(
        7,
        "Does the baseline return a non-empty output on the 3 longest train inputs?",
        ProbeCheck(count="output_chars", rule="all", select="longest"),
    )
    return items
