"""RunStatusTracker — the live run-state object the loop builds as it goes
(docs/architecture.md section 11).

The tracker is an `EventLog` consumer: the loop keeps emitting events, and the
tracker folds them into one `RunStatus`. Everything
downstream is a view of it — the plain display reads snapshots, and the end-of-run
report gets the full structured history via `ReportInput` (report.py).

Also home of the run-id scheme (the run's start timestamp,
`YYMMDD-HHMM`) and the item kind/label/headline composition the orchestrator
emits with `wave_start` — composed here so the wave log carries the same
human-readable labels the UI shows.

Unknown event kinds are ignored (forward-compatible), and every handler
tolerates extra fields — events are an audit surface first, a protocol second.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from kiln_harness_tuner.schemas import (
    PROVIDER_LANE,
    SUBSCRIPTION_LANE,
    BaselineReview,
    BatchItem,
    DetectabilityReport,
    Detection,
    DroppedEvalItem,
    EvalOnlyItem,
    GateBlock,
    ItemOutcome,
    ItemStatus,
    MeasureIssueBaselineItem,
    MergeWork,
    ModelPreflightResult,
    NoiseCheckResult,
    PhaseSegment,
    ProbeItem,
    ProbeResult,
    PromotionDecision,
    RcChange,
    RemeasureItem,
    RunStatus,
    ScreenResult,
    SetupReport,
    TrialResult,
    UnloadableEval,
    WaveStatus,
    lane_for_source,
)


def make_run_id(now: datetime) -> str:
    """Start timestamp `YYMMDD-HHMM` — sortable, human-readable,
    collision-free at CLI granularity."""
    return now.strftime("%y%m%d-%H%M")


def item_kind(item: BatchItem) -> str:
    if isinstance(item, EvalOnlyItem):
        return "eval"
    if isinstance(item, MeasureIssueBaselineItem):
        return "measure"
    if isinstance(item, RemeasureItem):
        return "remeasure"
    if isinstance(item, ProbeItem):
        return "probe"
    return "merge" if isinstance(item.work, MergeWork) else "fix"


def item_label(item: BatchItem) -> str:
    """The one-line label: fix → hypothesis + issue id; merge → the
    ingredient experiment-memory ids; eval_only → the issue id;
    measure_issue_baseline → the issue and the eval it settles;
    remeasure → the rc whose coverage it completes."""
    if isinstance(item, EvalOnlyItem):
        return f"build eval for {item.issue_memory_id}"
    if isinstance(item, MeasureIssueBaselineItem):
        return f"measure baseline for {item.issue_memory_id} (eval {item.eval_id})"
    if isinstance(item, RemeasureItem):
        evals = ", ".join(item.eval_ids)
        return f"re-measure rc {item.rc_id} on {evals}"
    if isinstance(item, ProbeItem):
        return f"probe: {item.question}"
    work = item.work
    if isinstance(work, MergeWork):
        return " + ".join(w.experiment_memory_id for w in work.wins)
    flag = "[wildcard] " if work.wildcard else ""
    return f"{flag}{work.hypothesis} ({work.issue_memory_id})"


def item_summary(item: BatchItem) -> dict[str, Any]:
    """One `wave_start.items` entry: the id, kind and label the tracker and
    the plain lines read, then the item's details (`item_details`)."""
    return {
        "item_id": item.id,
        "kind": item_kind(item),
        "label": item_label(item),
        **item_details(item),
    }


def item_details(item: BatchItem) -> dict[str, Any]:
    """What a display shows of a planned item before its outcome: its type,
    hypothesis, targets and kind of change. Every key is on every item, None
    where it does not apply to the item's type. The lists are new lists:
    the loop changes the item's own lists during the wave (a `needs_eval`
    fix gains its new eval as a target)."""
    details: dict[str, Any] = {
        "type": item.type,
        "hypothesis": None,
        "technique": None,
        "issue_memory_id": None,
        "wildcard": None,
        "needs_eval": None,
        "eval_id": None,
        "targets": None,
        "promotion_candidate": None,
        "max_usd": None,
        "merge_wins": None,
        "probe": None,
        "remeasure": None,
        "measure": None,
        "parent_rc_id": None,
        "existing_rc_id": None,
    }
    if isinstance(item, EvalOnlyItem):
        details["issue_memory_id"] = item.issue_memory_id
    elif isinstance(item, MeasureIssueBaselineItem):
        details["issue_memory_id"] = item.issue_memory_id
        details["measure"] = {"eval_id": item.eval_id, "scores": _copy(item.scores)}
    elif isinstance(item, RemeasureItem):
        details["remeasure"] = {"rc_id": item.rc_id, "eval_ids": list(item.eval_ids)}
    elif isinstance(item, ProbeItem):
        details["probe"] = {
            "question": item.question,
            "max_drives": item.max_drives,
            "for_idea": item.for_idea,
            "code": item.check is not None,
        }
        details["parent_rc_id"] = item.parent_rc_id
    else:
        details["targets"] = [
            {"eval_id": t.eval_id, "scores": _copy(t.scores)}
            for t in item.evaluation_target
        ]
        details["promotion_candidate"] = item.promotion_candidate
        details["max_usd"] = item.max_usd
        work = item.work
        if isinstance(work, MergeWork):
            details["merge_wins"] = [
                {"experiment_memory_id": w.experiment_memory_id, "rc_id": w.rc_id}
                for w in work.wins
            ]
        else:
            details.update(
                hypothesis=work.hypothesis,
                technique=work.technique,
                issue_memory_id=work.issue_memory_id,
                wildcard=work.wildcard,
                needs_eval=work.needs_eval,
                eval_id=work.eval_id,
                parent_rc_id=work.parent_rc_id,
                existing_rc_id=work.existing_rc_id,
            )
    return details


def _copy(scores: Sequence[str] | None) -> list[str] | None:
    return list(scores) if scores is not None else None


def exit_detail(reason: object, detail: object) -> str | None:
    """The `exit` event's `detail` as the run screen, the plain log and the
    report show it: only on a `timeout`, where it says how much time was
    left (`orchestrator.wave_floor_detail`). Other details stay in the wave
    log: a provider error has its own line, and a crash its traceback."""
    if reason != "timeout" or not isinstance(detail, str) or not detail:
        return None
    return detail


def spend_split(by_lane: Mapping[str, float]) -> tuple[float, float]:
    """`(kiln, other)` from a per-lane fold — the run's own `spend_by_lane`
    or one item's. The agent lane holds no money (`_on_spend`).

    `other` is money on a lane this vocabulary does not know. It is returned
    rather than folded into the Kiln side so a renderer can show it: the fold
    never drops money, and neither should the display."""
    kiln = by_lane.get(PROVIDER_LANE, 0.0)
    other = sum(
        amount
        for lane, amount in by_lane.items()
        if lane not in (SUBSCRIPTION_LANE, PROVIDER_LANE)
    )
    return kiln, other


def plan_headline(items: Sequence[BatchItem]) -> str:
    """`"N experiments[, M eval-only][, K baseline measurements]"` (the
    wave-start headline). Counted per type rather than by subtraction — with
    three item types a `len(items) - experiments` remainder would silently
    mislabel one of them."""
    counts = Counter(item_kind(item) for item in items)
    experiments = counts["fix"] + counts["merge"]
    parts = []
    if experiments:
        parts.append(f"{experiments} experiment{'s' if experiments != 1 else ''}")
    if counts["eval"]:
        parts.append(f"{counts['eval']} eval-only")
    if counts["measure"]:
        measurements = counts["measure"]
        parts.append(
            f"{measurements} baseline measurement{'s' if measurements != 1 else ''}"
        )
    if counts["remeasure"]:
        remeasures = counts["remeasure"]
        parts.append(f"{remeasures} re-measurement{'s' if remeasures != 1 else ''}")
    if counts["probe"]:
        probes = counts["probe"]
        parts.append(f"{probes} probe{'s' if probes != 1 else ''}")
    return ", ".join(parts) or "empty plan"


class RunStatusTracker:
    """Folds loop events into a `RunStatus`. Implements the EventLog
    protocol structurally; wire behind a FanoutLog before any UI so renders
    see post-update state.

    `clock` MUST share the orchestrator `Ctx.clock`'s domain (monotonic by
    default): `run_start`'s `deadline` field is in Ctx.clock units and gets
    compared against this clock for `time_remaining_secs` — cli.py passes
    `ctx.clock` through for exactly this reason."""

    def __init__(
        self,
        *,
        project_id: str,
        task_id: str,
        guidance: str,
        clock: Callable[[], float] = time.monotonic,
        agent_cli: str = "claude",
    ):
        self._status = RunStatus(
            project_id=project_id,
            task_id=task_id,
            guidance=guidance,
            agent_cli=agent_cli,
            baseline_history=[],
            waves=[],
            spend_total_usd=0.0,
            time_remaining_secs=0.0,
            exit_reason=None,
        )
        self._clock = clock
        # cli.py builds the tracker immediately before `orchestrate`, so
        # construction IS the run's start — and the totals row
        # must read truthfully from its first frame, before any event lands.
        self._run_started = clock()
        self._deadline: float | None = None
        self._item_started: dict[str, float] = {}  # active wave only
        # Active wave only: each item's current live_state + when it entered
        # it — the ItemStatus.phase_secs fold.
        self._item_state_started: dict[str, tuple[str, float]] = {}
        self._phase_started: float | None = None  # phase_start clock reading
        # A reader on another thread may snapshot between events — folds
        # and copies must not interleave.
        self._lock = threading.Lock()

    # ---- EventLog surface ----

    def event(self, kind: str, **fields: object) -> None:
        handler = getattr(self, f"_on_{kind}", None)
        if handler is not None:
            with self._lock:
                handler(**fields)

    # ---- snapshot (the only read surface) ----

    def snapshot(self) -> RunStatus:
        """Deep copy with fresh in-flight elapsed + time remaining — safe to
        hand to renderers/agents (mutations never reach the tracker)."""
        with self._lock:
            status = self._status.model_copy(deep=True)
            status.elapsed_secs = self._clock() - self._run_started
            if self._deadline is not None:
                status.time_remaining_secs = max(self._deadline - self._clock(), 0.0)
            if status.phase is not None and self._phase_started is not None:
                status.phase_elapsed_secs = self._clock() - self._phase_started
            if status.waves:
                for item in status.waves[-1].items:
                    if item.live_state == "done":
                        continue
                    started = self._item_started.get(item.item_id)
                    if started is not None:
                        item.elapsed_secs = self._clock() - started
                    state_entry = self._item_state_started.get(item.item_id)
                    if state_entry is not None:
                        # In-flight current-state partial, on the COPY only —
                        # the tracker folds durations at transitions, never
                        # here (no double-fold).
                        state, state_started = state_entry
                        item.phase_secs[state] = item.phase_secs.get(state, 0.0) + (
                            self._clock() - state_started
                        )
        return status

    # ---- handlers (one per event kind; **_ tolerates extra fields) ----

    def _on_run_start(
        self,
        deadline: object = None,
        timeout_mins: object = None,
        budget_usd: object = None,
        objective: object = None,
        eval_building: object = None,
        **_: object,
    ) -> None:
        if objective in ("quality", "speed", "cost"):
            self._status.objective = objective  # type: ignore[assignment]
        if isinstance(eval_building, bool):
            self._status.eval_building = eval_building
        # Both `object`: event fields are untrusted audit data, and the
        # isinstance guards below — not the annotations — are what make them
        # safe to fold.
        if isinstance(deadline, (int, float)) and not isinstance(deadline, bool):
            self._deadline = float(deadline)
            # The deadline alone decides whether "time left" is knowable —
            # `timeout_mins` is the budget's label (the report header),
            # not its clock. Gating the two on one field each way round lies:
            # a junk budget would suppress a computable "left", and a junk
            # deadline would render "0s left" on a run that has hours.
            self._status.time_remaining_known = True
        if isinstance(timeout_mins, (int, float)) and not isinstance(
            timeout_mins, bool
        ):
            self._status.timeout_mins = int(timeout_mins)
        if isinstance(budget_usd, (int, float)) and not isinstance(budget_usd, bool):
            self._status.budget_usd = float(budget_usd)

    def _on_eval_job_errors(
        self,
        errors: Sequence[object] = (),
        split: object = None,
        error_class: object = None,
        **_: object,
    ) -> None:
        # First-class hole accounting: the count is kept, not re-derived by
        # hand-reading prose.
        self._status.eval_job_error_items += len(errors)
        if errors:
            key = f"{split or 'unknown'} {error_class or 'unclassified'}"
            classes = self._status.eval_job_error_classes
            classes[key] = classes.get(key, 0) + len(errors)

    def _on_speed_result(
        self,
        rc_id: object = None,
        latency: object = None,
        cost: object = None,
        **_: object,
    ) -> None:
        # Every val-side speed and cost comparison, for the report.
        self._status.speed_results.append(
            {
                "wave": len(self._status.waves),
                "rc_id": str(rc_id),
                "latency": latency if isinstance(latency, dict) else None,
                "cost": cost if isinstance(cost, dict) else None,
            }
        )

    def _on_val_fill_failed(self, **_: object) -> None:
        self._status.val_fill_failures += 1

    def _on_promotion_blocked(
        self, winner_rc_id: object = None, reasons: Sequence[object] = (), **_: object
    ) -> None:
        # The gate refused an ineligible winner — surfaces where
        # promotion_skipped does (the wave block + the report).
        if self._status.waves:
            summary = "; ".join(str(r) for r in reasons) or "ineligible winner"
            self._status.waves[
                -1
            ].promotion_skipped = (
                f"blocked: winner {winner_rc_id} ineligible — {summary}"
            )

    def _on_gate_block(
        self,
        rc_id: object = None,
        exp_mem_id: object = None,
        reasons: Sequence[object] = (),
        **_: object,
    ) -> None:
        # A candidate the gate's integrity check blocked for
        # copying eval text — the report lists each with its reasons.
        self._status.gate_blocks.append(
            GateBlock(
                rc_id=str(rc_id),
                experiment_memory_id=str(exp_mem_id) if exp_mem_id else None,
                reasons=[str(r) for r in reasons],
            )
        )

    def _on_baseline_found(self, rc_id: str | None = None, **_: object) -> None:
        # Pre-flight seed: the startup baseline
        # reaches the history even if the run exits before wave 1;
        # wave_start's consecutive dedup keeps it single.
        if rc_id and (
            not self._status.baseline_history
            or self._status.baseline_history[-1] != rc_id
        ):
            self._status.baseline_history.append(str(rc_id))

    def _on_janitor(self, memory_ids: Sequence[object] = (), **_: object) -> None:
        self._status.janitor_stamped = [str(m) for m in memory_ids]

    def _on_setup_sync(self, report: SetupReport | None = None, **_: object) -> None:
        """The setup phase's record — the full object, so
        the report writer gets the ids and not just the event's counts."""
        if report is not None:
            self._status.setup = report

    def _on_eval_unloadable(
        self,
        eval_id: object = None,
        name: object = None,
        reason: object = None,
        **_: object,
    ) -> None:
        # An eval Kiln can't load, which the run leaves out — the report
        # names each one.
        self._status.unloadable_evals.append(
            UnloadableEval(
                eval_id=str(eval_id), name=str(name or eval_id), reason=str(reason)
            )
        )

    def _on_eval_item_dropped(
        self,
        item_id: object = None,
        wave: object = None,
        item_kind: object = None,
        issue_memory_id: object = None,
        **_: object,
    ) -> None:
        # A planned eval build code dropped (eval building off): its issue
        # has no eval, which the report names.
        if not isinstance(wave, int) or isinstance(wave, bool):
            return
        self._status.dropped_eval_items.append(
            DroppedEvalItem(
                item_id=str(item_id),
                wave=wave,
                kind=str(item_kind),
                issue_memory_id=str(issue_memory_id),
            )
        )

    def _on_detectability(
        self, report: DetectabilityReport | None = None, **_: object
    ) -> None:
        # The pre-run check, for the report's appendix.
        if isinstance(report, DetectabilityReport):
            self._status.detectability = report

    def _on_noise_check(
        self, result: NoiseCheckResult | None = None, **_: object
    ) -> None:
        if isinstance(result, NoiseCheckResult):
            self._status.noise_check = result

    def _on_model_preflight(
        self, result: ModelPreflightResult | None = None, **_: object
    ) -> None:
        # The models checked and the ones flagged, for the report.
        if isinstance(result, ModelPreflightResult):
            self._status.model_preflight = result

    def _on_screening_round(self, results: Sequence[object] = (), **_: object) -> None:
        # Every screening result, for the report's trade-off
        # table. A result that does not validate is left out, not guessed.
        for raw in results:
            try:
                self._status.screen_results.append(ScreenResult.model_validate(raw))
            except ValidationError:
                continue

    def _on_smell_detections(
        self, rc: object = None, detections: Sequence[object] = (), **_: object
    ) -> None:
        # The code detections of each baseline, for the report's smells
        # appendix. A detection that does not validate is left out.
        if not isinstance(rc, str):
            return
        kept = []
        for raw in detections:
            try:
                kept.append(Detection.model_validate(raw))
            except ValidationError:
                continue
        self._status.detections_by_rc[rc] = kept

    def _on_baseline_review(self, review: object = None, **_: object) -> None:
        # Each review of a baseline, for the report's smells appendix. A
        # review that does not validate is left out.
        try:
            self._status.baseline_reviews.append(BaselineReview.model_validate(review))
        except ValidationError:
            return

    # The warm path carries the same object: `setup_ok` means nothing was
    # written, not that nothing is worth reporting (unbound issues and the
    # baseline rule ride along).
    _on_setup_ok = _on_setup_sync

    def _on_phase_start(self, name: str, label: str = "", **_: object) -> None:
        self._status.phase = name
        self._status.phase_detail = label or name
        self._status.phase_elapsed_secs = 0.0
        self._phase_started = self._clock()

    def _on_phase_end(
        self,
        name: str = "",
        wave: object = None,
        elapsed_secs: object = None,
        **_: object,
    ) -> None:
        # Run-level segment record: the event's elapsed
        # is authoritative (same clock domain); a bare phase_end falls back
        # to the tracker's own phase_start reading.
        if not isinstance(elapsed_secs, (int, float)) or isinstance(elapsed_secs, bool):
            elapsed_secs = (
                self._clock() - self._phase_started
                if self._phase_started is not None
                else 0.0
            )
        self._status.phase_segments.append(
            PhaseSegment(
                name=str(name) if name else (self._status.phase or "unknown"),
                wave=wave
                if isinstance(wave, int) and not isinstance(wave, bool)
                else None,
                elapsed_secs=float(elapsed_secs),
            )
        )
        self._status.phase = None
        self._status.phase_detail = ""
        self._status.phase_elapsed_secs = 0.0
        self._phase_started = None

    def _on_wave_start(
        self,
        wave: int,
        should_wrap_up: bool = False,
        baseline_rc_id: str | None = None,
        plan_headline: str = "",
        rejected_summary: str = "",
        items: Sequence[dict] = (),
        probe_phase: bool = False,
        **_: object,
    ) -> None:
        now = self._clock()
        self._item_started = {str(i["item_id"]): now for i in items}
        self._item_state_started = {str(i["item_id"]): ("planned", now) for i in items}
        if baseline_rc_id and (
            not self._status.baseline_history
            or self._status.baseline_history[-1] != baseline_rc_id
        ):
            self._status.baseline_history.append(baseline_rc_id)
        self._status.waves.append(
            WaveStatus(
                wave=wave,
                should_wrap_up=should_wrap_up,
                plan_headline=plan_headline,
                rejected_summary=rejected_summary,
                probe_phase=probe_phase,
                items=[
                    ItemStatus(
                        item_id=str(i["item_id"]),
                        kind=str(i.get("kind", "")),
                        label=str(i.get("label", "")),
                        live_state="planned",
                        eval_progress=None,
                        outcome=None,
                        elapsed_secs=0.0,
                        spend_usd=0.0,
                    )
                    for i in items
                ],
                promotion=None,
                tripwire=None,
            )
        )

    def _find_item(self, item_id: object) -> ItemStatus | None:
        if not self._status.waves:
            return None
        for item in self._status.waves[-1].items:
            if item.item_id == item_id:
                return item
        return None

    def _fold_item_phase(self, item: ItemStatus, now: float) -> None:
        """Close out the item's current state's duration into `phase_secs`."""
        entry = self._item_state_started.get(item.item_id)
        if entry is None:
            return
        state, started = entry
        item.phase_secs[state] = item.phase_secs.get(state, 0.0) + (now - started)

    def _on_item_state(self, item_id: str, state: str, **_: object) -> None:
        item = self._find_item(item_id)
        if item is None or item.live_state == "done":
            # A late state event after the outcome landed must not reopen the
            # item or restart its state clock (the finding line — and the
            # folded phase_secs — are frozen).
            return
        now = self._clock()
        self._fold_item_phase(item, now)
        self._item_state_started[item.item_id] = (state, now)
        item.live_state = state  # type: ignore[assignment]
        item.detail = None  # a state change invalidates the sub-detail

    def _on_item_detail(self, item_id: str, detail: str = "", **_: object) -> None:
        item = self._find_item(item_id)
        if item is not None and item.live_state != "done":
            item.detail = detail or None

    def _on_item_trial(self, item_id: str, trial: object = None, **_: object) -> None:
        """The residual trial's result, kept on the item for
        the UIs and the report. No event of this kind is written now; an
        older wave log that holds one still replays."""
        item = self._find_item(item_id)
        if item is None or not isinstance(trial, dict):
            return
        try:
            item.trial = TrialResult.model_validate(trial)
        except ValidationError:
            return

    def _on_item_probe(self, item_id: str, probe: object = None, **_: object) -> None:
        """A probe item's answer, kept on the item for the UIs
        and the report."""
        item = self._find_item(item_id)
        if item is None or not isinstance(probe, dict):
            return
        try:
            item.probe = ProbeResult.model_validate(probe)
        except ValidationError:
            return

    def _on_item_change(self, item_id: str, change: object = None, **_: object) -> None:
        """The build agent's structured "what changed" — kept
        on the item for every UI and the report; a malformed payload is
        ignored rather than breaking the fold."""
        item = self._find_item(item_id)
        if item is None or not isinstance(change, dict):
            return
        try:
            item.change = RcChange.model_validate(change)
        except ValidationError:
            return

    def _on_eval_progress(
        self, item_id: str, done: int, total: int, **_: object
    ) -> None:
        item = self._find_item(item_id)
        if item is not None:
            item.eval_progress = (done, total)

    def _on_spend(
        self,
        cost_usd: float = 0.0,
        item_id: str | None = None,
        source: str = "",
        lane: str = "",
        evidence_cost_usd: float | None = None,
        tokens: int = 0,
        **_: object,
    ) -> None:
        # The fold keeps its dimensions — a missing
        # source/lane lands under "unknown", never dropped.
        source_key = str(source) if source else "unknown"
        # The event's own lane always wins. Only when it carries none — every
        # `spend` event written before the lane field existed — is the lane
        # derived from the source (`lane_for_source`), which is
        # what makes the agent/Kiln split recoverable on historical wave
        # logs instead of "unknown".
        lane_key = str(lane) if lane else (lane_for_source(source_key) or "unknown")
        source_fold = self._status.spend_by_source.setdefault(source_key, {})
        if lane_key == SUBSCRIPTION_LANE:
            # The agent sessions run on the agent CLI's own account: tokens
            # only. Any dollar figure on the event (an older wave log) is not
            # money the run spent, and no total, row or item carries it.
            if tokens:
                self._status.agent_tokens += int(tokens)
                by_source = self._status.agent_tokens_by_source
                by_source[source_key] = by_source.get(source_key, 0) + int(tokens)
            return
        self._status.spend_total_usd += cost_usd
        # Evidence cost defaults to the spend itself; eval-lane events say
        # what they read.
        self._status.evidence_spend_usd += (
            cost_usd if evidence_cost_usd is None else float(evidence_cost_usd)
        )
        by_lane = self._status.spend_by_lane
        by_lane[lane_key] = by_lane.get(lane_key, 0.0) + cost_usd
        source_fold[lane_key] = source_fold.get(lane_key, 0.0) + cost_usd
        item = self._find_item(item_id) if item_id is not None else None
        if item is not None:
            item.spend_usd += cost_usd
            item.spend_by_lane[lane_key] = (
                item.spend_by_lane.get(lane_key, 0.0) + cost_usd
            )

    def _on_item_outcome(
        self, item_id: str, outcome: ItemOutcome | None = None, **_: object
    ) -> None:
        item = self._find_item(item_id)
        if item is None:
            return
        item.outcome = outcome
        item.live_state = "done"
        item.detail = None  # the finding line replaces any sub-detail
        now = self._clock()
        self._fold_item_phase(item, now)
        self._item_state_started.pop(item.item_id, None)
        started = self._item_started.get(item.item_id)
        if started is not None:
            item.elapsed_secs = now - started

    def _on_tripwire(
        self, error_class: str = "?", failed: int = 0, **_: object
    ) -> None:
        if self._status.waves:
            self._status.waves[-1].tripwire = f"{failed}× {error_class}"

    def _on_promotion_skipped(self, reason: str = "", **_: object) -> None:
        # A promote_best hard-fail is noted in RunStatus and the report.
        if self._status.waves:
            self._status.waves[-1].promotion_skipped = str(reason)

    def _on_promotion(
        self, decision: PromotionDecision | None = None, **_: object
    ) -> None:
        if self._status.waves and decision is not None:
            self._status.waves[-1].promotion = decision
        if (
            decision is not None
            and decision.winner_rc_id
            and (
                not self._status.baseline_history
                or self._status.baseline_history[-1] != decision.winner_rc_id
            )
        ):
            self._status.baseline_history.append(decision.winner_rc_id)

    def _on_exit(self, reason: str, detail: object = None, **_: object) -> None:
        self._status.exit_reason = reason
        self._status.exit_detail = exit_detail(reason, detail)
