"""The one step that finds smells in a baseline: the detectors in code, then
the baseline reviewer.

`ensure_baseline_reviewed` runs once for each baseline, at the top of a
wave. It runs the detectors (`ensure_detections`), then dispatches the
reviewer, and code writes one smell issue for each finding it validates.
`run_detectors` collects the inputs once, runs each registered detector,
and keeps the detections whose smell is a smell document. Neither raises,
apart from `ProviderQuotaError`. Code computes the facts; the planner and
the baseline reviewer interpret them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from kiln_harness_tuner.gateway.trace_files import parse_trace, trace_stats
from kiln_harness_tuner.kiln import KilnError
from kiln_harness_tuner.knowledge import load
from kiln_harness_tuner.memory_schema import fits_tag
from kiln_harness_tuner.prompt_text import SKILL_TOOL_PREFIX, rc_texts
from kiln_harness_tuner.provider_errors import ProviderQuotaError
from kiln_harness_tuner.schemas import (
    SUBSCRIPTION_LANE,
    BaselineRef,
    BaselineReview,
    BaselineReviewInput,
    Detection,
    SmellFinding,
    TraceSample,
)
from kiln_harness_tuner.smells.detectors import DETECTORS
from kiln_harness_tuner.smells.model import Detector, DetectorInputs

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx


# The trace sample of the baseline reviewer: the cost of the review has a
# limit, and the reviewer can ask the gateway for more.
REVIEW_ITEMS_PER_EVAL = 4
REVIEW_MAX_ITEMS = 20
# The reviewer's skill asks for at most this many findings; code keeps the
# first ones of a longer list.
REVIEW_MAX_FINDINGS = 15
REVIEW_REASON_CHARS = 300
# The review sits before the planner on the run's clock: one retry at most,
# and none after a session that hit its time cap (`timeout`). A late
# failure of another class, or the dispatcher's in-session repair of bad
# output, can still take more than the one timeout `_too_little_time`
# allows for; the deadline check after the review covers that.
REVIEW_DISPATCH_BACKOFFS: tuple[float, ...] = (30.0,)
REVIEW_TERMINAL_ERRORS = ("parse_failure", "timeout")


async def ensure_baseline_reviewed(
    ctx: Ctx,
    baseline: BaselineRef,
    *,
    wrap_up: bool = False,
    deadline: float | None = None,
) -> bool:
    """Find the smells of the present baseline, once for each baseline: the
    detectors in code, then the baseline reviewer. At wave 1 the baseline is
    the first one; after a promotion the next wave sees a new rc id, so the
    step runs again. A run that ends on the wave of a promotion pays for no
    review that no planner would read.

    It never raises, apart from `ProviderQuotaError`: a raise before wave 1
    would end the run with no report. A review that cannot run is recorded
    as `failed`, and the run goes on with the code detections.

    No session is dispatched on a wrap-up wave (`wrap_up`), or when the
    time to `deadline` is under the minimum to start a wave plus the
    review's timeout: no planner after it would read the review. Such a
    review is `skipped`, and the detectors still run.

    Returns True when the review phase ran (it takes minutes on the run's
    clock, so the caller checks the deadline again)."""
    if ctx.reviewed_rc_id == baseline.rc_id:
        return False
    ctx.reviewed_rc_id = baseline.rc_id
    not_run = None
    if not ctx.baseline_review:
        not_run = BaselineReview(
            rc_id=baseline.rc_id, status="off", reason="turned off"
        )
    elif wrap_up or _too_little_time(ctx, deadline):
        not_run = BaselineReview(
            rc_id=baseline.rc_id,
            status="skipped",
            reason="a wrap-up wave" if wrap_up else "too little time",
        )
    if not_run is not None:
        # No session runs, so no phase opens: the detectors are free reads.
        await ensure_detections(ctx, baseline)
        _record(ctx, not_run)
        return False
    started = ctx.clock()
    ctx.log.event(
        "phase_start",
        name="baseline_review",
        label="Baseline review: reading the design of the baseline for smells…",
    )
    try:
        _record(ctx, await _review_or_failure(ctx, baseline))
    finally:
        ctx.log.event(
            "phase_end", name="baseline_review", elapsed_secs=ctx.clock() - started
        )
    return True


def _too_little_time(ctx: Ctx, deadline: float | None) -> bool:
    """True when a review of its full timeout would leave less than the
    minimum to start a wave before `deadline`."""
    if deadline is None:
        return False
    from kiln_harness_tuner.dispatch.base import BASELINE_REVIEW_TIMEOUT_SECS
    from kiln_harness_tuner.orchestrator import MIN_WAVE_START_SECS

    needed = MIN_WAVE_START_SECS + BASELINE_REVIEW_TIMEOUT_SECS
    return deadline - ctx.clock() < needed


async def _review_or_failure(ctx: Ctx, baseline: BaselineRef) -> BaselineReview:
    """The detectors, then the review; an exception other than
    `ProviderQuotaError` becomes a `failed` review."""
    try:
        await ensure_detections(ctx, baseline)
        return await _review(ctx, baseline.rc_id)
    except ProviderQuotaError:
        raise
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        return BaselineReview(
            rc_id=baseline.rc_id, status="failed", reason=reason[:REVIEW_REASON_CHARS]
        )


async def _review(ctx: Ctx, rc_id: str) -> BaselineReview:
    """One review of the run config `rc_id`: skipped (a resumed lineage
    already holds its smell issues), failed (the session gave no result),
    or done, with one smell issue for each finding. A write that fails is
    logged and named in `reason`; its `issue_ids` entry is empty."""
    known_issues = await ctx.memory.find_smell_issues(rc_id)
    if known_issues:
        return BaselineReview(
            rc_id=rc_id,
            status="skipped",
            reason="already reviewed",
            issue_ids=[issue.id for issue in known_issues],
        )
    from kiln_harness_tuner.items import call_with_retries

    titles = _smell_titles(ctx)
    result = await call_with_retries(
        ctx.dispatch.baseline_review,
        BaselineReviewInput(
            baseline=BaselineRef(rc_id=rc_id),
            detections=list(ctx.detections),
            samples=await baseline_trace_samples(ctx, rc_id),
            known_smells=sorted(titles or ()),
        ),
        backoffs=REVIEW_DISPATCH_BACKOFFS,
        sleep=ctx.sleep,
        log=ctx.log,
        source="baseline_review",
        terminal_errors=REVIEW_TERMINAL_ERRORS,
    )
    ctx.log.event(
        "spend",
        source="baseline_review",
        lane=SUBSCRIPTION_LANE,
        tokens=result.usage.tokens,
    )
    if result.data is None:
        why = result.error.detail if result.error else (result.detail or "")
        kind = (
            f"{result.status} {result.error.error_class}"
            if result.error
            else (result.status)
        )
        return BaselineReview(
            rc_id=rc_id,
            status="failed",
            reason=f"the review did not run ({kind}: {why})"[:REVIEW_REASON_CHARS],
        )
    findings = _valid_findings(ctx, result.data.findings, titles)
    issue_ids: list[str] = []
    unwritten: list[str] = []
    for finding in findings:
        # Each write on its own: a failed write keeps the issues already
        # written and the findings after it.
        try:
            issue_ids.append(
                await ctx.memory.record_smell(
                    finding, rc_id, title=(titles or {}).get(finding.smell)
                )
            )
        except ProviderQuotaError:
            raise
        except Exception as exc:
            issue_ids.append("")
            unwritten.append(finding.smell)
            ctx.log.event(
                "smell_issue_write_failed",
                rc=rc_id,
                smell=finding.smell,
                reason=f"{type(exc).__name__}: {exc}"[:REVIEW_REASON_CHARS],
            )
    reason = ""
    if unwritten:
        reason = (
            f"{len(unwritten)} of {len(findings)} finding(s) could not be written"
            f" as issues: {', '.join(unwritten)}"
        )[:REVIEW_REASON_CHARS]
    return BaselineReview(
        rc_id=rc_id,
        status="done",
        reason=reason,
        findings=findings,
        issue_ids=issue_ids,
        detection_checks=result.data.detection_checks,
        notes=result.data.notes,
    )


def _valid_findings(
    ctx: Ctx, returned: list[SmellFinding], titles: dict[str, str] | None
) -> list[SmellFinding]:
    """The findings that code writes, at most `REVIEW_MAX_FINDINGS`, one
    for each smell. A finding is dropped, with `smell_unknown`, when its
    smell is not a smell document, or, when the knowledge skill cannot be
    read, when its id does not fit a memory tag. Two findings of one smell
    are merged."""
    by_smell: dict[str, SmellFinding] = {}
    for finding in returned:
        known = finding.smell in titles if titles is not None else True
        if not known or not fits_tag(finding.smell):
            ctx.log.event(
                "smell_unknown", source="baseline_review", smell=finding.smell
            )
            continue
        first = by_smell.get(finding.smell)
        by_smell[finding.smell] = finding if first is None else _merged(first, finding)
    return list(by_smell.values())[:REVIEW_MAX_FINDINGS]


def _merged(first: SmellFinding, second: SmellFinding) -> SmellFinding:
    """One finding from two of the same smell: the evidence and the
    confidence of both, and the places of both, each cut to its limit."""
    return SmellFinding(
        smell=first.smell,
        evidence=f"{first.evidence} {second.evidence}"[:900],
        where=list(dict.fromkeys([*first.where, *second.where]))[:12],
        confidence=f"{first.confidence} / {second.confidence}"[:300],
    )


def _record(ctx: Ctx, review: BaselineReview) -> None:
    """Keep the review for the report, and emit it. Every status but `done`
    also emits `baseline_review_skipped` with the reason."""
    ctx.baseline_reviews.append(review)
    if review.status != "done":
        ctx.log.event(
            "baseline_review_skipped",
            rc=review.rc_id,
            status=review.status,
            reason=review.reason,
        )
    ctx.log.event("baseline_review", review=review, line=review_line(review))


def review_line(review: BaselineReview) -> str:
    """The review's one plain line for the displays."""
    head = f"baseline review: {review.rc_id}"
    if review.status == "done":
        smells = ", ".join(f.smell for f in review.findings)
        line = f"{head} — {len(review.findings)} smell finding(s)"
        line += f": {smells}" if smells else ""
        return line + (f" ({review.reason})" if review.reason else "")
    return f"{head} — {review.status}: {review.reason}"


async def baseline_trace_samples(ctx: Ctx, rc_id: str) -> list[TraceSample]:
    """The train items whose stored baseline traces the reviewer starts
    from, for each active eval: up to `REVIEW_ITEMS_PER_EVAL`, and at most
    `REVIEW_MAX_ITEMS` in all. The items are the eval's stored train rows,
    sorted by id, which is the order of the train set file
    (`gateway.trainset`), so a second run chooses the same ones. While the
    total is over the limit, the largest share is cut by one, the last eval
    first. Whether an item passed plays no part. An eval whose rows cannot
    be read gives an empty sample."""
    from kiln_harness_tuner.analysis import AnalysisError
    from kiln_harness_tuner.orchestrator import _active_evals

    try:
        active = await _active_evals(ctx)
    except KilnError:
        return []
    found: list[tuple[str, list[str]]] = []
    for eval_def, eval_config_id in active:
        eval_id = str(eval_def["id"])
        try:
            rows = await ctx.analyzer.stored_results(
                eval_id, eval_config_id, rc_id, "train"
            )
        except (KilnError, AnalysisError):
            # The train read refuses held-out rows (`HeldOutLeakError`); then
            # none of that eval's rows are used.
            rows = []
        ids = {
            str(item_id)
            for run in rows
            if isinstance(run, dict)
            and (item_id := run.get("dataset_id") or run.get("eval_input_id"))
        }
        found.append((eval_id, sorted(ids)))
    shares = [min(REVIEW_ITEMS_PER_EVAL, len(ids)) for _, ids in found]
    while sum(shares) > REVIEW_MAX_ITEMS:
        largest = max(range(len(shares)), key=lambda i: (shares[i], i))
        shares[largest] -= 1
    return [
        TraceSample(eval_id=eval_id, item_ids=ids[:share])
        for (eval_id, ids), share in zip(found, shares, strict=True)
    ]


async def ensure_detections(ctx: Ctx, baseline: BaselineRef) -> None:
    """Run the detectors once for each baseline (`ensure_baseline_reviewed`
    calls it first). `ctx.detections` is replaced, never appended to: it
    always describes the present baseline.

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
    known = _smell_titles(ctx)
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


def _smell_titles(ctx: Ctx) -> dict[str, str] | None:
    """The id and the title of each smell document, or None when the
    knowledge skill cannot be loaded (the detections and the findings are
    then kept unchecked)."""
    try:
        return {
            doc.id: doc.meta.title if doc.meta is not None else doc.id
            for doc in load().by_kind("smells")
        }
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
