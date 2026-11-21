"""Holes in the val evidence at the promotion gate: a candidate whose val
measurement is incomplete (a failed fill, coverage under the floor, an
incomplete speed or cost pass) is neither promoted nor rejected. It stays
pooled for a later wave. Only a candidate judged on complete evidence gets
`— REJECTED AT VAL`. An rc that has holes at `HOLED_GATE_LIMIT` gates of one
run is rejected, so that it cannot come back to the gate in every wave."""

from __future__ import annotations

import pytest
from conftest import fix_item, make_ctx, make_plan, make_result
from fake_dispatcher import infra, ok
from fake_kiln import make_run
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
from kiln_harness_tuner.analysis import JobHealth, MeasurementFailed
from kiln_harness_tuner.latency import compare_latencies
from kiln_harness_tuner.orchestrator import (
    HOLED_GATE_LIMIT,
    POOLED_REASON,
    maybe_promote,
    orchestrate,
)
from kiln_harness_tuner.report import render_fallback_report
from kiln_harness_tuner.schemas import ErrorInfo, ItemOutcome, LatencyComparison
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


def _refusals(ctx) -> list[str]:
    """The `promotion_blocked` events without `pooled`: a refused winner."""
    return [
        f["winner_rc_id"]
        for k, f in ctx.log.events
        if k == "promotion_blocked" and not f.get("pooled")
    ]


def _status(ctx):
    tracker = RunStatusTracker(project_id="p", task_id="t", guidance="g")
    for kind, fields in ctx.log.events:
        tracker.event(kind, **fields)
    return tracker.snapshot()


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
    # RunStatus: pooled at the first gate, rejected at the second. Never a
    # refused winner.
    assert _pooled_events(ctx) == ["rc_cand"]
    assert _refusals(ctx) == []
    status = _status(ctx)
    first, second = status.waves
    assert (first.pooled, first.rejected_incomplete) == (["rc_cand"], [])
    assert (second.pooled, second.rejected_incomplete) == ([], ["rc_cand"])
    assert first.promotion_skipped is None and second.promotion_skipped is None
    text = render_fallback_report(status)
    assert "rejected, val evidence incomplete at repeated gates: rc_cand" in text


async def test_a_third_holed_gate_stamps_only_its_own_record():
    # A rejected rc that comes back again: the earlier records carry the
    # stamp already, so only the new record is written and logged.
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
        ok(make_plan(_resubmit("w3-1", "rc_cand"))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", *([ok(make_result())] * 3))

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    records = [
        f["outcome"].experiment_memory_id
        for k, f in ctx.log.events
        if k == "item_outcome"
    ]
    assert len(set(records)) == 3
    recorded = [f["exp_mem_id"] for k, f in ctx.log.events if k == "rejection_recorded"]
    # Gate 2 stamps the records of gates 1 and 2; gate 3 stamps its own.
    assert sorted(recorded) == sorted(records)
    assert all(kiln.memories[m]["overview"].endswith(STAMP) for m in records)
    assert _status(ctx).waves[2].rejected_incomplete == ["rc_cand"]


def _resubmitted_three_times(ctx, *, fails: str) -> None:
    """Three gates of one rc, rc_cand, through the single-candidate rule:
    built in wave 1, resubmitted unchanged in waves 2 and 3. The val fill
    of `fails` (the candidate or the baseline) fails at every gate."""
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln)
    seed_train(kiln, "rc_cand")
    seed_val(kiln, *({"rc_base", "rc_cand"} - {fails}))
    _always_fails(kiln, fails)
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"))),
        ok(make_plan(_resubmit("w2-1", "rc_cand"))),
        ok(make_plan(_resubmit("w3-1", "rc_cand"))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", *([ok(make_result())] * 3))


def _records(ctx) -> list[str]:
    return [
        f["outcome"].experiment_memory_id
        for k, f in ctx.log.events
        if k == "item_outcome"
    ]


async def test_baseline_side_holes_count_once_the_baseline_reaches_the_limit():
    # The baseline's val fill fails at every gate; the candidate's own val
    # runs are complete.
    # - Gate 1: the baseline is under its limit. The hole is not the
    #   candidate's, so it does not count. The candidate stays pooled.
    # - Gate 2: the baseline reaches the limit. One `baseline_val_holes`
    #   event, and from this gate on, the hole counts against the candidate.
    # - Gate 3: the second counted gate of rc_cand: the bound rejects it.
    #   The record of gate 1 was not counted, so it is not stamped.
    ctx = make_ctx(gate_agent="auto")
    _resubmitted_three_times(ctx, fails="rc_base")

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    first, second, third = _records(ctx)
    assert ctx.baseline_holed_gates == {"rc_base": 3}
    assert ctx.holed_gates == {"rc_cand": [second, third]}
    events = [f for k, f in ctx.log.events if k == "baseline_val_holes"]
    assert [(f["baseline_rc_id"], f["holed_gates"]) for f in events] == [
        ("rc_base", HOLED_GATE_LIMIT)
    ]
    assert _pooled_events(ctx) == ["rc_cand", "rc_cand"]
    recorded = [f["exp_mem_id"] for k, f in ctx.log.events if k == "rejection_recorded"]
    assert sorted(recorded) == sorted([second, third])
    assert not ctx.kiln.memories[first]["overview"].endswith(STAMP)
    status = _status(ctx)
    assert [w.baseline_val_holes for w in status.waves] == [None, "rc_base", None]
    assert [w.pooled for w in status.waves] == [["rc_cand"], ["rc_cand"], []]
    assert status.waves[2].rejected_incomplete == ["rc_cand"]
    text = render_fallback_report(status)
    assert "baseline val evidence incomplete at repeated gates: rc_base" in text


async def test_a_failed_bound_write_stays_pooled_and_is_written_again():
    # At gate 2 the stamp of the gate-1 record fails. The rc is not in
    # `rejected_incomplete` alone: it stays in `pooled` too, since one of
    # its records is not stamped. Gate 3 writes the gate-1 record again.
    ctx = make_ctx(gate_agent="auto")
    _resubmitted_three_times(ctx, fails="rc_cand")
    stamp = ctx.memory.stamp_rejected
    calls = []

    async def flaky_stamp(memory_id):
        calls.append(memory_id)
        if len(calls) == 1:
            raise RuntimeError("memory write failed")
        return await stamp(memory_id)

    ctx.memory.stamp_rejected = flaky_stamp

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    first, second, third = _records(ctx)
    assert calls == [first, second, first, third]
    recorded = [f["exp_mem_id"] for k, f in ctx.log.events if k == "rejection_recorded"]
    assert recorded == [second, first, third]
    for mem_id in (first, second, third):
        assert ctx.kiln.memories[mem_id]["overview"].endswith(STAMP)
    status = _status(ctx)
    assert [w.pooled for w in status.waves] == [["rc_cand"], ["rc_cand"], []]
    assert [w.rejected_incomplete for w in status.waves] == [
        [],
        ["rc_cand"],
        ["rc_cand"],
    ]


# ---- the other gate paths ----


async def test_a_refused_holed_winner_and_the_other_holed_candidates_stay_pooled():
    # `promote_best` picks a candidate whose val fill failed. The gate
    # refuses it and returns before the rejections. Every holed candidate
    # still gets its pooled event, and nothing is stamped.
    ctx = make_ctx()
    _scenario(ctx, 3, holed=("rc_w1-2", "rc_w1-3"))
    ctx.dispatch.queue("promote_best", decision("rc_w1-2"))

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    assert ctx.kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_base"]
    assert _pooled_events(ctx) == ["rc_w1-2", "rc_w1-3"]
    assert _refusals(ctx) == ["rc_w1-2"]
    assert not any(_stamped(ctx, rc) for rc in ("rc_w1-1", "rc_w1-2", "rc_w1-3"))
    (wave,) = _status(ctx).waves
    assert wave.pooled == ["rc_w1-2", "rc_w1-3"]


async def test_a_held_gate_does_not_hold_a_holed_candidate():
    # The gate escalates on an OPEN constraint. The judged candidate is
    # HELD. The holed one was not judged: no HELD stamp, it stays pooled.
    ctx = make_ctx()
    _scenario(ctx, 2, holed=("rc_w1-2",))
    ctx.dispatch.queue(
        "promote_best",
        decision(
            None, rationale="blocked on OPEN 424242093572", blocked_on="424242093572"
        ),
    )

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    held = ctx.kiln.memories[_exp_mem(ctx, "rc_w1-1")]
    assert held["overview"].endswith(" — HELD (constraint 424242093572) 07-22")
    holed = ctx.kiln.memories[_exp_mem(ctx, "rc_w1-2")]
    assert "HELD" not in holed["overview"]
    assert "held at the val gate" not in (holed["content"] or "")
    holds = [f["rc_id"] for k, f in ctx.log.events if k == "hold_recorded"]
    assert holds == ["rc_w1-1"]
    assert _pooled_events(ctx) == ["rc_w1-2"]
    assert not [k for k, _ in ctx.log.events if k == "rejection_recorded"]


def _two_gates(ctx, second_gate) -> None:
    """Two waves of two candidates, through `promote_best`. The val fill of
    rc_x fails each time. Wave 1: rc_x and rc_y. Wave 2: rc_x again (an
    unchanged resubmission) and rc_z. `second_gate`: the second answer of
    `promote_best`."""
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    for issue in ("issue_1", "issue_2", "issue_3"):
        seed_issue(kiln, issue)
    for rc in ("rc_x", "rc_y", "rc_z"):
        seed_train(kiln, rc)
    seed_val(kiln, "rc_base", "rc_y", "rc_z")
    _always_fails(kiln, "rc_x")
    built = {"w1-1": "rc_x", "w1-2": "rc_y", "w2-2": "rc_z"}
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(
            make_plan(
                fix_item("w1-1", issue_memory_id="issue_1"),
                fix_item("w1-2", issue_memory_id="issue_2"),
            )
        ),
        ok(
            make_plan(
                _resubmit("w2-1", "rc_x"),
                fix_item("w2-2", issue_memory_id="issue_3"),
            )
        ),
        ok(make_plan(loop_exit="done")),
    )
    d.queue(
        "create_experiment_rc",
        *([lambda req: rc_ok(built[req.item.id])] * 3),
    )
    d.queue("analyze_experiment_results", *([ok(make_result())] * 4))
    d.queue("promote_best", decision(None, rationale="no winner"), *second_gate)


async def test_the_bound_on_the_promote_best_path():
    ctx = make_ctx()
    _two_gates(ctx, [decision(None, rationale="no winner")])

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    recorded = [
        (f["rc_id"], f["reason"])
        for k, f in ctx.log.events
        if k == "rejection_recorded"
    ]
    assert sorted(recorded) == sorted(
        [
            ("rc_y", "no gain"),
            ("rc_x", "val incomplete"),
            ("rc_x", "val incomplete"),
            ("rc_z", "no gain"),
        ]
    )
    assert _pooled_events(ctx) == ["rc_x"]
    first, second = _status(ctx).waves
    assert (first.pooled, first.rejected_incomplete) == (["rc_x"], [])
    assert (second.pooled, second.rejected_incomplete) == ([], ["rc_x"])


async def test_the_bound_stamps_on_a_promote_best_hard_fail():
    # `promote_best` fails at the second gate: the gate stamps no decision.
    # The bound stamped rc_x before the decision, and RunStatus says so next
    # to the skipped promotion.
    ctx = make_ctx()
    _two_gates(ctx, [infra("provider_error")] * 5)

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    assert _stamped(ctx, "rc_y")
    assert not _stamped(ctx, "rc_z")
    for mem_id in ctx.holed_gates["rc_x"]:
        assert ctx.kiln.memories[mem_id]["overview"].endswith(STAMP)
    second = _status(ctx).waves[1]
    assert second.promotion_skipped
    assert second.rejected_incomplete == ["rc_x"]
    assert second.pooled == []
    text = render_fallback_report(_status(ctx))
    assert "rejected, val evidence incomplete at repeated gates: rc_x" in text


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
    # The single-candidate rule's path also says "pooled", and it does not
    # report the candidate as a refused winner.
    assert _pooled_events(ctx) == ["rc_cand"]
    assert _refusals(ctx) == []
    (wave,) = _status(ctx).waves
    assert wave.pooled == ["rc_cand"]
    assert wave.promotion_skipped is None
    assert wave.rejected_incomplete == []


# ---- the speed and cost objective ----


def _paired(ratio: float, *, incomplete: bool = False) -> LatencyComparison:
    """A paired pass of 12 items. `incomplete`: the pass planned 30, and 12
    pairs are under the floor, so `compare_latencies` gives no verdict."""
    base = {f"i{k}": 1000.0 + k for k in range(12)}
    comparison = compare_latencies(
        base,
        {k: v * ratio for k, v in base.items()},
        method="paired_drive",
        split="val",
        planned=30 if incomplete else None,
    )
    assert comparison.incomplete is incomplete
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


# ---- the side of a hole, in `_assemble_val_tables` ----


def _rows(*item_ids: str) -> list[dict]:
    return [make_run(i, accuracy=1.0) for i in item_ids]


FULL = ("v1", "v2", "v3", "v4")


@pytest.mark.parametrize(
    ("candidate", "baseline", "baseline_side_only", "baseline_holed"),
    [
        # A partial candidate fill (2 of 4 stored): the candidate's hole.
        ((_rows("v1", "v2"), 4), (_rows(*FULL), 4), set(), False),
        # Both sides complete, on different items: a pair hole with neither
        # side under the floor. The sides cannot be told apart: it counts.
        ((_rows(*FULL), 4), (_rows("v1", "v2", "v5", "v6"), 4), set(), False),
        # Both sides under the floor: it counts.
        ((_rows("v1", "v2"), 4), (_rows("v1", "v2"), 4), set(), True),
        # Only the baseline is under the floor: the baseline's hole.
        ((_rows(*FULL), 4), (_rows("v1", "v2"), 4), {"rc_cand"}, True),
        # A cached candidate fill (no job total) with the same 3 items as a
        # baseline whose job planned 4: read against the baseline's total,
        # the candidate is short too.
        ((_rows("v1", "v2", "v3"), None), (_rows("v1", "v2", "v3"), 4), set(), True),
        # The baseline fill failed: the baseline's hole.
        ((_rows(*FULL), 4), "fail", {"rc_cand"}, True),
    ],
)
async def test_the_side_of_a_val_hole(
    monkeypatch, candidate, baseline, baseline_side_only, baseline_holed
):
    ctx = make_ctx()
    seed_active_eval(ctx.kiln)
    fills = {"rc_cand": candidate, "rc_base": baseline}

    async def ensure_measured(eval_id, eval_config_id, rc_id, split):
        fill = fills[rc_id]
        if fill == "fail":
            raise MeasurementFailed(
                ErrorInfo(error_class="provider_error", detail="boom")
            )
        runs, n_expected = fill
        return runs, JobHealth(n_expected=n_expected)

    monkeypatch.setattr(ctx.analyzer, "ensure_measured", ensure_measured)
    mem = ctx.kiln.add_memory("EXP 0722-w1-1: x", "task::t1", ["experiment"])
    outcome = ItemOutcome(
        item=fix_item("w1-1"),
        status="completed",
        rc_id="rc_cand",
        experiment_memory_id=mem,
        result=make_result(),
        infra_error=None,
    )
    candidates = [
        orchestrator.GateCandidate(
            rc_id="rc_cand", experiment_memory_id=mem, outcome=outcome
        )
    ]

    assembly = await orchestrator._assemble_val_tables(ctx, candidates, "rc_base")

    (table,) = assembly.tables
    assert table.ineligible
    assert assembly.baseline_side_only == baseline_side_only
    assert assembly.baseline_holed is baseline_holed
