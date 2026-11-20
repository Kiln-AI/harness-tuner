"""Holes in the val evidence at the promotion gate: a candidate whose val
measurement is incomplete (a failed fill, coverage under the floor, an
incomplete speed or cost pass) is neither promoted nor rejected. It stays
pooled for a later wave. Only a candidate judged on complete evidence gets
`— REJECTED AT VAL`. An rc that has holes at `HOLED_GATE_LIMIT` gates of one
run is rejected, so that it cannot come back to the gate in every wave."""

from __future__ import annotations

from conftest import fix_item, make_ctx, make_plan, make_result
from fake_dispatcher import ok
from test_orchestrator import (
    decision,
    rc_ok,
    seed_active_eval,
    seed_baseline,
    seed_issue,
    seed_train,
    seed_val,
)

from kiln_harness_tuner import orchestrator
from kiln_harness_tuner.latency import compare_latencies
from kiln_harness_tuner.orchestrator import (
    HOLED_GATE_LIMIT,
    POOLED_REASON,
    maybe_promote,
    orchestrate,
)
from kiln_harness_tuner.report import render_fallback_report
from kiln_harness_tuner.schemas import ItemOutcome, LatencyComparison
from kiln_harness_tuner.status import RunStatusTracker

STAMP = " — REJECTED AT VAL 07-22"


def _always_fails(kiln, rc_id: str) -> None:
    """Every val fill job of `rc_id` fails: a provider error, every time."""
    kiln.job_behaviors[("eval_1", rc_id, "val")] = ["fail"] * 20


def _scenario(ctx, n: int, *, holed: tuple[str, ...] = ()) -> list[str]:
    """One wave of `n` successful candidates rc_w1-1 … rc_w1-n; wave 2
    exits. The rc ids in `holed` have no stored val row and a val fill that
    fails."""
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    rcs = [f"rc_w1-{i}" for i in range(1, n + 1)]
    items = []
    for i, rc in enumerate(rcs, start=1):
        seed_issue(kiln, f"issue_{i}")
        seed_train(kiln, rc)
        items.append(fix_item(f"w1-{i}", issue_memory_id=f"issue_{i}"))
    seed_val(kiln, "rc_base", *(rc for rc in rcs if rc not in holed))
    for rc in holed:
        _always_fails(kiln, rc)
    d = ctx.dispatch
    d.queue("batch_plan", ok(make_plan(*items)), ok(make_plan(loop_exit="done")))
    d.queue("create_experiment_rc", *([lambda req: rc_ok(f"rc_{req.item.id}")] * n))
    d.queue("analyze_experiment_results", *([ok(make_result())] * n))
    return rcs


def _exp_mem(ctx, rc_id: str) -> str:
    (outcome,) = [
        f["outcome"]
        for k, f in ctx.log.events
        if k == "item_outcome" and f["outcome"].rc_id == rc_id
    ]
    return outcome.experiment_memory_id


def _stamped(ctx, rc_id: str) -> bool:
    return ctx.kiln.memories[_exp_mem(ctx, rc_id)]["overview"].endswith(STAMP)


def _pooled_events(ctx) -> list[str]:
    return [
        f["winner_rc_id"]
        for k, f in ctx.log.events
        if k == "promotion_blocked" and f.get("pooled")
    ]


async def test_no_winner_stamps_only_the_candidate_with_complete_evidence():
    # The case of the finding: the promotion agent picks no winner, and one
    # of the two candidates has a failed val fill. Before the fix, both were
    # stamped, and the holed one was lost for good on a provider error.
    ctx = make_ctx()
    _scenario(ctx, 2, holed=("rc_w1-2",))
    ctx.dispatch.queue("promote_best", decision(None, rationale="neither beat val"))

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    assert _stamped(ctx, "rc_w1-1")
    assert not _stamped(ctx, "rc_w1-2")
    content = ctx.kiln.memories[_exp_mem(ctx, "rc_w1-2")]["content"] or ""
    assert "rejected at the val gate" not in content
    recorded = [f["rc_id"] for k, f in ctx.log.events if k == "rejection_recorded"]
    assert recorded == ["rc_w1-1"]
    assert _pooled_events(ctx) == ["rc_w1-2"]
    (blocked,) = [f for k, f in ctx.log.events if k == "promotion_blocked"]
    assert blocked["reasons"] == [POOLED_REASON]


async def test_a_winner_still_stamps_the_losers_with_complete_evidence():
    ctx = make_ctx()
    _scenario(ctx, 3, holed=("rc_w1-3",))
    ctx.dispatch.queue("promote_best", decision("rc_w1-1"))

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    assert "rc_rc_w1-1" in ctx.kiln.memories["mem_baseline"]["tags"]
    assert _stamped(ctx, "rc_w1-2")
    assert ctx.kiln.memories[_exp_mem(ctx, "rc_w1-2")]["content"].endswith(
        "passed over: not promoted; rc rc_w1-1 was promoted instead."
    )
    assert not _stamped(ctx, "rc_w1-3")
    assert _pooled_events(ctx) == ["rc_w1-3"]


async def test_the_pooled_candidate_is_not_reported_as_rejected():
    ctx = make_ctx()
    _scenario(ctx, 2, holed=("rc_w1-2",))
    ctx.dispatch.queue("promote_best", decision(None, rationale="neither beat val"))

    await orchestrate(ctx, "", timeout_mins=180)

    tracker = RunStatusTracker(project_id="p", task_id="t", guidance="g")
    for kind, fields in ctx.log.events:
        tracker.event(kind, **fields)

    wave = tracker.snapshot().waves[0]
    assert wave.pooled == ["rc_w1-2"]
    # A pooled candidate is not a refused winner.
    assert wave.promotion_skipped is None
    text = render_fallback_report(tracker.snapshot())
    assert "not judged, val evidence incomplete (stays pooled): rc_w1-2" in text


# ---- the single-candidate rule, and the bound ----


def _resubmit(item_id: str, rc_id: str):
    item = fix_item(item_id)
    item.work.existing_rc_id = rc_id
    return item


async def test_a_single_holed_candidate_is_pooled_then_rejected_at_the_bound():
    # The code rule's path (one candidate, `--gate-agent auto`). Its val fill
    # fails at every gate. Code does not refuse a resubmission of the pooled
    # rc, so without a bound it could come back in every wave.
    assert HOLED_GATE_LIMIT == 2
    ctx = make_ctx(gate_agent="auto")
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln)
    seed_train(kiln, "rc_cand")
    seed_val(kiln, "rc_base")
    _always_fails(kiln, "rc_cand")
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"))),
        ok(make_plan(_resubmit("w2-1", "rc_cand"))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()), ok(make_result()))

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    assert d.calls_to("promote_best") == []
    records = [
        f["outcome"].experiment_memory_id
        for k, f in ctx.log.events
        if k == "item_outcome"
    ]
    assert len(set(records)) == 2
    # The second holed gate rejects the rc: both of its records say so.
    recorded = [f for k, f in ctx.log.events if k == "rejection_recorded"]
    assert sorted(f["exp_mem_id"] for f in recorded) == sorted(records)
    assert {f["reason"] for f in recorded} == {"val incomplete"}
    for mem_id in records:
        record = kiln.memories[mem_id]
        assert record["overview"].endswith(STAMP)
        assert "val incomplete again" in record["content"]
    assert ctx.holed_gates == {"rc_cand": records}


async def test_one_holed_gate_is_under_the_bound():
    ctx = make_ctx(gate_agent="auto")
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln)
    seed_train(kiln, "rc_cand")
    seed_val(kiln, "rc_base")
    _always_fails(kiln, "rc_cand")
    d = ctx.dispatch
    d.queue(
        "batch_plan", ok(make_plan(fix_item("w1-1"))), ok(make_plan(loop_exit="done"))
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    assert not [k for k, _ in ctx.log.events if k == "rejection_recorded"]
    assert not any(STAMP in m["overview"] for m in kiln.memories.values())


# ---- the speed and cost objective ----


def _paired(ratio: float, *, incomplete: bool = False) -> LatencyComparison:
    base = {f"i{k}": 1000.0 + k for k in range(12)}
    comparison = compare_latencies(
        base,
        {k: v * ratio for k, v in base.items()},
        method="paired_drive",
        split="val",
    )
    if incomplete:
        comparison = comparison.model_copy(
            update={"incomplete": True, "clearly_faster": False, "planned_items": 30}
        )
    return comparison


async def test_an_incomplete_speed_pass_is_a_hole(monkeypatch):
    # Under speed the fills are complete, but the paired drive pass of one
    # candidate is incomplete: no verdict on its speed. The other candidate
    # was measured and is not faster: it is judged, and rejected.
    ctx = make_ctx(gate_agent="auto")
    ctx.objective = "speed"
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_val(kiln, "rc_base", "rc_a", "rc_b")
    speeds = {"rc_a": _paired(0.5, incomplete=True), "rc_b": _paired(1.0)}

    async def speed_pass(ctx, tables, baseline_rc_id):
        for table in tables:
            table.latency = speeds[table.rc_id]

    monkeypatch.setattr(orchestrator, "_speed_val_pass", speed_pass)
    outcomes = []
    for rc in ("rc_a", "rc_b"):
        mem = kiln.add_memory(
            f"EXP 0722-{rc}: faster model — +0.0pp, p=1",
            "task::t1",
            ["experiment"],
        )
        outcomes.append(
            ItemOutcome(
                item=fix_item(rc),
                status="completed",
                rc_id=rc,
                experiment_memory_id=mem,
                result=make_result(),
                infra_error=None,
            )
        )
    record = await ctx.memory.require_baseline_record()

    promoted = await maybe_promote(ctx, outcomes, record, wave=1)

    assert promoted is not None and promoted.winner_rc_id is None
    by_rc = {o.rc_id: kiln.memories[o.experiment_memory_id] for o in outcomes}
    assert not by_rc["rc_a"]["overview"].endswith(STAMP)
    assert by_rc["rc_b"]["overview"].endswith(STAMP)
    assert _pooled_events(ctx) == ["rc_a"]
    recorded = [f["rc_id"] for k, f in ctx.log.events if k == "rejection_recorded"]
    assert recorded == ["rc_b"]


async def test_an_unmeasured_speed_pass_is_a_hole(monkeypatch):
    ctx = make_ctx(gate_agent="auto")
    ctx.objective = "cost"
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_val(kiln, "rc_base", "rc_a")

    async def no_pass(ctx, tables, baseline_rc_id):
        return None  # nothing drivable: every comparison stays None

    monkeypatch.setattr(orchestrator, "_speed_val_pass", no_pass)
    mem = kiln.add_memory(
        "EXP 0722-w1-1: cheaper — +0.0pp, p=1", "task::t1", ["experiment"]
    )
    outcome = ItemOutcome(
        item=fix_item("w1-1"),
        status="completed",
        rc_id="rc_a",
        experiment_memory_id=mem,
        result=make_result(),
        infra_error=None,
    )
    record = await ctx.memory.require_baseline_record()

    await maybe_promote(ctx, [outcome], record, wave=1)

    assert not kiln.memories[mem]["overview"].endswith(STAMP)
    assert _pooled_events(ctx) == ["rc_a"]


def test_val_holes_leaves_out_a_quote_block_and_a_measured_verdict():
    ctx = make_ctx()
    ctx.objective = "speed"
    from kiln_harness_tuner.schemas import CandidateValTable, GateBlock

    def table(rc_id, latency):
        return CandidateValTable(
            rc_id=rc_id,
            experiment_memory_id=f"mem_{rc_id}",
            per_eval=[],
            ineligible=True,
            latency=latency,
        )

    tables = [
        table("rc_fill", _paired(0.5)),  # a fill hole
        table("rc_quote", _paired(0.5)),  # a quote block, and a fill hole
        table("rc_slow", _paired(1.0)),  # measured, not faster: a verdict
        table("rc_short", _paired(0.5, incomplete=True)),
        table("rc_none", None),
    ]
    ctx.gate_blocks.append(GateBlock(rc_id="rc_quote", reasons=["quotes"]))
    holed = orchestrator._val_holes(ctx, tables, {"rc_fill", "rc_quote"})
    assert holed == {"rc_fill", "rc_short", "rc_none"}
    # Under quality the speed pass is not read.
    ctx.objective = "quality"
    assert orchestrator._val_holes(ctx, tables, {"rc_fill"}) == {"rc_fill"}
