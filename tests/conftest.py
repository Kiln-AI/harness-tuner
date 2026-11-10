"""Shared test helpers: the loop fixtures (items + orchestrator), the
`baseline_record` builder the memory-format and setup-phase suites both
seed their singleton with, and a stream that says whether it is a terminal."""

import io
import logging
from datetime import datetime

import pytest
from fake_dispatcher import FakeDispatcher
from fake_kiln import FakeKiln

from kiln_harness_tuner.analysis import Analyzer
from kiln_harness_tuner.memory import MemoryWriter
from kiln_harness_tuner.memory_schema import baseline_rc_tag
from kiln_harness_tuner.orchestrator import Ctx, EventLog
from kiln_harness_tuner.schemas import (
    BatchPlan,
    EvalOnlyItem,
    EvalTarget,
    ExperimentItem,
    ExperimentResult,
    FixWork,
    MeasureIssueBaselineItem,
    TargetBlock,
)
from kiln_harness_tuner.status import RunStatusTracker

FIXED_NOW = datetime(2026, 7, 22, 14, 0)
TARGET = TargetBlock(
    project_path="/tmp/proj", project_id="p1", task_id="t1", kiln_host="http://h"
)


class TtyStream(io.StringIO):
    """A text stream whose `isatty()` answers `tty`: a stand-in for stdin or
    stdout in the tests of what a terminal changes."""

    def __init__(self, tty: bool):
        super().__init__()
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


def baseline_record(
    kiln: FakeKiln,
    overview: str,
    content: str | None = None,
    rc_id: str | None = "rc_base",
    memory_id: str | None = None,
) -> str:
    """The task-scope `baseline` singleton in the fakes' memory store —
    shared by the memory-format and setup-phase suites (both read the same
    record through different doors).

    `rc_id` becomes the `rc_<id>` tag the loop reads the baseline from
    from the tag; `None` seeds a record carrying no rc tag at all, which
    the loop treats as fatal."""
    tags = ["baseline"] if rc_id is None else ["baseline", baseline_rc_tag(rc_id)]
    return kiln.add_memory(
        overview, "task::t1", tags, content=content, memory_id=memory_id
    )


class RecordingLog(EventLog):
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def event(self, kind: str, **fields: object) -> None:
        self.events.append((kind, fields))

    def reasons(self) -> list[str]:
        return [f["reason"] for k, f in self.events if k == "exit"]


class ManualClock:
    """monotonic() stand-in whose reading is set directly (`clock.t = 130.0`)
    — for tests that advance time between assertions rather than scheduling
    it up front."""

    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def make_tracker(clock: ManualClock | None = None) -> RunStatusTracker:
    """A tracker on a settable clock — everything it computes at snapshot
    time (item/phase elapsed, time left, the run's own elapsed) has to be
    deterministic for both the status folds and the UI renders."""
    return RunStatusTracker(
        project_id="p1",
        task_id="t1",
        guidance="focus evals X",
        clock=clock or ManualClock(),
    )


class FakeClock:
    """monotonic() stand-in: pops scheduled values, then sticks on the last."""

    def __init__(self, *values: float):
        self._values = list(values) or [0.0]

    def __call__(self) -> float:
        if len(self._values) > 1:
            return self._values.pop(0)
        return self._values[0]


def make_ctx(
    kiln: FakeKiln | None = None,
    dispatcher: FakeDispatcher | None = None,
    clock: FakeClock | None = None,
    gate_agent: str = "always",
    eval_building: bool = False,
) -> Ctx:
    """`gate_agent` defaults to "always" here (production: "auto") so the
    many tests that pin the promote AGENT's mechanics keep scripting it; the
    code-rule path has its own tests under `gate_agent="auto"`.
    `eval_building` defaults off, as in production
    (`--experimental-eval-building`)."""
    kiln = kiln if kiln is not None else FakeKiln()
    # Two SEPARATE recorders: ctx.sleeps sees only the loop's
    # own backoffs (dispatch ladders, tripwire, promotion writes);
    # ctx.analyzer_sleeps sees the eval-job retry sleeps — assertions on
    # magic backoff values never entangle.
    sleeps: list[float] = []
    analyzer_sleeps: list[float] = []

    async def record_sleep(secs: float) -> None:
        sleeps.append(secs)

    async def record_analyzer_sleep(secs: float) -> None:
        analyzer_sleeps.append(secs)

    ctx = Ctx(
        dispatch=dispatcher if dispatcher is not None else FakeDispatcher(),
        kiln=kiln,
        memory=MemoryWriter(kiln, project_id="p1", task_id="t1", now=lambda: FIXED_NOW),
        analyzer=Analyzer(
            kiln, project_id="p1", task_id="t1", sleep=record_analyzer_sleep
        ),
        target=TARGET,
        log=RecordingLog(),
        clock=clock if clock is not None else FakeClock(),
        sleep=record_sleep,
        gate_agent=gate_agent,
        eval_building=eval_building,
    )
    ctx.sleeps = sleeps
    ctx.analyzer_sleeps = analyzer_sleeps
    # Same wiring as cli.py: the analyzer logs `eval_job_errors`
    # into the shared log once it exists.
    ctx.analyzer.log = ctx.log
    return ctx


def fix_item(
    item_id: str = "w1-1",
    *,
    promotion_candidate: bool = True,
    needs_eval: bool = False,
    eval_id: str | None = "eval_1",
    issue_memory_id: str = "issue_1",
    parent_rc_id: str | None = None,
    residual_gate: bool = True,
) -> ExperimentItem:
    return ExperimentItem(
        id=item_id,
        type="experiment",
        evaluation_target=[EvalTarget(eval_id="eval_1")],
        promotion_candidate=promotion_candidate,
        work=FixWork(
            issue_memory_id=issue_memory_id,
            hypothesis="retry-skill for 429s",
            technique="prompt",
            predicted_effect="+10% pass rate",
            needs_eval=needs_eval,
            eval_id=eval_id,
            parent_rc_id=parent_rc_id,
            residual_gate=residual_gate,
            residual_gate_reason="" if residual_gate else "a test of other stages",
        ),
    )


def eval_only_item(
    item_id: str = "w1-1", issue_memory_id: str = "issue_1"
) -> EvalOnlyItem:
    return EvalOnlyItem(
        id=item_id,
        type="eval_only",
        issue_memory_id=issue_memory_id,
        reason="cheap-kill check",
    )


def measure_item(
    item_id: str = "w1-1",
    *,
    issue_memory_id: str = "issue_1",
    eval_id: str = "eval_1",
    scores: list[str] | None = None,
) -> MeasureIssueBaselineItem:
    return MeasureIssueBaselineItem(
        id=item_id,
        type="measure_issue_baseline",
        issue_memory_id=issue_memory_id,
        eval_id=eval_id,
        scores=scores,
        reason="p1 issue, eval already exists — settle it cheap",
    )


def make_plan(
    *items,
    rejected_summary: str = "nothing else worth it",
    loop_exit: str | None = None,
    open_questions=(),
) -> BatchPlan:
    return BatchPlan(
        items=list(items),
        rejected_summary=rejected_summary,
        open_questions=list(open_questions),
        loop_exit=loop_exit,
    )


def make_result(
    successful: bool = True, overview: str | None = None, **kwargs
) -> ExperimentResult:
    return ExperimentResult(
        successful=successful,
        findings="findings",
        memory_overview=overview or "EXP 0722-w1-1: retry-skill — +18%, n=2, p=.03",
        memory_content="result: +18%",
        **kwargs,
    )


@pytest.fixture(autouse=True)
def isolate_root_logger():
    """`kiln_harness_tuner.logbook` attaches a FileHandler to the ROOT logger, so any
    test that reaches `cli.main` (or calls `configure_run_logging`) would otherwise
    leave one behind pointing into a torn-down `tmp_path` — every later test in the
    session then logs into a deleted file. Restore what we found."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.handlers = handlers
    root.setLevel(level)


@pytest.fixture(autouse=True)
def no_agent_cli_check(monkeypatch):
    """`cli._run` sends one prompt through the real agent CLI before a run
    starts. The suite never spawns one: every test that reaches `cli.main`
    gets a check that passes. `tests/test_agent_cli.py` tests the check
    itself, through its own import of it."""

    async def passes(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr("kiln_harness_tuner.cli.check_agent_cli", passes)


@pytest.fixture(autouse=True)
def state_dir(monkeypatch, tmp_path):
    """Every test's state folder is its own: a run that reaches `cli.main`
    writes its runs, reports and agents folder here, never in the real
    `~/.kiln_ai/harness_tuner`."""
    state = tmp_path / "state"
    monkeypatch.setenv("KILN_HARNESS_TUNER_HOME", str(state))
    return state
