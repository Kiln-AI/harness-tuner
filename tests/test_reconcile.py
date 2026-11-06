"""Spend reconciliation: at every wave boundary and every exit,
the provider spend booked so far is compared with the records this run
created, and the gap is booked. Run 260924-2248 booked $3.98 while its
records cost $6.94: work in flight at the stop never reached the ledger."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import make_ctx
from test_orchestrator import seed_baseline
from test_quarantine import TASK, Project

from kiln_harness_tuner.orchestrator import SpendMeter, orchestrate
from kiln_harness_tuner.reconcile import reconcile, records_spend
from kiln_harness_tuner.wave_log import FanoutLog

T0 = datetime(2026, 9, 25, 9, 0, 0)
KEY = ("e1", "ec1", "rc_cand")


def _at(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class Disk:
    def __init__(self, tmp_path: Path):
        self.p = Project(tmp_path / "proj")
        self.p.eval("e1", "a")
        self.root = self.p.root

    def eval_run(self, run_id, minutes, *, rc="rc_cand", judge=0.1, scored=None, **kw):
        folder = next((self.p.task / "evals").glob("e1 - *"))
        _write(
            folder / "configs" / "ec1 - judge" / "runs" / run_id / "eval_run.kiln",
            {
                "id": run_id,
                "created_at": _at(minutes),
                "task_run_config_id": rc,
                "eval_usage": {"cost": judge},
                "task_run_usage": None,  # as on disk
                "scored_run_id": scored,
                **kw,
            },
        )

    def task_run(self, run_id, minutes, cost, *, tags=()):
        _write(
            self.p.task / "runs" / run_id / "task_run.kiln",
            {
                "id": run_id,
                "created_at": _at(minutes),
                "usage": {"cost": cost},
                "tags": list(tags),
            },
        )


def _found(disk, jobs=None):
    return records_spend(disk.root, TASK, T0, {KEY: T0} if jobs is None else jobs)


class TestAttribution:
    def test_the_records_of_this_run_and_nothing_else(self, tmp_path):
        d = Disk(tmp_path)
        # An eval run of a job this run started, with its scored task run.
        d.eval_run("er1", 5, judge=0.10, scored="tr1")
        d.task_run("tr1", 5, 0.20)
        # Two eval runs scoring the same task run: the task run once.
        d.eval_run("er2", 6, judge=0.10, scored="tr2")
        d.eval_run("er3", 7, judge=0.10, scored="tr2")
        d.task_run("tr2", 6, 0.20)
        # A timing drive this run made.
        d.task_run(
            "drive",
            8,
            0.05,
            tags=["kiln_harness_tuner_latency", "kiln_harness_tuner_latency_w1"],
        )
        found = _found(d)
        assert set(found.eval_runs) == {"er1", "er2", "er3"}
        assert set(found.task_runs) == {"tr1", "tr2", "drive"}
        assert found.total == pytest.approx(0.30 + 0.40 + 0.05)

    def test_records_outside_the_run_are_ignored(self, tmp_path):
        d = Disk(tmp_path)
        d.eval_run("reused", -60, scored="tr_old")  # a stored row the run reused
        d.task_run("tr_old", -60, 0.20)
        d.eval_run("other_rc", 5, rc="rc_nobody_measured")
        d.task_run("untagged", 5, 0.30)  # someone else's drive
        d.task_run("old_drive", -60, 0.05, tags=["kiln_harness_tuner_latency"])
        d.eval_run("no_time", 5, created_at="yesterday-ish")
        assert _found(d).total == 0.0

    def test_an_eval_run_before_the_first_job_on_its_config_is_not_ours(self, tmp_path):
        d = Disk(tmp_path)
        d.eval_run("early", 1)  # 1 min in; this run's job started at 30 min
        d.eval_run("late", 31)
        found = _found(d, {KEY: T0 + timedelta(minutes=30)})
        assert set(found.eval_runs) == {"late"}

    def test_no_scored_task_run_on_disk_uses_the_eval_runs_own_task_cost(
        self, tmp_path
    ):
        d = Disk(tmp_path)
        d.eval_run("er1", 5, judge=0.1, scored="gone", task_run_usage={"cost": 0.07})
        assert _found(d).total == pytest.approx(0.17)


def _ctx(tmp_path, *, booked=0.0):
    """A ctx whose log feeds a SpendMeter, as the CLI wires it."""
    d = Disk(tmp_path)
    ctx = make_ctx()
    ctx.target = ctx.target.model_copy(
        update={"project_path": str(d.root), "task_id": TASK}
    )
    ctx.analyzer.run_started_at = T0
    ctx.analyzer.started_jobs = {KEY: T0}
    meter = SpendMeter()
    events = ctx.log
    ctx.log = FanoutLog(events, meter)
    ctx.spend = meter
    if booked:
        ctx.log.event("spend", source="eval_jobs", lane="provider", cost_usd=booked)
    return ctx, d, events


class TestReconcile:
    def test_the_gap_is_booked_once_and_said_plainly(self, tmp_path):
        ctx, d, events = _ctx(tmp_path, booked=0.30)
        d.eval_run("er1", 5, judge=0.10, scored="tr1")
        d.task_run("tr1", 5, 0.20)
        d.eval_run("er2", 6, judge=0.10, scored="tr2")  # in flight at the stop
        d.task_run("tr2", 6, 0.20)
        result = reconcile(ctx, when="exit")
        assert result.gap == pytest.approx(0.30)
        (line,) = [f["line"] for k, f in events.events if k == "spend_reconciled"]
        assert line == (
            "spend reconciled: booked $0.30, found $0.60 in records"
            " (+$0.30 in-flight work)"
        )
        booked = [f for k, f in events.events if k == "spend"]
        assert booked[-1]["source"] == "reconciliation"
        assert booked[-1]["lane"] == "provider"
        # Again: nothing new to book — the reconciliation itself is booked.
        assert reconcile(ctx, when="exit").gap == 0.0
        assert ctx.spend.provider_total == pytest.approx(0.60)

    def test_subscription_spend_is_not_compared(self, tmp_path):
        ctx, d, events = _ctx(tmp_path)
        ctx.log.event(
            "spend", source="batch_plan", lane="subscription_equivalent", cost_usd=5.0
        )
        d.task_run("drive", 8, 0.05, tags=["kiln_harness_tuner_latency"])
        assert reconcile(ctx, when="exit").gap == pytest.approx(0.05)

    def test_quiet_at_a_wave_boundary_when_nothing_is_missing(self, tmp_path):
        ctx, _d, events = _ctx(tmp_path)
        reconcile(ctx, when="before wave 2", quiet=True)
        assert not [k for k, _ in events.events if k == "spend_reconciled"]


class TestInTheLoop:
    async def test_an_interrupted_run_books_its_in_flight_work_before_it_exits(
        self, tmp_path
    ):
        ctx, d, events = _ctx(tmp_path)
        seed_baseline(ctx.kiln)

        def interrupted(req):
            # SIGTERM cancels the run while this run's eval job (started in
            # setup, never read back) is still writing its records.
            d.eval_run("er1", 5, judge=0.10, scored="tr1")
            d.task_run("tr1", 5, 0.20)
            raise asyncio.CancelledError

        ctx.dispatch.queue("batch_plan", interrupted)
        with pytest.raises(asyncio.CancelledError):
            await orchestrate(ctx, "g", timeout_mins=180)
        kinds = [k for k, _ in events.events]
        assert kinds.index("spend_reconciled") < kinds.index("exit")
        (line,) = [f["line"] for k, f in events.events if k == "spend_reconciled"]
        assert line.endswith("found $0.30 in records (+$0.30 in-flight work)")
        (exit_event,) = [f for k, f in events.events if k == "exit"]
        assert exit_event["reason"] == "interrupted"

    async def test_the_budget_reads_the_reconciled_figure(self, tmp_path):
        ctx, d, events = _ctx(tmp_path, booked=1.0)
        seed_baseline(ctx.kiln)
        ctx.budget_usd = 2.0
        d.task_run("booked", 7, 1.00, tags=["kiln_harness_tuner_latency"])
        d.task_run("in_flight", 8, 1.50, tags=["kiln_harness_tuner_latency"])
        result = await orchestrate(ctx, "g", timeout_mins=180)
        assert result == "budget_exhausted"
        assert ctx.dispatch.calls_to("batch_plan") == []
        (exit_event,) = [f for k, f in events.events if k == "exit"]
        assert exit_event["spent_usd"] == pytest.approx(2.5)


def test_a_job_time_before_the_run_never_widens_the_window(tmp_path):
    d = Disk(tmp_path)
    d.eval_run("before", -60)
    assert _found(d, {KEY: T0 - timedelta(hours=2)}).total == 0.0


async def test_every_eval_job_the_run_starts_is_registered_first_time_kept():
    ctx = make_ctx()
    ctx.kiln.evals["e1"] = {"id": "e1"}
    await ctx.analyzer._start_job("e1", "ec1", "rc_cand", "train")
    first = ctx.analyzer.started_jobs[KEY]
    await ctx.analyzer._start_job("e1", "ec1", "rc_cand", "val")
    assert ctx.analyzer.started_jobs == {KEY: first}


def test_the_line_is_printed_by_both_displays():
    from kiln_harness_tuner.ui import SETUP_LINE_EVENTS

    assert "spend_reconciled" in SETUP_LINE_EVENTS
