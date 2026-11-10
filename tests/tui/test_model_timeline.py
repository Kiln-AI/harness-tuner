"""The run timeline, the wave estimate and the time and spend views
(functional spec 7.3, 7.4 and 16.15)."""

import pytest
from model_events import Run, fix, probe, row, table, val_row

from kiln_harness_tuner.tui.model.state import plain
from kiln_harness_tuner.tui.model.timeline import axis_ticks, projected_waves

MIN = 60.0


def spans(run: Run) -> list[tuple[str, int, float, float, str | None]]:
    view = run.model.timeline(run.t)
    return [(s.kind, s.wave, s.start / MIN, s.end / MIN, s.mark) for s in view.segments]


def test_segments_from_phase_wave_screen_and_gate_events():
    run = Run().standard_start()
    run.at(3).wave_start(0, probe("r0-1"))
    run.at(5).outcome("r0-1")
    run.emit("wave_end", wave=0)
    run.at(6).plan(1).at(8).planned(1)
    run.at(8.5).emit("candidate_budget", item_id=None, rc_id="rc_s", usd=1.0)
    run.at(10).emit("screening_round", round=1, line="", results=[])
    run.plan(1).at(11).planned(1)
    run.wave_start(1, fix("w1-1"), fix("w1-2"))
    run.at(20).success("w1-1", "rc_a", row())
    run.at(25).outcome("w1-2", "dead_end")
    run.at(26).gate(1, table("rc_a", val_row("e1", 0.6, 0.7)), winner="rc_a", end=False)
    run.at(28).gate_end(1)
    run.at(29).plan(2).at(31).planned(2)
    run.wave_start(2, fix("w2-1"), parent="rc_a")
    run.at(40).success("w2-1", "rc_b", row(parent="rc_a"))
    run.at(41).gate(2, table("rc_b", val_row("e1", 0.7, 0.7)), winner=None)
    run.at(42).emit("phase_start", name="tripwire_backoff", wave=2, label="")
    run.at(47).emit("phase_end", name="tripwire_backoff", wave=2, elapsed_secs=300)
    run.emit("exit", reason="timeout")
    run.emit("phase_start", name="reporting", label="")
    assert spans(run)[-1][0] == "report"
    assert not run.model.timeline(run.t).ended
    run.at(50).emit("phase_end", name="reporting", elapsed_secs=180)
    assert spans(run) == [
        ("setup", 0, 0.0, 6.0, None),
        ("recon", 0, 3.0, 5.0, None),
        ("plan", 1, 6.0, 8.0, None),
        ("screen", 1, 8.0, 10.0, None),
        ("plan", 1, 10.0, 11.0, None),
        ("items", 1, 11.0, 25.0, None),
        ("gate", 1, 26.0, 28.0, "promotion"),
        ("plan", 2, 29.0, 31.0, None),
        ("items", 2, 31.0, 40.0, None),
        ("gate", 2, 41.0, 41.0, "no_winner"),
        ("backoff", 2, 42.0, 47.0, None),
        ("report", 2, 47.0, 50.0, None),
    ]
    view = run.model.timeline(run.t)
    assert (view.ended, view.now / MIN, view.now_label) == (True, 50.0, "ended 15:02")
    assert view.projected == ()


def test_an_open_segment_runs_to_now():
    run = Run().standard_start()
    run.at(6).plan(1)
    view = run.model.timeline(run.at(9).t)
    assert (view.segments[-1].kind, view.segments[-1].end / MIN) == ("plan", 9.0)
    assert (view.ended, view.now_label) == (False, "now 14:21")


def test_no_timeline_before_the_run_starts():
    assert Run().model.timeline(0.0) is None


def project(**overrides):
    args = {
        "wave": 4,
        "cycle_start": 64 * MIN,
        "cycle_spend": 20.0,
        "now": 84 * MIN,
        "spend_now": 21.4,
        "deadline": 180 * MIN,
        "budget": None,
        "cycles": [(27 * MIN, 5.0)],
        "wrap_up": False,
    }
    args.update(overrides)
    return [(start / MIN, wave) for start, wave in projected_waves(**args)]


def test_projection_mirrors_the_loop_rules():
    # One wave every 27 minutes from the current cycle's end. Every wave
    # that starts before the limit is a normal wave, also wave 8 with 8
    # minutes left; the first one at or past the limit is the wrap-up, and
    # it is the last.
    assert project() == [(91.0, 5), (118.0, 6), (145.0, 7), (172.0, 8), (199.0, 9)]
    assert project(deadline=160 * MIN) == [
        (91.0, 5),
        (118.0, 6),
        (145.0, 7),
        (172.0, 8),
    ]
    assert project(deadline=135 * MIN) == [(91.0, 5), (118.0, 6), (145.0, 7)]
    # A wave that starts exactly at the limit is the wrap-up.
    assert project(deadline=118 * MIN) == [(91.0, 5), (118.0, 6)]
    # A wave that overruns its mean: the next starts no earlier than now.
    assert project(now=100 * MIN)[0] == (100.0, 5)


def test_projection_stops_at_the_budget():
    # $20 spent when this cycle began, $5 a cycle: the next wave starts at
    # about $25. With $31 it has $6 left, so it is a normal wave; the one
    # after starts with $1 left, under one cycle's cost, so it is the last.
    assert project(budget=31.0) == [(91.0, 5), (118.0, 6)]
    # With $30 the second one would start at the budget: the run exits.
    assert project(budget=30.0) == [(91.0, 5)]
    assert project(budget=21.0) == []


def test_no_projection_before_a_cycle_ends_or_in_the_wrap_up_wave():
    assert project(cycles=[]) == []
    assert project(wrap_up=True) == []


def test_axis_ticks_every_30_minutes():
    assert axis_ticks(180 * MIN) == (
        (0.0, "0"),
        (1800.0, "30m"),
        (3600.0, "1h"),
        (5400.0, "1h30"),
        (7200.0, "2h"),
        (9000.0, "2h30"),
        (10800.0, "3h"),
    )
    assert [label for _, label in axis_ticks(100 * MIN)] == [
        "0",
        "30m",
        "1h",
        "1h30",
        "1h40",
    ]


def two_wave_run() -> Run:
    run = Run().standard_start()
    run.at(10).plan(1)
    run.emit("spend", source="batch_plan", lane="subscription_equivalent", cost_usd=2.0)
    run.at(12).planned(1).wave_start(1, fix("w1-1"))
    run.emit("spend", item_id="w1-1", source="eval_jobs", lane="provider", cost_usd=3.0)
    run.at(35).outcome("w1-1", "dead_end")
    run.emit("wave_end", wave=1)
    run.at(37).plan(2)
    return run


def test_the_wave_estimate_and_the_projected_waves_agree():
    run = two_wave_run()
    # Wave 1's cycle: 27 minutes and $3.00 (the planner's $2.00 is agent
    # cost, not in the budget).
    assert run.model.cycles == [(27 * MIN, 3.0)]
    clock = run.model.clock(run.at(40).t)
    assert plain(clock.wave) == "wave 2 of ~8"
    view = run.model.timeline(run.t)
    # Waves 3 to 7 start before the 3-hour limit; wave 8 is the wrap-up.
    assert [wave for _start, wave in view.projected] == [3, 4, 5, 6, 7, 8]
    assert [start / MIN for start, _ in view.projected] == [
        64.0,
        91.0,
        118.0,
        145.0,
        172.0,
        199.0,
    ]


def test_the_wave_line_before_wave_1_and_in_the_last_wave():
    run = Run().standard_start()
    assert plain(run.model.clock(run.t).wave) == "before wave 1"
    run.at(10).plan(1)
    assert plain(run.model.clock(run.t).wave) == "wave 1"
    run = two_wave_run()
    run.planned(2).wave_start(2, fix("w2-1"), wrap_up=True)
    clock = run.model.clock(run.t)
    assert (plain(clock.wave), clock.last_wave) == ("wave 2 of 2", True)
    assert run.model.timeline(run.t).projected == ()


def test_planning_the_wrap_up_wave_is_the_last_wave():
    run = two_wave_run()
    run.planned(2).at(40).plan(3, wrap_up=True)
    clock = run.model.clock(run.t)
    assert (plain(clock.wave), clock.last_wave) == ("wave 3 of 3", True)


def test_clock_view_gauges_lanes_and_times():
    run = two_wave_run()
    run.emit(
        "spend",
        source="x",
        lane="subscription_equivalent",
        cost_usd=1.0,
        tokens=12_000,
    )
    clock = run.model.clock(run.at(84).t)
    assert (clock.time.label, clock.time.value, clock.time.limit) == (
        "Time",
        "1h24m",
        "3h00m",
    )
    assert (clock.time.done, clock.time.total) == (84 * MIN, 180 * MIN)
    assert (clock.spend.value, clock.spend.done, clock.spend.total) == (
        "$3.00",
        3.0,
        50.0,
    )
    assert clock.spend.limit == "$50.00"
    assert plain(clock.lanes) == ("Kiln $3.00 · Claude Code ~12k tokens · $2.14/h")
    assert clock.times == "started 14:12 · ends 17:12"


@pytest.mark.parametrize(
    "agent_cli,label", [("codex", "Codex"), ("opencode", "OpenCode")]
)
def test_the_agent_lane_is_named_for_the_agent_cli(agent_cli, label):
    from dataclasses import replace

    from model_events import INFO

    run = Run(replace(INFO, agent_cli=agent_cli)).standard_start()
    assert label in plain(run.model.clock(run.t).lanes)


def test_no_budget_means_no_gauge_end():
    run = Run().standard_start(budget=None)
    clock = run.model.clock(run.t)
    assert (clock.spend.total, clock.spend.limit) == (None, "no budget")


def test_the_clocks_stop_at_the_exit():
    run = two_wave_run()
    run.at(60).emit("exit", reason="budget_exhausted")
    clock = run.model.clock(run.at(70).t)
    assert clock.time.value == "1h00m"
    assert plain(clock.wave) == "1 wave, done"
    assert "/h" not in plain(clock.lanes)
