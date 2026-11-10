"""Recorded event sequences for the run model's tests.

`Run` emits events with the real emitters' field shapes: wave-start entries
from `status.item_summary` on real plan items, and the loop's own Pydantic
objects where the loop passes objects (`detectability.report`,
`item_outcome.outcome`, `promotion.decision`) or their JSON dumps where it
passes dumps (C1b rows, C2 tables). Each event is stamped on a clock the
test moves with `at(minutes)`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from kiln_harness_tuner.schemas import (
    CandidateValTable,
    DetectabilityReport,
    DetectabilityRow,
    ErrorInfo,
    EvalOnlyItem,
    EvalTarget,
    ExperimentItem,
    ExperimentResult,
    FixWork,
    ItemOutcome,
    LatencyComparison,
    MeasureIssueBaselineItem,
    MeasurementSummary,
    MergeWork,
    PerEvalResult,
    ProbeItem,
    PromotionDecision,
    RemeasureItem,
    TrainDetectability,
    WinRef,
)
from kiln_harness_tuner.status import item_summary
from kiln_harness_tuner.tui.model import words
from kiln_harness_tuner.tui.model.events import Stamped
from kiln_harness_tuner.tui.model.fold import RunModel
from kiln_harness_tuner.tui.model.names import RcNames
from kiln_harness_tuner.tui.model.state import RunInfo, plain

START_RC = "424242000001"
START_NAME = "rc 0003"
HOST = "http://localhost:8757"
INFO = RunInfo(
    project_id="424242110001",
    task_id="424242783017",
    kiln_host=HOST,
    agent_cli="claude",
    agent_cli_text="claude -p",
    project_name="Depot Ops",
    task_name="Depot Ops Desk",
    run_id="261109-1412",
)
# The loop's clock at the run's start, and the wall clock then.
T0 = 5000.0
WALL0 = datetime(2026, 11, 9, 14, 12)


def fix(
    item_id: str,
    eval_id: str = "e1",
    *,
    technique: str = "prompt",
    hypothesis: str = "Name the hold fields in the prompt",
    candidate: bool = True,
    needs_eval: bool = False,
) -> ExperimentItem:
    return ExperimentItem(
        id=item_id,
        type="experiment",
        evaluation_target=[EvalTarget(eval_id=eval_id)] if not needs_eval else [],
        promotion_candidate=candidate,
        work=FixWork(
            issue_memory_id="424242500001",
            hypothesis=hypothesis,
            technique=technique,
            predicted_effect="+5 points",
            needs_eval=needs_eval,
            eval_id=None if needs_eval else eval_id,
        ),
    )


def merge(item_id: str, *rc_ids: str) -> ExperimentItem:
    return ExperimentItem(
        id=item_id,
        type="experiment",
        evaluation_target=[EvalTarget(eval_id="e1"), EvalTarget(eval_id="e2")],
        promotion_candidate=True,
        work=MergeWork(
            wins=[
                WinRef(experiment_memory_id=f"424242600{i:03d}", rc_id=rc)
                for i, rc in enumerate(rc_ids)
            ]
        ),
    )


def probe(
    item_id: str,
    question: str = "Does the agent call get_container?",
    *,
    for_idea: str | None = None,
):
    return ProbeItem(
        id=item_id,
        type="probe",
        question=question,
        method="drive three items and read the trace",
        max_drives=3,
        for_idea=for_idea,
        reason="the planner needs it",
    )


def eval_only(item_id: str) -> EvalOnlyItem:
    return EvalOnlyItem(
        id=item_id, type="eval_only", issue_memory_id="424242500009", reason="no eval"
    )


def measure(item_id: str, eval_id: str = "e2") -> MeasureIssueBaselineItem:
    return MeasureIssueBaselineItem(
        id=item_id,
        type="measure_issue_baseline",
        issue_memory_id="424242500010",
        eval_id=eval_id,
        reason="settle it",
    )


def remeasure(item_id: str, rc_id: str, eval_id: str = "e1") -> RemeasureItem:
    return RemeasureItem(
        id=item_id, type="remeasure", rc_id=rc_id, eval_ids=[eval_id], reason="holes"
    )


def comparison(base: float, cand: float, *, metric="latency_ms", clear=False):
    return LatencyComparison(
        method="eval_runs",
        split="train",
        n_items=30,
        metric=metric,
        baseline_median_ms=base,
        candidate_median_ms=cand,
        clearly_faster=clear,
    )


def row(
    eval_id: str = "e1",
    base: float = 0.6,
    cand: float = 0.7,
    *,
    score_key: str = "pass",
    parent: str = START_RC,
    split: str = "train",
    ci: tuple[float, float] | None = (0.02, 0.18),
    direction: str = "higher_is_better",
    score_type: str = "pass_fail",
    latency: LatencyComparison | None = None,
    cost: LatencyComparison | None = None,
) -> PerEvalResult:
    raw = cand - base
    return PerEvalResult(
        eval_id=eval_id,
        score_key=score_key,
        split=split,
        baseline_rc_id=parent,
        compared_to="parent",
        n_items=30,
        delta=-raw if direction == "lower_is_better" else raw,
        p_value=0.01,
        significant=ci is not None and ci[0] > 0,
        ci_low=None if ci is None else ci[0],
        ci_high=None if ci is None else ci[1],
        fail_to_pass=3,
        pass_to_fail=0,
        spend_usd=0.1,
        baseline_score=base,
        candidate_score=cand,
        score_type=score_type,
        direction=direction,
        latency=latency,
        cost=cost,
    )


def detect_row(
    eval_id: str,
    name: str,
    *,
    score_key: str = "pass",
    score_type: str = "pass_fail",
    direction: str = "higher_is_better",
    n: int = 100,
    room: int = 40,
    val_n: int = 50,
    driving: bool = True,
) -> DetectabilityRow:
    return DetectabilityRow(
        train=TrainDetectability(
            eval_id=eval_id,
            eval_name=name,
            score_key=score_key,
            score_type=score_type,
            direction=direction,
            n=n,
            room=room,
            floor=6,
        ),
        val_n=val_n,
        promotion_driving=driving,
    )


class Run:
    """A run, event by event, folded into a `RunModel` as it goes."""

    def __init__(self, info: RunInfo = INFO, names: RcNames | None = None):
        self.t = T0
        self.events: list[Stamped] = []
        self.plan_items: dict[str, Any] = {}
        self.model = RunModel(
            info,
            started=T0 - 40,
            names=names or RcNames({START_RC: START_NAME}),
            clock=lambda: self.t,
        )

    # ---- the clock ----

    def at(self, minutes: float) -> Run:
        self.t = T0 + minutes * 60
        return self

    @property
    def wall(self) -> datetime:
        return WALL0 + timedelta(seconds=self.t - T0)

    def emit(self, kind: str, **fields: Any) -> Stamped:
        stamped = Stamped(kind, fields, self.t, self.wall)
        self.events.append(stamped)
        self.model.apply(stamped)
        return stamped

    # ---- reading the model ----

    def log(self) -> list[str]:
        return [line.text for line in self.model.log if not line.divider]

    def card(self, item_id: str):
        cards = self.model.experiments(self.t).cards
        return next(c for c in cards if c.item_id == item_id)

    def concern(self, name: str):
        """The Results card of the concern `name`: an eval's card says
        "Eval: " before its name."""
        titles = (name, words.eval_title(name))
        return next(c for c in self.model.results().cards if c.title in titles)

    def line5(self, item_id: str) -> str:
        return plain(self.card(item_id).line5)

    # ---- the run's start ----

    def start(
        self,
        *,
        timeout_mins: int = 180,
        budget: float | None = 50.0,
        objective="quality",
    ) -> Run:
        self.emit(
            "run_start",
            timeout_mins=timeout_mins,
            deadline=self.t + timeout_mins * 60,
            deadline_iso=(self.wall + timedelta(minutes=timeout_mins)).isoformat(),
            budget_usd=budget,
            objective=objective,
            model_policy=None,
        )
        self.emit("baseline_found", rc_id=START_RC)
        return self

    def detectability(self, *rows: DetectabilityRow, warnings=()) -> Run:
        report = DetectabilityReport(
            baseline_rc_id=START_RC,
            alpha=0.05,
            floor_binary=6,
            floor_rating=6,
            rows=list(rows),
            warnings=list(warnings),
        )
        self.emit("detectability", line="detectability: …", report=report)
        return self

    def standard_start(self, **kwargs: Any) -> Run:
        """A run with two pass-rate evals (60% and 80% on train)."""
        self.start(**kwargs)
        return self.detectability(
            detect_row("e1", "Hold placement", room=40),
            detect_row("e2", "Refusal honesty", room=20),
        )

    # ---- waves and items ----

    def plan(self, wave: int, *, wrap_up: bool = False, replan: bool = False) -> Run:
        head = "Re-planning" if replan else "Planning"
        self.emit(
            "phase_start",
            name="planning",
            wave=wave,
            label=f"{head} {'wrap-up ' if wrap_up else ''}wave {wave}… (a session)",
        )
        return self

    def planned(self, wave: int, elapsed: float = 120.0) -> Run:
        self.emit("phase_end", name="planning", wave=wave, elapsed_secs=elapsed)
        return self

    def wave_start(
        self,
        wave: int,
        *items: Any,
        parent: str = START_RC,
        wrap_up: bool = False,
        probe_phase: bool = False,
        headline: str = "",
    ) -> Run:
        for item in items:
            self.plan_items[item.id] = item
        self.emit(
            "wave_start",
            wave=wave,
            should_wrap_up=wrap_up,
            baseline_rc_id=parent,
            plan_headline=headline or f"{len(items)} experiments",
            rejected_summary="",
            items=[item_summary(item) for item in items],
            **({"probe_phase": True} if probe_phase else {}),
        )
        return self

    def state(self, item_id: str, state: str) -> Run:
        self.emit("item_state", item_id=item_id, state=state)
        return self

    def chunk(
        self, item_id: str, rc_id: str, cells: list[tuple[bool | None, bool | None]]
    ) -> Run:
        """One `measure_chunk` with these (parent_pass, cand_pass) rows."""
        item = self.model.items[item_id]
        judged = item.judged + len(cells)
        fixed = item.fixed + sum(1 for p, c in cells if p is False and c)
        broke = item.broke + sum(1 for p, c in cells if p is True and not c)
        self.emit(
            "measure_chunk",
            item_id=item_id,
            rc_id=rc_id,
            chunk=1,
            chunks=3,
            line=f"{item_id}: chunk",
            planned=36,
            spend_usd=0.5,
            cap_usd=4.0,
            decision="continue",
            judged=judged,
            fixed=fixed,
            broke=broke,
            cells=[["e1", f"i{n}", p, c] for n, (p, c) in enumerate(cells)],
        )
        return self

    def arm_results(
        self,
        item_id: str,
        rc_id: str,
        *rows: PerEvalResult,
        parent: str = START_RC,
        latency: LatencyComparison | None = None,
        cost: LatencyComparison | None = None,
    ) -> Run:
        self.emit(
            "arm_results",
            item_id=item_id,
            rc_id=rc_id,
            parent_rc_id=parent,
            per_eval=[r.model_dump(mode="json") for r in rows],
            latency=None if latency is None else latency.model_dump(mode="json"),
            cost=None if cost is None else cost.model_dump(mode="json"),
            errors=[],
        )
        return self

    def outcome(
        self,
        item_id: str,
        status: str = "completed",
        *,
        rc_id: str | None = None,
        successful: bool | None = None,
        stop_kind: str | None = None,
        measurement_status: str = "stopped",
        error_class: str | None = None,
        findings: str = "",
    ) -> Run:
        item = self.plan_items[item_id]
        if rc_id is None and isinstance(item, RemeasureItem):
            rc_id = item.rc_id  # as the real emitter does (`items._remeasure`)
        result = None
        if successful is not None:
            result = ExperimentResult(
                successful=successful,
                findings=findings,
                memory_overview="+5",
                memory_content="",
            )
        measurement = None
        if stop_kind is not None or status in ("capped", "not_landed"):
            measurement = MeasurementSummary(
                status="capped" if status == "capped" else measurement_status,
                stop_kind=stop_kind,
            )
        infra = None
        if error_class is not None:
            infra = ErrorInfo(error_class=error_class, detail="boom")
        self.emit(
            "item_outcome",
            item_id=item_id,
            outcome=ItemOutcome(
                item=item,
                status=status,
                rc_id=rc_id,
                experiment_memory_id=f"exp-{item_id}" if rc_id else None,
                result=result,
                infra_error=infra,
                measurement=measurement,
            ),
        )
        return self

    def success(
        self,
        item_id: str,
        rc_id: str,
        *rows: PerEvalResult,
        parent: str = START_RC,
        **kwargs: Any,
    ) -> Run:
        """An arm through measure, analyze and a successful verdict."""
        self.state(item_id, "building_rc").state(item_id, "measuring")
        self.emit("measurement", item_id=item_id, rc_id=rc_id, status="completed")
        self.state(item_id, "analyzing")
        self.arm_results(item_id, rc_id, *rows, parent=parent, **kwargs)
        return self.outcome(item_id, rc_id=rc_id, successful=True)

    # ---- the gate ----

    def gate(
        self,
        wave: int,
        *tables: CandidateValTable,
        winner: str | None,
        blocked: dict[str, list[str]] | None = None,
        blocked_on: str | None = None,
        end: bool = True,
    ) -> Run:
        self.emit(
            "phase_start",
            name="promotion_gate",
            wave=wave,
            label="Promotion gate: measuring candidates on val…",
        )
        self.emit(
            "gate_fills_planned",
            baseline_rc_id=START_RC,
            rc_ids=[t.rc_id for t in tables],
            eval_ids=["e1", "e2"],
            fills=2 * (len(tables) + 1),
        )
        self.emit(
            "gate_val_tables",
            baseline_rc_id=tables[0].per_eval[0].baseline_rc_id if tables else START_RC,
            objective="quality",
            tables=[t.model_dump(mode="json") for t in tables],
        )
        decision = PromotionDecision(
            winner_rc_id=winner,
            winner_experiment_memory_id=None,
            rationale="the numbers",
            blocked_on=blocked_on,
        )
        self.emit(
            "promotion", winner_rc_id=winner, rationale="the numbers", decision=decision
        )
        for rc, reasons in (blocked or {}).items():
            self.emit("promotion_blocked", winner_rc_id=rc, reasons=reasons)
        if end:
            self.gate_end(wave)
        return self

    def gate_end(self, wave: int) -> Run:
        self.emit("wave_end", wave=wave)
        self.emit("phase_end", name="promotion_gate", wave=wave, elapsed_secs=60.0)
        return self


def table(
    rc_id: str,
    *rows: PerEvalResult,
    latency: LatencyComparison | None = None,
    cost: LatencyComparison | None = None,
) -> CandidateValTable:
    return CandidateValTable(
        rc_id=rc_id,
        experiment_memory_id=f"exp-{rc_id}",
        per_eval=list(rows),
        latency=latency,
        cost=cost,
    )


def val_row(eval_id: str, base: float, cand: float, *, parent: str = START_RC):
    return row(eval_id, base, cand, parent=parent, split="val")
