"""`RunModel`: the reducer that folds the run's stamped events into the run
screen's state (architecture section 7).

`apply` folds one event: the `UiTracker` first (the run's money), then the
model's own handler for the kind, if it has one. Unknown kinds are ignored,
and every handler reads its fields defensively: the events are an audit
surface first. Handlers also decide when an event makes a log line.
Nothing here runs on the loop's thread or writes to the loop.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from kiln_harness_tuner.schemas import (
    PROVIDER_LANE,
    SUBSCRIPTION_LANE,
    CandidateValTable,
    DetectabilityReport,
    ItemOutcome,
    LatencyComparison,
    ModelPreflightResult,
    NoiseCheckResult,
    ProbeResult,
    PromotionDecision,
    ScreenResult,
    spend_lane_labels,
)
from kiln_harness_tuner.status import spend_split
from kiln_harness_tuner.tui.model import cards, links, log, results, timeline, words
from kiln_harness_tuner.tui.model.events import Stamped
from kiln_harness_tuner.tui.model.names import RcNames
from kiln_harness_tuner.tui.model.state import (
    COST,
    LATENCY,
    LATENCY_KEY,
    Cell,
    CheckStatus,
    ClockView,
    Concern,
    ConcernKey,
    ExperimentsView,
    GateModel,
    GaugeView,
    GoalView,
    ItemModel,
    Line,
    LogLine,
    Mode,
    PlanKind,
    PlanStep,
    ResultsView,
    RunInfo,
    Segment,
    Span,
    StartStep,
    TimelineView,
    TitleView,
    Unconfirmed,
    WaveModel,
    joined,
)
from kiln_harness_tuner.tui.model.tracker import UiTracker

logger = logging.getLogger("kiln_harness_tuner.tui")

PLANNER_KINDS = ("plan", "replan", "rescreen")
CHECK_FINDINGS = {
    "judge_copy": "copied",
    "generality": "leaked",
    "reference_check": "found",
}
BASELINE_PROGRESS_PREFIX = "baseline measuring: "


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _whole(value: object) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _texts(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else []


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def integrity_block(reasons: list[str]) -> bool:
    """Is a `promotion_blocked` inside the gate a refusal of the config,
    by code's integrity checks? Its reasons are code-composed
    (`orchestrator._apply_checked`, `_run_promotion_gate`): an integrity
    refusal names the checks or carries a `BLOCKED:` flag. Every other block
    is val evidence with holes, and the config stays pooled."""
    return any("integrity" in r or r.startswith("BLOCKED:") for r in reasons)


def _cell(parent_pass: object, cand_pass: object) -> Cell:
    """One judged item against its parent. A candidate with no stored row
    counts as a fail, as the engine counts it (`Judged.d`)."""
    if parent_pass is None:
        return "unknown"
    if parent_pass:
        return "held" if cand_pass else "broke"
    return "fixed" if cand_pass else "failing"


def _valid(model_type: Any, value: object) -> Any:
    if isinstance(value, model_type):
        return value
    try:
        return model_type.model_validate(value)
    except ValueError:
        return None


class RunModel:
    def __init__(
        self,
        info: RunInfo,
        *,
        started: float,
        names: RcNames | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.info = info
        self.names = names or RcNames()
        self.tracker = UiTracker(
            project_id=info.project_id,
            task_id=info.task_id,
            agent_cli=info.agent_cli,
            clock=clock,
        )
        self.version = 0
        self.objective = info.objective
        # The hand-off: the run's own start begins with the agent CLI test.
        self.started = started
        self.cli_checked: float | None = None
        # The run's clock: `zero` is its start on the loop's own clock.
        self.zero: float | None = None
        self.zero_wall: datetime | None = None
        self.limit: float | None = None
        self.deadline: float | None = None
        self.ends_wall: datetime | None = None
        self.budget: float | None = None
        self.exit_reason: str | None = None
        self.exit_at: float | None = None
        self.report_path: str | None = None
        self.run_ended = False
        self.train_items: int | None = None
        self.heldout_items: int | None = None
        # Results.
        self.concerns: list[Concern] = []
        self.eval_names: dict[str, str] = {}
        self.val_n: dict[str, int] = {}
        self.start_rc: str | None = None
        self.start_train: dict[ConcernKey, float] = {}
        self.start_val: dict[ConcernKey, float] | None = None
        self.confirmed_rc: str | None = None
        self.confirmed: dict[ConcernKey, float] = {}
        self.generation = 0
        self.promotions: list[str] = []
        self.rejected: set[str] = set()
        # Configs a gate blocked, or the model policy refused before one.
        self.refused: set[str] = set()
        self.unconfirmed: list[Unconfirmed] = []
        # Items, waves and the gate.
        self.waves: list[WaveModel] = []
        self.items: dict[str, ItemModel] = {}
        self.rc_items: dict[str, str] = {}
        self.gate: GateModel | None = None
        # The Experiments section.
        self.mode: Mode = "starting"
        self.mode_since = started
        self.planning_wave: int | None = None
        self.plans: dict[int, list[PlanStep]] = {}
        self.start_steps: list[StartStep] = []
        self._step_at: dict[str, int] = {}
        self.opening_screen: PlanStep | None = None
        # Main-wave cycles, for the wave estimate: each runs from a wave's
        # first planning start to the next wave's.
        self.cycle_wave: int | None = None
        self.cycle_start: float | None = None
        self.cycle_spend = 0.0
        self.cycles: list[tuple[float, float]] = []
        self.wrap_up = False
        # The timeline.
        self.segments: list[Segment] = []
        self.open_segments: dict[str, int] = {}
        self._planning_ended: float | None = None
        # The log.
        self.log: list[LogLine] = []
        self.questions: dict[str, str] = {}
        self.ui_errors = 0

    # ---- inputs ----

    def apply(self, stamped: Stamped) -> None:
        self.tracker.fold(stamped)
        handler = getattr(self, f"_on_{stamped.kind}", None)
        if handler is not None:
            handler(stamped, stamped.fields)
        self.version += 1

    def agent_cli_checked(self, at: float) -> None:
        """The agent CLI answered its test prompt (`_RUN_PROGRESS.cli_checked`,
        which no event carries)."""
        if self.cli_checked is None:
            self.cli_checked = at
            self.version += 1

    def learn_names(self, rows: Iterable[Mapping[str, Any]]) -> None:
        """The answer of the app's run config lookup (`RcNames.learn`)."""
        if self.names.learn(rows):
            self.version += 1

    def update_info(self, **changes: Any) -> None:
        self.info = replace(self.info, **changes)
        self.version += 1

    def note_link(self, text: str) -> None:
        """A line the app adds to the log itself: the address of a link it
        could not open (no graphical browser, or over SSH)."""
        self.log.append(log.line(datetime.now(), "link", text))
        self.version += 1

    def note_ui_error(self) -> None:
        """The drain could not fold an event. The log says so once."""
        self.ui_errors += 1
        if self.ui_errors == 1:
            self.log.append(log.line(datetime.now(), "warn", words.UI_ERROR))
        self.version += 1

    # ---- what the other modules read ----

    def item(self, item_id: object) -> ItemModel | None:
        return self.items.get(str(item_id)) if item_id else None

    def wave(self, number: int) -> WaveModel | None:
        return next((w for w in reversed(self.waves) if w.wave == number), None)

    def succeeded(self, item_id: str) -> bool:
        item = self.items.get(item_id)
        outcome = item.outcome if item else None
        return bool(
            outcome is not None
            and outcome.status == "completed"
            and outcome.result is not None
            and outcome.result.successful
        )

    def counts(self, result: Unconfirmed) -> bool:
        """Does a train result still count for Best Unconfirmed? Not when
        its arm landed without success, nor when a gate judged its config
        and did not promote it. A config the gate did not judge stays
        pooled, and counts (functional spec 7.5). A screen survivor's
        estimate counts until an arm works on its config: from then on only
        a successful arm keeps it, for what the arm did not measure itself
        (`_on_arm_results` replaces the rest)."""
        if result.item_id is not None and not self.succeeded(result.item_id):
            return False
        rc = result.rc_id
        if rc is None:
            return True
        if rc in self.rejected or rc in self.refused:
            return False
        measured_by = self.items.get(self.rc_items.get(rc, ""))
        if measured_by is None:
            return True
        if result.item_id is None:
            return measured_by.landed and self.succeeded(measured_by.item_id)
        return not measured_by.landed or self.succeeded(measured_by.item_id)

    def rc_label(self, rc_id: str) -> str:
        return self.names.label(rc_id, self.rc_items.get(rc_id))

    def parent_label(self, wave: int | None = None) -> str:
        """The baseline a wave branches from: "start", or its name."""
        found = self.wave(wave) if wave is not None else None
        rc = found.parent_rc_id if found else (self.confirmed_rc or self.start_rc)
        if not rc or rc == self.start_rc:
            return words.FROM_START
        return self.rc_label(rc)

    def holder_label(self, result: Unconfirmed) -> str:
        if result.rc_id:
            return self.rc_label(result.rc_id)
        return result.label or result.item_id or ""

    def val_progress(self, rc_id: str | None) -> tuple[int, int]:
        """A gate candidate's val items stored, of the items it needs: each
        fill's own count once it lands, the eval's val size before."""
        gate = self.gate
        if gate is None or rc_id is None:
            return 0, 0
        done = total = 0
        for eval_id in gate.eval_ids:
            fill = gate.fills.get((rc_id, eval_id))
            if fill is None:
                total += self.val_n.get(eval_id, 0)
                continue
            stored, expected = fill
            done += stored
            total += max(stored, expected or 0)
        return done, max(total, done)

    def live_count(self) -> int:
        return sum(1 for item in self.items.values() if item.live)

    # ---- the views ----

    def title(self) -> TitleView:
        info = self.info
        return TitleView(
            task=info.task_name,
            run=words.run_label(info.run_id),
            meta=words.title_meta(
                info.agent_cli_text, info.agent_slots, info.eval_slots
            ),
        )

    def goal(self) -> GoalView:
        evals = len({c.eval_id for c in self.concerns}) or None

        def count(value: int | None) -> Span:
            return Span(str(value) if value is not None else words.UNKNOWN)

        data: Line = (
            Span("train ", "muted"),
            count(self.train_items),
            Span(" · held out ", "muted"),
            count(self.heldout_items),
            Span(f" (val and test) · {words.TEST_NEVER_READ}", "muted"),
        )
        return GoalView(words.goal_rows(self.objective, evals), data)

    def clock(self, now: float) -> ClockView:
        status = self.tracker.status
        until = self.exit_at if self.exit_at is not None else now
        elapsed = max(until - self.zero, 0.0) if self.zero is not None else 0.0
        limit = self.limit
        time_gauge = GaugeView(
            words.TIME_LABEL,
            words.duration(elapsed),
            elapsed,
            limit,
            words.duration(limit) if limit else "",
        )
        # The task's model calls: the agents' tokens are on the lanes line.
        spent = status.spend_total_usd
        spend_gauge = GaugeView(
            words.SPEND_LABEL,
            words.money(spent),
            spent,
            self.budget,
            words.money(self.budget) if self.budget is not None else words.NO_BUDGET,
        )
        return ClockView(
            time=time_gauge,
            spend=spend_gauge,
            lanes=self._lanes(spent, elapsed),
            times=words.times_text(self.zero_wall, self.ends_wall),
            wave=self._wave_line(now),
            last_wave=self.wrap_up and self.exit_reason is None,
        )

    def _lanes(self, spent: float, elapsed: float) -> Line:
        status = self.tracker.status
        labels = spend_lane_labels(status.agent_cli)
        kiln, other = spend_split(status.spend_by_lane)
        parts: list[Line] = [
            (Span(f"{labels[PROVIDER_LANE]} ", "muted"), Span(words.money(kiln))),
        ]
        if other:
            parts.append((Span("other ", "muted"), Span(words.money(other))))
        # The agents' tokens: a rough count, not money, not in the budget.
        parts.append(
            (
                Span(f"{labels[SUBSCRIPTION_LANE]} ", "muted"),
                Span(words.tokens(status.agent_tokens)),
            )
        )
        main_waves = any(w.wave >= 1 for w in self.waves)
        if main_waves and self.exit_reason is None and elapsed > 0:
            parts.append((Span(words.burn(spent / (elapsed / 3600))),))
        return joined(parts)

    def _wave_line(self, now: float) -> Line:
        if self.exit_reason is not None:
            waves = len({w.wave for w in self.waves if w.wave >= 1})
            return (Span(f"{words.plural(waves, 'wave')}, done", "muted"),)
        wave = self.cycle_wave
        if wave is None:
            return (Span(words.BEFORE_WAVE_1, "muted"),)
        head: Line = (Span("wave ", "muted"), Span(str(wave), "bold"))
        estimate = timeline.waves_estimate(self, now)
        if estimate is None:
            return head
        about = "" if self.wrap_up else "~"
        return head + (Span(f" of {about}{estimate}", "muted"),)

    def timeline(self, now: float) -> TimelineView | None:
        return timeline.timeline_view(self, now)

    def results(self) -> ResultsView:
        return results.results_view(self)

    def experiments(self, now: float) -> ExperimentsView:
        return cards.experiments_view(self, now)

    def compare_url(self) -> str:
        info = self.info
        return links.compare_url(
            info.kiln_host, info.project_id, info.task_id, self.results().compare_rc_ids
        )

    def lineage_url(self, selected: str | None = None) -> str:
        info = self.info
        return links.lineage_url(
            info.kiln_host, info.project_id, info.task_id, selected
        )

    # ---- small helpers for the handlers ----

    def _say(self, s: Stamped, kind: str, text: str, *, bold: bool = False) -> None:
        self.log.append(log.line(s.wall, kind, text, bold=bold))

    def _set_mode(self, mode: Mode, at: float) -> None:
        if self.mode != mode:
            self.mode = mode
            self.mode_since = at

    def _step(
        self,
        label: str,
        status: CheckStatus,
        text: str,
        *,
        since: float | None = None,
    ) -> None:
        step = StartStep(status, label, text, since)
        if label in self._step_at:
            self.start_steps[self._step_at[label]] = step
        else:
            self._step_at[label] = len(self.start_steps)
            self.start_steps.append(step)

    def _extra_step(self, status: CheckStatus, text: str) -> None:
        """A line under a step, with no label of its own (a warning)."""
        self.start_steps.append(StartStep(status, "", text))

    def _offset(self, at: float) -> float | None:
        return None if self.zero is None else max(at - self.zero, 0.0)

    def _open_segment(self, kind: str, wave: int, at: float) -> None:
        start = self._offset(at)
        if start is None:
            return
        self._close_segment(kind, at)
        self.open_segments[kind] = len(self.segments)
        self.segments.append(Segment(kind, wave, start))

    def _close_segment(self, kind: str, at: float, mark: str | None = None) -> None:
        index = self.open_segments.pop(kind, None)
        end = self._offset(at)
        if index is None or end is None:
            return
        segment = self.segments[index]
        self.segments[index] = replace(segment, end=max(end, segment.start), mark=mark)

    def _see_rc(self, item: ItemModel | None, rc_id: object) -> None:
        if not rc_id:
            return
        rc = str(rc_id)
        self.names.see(rc)
        if item is not None:
            item.rc_id = rc
            # A re-measure completes an rc another item built: it never
            # names the rc.
            if item.kind != "remeasure":
                self.rc_items[rc] = item.item_id

    def _add_concern(self, eval_id: str, score_key: str, row: Any) -> None:
        if any(c.key == (eval_id, score_key) for c in self.concerns):
            return
        name = self.eval_names.get(eval_id, eval_id)
        concern = results.concern_from_row(row, name)
        if concern is not None:
            self.concerns.append(concern)

    def _check_wave_landed(self, at: float) -> None:
        """An items stretch ends with its wave's last verdict."""
        if not self.waves:
            return
        wave = self.waves[-1]
        if wave.items and all(item.landed for item in wave.items):
            self._close_segment("recon" if wave.wave == 0 else "items", at)

    # ---- the run and its start ----

    def _on_run_start(self, s: Stamped, f: Mapping[str, Any]) -> None:
        timeout = _number(f.get("timeout_mins"))
        deadline = _number(f.get("deadline"))
        self.limit = timeout * 60 if timeout is not None else None
        self.deadline = deadline
        if deadline is not None and self.limit is not None:
            self.zero = deadline - self.limit
        else:
            self.zero = s.mono
        self.zero_wall = s.wall - timedelta(seconds=s.mono - self.zero)
        try:
            self.ends_wall = datetime.fromisoformat(_text(f.get("deadline_iso")))
        except ValueError:
            self.ends_wall = (
                self.zero_wall + timedelta(seconds=self.limit) if self.limit else None
            )
        self.budget = _number(f.get("budget_usd"))
        if f.get("objective") in ("quality", "speed", "cost"):
            self.objective = str(f["objective"])
        # The loop starts only once the agent CLI answered its test prompt.
        if self.cli_checked is None:
            self.cli_checked = s.mono
        self._open_segment("setup", 0, s.mono)
        self.log.append(log.divider(s.wall, words.START_DIVIDER))
        self._say(
            s,
            "run",
            log.run_started(self.objective, self.budget, _whole(timeout)),
        )

    def _on_train_set(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self.train_items = _whole(f.get("items"))

    def _on_quarantine(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self.heldout_items = _whole(f.get("heldout_items"))

    def _on_setup_sync(self, s: Stamped, f: Mapping[str, Any]) -> None:
        writes = [(field, _whole(f.get(field)) or 0) for field in words.SETUP_WRITES]
        text = words.setup_text(f.get("evals_active"), f.get("evals_total"), writes)
        self._step(words.SETUP_STEP, "ok", text)

    _on_setup_ok = _on_setup_sync

    def _on_eval_unloadable(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self._extra_step("warn", _text(f.get("line") or f.get("reason")))

    def _on_eval_item_dropped(self, s: Stamped, f: Mapping[str, Any]) -> None:
        # A planned eval build that code dropped because eval building is
        # off. The item never starts, so it gets no card: only this line.
        text = _text(f.get("line"))
        if text:
            self._say(s, "warn", text)

    def _on_baseline_found(self, s: Stamped, f: Mapping[str, Any]) -> None:
        rc = _text(f.get("rc_id"))
        if not rc:
            return
        self.start_rc = rc
        self.names.see(rc)
        self._step(words.BASELINE_STEP, "ok", self.rc_label(rc))
        self._say(s, "run", log.starting_from(self.rc_label(rc)))

    def _baseline_name(self) -> str:
        return self.rc_label(self.start_rc) if self.start_rc else words.BASELINE_STEP

    def _on_baseline_progress(self, s: Stamped, f: Mapping[str, Any]) -> None:
        progress = _text(f.get("line")).removeprefix(BASELINE_PROGRESS_PREFIX)
        since = self._steps_since(words.BASELINE_STEP) or s.mono
        self._step(
            words.BASELINE_STEP,
            "busy",
            f"{self._baseline_name()} · {progress}",
            since=since,
        )

    def _steps_since(self, label: str) -> float | None:
        index = self._step_at.get(label)
        return self.start_steps[index].since if index is not None else None

    def _on_baseline_measured(self, s: Stamped, f: Mapping[str, Any]) -> None:
        evals = _mapping(f.get("evals"))
        hows = [_text(e.get("how")) for e in evals.values() if isinstance(e, Mapping)]
        failed = sum(1 for how in hows if how.startswith("failed"))
        name = self._baseline_name()
        if failed:
            text = f"{name} · {words.baseline_failed(failed, len(hows))}"
            self._step(words.BASELINE_STEP, "warn", text)
        elif hows and all(how == "stored" for how in hows):
            self._step(words.BASELINE_STEP, "ok", f"{name} · {words.BASELINE_STORED}")
        else:
            self._step(words.BASELINE_STEP, "ok", f"{name} · {words.BASELINE_MEASURED}")

    def _on_baseline_speed(self, s: Stamped, f: Mapping[str, Any]) -> None:
        latency = _valid(LatencyComparison, f.get("latency"))
        rc = _text(f.get("rc_id"))
        if latency is not None and latency.baseline_median_ms is not None:
            if rc and rc == self.start_rc:
                self.start_train.setdefault(LATENCY_KEY, latency.baseline_median_ms)

    def _on_model_preflight(self, s: Stamped, f: Mapping[str, Any]) -> None:
        result = f.get("result")
        if not isinstance(result, ModelPreflightResult):
            return
        if result.skipped and not result.checks:
            self._step(words.MODELS_STEP, "skip", words.skipped(result.skipped))
            return
        flagged = len(result.flagged())
        text = words.models_text(
            len(result.checks), flagged, len(result.inconclusive())
        )
        self._step(words.MODELS_STEP, "warn" if flagged else "ok", text)

    def _on_detectability(self, s: Stamped, f: Mapping[str, Any]) -> None:
        report = f.get("report")
        if not isinstance(report, DetectabilityReport):
            return
        found = results.concerns_from(report)
        known = {c.key for c in found}
        self.concerns = found + [c for c in self.concerns if c.key not in known]
        for row in report.rows:
            train = row.train
            self.eval_names.setdefault(train.eval_id, train.eval_name or train.eval_id)
            if row.val_n is not None:
                self.val_n[train.eval_id] = max(
                    self.val_n.get(train.eval_id, 0), row.val_n
                )
        for key, rate in results.start_pass_rates(report).items():
            self.start_train.setdefault(key, rate)
        if report.warnings:
            self._step(words.DETECTION_STEP, "warn", report.warnings[0])
            for warning in report.warnings[1:]:
                self._extra_step("warn", warning)
        else:
            text = words.detection_ok(report.floor_binary, len(report.rows))
            self._step(words.DETECTION_STEP, "ok", text)

    def _on_detectability_warning(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self._say(s, "warn", log.detectability_warning(_text(f.get("line"))))

    def _on_noise_estimate(self, s: Stamped, f: Mapping[str, Any]) -> None:
        text = words.noise_busy(f.get("estimate_usd"))
        self._step(words.NOISE_STEP, "busy", text, since=s.mono)

    def _on_noise_check(self, s: Stamped, f: Mapping[str, Any]) -> None:
        result = f.get("result")
        if not isinstance(result, NoiseCheckResult):
            return
        if result.skipped:
            self._step(words.NOISE_STEP, "skip", words.skipped(result.skipped))
            return
        text = words.noise_ok(len(result.rows), result.items_per_eval)
        self._step(words.NOISE_STEP, "warn" if result.warnings else "ok", text)
        for warning in result.warnings:
            self._extra_step("warn", warning)

    def _on_recon_skipped(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self._step(words.RECON_STEP, "skip", words.skipped(_text(f.get("reason"))))

    def _on_opening_screen_skipped(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self._step(words.SCREEN_STEP, "skip", words.skipped(_text(f.get("reason"))))

    # ---- phases ----

    def _on_phase_start(self, s: Stamped, f: Mapping[str, Any]) -> None:
        name = f.get("name")
        wave = _whole(f.get("wave"))
        if name == "preflight":
            self._step(words.PREFLIGHT_STEP, "busy", words.PREFLIGHT_BUSY, since=s.mono)
        elif name == "setup":
            self._step(words.SETUP_STEP, "busy", words.SETUP_BUSY, since=s.mono)
        elif name == "baseline_measure":
            text = f"{self._baseline_name()} · {words.BASELINE_MEASURING}"
            self._step(words.BASELINE_STEP, "busy", text, since=s.mono)
        elif name == "planning" and wave is not None:
            self._planning_started(s, wave, _text(f.get("label")))
        elif name == "promotion_gate":
            self._gate_started(s, wave)
        elif name == "tripwire_backoff":
            self._open_segment("backoff", wave or 0, s.mono)
            self._say(s, "warn", log.BACKOFF)
        elif name == "reporting":
            self._open_segment("report", self.cycle_wave or 0, s.mono)

    def _on_phase_end(self, s: Stamped, f: Mapping[str, Any]) -> None:
        name = f.get("name")
        if name == "preflight":
            self._step(words.PREFLIGHT_STEP, "ok", words.PREFLIGHT_OK)
        elif name == "planning":
            steps = self.plans.get(_whole(f.get("wave")) or -1, [])
            if steps and steps[-1].kind in PLANNER_KINDS and steps[-1].ended is None:
                steps[-1].ended = s.mono
            self._close_segment("plan", s.mono)
            self._planning_ended = s.mono
        elif name == "promotion_gate":
            self._gate_ended(s)
        elif name == "tripwire_backoff":
            self._close_segment("backoff", s.mono)
        elif name == "reporting":
            self._close_segment("report", s.mono)

    def _planning_started(self, s: Stamped, wave: int, label: str) -> None:
        self._close_segment("setup", s.mono)
        self._close_segment("screen", s.mono)
        self._set_mode("planning", s.mono)
        first = wave not in self.plans
        steps = self.plans.setdefault(wave, [])
        for step in steps:
            if step.ended is None:
                step.ended = s.mono
        if first:
            spent = self.tracker.status.spend_total_usd
            if self.cycle_wave is not None and self.cycle_start is not None:
                self.cycles.append(
                    (s.mono - self.cycle_start, spent - self.cycle_spend)
                )
            self.cycle_wave, self.cycle_start, self.cycle_spend = wave, s.mono, spent
            self.wrap_up = "wrap-up wave" in label
            self.planning_wave = wave
            self.log.append(log.divider(s.wall, f"wave {wave}"))
            kind: PlanKind = "plan"
        else:
            kind = "replan" if label.startswith("Re-planning") else "rescreen"
        steps.append(PlanStep(kind, s.mono))
        self._open_segment("plan", wave, s.mono)

    # ---- screening ----

    def _screening(self, s: Stamped) -> PlanStep | None:
        """The step of the screening round under way, opened by its first
        event. It starts where the planner session before it ended."""
        wave = self.planning_wave or 0
        if "screen" not in self.open_segments:
            start = self._planning_ended if self._planning_ended is not None else s.mono
            self._planning_ended = None
            self._open_segment("screen", wave, start)
            if wave == 0:
                self._step(words.SCREEN_STEP, "busy", "", since=start)
            else:
                steps = self.plans.setdefault(wave, [])
                if steps and steps[-1].kind in PLANNER_KINDS:
                    steps[-1].then = "screen"
                steps.append(PlanStep("screen", start))
        if wave == 0:
            if self.opening_screen is None:
                self.opening_screen = PlanStep("screen", s.mono)
            return self.opening_screen
        steps = self.plans.get(wave, [])
        return steps[-1] if steps and steps[-1].kind == "screen" else None

    def _on_screen_candidate_dropped(self, s: Stamped, f: Mapping[str, Any]) -> None:
        # A drop happens before a round: no screen runs for it.
        self._say(s, "run", log.settled_survivor(_text(f.get("line"))))

    def _on_screening_round(self, s: Stamped, f: Mapping[str, Any]) -> None:
        step = self._screening(s)
        raw = _list(f.get("results"))
        found = [r for r in (_valid(ScreenResult, x) for x in raw) if r is not None]
        survivors = [r for r in found if r.verdict == "survivor"]
        if step is not None:
            step.ended = s.mono
            step.candidates = len(found)
            step.survivors = len(survivors)
            # The loop's own round number, run-wide, as in its files.
            step.round = _whole(f.get("round")) or 0
        self._close_segment("screen", s.mono)
        if (self.planning_wave or 0) == 0:
            text = words.screen_text(len(found), None, len(survivors))
            self._step(words.SCREEN_STEP, "ok", text)
        for result in survivors:
            self.names.see(result.rc_id)
            self.unconfirmed.extend(
                results.survivor_unconfirmed(result, self.generation)
            )
        self._say(s, "run", log.screening_round(_whole(f.get("round")), found))
        for result in found:
            if result.verdict != "survivor":
                self._say(s, "nowin", log.screen_drop(result))

    # ---- waves and items ----

    def _on_wave_start(self, s: Stamped, f: Mapping[str, Any]) -> None:
        wave = _whole(f.get("wave"))
        if wave is None:
            return
        probe_phase = bool(f.get("probe_phase"))
        wrap_up = bool(f.get("should_wrap_up"))
        parent = _text(f.get("baseline_rc_id")) or None
        self.names.see(parent)
        entries = _list(f.get("items"))
        items = [
            ItemModel(
                item_id=str(entry.get("item_id")),
                wave=wave,
                kind=_text(entry.get("kind")),
                details=entry,
                state_since=s.mono,
            )
            for entry in entries
            if isinstance(entry, Mapping) and entry.get("item_id")
        ]
        model = WaveModel(
            wave,
            probe_phase,
            wrap_up,
            parent,
            _text(f.get("plan_headline")),
            items,
        )
        self.waves.append(model)
        for item in items:
            self.items[item.item_id] = item
        self._close_segment("screen", s.mono)
        if wave == 0:
            self._open_segment("recon", 0, s.mono)
            self._step(words.RECON_STEP, "busy", "", since=s.mono)
        else:
            self._set_mode("running", s.mono)
            if probe_phase:
                steps = self.plans.setdefault(wave, [])
                if steps and steps[-1].kind in PLANNER_KINDS:
                    steps[-1].then = "probes"
                steps.append(PlanStep("probes", s.mono))
            else:
                self.wrap_up = wrap_up
            self._open_segment("items", wave, s.mono)
        self._say(
            s,
            "wave",
            log.wave_started(
                wave, probe_phase, self.parent_label(wave), model.headline, wrap_up
            ),
        )

    def _on_wave_end(self, s: Stamped, f: Mapping[str, Any]) -> None:
        wave = _whole(f.get("wave"))
        if wave == 0:
            self._close_segment("recon", s.mono)
            found = self.wave(0)
            items = found.items if found else []
            answered = cards.answered(items)
            if answered == len(items):
                self._step(words.RECON_STEP, "ok", words.all_probes_answered(answered))
            else:
                text = words.probes_answered(answered, len(items))
                self._step(words.RECON_STEP, "warn", text)
            return
        self._close_segment("items", s.mono)
        if f.get("probe_phase") and wave is not None:
            for step in self.plans.get(wave, []):
                if step.kind == "probes" and step.ended is None:
                    step.ended = s.mono

    def _on_item_state(self, s: Stamped, f: Mapping[str, Any]) -> None:
        item = self.item(f.get("item_id"))
        state = _text(f.get("state"))
        if item is None or item.landed or not state:
            return
        if state == "building_rc":
            item.builds += 1
            if item.builds >= 2:
                item.rebuild_reason = words.FINDINGS.get(
                    item.finding or "", words.REBUILD_UNKNOWN
                )
                self._say(s, "warn", log.rebuilding(item.item_id, item.finding))
            # The checks of the new build report their own findings.
            item.finding = None
        item.state = state
        item.state_since = s.mono

    def _on_item_detail(self, s: Stamped, f: Mapping[str, Any]) -> None:
        item = self.item(f.get("item_id"))
        if item is not None and not item.landed:
            item.last_detail = _text(f.get("detail")) or None

    def _on_item_change(self, s: Stamped, f: Mapping[str, Any]) -> None:
        change = f.get("change")
        item = self.item(f.get("item_id"))
        if item is not None and isinstance(change, Mapping) and change.get("summary"):
            self._say(s, "built", log.built(item.item_id, _text(change["summary"])))

    def _check(self, kind: str, f: Mapping[str, Any]) -> None:
        """A build check's finding, kept when it forces the rebuild: the
        first such finding of the build. A reference check forces one only
        under `--reference-check refuse` (`items._check_build`); under
        `warn` it is a warning and the item goes on."""
        item = self.item(f.get("item_id"))
        self._see_rc(item, f.get("rc_id"))
        forces = f.get("status") == CHECK_FINDINGS[kind] and (
            kind != "reference_check" or f.get("mode") == "refuse"
        )
        if item is not None and forces and item.finding is None:
            item.finding = kind

    def _on_judge_copy(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self._check("judge_copy", f)

    def _on_generality(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self._check("generality", f)

    def _on_reference_check(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self._check("reference_check", f)

    def _on_queue_drawn(self, s: Stamped, f: Mapping[str, Any]) -> None:
        item = self.item(f.get("item_id"))
        size = _whole(f.get("size"))
        if f.get("item_id") is None:
            step = self._screening(s)
            if step is not None and size:
                step.items_each = max(step.items_each or 0, size)
        elif item is not None and size is not None:
            item.planned = size

    def _on_candidate_budget(self, s: Stamped, f: Mapping[str, Any]) -> None:
        if f.get("item_id") is None:
            step = self._screening(s)
            if step is not None:
                step.candidates += 1
            return
        self._see_rc(self.item(f.get("item_id")), f.get("rc_id"))

    def _on_measure_chunk(self, s: Stamped, f: Mapping[str, Any]) -> None:
        if f.get("item_id") is None:
            self._screening(s)
            return
        item = self.item(f.get("item_id"))
        self._see_rc(item, f.get("rc_id"))
        if item is None:
            return
        for name in ("judged", "fixed", "broke"):
            value = _whole(f.get(name))
            if value is not None:
                setattr(item, name, value)
        planned = _whole(f.get("planned"))
        if planned:
            item.planned = planned
        cells = _list(f.get("cells"))
        for cell in cells:
            if isinstance(cell, (list, tuple)) and len(cell) == 4:
                item.cells.append(_cell(cell[2], cell[3]))

    def _on_measurement(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self._see_rc(self.item(f.get("item_id")), f.get("rc_id"))

    def _on_arm_estimates(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self._see_rc(self.item(f.get("item_id")), f.get("rc_id"))
        rows = _list(f.get("per_eval"))
        for row in rows:
            if isinstance(row, Mapping) and row.get("eval_id") and row.get("eval_name"):
                self.eval_names.setdefault(str(row["eval_id"]), str(row["eval_name"]))

    def _on_arm_results(self, s: Stamped, f: Mapping[str, Any]) -> None:
        item = self.item(f.get("item_id"))
        self._see_rc(item, f.get("rc_id"))
        if item is None:
            return
        rows = results.parsed_rows(f.get("per_eval") or [])
        latency = _valid(LatencyComparison, f.get("latency"))
        cost = _valid(LatencyComparison, f.get("cost"))
        item.train_rows, item.train_latency, item.train_cost = rows, latency, cost
        for row in rows:
            self._add_concern(row.eval_id, row.score_key, row)
        parent = _text(f.get("parent_rc_id"))
        if parent and parent == self.start_rc:
            # Start's rating, cost and latency come from the first arm that
            # measured them against Start (16.6); its pass rates from setup.
            for key, value in results.side_values(
                rows, latency, cost, "baseline"
            ).items():
                self.start_train.setdefault(key, value)
        measured = results.arm_unconfirmed(
            rows,
            latency,
            cost,
            rc_id=item.rc_id,
            item_id=item.item_id,
            generation=self.generation,
        )
        # The arm measured its config in full on train: its numbers replace
        # a screen estimate of the same config.
        keys = {result.key for result in measured}
        self.unconfirmed = [
            result
            for result in self.unconfirmed
            if not (
                result.item_id is None
                and result.rc_id is not None
                and result.rc_id == item.rc_id
                and result.key in keys
            )
        ]
        self.unconfirmed.extend(measured)

    def _on_item_probe(self, s: Stamped, f: Mapping[str, Any]) -> None:
        item = self.item(f.get("item_id"))
        probe = _valid(ProbeResult, f.get("probe"))
        if item is None or probe is None:
            return
        item.probe = probe
        self._log_probe_answer(s, item, probe)

    def _log_probe_answer(
        self, s: Stamped, item: ItemModel, probe: ProbeResult
    ) -> None:
        question = _text((item.details.get("probe") or {}).get("question"))
        verdict = words.probe_answer(probe.answer, probe.drives)
        self.log.append(
            log.verdict_line(
                s.wall, verdict, log.probe_answered(item.item_id, question, verdict)
            )
        )

    def _on_item_outcome(self, s: Stamped, f: Mapping[str, Any]) -> None:
        item = self.item(f.get("item_id"))
        outcome = f.get("outcome")
        if item is None or not isinstance(outcome, ItemOutcome):
            return
        item.outcome = outcome
        self._see_rc(item, outcome.rc_id)
        if outcome.probe is not None and item.probe is None:
            item.probe = outcome.probe
            self._log_probe_answer(s, item, outcome.probe)
        verdict = cards.landed_verdict(self, item)
        if verdict is not None and item.probe is None:
            text = log.item_verdict(
                item.item_id, cards.item_subject(self, item), verdict
            )
            self.log.append(log.verdict_line(s.wall, verdict, text))
        self._check_wave_landed(s.mono)

    def _cut_off(self, s: Stamped, f: Mapping[str, Any], fatal: bool) -> None:
        ids = []
        for item_id in _texts(f.get("item_ids")):
            item = self.item(item_id)
            if item is not None and not item.landed:
                item.cut_off = True
                ids.append(item_id)
        if ids:
            self._say(s, "stop", log.cut_off(ids, fatal))
        self._check_wave_landed(s.mono)

    def _on_deadline_cancel(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self._cut_off(s, f, fatal=False)

    def _on_fatal_cancel(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self._cut_off(s, f, fatal=True)

    def _on_deadline_grace(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self._say(s, "warn", log.grace(_texts(f.get("item_ids")), f.get("grace_secs")))

    def _on_spend(self, s: Stamped, f: Mapping[str, Any]) -> None:
        # The tracker folds the run's money; an item's own spend is kept on
        # the item, which outlives the tracker's last wave.
        # Agent sessions send tokens, not dollars, so this is Kiln money.
        item = self.item(f.get("item_id"))
        cost = _number(f.get("cost_usd"))
        if item is not None and cost is not None:
            item.spend_usd += cost

    # ---- the promotion gate ----

    def _gate_started(self, s: Stamped, wave: int | None) -> None:
        self._set_mode("gate", s.mono)
        self._close_segment("items", s.mono)
        number = wave if wave is not None else (self.cycle_wave or 0)
        self._open_segment("gate", number, s.mono)
        gate = GateModel(number, s.mono)
        self.gate = gate
        found = self.wave(number)
        candidates = 0
        for item in found.items if found else []:
            if (
                item.gate is None
                and cards.landed_verdict(self, item) is words.SUCCESS_GATE
            ):
                item.gate = "at_gate"
                candidates += 1
        self._say(s, "gate", log.gate_started(candidates))

    def _open_gate(self) -> GateModel | None:
        gate = self.gate
        return gate if gate is not None and gate.ended is None else None

    def _on_gate_fills_planned(self, s: Stamped, f: Mapping[str, Any]) -> None:
        gate = self._open_gate()
        if gate is not None:
            gate.rc_ids = _texts(f.get("rc_ids"))
            gate.eval_ids = _texts(f.get("eval_ids"))

    def _on_gate_fill(self, s: Stamped, f: Mapping[str, Any]) -> None:
        gate = self._open_gate()
        if gate is not None:
            key = (_text(f.get("rc_id")), _text(f.get("eval_id")))
            gate.fills[key] = (_whole(f.get("stored")) or 0, _whole(f.get("expected")))

    def _on_val_fill_failed(self, s: Stamped, f: Mapping[str, Any]) -> None:
        gate = self._open_gate()
        if gate is not None:
            gate.fills[(_text(f.get("rc_id")), _text(f.get("eval_id")))] = (0, None)

    def _on_gate_val_tables(self, s: Stamped, f: Mapping[str, Any]) -> None:
        gate = self._open_gate()
        raw = _list(f.get("tables"))
        tables = [
            t for t in (_valid(CandidateValTable, x) for x in raw) if t is not None
        ]
        if gate is not None:
            gate.tables = tables
        for table in tables:
            for row in table.per_eval:
                self._add_concern(row.eval_id, row.score_key, row)
        if self.start_val is None and tables:
            # The first gate's incumbent is Start, measured on every val item.
            values: dict[ConcernKey, float] = {}
            for table in tables:
                for key, value in results.table_values(table, "baseline").items():
                    values.setdefault(key, value)
            self.start_val = values

    def _on_gate_block(self, s: Stamped, f: Mapping[str, Any]) -> None:
        gate = self._open_gate()
        if gate is not None and f.get("rc_id"):
            gate.copied.add(str(f["rc_id"]))

    def _on_promotion(self, s: Stamped, f: Mapping[str, Any]) -> None:
        gate = self._open_gate()
        decision = f.get("decision")
        if gate is not None and isinstance(decision, PromotionDecision):
            gate.decision = decision

    def _on_promotion_blocked(self, s: Stamped, f: Mapping[str, Any]) -> None:
        rc = _text(f.get("winner_rc_id"))
        reasons = _texts(f.get("reasons"))
        gate = self._open_gate()
        if gate is not None:
            gate.blocked[rc] = reasons
            return
        # Before the gate: a model-policy refusal; no gate for this rc.
        item = self.item(self.rc_items.get(rc))
        if item is not None:
            item.gate = "refused"
        if rc:
            self.refused.add(rc)
        self._say(s, "stop", log.blocked(self._who(rc), reasons, before_gate=True))

    def _on_promotion_skipped(self, s: Stamped, f: Mapping[str, Any]) -> None:
        reason = _text(f.get("reason"))
        gate = self._open_gate()
        if gate is not None:
            gate.skipped = reason
            return
        for rc in _texts(f.get("candidate_rc_ids")):
            item = self.item(self.rc_items.get(rc))
            if item is not None:
                item.gate = "pooled"
        self._say(s, "gate", log.gate_skipped(reason))

    def _on_rejection_recorded(self, s: Stamped, f: Mapping[str, Any]) -> None:
        if f.get("rc_id"):
            self.rejected.add(str(f["rc_id"]))

    def _who(self, rc: str) -> str:
        return self.rc_items.get(rc) or self.rc_label(rc)

    def _gate_ended(self, s: Stamped) -> None:
        """The gate's results, settled once it has closed: a `promotion`
        counts only with no `promotion_blocked` for its winner in between
        (audit finding 11)."""
        gate = self._open_gate()
        if gate is None:
            return
        gate.ended = s.mono
        self._set_mode("running", s.mono)
        decision = gate.decision
        winner = decision.winner_rc_id if decision is not None else None
        if winner and winner not in gate.blocked:
            gate.promoted = winner
            self._promote(gate, winner)
        self._close_segment(
            "gate", s.mono, "promotion" if gate.promoted else "no_winner"
        )
        held_on = decision.blocked_on if decision is not None and not winner else None
        found = self.wave(gate.wave)
        for item in found.items if found else []:
            if item.gate != "at_gate" and not (
                item.rc_id and item.rc_id in gate.rc_ids
            ):
                continue
            self._settle(s, gate, item, held_on, winner)
        if gate.skipped is not None:
            self._say(s, "gate", log.gate_skipped(gate.skipped))
        elif decision is not None and not winner:
            self._say(s, "gate", log.NO_WINNER)

    def _promote(self, gate: GateModel, winner: str) -> None:
        self.confirmed_rc = winner
        table = next((t for t in gate.tables if t.rc_id == winner), None)
        self.confirmed = results.table_values(table, "candidate") if table else {}
        self.generation += 1
        self.promotions.append(winner)
        self.names.see(winner)

    def _settle(
        self,
        s: Stamped,
        gate: GateModel,
        item: ItemModel,
        held_on: str | None,
        winner: str | None,
    ) -> None:
        rc = item.rc_id or ""
        who = item.item_id
        if rc and rc == gate.promoted:
            item.gate = "promoted"
            concern, change = self._val_change(gate, item)
            self._say(s, "promo", log.promoted(who, concern, change), bold=True)
        elif rc in gate.copied:
            item.gate = "copied"
            self.refused.add(rc)
            self._say(s, "stop", log.copied(who))
        elif rc in gate.blocked and integrity_block(gate.blocked[rc]):
            item.gate = "blocked"
            self.refused.add(rc)
            self._say(s, "stop", log.blocked(who, gate.blocked[rc], before_gate=False))
        elif rc in gate.blocked:
            # Its val evidence has holes: the loop writes no verdict.
            item.gate = "pooled"
            self._say(s, "gate", log.pooled(who, gate.blocked[rc]))
        elif held_on is not None:
            item.gate = "held"
            self._say(s, "assumed", log.held(who, self.questions.get(held_on)))
        elif (
            gate.decision is None or gate.skipped is not None or winner in gate.blocked
        ):
            # No decision, or a winner refused at apply: no rejection is
            # written, and the candidates stay pooled.
            item.gate = "pooled"
        else:
            item.gate = "rejected"
            if rc:
                self.rejected.add(rc)
            concern, val = self._val_change(gate, item)
            train = self._train_change(item)
            self._say(s, "stop", log.rejected(who, concern, train, val))

    def _headline_concern(self, item: ItemModel) -> Concern | None:
        """What a gate line reports: the objective's own measure under speed
        and cost; under quality, the item's target eval. (The spec's example
        names the target eval; under speed and cost the eval is only held,
        so its change says nothing about why the gate decided.)"""
        if self.objective == "speed":
            return LATENCY
        if self.objective == "cost":
            return COST
        targets = item.targets
        return next(
            (c for c in self.concerns if targets and c.eval_id == targets[0]), None
        )

    @staticmethod
    def _change(concern: Concern, base: dict, cand: dict) -> str | None:
        if concern.key not in base or concern.key not in cand:
            return None
        return words.change_text(concern.kind, cand[concern.key], base[concern.key])

    def _val_change(
        self, gate: GateModel, item: ItemModel
    ) -> tuple[str | None, str | None]:
        """The item's change at this gate, against the incumbent on val."""
        concern = self._headline_concern(item)
        table = next((t for t in gate.tables if t.rc_id == item.rc_id), None)
        if concern is None or table is None:
            return (concern.name if concern else None), None
        return concern.name, self._change(
            concern,
            results.table_values(table, "baseline"),
            results.table_values(table, "candidate"),
        )

    def _train_change(self, item: ItemModel) -> str | None:
        """The item's change on train, against its parent."""
        concern = self._headline_concern(item)
        if concern is None:
            return None

        def side(which: Any) -> dict:
            rows, latency, cost = item.train_rows, item.train_latency, item.train_cost
            return results.side_values(rows, latency, cost, which)

        return self._change(concern, side("baseline"), side("candidate"))

    # ---- the planner's questions, health and memory ----

    def _on_open_questions(self, s: Stamped, f: Mapping[str, Any]) -> None:
        questions = _list(f.get("questions"))
        for entry in questions:
            if not isinstance(entry, Mapping):
                continue
            question = _text(entry.get("question"))
            constraint = _text(entry.get("constraint_id"))
            if constraint and constraint in self.questions:
                continue
            if constraint:
                self.questions[constraint] = question
            self._say(
                s, "assumed", log.assumed(_text(entry.get("default_assumed")), question)
            )

    def _on_tripwire(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self._say(
            s,
            "warn",
            log.tripwire(f.get("wave"), f.get("failed"), f.get("error_class")),
        )

    def _on_eval_job_errors(self, s: Stamped, f: Mapping[str, Any]) -> None:
        errors = _list(f.get("errors"))
        if errors:
            text = log.job_errors(len(errors), f.get("split"), f.get("error_class"))
            self._say(s, "warn", text)

    def _on_dispatch_retry(self, s: Stamped, f: Mapping[str, Any]) -> None:
        text = log.retry(
            f.get("item_id"),
            f.get("source"),
            f.get("error_class"),
            f.get("backoff_secs"),
        )
        self._say(s, "warn", text)

    def _on_provider_quota(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self._say(s, "warn", _text(f.get("line")))

    def _on_memory_write_failed(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self._say(s, "warn", log.memory_write_failed(f.get("what")))

    def _on_memory_written(self, s: Stamped, f: Mapping[str, Any]) -> None:
        text = log.memory_record(
            f.get("record_kind"), f.get("op"), _text(f.get("overview"))
        )
        if text is not None:
            self._say(s, "lesson", text)

    # ---- the end ----

    def _on_exit(self, s: Stamped, f: Mapping[str, Any]) -> None:
        if self.exit_reason is not None:
            return
        self.exit_reason = _text(f.get("reason")) or None
        self.exit_at = s.mono
        self._set_mode("done", s.mono)
        for kind in list(self.open_segments):
            self._close_segment(kind, s.mono)
        for index, step in enumerate(self.start_steps):
            if step.status == "busy":
                self.start_steps[index] = replace(step, status="fail", since=None)
        self._say(s, "run", log.ended(self.exit_reason))

    def _on_report_written(self, s: Stamped, f: Mapping[str, Any]) -> None:
        self.report_path = _text(f.get("path")) or None
        self._say(s, "run", log.report_written(bool(f.get("fallback"))))

    def _on_run_ended(self, s: Stamped, f: Mapping[str, Any]) -> None:
        """The run thread has ended (architecture 5.3): put by the thread
        wrapper, not the loop."""
        self.run_ended = True
