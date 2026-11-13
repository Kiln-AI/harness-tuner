"""The one step that runs the smell detectors on a baseline.

`run_detectors` collects the inputs once, runs each registered detector,
and keeps the detections whose smell is a smell document. It never raises:
a detector that raises is skipped, and inputs that cannot be read give no
detection. Code computes the facts; the planner and the baseline reviewer
interpret them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from kiln_harness_tuner.gateway.trace_files import parse_trace, trace_stats
from kiln_harness_tuner.kiln import KilnError
from kiln_harness_tuner.knowledge import load
from kiln_harness_tuner.prompt_text import SKILL_TOOL_PREFIX, rc_texts
from kiln_harness_tuner.provider_errors import ProviderQuotaError
from kiln_harness_tuner.schemas import BaselineRef, Detection
from kiln_harness_tuner.smells.detectors import DETECTORS
from kiln_harness_tuner.smells.model import Detector, DetectorInputs

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx


async def ensure_detections(ctx: Ctx, baseline: BaselineRef) -> None:
    """Run the detectors once for each baseline. At wave 1 the baseline is
    the first one; after a promotion the next wave sees a new rc id, so they
    run again. `ctx.detections` is replaced, never appended to: it always
    describes the present baseline.

    The component design runs the detectors inside `ensure_baseline_reviewed`,
    which comes with the baseline reviewer in a later phase. Until then this
    is the call site, and that function calls it first.

    It never raises, apart from `ProviderQuotaError`: a raise before wave 1
    would end the run with no report."""
    if ctx.detected_rc_id == baseline.rc_id:
        return
    ctx.detected_rc_id = baseline.rc_id
    try:
        ctx.detections = await run_detectors(ctx, baseline.rc_id)
    except ProviderQuotaError:
        raise
    except Exception as exc:
        ctx.detections = []
        ctx.log.event(
            "smell_detection_skipped",
            rc=baseline.rc_id,
            reason=f"{type(exc).__name__}: {exc}",
        )


async def run_detectors(
    ctx: Ctx, rc_id: str, *, detectors: Sequence[Detector] = DETECTORS
) -> list[Detection]:
    """The detections of the run config `rc_id`, with the events
    `smell_detector_failed`, `smell_unknown`, `smell_detection_skipped` and
    `smell_detections`.

    The stored train rows are read only when a detector reads traces (the
    component design reads them each time): no V1 detector does, and the
    detectors are meant to cost nothing."""
    try:
        inputs, unread_tools = await _collect(
            ctx, rc_id, traces=any(d.reads_traces for d in detectors)
        )
    except ProviderQuotaError:
        raise
    except Exception as exc:
        # A raise here would end the run before wave 1 with no report.
        ctx.log.event(
            "smell_detection_skipped", rc=rc_id, reason=f"{type(exc).__name__}: {exc}"
        )
        return []
    found: list[Detection] = []
    for detector in detectors:
        try:
            # A return that is not a list of detections counts as a failure
            # of that detector, like a raise.
            found += [Detection.model_validate(d) for d in detector.run(inputs)]
        except Exception as exc:
            ctx.log.event(
                "smell_detector_failed",
                detector=detector.id,
                reason=f"{type(exc).__name__}: {exc}",
            )
    known = _smell_ids(ctx)
    detections = []
    for detection in found:
        if known is not None and detection.smell not in known:
            ctx.log.event(
                "smell_unknown", detector=detection.detector, smell=detection.smell
            )
            continue
        detections.append(detection)
    ctx.log.event(
        "smell_detections",
        rc=rc_id,
        count=len(detections),
        detections=detections,
        unread_tools=unread_tools,
    )
    return detections


def _smell_ids(ctx: Ctx) -> set[str] | None:
    """The ids of the smell documents, or None when the knowledge skill
    cannot be loaded (the detections are then kept unchecked)."""
    try:
        return {doc.id for doc in load().by_kind("smells")}
    except Exception as exc:  # KnowledgeError, or an OSError while reading a document
        ctx.log.event("knowledge_unreadable", reason=str(exc))
        return None


async def _collect(
    ctx: Ctx, rc_id: str, *, traces: bool
) -> tuple[DetectorInputs, list[str]]:
    """The inputs of every detector, and the attached tools whose definition
    could not be read. Raises when the run config cannot be read."""
    from kiln_harness_tuner.probes import task_run_configs

    listing = await task_run_configs(ctx)
    row = next((r for r in listing if str(r.get("id")) == rc_id), None)
    texts = (await rc_texts(ctx, [rc_id], listing=listing))[rc_id]
    if row is None or not texts.found:
        raise LookupError(f"run config {rc_id} is not listed")
    properties = row.get("run_config_properties")
    properties = properties if isinstance(properties, dict) else {}
    base = f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}"
    definitions: list[dict[str, Any]] = []
    unread: list[str] = []
    for tool_id in texts.tool_ids:
        if tool_id.startswith(SKILL_TOOL_PREFIX):
            # A skill is read through `texts.skills`; Kiln's definition
            # route does not resolve skill ids.
            continue
        try:
            response = await ctx.kiln.call("GET", f"{base}/tools/{tool_id}/definition")
        except KilnError:
            response = None
        definition = _definition(tool_id, response)
        if definition is None:
            unread.append(tool_id)
        else:
            definitions.append(definition)
    return (
        DetectorInputs(
            rc_id=rc_id,
            properties=properties,
            texts=texts,
            tool_definitions=definitions,
            trace_stats=await _train_trace_stats(ctx, rc_id) if traces else [],
        ),
        unread,
    )


def _definition(tool_id: str, row: Any) -> dict[str, Any] | None:
    """One tool definition as `tool_id`, `name`, `description` and
    `parameters`. The route answers with `function_name` at the top level;
    the OpenAI form (`{"function": {...}}`) is read too."""
    if not isinstance(row, dict):
        return None
    function = row.get("function") if isinstance(row.get("function"), dict) else row
    return {
        "tool_id": tool_id,
        "name": function.get("function_name") or function.get("name") or tool_id,
        "description": function.get("description"),
        "parameters": function.get("parameters"),
    }


async def _train_trace_stats(ctx: Ctx, rc_id: str) -> list[dict[str, Any]]:
    """The trace stats of each stored train run of the baseline, on every
    active eval. An eval whose rows cannot be read gives none; the train
    read refuses held-out rows (`HeldOutLeakError`), and then none of that
    eval's rows are used."""
    from kiln_harness_tuner.analysis import AnalysisError
    from kiln_harness_tuner.orchestrator import _active_evals

    try:
        active = await _active_evals(ctx)
    except KilnError:
        return []
    stats: list[dict[str, Any]] = []
    for eval_def, eval_config_id in active:
        eval_id = str(eval_def["id"])
        try:
            rows = await ctx.analyzer.stored_results(
                eval_id, eval_config_id, rc_id, "train"
            )
        except (KilnError, AnalysisError):
            continue
        for run in rows:
            if not isinstance(run, dict):
                continue
            messages, _tools = parse_trace(run.get("task_run_trace"))
            if not messages:
                continue
            stats.append(
                {
                    "eval_id": eval_id,
                    "item_id": run.get("dataset_id") or run.get("eval_input_id"),
                    **trace_stats(messages, run.get("task_run_usage")),
                }
            )
    return stats
