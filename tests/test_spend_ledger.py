"""Spend is booked once per record.

Run 260924-0335 reported $153 of provider spend against ~$102 of stored
records: the baseline rows setup measured were booked by setup, again as the
parent side of each arm's comparison, and again by every measure-baseline
item that reused them. This reproduces that shape and pins the fix."""

from __future__ import annotations

import pytest
from conftest import fix_item, make_ctx, make_plan, make_result, measure_item
from fake_dispatcher import ok
from fake_kiln import make_run
from test_orchestrator import rc_ok, seed_active_eval, seed_baseline, seed_issue
from test_speed_objective import _project

from kiln_harness_tuner.orchestrator import orchestrate

BASE_ROW_COST = 1.0  # per baseline record (drive 0.75 + judge 0.25)
CAND_ROW_COST = 0.5


def _row(record_id: str, item: str, drive: float, judge: float, score: float):
    return {
        **make_run(item, cost=drive, eval_cost=judge, accuracy=score),
        "id": record_id,
    }


def _provider_spend(ctx, *, item_id=None, source=None) -> float:
    return sum(
        float(f["cost_usd"])
        for k, f in ctx.log.events
        if k == "spend"
        and f.get("lane") == "provider"
        and (item_id is None or f.get("item_id") == item_id)
        and (source is None or f.get("source") == source)
    )


async def test_reused_baseline_records_are_booked_once(tmp_path):
    ctx = make_ctx(gate_agent="auto")
    ctx.measure_baseline_first = True
    ctx.target = ctx.target.model_copy(update={"project_path": str(_project(tmp_path))})
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln)
    # Setup measures the baseline: 8 new records, $1 each.
    kiln.pending[("eval_1", "rc_base", "train")] = [
        _row(f"er_base_{n}", f"train{n}", 0.75, 0.25, 1.0 if n % 2 else 0.0)
        for n in range(8)
    ]
    # The arm: 8 new records, $0.50 each, better on every item.
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        _row(f"er_cand_{n}", f"train{n}", 0.4, 0.1, 1.0) for n in range(8)
    ]
    ctx.dispatch.queue(
        "batch_plan",
        ok(
            make_plan(
                measure_item("w1-2"),  # reuses setup's rows
                measure_item("w1-3"),  # and again
                fix_item("w1-1", promotion_candidate=False),  # parent = those rows
            )
        ),
        ok(make_plan(loop_exit="done")),
    )
    ctx.dispatch.queue("create_experiment_rc", rc_ok("rc_cand"))
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    await orchestrate(ctx, "g", timeout_mins=180)

    assert _provider_spend(ctx, source="baseline_measure") == pytest.approx(
        8 * BASE_ROW_COST
    )
    # Reuse is free — before the fix each of these booked the $8 again.
    assert _provider_spend(ctx, item_id="w1-2") == 0.0
    assert _provider_spend(ctx, item_id="w1-3") == 0.0
    # The arm pays for its own records only — before the fix, $8 + $4.
    assert _provider_spend(ctx, item_id="w1-1") == pytest.approx(8 * CAND_ROW_COST)
    # The run total equals the records created, each once: $8 + $4.
    assert _provider_spend(ctx) == pytest.approx(12.0)


async def test_rows_without_an_id_are_still_counted():
    """A record the server serves without an id cannot be de-duplicated; it
    is counted as before, never silently dropped from the budget."""
    ctx = make_ctx()
    rows = [make_run("a", cost=1.0), make_run("a", cost=1.0)]
    assert ctx.analyzer.fresh_runs_cost(rows) == 2.0
    assert ctx.analyzer.fresh_runs_cost(rows) == 2.0


async def test_the_budget_meter_sees_the_corrected_figure(tmp_path):
    from kiln_harness_tuner.orchestrator import SpendMeter
    from kiln_harness_tuner.wave_log import FanoutLog

    ctx = make_ctx()
    meter = SpendMeter()
    ctx.spend = meter
    ctx.log = FanoutLog(ctx.log, meter)
    rows = [_row("er_1", "a", 1.0, 0.5, 1.0)]
    for _ in range(3):  # three readers of the same record
        ctx.log.event(
            "spend",
            source="eval_jobs",
            lane="provider",
            cost_usd=ctx.analyzer.fresh_runs_cost(rows),
        )
    assert meter.total == pytest.approx(1.5)
