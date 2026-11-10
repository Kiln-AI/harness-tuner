"""execute_plan_item — one BatchPlan item from planned to measured ItemOutcome
(docs/architecture.md section 4). One-crack and eval-freeze are visible in its shape:
the builder returns before results exist; the analyzer gets data and ids, never an
edit surface.

Three item types: `experiment` (build → measure → analyze), `eval_only`
(commission a NEW eval), and `measure_issue_baseline` (run an
EXISTING eval against the baseline to settle an `UNMEASURED:` issue). The last
is the only one that dispatches no agent at all: pure code, one eval run, one
memory PATCH.

Also home of the agent-dispatch retry ladder shared with the loop-level
dispatches (orchestrator): backoff 30s/60s/120s for wave items, plus one extra
5-min attempt for the singular load-bearing calls (batch_plan / promote_best /
report_writer). Only `failed_infra` retries, and never `parse_failure` — that
class already spent its one in-session repair inside the dispatcher and is
terminal.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar

from kiln_harness_tuner.analysis import (
    AnalysisError,
    MeasurementFailed,
    expand_targets,
    pairable_item_count,
    partial_measurement_error,
    reproduction_verdict,
    runs_cost,
    speed_rule_text,
    success_by_rule,
    success_by_speed_rule,
)
from kiln_harness_tuner.generality import GeneralityCheck, check_generality
from kiln_harness_tuner.judge_copy import JudgeCopyCheck, check_judge_copies
from kiln_harness_tuner.latency import latency_line, sample_inputs, split_inputs
from kiln_harness_tuner.measure import (
    Candidate,
    Measured,
    MeasureSpec,
    arm_estimates,
    measure,
)
from kiln_harness_tuner.memory import (
    IssueWriteRefused,
    MemoryRecordNotFoundError,
    MemoryWriteRefused,
)
from kiln_harness_tuner.memory_schema import RecordKindError
from kiln_harness_tuner.objective import decisive, is_efficiency
from kiln_harness_tuner.probes import rc_props, run_code_probe
from kiln_harness_tuner.provider_errors import ProviderQuotaError
from kiln_harness_tuner.reference_check import ReferenceCheck, check_references
from kiln_harness_tuner.schemas import (
    PROVIDER_LANE,
    SUBSCRIPTION_LANE,
    AgentResult,
    AnalysisBundle,
    AnalysisInput,
    BaselineRef,
    BatchItem,
    ErrorInfo,
    EvalBuildInput,
    EvalBuildReport,
    EvalOnlyItem,
    EvalTarget,
    ExperimentItem,
    FixWork,
    ItemOutcome,
    LatencyComparison,
    MeasureIssueBaselineItem,
    MeasurementSummary,
    ProbeAnswer,
    ProbeInput,
    ProbeItem,
    ProbeResult,
    ProbeTrainInput,
    RcBuildInput,
    RcBuildResult,
    RemeasureItem,
    ResidualMiss,
    Usage,
    dump_or_none,
)
from kiln_harness_tuner.snapshots import snapshot_memory
from kiln_harness_tuner.status import item_kind
from kiln_harness_tuner.stop_rules import (
    default_stop_rules,
    merge_rules,
    noise_flip_rate,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx, EventLog

T = TypeVar("T")

# Three retries, one per listed backoff (30s/60s/120s = 4 calls total);
# loop-level dispatches add one extra long-backoff attempt (5 min) — they are
# singular and load-bearing, no wave of siblings absorbs the loss.
ITEM_DISPATCH_BACKOFFS: tuple[float, ...] = (30.0, 60.0, 120.0)
LOOP_DISPATCH_BACKOFFS: tuple[float, ...] = (30.0, 60.0, 120.0, 300.0)


def _sum_usage(a: Usage, b: Usage) -> Usage:
    return Usage(
        cost_usd=a.cost_usd + b.cost_usd,
        tokens=a.tokens + b.tokens,
        wall_secs=a.wall_secs + b.wall_secs,
        unmeasured_tokens=a.unmeasured_tokens + b.unmeasured_tokens,
    )


async def call_with_retries(
    fn: Callable[..., Awaitable[AgentResult[T]]],
    req: object,
    *,
    backoffs: tuple[float, ...],
    sleep: Callable[[float], Awaitable[None]],
    log: EventLog | None = None,
    source: str = "",
    item_id: str | None = None,
) -> AgentResult[T]:
    """Retry ladder. The returned result's `usage` covers EVERY attempt
    — failed tries cost real money and must reach the spend accounting
    — and its `attempts` says how many there were; a copy keeps
    scripted/shared results unmutated.

    `attempts` is reported rather than left to callers to infer, because the
    ladder's two terminal cases (a non-`failed_infra` status, and
    `parse_failure`) both return without retrying: a caller re-deriving "was
    this retried?" from `status` gets the second one wrong, and setup's fatal
    message did exactly that. The ladder owns its
    own arithmetic.

    With `log` set, each retry emits a `dispatch_retry` event before its
    backoff sleep (a ladder can sit silent
    for 30s–5min per rung — the audit trail and the UI must see it)."""
    total = Usage()
    attempts = 0

    async def attempt_once() -> AgentResult[T]:
        # The circuit breaker carries the spend of the session that hit the
        # wall; add every earlier rung's, so the exit books the whole ladder.
        nonlocal total, attempts
        attempts += 1
        try:
            result = await fn(req)
        except ProviderQuotaError as exc:
            exc.usage = _sum_usage(total, exc.usage or Usage())
            raise
        total = _sum_usage(total, result.usage)
        return result

    result = await attempt_once()
    for attempt, backoff in enumerate(backoffs, start=1):
        if result.status != "failed_infra":
            break
        if result.error and result.error.error_class == "parse_failure":
            break  # terminal — the in-session repair already ran
        if log is not None:
            log.event(
                "dispatch_retry",
                source=source,
                item_id=item_id,
                attempt=attempt,
                backoff_secs=backoff,
                error_class=result.error.error_class if result.error else "?",
            )
        await sleep(backoff)
        result = await attempt_once()
    # Always stamped, so `attempts` is meaningful on every laddered result —
    # not just the retried ones (a dispatcher's own result defaults to 1).
    return result.model_copy(update={"usage": total, "attempts": attempts})


def _error_of(result: AgentResult) -> ErrorInfo:
    """The infra error for a data-less result. Any `data is None` is treated as
    an infra outcome; a `failed_expected` envelope with null data carries no
    `error`, so the envelope's own prose becomes the detail."""
    if result.error is not None:
        return result.error
    return ErrorInfo(
        error_class="provider_error",
        detail=result.detail or "agent returned no data",
    )


def _outcome(
    item: BatchItem,
    status: str,
    *,
    rc_id: str | None = None,
    experiment_memory_id: str | None = None,
    result=None,
    infra_error: ErrorInfo | None = None,
    probe: ProbeResult | None = None,
    speed: LatencyComparison | None = None,
    speed_verdict: str | None = None,
) -> ItemOutcome:
    return ItemOutcome(
        item=item,
        status=status,  # type: ignore[arg-type]
        rc_id=rc_id,
        experiment_memory_id=experiment_memory_id,
        result=result,
        infra_error=infra_error,
        probe=probe,
        speed=speed,
        speed_verdict=speed_verdict,
    )


async def _experiment_write(
    ctx: Ctx, item_id: str, write: str, pending: Awaitable[None]
) -> None:
    """One lifecycle write on the item's own experiment record.
    A REFUSED write (`MemoryWriteRefused`, e.g. `ExperimentStateError`: the
    record's state does not allow the move) is logged as
    `memory_write_failed`, and the item keeps its outcome. The refusal is
    deterministic and concerns one record. Before, it left the item, the
    wave cancelled every sibling (`fatal_cancel`) and the run ended. A
    transport error after its retries is still raised."""
    try:
        await pending
    except MemoryWriteRefused as exc:
        ctx.log.event(
            "memory_write_failed",
            write=write,
            what="the experiment record's state",
            item_id=item_id,
            error=repr(exc),
        )


async def _infra_outcome(
    ctx: Ctx,
    item: BatchItem,
    failed: AgentResult,
    exp_mem_id: str | None = None,
    rc_id: str | None = None,
) -> ItemOutcome:
    """Terminal failed_infra: code stamps the open experiment memory
    `— FAILED (infra: <class>)` at this moment — the startup janitor
    is for crash debris only, never a live run's cleanup."""
    error = _error_of(failed)
    if exp_mem_id is not None:
        await _experiment_write(
            ctx, item.id, "mark_failed", ctx.memory.mark_failed(exp_mem_id, error)
        )
    return _outcome(
        item,
        "failed_infra",
        rc_id=rc_id,
        experiment_memory_id=exp_mem_id,
        infra_error=error,
    )


def eval_build_issue(item: BatchItem) -> str | None:
    """The issue an item would dispatch the eval-build agent for — an
    `eval_only` item, or a fix with `needs_eval: true` — else None."""
    if isinstance(item, EvalOnlyItem):
        return item.issue_memory_id
    if (
        isinstance(item, ExperimentItem)
        and isinstance(item.work, FixWork)
        and item.work.needs_eval
    ):
        return item.work.issue_memory_id
    return None


def drop_eval_building_items(ctx: Ctx, items: list, wave: int) -> list:
    """The eval-building guard: eval building is experimental and off
    unless `--experimental-eval-building` turns it on. While it is off,
    every item that would reach `_run_eval_build` is dropped here, before
    any dispatch, with one `eval_item_dropped` event (and log line) each;
    the rest of the wave runs as planned. The planner is told the setting,
    so a dropped item is a planner error, not a normal outcome."""
    if ctx.eval_building:
        return list(items)
    kept = []
    for item in items:
        issue_id = eval_build_issue(item)
        if issue_id is None:
            kept.append(item)
            continue
        line = (
            f"dropped {item.id}: it builds a new eval for issue {issue_id},"
            " and eval building is off (experimental:"
            " --experimental-eval-building turns it on)"
        )
        ctx.log.event(
            "eval_item_dropped",
            wave=wave,
            item_id=item.id,
            item_kind=item_kind(item),
            issue_memory_id=issue_id,
            line=line,
        )
        logger.warning("%s", line)
        ctx.dropped_eval_lines.append(line)
    return kept


async def _run_eval_build(
    ctx: Ctx, item_id: str, issue_memory_id: str, baseline: BaselineRef
) -> AgentResult:
    if not ctx.eval_building:
        # The boundary refuses too, whatever path brought the item here: the
        # wave runner drops these items, but `execute_plan_item` is public.
        detail = (
            f"eval building is off: {item_id} was not dispatched"
            " (--experimental-eval-building turns it on)"
        )
        logger.error("%s", detail)
        return AgentResult[EvalBuildReport](
            status="failed_infra",
            error=ErrorInfo(error_class="provider_error", detail=detail),
        )
    # The eval-build boundary — issue id + baseline ONLY
    # (hypothesis/technique never cross; the eval measures the issue, not the
    # planned fix).
    _set_state(ctx, item_id, "building_eval")
    rep = await call_with_retries(
        ctx.dispatch.eval_build,
        EvalBuildInput(issue_memory_id=issue_memory_id, baseline=baseline),
        backoffs=ITEM_DISPATCH_BACKOFFS,
        sleep=ctx.sleep,
        log=ctx.log,
        source="eval_build",
        item_id=item_id,
    )
    ctx.log.event(
        "spend",
        item_id=item_id,
        source="eval_build",
        lane=SUBSCRIPTION_LANE,
        cost_usd=rep.usage.cost_usd,
        tokens=rep.usage.tokens,
        unmeasured_tokens=rep.usage.unmeasured_tokens,
    )
    return rep


async def execute_plan_item(
    ctx: Ctx,
    item: BatchItem,
    baseline: BaselineRef,
    baseline_lineage: list[str],
    wave: int,
) -> ItemOutcome:
    """Instrumentation wrapper: every path lands exactly one
    `item_outcome` event (the UI's "replace running status with finding")."""
    outcome = await _execute_plan_item(ctx, item, baseline, baseline_lineage, wave)
    # What the reference check found on the run config this
    # item ended with (`kiln_harness_tuner.reference_check`), for the report.
    outcome.reference_findings = ctx.reference_findings.pop(item.id, [])
    ctx.log.event("item_outcome", item_id=item.id, outcome=outcome)
    return outcome


def _measurement_error(exc: MeasurementFailed | AnalysisError) -> ErrorInfo:
    """`MeasurementFailed` already carries a classified `ErrorInfo`; the
    analysis-error family is a data-shape condition, and `ErrorClass` has no
    literal for that — `provider_error` is the same nearest fit
    `compare_rows` uses for its holes."""
    if isinstance(exc, MeasurementFailed):
        return exc.error
    return ErrorInfo(error_class="provider_error", detail=str(exc))


async def _measure_issue_baseline(
    ctx: Ctx, item: MeasureIssueBaselineItem, baseline: BaselineRef
) -> ItemOutcome:
    """Settle an `UNMEASURED:` issue against the current baseline
    — **pure code, no agent dispatch**: run the issue's EXISTING eval on
    the train split, apply the any-headroom rule, PATCH the issue.

    Cheap by construction: this reuses `ensure_measured`, and an (eval,
    config, rc, train) pair the loop already measured re-scores nothing
    server-side (measured once, read forever) — so the routine case is
    one eval-job round trip and one memory write, with no builder, no
    analyzer and no new run config.

    Failure policy: an eval-shape or measurement
    error fails THIS ITEM `failed_infra` and leaves the issue record
    untouched — its state stays `UNMEASURED:` and a later wave can retry for
    free. `AnalysisError` covers the shape family (an all-informational eval
    raising `TargetExpansionError` is the named case, a missing default
    eval config, an unmappable score type, and — narrowly, unlike the
    experiment lane — a missing direction flag: this item writes one line and
    is re-runnable, so failing it beats killing a wave, and `failed_infra`
    carries the error's own detail, never silence). A `KilnAPIError` is NOT
    caught: a 422 from a missing train/val split is the no-degradation
    rule and stays fatal, exactly as in the experiment lane.

    **An incomplete measurement is one of those eval errors.** The experiment
    lane may ship a partial side because a partial side is still *evidence*
    the analyzer weighs beside its coverage fields; this lane writes TERMINAL
    issue state from it, so a job that ended with errored items is refused
    outright. Otherwise a split where 21 of 24 items failed would settle the
    issue `WONT_FIX:` off the 3 that happened to pass, and the summary line
    (`n=3`) would read like a small eval rather than a broken run."""
    _set_state(ctx, item.id, "measuring")
    try:
        eval_def = await ctx.analyzer.fetch_eval(item.eval_id)
        eval_config_id = ctx.analyzer.default_eval_config_id(eval_def)
        # `model_construct`: the item is already schema-validated, and a
        # plain `EvalTarget(...)` would raise `pydantic.ValidationError` for
        # an unvalidated `scores=[]` — neither AnalysisError nor
        # MeasurementFailed, so it would escape this `try` and kill the run
        # through the wave gather. Constructing without validation lets
        # `expand_targets`' documented guard be the one that fires.
        specs = expand_targets(
            eval_def,
            EvalTarget.model_construct(eval_id=item.eval_id, scores=item.scores),
        )
        runs, health = await ctx.analyzer.ensure_measured(
            item.eval_id, eval_config_id, baseline.rc_id, "train"
        )
    except (MeasurementFailed, AnalysisError) as exc:
        return _outcome(item, "failed_infra", infra_error=_measurement_error(exc))
    # Logged the moment the measurement lands, before any verdict can fail:
    # a job that ran cost real money whatever happens next (items.py's rule).
    ctx.log.event(
        "spend",
        item_id=item.id,
        source="eval_jobs",
        lane=PROVIDER_LANE,
        cost_usd=ctx.analyzer.fresh_runs_cost(runs),
        evidence_cost_usd=runs_cost(runs),
    )
    if not health.clean:
        return _outcome(
            item,
            "failed_infra",
            infra_error=partial_measurement_error(
                item.eval_id, baseline.rc_id, "train", health
            ),
        )
    try:
        # `n_expected` is the job's own view of the split size — the last
        # denominator that would otherwise be taken on faith.
        verdict = reproduction_verdict(runs, specs, n_expected=health.n_expected)
    except (MeasurementFailed, AnalysisError) as exc:
        return _outcome(item, "failed_infra", infra_error=_measurement_error(exc))
    try:
        await ctx.memory.record_baseline_measurement(
            item.issue_memory_id,
            reproduced=verdict.reproduced,
            rc_id=baseline.rc_id,
            summary=verdict.summary,
            # The writer's own preconditions: refuses when the issue is bound
            # to a DIFFERENT eval, and when it is already closed. Both ids
            # arrive from the planner independently.
            expected_eval_id=item.eval_id,
        )
    except (IssueWriteRefused, MemoryRecordNotFoundError, RecordKindError) as exc:
        # Planner-supplied-id faults, all of which leave every record
        # untouched — the item fails, the wave lives. `MemoryRecordNotFound`
        # is `_get`'s not-found specifically, NOT any `KeyError` from deeper
        # in the write: a real bug in `_patch`/`_append_content` must not be
        # laundered into a "bad id" report.
        return _outcome(
            item,
            "failed_infra",
            infra_error=ErrorInfo(error_class="provider_error", detail=str(exc)),
        )
    # The completed row cannot carry the verdict (result=None
    # and BOTH verdicts end `completed`), so the wave log gets it here — the
    # audit trail answers "did it reproduce?" without opening memory. Emitted
    # AFTER the write, so it only ever describes state that actually landed.
    ctx.log.event(
        "item_detail",
        item_id=item.id,
        detail=f"{'reproduced' if verdict.reproduced else 'not reproduced'}"
        f" on baseline {baseline.rc_id} ({verdict.summary})",
    )
    # As for a successful eval_only item: result=None, so
    # it is never a promotion candidate and never enters maybe_promote.
    return _outcome(item, "completed")


async def _remeasure(ctx: Ctx, item: RemeasureItem) -> ItemOutcome:
    """Complete an EXISTING rc's measurement on `item.eval_ids`
    — **pure code, no agent dispatch**. Kiln's
    item-grained cache means each `ensure_measured` re-drives only the items
    that never stored a row, so a rate-limit-wiped measurement gets finished
    without a duplicate rc build. Coverage lands in the wave log per eval
    (`item_detail`); a fill that still stores nothing fails the item
    `failed_infra` and a later wave can retry. A 4xx (e.g. the 422 missing-
    split answer) propagates unchanged — hard, never a hole."""
    _set_state(ctx, item.id, "measuring")
    for eval_id in item.eval_ids:
        try:
            eval_def = await ctx.analyzer.fetch_eval(eval_id)
            eval_config_id = ctx.analyzer.default_eval_config_id(eval_def)
            runs, health = await ctx.analyzer.ensure_measured(
                eval_id, eval_config_id, item.rc_id, item.split
            )
        except (MeasurementFailed, AnalysisError) as exc:
            return _outcome(
                item,
                "failed_infra",
                rc_id=item.rc_id,
                infra_error=_measurement_error(exc),
            )
        ctx.log.event(
            "spend",
            item_id=item.id,
            source="eval_jobs",
            lane=PROVIDER_LANE,
            cost_usd=ctx.analyzer.fresh_runs_cost(runs),
            evidence_cost_usd=runs_cost(runs),
        )
        stored = pairable_item_count(runs)
        total = max(health.n_expected or 0, stored)
        detail = f"{eval_id}: {stored}/{total} stored"
        if not health.clean:
            detail += f", {len(health.missing_item_ids)} still dark"
        ctx.log.event("item_detail", item_id=item.id, detail=detail)
    # Result=None, like the other pure-code items: never a promotion
    # candidate, never enters maybe_promote.
    return _outcome(item, "completed", rc_id=item.rc_id)


async def _probe(ctx: Ctx, item: ProbeItem, baseline: BaselineRef) -> ItemOutcome:
    """A viability probe: one `claude -p` session answers one
    yes/no question about a mechanism by forking an ephemeral run config and
    reading a few train traces. No judge, no measurement, no experiment
    record. CODE writes the answer to memory as a `probe`-tagged lesson so
    the next planner finds it, and the wave log carries it for the UIs.

    An agent that could not run the probe (`failed_expected`) still ends the
    item `completed` with answer `unclear` and the agent's reason as the
    evidence — an unanswered probe is information the planner needs, not an
    infra retry. Only a dispatcher failure is `failed_infra`."""
    _set_state(ctx, item.id, "probing")
    await snapshot_memory(ctx, f"{item.id}-probe-before")
    if item.check is not None:
        # A CODE probe: no agent — fork, drive,
        # count (`kiln_harness_tuner.probes`). Provider-lane spend: the drives are Kiln
        # runs.
        result, cost = await run_code_probe(ctx, item, baseline)
        ctx.log.event(
            "spend",
            item_id=item.id,
            source="probe",
            lane=PROVIDER_LANE,
            cost_usd=cost,
            evidence_cost_usd=cost,
        )
    else:
        rep = await call_with_retries(
            ctx.dispatch.probe,
            ProbeInput(
                item=item, baseline=baseline, train_inputs=probe_train_inputs(ctx)
            ),
            backoffs=ITEM_DISPATCH_BACKOFFS,
            sleep=ctx.sleep,
            log=ctx.log,
            source="probe",
            item_id=item.id,
        )
        ctx.log.event(
            "spend",
            item_id=item.id,
            source="probe",
            lane=SUBSCRIPTION_LANE,
            cost_usd=rep.usage.cost_usd,
            tokens=rep.usage.tokens,
            unmeasured_tokens=rep.usage.unmeasured_tokens,
        )
        if rep.data is None:
            if rep.status != "failed_expected":
                return _outcome(item, "failed_infra", infra_error=_error_of(rep))
            result = ProbeResult(
                answer="unclear",
                evidence=rep.detail or "the probe agent could not run the probe",
            )
        else:
            # A probe answers a mechanism question only: its
            # fork is never timed — speed is measured by the engine, on a
            # screening candidate or an arm, never read off a probe.
            result = rep.data
    await snapshot_memory(ctx, f"{item.id}-probe-after")
    ctx.log.event("item_probe", item_id=item.id, probe=result.model_dump())
    ctx.probe_answers.append(
        ProbeAnswer(
            item_id=item.id,
            question=item.question,
            answer=result.answer,
            evidence=result.evidence[:600],
            for_idea=item.for_idea,
        )
    )
    ctx.log.event(
        "item_detail",
        item_id=item.id,
        detail=f"probe → {result.answer.upper()}: {result.evidence[:160]}",
    )
    try:
        await ctx.memory.record_probe(item, result)
    except Exception as exc:  # the answer is in the wave log; memory is best-effort
        ctx.log.event(
            "memory_write_failed",
            write="probe",
            what="the probe's answer as a lesson",
            item_id=item.id,
            error=repr(exc),
        )
    return _outcome(item, "completed", probe=result)


# How many train items a probe agent is handed to choose its drives from.
PROBE_TRAIN_INPUTS = 12


def probe_train_inputs(ctx: Ctx) -> list[ProbeTrainInput]:
    """Train items for the probe agent, read from disk by CODE:
    the agents read no project file, and no item outside train reaches them.
    A fixed sample across the active evals' train splits."""
    try:
        inputs = split_inputs(ctx.target.project_path, ctx.target.task_id, "train")
    except Exception as exc:  # the probe then says it had nothing to drive
        ctx.log.event("probe_inputs_unavailable", error=repr(exc))
        return []
    return [
        ProbeTrainInput(
            id=i.id, structured_input=i.structured, plaintext_input=i.plaintext
        )
        for i in sample_inputs(inputs, PROBE_TRAIN_INPUTS)
    ]


# Run-config properties whose change makes an arm a MODEL-LEVEL swap: its
# lever is checked on timing drives, and the residual block does not gate it
# (a swap moves every item a little, not the parent's failures).
SWAP_KEYS = (
    "model_name",
    "model_provider_name",
    "thinking_level",
    "temperature",
    "top_p",
    "structured_output_mode",
)


async def _is_model_swap(ctx: Ctx, parent_rc_id: str, rc_id: str) -> bool:
    """Does the arm change the model, the provider or a sampling parameter
    against its parent? A config that cannot be read cannot be compared: it
    is logged and treated as no swap (the full measurement still runs)."""
    try:
        parent = await rc_props(ctx, parent_rc_id)
        child = await rc_props(ctx, rc_id)
    except Exception as exc:
        ctx.log.event("swap_check_unavailable", rc_id=rc_id, error=repr(exc)[:200])
        return False
    return any(parent.get(k) != child.get(k) for k in SWAP_KEYS)


def _set_state(ctx: Ctx, item_id: str, state: str) -> None:
    """One `item_state` event plus the in-memory copy the wave runner reads
    at the deadline (an ANALYZING item is granted a grace, the rest are
    cancelled — orchestrator `_run_wave`)."""
    ctx.item_states[item_id] = state
    ctx.log.event("item_state", item_id=item_id, state=state)


async def _link_eval(
    ctx: Ctx, item: BatchItem, issue_id: str, rep: EvalBuildReport
) -> ItemOutcome | None:
    """The eval link after a reproduced eval build. The issue id is the
    planner's, so the same planner-supplied-id faults as
    `_measure_issue_baseline` (not found, wrong kind, terminal state) end
    the ITEM as `failed_infra` — never the wave. None when the link landed."""
    try:
        await ctx.memory.link_eval(issue_id, rep)
    except (IssueWriteRefused, MemoryRecordNotFoundError, RecordKindError) as exc:
        return _outcome(
            item,
            "failed_infra",
            infra_error=ErrorInfo(error_class="provider_error", detail=str(exc)),
        )
    return None


async def _mark_not_reproduced(
    ctx: Ctx, item: BatchItem, issue_id: str, rep: AgentResult
) -> ItemOutcome:
    """The eval-build agent could not reproduce the issue on the baseline:
    record it and stop. A CLOSED issue is refused instead — the same
    `ISSUE_TERMINAL_STATES` doctrine `measure_issue_baseline` enforces, at
    the loop's other `WONT_FIX:` writer. Enforcing it at one of two writers
    and not the other is worse than either extreme: the planner supplies the
    issue id at both, and `mark_wont_fix` would relabel a landed `FIXED:` as
    a decision and lose the citing rc.

    Both call sites sit BEFORE `open_experiment`, so the refusal is a plain
    `failed_infra` with no experiment memory to stamp and nothing written."""
    # Both call sites reach here only through `if not rep.data.reproduced` —
    # data exists by construction; stated for the type checker.
    assert rep.data is not None
    try:
        await ctx.memory.mark_wont_fix(
            issue_id, rep.data.not_reproduced_reason or "not reproduced at eval build"
        )
    except (IssueWriteRefused, MemoryRecordNotFoundError, RecordKindError) as exc:
        return _outcome(
            item,
            "failed_infra",
            infra_error=ErrorInfo(error_class="provider_error", detail=str(exc)),
        )
    return _outcome(item, "skipped_not_reproduced")


async def _execute_plan_item(
    ctx: Ctx,
    item: BatchItem,
    baseline: BaselineRef,
    baseline_lineage: list[str],
    wave: int,
) -> ItemOutcome:
    if isinstance(item, MeasureIssueBaselineItem):
        return await _measure_issue_baseline(ctx, item, baseline)

    if isinstance(item, RemeasureItem):
        return await _remeasure(ctx, item)

    if isinstance(item, ProbeItem):
        return await _probe(ctx, item, baseline)

    if isinstance(item, EvalOnlyItem):
        rep = await _run_eval_build(ctx, item.id, item.issue_memory_id, baseline)
        if rep.data is None:
            return _outcome(item, "failed_infra", infra_error=_error_of(rep))
        if not rep.data.reproduced:
            return await _mark_not_reproduced(ctx, item, item.issue_memory_id, rep)
        if (
            refused := await _link_eval(ctx, item, item.issue_memory_id, rep.data)
        ) is not None:
            return refused
        # A successful eval_only item ends "completed" with result=None.
        return _outcome(item, "completed")

    if isinstance(item.work, FixWork) and item.work.needs_eval:
        rep = await _run_eval_build(ctx, item.id, item.work.issue_memory_id, baseline)
        if rep.data is None:
            return _outcome(item, "failed_infra", infra_error=_error_of(rep))
        if not rep.data.reproduced:
            return await _mark_not_reproduced(ctx, item, item.work.issue_memory_id, rep)
        item.work.eval_id = rep.data.eval_id
        if (
            refused := await _link_eval(ctx, item, item.work.issue_memory_id, rep.data)
        ) is not None:
            return refused
        # An item that commissions its own eval must actually MEASURE it. A
        # planner might not list the new eval in evaluation_target, and the item
        # would produce no primary number because the eval did not exist at plan
        # time. So the commissioned eval always joins the target list.
        if not any(t.eval_id == rep.data.eval_id for t in item.evaluation_target):
            item.evaluation_target.append(EvalTarget(eval_id=rep.data.eval_id))

    exp_mem = await ctx.memory.open_experiment(item, wave, baseline)

    _set_state(ctx, item.id, "building_rc")
    reused = getattr(item.work, "existing_rc_id", None)
    if reused:
        # The run config already exists (a prior run built and
        # measured it and lost only the analysis to its deadline) — no build
        # session, no build spend; the item measures and analyzes it.
        rc = AgentResult[RcBuildResult](
            status="ok",
            data=RcBuildResult(
                rc_id=reused, build_notes=f"reused existing run config {reused}"
            ),
            usage=Usage(),
        )
        ctx.log.event("item_detail", item_id=item.id, detail=f"reusing rc {reused}")
    else:
        rc = await _build_rc(ctx, item, RcBuildInput(item=item, baseline=baseline))
    if rc.data is None:
        if rc.status == "failed_expected":
            # The builder PROVED the lever is a product dead-end (for example,
            # an inert provider knob) — filing it failed_infra
            # ("retryable" per the skill taxonomy) invites a later wave to
            # retry a disproven lever. It gets its own terminal status and
            # its own memory stamp.
            reason = rc.detail or "builder reported a dead end"
            await _experiment_write(
                ctx, item.id, "mark_dead_end", ctx.memory.mark_dead_end(exp_mem, reason)
            )
            ctx.log.event("item_detail", item_id=item.id, detail=f"dead end: {reason}")
            return _outcome(item, "dead_end", experiment_memory_id=exp_mem)
        return await _infra_outcome(ctx, item, rc, exp_mem)
    await _record_build(ctx, item, exp_mem, rc.data)

    parent_rc_id = baseline.rc_id
    if isinstance(item.work, FixWork) and item.work.parent_rc_id:
        parent_rc_id = item.work.parent_rc_id
    # What the build added is checked for train-set memorisation
    # BEFORE any train budget is spent on it (`kiln_harness_tuner.generality`), and
    # its whole text for eval text (`kiln_harness_tuner.judge_copy`),
    # and for names it does not attach
    # (`kiln_harness_tuner.reference_check`).
    reviewed = await _review_build(
        ctx, item, baseline, exp_mem, parent_rc_id, rc.data, reused=bool(reused)
    )
    if isinstance(reviewed, ItemOutcome):
        return reviewed
    rc_id = reviewed.rc_id

    _set_state(ctx, item.id, "measuring")
    # One measurement path (`kiln_harness_tuner.measure`): the arm is
    # judged chunk by chunk on a queue drawn from its parent's stored train
    # results — the parent's failures and a few passes first, then the
    # rest — with the objective's stop rules after every chunk. A measurement
    # that completes IS the train measurement: the analysis below reads its stored rows.
    efficiency = is_efficiency(ctx.objective)
    measurement = await _measure_arm(ctx, item, rc_id, parent_rc_id, baseline)
    stopped = await _stopped_arm(ctx, item, rc_id, exp_mem, measurement)
    if stopped is not None:
        return stopped
    try:
        bundle = await ctx.analyzer.run_experiment_analysis(
            item,
            rc_id,
            baseline,
            baseline_lineage,
            progress=lambda detail: ctx.log.event(
                "item_detail", item_id=item.id, detail=detail
            ),
        )
    except AnalysisError as exc:
        # A data-shape condition on this item's own targets (an eval with no
        # comparable score, a missing default config) is this ITEM's
        # failure, not the run's: the gate skips such evals, and the train
        # side must not kill the wave for one.
        ctx.log.event("item_detail", item_id=item.id, detail=f"analysis failed: {exc}")
        error = _measurement_error(exc)
        await _experiment_write(
            ctx, item.id, "mark_failed", ctx.memory.mark_failed(exp_mem, error)
        )
        return _outcome(
            item,
            "failed_infra",
            rc_id=rc_id,
            experiment_memory_id=exp_mem,
            infra_error=error,
        )
    ctx.log.event(
        "spend",
        item_id=item.id,
        source="eval_jobs",
        lane=PROVIDER_LANE,
        cost_usd=bundle.spend_total_usd,
        evidence_cost_usd=bundle.evidence_spend_usd,
    )
    if measurement.latency is not None or measurement.cost is not None:
        bundle.latency = measurement.latency  # the engine's paired timing
        bundle.cost = measurement.cost
    ctx.log.event(
        "arm_results",
        item_id=item.id,
        rc_id=rc_id,
        parent_rc_id=parent_rc_id,
        per_eval=[row.model_dump(mode="json") for row in bundle.per_eval],
        latency=dump_or_none(bundle.latency),
        cost=dump_or_none(bundle.cost),
        errors=[error.model_dump(mode="json") for error in bundle.errors],
    )

    if not bundle.per_eval and bundle.errors:
        # Total measurement hole: every targeted pair came back with zero rows —
        # there is nothing to analyze, and a paid analysis agent on an empty
        # table buys only "no verdict possible". The item
        # fails on the first hole's own error; partial bundles (some rows +
        # some holes) still ship to the analyzer under the holes doctrine.
        failed = AgentResult(status="failed_infra", data=None, error=bundle.errors[0])
        return await _infra_outcome(ctx, item, failed, exp_mem, rc_id=rc_id)

    _set_state(ctx, item.id, "analyzing")
    await snapshot_memory(ctx, f"{item.id}-analysis-before")
    res = await call_with_retries(
        ctx.dispatch.analyze_experiment_results,
        AnalysisInput(
            item=item,
            rc_id=rc_id,
            bundle=bundle,
            experiment_memory_id=exp_mem,
            speed_rule=speed_rule_text(ctx.objective) if efficiency else None,
            reference_findings=list(ctx.reference_findings.get(item.id, [])),
        ),
        backoffs=ITEM_DISPATCH_BACKOFFS,
        sleep=ctx.sleep,
        log=ctx.log,
        source="analyze_experiment_results",
        item_id=item.id,
    )
    await snapshot_memory(ctx, f"{item.id}-analysis-after")
    ctx.log.event(
        "spend",
        item_id=item.id,
        source="analyze_experiment_results",
        lane=SUBSCRIPTION_LANE,
        cost_usd=res.usage.cost_usd,
        tokens=res.usage.tokens,
        unmeasured_tokens=res.usage.unmeasured_tokens,
    )
    if res.data is None:
        return await _infra_outcome(ctx, item, res, exp_mem, rc_id=rc_id)

    # `successful` is the ticket into the val gate, and the code
    # rule is its floor — the analyzer keeps its interpretation and may veto,
    # never promote past the numbers (`successful` = analyzer AND rule).
    from kiln_harness_tuner.orchestrator import (
        eval_weights,
    )  # the orchestrator imports us

    verdict = (
        success_by_speed_rule(
            bundle, ctx.coverage_floor, ctx.objective, await eval_weights(ctx)
        )
        if efficiency
        else success_by_rule(item, bundle, ctx.coverage_floor)
    )
    ctx.log.event(
        "success_rule",
        item_id=item.id,
        analyzer_successful=res.data.successful,
        rule_ok=verdict.ok,
        reasons=verdict.reasons,
    )
    if res.data.successful and not verdict.ok:
        logger.warning(
            "item %s: analyzer said successful, code rule says no — %s",
            item.id,
            "; ".join(verdict.reasons),
        )
        res.data.successful = False
        res.data.findings = (
            res.data.findings.rstrip()
            + "\n\n[code rule] not successful: "
            + "; ".join(verdict.reasons)
        )
    elif (
        not res.data.successful
        and verdict.ok
        and res.data.veto_reason == "prediction_miss"
        and not efficiency
    ):
        # An analyzer can veto a real +3.3pp win because the item had predicted
        # +15pp on a baseline with 11.7pp of headroom. A prediction sets the
        # caution, never the verdict: when
        # the code floor passes and the only ground for the veto is the
        # missed magnitude, the arm stays successful and goes to the val
        # gate, which is the filter with more data. Any other veto reason
        # (dominated by its own ingredient, holes, a secondary regression)
        # still stands.
        logger.warning(
            "item %s: analyzer vetoed on a prediction miss; code rule passed —"
            " restoring successful (the miss stays in findings as caution)",
            item.id,
        )
        res.data.successful = True
        res.data.findings = (
            res.data.findings.rstrip()
            + "\n\n[code rule] successful: the primary target improved and the"
            " code floor passed; the analyzer's veto was a prediction miss,"
            " which is caution, not a verdict. Read the magnitude against"
            " the row's headroom, not the item's prediction."
        )
        ctx.log.event("veto_overridden", item_id=item.id, veto_reason="prediction_miss")

    # The code-composed lines on the record the planner reads.
    lines = [f"measurement: {measurement.line}"]
    if bundle.latency is not None or bundle.cost is not None:
        lines += [
            latency_line("latency", bundle.latency),
            latency_line("cost", bundle.cost),
        ]
    else:
        lines += passive_lines(bundle)
    res.data.memory_content = res.data.memory_content.rstrip() + "\n" + "\n".join(lines)
    await _experiment_write(
        ctx, item.id, "record_verdict", ctx.memory.record_verdict(exp_mem, res.data)
    )
    done = _outcome(
        item,
        "completed",
        rc_id=rc_id,
        experiment_memory_id=exp_mem,
        result=res.data,
    )
    done.measurement = measurement_summary(measurement)
    return done


async def _build_rc(
    ctx: Ctx, item: ExperimentItem, req: RcBuildInput, phase: str = "build"
) -> AgentResult[RcBuildResult]:
    """One `create_experiment_rc` dispatch on the item ladder, with its spend
    event, its memory snapshots and its build sample. `phase` is "rebuild"
    for the one rebuild after a generality leak."""
    build_started = ctx.clock()
    await snapshot_memory(ctx, f"{item.id}-{phase}-before")
    rc = await call_with_retries(
        ctx.dispatch.create_experiment_rc,
        req,
        backoffs=ITEM_DISPATCH_BACKOFFS,
        sleep=ctx.sleep,
        log=ctx.log,
        source="create_experiment_rc",
        item_id=item.id,
    )
    ctx.log.event(
        "spend",
        item_id=item.id,
        source="create_experiment_rc",
        lane=SUBSCRIPTION_LANE,
        cost_usd=rc.usage.cost_usd,
        tokens=rc.usage.tokens,
        unmeasured_tokens=rc.usage.unmeasured_tokens,
    )
    # One (usd, secs) sample per build, by technique — the planner's
    # `lever_costs`. A merge is its own class.
    await snapshot_memory(ctx, f"{item.id}-{phase}-after")
    technique = getattr(item.work, "technique", None) or "merge"
    ctx.build_samples.setdefault(technique, []).append(
        (rc.usage.cost_usd, ctx.clock() - build_started)
    )
    return rc


async def _record_build(
    ctx: Ctx,
    item: ExperimentItem,
    exp_mem: str,
    built: RcBuildResult,
    *,
    rebuild: bool = False,
) -> None:
    """The rc id goes on the experiment record NOW, not at the verdict: a
    record abandoned between build and analysis must name what it built,
    or the next run cannot reuse it.
    A `rebuild` replaces the refused build's lines instead of adding to them."""
    write = ctx.memory.record_rc_rebuilt if rebuild else ctx.memory.record_rc_built
    try:
        await write(exp_mem, built.rc_id, built.change)
    except Exception as exc:  # bookkeeping for a FUTURE run — never fatal now
        ctx.log.event(
            "memory_write_failed",
            write="rc_rebuilt" if rebuild else "rc_built",
            what="rc id on the experiment record",
            exp_mem_id=exp_mem,
            rc_id=built.rc_id,
            error=repr(exc),
        )
    if built.change is not None:
        ctx.log.event("item_change", item_id=item.id, change=built.change.model_dump())


async def _note_generality(ctx: Ctx, exp_mem: str, finding: str) -> None:
    try:
        await ctx.memory.record_generality(exp_mem, finding)
    except Exception as exc:  # the finding is in the wave log either way
        ctx.log.event(
            "memory_write_failed",
            write="generality",
            what="the generality check's finding",
            exp_mem_id=exp_mem,
            error=repr(exc),
        )


async def _check(
    ctx: Ctx, item: ExperimentItem, rc_id: str, parent_rc_id: str
) -> GeneralityCheck:
    """One generality check, logged: a `generality` event per check, and a
    detail line on the item when there was something to review. The item
    is REVIEWING only while a review session runs."""
    check = await check_generality(
        ctx,
        item,
        rc_id,
        parent_rc_id,
        before_review=lambda: _set_state(ctx, item.id, "reviewing"),
    )
    ctx.log.event(
        "generality",
        item_id=item.id,
        rc_id=rc_id,
        status=check.status,
        leaks=list(check.leaks),
        notes=check.review_notes,
        line=f"generality {rc_id}: {check.status}"
        + (
            f" — {check.summary}"
            if check.status in ("leaked", "unchecked") or check.note
            else ""
        ),
    )
    if check.status != "unchanged":
        ctx.log.event(
            "item_detail",
            item_id=item.id,
            detail=f"generality: {check.status}"
            + (f" — {check.summary}" if check.status != "clean" or check.note else ""),
        )
    return check


async def _note_judge_copy(ctx: Ctx, exp_mem: str, finding: str) -> None:
    try:
        await ctx.memory.record_judge_copy(exp_mem, finding)
    except Exception as exc:  # the finding is in the wave log either way
        ctx.log.event(
            "memory_write_failed",
            write="judge_copy",
            what="the judge-copy check's finding",
            exp_mem_id=exp_mem,
            error=repr(exc),
        )


async def _check_copies(
    ctx: Ctx, item: ExperimentItem, rc_id: str, baseline_rc_id: str
) -> JudgeCopyCheck:
    """One judge-copy check, logged like the generality check:
    a `judge_copy` event per check, and a `judge copy:` detail line on the
    item when it found a copy or could not read something."""
    check = await check_judge_copies(ctx, rc_id, baseline_rc_id)
    told = check.status != "clean" or bool(check.note)
    ctx.log.event(
        "judge_copy",
        item_id=item.id,
        rc_id=rc_id,
        status=check.status,
        findings=list(check.findings),
        note=check.note,
        unread_evals=list(check.unread_evals),
        line=f"judge copy {rc_id}: {check.status}"
        + (f" — {check.summary}" if told else ""),
    )
    if told:
        ctx.log.event(
            "item_detail",
            item_id=item.id,
            detail=f"judge copy: {check.status} — {check.summary}",
        )
    return check


async def _note_references(ctx: Ctx, exp_mem: str, finding: str) -> None:
    try:
        await ctx.memory.record_reference_check(exp_mem, finding)
    except Exception as exc:  # the finding is in the wave log either way
        ctx.log.event(
            "memory_write_failed",
            write="reference_check",
            what="the reference check's finding",
            exp_mem_id=exp_mem,
            error=repr(exc),
        )


async def _check_references(
    ctx: Ctx, item: ExperimentItem, rc_id: str, parent_rc_id: str
) -> ReferenceCheck | None:
    """One reference check, logged like the judge-copy check:
    a `reference_check` event per check, and a `reference check:` detail
    line on the item when it found something or could not read something.
    None under `--reference-check off`. The findings for the latest checked
    run config are kept for the item's outcome and its analysis."""
    if ctx.reference_check == "off":
        return None
    check = await check_references(ctx, rc_id, parent_rc_id)
    if check.status == "found":
        ctx.reference_findings[item.id] = list(check.findings)
    else:
        ctx.reference_findings.pop(item.id, None)
    told = check.status != "clean" or bool(check.note)
    ctx.log.event(
        "reference_check",
        item_id=item.id,
        rc_id=rc_id,
        mode=ctx.reference_check,
        status=check.status,
        findings=list(check.findings),
        note=check.note,
        line=f"reference check {rc_id}: {check.status}"
        + (f" — {check.summary}" if told else ""),
    )
    if told:
        ctx.log.event(
            "item_detail",
            item_id=item.id,
            detail=f"reference check: {check.status} — {check.summary}",
        )
    return check


def _found(check: ReferenceCheck | None) -> bool:
    return check is not None and check.status == "found"


def _references_after(check: ReferenceCheck) -> str:
    """What a rebuild's reference check says, for the record."""
    if check.status == "unchecked":
        return check.note
    return f"clean ({check.note})" if check.note else "clean"


def _generality_after(check: GeneralityCheck) -> str:
    """What a rebuild's generality check says, for the record."""
    if check.status == "unchecked":
        return check.note
    if check.status == "unchanged":
        return "its instructions now match the parent's"
    return f"clean ({check.note})" if check.note else "clean"


def _copies_after(check: JudgeCopyCheck) -> str:
    """What a rebuild's judge-copy check says, for the record."""
    if check.status == "unchecked":
        return check.note
    return f"clean ({check.note})" if check.note else "clean"


async def _review_build(
    ctx: Ctx,
    item: ExperimentItem,
    baseline: BaselineRef,
    exp_mem: str,
    parent_rc_id: str,
    built: RcBuildResult,
    *,
    reused: bool = False,
) -> RcBuildResult | ItemOutcome:
    """The step between the build and the train measurement.
    Returns the run config to measure — the build, or its one rebuild — or
    the item's outcome when the experiment ends here.

    Two checks, both before any train spend:

    - the generality review: every build that changed the
      instructions — the prompt, the attached skills' or code tools' text —
      is checked for train-set memorisation;
    - the judge-copy check (`kiln_harness_tuner.judge_copy`): the WHOLE
      text of every build — prompt, skills, code tools — is searched for
      text copied from the active evals, with the baseline's text exempt.

    A finding from either gets ONE rebuild with every finding, then both
    checks again. A rebuild that still has a finding ends the item a dead
    end, before any train spend. Merges go through the same path.

    A third check (`kiln_harness_tuner.reference_check`) looks for names
    the build does not attach: a skill, or a tool the parent attached.
    Under `--reference-check warn` (the default) a finding is a `reference
    check:` line and the item goes on; under
    `refuse` it joins the one rebuild like the other two; under `off` the
    check does not run.

    A `reused` run config (`existing_rc_id`) is checked too: a check cut by
    a deadline can leave one unchecked. It is never rebuilt — reuse means
    "measure this exact run config", and a rebuild has none of the stored
    rows it was reused for — so a finding ends the item a dead end."""
    copies = await _check_copies(ctx, item, built.rc_id, baseline.rc_id)
    first = await _check(ctx, item, built.rc_id, parent_rc_id)
    refs = await _check_references(ctx, item, built.rc_id, parent_rc_id)
    refuse = ctx.reference_check == "refuse"
    if first.note and first.status != "leaked":
        await _note_generality(ctx, exp_mem, first.note)
    if copies.note and copies.status != "copied":
        await _note_judge_copy(ctx, exp_mem, copies.note)
    if refs is not None and refs.note and refs.status != "found":
        await _note_references(ctx, exp_mem, refs.note)
    leaked_first = first.status == "leaked"
    copied_first = copies.status == "copied"
    refused_first = refuse and _found(refs)
    # Under `warn` the line waits for the outcome: a rebuild for another
    # check replaces the run config, and the one line must say what the
    # measured one does.
    warned_first = not refuse and _found(refs)
    which = "reused run config" if reused else "first build"
    dangling = ""
    if refs is not None and _found(refs):
        dangling = (
            f"{which} {built.rc_id} names what it does not attach ({refs.summary})"
        )
        if refs.note:
            dangling += f" ({refs.note})"
        if warned_first:
            dangling += "; a warning, not a refusal"
    if not leaked_first and not copied_first and not refused_first:
        if warned_first:
            await _note_references(ctx, exp_mem, dangling)
        return built
    leaked = f"{which} {built.rc_id} leaked train specifics ({first.summary})"
    if first.note:
        leaked += f" ({first.note})"
    copied = f"{which} {built.rc_id} copies eval text ({copies.summary})"
    if copies.note:
        copied += f" ({copies.note})"

    async def note_first(suffix: str) -> None:
        if leaked_first:
            await _note_generality(ctx, exp_mem, f"{leaked}; {suffix}")
        if copied_first:
            await _note_judge_copy(ctx, exp_mem, f"{copied}; {suffix}")
        if refused_first or warned_first:
            await _note_references(ctx, exp_mem, f"{dangling}; {suffix}")

    if reused:
        await note_first("a reused run config is not rebuilt")
        reason = "; ".join(
            r
            for r, hit in (
                (
                    "generality: the reused run config leaks train specifics",
                    leaked_first,
                ),
                ("judge copy: the reused run config copies eval text", copied_first),
                (
                    "reference check: the reused run config names what it does"
                    " not attach",
                    refused_first,
                ),
            )
            if hit
        )
        await _experiment_write(
            ctx, item.id, "mark_dead_end", ctx.memory.mark_dead_end(exp_mem, reason)
        )
        ctx.log.event("item_detail", item_id=item.id, detail=f"dead end: {reason}")
        return _outcome(
            item, "dead_end", rc_id=built.rc_id, experiment_memory_id=exp_mem
        )
    _set_state(ctx, item.id, "building_rc")
    rebuilt = await _build_rc(
        ctx,
        item,
        RcBuildInput(
            item=item,
            baseline=baseline,
            rejected_rc_id=built.rc_id,
            generality_findings=list(first.leaks) if leaked_first else [],
            judge_copy_findings=list(copies.findings) if copied_first else [],
            reference_findings=list(refs.findings)
            if refs is not None and refused_first
            else [],
        ),
        phase="rebuild",
    )
    if rebuilt.data is None:
        await note_first("the rebuild did not land")
        if rebuilt.status == "failed_expected":
            why = rebuilt.detail or "the rebuild reported a dead end"
            check = (
                "generality"
                if leaked_first
                else "judge copy"
                if copied_first
                else "reference check"
            )
            await _experiment_write(
                ctx,
                item.id,
                "mark_dead_end",
                ctx.memory.mark_dead_end(exp_mem, f"{check}: {why}"),
            )
            ctx.log.event(
                "item_detail", item_id=item.id, detail=f"dead end: {check}: {why}"
            )
            return _outcome(
                item, "dead_end", rc_id=built.rc_id, experiment_memory_id=exp_mem
            )
        return await _infra_outcome(ctx, item, rebuilt, exp_mem, rc_id=built.rc_id)
    await _record_build(ctx, item, exp_mem, rebuilt.data, rebuild=True)
    new_rc = rebuilt.data.rc_id
    copies2 = await _check_copies(ctx, item, new_rc, baseline.rc_id)
    second = await _check(ctx, item, new_rc, parent_rc_id)
    refs2 = await _check_references(ctx, item, new_rc, parent_rc_id)
    leaked_again = second.status == "leaked"
    copied_again = copies2.status == "copied"
    refused_again = refuse and _found(refs2)
    # The record: what each check found on the first build, and what it
    # found on the rebuild.
    if leaked_first and leaked_again:
        await _note_generality(
            ctx,
            exp_mem,
            f"{leaked}; the rebuild {new_rc} still leaked ({second.summary})"
            + (f" ({second.note})" if second.note else ""),
        )
    elif leaked_first:
        await _note_generality(
            ctx,
            exp_mem,
            f"{leaked}; rebuilt once as {new_rc} — " + _generality_after(second),
        )
    elif leaked_again:
        await _note_generality(
            ctx,
            exp_mem,
            f"the rebuild {new_rc} leaked train specifics ({second.summary})",
        )
    elif second.note:
        await _note_generality(ctx, exp_mem, second.note)
    if copied_first and copied_again:
        await _note_judge_copy(
            ctx,
            exp_mem,
            f"{copied}; the rebuild {new_rc} still copies eval text"
            f" ({copies2.summary})",
        )
    elif copied_first:
        await _note_judge_copy(
            ctx,
            exp_mem,
            f"{copied}; rebuilt once as {new_rc} — " + _copies_after(copies2),
        )
    elif copied_again:
        await _note_judge_copy(
            ctx,
            exp_mem,
            f"the rebuild {new_rc} copies eval text ({copies2.summary})",
        )
    elif copies2.note:
        await _note_judge_copy(ctx, exp_mem, copies2.note)
    if refs2 is not None:
        dangling2 = f"the rebuild {new_rc} names what it does not attach"
        if (refused_first and refused_again) or (warned_first and _found(refs2)):
            await _note_references(
                ctx,
                exp_mem,
                f"{dangling}; the rebuild {new_rc} still names what it does not"
                f" attach ({refs2.summary})",
            )
        elif refused_first or warned_first:
            # Under `warn` the rebuild was for another check; the line says
            # the measured run config is clean, as its analysis input does.
            await _note_references(
                ctx,
                exp_mem,
                f"{dangling}; rebuilt once as {new_rc} — " + _references_after(refs2),
            )
        elif refused_again:
            await _note_references(ctx, exp_mem, f"{dangling2} ({refs2.summary})")
        elif _found(refs2):
            await _note_references(
                ctx,
                exp_mem,
                f"{dangling2} ({refs2.summary}); a warning, not a refusal",
            )
        elif refs2.note:
            await _note_references(ctx, exp_mem, refs2.note)
    reasons = []
    if leaked_again:
        reasons.append(
            "generality: still leaks train specifics after one rebuild"
            if leaked_first
            else "generality: the rebuild leaks train specifics"
        )
    if copied_again:
        reasons.append(
            "judge copy: still copies eval text after one rebuild"
            if copied_first
            else "judge copy: the rebuild copies eval text"
        )
    if refused_again:
        reasons.append(
            "reference check: still names what it does not attach after one rebuild"
            if refused_first
            else "reference check: the rebuild names what it does not attach"
        )
    if reasons:
        reason = "; ".join(reasons)
        await _experiment_write(
            ctx, item.id, "mark_dead_end", ctx.memory.mark_dead_end(exp_mem, reason)
        )
        ctx.log.event("item_detail", item_id=item.id, detail=f"dead end: {reason}")
        return _outcome(item, "dead_end", rc_id=new_rc, experiment_memory_id=exp_mem)
    return rebuilt.data


def passive_lines(bundle: AnalysisBundle) -> list[str]:
    """An untimed arm's per-request latency and cost against its
    parent, one line each per eval, from the rows' passive `latency` and
    `cost` (the stored eval runs). The rows of one eval share one
    comparison. Empty when no row has one."""
    seen: dict[tuple[str, str], str] = {}
    for row in bundle.per_eval:
        for name, comparison in (("latency", row.latency), ("cost", row.cost)):
            key = (name, row.eval_id)
            if comparison is not None and key not in seen:
                seen[key] = latency_line(f"{name} {row.eval_id}", comparison)
    return list(seen.values())


# ---- An arm through the measurement engine


def measurement_summary(m: Measured) -> MeasurementSummary:
    est = m.estimate
    lo, hi = est.ci() if est is not None else (None, None)
    judged: dict[str, int] = {}
    for row in m.evidence.judged:
        judged[row.eval_id] = judged.get(row.eval_id, 0) + 1
    return MeasurementSummary(
        status=m.status,  # type: ignore[arg-type]
        stop_kind=m.stop_kind,
        stop_line=m.stop_line,
        line=m.line,
        judged=m.evidence.judged_n,
        chunks=m.chunks,
        planned_chunks=m.planned_chunks,
        net=est.net if est is not None else None,
        net_ci_low=lo,
        net_ci_high=hi,
        spend_usd=m.spend_usd,
        budget_usd=m.budget.usd if m.budget is not None else None,
        budget_line=m.budget.line if m.budget is not None else "",
        judged_per_eval=judged,
        planned_per_eval=dict(m.planned_per_eval),
    )


async def _measure_arm(
    ctx: Ctx,
    item: ExperimentItem,
    rc_id: str,
    parent_rc_id: str,
    baseline: BaselineRef,
) -> Measured:
    """The arm through the engine: every item of its targeted evals' train
    split, the objective's rules merged with the planner's for this arm.
    Timing drives run when speed or cost decide, or when the arm is a
    model-level swap (its lever is checked on them). Under quality any other
    arm is not timed: no reference or parent re-drives. Its
    latency and cost stay visible from the stored eval runs (each row's
    passive `latency` and `cost`)."""
    from kiln_harness_tuner.viability import screen_seed

    swap = await _is_model_swap(ctx, parent_rc_id, rc_id)
    named = list(getattr(item.work, "target_items", None) or [])
    residual = (
        isinstance(item.work, FixWork)
        and item.work.residual_gate
        and not swap
        and not named
    )
    rules = merge_rules(
        default_stop_rules(
            ctx.objective,
            full=True,
            residual=residual,
            targets=bool(named),
            noise_rate=noise_flip_rate(
                getattr(ctx, "train_noise", []),
                [t.eval_id for t in item.evaluation_target],
            ),
        ),
        getattr(item.work, "stop_rules", None) or [],
    )
    spec = MeasureSpec(
        targets=list(item.evaluation_target),
        rules=rules,
        seed=f"{screen_seed(ctx)}:{item.id}",
        queue_parent_rc_id=parent_rc_id,
        reference_rc_id=baseline.rc_id,
        objective=ctx.objective,
        full=True,
        named=named,
        timing=is_efficiency(ctx.objective) or swap,
        source="measurement",
        item_id=item.id,
        progress=lambda _rc, line: ctx.log.event(
            "item_detail", item_id=item.id, detail=line
        ),
    )
    (measured,) = await measure(
        ctx,
        [
            Candidate(
                rc_id=rc_id,
                label=item.id,
                parent_rc_id=parent_rc_id,
                max_usd=item.max_usd,
                budget_reason=item.budget_reason,
            )
        ],
        spec,
    )
    ctx.log.event(
        "measurement",
        item_id=item.id,
        rc_id=rc_id,
        status=measured.status,
        stop_kind=measured.stop_kind,
        # The measured line starts with its label, which is usually the id.
        line=(
            measured.line
            if measured.line.startswith(f"{item.id}:")
            else f"{item.id}: {measured.line}"
        ),
    )
    ctx.log.event("arm_estimates", **arm_estimates(measured, item.id, parent_rc_id))
    return measured


async def _stopped_arm(
    ctx: Ctx,
    item: ExperimentItem,
    rc_id: str,
    exp_mem: str,
    m: Measured,
) -> ItemOutcome | None:
    """The outcome of an arm the engine did not complete, or None to go on
    to the analysis. A lever that never reached the call is a DEAD END (the
    lever is disproven for this config); any other stop is NOT LANDED (this
    build missed the bar — another build may not); a measurement that
    stored nothing is infra."""
    if m.status == "completed":
        return None
    summary = measurement_summary(m)
    if m.status == "capped":
        # The spending cap (`kiln_harness_tuner.budget`): inconclusive — never a dead
        # end or a failure. The rows stay stored; a later arm on this rc
        # with a larger cap goes on from them.
        await _experiment_write(
            ctx,
            item.id,
            "mark_capped",
            ctx.memory.mark_capped(exp_mem, m.overview_detail),
        )
        out = _outcome(item, "capped", rc_id=rc_id, experiment_memory_id=exp_mem)
        out.measurement = summary
        return out
    if m.status == "failed":
        error = m.error or ErrorInfo(
            error_class="provider_error", detail="the measurement stored no row"
        )
        await _experiment_write(
            ctx, item.id, "mark_failed", ctx.memory.mark_failed(exp_mem, error)
        )
        return _outcome(
            item,
            "failed_infra",
            rc_id=rc_id,
            experiment_memory_id=exp_mem,
            infra_error=error,
        )
    speed = decisive(m, ctx.objective) or m.latency
    if m.stop_kind == "lever_not_applied":
        await _experiment_write(
            ctx,
            item.id,
            "mark_dead_end",
            ctx.memory.mark_dead_end(exp_mem, f"not viable: {m.stop_line}"[:200]),
        )
        out = _outcome(
            item,
            "dead_end",
            rc_id=rc_id,
            experiment_memory_id=exp_mem,
            speed=speed,
            speed_verdict=m.stop_line,
        )
        out.measurement = summary
        return out
    detail = m.overview_detail
    if m.stop_kind == "residual" and m.residual is not None:
        gate = m.residual
        detail = gate.summary
        try:
            await ctx.memory.record_residual(exp_mem, gate)
        except Exception as exc:  # the lines are in the wave log either way
            ctx.log.event(
                "memory_write_failed",
                write="residual",
                what="the residual block's lines",
                error=repr(exc),
            )
        ctx.residual_misses.append(
            ResidualMiss(
                item_id=item.id,
                rc_id=rc_id,
                summary=gate.summary,
                still_failing=gate.still_failing,
                broken_guards=gate.broken_guards,
            )
        )
    await _experiment_write(
        ctx, item.id, "mark_not_landed", ctx.memory.mark_not_landed(exp_mem, detail)
    )
    out = _outcome(
        item,
        "not_landed",
        rc_id=rc_id,
        experiment_memory_id=exp_mem,
        speed=speed
        if m.stop_kind in ("not_better", "latency_ratio", "cost_ratio")
        else None,
        speed_verdict=m.stop_line,
    )
    out.measurement = summary
    if m.stop_kind == "residual":
        out.residual = m.residual
    return out
