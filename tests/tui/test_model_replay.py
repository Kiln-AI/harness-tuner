"""The run model on the events of whole loop runs from the test suite: the
real emitters, in their real order."""

from datetime import datetime, timedelta

from model_events import INFO
from test_loop_events import quality_run
from test_speed_objective import _speed_ctx

from kiln_harness_tuner.orchestrator import orchestrate
from kiln_harness_tuner.tui.model.events import Stamped
from kiln_harness_tuner.tui.model.fold import RunModel
from kiln_harness_tuner.tui.model.names import RcNames
from kiln_harness_tuner.tui.model.state import RunInfo, plain

WALL = datetime(2026, 11, 9, 14, 12)


def replay(events, objective: str) -> tuple[RunModel, float]:
    """Each event one second after the one before, from the run's start on
    its own clock."""
    run_start = next(fields for kind, fields in events if kind == "run_start")
    zero = run_start["deadline"] - run_start["timeout_mins"] * 60
    info = RunInfo(**{**INFO.__dict__, "objective": objective})
    model = RunModel(info, started=zero - 30, names=RcNames({"rc_base": "Baseline"}))
    for n, (kind, fields) in enumerate(events):
        model.apply(Stamped(kind, fields, zero + n, WALL + timedelta(seconds=n)))
    return model, zero + len(events)


def every_view(model: RunModel, now: float) -> None:
    model.title()
    model.goal()
    model.clock(now)
    model.timeline(now)
    model.results()
    model.experiments(now)
    model.compare_url()


async def test_a_quality_run_rejected_at_val(tmp_path):
    ctx = await quality_run(tmp_path)
    model, now = replay(ctx.log.events, "quality")
    every_view(model, now)
    item = model.items["w1-1"]
    assert (item.rc_id, item.judged, item.planned) == ("rc_cand", 30, 30)
    assert item.cells.count("fixed") == 1 and item.cells.count("held") == 29
    assert item.gate == "rejected"
    assert model.confirmed_rc is None
    assert "rc_cand" in model.rejected
    log = [line.text for line in model.log if not line.divider]
    assert "w1-1 Eval eval_1: success, goes to the gate" in log
    assert "w1-1 rejected at val · Eval eval_1 train +3.3, val +0.0" in log
    assert log[-1] == "Run ended: Nothing left to try: done"
    view = model.experiments(now)
    assert view.mode == "done"
    assert plain(view.done.best) == (
        "Best Confirmed  no promotion: the run kept its starting point"
    )
    assert view.done.next_step.startswith("Next: run your test split on Baseline.")
    kinds = [s.kind for s in model.timeline(now).segments]
    assert kinds == ["setup", "plan", "items", "gate", "plan"]


async def test_a_speed_run_that_promotes(tmp_path):
    ctx = _speed_ctx(tmp_path, cand_ms=400.0)
    await orchestrate(ctx, "go fast", timeout_mins=180)
    model, now = replay(ctx.log.events, "speed")
    every_view(model, now)
    assert model.confirmed_rc == "rc_cand"
    assert model.items["w1-1"].gate == "promoted"
    gate = [s for s in model.timeline(now).segments if s.kind == "gate"]
    assert [s.mark for s in gate] == ["promotion"]
    latency = next(c for c in model.results().cards if c.title == "Latency")
    # Start under speed is the baseline's own timing, then the gate's val pass.
    assert latency.start is not None and latency.confirmed is not None
    assert latency.confirmed.change_tone == "success"
    # Under speed, the gate line reports latency, the objective's measure.
    promoted = [line.text for line in model.log if "promoted" in line.text]
    assert len(promoted) == 1 and promoted[0].startswith("w1-1 promoted · Latency −")
    assert model.experiments(now).done.totals[2] == ("1", "promotion")
