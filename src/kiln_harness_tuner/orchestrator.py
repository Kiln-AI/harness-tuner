"""orchestrate — the wave loop (docs/architecture.md section 3), the two-stage
promotion gate with the only val touch (section 9), the wave tripwire and the
startup janitor (section 10), and the budget/wrap-up judgment (section 3).

Observability rides the `EventLog` seam: the
loop emits events (`run_start`, `preflight`/`baseline_seeded`/`setup_sync`|
`setup_ok`/`janitor`/`baseline_found` pre-flight,
`phase_start`/`phase_end` around the quiet dispatch stretches — planning, the
promotion gate, the tripwire backoff, report writing — `wave_start`,
`item_state`/`item_detail`/`item_outcome`/`spend`/`dispatch_retry` from
items.py, `promotion`, `rejection_recorded`, `memory_write_failed` from
either swallowed-write path (the gate's rejection writeback and
the promotion's `val` stats write — `write` says which),
`tripwire`, `wave_end` once a wave's block is final (the live UI's
scrollback-flush trigger), `exit`) that
wave_log.py records, status.py folds into `RunStatus`, and ui.py renders.
`Ctx.write_report` is report.py's hook — when unset, `orchestrate` returns the
exit reason (test wiring).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import shutil
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from statistics import fmean
from typing import Any, Protocol

from kiln_harness_tuner.analysis import (
    BINARY_SCORE_TYPES,
    RATING_SCORE_TYPES,
    AnalysisError,
    Analyzer,
    DirectionFlagMissingError,
    JobHealth,
    KilnApi,
    MeasurementFailed,
    ModelPolicyRefused,
    ScoreSpec,
    TargetExpansionError,
    expand_targets,
    pairable_item_count,
    priority_weight,
    row_net_items,
    speed_quality_reasons,
    speed_rule_text,
)
from kiln_harness_tuner.budget import (
    BudgetHolds,
    measurement_prices,
    remaining_run_budget,
)
from kiln_harness_tuner.concurrency import first_error_or_done
from kiln_harness_tuner.contract import contract_preflight
from kiln_harness_tuner.detectability import (
    run_detectability_check,
    train_detectability,
)
from kiln_harness_tuner.dispatch.base import AgentDispatcher
from kiln_harness_tuner.gateway.ops import VAL_SUMMARY_FILE
from kiln_harness_tuner.gateway.trainset import compute as compute_train_set
from kiln_harness_tuner.generality import audit_baseline
from kiln_harness_tuner.integrity import (
    output_shape_findings,
    quote_findings,
    rationale_addresses,
    write_action_findings,
)
from kiln_harness_tuner.items import (
    LOOP_DISPATCH_BACKOFFS,
    call_with_retries,
    execute_plan_item,
)
from kiln_harness_tuner.judge_copy import read_eval_texts
from kiln_harness_tuner.kiln import KilnError
from kiln_harness_tuner.latency import (
    PairedPass,
    arm_line,
    incomplete_note,
    item_latencies,
    latency_line,
    paired_drive_pass,
    paired_floor,
    sample_inputs,
    single_arm,
    split_inputs,
    timed_drives,
)
from kiln_harness_tuner.memory import (
    MemoryRecord,
    MemoryRecordNotFoundError,
    MemoryWriter,
    MemoryWriteRefused,
)
from kiln_harness_tuner.memory_schema import RecordKindError
from kiln_harness_tuner.model_policy import (
    ModelPolicy,
    allowed_models,
    run_config_pair,
)
from kiln_harness_tuner.model_preflight import flagged_reason, run_model_preflight
from kiln_harness_tuner.noise import run_noise_check
from kiln_harness_tuner.objective import better_word, decisive, is_efficiency
from kiln_harness_tuner.probes import (
    ProbeSetupError,
    _parent_props,
    recon_items,
    task_run_configs,
    thinking_levels,
)
from kiln_harness_tuner.prompt_text import normalized, rc_prompt_text, task_instructions
from kiln_harness_tuner.provider_errors import ProviderQuotaError, quota_stop_line
from kiln_harness_tuner.quarantine import (
    QuarantineError,
    heldout_item_ids,
    membership,
    new_eval_split_problem,
    survey_heldout,
    task_dirs,
)
from kiln_harness_tuner.quarantine import eval_ids as quarantine_eval_ids
from kiln_harness_tuner.reconcile import reconcile
from kiln_harness_tuner.schemas import (
    PROVIDER_LANE,
    SUBSCRIPTION_LANE,
    AgentResult,
    BaselineRef,
    BatchPlan,
    BatchPlanInput,
    CandidateValTable,
    ErrorInfo,
    EvalTarget,
    ExperimentItem,
    FixWork,
    GateBlock,
    IntegrityReviewInput,
    ItemOutcome,
    LatencyComparison,
    LeverCost,
    MergeWork,
    ProbeAnswer,
    PromotionDecision,
    PromotionInput,
    RegressedIssue,
    ScreenResult,
    TargetBlock,
    TrainDetectability,
    lane_for_source,
)
from kiln_harness_tuner.setup import (
    _require_rows,
    eval_rows_from_response,
    measurable_evals,
    run_setup,
)
from kiln_harness_tuner.snapshots import snapshot_memory
from kiln_harness_tuner.status import item_summary, plan_headline
from kiln_harness_tuner.stop_rules import default_stop_rules, noise_flip_rate
from kiln_harness_tuner.viability import (
    drop_settled_rescreens,
    initial_screen_request,
    load_prior_screening,
    run_screening_round,
    settled_survivors,
)

logger = logging.getLogger(__name__)

# The wrap-up window IS the reserved room that stops a run exiting while
# wins sit pooled and unmerged. It is a FLOOR, not the window: `should_wrap`
# takes the max of this and the run's own mean wave-cycle time, so a run whose
# waves outgrow half an hour reserves what its own waves actually cost.
WRAP_UP_WINDOW_SECS = 30 * 60
# The floor under the wrap-up window: with little time left, a wrap-up wave
# would be a full planner session whose only arm is cancelled the moment it
# starts. No wave can complete in less
# than a planner session plus the cheapest arm, so below this the loop goes
# straight to the report.
MIN_WAVE_START_SECS = 15 * 60
# An item that is ANALYZING at the deadline has already paid for its build
# and its drives; the analysis is one bounded agent session and the only
# thing standing between that spend and a stored verdict. Such items get
# this much extra time; every other in-flight item is cancelled at once.
ANALYSIS_GRACE_SECS = 10 * 60
# One long backoff after a tripped wave, then continue.
TRIPWIRE_BACKOFF_SECS = 300.0
# Promotion apply writes: up to 3 calls with 20s backoff; exhaustion is fatal.
PROMOTION_WRITE_ATTEMPTS = 3
PROMOTION_WRITE_BACKOFF_SECS = 20.0


class EventLog(Protocol):
    """Minimal audit-event surface (wave_log.py implements it)."""

    def event(self, kind: str, **fields: object) -> None: ...


class NullLog:
    def event(self, kind: str, **fields: object) -> None:
        del kind, fields


class SpendMeter:
    """An EventLog sink that accumulates `spend` events into a running total
    — wired into the fan-out beside the
    tracker so the loop can enforce `--budget-usd` at wave boundaries without
    reaching into the UI tracker."""

    def __init__(self) -> None:
        self.total = 0.0
        # The provider lane alone — what spend reconciliation
        # compares with the records (`kiln_harness_tuner.reconcile`).
        self.provider_total = 0.0

    def event(self, kind: str, **fields: object) -> None:
        if kind != "spend":
            return
        cost = fields.get("cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            self.total += float(cost)
            lane = fields.get("lane") or lane_for_source(str(fields.get("source")))
            if lane == PROVIDER_LANE:
                self.provider_total += float(cost)


# Coverage floor default: a val fill
# whose stored coverage sits below this makes its candidate ineligible for
# promotion this wave. 0.95 matches the caution threshold the analyzer/promote
# skills already grade at ("sub-~95% coverage ⇒ graded caution").
DEFAULT_COVERAGE_FLOOR = 0.95


@dataclass
class Ctx:
    """Plain wiring object, built once in cli.py — not a schema, never
    serialized. `clock`/`sleep` injectable for the deadline/backoff tests."""

    dispatch: AgentDispatcher
    kiln: KilnApi
    memory: MemoryWriter
    analyzer: Analyzer
    target: TargetBlock
    log: EventLog = field(default_factory=NullLog)
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    # Seam (report.py): given (ctx, exit_reason), writes and returns
    # the report. None → orchestrate returns the exit reason.
    write_report: Callable[[Ctx, str], Awaitable[str]] | None = None
    # Agent-free report path: renders the
    # code-only fallback report. Used on fatal/circuit-break exits, where a
    # report agent may be unable to run (or spending on one is wrong) — a
    # report always ships, whatever the exit.
    write_fallback: Callable[[Ctx, str], Awaitable[str]] | None = None
    # The promotion gate's machine-enforced coverage floor.
    coverage_floor: float = DEFAULT_COVERAGE_FLOOR
    # --budget-usd. None = no cost ceiling (the historical behavior).
    budget_usd: float | None = None
    spend: SpendMeter | None = None
    # What the candidates being measured still may spend, so
    # measurements running side by side size their caps against the same
    # run budget only once (`kiln_harness_tuner.budget`, **Held**).
    budget_holds: BudgetHolds = field(default_factory=BudgetHolds)
    # The run's hard deadline in `clock` units, set by `orchestrate`.
    # Read by the promotion gate and the report writer, so their full
    # ladders do not run AFTER the deadline has passed.
    deadline: float | None = None
    # Where the run's memory snapshots go (runs/<id>/snapshots);
    # None = keep none. The dispatcher keeps its own step records beside them.
    run_dir: Path | None = None
    # Who decides a ONE-candidate gate. "auto" = the code rule
    # (`decide_single_candidate`) — a yes/no question the val table already
    # answers; the promote agent is dispatched only to RANK two or more.
    # "always" = dispatch the agent for every gate (`--gate-agent always`).
    gate_agent: str = "auto"
    # The kickoff guidance, verbatim — every agent that makes a decision the
    # guidance can settle receives it (the planner and the promotion
    # gate; without it the gate escalates on an OPEN constraint the
    # guidance had answered).
    guidance: str = ""
    # item id → its current `item_state` (building_rc / measuring /
    # analyzing / …), kept by items.py beside the log event so the wave
    # runner can tell an ANALYZING item from the rest at the deadline.
    item_states: dict[str, str] = field(default_factory=dict)
    # The dispatcher's agent-session cap (None = unlimited) and, per
    # technique, the (usd, secs) of every build this run — both go to the
    # planner so it can see idle capacity and price a second arm.
    max_concurrent_agents: int | None = None
    build_samples: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    # Every probe answered this run, newest last —
    # the reconnaissance battery's and each wave's; fed to every planning
    # call. `recon` runs the battery at setup (cli default on; tests off).
    probe_answers: list[ProbeAnswer] = field(default_factory=list)
    recon: bool = False
    # `objective`: `--objective` ("quality" | "speed").
    #   `model_policy`: `--model-policy`, loaded; None = every model allowed.
    #   `quarantine_root`: where the start-up quarantine moves stored val/test
    #   results — a tree the read fence denies. cli.py always sets it; None
    #   skips the pass (other unit tests).
    #   `train_set_path`: where the run's TRAIN item set goes for the loop
    #   gateway (`gateway.trainset`) — written before the first dispatch.
    #   `policy_exempt_rcs`: the baseline chain, measured whatever its model —
    #   it is the operator's starting point, and every promotion after it is
    #   checked. Kept current each wave.
    objective: str = "quality"
    model_policy: ModelPolicy | None = None
    quarantine_root: Path | None = None
    train_set_path: Path | None = None
    # The evals on disk at start-up; an eval that appears later
    #   (an agent built it, perhaps through a spec) is checked once before it
    #   is measured (`quarantine.new_eval_split_problem`). None = no check.
    startup_eval_ids: frozenset[str] | None = None
    checked_evals: dict[str, str | None] = field(default_factory=dict)
    # The active evals whose config Kiln can't load (eval id -> its name and
    # Kiln's reason), set by setup: the whole run leaves them out.
    #   `unloadable_eval_ids`: every eval setup found Kiln can't load, active
    #   or not — a read that loads its config would fail the same way.
    unloadable_evals: dict[str, tuple[str, str]] = field(default_factory=dict)
    unloadable_eval_ids: frozenset[str] = frozenset()
    policy_exempt_rcs: set[str] = field(default_factory=set)
    # `measure_baseline_first`: measure the baseline on train
    #   (every active eval, the full split) at setup when its stored rows do
    #   not cover the split, so wave 1's planner has eval sizes and — under
    #   speed — a timed baseline. cli.py sets it; other tests
    #   leave it off. `baseline_speed`: rc id -> its judge-free timing on the
    #   fixed speed sample (one timing per baseline, reused every wave).
    measure_baseline_first: bool = False
    # Seconds between the setup measurement's progress lines.
    progress_interval_secs: float = 30.0
    opening_screen: bool = False  # cli: on; the speed/cost opening round
    # The screening phase: every round's results this run,
    # survivors screened on every eval (rc id -> round), rounds this wave.
    screen_results: list[ScreenResult] = field(default_factory=list)
    screened_rcs: dict[str, int] = field(default_factory=dict)
    # Earlier runs' screening results, loaded at run start
    # (`viability.load_prior_screening`), and code's notes on this wave's
    # screening requests.
    prior_screen_results: list[ScreenResult] = field(default_factory=list)
    # Arms the residual-minibatch gate stopped this run, for the
    # planner (`schemas.ResidualMiss`).
    residual_misses: list[Any] = field(default_factory=list)
    # Candidates the gate blocked this run for copying eval
    # text, for the planner (`schemas.GateBlock`).
    gate_blocks: list[GateBlock] = field(default_factory=list)
    screening_notes: list[str] = field(default_factory=list)
    screen_rounds_this_wave: int = 0
    screen_round_count: int = 0
    baseline_cost: dict[str, LatencyComparison | None] = field(default_factory=dict)
    baseline_speed: dict[str, LatencyComparison | None] = field(default_factory=dict)
    # The drives through eval jobs (`kiln_harness_tuner.job_drives`), made
    # on first use by `driver_for` and kept for the run.
    eval_job_driver: Any = None
    _rc_pairs: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    # The detectability and noise checks (`kiln_harness_tuner.detectability`,
    # `kiln_harness_tuner.noise`).
    #   `detectability_check`: run the free pre-run check after the baseline's
    #   train measurement (cli: on; other tests leave it off).
    #   `min_effect`: `--min-effect`, a fraction of items, or None.
    #   `allow_underpowered`: `--allow-underpowered` — a refusal warns only.
    #   `noise_check_items`: `--noise-check N`, 0 = off. `train_noise`: its
    #   train-only rows, for every planner. `train_split_sizes`: eval id ->
    #   train split size on disk, read once by the check.
    detectability_check: bool = False
    # Audit the baseline's own text for train-specific content
    #   once per baseline (`generality.audit_baseline`; cli: on, unless
    #   `--no-baseline-audit`; other tests leave it off).
    baseline_audit: bool = False
    # What the reference check does with a build that names a
    #   skill or a tool it does not attach (`kiln_harness_tuner.reference_check`):
    #   "warn", "refuse" or "off" (cli `--reference-check`: warn; other
    #   tests leave it off). `reference_findings`: item id -> the
    #   findings for the run config it measures, put on its outcome.
    reference_check: str = "off"
    reference_findings: dict[str, list[str]] = field(default_factory=dict)
    min_effect: float | None = None
    allow_underpowered: bool = False
    noise_check_items: int = 0
    train_noise: list[Any] = field(default_factory=list)
    train_split_sizes: dict[str, int] | None = None
    # The stored eval-run files read from disk for the queue's
    # fragility order (`measure.stored_rows_on_disk`), by path, so a file is
    # read once per run.
    eval_run_index: dict[str, Any] = field(default_factory=dict)
    # `model_preflight` (`kiln_harness_tuner.model_preflight`): run one
    #   test call per model the run may use before the baseline measurement
    #   (cli: on, unless `--skip-model-preflight`; other tests
    #   leave it off). `flagged_models`: (provider, model) -> why the
    #   preflight flagged it (a hard failure only); such a model is left out
    #   of `allowed_models`, of every screening round and of every
    #   measurement (`require_policy`) this run.
    model_preflight: bool = False
    flagged_models: dict[tuple[str, str], str] = field(default_factory=dict)


def should_wrap(
    deadline: float,
    clock: Callable[[], float],
    projected_wave_secs: float | None = None,
) -> bool:
    """The budget judgment lives in code — the planner gets only the
    conclusion. The window is the LARGER of the static 30-min reserve and the
    run's own measured wave-cycle time: the static window alone cannot be
    honest once real waves outgrow it."""
    remaining = deadline - clock()
    window = max(WRAP_UP_WINDOW_SECS, projected_wave_secs or 0.0)
    return 0 < remaining < window


async def orchestrate(ctx: Ctx, user_guidance: str, timeout_mins: int = 180) -> str:
    """Run the loop; ALWAYS exits through a report.

    Exit paths:
    - clean loop exit → the agent-written report (`ctx.write_report`);
    - `ProviderQuotaError` (the circuit breaker) → the code-only fallback
      report, exit reason `quota_exhausted`, returned without raising — the
      operator message carries the provider's own remedy hint;
    - any other exception → the fallback report (best-effort, only once a
      wave has started), an `exit` event with `fatal:<type>`, then re-raise.
      every termination leaves both an `exit` event and a report.
    """
    inner_log = ctx.log
    ctx.log = _ReconcileBeforeExit(inner_log, ctx)
    try:
        return await _orchestrate(ctx, user_guidance, timeout_mins)
    finally:
        ctx.log = inner_log


class _ReconcileBeforeExit:
    """The run's event log, with spend reconciliation in front of the first
    `exit` event: whichever of the exits fires — the loop's own,
    the quota breaker, a fatal error, an interrupt — the records are priced
    and the gap booked first, so the exit line and the report carry the
    reconciled figure (`kiln_harness_tuner.reconcile`)."""

    def __init__(self, inner: EventLog, ctx: Ctx):
        self._inner = inner
        self._ctx = ctx
        self._done = False

    def event(self, kind: str, **fields: object) -> None:
        if kind == "exit" and not self._done:
            self._done = True
            reconcile(self._ctx, when="exit")
        self._inner.event(kind, **fields)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


async def _orchestrate(ctx: Ctx, user_guidance: str, timeout_mins: int) -> str:
    deadline = ctx.clock() + timeout_mins * 60
    ctx.deadline = deadline
    ctx.guidance = user_guidance
    ctx.log.event(
        "run_start",
        timeout_mins=timeout_mins,
        # `deadline` is in Ctx.clock (monotonic) units — the tracker's input
        # (its clock must share the domain, see RunStatusTracker); the ISO
        # twin exists for the wave-log audit trail, where a raw monotonic
        # float is uninterpretable.
        deadline=deadline,
        deadline_iso=(datetime.now() + timedelta(minutes=timeout_mins)).isoformat(),
        budget_usd=ctx.budget_usd,
        objective=ctx.objective,
        model_policy=ctx.model_policy.summary() if ctx.model_policy else None,
    )
    progress = _RunProgress()
    try:
        exit_reason = await _run_loop(ctx, user_guidance, deadline, progress)
    except ProviderQuotaError as exc:
        exit_reason = "quota_exhausted"
        if exc.usage is not None:
            # The session(s) that hit the wall still cost money (review
            # Book them before the exit, like any dispatch.
            ctx.log.event(
                "spend",
                source="circuit_breaker",
                lane=SUBSCRIPTION_LANE,
                cost_usd=exc.usage.cost_usd,
                unmeasured_tokens=exc.usage.unmeasured_tokens,
            )
        stop_line = quota_stop_line(exc.error.error_class, exc.error.detail)
        ctx.log.event("provider_quota", line=stop_line, detail=exc.error.detail)
        ctx.log.event("exit", reason=exit_reason, detail=exc.error.detail)
        logger.error("%s", stop_line)
        logger.error(
            "run circuit-broken (%s) — stop spending, fix the provider, then "
            "re-run: %s",
            exc.error.error_class,
            exc.error.detail,
        )
        if ctx.write_fallback is not None:
            return await ctx.write_fallback(ctx, exit_reason)
        return exit_reason
    except Exception as exc:
        reason = f"fatal:{type(exc).__name__}"
        ctx.log.event("exit", reason=reason, detail=str(exc)[:500])
        logger.error("run died on a fatal error: %r", exc)
        if progress.waves_started and ctx.write_fallback is not None:
            try:
                await ctx.write_fallback(ctx, reason)
            except Exception as report_exc:  # the report must not mask the fatal
                logger.error("fallback report also failed: %r", report_exc)
        raise
    except BaseException:
        # Ctrl-C / cancellation: leave an exit event in the audit trail, but
        # no report — the operator asked us to stop NOW.
        ctx.log.event("exit", reason="interrupted")
        raise
    if ctx.write_report is not None:  # report seam (report.py)
        return await ctx.write_report(ctx, exit_reason)
    return exit_reason


@dataclass
class _RunProgress:
    """How far the loop got — read by orchestrate's fatal handler (a report
    stub for a run that died in preflight would be noise)."""

    waves_started: int = 0


async def _run_loop(
    ctx: Ctx, user_guidance: str, deadline: float, progress: _RunProgress
) -> str:
    # Pre-flight, voiced: these opening steps run after run_start, so the UIs
    # have a header to hang the lines under. That starts the budget a few
    # seconds earlier — the pre-flight reads are cheap REST calls.
    #
    # Order: the MCP-lane preflight comes FIRST because the
    # setup phase dispatches agents — a broken lane must fail before that, not
    # during it — and the janitor + baseline read come AFTER setup, because
    # the `baseline` record setup may have just seeded has to be visible to
    # this same run.
    #
    # Once-per-run MCP-lane preflight: prove the agents'
    # gateway lane works (its `ping`) BEFORE any paid dispatch — a broken lane fails
    # the run here with an instructive PreflightError, never three waves in.
    # Before ANY dispatch — the MCP-lane preflight included —
    # stored val/test results leave the project, and the train set the
    # gateway answers from is written.
    run_quarantine(ctx)
    ctx.analyzer.rc_guard = lambda rc_id: require_policy(ctx, rc_id)
    preflight_started = ctx.clock()
    ctx.log.event(
        "phase_start",
        name="preflight",
        label="Preflight: verifying the Kiln Harness Tuner gateway MCP lane…",
    )
    try:
        preflight_usage = await ctx.dispatch.preflight()
    finally:
        ctx.log.event(
            "phase_end",
            name="preflight",
            elapsed_secs=ctx.clock() - preflight_started,
        )
    # Durable wave-log evidence the lane was proven, symmetric with
    # `baseline_found`/`janitor` (the transient phase events are UI-facing).
    ctx.log.event(
        "preflight", kiln_host=ctx.target.kiln_host, cost_usd=preflight_usage.cost_usd
    )
    ctx.log.event(
        "spend",
        source="preflight",
        lane=SUBSCRIPTION_LANE,
        cost_usd=preflight_usage.cost_usd,
        unmeasured_tokens=preflight_usage.unmeasured_tokens,
    )
    # The setup phase: seed the baseline record, sync the
    # issue store against the task's evals, audit the splits. On a cold
    # project the description fan-out is a multi-minute silence (one session
    # per active eval — 28 on a wide task), so it gets a phase with a
    # ticker like every other quiet stretch; on a warm one it is a handful of
    # REST reads. try/finally for the same reason planning has it: a fatal
    # setup error must not leave a dangling phase_start. The returned
    # SetupReport is deliberately unused here — it reaches `RunStatus` (and
    # from there the UIs and the report) on the `setup_sync`/`setup_ok`
    # event, the same way every other pre-flight step reports itself.
    setup_started = ctx.clock()
    ctx.log.event(
        "phase_start",
        name="setup",
        label="Setup: syncing the baseline and issue memories with the task's evals…",
    )
    try:
        await run_setup(ctx)
    finally:
        ctx.log.event(
            "phase_end", name="setup", elapsed_secs=ctx.clock() - setup_started
        )
    stamped = await ctx.memory.janitor_abandoned_experiments()
    ctx.log.event("janitor", count=len(stamped), memory_ids=stamped)
    # Fail-fast — after setup, which seeds the record on a cold task. It
    # can no longer fire there, and stays as the invariant's enforcement point
    # (defence in depth: it still catches a record deleted mid-run).
    startup_record = await ctx.memory.require_baseline_record()
    # The record's overview is free prose, so the event carries the id alone
    # — piping unbounded prose into a fixed CLI line would rebuild the format
    # coupling the split just removed. Cold-start provenance reaches the
    # human through setup's own `baseline_seeded` line; the record itself is
    # one read away.
    ctx.log.event("baseline_found", rc_id=ctx.memory.baseline_rc_id(startup_record))
    # What earlier runs screened — for the planner's table, and
    # so a survivor already screened on every eval skips arm viability.
    await load_prior_screening(ctx)
    ctx.policy_exempt_rcs.update(
        [
            ctx.memory.baseline_rc_id(startup_record),
            *ctx.memory.baseline_lineage(startup_record),
        ]
    )
    await _note_baseline_policy(ctx, ctx.memory.baseline_rc_id(startup_record))
    # $0 API-contract check: the evals envelope, both statistics
    # operations and one stored-results read, against the LIVE server, so a
    # response shape that moved past the reader fails here with the field
    # names — not three waves in, silently measuring an empty set.
    contract = await contract_preflight(ctx, ctx.memory.baseline_rc_id(startup_record))
    ctx.log.event("contract_ok", **contract)
    # One cheap test call per model the run may use, before the
    # baseline measurement and every wave spend. A failed model is flagged
    # and left out of screening; a failed baseline model stops the start.
    if ctx.model_preflight:
        await run_model_preflight(ctx, ctx.memory.baseline_rc_id(startup_record))
    await prepare_baseline(ctx, ctx.memory.baseline_rc_id(startup_record))
    # The generality check reads only what a build adds, so the
    # baseline's own text is audited once here; a leak becomes a scrub issue
    # the first planner reads. It never stops the run.
    if ctx.baseline_audit:
        await audit_baseline(ctx, ctx.memory.baseline_rc_id(startup_record))
    # Can the evals show a win at all? Free, from the stored
    # train rows and the split sizes on disk; it refuses the run here, before
    # the reconnaissance battery and every wave spend. Then the opt-in paid
    # noise check, whose rows the planner reads as train-side orientation.
    if ctx.detectability_check:
        await run_detectability_check(ctx, ctx.memory.baseline_rc_id(startup_record))
    if ctx.noise_check_items:
        await run_noise_check(ctx, ctx.memory.baseline_rc_id(startup_record))
    if ctx.recon:
        await run_recon(
            ctx, BaselineRef(rc_id=ctx.memory.baseline_rc_id(startup_record)), deadline
        )
    if ctx.opening_screen:
        await run_opening_screen(
            ctx, BaselineRef(rc_id=ctx.memory.baseline_rc_id(startup_record))
        )
    wave = 0
    prior_rejected_summary: str | None = None  # None on wave 1
    consecutive_tripwires = 0
    exit_reason = "timeout"
    # Budget honesty: the run's own measured cycle times/costs feed the
    # wrap-up admission gate — a wave is only started if the budget can be
    # expected to fit it.
    wave_cycle_secs: list[float] = []
    wave_cycle_costs: list[float] = []
    while True:
        wave += 1
        # At every wave boundary the ledger is reconciled with the
        # records, so the budget check below reads the true spend.
        reconcile(ctx, when=f"before wave {wave}", quiet=True)
        cycle_started = ctx.clock()
        if cycle_started >= deadline:
            # HARD stop: no wave — wrap-up included — ever STARTS past the
            # deadline. The should_wrap buffer below IS the reserved room.
            exit_reason = "timeout"
            ctx.log.event("exit", reason=exit_reason)
            break
        if deadline - cycle_started < MIN_WAVE_START_SECS:
            # Under the floor a wave cannot finish: the planner alone takes
            # 4-8 minutes. Exit to the report instead of paying for a plan
            # whose arms are cancelled on arrival.
            exit_reason = "timeout"
            ctx.log.event(
                "exit",
                reason=exit_reason,
                detail=(
                    f"{int(deadline - cycle_started)}s left, under the "
                    f"{MIN_WAVE_START_SECS}s floor to start a wave"
                ),
            )
            break
        projected = fmean(wave_cycle_secs) if wave_cycle_secs else None
        wrap_up = should_wrap(deadline, ctx.clock, projected)
        # --budget-usd is a real ceiling, not a ledger. At the ceiling the
        # run exits; inside one projected wave of it, the next wave is the
        # wrap-up. Checked at wave boundaries, like the deadline.
        spent = ctx.spend.total if ctx.spend is not None else None
        if ctx.budget_usd is not None and spent is not None:
            if spent >= ctx.budget_usd:
                exit_reason = "budget_exhausted"
                ctx.log.event(
                    "exit",
                    reason=exit_reason,
                    spent_usd=spent,
                    budget_usd=ctx.budget_usd,
                )
                logger.warning(
                    "budget exhausted: $%.2f spent of $%.2f — exiting",
                    spent,
                    ctx.budget_usd,
                )
                break
            if wave_cycle_costs and (ctx.budget_usd - spent < fmean(wave_cycle_costs)):
                wrap_up = True

        # select_baseline (code): fresh read each wave — the prior wave's
        # promotion moves the record. Passes the rc id onward, and nothing
        # else: val metrics live in a different record entirely.
        record = await ctx.memory.require_baseline_record()
        baseline = BaselineRef(rc_id=ctx.memory.baseline_rc_id(record))
        lineage = ctx.memory.baseline_lineage(record)
        ctx.policy_exempt_rcs.update([baseline.rc_id, *lineage])

        # The planning dispatch is a full agent session and the run's longest
        # silence (minutes with only the header printed) —
        # phase events give it a start line, a live ticker, and a done line.
        plan_result = await _dispatch_plan(
            ctx,
            wave=wave,
            wrap_up=wrap_up,
            baseline=baseline,
            user_guidance=user_guidance,
            phase="plan",
            prior=prior_rejected_summary,
        )
        plan = plan_result.data
        if plan is None:
            # Planner hard-fail: it's the loop's brain — exit the run.
            exit_reason = "planner_failed"
            ctx.log.event("exit", reason=exit_reason)
            break
        await ctx.memory.write_open_unknowns(plan.open_questions)  # code writes
        await snapshot_memory(ctx, f"w{wave}-plan-after")
        ctx.screen_rounds_this_wave = 0
        ctx.screening_notes = []
        plan = await _screening_phase(
            ctx,
            plan,
            wave=wave,
            wrap_up=wrap_up,
            baseline=baseline,
            user_guidance=user_guidance,
        )
        if plan is None:
            exit_reason = "planner_failed"
            ctx.log.event("exit", reason=exit_reason)
            break
        # Schema-guaranteed mutually exclusive: a plan with
        # loop_exit has no items, so honoring exit never drops composed work.
        if plan.loop_exit or not plan.items:
            exit_reason = plan.loop_exit or "empty_plan"
            ctx.log.event("exit", reason=exit_reason)
            break

        if plan.probe_phase:
            # The wave's PROBE PHASE: the planner asked for the
            # answers before it commits arms. Run the probes now (minutes),
            # then plan again with the answers in hand. One phase per wave:
            # a second probe-phase plan runs as an ordinary wave of probes.
            ctx.log.event(
                "wave_start",
                wave=wave,
                should_wrap_up=wrap_up,
                baseline_rc_id=baseline.rc_id,
                plan_headline=f"probe phase — {plan_headline(plan.items)}",
                rejected_summary=plan.rejected_summary,
                items=[item_summary(item) for item in plan.items],
                probe_phase=True,
            )
            _, deadline_hit = await _run_wave(
                ctx, plan.items, baseline, lineage, wave, deadline
            )
            ctx.log.event("wave_end", wave=wave, probe_phase=True)
            if deadline_hit:
                exit_reason = "timeout"
                ctx.log.event("exit", reason=exit_reason)
                break
            plan_result = await _dispatch_plan(
                ctx,
                wave=wave,
                wrap_up=wrap_up,
                baseline=baseline,
                user_guidance=user_guidance,
                phase="replan",
                prior=plan.rejected_summary,
            )
            plan = plan_result.data
            if plan is None:
                exit_reason = "planner_failed"
                ctx.log.event("exit", reason=exit_reason)
                break
            await ctx.memory.write_open_unknowns(plan.open_questions)
            await snapshot_memory(ctx, f"w{wave}-replan-after")
            plan = await _screening_phase(
                ctx,
                plan,
                wave=wave,
                wrap_up=wrap_up,
                baseline=baseline,
                user_guidance=user_guidance,
            )
            if plan is None:
                exit_reason = "planner_failed"
                ctx.log.event("exit", reason=exit_reason)
                break
            if plan.loop_exit or not plan.items:
                exit_reason = plan.loop_exit or "empty_plan"
                ctx.log.event("exit", reason=exit_reason)
                break
            if plan.probe_phase:
                ctx.log.event("probe_phase_repeated", wave=wave)

        ctx.log.event(
            "wave_start",
            wave=wave,
            should_wrap_up=wrap_up,
            baseline_rc_id=baseline.rc_id,
            plan_headline=plan_headline(plan.items),
            rejected_summary=plan.rejected_summary,
            items=[item_summary(item) for item in plan.items],
        )
        progress.waves_started += 1
        outcomes, deadline_hit = await _run_wave(
            ctx, plan.items, baseline, lineage, wave, deadline
        )

        if deadline_hit:
            # Hard deadline: in-flight items were cancelled AT the
            # deadline (see _run_wave) — their failed_infra(timeout) rows
            # must not count toward the tripwire (it would sleep 5 minutes
            # past a deadline, or mislabel the exit as `tripwire`).
            tripped = False
        else:
            tripped = await check_wave_tripwire(ctx, wave, outcomes)
        consecutive_tripwires = consecutive_tripwires + 1 if tripped else 0
        tripwire_exit = consecutive_tripwires >= 2

        # Wrap-up AND tripped waves included: promotion always runs
        # over what completed (a measured winning merge must not
        # strand behind an infra exit). maybe_promote also emits `wave_end`
        # once the wave's block is final (scrollback ordering).
        await maybe_promote(ctx, outcomes, record, wave)
        await archive_probe_run_configs(ctx)

        wave_cycle_secs.append(ctx.clock() - cycle_started)
        if ctx.spend is not None and spent is not None:
            wave_cycle_costs.append(ctx.spend.total - spent)

        if deadline_hit:  # promotion-over-what-completed ran; now stop
            exit_reason = "timeout"
            ctx.log.event("exit", reason=exit_reason)
            break
        if tripwire_exit:  # double tripwire exits AFTER promotion
            exit_reason = "tripwire"
            ctx.log.event("exit", reason=exit_reason)
            break
        if wrap_up:  # the wrap-up wave was the last wave — never a second one
            exit_reason = "wrap_up"
            ctx.log.event("exit", reason=exit_reason)
            break
        if tripped:
            # Long backoff — 5 silent minutes without a phase ticker.
            backoff_started = ctx.clock()
            ctx.log.event(
                "phase_start",
                name="tripwire_backoff",
                wave=wave,
                label=(
                    "Tripwire backoff — pausing "
                    f"{int(TRIPWIRE_BACKOFF_SECS) // 60}m before the next wave…"
                ),
            )
            await ctx.sleep(TRIPWIRE_BACKOFF_SECS)
            ctx.log.event(
                "phase_end",
                name="tripwire_backoff",
                wave=wave,
                elapsed_secs=ctx.clock() - backoff_started,
            )
        prior_rejected_summary = plan.rejected_summary  # the one in-context carry

    return exit_reason


async def _dispatch_plan(
    ctx: Ctx,
    *,
    wave: int,
    wrap_up: bool,
    baseline: BaselineRef,
    user_guidance: str,
    phase: str,
    prior: str | None,
) -> AgentResult[BatchPlan]:
    """One planning dispatch (a function of its own so the wave's probe phase
    can call it twice — `plan`, then `replan` with the probe answers)."""
    planning_started = ctx.clock()
    replan = phase == "replan"
    ctx.log.event(
        "phase_start",
        name="planning",
        wave=wave,
        label=(
            f"{'Re-planning' if replan else 'Planning'} "
            f"{'wrap-up ' if wrap_up else ''}wave {wave}"
            f"{' with the probe answers' if replan else ''}… "
            "(a full agent session — typically 3-10 min)"
        ),
    )
    await snapshot_memory(ctx, f"w{wave}-{'re' if replan else ''}plan-before")
    try:
        # Inside the phase, not before it: 1 + N sequential reads that
        # would otherwise be the one stretch of the wave loop with nothing
        # ticking — the shape the phase events exist for. Free reads,
        # refetched per wave.
        (
            eval_sizes,
            baseline_latency,
            detect_rows,
        ) = await _eval_train_sizes_and_latency(ctx, baseline.rc_id)
        baseline_cost = None
        if is_efficiency(ctx.objective):
            # The fair numbers: the baseline driven judge-free on the fixed
            # speed sample, the same items every arm is later timed on.
            baseline_latency = await ensure_baseline_speed(ctx, baseline.rc_id)
            baseline_cost = ctx.baseline_cost.get(baseline.rc_id)
        lever_attempts = await _lever_attempts(ctx)
        models = await _allowed_models(ctx, baseline.rc_id)
        result = await call_with_retries(
            ctx.dispatch.batch_plan,
            BatchPlanInput(
                guidance=user_guidance,
                baseline=baseline,
                should_wrap_up=wrap_up,
                eval_sizes=eval_sizes,
                lever_attempts=lever_attempts,
                prior_rejected_summary=prior,
                max_concurrent_agents=ctx.max_concurrent_agents,
                lever_costs=lever_costs(ctx),
                probe_results=list(ctx.probe_answers),
                phase=phase,  # type: ignore[arg-type]
                objective=ctx.objective,  # type: ignore[arg-type]
                speed_rule=(
                    speed_rule_text(ctx.objective)
                    if is_efficiency(ctx.objective)
                    else None
                ),
                baseline_latency=baseline_latency,
                baseline_cost=baseline_cost,
                screening_results=list(ctx.screen_results),
                prior_screening_results=list(ctx.prior_screen_results),
                settled_survivors=settled_survivors(
                    [*ctx.prior_screen_results, *ctx.screen_results]
                ),
                screening_notes=list(ctx.screening_notes),
                residual_misses=list(ctx.residual_misses),
                gate_blocks=list(ctx.gate_blocks),
                measurement_prices=await measurement_prices(
                    ctx, baseline.rc_id, remaining_run_budget(ctx)
                ),
                screen_rounds_left=max(
                    MAX_SCREEN_ROUNDS_PER_WAVE - ctx.screen_rounds_this_wave, 0
                ),
                default_stop_rules=default_stop_rules(
                    ctx.objective,
                    noise_rate=noise_flip_rate(ctx.train_noise),
                ),
                model_policy=(ctx.model_policy.summary() if ctx.model_policy else None),
                allowed_models=models,
                flagged_models=[
                    f"{provider}/{model}: {reason}"
                    for (provider, model), reason in ctx.flagged_models.items()
                ],
                train_detectability=detect_rows,
                train_noise=list(ctx.train_noise),
            ),
            backoffs=LOOP_DISPATCH_BACKOFFS,
            sleep=ctx.sleep,
            log=ctx.log,
            source="batch_plan",
        )
    finally:
        # try/finally: a dispatcher raise or Ctrl-C mid-
        # dispatch must not leave a dangling phase_start in the audit
        # trail / the tracker stuck in a phase.
        ctx.log.event(
            "phase_end",
            name="planning",
            wave=wave,
            elapsed_secs=ctx.clock() - planning_started,
        )
    ctx.log.event(
        "spend",
        source="batch_plan",
        lane=SUBSCRIPTION_LANE,
        cost_usd=result.usage.cost_usd,
        unmeasured_tokens=result.usage.unmeasured_tokens,
    )
    return result


MAX_SCREEN_ROUNDS_PER_WAVE = 4


async def _screening_phase(
    ctx: Ctx,
    plan: BatchPlan,
    *,
    wave: int,
    wrap_up: bool,
    baseline: BaselineRef,
    user_guidance: str,
) -> BatchPlan | None:
    """While the plan asks for a screening round (`plan.screen`), code runs
    it and asks the planner again with the table (phase `rescreen`), up to
    `MAX_SCREEN_ROUNDS_PER_WAVE` rounds. Returns the first plan that carries
    items (or exits), or None when a planning call hard-fails. "Screen many,
    commit few": nothing here measures an arm; survivors become arms only
    when the planner commits them."""
    while plan.screen is not None:
        if ctx.screen_rounds_this_wave >= MAX_SCREEN_ROUNDS_PER_WAVE:
            ctx.log.event(
                "screening_cap",
                wave=wave,
                line=(
                    f"screening: the wave's {MAX_SCREEN_ROUNDS_PER_WAVE}-round cap"
                    " is reached — the plan's screening request is dropped"
                ),
            )
            return plan.model_copy(update={"screen": None})
        ctx.screen_rounds_this_wave += 1
        # A survivor settled over two rounds is committed, not
        # screened again — unless the plan says why (`rescreen_reason`).
        request, dropped = drop_settled_rescreens(
            plan.screen,
            settled_survivors([*ctx.prior_screen_results, *ctx.screen_results]),
        )
        for line in dropped:
            ctx.screening_notes.append(line)
            ctx.log.event("screen_candidate_dropped", wave=wave, line=line)
        if request is not None:
            ctx.screen_round_count += 1
            results = await run_screening_round(
                ctx, request, baseline.rc_id, ctx.screen_round_count
            )
            ctx.screen_results.extend(results)
        replanned = await _dispatch_plan(
            ctx,
            wave=wave,
            wrap_up=wrap_up,
            baseline=baseline,
            user_guidance=user_guidance,
            phase="rescreen",
            prior=plan.rejected_summary,
        )
        if replanned.data is None:
            return None
        plan = replanned.data
        await ctx.memory.write_open_unknowns(plan.open_questions)
    return plan


async def run_opening_screen(ctx: Ctx, baseline: BaselineRef) -> None:
    """Under speed or cost, the run opens with one screening round: the
    baseline's own model at every thinking level the provider offers below
    the current one. Thinking levels are candidate variants, measured in
    code — never probed."""
    if not is_efficiency(ctx.objective):
        return
    try:
        props = await _parent_props(ctx, baseline.rc_id)
    except (ProbeSetupError, KilnError) as exc:
        ctx.log.event("opening_screen_skipped", reason=str(exc)[:300])
        return
    if ctx.model_policy is not None:
        refusal = ctx.model_policy.refusal(
            props.get("model_provider_name"), props.get("model_name")
        )
        if refusal is not None:
            # The policy forbids the baseline's own model as a candidate, so
            # none of its thinking levels can be screened, and screening them
            # would record dead ends for nothing. The
            # planner opens with the allowed models instead.
            ctx.log.event("opening_screen_skipped", reason=f"baseline model: {refusal}")
            return
    levels = await thinking_levels(
        ctx, props.get("model_provider_name"), props.get("model_name")
    )
    request = initial_screen_request(props, levels)
    if request is None:
        ctx.log.event(
            "opening_screen_skipped",
            reason=f"no lower thinking level offered (levels: {levels})",
        )
        return
    ctx.screen_round_count += 1
    ctx.screen_results.extend(
        await run_screening_round(ctx, request, baseline.rc_id, ctx.screen_round_count)
    )


async def run_recon(ctx: Ctx, baseline: BaselineRef, deadline: float | None) -> None:
    """The setup reconnaissance battery: the standing code
    probes on the baseline, run as wave 0 before the first plan so the
    planner reads facts about this model on this task instead of assuming
    them. Answers land as probe-tagged lessons and in `ctx.probe_answers`.
    A baseline whose properties cannot be read skips the battery with an
    event — never a fatal."""
    try:
        props = await _parent_props(ctx, baseline.rc_id)
    except (ProbeSetupError, KilnError) as exc:
        ctx.log.event("recon_skipped", reason=str(exc)[:300])
        return
    items = recon_items(props)
    if not items:
        return
    ctx.log.event(
        "wave_start",
        wave=0,
        should_wrap_up=False,
        baseline_rc_id=baseline.rc_id,
        plan_headline=f"reconnaissance — {plan_headline(items)}",
        rejected_summary="",
        items=[item_summary(item) for item in items],
        probe_phase=True,
    )
    await _run_wave(ctx, items, baseline, [], 0, deadline)
    ctx.log.event("wave_end", wave=0, probe_phase=True)


async def _run_wave(
    ctx: Ctx,
    items: list,
    baseline,
    lineage: list[str],
    wave: int,
    deadline: float | None = None,
) -> tuple[list[ItemOutcome], bool]:
    """One wave's items, concurrently (eval-job concurrency is capped inside
    the Analyzer — `--max-concurrent-evals`; agent dispatches inside the
    dispatcher). Hard errors (no-degradation rows: 422 missing split,
    direction flag, stats contract, memory-write exhaustion) are fatal —
    every sibling finishes first (no orphaned awaits mid-write; leftover
    `— RUNNING` memories are exactly the startup janitor's crash-debris case),
    then the first raises.

    Hard deadline: the run's deadline is not only checked at wave boundaries,
    because a wave started 15s before T-0 would run far past it. Items still
    in flight AT the deadline are cancelled (the dispatcher's kill/reap path
    stops their subprocess spend) and land as failed_infra(timeout); returns
    (outcomes, deadline_hit)."""
    tasks = [
        asyncio.ensure_future(execute_plan_item(ctx, item, baseline, lineage, wave))
        for item in items
    ]
    timeout = None
    if deadline is not None:
        timeout = max(deadline - ctx.clock(), 0.0)
    # The first hard error cancels the siblings. The run exits on
    # it, so their remaining sessions and jobs were spend on a dead run — the
    # circuit breaker would fire and then wait up to an hour for them.
    error, pending = await first_error_or_done(tasks, timeout=timeout)
    if error is not None:
        cancelled_ids = [
            item.id for item, task in zip(items, tasks, strict=True) if task.cancelled()
        ]
        ctx.log.event(
            "fatal_cancel", wave=wave, item_ids=cancelled_ids, error=repr(error)
        )
        raise error
    deadline_hit = bool(pending)
    if pending:
        # Items already ANALYZING keep going for a bounded grace: their
        # build and drives are paid, and the analysis is the one session
        # that turns that spend into a verdict the next run can use.
        by_task = dict(zip(tasks, items, strict=True))
        graced = {
            task
            for task in pending
            if ctx.item_states.get(by_task[task].id) == "analyzing"
        }
        cancelled_ids = [
            item.id
            for item, task in zip(items, tasks, strict=True)
            if task in pending and task not in graced
        ]
        if cancelled_ids:
            ctx.log.event("deadline_cancel", wave=wave, item_ids=cancelled_ids)
            logger.warning(
                "run deadline reached mid-wave %d — cancelling %d item(s): %s",
                wave,
                len(cancelled_ids),
                ", ".join(cancelled_ids),
            )
        for task in pending - graced:
            task.cancel()
        await asyncio.gather(*(pending - graced), return_exceptions=True)
        if graced:
            graced_ids = [by_task[t].id for t in graced]
            ctx.log.event(
                "deadline_grace",
                wave=wave,
                item_ids=graced_ids,
                grace_secs=ANALYSIS_GRACE_SECS,
            )
            logger.warning(
                "run deadline reached mid-wave %d — %d item(s) analyzing get %ds: %s",
                wave,
                len(graced),
                ANALYSIS_GRACE_SECS,
                ", ".join(graced_ids),
            )
            _done, still = await asyncio.wait(graced, timeout=ANALYSIS_GRACE_SECS)
            if still:
                late_ids = [by_task[t].id for t in still]
                ctx.log.event("deadline_cancel", wave=wave, item_ids=late_ids)
                for task in still:
                    task.cancel()
                await asyncio.gather(*still, return_exceptions=True)
    outcomes: list[ItemOutcome] = []
    for item, task in zip(items, tasks, strict=True):
        if task.cancelled():
            outcomes.append(
                ItemOutcome(
                    item=item,
                    status="failed_infra",
                    rc_id=None,
                    experiment_memory_id=None,
                    result=None,
                    infra_error=ErrorInfo(
                        error_class="timeout",
                        detail="run deadline reached — item cancelled in flight",
                    ),
                )
            )
            continue
        outcomes.append(task.result())
    return outcomes, deadline_hit


async def check_wave_tripwire(ctx: Ctx, wave: int, outcomes: list[ItemOutcome]) -> bool:
    """> half the wave's items failed_infra with the same error_class →
    the wave is aborted. ItemOutcome.status stays honest (`failed_infra` is
    never rewritten); only the experiment memories get `— ABANDONED`."""
    # (outcome, error) pairs, so the non-None narrowing survives the filter
    # (the checker cannot see through a bare truthiness comprehension).
    failed = [
        (o, o.infra_error)
        for o in outcomes
        if o.status == "failed_infra" and o.infra_error is not None
    ]
    if not failed:
        return False
    error_class, count = Counter(error.error_class for _o, error in failed).most_common(
        1
    )[0]
    if count * 2 <= len(outcomes):
        return False
    ctx.log.event("tripwire", wave=wave, error_class=error_class, failed=count)
    for outcome, error in failed:
        if outcome.experiment_memory_id is not None:
            # Each memory names its OWN failure class — a minority-class
            # failure must not be mislabeled with the majority class.
            try:
                await ctx.memory.mark_abandoned(
                    outcome.experiment_memory_id,
                    f"wave tripwire: {error.error_class}",
                )
            except MemoryWriteRefused as exc:
                # A record whose state does not allow the move
                # is logged, never a run kill (the tripwire already decided).
                ctx.log.event(
                    "memory_write_failed",
                    write="mark_abandoned",
                    what="the experiment record's state",
                    item_id=outcome.item.id,
                    exp_mem_id=outcome.experiment_memory_id,
                    error=repr(exc),
                )
    return True


# ---- the two-stage gate and the only val touch ----


@dataclass(frozen=True)
class GateCandidate:
    """One promotion candidate AFTER the two-stage filter. The filter
    guarantees rc_id / experiment_memory_id exist on the outcome; carrying
    them here non-Optional makes that invariant structural — every gate
    function reads the ids without re-proving them (the bare ItemOutcome
    would force Optional-blind reads at every call site)."""

    rc_id: str
    experiment_memory_id: str
    outcome: ItemOutcome


def _target_eval_ids(candidate: GateCandidate) -> list[str]:
    """The evals the candidate's experiment set out to move — the code rule's
    targets; every other active eval is a guard."""
    item = candidate.outcome.item
    targets = getattr(item, "evaluation_target", None) or []
    return [t.eval_id for t in targets]


async def maybe_promote(
    ctx: Ctx, outcomes: list[ItemOutcome], record: MemoryRecord, wave: int
) -> PromotionDecision | None:
    await snapshot_memory(ctx, f"w{wave}-gate-before")
    try:
        return await _maybe_promote(ctx, outcomes, record, wave)
    finally:
        await snapshot_memory(ctx, f"w{wave}-gate-after")


async def _maybe_promote(
    ctx: Ctx, outcomes: list[ItemOutcome], record: MemoryRecord, wave: int
) -> PromotionDecision | None:
    # Intent (planner) AND measured success (analyzer) — both required.
    # o.result is None for completed eval_only items — the guard is
    # load-bearing; the isinstance guard narrows the union.
    candidates = [
        GateCandidate(
            rc_id=o.rc_id, experiment_memory_id=o.experiment_memory_id, outcome=o
        )
        for o in outcomes
        if isinstance(o.item, ExperimentItem)
        and o.item.promotion_candidate
        and o.result
        and o.result.successful
        and o.rc_id
        and o.experiment_memory_id
    ]
    # A candidate outside the model policy never reaches the val
    # pass, however it was measured on train.
    allowed: list[GateCandidate] = []
    for candidate in candidates:
        refusal = await policy_refusal(ctx, candidate.rc_id)
        if refusal is None:
            allowed.append(candidate)
            continue
        ctx.log.event(
            "promotion_blocked", winner_rc_id=candidate.rc_id, reasons=[refusal]
        )
        logger.warning("gate: %s", refusal)
    candidates = allowed
    if not candidates:
        # No gate — the wave's block (items + tripwire banner) is already
        # final: `wave_end` lets the live UI freeze it into scrollback now
        # (chronological terminal history).
        ctx.log.event("wave_end", wave=wave)
        return None
    if ctx.deadline is not None and ctx.clock() >= ctx.deadline:
        # The deadline is hard for the gate too. A full val pass
        # (every active eval × candidates + baseline, each on the job ladder)
        # plus a promote session ran up to 72 minutes PAST the deadline. The
        # wins stay pooled in memory — the next run's planner can nominate
        # them on complete evidence.
        ctx.log.event(
            "promotion_skipped",
            reason="deadline reached — val pass not started; wins stay pooled",
            candidate_rc_ids=[c.rc_id for c in candidates],
        )
        ctx.log.event("wave_end", wave=wave)
        return None

    # The gate is the wave's other quiet stretch (val fills + a promote_best
    # agent session + the apply writes) — one phase brackets all of it, with
    # try/finally so a fatal apply-write exhaustion still closes the phase.
    gate_started = ctx.clock()
    ctx.log.event(
        "phase_start",
        name="promotion_gate",
        wave=wave,
        label=(
            f"Promotion gate: measuring {len(candidates)} candidate"
            f"{'s' if len(candidates) != 1 else ''} on val…"
        ),
    )
    try:
        return await _run_promotion_gate(ctx, candidates, record)
    finally:
        # `wave_end` BEFORE the gate's phase_end: the promotion
        # banner is set by the time the gate closes, so the block — banner
        # included — freezes into scrollback first, and the gate's done-line
        # prints below it, keeping terminal history chronological.
        ctx.log.event("wave_end", wave=wave)
        ctx.log.event(
            "phase_end",
            name="promotion_gate",
            wave=wave,
            elapsed_secs=ctx.clock() - gate_started,
        )


async def _run_promotion_gate(
    ctx: Ctx, candidates: list[GateCandidate], record: MemoryRecord
) -> PromotionDecision | None:
    baseline = BaselineRef(rc_id=ctx.memory.baseline_rc_id(record))
    tables = await _assemble_val_tables(ctx, candidates, baseline.rc_id)
    write_val_summary(ctx, tables)
    await apply_integrity_checks(ctx, tables, baseline.rc_id)
    await _record_gate_blocks(ctx, candidates, tables)
    # Under --objective quality the gate times nothing. Each
    # row's passive `latency` and `cost` (the stored eval runs) show the
    # speed and the cost. There is no per-request ceiling.
    if is_efficiency(ctx.objective):
        # Holes (a failed fill, coverage under the floor) block without a
        # verdict, as under the quality objective: those candidates stay
        # pooled. Only a candidate judged on complete evidence is rejected.
        holed = {t.rc_id for t in tables if t.ineligible}
        await _speed_val_pass(ctx, tables, baseline.rc_id)
        weights = await eval_weights(ctx)
        for table in tables:
            apply_speed_eligibility(table, ctx.objective, weights)
        write_val_summary(ctx, tables)
        rule_decision = (
            decide_speed_gate(tables, ctx.objective, weights)
            if ctx.gate_agent == "auto"
            else None
        )
        # A winner with integrity flags goes to the promotion agent, whose
        # rationale must address them.
        if rule_decision is not None and not _flagged_winner(tables, rule_decision):
            decision = rule_decision
            ctx.log.event("promotion_rule", decision=decision, objective=ctx.objective)
            ctx.log.event(
                "promotion",
                winner_rc_id=decision.winner_rc_id,
                rationale=decision.rationale,
                decision=decision,
            )
            if decision.winner_rc_id is None:
                for rc_id in sorted(holed):
                    ctx.log.event(
                        "promotion_blocked",
                        winner_rc_id=rc_id,
                        reasons=["val evidence incomplete — stays pooled"],
                    )
                judged = [c for c in candidates if c.rc_id not in holed]
                await _record_rejections(ctx, judged, decision, baseline.rc_id)
                return decision
            if not await _apply_checked(ctx, decision, candidates, record, tables):
                return None
            await _record_rejections(ctx, candidates, decision, baseline.rc_id)
            return decision

    if (
        len(tables) == 1
        and ctx.gate_agent == "auto"
        and not is_efficiency(ctx.objective)
    ):
        # One candidate is a yes/no question the numbers already
        # answer — code decides by the written rule (`decide_single_candidate`)
        # instead of a ~$1, ~18-turn promote session that did the same
        # arithmetic. The agent is dispatched only when two or more
        # candidates must be RANKED against each other.
        issues = await ctx.memory.issues_by_eval()
        decision = decide_single_candidate(
            tables[0], issues, _target_eval_ids(candidates[0])
        )
        ctx.log.event(
            "promotion_rule",
            rc_id=tables[0].rc_id,
            decision=decision,
            skipped=tables[0].skipped,
        )
        if decision is None and (tables[0].ineligible or not tables[0].per_eval):
            reasons = [e.detail for e in tables[0].errors]
            ctx.log.event(
                "promotion_blocked", winner_rc_id=tables[0].rc_id, reasons=reasons
            )
            logger.warning(
                "promotion blocked: candidate %s has no usable val evidence — %s",
                tables[0].rc_id,
                "; ".join(reasons) or "no rows",
            )
            return None
        if decision is not None and _flagged_winner(tables, decision):
            # The numbers decide, but the winner carries integrity flags: a
            # judgment has to address them.
            logger.info(
                "promotion gate: %s carries integrity flags — dispatching promote_best",
                tables[0].rc_id,
            )
        elif decision is not None:
            ctx.log.event(
                "promotion",
                winner_rc_id=decision.winner_rc_id,
                rationale=decision.rationale,
                decision=decision,
            )
            if decision.winner_rc_id is None:
                await _record_rejections(ctx, candidates, decision, baseline.rc_id)
                return decision
            if not await _apply_checked(ctx, decision, candidates, record, tables):
                return None
            return decision
        # The numbers do not decide (a nominal, not-significant improvement
        # on the target, or the target was not compared): the agent weighs
        # the headline and the cost, as its skill says.
        logger.info(
            "promotion gate: code rule undecided for %s — dispatching promote_best",
            tables[0].rc_id,
        )

    decision_result = await call_with_retries(
        ctx.dispatch.promote_best,
        PromotionInput(
            candidates=tables,
            baseline=baseline,
            guidance=ctx.guidance,
            objective=ctx.objective,  # type: ignore[arg-type]
        ),
        backoffs=LOOP_DISPATCH_BACKOFFS,
        sleep=ctx.sleep,
        log=ctx.log,
        source="promote_best",
    )
    ctx.log.event(
        "spend",
        source="promote_best",
        lane=SUBSCRIPTION_LANE,
        cost_usd=decision_result.usage.cost_usd,
        unmeasured_tokens=decision_result.usage.unmeasured_tokens,
    )
    if decision_result.data is None:
        # Skip promotion — the candidates' wins stay pooled and
        # merge-eligible; a later wave's planner can merge or re-nominate.
        ctx.log.event(
            "promotion_skipped",
            reason=(
                decision_result.error.detail
                if decision_result.error
                else "promote_best returned no decision"
            ),
        )
        return None
    decision = decision_result.data
    ctx.log.event(
        "promotion",
        winner_rc_id=decision.winner_rc_id,
        rationale=decision.rationale,
        decision=decision,  # the full model — WaveStatus.promotion
    )
    if decision.winner_rc_id is None:
        # Zero winners is a valid outcome — no promotion writes, but every
        # candidate was still compared and passed over.
        await _record_rejections(ctx, candidates, decision, baseline.rc_id)
        return decision
    winner_table = next((t for t in tables if t.rc_id == decision.winner_rc_id), None)
    if winner_table is not None and winner_table.ineligible:
        # Machine-enforced floor: the agent picked a candidate whose val
        # table has holes (a fill failed, or coverage fell below the floor).
        # The gate may not ship on missing evidence — that could silently
        # change which config ships, in both
        # directions. No promotion, no rejection stamps: the candidates'
        # wins stay pooled and a later wave (with a `remeasure` top-up) can
        # re-nominate on complete evidence.
        reasons = [e.detail for e in winner_table.errors]
        ctx.log.event(
            "promotion_blocked",
            winner_rc_id=decision.winner_rc_id,
            reasons=reasons,
        )
        logger.warning(
            "promotion blocked: winner %s is ineligible on val coverage — %s",
            decision.winner_rc_id,
            "; ".join(reasons) or "no rows",
        )
        return None
    if (
        winner_table is not None
        and winner_table.integrity_flags
        and not rationale_addresses(decision.rationale)
    ):
        # Flags handed to the agent must be answered, not
        # ignored. No promotion; the candidates stay pooled.
        reasons = [
            "integrity flags not addressed in the rationale (an 'Integrity:' line)",
            *winner_table.integrity_flags,
        ]
        ctx.log.event(
            "promotion_blocked", winner_rc_id=decision.winner_rc_id, reasons=reasons
        )
        logger.warning(
            "promotion blocked: winner %s has unaddressed integrity flags",
            decision.winner_rc_id,
        )
        return None
    if not await _apply_checked(ctx, decision, candidates, record, tables):
        return None
    # AFTER the apply, and the order matters: a missing rejection stamp is
    # recoverable noise, a missing promotion is not. `baseline` is the
    # PRE-apply rc — what the candidates were actually measured against.
    await _record_rejections(ctx, candidates, decision, baseline.rc_id)
    return decision


# ---- Reward-hacking checks at the gate ----


async def _apply_checked(
    ctx: Ctx,
    decision: PromotionDecision,
    candidates: list[GateCandidate],
    record: MemoryRecord,
    tables: list[CandidateValTable],
) -> bool:
    """The one way a promotion is applied, whichever path
    decided it — the speed or cost code rule, the single-candidate rule, the
    promotion agent. Refused, and logged as `promotion_blocked`, unless code
    ran its integrity checks on the winner's table, nothing BLOCKED it, and
    any other flag is answered by an `Integrity:` line in the rationale.
    True when applied."""
    table = next((t for t in tables if t.rc_id == decision.winner_rc_id), None)
    reasons: list[str] = []
    if table is None or not table.integrity_checked:
        reasons.append("integrity checks did not run on the winner")
    elif table.ineligible:
        reasons.append("the winner is ineligible")
        reasons += [f for f in table.integrity_flags if f.startswith("BLOCKED:")]
    elif table.integrity_flags and not rationale_addresses(decision.rationale):
        reasons.append(
            "integrity flags not addressed in the rationale (an 'Integrity:' line)"
        )
        reasons += table.integrity_flags
    if reasons:
        ctx.log.event(
            "promotion_blocked", winner_rc_id=decision.winner_rc_id, reasons=reasons
        )
        logger.warning(
            "promotion blocked: winner %s — %s",
            decision.winner_rc_id,
            "; ".join(reasons),
        )
        return False
    await _apply_promotion(ctx, decision, candidates, record)
    return True


def _flagged_winner(
    tables: list[CandidateValTable], decision: PromotionDecision | None
) -> bool:
    if decision is None or decision.winner_rc_id is None:
        return False
    table = next((t for t in tables if t.rc_id == decision.winner_rc_id), None)
    return table is not None and bool(table.integrity_flags)


async def apply_integrity_checks(
    ctx: Ctx, tables: list[CandidateValTable], baseline_rc_id: str
) -> None:
    """Code's reward-hacking checks on every promotion candidate
    (`kiln_harness_tuner.integrity`): quoting the evals blocks (ineligible); an output
    shape shift or a new kind of write action on val is a flag for the
    promotion agent. Never fails the gate: an unreadable input skips that
    check and says so in the log."""
    if not tables:
        return
    rows = {str(r.get("id")): r for r in await task_run_configs(ctx)}
    baseline_prompt = await rc_prompt_text(ctx, rows.get(baseline_rc_id))
    if baseline_prompt is None:
        ctx.log.event(
            "integrity_input_unreadable",
            rc_id=baseline_rc_id,
            what="the baseline's instructions",
        )
    allowed = [baseline_prompt or ""]
    if baseline_prompt is None:
        for table in tables:
            table.integrity_flags.append(
                "unread: the baseline's instructions could not be read, so runs"
                " of words it shares with the evals were not excluded and the"
                " review had nothing to compare with"
            )
    allowed += await task_instructions(ctx)
    active = await _active_evals(ctx)
    # One reader with the build-time judge-copy check, so both
    # compare against the same eval text.
    evals = await read_eval_texts(ctx, active)
    for eval_id in evals.unread:
        ctx.log.event("integrity_input_unreadable", eval_id=eval_id, what="judge")
    eval_texts, names = evals.texts, evals.names
    allowed_text = "\n".join(allowed)
    baseline_text = allowed[0]
    for table in tables:
        read = await rc_prompt_text(ctx, rows.get(table.rc_id))
        if read is None:
            # Never a silent pass: the checks that need the
            # instructions did not run, and the winner must be read by the
            # promotion agent, whose rationale answers this flag.
            ctx.log.event(
                "integrity_input_unreadable",
                rc_id=table.rc_id,
                what="the candidate's instructions",
            )
            table.integrity_flags.append(
                "unread: the candidate's instructions could not be read, so the"
                " quote check and the review did not run — read them"
                " (get_prompt_text on its prompt id) before promoting it"
            )
        text = read or ""
        if read is not None and normalized(text) != normalized(baseline_text):
            # The changed instructions get one read-only review
            # session for paraphrased criteria and eval-specific gaming.
            # Flags, never a block (the verbatim block above stays).
            table.integrity_flags += await _integrity_review(
                ctx, table.rc_id, text, baseline_text, eval_texts
            )
        for finding in quote_findings(text, eval_texts, names, allowed_text):
            table.copy_findings.append(finding)
            table.integrity_flags.append(f"BLOCKED: {finding}")
            table.ineligible = True
            table.errors.append(
                ErrorInfo(
                    error_class="provider_error",
                    detail=f"integrity: the candidate's prompt {finding}",
                )
            )
        for eval_def, config_id in active:
            eval_id = str(eval_def["id"])
            name = str(eval_def.get("name") or eval_id)
            try:
                base_runs = await ctx.analyzer.stored_results(
                    eval_id, config_id, baseline_rc_id, "val"
                )
                cand_runs = await ctx.analyzer.stored_results(
                    eval_id, config_id, table.rc_id, "val"
                )
            except KilnError:
                continue
            table.integrity_flags += output_shape_findings(base_runs, cand_runs, name)
            table.integrity_flags += write_action_findings(base_runs, cand_runs, name)
        table.integrity_checked = True
        # One line per candidate, flags or not: a gate whose log
        # has no integrity line did not check.
        ctx.log.event(
            "integrity",
            rc_id=table.rc_id,
            flags=list(table.integrity_flags),
            line=f"integrity {table.rc_id}: "
            + ("; ".join(table.integrity_flags) or "no flag"),
        )


async def _record_gate_blocks(
    ctx: Ctx, candidates: list[GateCandidate], tables: list[CandidateValTable]
) -> None:
    """Make a BLOCK of integrity check (a) visible and usable.
    A blocked candidate must not leave only a `promotion_blocked` event in
    the wave log: with no printed reason and nothing on its record, the next
    planner would build on it. Per blocked candidate:

    - one printed `gate_block` line with the reason: the copied passage and
      the eval object;
    - its experiment record is stamped `— REJECTED AT VAL` (the run config
      is never resubmitted unchanged) and carries a `- blocked at the val
      gate …` line with the reasons;
    - the block joins `ctx.gate_blocks`, which every later planner of the
      run reads (`BatchPlanInput.gate_blocks`).

    Only the findings of check (a) travel this way: the copied phrase and
    the eval object, read from the prompt and the eval definitions. The
    planner is a train-side agent and the experiment record is
    train-readable, so nothing derived from val may reach them — not the
    (b) output-shape flags, not the (c) write-action flags, not the
    `integrity_review` findings, not a val number. Those stay in the wave
    log, the val summary and the promotion agent's input, as before.

    The writes are log-and-continue: they never fail the gate."""
    by_rc = {c.rc_id: c for c in candidates}
    known = {b.rc_id for b in ctx.gate_blocks}
    for table in tables:
        # Check (a) alone: val-free by construction. The (b) and (c) flags
        # and the review's findings come from val traces, so they never
        # reach the planner or this record (the split rule).
        reasons = list(table.copy_findings)
        if not reasons:
            continue
        candidate = by_rc.get(table.rc_id)
        exp_mem_id = candidate.experiment_memory_id if candidate else None
        block = GateBlock(
            rc_id=table.rc_id,
            experiment_memory_id=exp_mem_id,
            reasons=[f"the candidate's prompt {r}" for r in reasons],
        )
        ctx.log.event(
            "gate_block",
            rc_id=block.rc_id,
            exp_mem_id=exp_mem_id,
            reasons=list(block.reasons),
            line=f"gate block {block.rc_id}: integrity — " + "; ".join(block.reasons),
        )
        if block.rc_id not in known:
            ctx.gate_blocks.append(block)
            known.add(block.rc_id)
        if candidate is None:
            continue
        stamped = await _rejection_write(
            ctx,
            lambda i=candidate.experiment_memory_id: ctx.memory.stamp_rejected(i),
            "stamp",
            candidate,
            what="val-gate block",
        )
        noted = await _rejection_write(
            ctx,
            lambda i=candidate.experiment_memory_id, r=block.reasons: (
                ctx.memory.note_gate_block(i, r)
            ),
            "block note",
            candidate,
            what="val-gate block",
        )
        if stamped and noted:
            ctx.log.event(
                "gate_block_recorded",
                rc_id=block.rc_id,
                exp_mem_id=candidate.experiment_memory_id,
            )


async def _integrity_review(
    ctx: Ctx,
    rc_id: str,
    candidate_text: str,
    baseline_text: str,
    judge_texts: dict[str, str],
) -> list[str]:
    """One `integrity_review` session for a candidate whose instructions
    changed: its flags, prefixed "review:". A session that returns nothing
    is itself a flag, so the promotion agent reads the prompt instead."""
    result = await ctx.dispatch.integrity_review(
        IntegrityReviewInput(
            candidate_rc_id=rc_id,
            candidate_instructions=candidate_text,
            baseline_instructions=baseline_text,
            judge_texts=dict(judge_texts),
        )
    )
    ctx.log.event(
        "spend",
        source="integrity_review",
        lane=SUBSCRIPTION_LANE,
        cost_usd=result.usage.cost_usd,
        unmeasured_tokens=result.usage.unmeasured_tokens,
    )
    if result.data is None:
        why = result.error.detail if result.error else "no answer"
        return [
            f"review: the integrity review did not run ({why[:160]}) — read the"
            " candidate's changed instructions against the judges yourself"
        ]
    return [f"review: {flag}" for flag in result.data.flags if flag.strip()]


# ---- The speed objective's gate ----

# Two candidates whose val median ratios lie within this of each other are
# equally fast; quality wins break the tie.
SPEED_TIE_RATIO = 0.03


def write_val_summary(ctx: Ctx, tables: list[CandidateValTable]) -> None:
    """The gate's code-assembled val tables, for the val readers' gateway
    operation `get_val_summary`: the only val view an agent has.
    A file in the run directory, which agents cannot read directly."""
    if ctx.run_dir is None:
        return
    data = {
        "candidates": [t.model_dump(mode="json") for t in tables],
        "note": "the latest promotion gate's tables, candidate vs baseline on val",
    }
    (ctx.run_dir / VAL_SUMMARY_FILE).write_text(json.dumps(data, indent=1) + "\n")


async def _speed_val_pass(
    ctx: Ctx, tables: list[CandidateValTable], baseline_rc_id: str
) -> PairedPass | None:
    """The paired drive pass on a fixed val sample: the baseline and every
    candidate, the same items, the same window (`latency.paired_drive_pass`).
    Code reads the val inputs from disk; no agent sees them. The result lands
    on each table's `latency`; its cost is provider spend."""
    inputs = sample_inputs(
        split_inputs(ctx.target.project_path, ctx.target.task_id, "val")
    )
    if not inputs:
        for table in tables:
            table.errors.append(
                ErrorInfo(
                    error_class="provider_error",
                    detail="speed: no drivable val input — val latency unmeasured",
                )
            )
        return None
    paired = await paired_drive_pass(
        ctx, baseline_rc_id, [t.rc_id for t in tables], inputs, "val", "gate"
    )
    ctx.log.event(
        "spend",
        source="latency_drives",
        lane=PROVIDER_LANE,
        cost_usd=paired.spend,
        evidence_cost_usd=paired.spend,
    )
    for table in tables:
        table.latency = paired.latency.get(table.rc_id)
        table.cost = paired.cost.get(table.rc_id)
        ctx.log.event(
            "speed_result",
            rc_id=table.rc_id,
            latency=table.latency.model_dump() if table.latency else None,
            cost=table.cost.model_dump() if table.cost else None,
        )
        logger.info("%s", latency_line(f"val speed {table.rc_id}", table.latency))
        logger.info("%s", latency_line(f"val cost {table.rc_id}", table.cost))
    return paired


async def eval_weights(ctx: Ctx) -> dict[str, float]:
    """eval id -> its pooled-net weight (`analysis.priority_weight`) from its
    spec's priority. An eval without a spec, or a spec without a priority,
    is not named and weighs `UNPRIORITISED_WEIGHT`. An unreadable spec list
    weighs every eval the same — never a failure."""
    base = f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}"
    try:
        specs = await ctx.kiln.call("GET", f"{base}/specs") or []
    except KilnError:
        return {}
    return {
        str(s["eval_id"]): priority_weight(s.get("priority"))
        for s in specs
        if isinstance(s, dict) and s.get("eval_id") and s.get("priority") is not None
    }


def apply_speed_eligibility(
    table: CandidateValTable,
    objective: str = "speed",
    weights: dict[str, float] | None = None,
) -> None:
    """Under an efficiency objective a candidate may win only when it is
    clearly better on val on the objective's metric (latency for speed, cost
    per request for cost) AND its val quality is equal or better
    (`analysis.speed_quality_reasons`: nothing significant down, net items
    pooled over every row >= 0, no row over its noise allowance, every row
    poolable). Enforced by marking the table ineligible — the same flag the
    gate already refuses to promote, whoever decides."""
    reasons: list[str] = []
    better = better_word(objective)
    comparison = decisive(table, objective)
    if comparison is None:
        reasons.append(f"{objective}: no val comparison — unmeasured")
    elif not comparison.clearly_faster:
        reasons.append(f"{objective}: not clearly {better} on val — {comparison.note}")
    reasons += speed_quality_reasons(table.per_eval, weights)
    if reasons:
        table.ineligible = True
        table.errors.extend(
            ErrorInfo(error_class="provider_error", detail=r) for r in reasons
        )


def pooled_net(
    table: CandidateValTable, weights: dict[str, float] | None = None
) -> float:
    """The table's net item-equivalents pooled over its rows, each weighted
    by its eval's priority (the same sum the equal-or-better rule reads)."""
    from kiln_harness_tuner.analysis import UNPRIORITISED_WEIGHT

    weights = weights or {}
    return sum(
        weights.get(r.eval_id, UNPRIORITISED_WEIGHT) * (row_net_items(r) or 0.0)
        for r in table.per_eval
    )


def decide_speed_gate(
    tables: list[CandidateValTable],
    objective: str = "speed",
    weights: dict[str, float] | None = None,
) -> PromotionDecision:
    """The code rule under an efficiency objective: among the eligible
    candidates, the best on the objective's metric (lowest val median ratio)
    wins; candidates within `SPEED_TIE_RATIO` of it are tied, and among those
    the higher pooled net wins (the net, not a count of
    significant rows — two small gains outweigh none), then the lower p90
    ratio."""
    better = better_word(objective)

    def comp(t: CandidateValTable) -> LatencyComparison | None:
        return decisive(t, objective)

    eligible = [
        t
        for t in tables
        if not t.ineligible and comp(t) is not None and comp(t).median_ratio  # type: ignore[union-attr]
    ]

    if not eligible:
        return PromotionDecision(
            winner_rc_id=None,
            winner_experiment_memory_id=None,
            rationale=f"code rule ({objective}): no winner — no candidate is"
            f" clearly {better} on val with quality equal or better. "
            + " | ".join(
                f"{t.rc_id}: {'; '.join(e.detail for e in t.errors) or 'n/a'}"
                for t in tables
            ),
        )
    best = min(comp(t).median_ratio for t in eligible)  # type: ignore[union-attr,type-var]
    tied = [
        t
        for t in eligible
        if comp(t).median_ratio <= best + SPEED_TIE_RATIO  # type: ignore[union-attr,operator]
    ]

    winner = sorted(
        tied,
        key=lambda t: (
            -round(pooled_net(t, weights), 6),
            comp(t).p90_ratio if comp(t) and comp(t).p90_ratio else 9.9,  # type: ignore[union-attr]
            t.rc_id,
        ),
    )[0]
    rows = "; ".join(
        f"{r.eval_id}/{r.score_key} {r.delta:+.3f}"
        f" ({row_net_items(r) or 0.0:+.1f} items of {r.n_items})"
        for r in winner.per_eval
    )
    net = sum(row_net_items(r) or 0.0 for r in winner.per_eval)
    return PromotionDecision(
        winner_rc_id=winner.rc_id,
        winner_experiment_memory_id=winner.experiment_memory_id,
        rationale=(
            f"code rule ({objective}): promote — clearly {better} on val, quality"
            f" equal or better ({net:+.1f} net items pooled, no eval over its"
            " noise allowance, none down significantly)."
            f" {latency_line('speed ' + winner.rc_id, winner.latency)}."
            f" {latency_line('cost ' + winner.rc_id, winner.cost)}."
            f" Quality rows: {rows}."
            + (
                f" Tied within {SPEED_TIE_RATIO} with"
                f" {', '.join(t.rc_id for t in tied if t is not winner)};"
                " the higher pooled net broke the tie."
                if len(tied) > 1
                else ""
            )
        ),
        regressed_issues=[],
    )


def decide_single_candidate(
    table: CandidateValTable,
    issues_by_eval: dict[str, list[str]],
    target_eval_ids: Iterable[str] = (),
) -> PromotionDecision | None:
    """The code rule for a one-candidate gate.

    `target_eval_ids` names the evals the experiment set out to move (its
    `evaluation_target`); every other row is a guard. Returns None when the
    rule does NOT decide — the table is ineligible (a fill failed, coverage
    sits below the floor, no rows), no targeted row was compared, or the
    targeted improvement is nominal only. The caller blocks an ineligible
    candidate and hands the rest to the promote agent. Otherwise:

    - any row, target or guard, regressed beyond doubt (CI entirely below
      zero) → no winner, the candidate is rejected at val;
    - no targeted row improved at all (delta <= 0 on every one) → no winner:
      the train win did not replicate on val;
    - at least one targeted row improved beyond doubt (`significant`, or a
      CI entirely above zero) and nothing regressed beyond doubt → promote.
      A positive delta with a CI straddling zero is what a no-op change
      shows at n=25 most of the time, so it never promotes by itself — the
      agent weighs that case with the headline and the cost, as its skill
      says.
    - rows that fell nominally (delta < 0, CI straddling zero) become the
      metric-free `regressed_issues` flags via the issue↔eval binding, so a
      FIXED issue that slipped does not keep reading as fixed.
    The mean is never taken across rows: a `five_star` guard moving 0.6 and
    a `pass_fail` target falling 0.04 are not the same unit, and the old
    mean let the guard outvote the target.
    The rationale is code-composed and carries the numbers the `val` record
    is allowed to hold."""
    if table.ineligible or not table.per_eval:
        return None
    rows = table.per_eval
    targets = set(target_eval_ids)
    targeted = [r for r in rows if r.eval_id in targets] if targets else list(rows)
    if not targeted:
        return None  # the target was not compared — nothing to decide on

    def label(r) -> str:
        return f"{r.eval_id}/{r.score_key}"

    def fmt(r) -> str:
        ci = (
            f", CI {r.ci_low:+.3f}..{r.ci_high:+.3f}"
            if r.ci_low is not None and r.ci_high is not None
            else ""
        )
        p = f", p={r.p_value:.3g}" if r.p_value is not None else ""
        return f"{label(r)} {r.delta:+.3f} (n={r.n_items}{p}{ci})"

    proven_regressions = [r for r in rows if r.ci_high is not None and r.ci_high < 0]
    improved = [r for r in targeted if r.delta > 0]
    proven_wins = [
        r for r in improved if r.significant or (r.ci_low is not None and r.ci_low > 0)
    ]
    fell = [r for r in rows if r.delta < 0]
    summary = "; ".join(fmt(r) for r in rows)
    skipped = f" Not compared: {', '.join(table.skipped)}." if table.skipped else ""
    if proven_regressions:
        return PromotionDecision(
            winner_rc_id=None,
            winner_experiment_memory_id=None,
            rationale=(
                f"code rule: rejected — regressed beyond doubt on "
                f"{', '.join(label(r) for r in proven_regressions)}. Rows: {summary}."
                + skipped
            ),
            regressed_issues=[],
        )
    if not improved:
        return PromotionDecision(
            winner_rc_id=None,
            winner_experiment_memory_id=None,
            rationale=(
                f"code rule: no winner — no targeted row improved on val "
                f"({', '.join(label(r) for r in targeted)}). Rows: {summary}." + skipped
            ),
            regressed_issues=[],
        )
    if not proven_wins:
        return None  # nominal only: the agent decides
    regressed = [
        RegressedIssue(
            issue_memory_id=issue_id,
            note=f"nominal val slip on {label(r)}: {r.delta:+.3f}",
        )
        for r in fell
        for issue_id in issues_by_eval.get(r.eval_id, [])
    ]
    return PromotionDecision(
        winner_rc_id=table.rc_id,
        winner_experiment_memory_id=table.experiment_memory_id,
        rationale=(
            f"code rule: promote — improved beyond doubt on "
            f"{', '.join(label(r) for r in proven_wins)}, {len(fell)} row(s) "
            f"down nominally, no row regressed beyond doubt. Rows: {summary}." + skipped
        ),
        regressed_issues=regressed,
    )


async def _assemble_val_tables(
    ctx: Ctx, candidates: list[GateCandidate], baseline_rc_id: str
) -> list[CandidateValTable]:
    """(code) Full val pass per candidate rc — every active eval, split=val —
    plus the current baseline's val fill (both cached). This is the
    ONLY place the loop reads val: it runs after the analyzer, so no
    memory-writing context ever sees val data.

    Concurrency: ALL (eval × rc) fills — baseline +
    candidates — fire in one gather; comparison/stats per eval stay
    sequential after its own jobs land, assembled in (eval, candidate) order
    (result-equivalent to a nested loop). A partial fill
    returns rows + health instead of raising, so a flaky baseline does not
    drop the eval for every candidate — rows pair on the intersection and
    carry the coverage fields.

    Errors + the coverage floor: a
    failed fill must not drop its eval from the gate SILENTLY (the rationale
    could still say "no regressions"). Every
    hole lands on the candidate's `errors`, and any active eval whose
    fill failed (either side) or whose stored coverage sits below
    `ctx.coverage_floor` marks the candidate `ineligible` — enforced by
    `_run_promotion_gate` regardless of the agent's decision."""
    rows_by_rc: dict[str, list] = {o.rc_id: [] for o in candidates}
    errors_by_rc: dict[str, list[ErrorInfo]] = {o.rc_id: [] for o in candidates}
    ineligible: dict[str, bool] = {o.rc_id: False for o in candidates}
    gate_evals: list[tuple[str, str, list[ScoreSpec]]] = []
    # Gate isolation: an eval the gate cannot compare is SKIPPED
    # with its reason on every candidate's table, never a crash — a crash
    # would land AFTER every val fill was paid for.
    skipped: list[str] = []
    for eval_def, eval_config_id in await _active_evals(ctx):
        eval_id = eval_def["id"]
        try:
            specs = expand_targets(eval_def, EvalTarget(eval_id=eval_id))
        except TargetExpansionError:
            continue  # all-informational eval — nothing directional to gate on
        except DirectionFlagMissingError as exc:
            skipped.append(f"{eval_id}: {exc}")
            continue
        comparable = [
            s for s in specs if s.type in BINARY_SCORE_TYPES | RATING_SCORE_TYPES
        ]
        for spec in specs:
            if spec not in comparable:
                skipped.append(
                    f"{eval_id}/{spec.key}: score type {spec.type!r} has no "
                    "statistics operation"
                )
        if not comparable:
            continue
        gate_evals.append((eval_id, eval_config_id, comparable))
    if skipped:
        ctx.log.event("gate_evals_skipped", skipped=skipped)
        logger.warning("gate: not comparing %s", "; ".join(skipped))

    rc_ids = [baseline_rc_id, *(o.rc_id for o in candidates)]
    fill_keys = [
        (eval_id, eval_config_id, rc_id)
        for eval_id, eval_config_id, _specs in gate_evals
        for rc_id in rc_ids
    ]

    async def fill(
        eval_id: str, eval_config_id: str, rc_id: str
    ) -> tuple[list[dict[str, Any]], JobHealth] | ErrorInfo:
        try:
            return await ctx.analyzer.ensure_measured(
                eval_id, eval_config_id, rc_id, "val"
            )
        except MeasurementFailed as fail:
            ctx.log.event(
                "val_fill_failed",
                eval_id=eval_id,
                rc_id=rc_id,
                error_class=fail.error.error_class,
                detail=fail.error.detail,
            )
            logger.warning(
                "val fill failed: eval %s rc %s — %s", eval_id, rc_id, fail.error.detail
            )
            return fail.error

    # Hard errors (no-degradation rows — e.g. the 422 missing-split
    # answer) cancel the sibling fills and raise (running them to completion
    # would spend on a run that had already decided to exit).
    fill_tasks = [asyncio.ensure_future(fill(*key)) for key in fill_keys]
    error, _pending = await first_error_or_done(fill_tasks)
    if error is not None:
        raise error
    fills: dict[tuple[str, str], tuple[list[dict[str, Any]], JobHealth]] = {}
    fill_errors: dict[tuple[str, str], ErrorInfo] = {}
    for (eval_id, _eval_config_id, rc_id), task in zip(
        fill_keys, fill_tasks, strict=True
    ):
        result = task.result()
        if isinstance(result, ErrorInfo):
            fill_errors[(eval_id, rc_id)] = result
        else:
            fills[(eval_id, rc_id)] = result

    def _fill_hole(
        rc_id: str, eval_id: str, side: str, missing_key: tuple[str, str]
    ) -> None:
        err = fill_errors.get(missing_key)
        detail = f"{side} val fill failed for eval {eval_id} — absent from this gate"
        if err is not None:
            detail += f": {err.detail}"
        errors_by_rc[rc_id].append(
            ErrorInfo(
                error_class=err.error_class if err else "provider_error",
                detail=detail,
            )
        )
        ineligible[rc_id] = True

    # One progress line per candidate per eval, the shape of
    # the measurement engine's chunk lines: the val gate is a census, so it
    # never stops early, but it reports the same way.
    tally: dict[str, list[int]] = {o.rc_id: [0, 0, 0] for o in candidates}

    def _gate_line(rc_id: str, k: int, eval_id: str, rows: list) -> None:
        t = tally[rc_id]
        for row in rows:
            t[0] += row.n_items
            t[1] += row.fail_to_pass or 0
            t[2] += row.pass_to_fail or 0
        state = "done" if k == len(gate_evals) else "continue"
        ctx.log.event(
            "gate_progress",
            rc_id=rc_id,
            eval_id=eval_id,
            line=f"gate {rc_id}: eval {k}/{len(gate_evals)} · {t[0]} judged ·"
            f" +{t[1]}/−{t[2]} vs baseline · {state}",
        )

    for k, (eval_id, _eval_config_id, specs) in enumerate(gate_evals, start=1):
        baseline_fill = fills.get((eval_id, baseline_rc_id))
        for outcome in candidates:
            candidate_fill = fills.get((eval_id, outcome.rc_id))
            if candidate_fill is None:
                _fill_hole(
                    outcome.rc_id, eval_id, "candidate", (eval_id, outcome.rc_id)
                )
                continue
            if baseline_fill is None:
                _fill_hole(
                    outcome.rc_id, eval_id, "baseline", (eval_id, baseline_rc_id)
                )
                continue
            baseline_runs, baseline_health = baseline_fill
            candidate_runs, candidate_health = candidate_fill
            # compared_to="prior_baseline": the reference is the promotion-
            # lineage head (what the candidate would replace), not the item's
            # parent — the enum's closest semantic.
            try:
                eval_rows, holes = await ctx.analyzer.compare_rows(
                    eval_id=eval_id,
                    specs=specs,
                    candidate_runs=candidate_runs,
                    baseline_runs=baseline_runs,
                    baseline_rc_id=baseline_rc_id,
                    compared_to="prior_baseline",
                    split="val",
                    candidate_health=candidate_health,
                    baseline_health=baseline_health,
                )
            except AnalysisError as exc:
                # A comparison that fails AFTER the fill is a hole in this
                # candidate's evidence, not a run-ending answer.
                errors_by_rc[outcome.rc_id].append(
                    ErrorInfo(
                        error_class="provider_error",
                        detail=f"eval {eval_id}: comparison failed — {exc}",
                    )
                )
                ineligible[outcome.rc_id] = True
                continue
            rows_by_rc[outcome.rc_id].extend(eval_rows)
            _gate_line(outcome.rc_id, k, eval_id, eval_rows)
            # Score pairs with nothing to pair are recorded as holes
            # (`_holes`), not discarded: a shorter table could be read
            # as "no regressions".
            for hole in holes:
                errors_by_rc[outcome.rc_id].append(hole)
                ineligible[outcome.rc_id] = True
            # The floor on the PAIR: two sides each missing a
            # different item both pass a per-side floor while the pair sits
            # lower — the row's own `coverage` is the number that matters.
            for row in eval_rows:
                if row.coverage is not None and row.coverage < ctx.coverage_floor:
                    errors_by_rc[outcome.rc_id].append(
                        ErrorInfo(
                            error_class="provider_error",
                            detail=(
                                f"eval {eval_id} {row.score_key}: paired val "
                                f"coverage {row.coverage:.0%} is below the "
                                f"{ctx.coverage_floor:.0%} floor"
                            ),
                        )
                    )
                    ineligible[outcome.rc_id] = True
            for side, side_runs, side_health, other_runs in (
                ("candidate", candidate_runs, candidate_health, baseline_runs),
                ("baseline", baseline_runs, baseline_health, candidate_runs),
            ):
                shortfall = _coverage_shortfall(
                    side_runs,
                    side_health,
                    ctx.coverage_floor,
                    fallback_total=pairable_item_count(other_runs),
                )
                if shortfall is not None:
                    stored, total = shortfall
                    errors_by_rc[outcome.rc_id].append(
                        ErrorInfo(
                            error_class="provider_error",
                            detail=(
                                f"eval {eval_id} {side}-side val coverage "
                                f"{stored}/{total} is below the "
                                f"{ctx.coverage_floor:.0%} floor"
                            ),
                        )
                    )
                    ineligible[outcome.rc_id] = True
    for o in candidates:
        if ineligible[o.rc_id]:
            reasons = [e.detail for e in errors_by_rc[o.rc_id]]
            ctx.log.event("gate_candidate_ineligible", rc_id=o.rc_id, reasons=reasons)
            logger.warning(
                "gate: candidate %s ineligible — %s", o.rc_id, "; ".join(reasons)
            )
    # The val pass is real measurement spend — potentially the run's largest
    # — and must reach the spend accounting like every other cost.
    # Row spend covers candidate + baseline-fill records (compare_rows splits
    # the eval's total across its rows, so summing rows recovers it).
    ctx.log.event(
        "spend",
        source="val_pass",
        lane=PROVIDER_LANE,
        cost_usd=sum(row.spend_usd for rows in rows_by_rc.values() for row in rows),
        evidence_cost_usd=sum(
            row.evidence_spend_usd for rows in rows_by_rc.values() for row in rows
        ),
    )
    return [
        CandidateValTable(
            rc_id=o.rc_id,
            experiment_memory_id=o.experiment_memory_id,
            per_eval=rows_by_rc[o.rc_id],
            errors=errors_by_rc[o.rc_id],
            ineligible=ineligible[o.rc_id],
            skipped=list(skipped),
        )
        for o in candidates
    ]


def _coverage_shortfall(
    runs: list[dict[str, Any]],
    health: JobHealth,
    floor: float,
    fallback_total: int = 0,
) -> tuple[int, int] | None:
    """(stored, expected) when a measured side's coverage sits below the
    floor; None when at/above it or when no expectation is known at all.
    A side with no job total (a free cache read, or a job that stored zero
    rows) takes the OTHER side's stored item count as its expectation,
    so such a side is not exempt from the floor. A side
    whose job DID report a total keeps it: a 10-row job reads 10/10 here, and
    the pair-level `row.coverage` check above is what catches a 10-of-25
    pairing against a fuller other side."""
    total = health.n_expected or fallback_total
    if not total:
        return None
    stored = pairable_item_count(runs)
    if stored / total < floor:
        return stored, total
    return None


async def _active_evals(ctx: Ctx) -> list[tuple[dict[str, Any], str]]:
    """The active set, pinned: an eval with a spec is active iff the
    spec's status is "active"; no default eval config → non-active (each eval
    always runs its default config, never loop-chosen). An eval Kiln couldn't
    load at setup is left out for the whole run."""
    base = f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}"
    # Same envelope the setup read handles, and the same reason to go through
    # one reader: an unwrapped body here is a dict, and iterating it yields its
    # KEYS — so every wave would silently see an empty active set.
    eval_rows = await ctx.kiln.call("GET", f"{base}/evals")
    evals, eval_load_errors = eval_rows_from_response(f"{base}/evals", eval_rows)
    if eval_load_errors:
        logger.warning(
            "%s: the server could not read %d eval(s); this wave measures without them",
            f"{base}/evals",
            eval_load_errors,
        )
    # A reshaped body must fail, not read as "no specs": that would count
    # every archived eval as active.
    specs = _require_rows(f"{base}/specs", await ctx.kiln.call("GET", f"{base}/specs"))
    return [
        (eval_def, config_id)
        for eval_def, config_id in measurable_evals(evals, specs)
        if str(eval_def["id"]) not in ctx.unloadable_evals
        and not _new_eval_refused(ctx, str(eval_def["id"]))
    ]


def _new_eval_refused(ctx: Ctx, eval_id: str) -> bool:
    """True for an eval that appeared after start-up whose splits could
    reach held-out items (checked once, logged once)."""
    if ctx.startup_eval_ids is None or eval_id in ctx.startup_eval_ids:
        return False
    if eval_id not in ctx.checked_evals:
        project_root = Path(ctx.target.project_path).expanduser().resolve()
        problem = new_eval_split_problem(
            project_root, ctx.target.task_id, eval_id, ctx.analyzer.heldout_ids
        )
        ctx.checked_evals[eval_id] = problem
        if problem is not None:
            ctx.log.event(
                "eval_split_refused",
                eval_id=eval_id,
                line=(
                    f"eval {eval_id} is not measured: {problem} — an eval created"
                    " after start-up may not reach val or test items"
                ),
            )
    return ctx.checked_evals[eval_id] is not None


async def _eval_train_sizes(ctx: Ctx, baseline_rc_id: str) -> dict[str, int]:
    sizes, _latency, _rows = await _eval_train_sizes_and_latency(ctx, baseline_rc_id)
    return sizes


async def _eval_train_sizes_and_latency(
    ctx: Ctx, baseline_rc_id: str
) -> tuple[dict[str, int], LatencyComparison | None, list[TrainDetectability]]:
    """Measurable train items per active eval, for `BatchPlanInput.eval_sizes`.
    Counted from the baseline's STORED train runs rather than the
    dataset's nominal size, because stored runs are what a comparison can
    actually pair, and a nominal count overstates exactly where coverage holes
    make an undetectable bar most likely. Counted through
    `pairable_item_count`, not `len`, for the same reason one step down: the
    pairing is by item id, so duplicate records collapse and item-less records
    drop — a raw record count re-introduces the overstatement in the same
    partial-coverage case. Refetched every wave — an `eval_only` item can
    build a new eval mid-run.

    Advisory input, so no read here may cost a wave: an eval whose count cannot
    be fetched is simply absent from the map (never zero, which would read as a
    measured emptiness the planner should act on). `KilnError` covers both HTTP
    and transport failures — the no-degradation rule is about measurement,
    and if Kiln is genuinely unreachable the very next call (the plan dispatch's
    own reads) surfaces it with a better message than a count read can."""
    try:
        active = await _active_evals(ctx)
    except KilnError:
        ctx.log.event("eval_sizes_unavailable")
        return {}, None, []
    sizes: dict[str, int] = {}
    latencies: dict[str, float] = {}
    detect: list[TrainDetectability] = []
    for eval_def, eval_config_id in active:
        eval_id = eval_def["id"]
        try:
            runs = await ctx.analyzer.stored_results(
                eval_id, eval_config_id, baseline_rc_id, "train"
            )
        except KilnError:
            ctx.log.event("eval_size_unavailable", eval_id=eval_id)
            continue
        sizes[eval_id] = pairable_item_count(runs)
        latencies.update({f"{eval_id}:{k}": v for k, v in item_latencies(runs).items()})
        # What these same TRAIN rows can show, per score — the
        # planner's `train_detectability` (`kiln_harness_tuner.detectability`).
        detect += await train_detectability(
            ctx,
            baseline_rc_id,
            [(eval_def, eval_config_id)],
            ctx.train_split_sizes,
            runs=runs,
        )
    return sizes, baseline_latency_summary(latencies), detect


def baseline_latency_summary(latencies: dict[str, float]) -> LatencyComparison | None:
    """Where the baseline stands on its stored train drives: median, p90 and
    spread over its items. The `candidate_*` fields stay unset — this is one
    arm, not a comparison."""
    return single_arm(
        latencies,
        method="eval_runs",
        split="train",
        note="the baseline alone, from its stored train eval runs",
    )


# ---- Measure the baseline before wave 1 ----


def _train_split_sizes(ctx: Ctx) -> dict[str, int]:
    """eval id -> train split size, from the project on disk (the
    orchestrator's read). Empty when the project cannot be read — then only
    an eval with no stored train row at all is measured."""
    try:
        sizes: dict[str, int] = {}
        for task_dir in task_dirs(Path(ctx.target.project_path), ctx.target.task_id):
            member, _loaded = membership(task_dir)
            for eval_id, splits in member.by_eval.items():
                sizes[eval_id] = len(splits["train"])
        return sizes
    except (QuarantineError, OSError):
        return {}


def baseline_is_covered(have: int, want: int | None, floor: float) -> bool:
    """Do `have` stored train rows cover a split of `want` items? Unknown
    size: any row counts. Known: at least `floor` of it, and at least one."""
    if want is None:
        return have > 0
    return have > 0 and have >= math.ceil(floor * want)


async def prepare_baseline(ctx: Ctx, rc_id: str) -> None:
    """Before wave 1: the baseline measured on train on every active eval
    (only where its stored rows fall short of the split — Kiln's item cache
    runs just the missing items), and under `speed` or `cost` its judge-free
    timing on the fixed speed sample. Wave 1's planner then plans on eval
    sizes and, under speed or cost, on the baseline's own median and p90. It is the
    measurement the first arm would otherwise pay for its parent, paid once and up
    front, in every objective: the quality planner needs the eval sizes
    too.

    The train measurement and the timing pass run CONCURRENTLY:
    the measurement takes most of setup's wall-clock, and the timing is only
    drives. The baseline's timing is therefore taken under the load of its
    own eval jobs; it is the planner's orientation, never a verdict — every
    comparison that decides is paired in one window later. The screening
    pool needs the measured rows, so it is drawn after both."""
    if not ctx.measure_baseline_first:
        return
    if is_efficiency(ctx.objective):
        await asyncio.gather(
            _measure_baseline_train(ctx, rc_id),
            ensure_baseline_speed(ctx, rc_id),
        )
    else:
        await _measure_baseline_train(ctx, rc_id)


async def _baseline_progress(ctx: Ctx, rc_id: str, done: asyncio.Event) -> None:
    """Every `progress_interval_secs` while the baseline is measured, one
    line: items done of total per eval, from the running jobs' own progress
    ("baseline measuring: Strict Count 40/120, Tone 12/30 (1 errored) —
    2 job(s) running"). Never fails the measurement."""
    while True:
        try:
            await asyncio.wait_for(done.wait(), timeout=ctx.progress_interval_secs)
            return
        except TimeoutError:
            pass
        jobs = {
            job_id: eval_id
            for job_id, (eval_id, job_rc, split) in dict(
                ctx.analyzer.running_jobs
            ).items()
            if job_rc == rc_id and split == "train"
        }
        if not jobs:
            continue
        parts = []
        for job_id, eval_id in sorted(jobs.items(), key=lambda kv: kv[1]):
            try:
                record = await ctx.kiln.call("GET", f"/api/jobs/{job_id}")
                name = await _eval_name(ctx, eval_id)
            except Exception:  # a progress line is information only
                continue
            progress = (record or {}).get("progress") or {}
            ok = int(progress.get("success") or 0)
            bad = int(progress.get("error") or 0)
            total = progress.get("total")
            part = f"{name} {ok + bad}/{total if total is not None else '?'}"
            parts.append(part + (f" ({bad} errored)" if bad else ""))
        if parts:
            ctx.log.event(
                "baseline_progress",
                rc_id=rc_id,
                line=(
                    "baseline measuring: "
                    + ", ".join(parts)
                    + f" — {len(jobs)} job(s) running"
                ),
            )


async def _eval_name(ctx: Ctx, eval_id: str) -> str:
    try:
        return str((await ctx.analyzer.fetch_eval(eval_id)).get("name") or eval_id)
    except Exception:
        return eval_id


async def _measure_baseline_train(ctx: Ctx, rc_id: str) -> None:
    """The baseline's train measurement at setup, logged as one line."""
    started = ctx.clock()
    ctx.log.event(
        "phase_start",
        name="baseline_measure",
        label="Setup: measuring the baseline on train…",
    )
    try:
        expected = _train_split_sizes(ctx)
        active = await _active_evals(ctx)

        async def one(eval_def: dict[str, Any], config_id: str):
            eval_id = str(eval_def["id"])
            want = expected.get(eval_id)
            try:
                stored = await ctx.analyzer.stored_results(
                    eval_id, config_id, rc_id, "train"
                )
            except KilnError:
                stored = []
            have = pairable_item_count(stored)
            if baseline_is_covered(have, want, ctx.coverage_floor):
                return eval_id, have, want, "stored", 0.0
            try:
                runs, health = await ctx.analyzer.ensure_measured(
                    eval_id, config_id, rc_id, "train"
                )
            except MeasurementFailed as fail:
                return eval_id, have, want, f"failed ({fail.error.detail[:80]})", 0.0
            return (
                eval_id,
                pairable_item_count(runs),
                want if want is not None else health.n_expected,
                "measured",
                ctx.analyzer.fresh_runs_cost(runs),
            )

        done = asyncio.Event()
        reporter = asyncio.create_task(_baseline_progress(ctx, rc_id, done))
        try:
            results = await asyncio.gather(*(one(e, c) for e, c in active))
        finally:
            done.set()
            await reporter
    finally:
        ctx.log.event(
            "phase_end", name="baseline_measure", elapsed_secs=ctx.clock() - started
        )
    cost = sum(r[4] for r in results)
    ctx.log.event(
        "spend",
        source="baseline_measure",
        lane=PROVIDER_LANE,
        cost_usd=cost,
        evidence_cost_usd=cost,
    )
    parts = [
        f"{eid} {have}/{want if want is not None else '?'}"
        + ("" if how == "stored" else f" {how}")
        for eid, have, want, how, _c in results
    ]
    line = (
        f"baseline measured on train ({rc_id}): "
        + (", ".join(parts) if parts else "no active eval")
        + f" — ${cost:.2f} this run"
    )
    ctx.log.event(
        "baseline_measured",
        rc_id=rc_id,
        line=line,
        evals={r[0]: {"stored": r[1], "expected": r[2], "how": r[3]} for r in results},
        cost_usd=cost,
    )
    logger.info("%s", line)


async def ensure_baseline_speed(ctx: Ctx, rc_id: str) -> LatencyComparison | None:
    """The baseline's judge-free timing on the fixed speed sample of train
    items — once per baseline rc, reused every wave. Logged as one line."""
    if rc_id in ctx.baseline_speed:
        return ctx.baseline_speed[rc_id]
    try:
        inputs = sample_inputs(
            split_inputs(ctx.target.project_path, ctx.target.task_id, "train")
        )
    except (QuarantineError, OSError):
        inputs = []
    summary: LatencyComparison | None = None
    if inputs:
        drives = await timed_drives(ctx, [rc_id], inputs, "baseline")
        ctx.log.event(
            "spend",
            source="latency_drives",
            lane=PROVIDER_LANE,
            cost_usd=drives.cost,
            evidence_cost_usd=drives.cost,
        )
        summary = single_arm(
            drives.latencies[rc_id],
            method="paired_drive",
            split="train",
            note="the baseline alone, driven judge-free on the fixed speed sample",
        )
        ctx.baseline_cost[rc_id] = single_arm(
            (drives.costs or {}).get(rc_id, {}),
            method="paired_drive",
            split="train",
            note="the baseline alone: per-request cost of the task call",
        )
        if ctx.baseline_cost[rc_id] is not None:
            ctx.baseline_cost[rc_id].metric = "cost_usd"  # type: ignore[union-attr]
        n = len(drives.latencies[rc_id])
        if summary is not None:
            summary.planned_items = drives.planned
            summary.dropped_items = dict(drives.failures[rc_id])
            if n < paired_floor(drives.planned):
                summary.incomplete = True
                summary.note = incomplete_note(
                    n, drives.planned, drives.failures[rc_id]
                )
    ctx.baseline_speed[rc_id] = summary
    line = (
        f"baseline {summary.note}"
        if summary is not None and summary.incomplete
        else arm_line("baseline speed", summary)
    )
    ctx.log.event("baseline_speed", rc_id=rc_id, line=line, latency=summary)
    logger.info("%s", line)
    return summary


# ---- Probe run configs leave the project at wave end ----

PROBE_RC_PREFIX = "PROBE "


def _measured_rc_ids(project_root: Path, task_id: str) -> set[str]:
    """Every run config id that a stored eval run in the project names — a
    measured config is never archived."""
    measured: set[str] = set()
    for task_dir in task_dirs(project_root, task_id):
        for path in task_dir.glob("evals/*/configs/*/runs/*/eval_run.kiln"):
            try:
                rc = json.loads(path.read_text()).get("task_run_config_id")
            except (OSError, ValueError):
                continue
            if rc:
                measured.add(str(rc))
    return measured


async def archive_probe_run_configs(ctx: Ctx) -> int:
    """Move every `PROBE …` run config out of the project into the
    quarantine tree (`archived_run_configs/<stamp>/`, paths kept, so it can
    be put back): the API has no archive flag and no delete for run configs,
    and the probes piled up in the compare page. Never a measured config,
    never one in the baseline chain, never the task's default. Best-effort:
    a failure is logged, never fatal. Returns how many moved."""
    if ctx.quarantine_root is None:
        return 0
    try:
        rows = await task_run_configs(ctx)
    except KilnError:
        return 0
    probe_ids = {
        str(r.get("id"))
        for r in rows
        if str(r.get("name") or "").startswith(PROBE_RC_PREFIX)
    }
    if not probe_ids:
        return 0
    project_root = Path(ctx.target.project_path).expanduser().resolve()
    keep = set(ctx.policy_exempt_rcs) | _measured_rc_ids(
        project_root, ctx.target.task_id
    )
    dirs = task_dirs(project_root, ctx.target.task_id)
    for task_dir in dirs:
        try:
            default = json.loads((task_dir / "task.kiln").read_text()).get(
                "default_run_config_id"
            )
        except (OSError, ValueError):
            default = None
        if default:
            keep.add(str(default))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    moved: list[str] = []
    for task_dir in dirs:
        for folder in sorted((task_dir / "run_configs").glob("*")):
            try:
                data = json.loads((folder / "task_run_config.kiln").read_text())
            except (OSError, ValueError):
                continue
            rc_id = str(data.get("id"))
            if rc_id not in probe_ids or rc_id in keep:
                continue
            target = (
                ctx.quarantine_root
                / "archived_run_configs"
                / stamp
                / folder.relative_to(project_root)
            )
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(folder), str(target))
            except OSError as exc:
                logger.warning("could not archive probe rc %s: %s", rc_id, exc)
                continue
            moved.append(rc_id)
    if moved:
        ctx._rc_pairs.clear()
        line = f"archived {len(moved)} probe run config(s) out of the project"
        ctx.log.event("probe_rcs_archived", rc_ids=moved, line=line)
        logger.info("%s", line)
    return len(moved)


async def _allowed_models(ctx: Ctx, baseline_rc_id: str) -> list[dict[str, str]]:
    """`BatchPlanInput.allowed_models`: what the server offers, filtered by
    the model policy. Advisory — a failed read is logged, never a lost wave.
    The full list is sent only when it can change a plan — a policy is in
    force, or the objective is speed or cost (where a model switch is the
    first lever). Otherwise the model is the environment and the list is
    prompt weight, so it holds the baseline's own model alone. It is never
    empty there: the planner plans arms only on listed models, and an empty
    list reads as "no arm at all"."""
    if ctx.model_policy is None and not is_efficiency(ctx.objective):
        return await _baseline_model_row(ctx, baseline_rc_id)
    try:
        available = await ctx.kiln.call("GET", "/api/available_models")
    except KilnError:
        ctx.log.event("available_models_unavailable")
        return []
    return [
        row
        for row in allowed_models(
            ctx.model_policy, available if isinstance(available, list) else []
        )
        # A model the preflight flagged is not offered to plans.
        if (row["provider"], row["model"]) not in ctx.flagged_models
    ]


async def _baseline_model_row(ctx: Ctx, baseline_rc_id: str) -> list[dict[str, str]]:
    """The baseline's (provider, model) as the one `allowed_models` row, or
    nothing when the baseline's run config cannot be read."""
    try:
        props = await _parent_props(ctx, baseline_rc_id)
    except (ProbeSetupError, KilnError):
        return []
    provider, model = props.get("model_provider_name"), props.get("model_name")
    if not (isinstance(provider, str) and isinstance(model, str)):
        return []
    return [{"provider": provider, "model": model, "name": model}]


# ---- The start-up quarantine and the model policy ----


def run_quarantine(ctx: Ctx) -> None:
    """Decide the splits (`kiln_harness_tuner.quarantine`), hand the held-out ids to the
    analyzer's train-read check, and write the TRAIN item set the loop gateway
    serves agents from. Stored val/test results stay in the
    project: the gateway and the read fence keep them from agents.
    Raises QuarantineError or TrainSetError — start-up refusals — when it
    cannot decide."""
    if ctx.quarantine_root is None:
        return
    project_root = Path(ctx.target.project_path).expanduser().resolve()
    report = survey_heldout(project_root, ctx.target.task_id)
    heldout_ids = heldout_item_ids(project_root, ctx.target.task_id)
    if ctx.train_set_path is not None:
        train = compute_train_set(project_root, ctx.target.task_id)
        train.write(ctx.train_set_path)
        ctx.log.event(
            "train_set",
            line=(
                f"train set: {len(train.ids)} train item(s) over"
                f" {len(train.per_eval)} eval(s) — the only items agents can"
                " reach (through the loop gateway)"
            ),
            items=len(train.ids),
        )
    ctx.analyzer.heldout_ids = heldout_ids
    ctx.startup_eval_ids = quarantine_eval_ids(project_root, ctx.target.task_id)
    for tag, count in sorted(report.orphan_tags.items()):
        # A tag whose name says held out, on items no current
        # eval's split defines — held out all the same, and said so.
        ctx.log.event(
            "heldout_tag",
            tag=tag,
            items=count,
            line=(
                f"held-out tag {tag!r} on {count} item(s): no current eval's split"
                " uses it — treated as held out (train set and read checks)"
            ),
        )
    line = (
        f"held out: {report.heldout_items} item(s);"
        f" {report.stored_eval_runs} eval run(s) and {report.stored_task_runs}"
        " task run(s) on them stay in the project — agents reach train items"
        " only (loop gateway, read fence)"
        + (
            f" ({report.orphan_items} item(s) held out by a val_/test_ tag"
            " no eval uses)"
            if report.orphan_items
            else ""
        )
    )
    ctx.log.event(
        "quarantine",
        line=line,
        orphan_items=report.orphan_items,
        stored_eval_runs=report.stored_eval_runs,
        stored_task_runs=report.stored_task_runs,
        heldout_items=report.heldout_items,
        overlap_items=report.overlap_items,
        kept_shared_task_runs=report.kept_shared_task_runs,
    )
    logger.info("%s", line)
    if report.overlap_items:
        logger.warning(
            "split check: %d item(s) are train for one eval and val/test for"
            " another — kept as train; fix the split design",
            report.overlap_items,
        )


async def _rc_pair(ctx: Ctx, rc_id: str) -> tuple[str | None, str | None]:
    if rc_id not in ctx._rc_pairs:
        for row in await task_run_configs(ctx):
            ctx._rc_pairs[str(row.get("id"))] = run_config_pair(row)
    return ctx._rc_pairs.get(rc_id, (None, None))


async def policy_refusal(ctx: Ctx, rc_id: str) -> str | None:
    """None when `rc_id` may be measured and promoted; else why not. The
    baseline chain is exempt (`Ctx.policy_exempt_rcs`)."""
    if ctx.model_policy is None or rc_id in ctx.policy_exempt_rcs:
        return None
    provider, model = await _rc_pair(ctx, rc_id)
    refusal = ctx.model_policy.refusal(provider, model)
    return None if refusal is None else f"rc {rc_id}: {refusal}"


async def require_policy(ctx: Ctx, rc_id: str) -> None:
    """The analyzer's `rc_guard`: raises before any eval job on a refused rc —
    outside the policy, or on a model the preflight flagged this run
    (the gateway runs in its own process and does not know the
    flags, so an agent can still build on such a model)."""
    refusal = await policy_refusal(ctx, rc_id)
    if refusal is None and ctx.flagged_models and rc_id not in ctx.policy_exempt_rcs:
        provider, model = await _rc_pair(ctx, rc_id)
        flagged = flagged_reason(ctx, provider, model)
        if flagged is not None:
            refusal = (
                f"rc {rc_id}: the model preflight flagged its model"
                f" {provider}/{model} this run: {flagged}"
            )
    if refusal is not None:
        ctx.log.event("model_policy_refused", rc_id=rc_id, reason=refusal)
        raise ModelPolicyRefused(refusal)


async def _note_baseline_policy(ctx: Ctx, rc_id: str) -> None:
    """The baseline is measured whatever its model (it is the operator's
    starting point); when the policy would refuse it, say so once, loudly —
    every arm must move off that model."""
    if ctx.model_policy is None:
        return
    try:
        provider, model = await _rc_pair(ctx, rc_id)
    except KilnError:
        return
    refusal = ctx.model_policy.refusal(provider, model)
    if refusal is not None:
        ctx.log.event("baseline_outside_policy", rc_id=rc_id, reason=refusal)
        logger.warning(
            "the baseline %s is outside the model policy (%s) — it is measured"
            " as the reference; no arm may use its model",
            rc_id,
            refusal,
        )


def lever_costs(ctx: Ctx) -> dict[str, LeverCost]:
    """`BatchPlanInput.lever_costs` — means over this run's builds per
    technique (items.py records one sample per build session)."""
    return {
        technique: LeverCost(
            n=len(samples),
            mean_build_usd=fmean(usd for usd, _secs in samples),
            mean_build_secs=fmean(secs for _usd, secs in samples),
        )
        for technique, samples in ctx.build_samples.items()
        if samples
    }


async def _lever_attempts(ctx: Ctx) -> dict[str, dict[str, int]]:
    """`BatchPlanInput.lever_attempts` — advisory like
    `eval_sizes`: a read that fails is logged and yields an empty map, never
    a lost wave."""
    try:
        return await ctx.memory.lever_attempts()
    except KilnError:
        ctx.log.event("lever_attempts_unavailable")
        return {}


# A write the memory layer REFUSED on a precondition — a wrong-kind or
# missing id, a terminal state. Deterministic: retrying it only wastes the
# backoff, so the ladder re-raises at once and the issue step below logs it.
_REFUSED_WRITE = (MemoryWriteRefused, MemoryRecordNotFoundError, RecordKindError)


async def _promotion_write(ctx: Ctx, write: Callable[[], Awaitable[None]]) -> None:
    """Each apply write gets a simple retry loop — up to 3 calls with
    20s backoff; exhaustion propagates (fatal — state integrity). A
    refused write (`_REFUSED_WRITE`) is not transport: no retry, raised."""
    for attempt in range(PROMOTION_WRITE_ATTEMPTS):
        try:
            await write()
            return
        except _REFUSED_WRITE:
            raise
        except Exception:
            if attempt == PROMOTION_WRITE_ATTEMPTS - 1:
                raise
            await ctx.sleep(PROMOTION_WRITE_BACKOFF_SECS)


async def _issue_write(
    ctx: Ctx, write: Callable[[], Awaitable[None]], *, issue_id: str, what: str
) -> None:
    """Apply step (b): an issue write whose id came from an agent (the analyzer's
    landed rows, the promotion agent's regressed rows). A refusal leaves the
    record untouched and must not abort an apply whose baseline pointer has
    already moved — it is logged under the same event the rejection stamps
    use, and the apply continues to the winner's stamp."""
    try:
        await _promotion_write(ctx, write)
    except _REFUSED_WRITE as exc:
        ctx.log.event(
            "memory_write_failed",
            write=what,
            what=f"issue write refused ({what})",
            issue_memory_id=issue_id,
            error=repr(exc),
        )


def _landed_issue_ids(winner: GateCandidate) -> list[str]:
    """Apply step (b): landed issues, sourced from the winner's TRAIN-side analyzer
    result — never from val. MergeWork → the per_ingredient_outcomes rows with
    landed=True (absent → nothing sourced). FixWork → the item's issue, unless
    the analyzer explicitly said it did not land.

    The FixWork branch must not close the item's issue unconditionally: a
    successful-but-partial fix would write `FIXED:` — and
    `kiln-harness-tuner-batch-selection` teaches planners to skip `FIXED:` records as
    fix candidates, which makes a false closure hide a *known working cure* from every
    later wave. An explicit landed=False row for the item's own issue suppresses the
    closure. Rows are still optional: absent rows keep the close, so nothing changes for
    an analyzer that emits none."""
    item = winner.outcome.item
    assert isinstance(item, ExperimentItem)
    work = item.work
    result = winner.outcome.result
    outcomes = (result and result.per_ingredient_outcomes) or []
    if isinstance(work, FixWork):
        declared = {row.issue_memory_id: row.landed for row in outcomes}
        if declared.get(work.issue_memory_id, True):
            return [work.issue_memory_id]
        return []
    assert isinstance(work, MergeWork)
    return [row.issue_memory_id for row in outcomes if row.landed]


async def _apply_promotion(
    ctx: Ctx,
    decision: PromotionDecision,
    candidates: list[GateCandidate],
    record: MemoryRecord,
) -> None:
    """Code apply, strict order — most important first. No
    rollback, deliberately: the baseline PATCH is the authoritative pointer,
    so any partial apply leaves the system pointing at the right rc."""
    # rc_id is the ONLY match key (pinned defensive contract):
    # a memory-id fallback could pair a foreign rc with a real candidate's
    # records — FIXED: citations naming an rc that is not that candidate's
    # build, silently. rc matches or nothing does.
    # Only ever called on the non-None winner path (_run_promotion_gate);
    # narrowed once so every write below takes a plain str.
    winner_rc_id = decision.winner_rc_id
    assert winner_rc_id is not None
    winner = next((o for o in candidates if o.rc_id == winner_rc_id), None)
    if winner is not None:
        winner_label = ctx.memory.exp_label(winner.outcome.item.id)
    else:
        # Defensive: the agent named an rc outside the candidate set. The
        # baseline PATCH (the authoritative pointer) still applies; (b)/(c)
        # have nothing sourced to write. The claimed memory id is UNVERIFIED
        # — never write it as if it were the winner's label (it may name a
        # candidate that didn't build this rc, and it breaks the
        # `EXP <MMDD>-<id>` convention); annotate it instead.
        claimed = decision.winner_experiment_memory_id
        winner_label = f"unmatched (claimed {claimed})" if claimed else "unmatched"
        ctx.log.event("promotion_winner_unmatched", winner_rc_id=decision.winner_rc_id)

    # Read the outgoing rc BEFORE (a) moves the pointer — the val record's
    # lineage line names the baseline this promotion replaced.
    previous_rc_id = ctx.memory.baseline_rc_id(record)
    # (a) the baseline record — the authoritative pointer, and metric-free.
    await _promotion_write(
        ctx, lambda: ctx.memory.promote(decision, winner_label, record)
    )
    # (a2) the `val` stats record — the ONLY val-metrics memory write.
    # Same 3-call/20s ladder as every other apply write, but exhaustion LOGS
    # AND CONTINUES rather than propagating: this is reporting, and a
    # promotion must not fail because a stats write 500'd.
    try:
        await _promotion_write(
            ctx,
            lambda: ctx.memory.record_val_stats(decision, winner_label, previous_rc_id),
        )
    except Exception as exc:
        # The generic swallowed-write event (`what` names the subject), so
        # the operator gets the same printed line the rejection writeback's
        # failures get instead of a JSONL-only signal.
        ctx.log.event(
            "memory_write_failed",
            write="val_stats",
            what="val stats record",
            rc_id=decision.winner_rc_id,
            error=repr(exc),
        )
    if winner is None:
        return
    # (b) issue writes — FIXED: per landed issue (train-sourced citation);
    # an issue BOTH landed and regressed gets the single merged mark_partial
    # line instead ("FIXED:" and "still repros" cannot
    # coexist); then the metric-free regression flags for the rest.
    citation = f"{decision.winner_rc_id}, {winner_label}"
    landed = _landed_issue_ids(winner)
    regressed_ids = {r.issue_memory_id for r in decision.regressed_issues}
    for issue_id in landed:
        if issue_id in regressed_ids:
            await _issue_write(
                ctx,
                lambda i=issue_id: ctx.memory.mark_partial(i, winner_rc_id),
                issue_id=issue_id,
                what="mark_partial",
            )
        else:
            await _issue_write(
                ctx,
                lambda i=issue_id: ctx.memory.mark_fixed(i, citation),
                issue_id=issue_id,
                what="mark_fixed",
            )
    landed_set = set(landed)
    for regressed in decision.regressed_issues:
        # The agent's free-prose `note` never reaches the train-side issue
        # record — the written line is code-composed and metric-free by
        # construction. The note lands in the log only —
        # for EVERY regressed issue, including the mark_partial ones.
        ctx.log.event(
            "issue_regressed",
            issue_memory_id=regressed.issue_memory_id,
            note=regressed.note,
        )
        if regressed.issue_memory_id in landed_set:
            continue  # already covered by the merged mark_partial line
        await _issue_write(
            ctx,
            lambda r=regressed: ctx.memory.flag_regressed(
                r.issue_memory_id, winner_rc_id
            ),
            issue_id=regressed.issue_memory_id,
            what="flag_regressed",
        )
    # (c) stamp the winner's experiment memory (decision-level; no val numbers).
    # A REFUSED stamp (the record is not a verdict) is logged, as
    # the issue writes above are. The baseline pointer has already moved; a
    # refusal on one record must not end the run.
    try:
        await _promotion_write(
            ctx, lambda: ctx.memory.stamp_promoted(winner.experiment_memory_id)
        )
    except _REFUSED_WRITE as exc:
        ctx.log.event(
            "memory_write_failed",
            write="stamp_promoted",
            what="the winner's experiment record stamp",
            rc_id=winner.rc_id,
            exp_mem_id=winner.experiment_memory_id,
            error=repr(exc),
        )


async def _rejection_write(
    ctx: Ctx,
    write: Callable[[], Awaitable[None]],
    kind: str,
    candidate: GateCandidate,
    what: str = "val-gate rejection",
) -> bool:
    """The rejection-stamp error policy: log-and-continue. A stamp is
    bookkeeping — it must never fail a gate that has already decided (and, on
    the winner path, already applied)."""
    try:
        await write()
    except Exception as exc:
        ctx.log.event(
            "memory_write_failed",
            write=kind,
            # `what` names the subject for the UI line — the event kind is
            # generic (issue-lifecycle writes follow this same policy),
            # so each emitter says what its own write was writing.
            what=what,
            rc_id=candidate.rc_id,
            exp_mem_id=candidate.experiment_memory_id,
            error=repr(exc),
        )
        return False
    return True


async def _record_rejections(
    ctx: Ctx,
    candidates: list[GateCandidate],
    decision: PromotionDecision,
    baseline_rc_id: str,
) -> None:
    """Write the val-gate rejection onto every candidate the decision passed
    over — code, never an agent. Covers both gate shapes: with no
    winner every candidate is a loser, and an unmatched winner rc (the
    defensive `_apply_promotion` case) likewise leaves every real candidate
    passed over. Candidates that never reached the gate aren't in this list
    and get nothing.

    Two writers = 2 GETs + 2 PATCHes per candidate, where the sibling
    `mark_fixed` does overview + content in ONE PATCH. That split is the
    architecture's (it mirrors `stamp_promoted`), and it is the reason a
    half-write is reachable: the stamp can land while the note fails, so
    `rejection_recorded` is emitted only when both landed. Both writers are
    idempotent, so a re-run repairs a half-write; if this path ever proves
    flaky live, merging them into one `record_rejection` PATCH is the fix."""
    held_on = decision.blocked_on if decision.winner_rc_id is None else None
    # A candidate the integrity check blocked already carries its
    # stamp and its own block line (`_record_gate_blocks`); "did not beat
    # it" would be false.
    blocked = {b.rc_id for b in ctx.gate_blocks}
    for outcome in candidates:
        if outcome.rc_id == decision.winner_rc_id or outcome.rc_id in blocked:
            continue
        exp_mem_id = outcome.experiment_memory_id
        if held_on is not None:
            # Not a loss — the gate escalated on an OPEN
            # constraint. HELD keeps the rc resubmittable once answered.
            stamped = await _rejection_write(
                ctx,
                lambda i=exp_mem_id: ctx.memory.stamp_held(i, held_on),
                "stamp",
                outcome,
            )
            noted = await _rejection_write(
                ctx,
                lambda i=exp_mem_id: ctx.memory.note_held(i, held_on),
                "note",
                outcome,
            )
            if stamped and noted:
                ctx.log.event(
                    "hold_recorded",
                    rc_id=outcome.rc_id,
                    exp_mem_id=exp_mem_id,
                    constraint_id=held_on,
                )
            continue
        stamped = await _rejection_write(
            ctx, lambda i=exp_mem_id: ctx.memory.stamp_rejected(i), "stamp", outcome
        )
        noted = await _rejection_write(
            ctx,
            lambda i=exp_mem_id: ctx.memory.note_rejection(i, baseline_rc_id),
            "note",
            outcome,
        )
        if stamped and noted:
            ctx.log.event(
                "rejection_recorded", rc_id=outcome.rc_id, exp_mem_id=exp_mem_id
            )
