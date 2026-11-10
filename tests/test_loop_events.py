"""The loop's display data: the events and fields a display reads beyond
the plain lines (the data contract audit's changes 1 to 7, for the new TUI).
Each payload is plain JSON data built when it is emitted, and none of it
changes what `PlainUi` prints or what the loop's tracker folds."""

from __future__ import annotations

import asyncio
import io
import json
from datetime import datetime
from pathlib import Path

import pytest
from conftest import (
    ManualClock,
    RecordingLog,
    fix_item,
    make_ctx,
    make_plan,
    make_tracker,
)
from fake_dispatcher import ok
from rich.console import Console
from test_orchestrator import rc_ok, seed_active_eval, seed_baseline
from test_screening_memory import _result
from test_speed_objective import _seed_rows, _speed_ctx
from test_viability import _ctx, _rc, _request

from kiln_harness_tuner.analysis import MeasurementFailed
from kiln_harness_tuner.estimate import FAIL, PASS, Estimate, Judged
from kiln_harness_tuner.gateway.ops import MEMORY_FILE
from kiln_harness_tuner.measure import BLOCK_DISC, Measured, arm_estimates, chunk_counts
from kiln_harness_tuner.memory_feed import MemoryFeed
from kiln_harness_tuner.orchestrator import orchestrate
from kiln_harness_tuner.schemas import (
    BatchPlan,
    ErrorInfo,
    OpenUnknown,
    ProbeItem,
    ProbeResult,
    ScreenCandidate,
)
from kiln_harness_tuner.stop_rules import Evidence
from kiln_harness_tuner.ui import PlainUi

# The kinds a display reads and the plain lines never print.
NEW_KINDS = {
    "arm_estimates",
    "arm_results",
    "gate_val_tables",
    "gate_fills_planned",
    "gate_fill",
    "open_questions",
    "memory_written",
}
QUESTION = OpenUnknown(
    question="Does the forum\n  allow links in a post?",
    default_assumed="no links",
    gates="the link-policy arm",
    reask_when="a moderator answers it",
)


def events(ctx, kind: str) -> list[dict]:
    return [fields for k, fields in ctx.log.events if k == kind]


def kinds(ctx) -> list[str]:
    return [k for k, _fields in ctx.log.events]


def memory_line(**fields) -> str:
    row = {
        "ts": "2026-11-10T14:00:00",
        "op": "create",
        "id": "424242500001",
        "role": "batch_plan",
        "scope": "project",
        "tags": ["lesson"],
        "overview": "LESSON: name the skill to make it read",
        **fields,
    }
    return json.dumps(row) + "\n"


async def quality_run(tmp_path: Path):
    """One fix arm under quality: the parent fails 1 of 30 train items, the
    candidate passes all 30 and both pass val."""
    ctx = _speed_ctx(tmp_path, cand_ms=1000.0, objective="quality", train_n=30)
    await orchestrate(ctx, "better", timeout_mins=180)
    return ctx


# ------------------------------------------------- change 1: arm results


async def test_arm_estimates_when_a_quality_arm_completes(tmp_path):
    ctx = await quality_run(tmp_path)
    (est,) = events(ctx, "arm_estimates")
    order = kinds(ctx)
    assert order.index("arm_estimates") == order.index("measurement") + 1
    assert {k: est[k] for k in ("item_id", "rc_id", "parent_rc_id")} == {
        "item_id": "w1-1",
        "rc_id": "rc_cand",
        "parent_rc_id": "rc_base",
    }
    assert (est["status"], est["stop_kind"]) == ("completed", None)
    (row,) = est["per_eval"]
    assert (row["eval_id"], row["eval_name"]) == ("eval_1", "Eval eval_1")
    assert (row["judged"], row["planned"], row["fixed"], row["broke"]) == (30, 30, 1, 0)
    # Every item judged: the estimate is the exact census, +1 of 30.
    assert row["net"] == row["ci_low"] == row["ci_high"] == pytest.approx(1 / 30)
    assert (row["rate"], row["n"], row["complete"]) == (1.0, 30, True)
    assert est["pooled"] == {
        "net": pytest.approx(1 / 30),
        "ci_low": pytest.approx(1 / 30),
        "ci_high": pytest.approx(1 / 30),
        "n": 30,
    }
    # Under quality the arm is not timed.
    assert est["latency"] is est["cost"] is None
    assert est["cap_usd"] > 0
    json.dumps(est)


async def test_arm_estimates_when_a_rule_stops_the_arm(tmp_path):
    ctx = _speed_ctx(tmp_path, cand_ms=1000.0, objective="quality", train_n=30)
    _seed_rows(ctx.kiln, "rc_cand", [0.0] + [1.0] * 29)
    ctx.dispatch.queues["create_experiment_rc"].clear()
    ctx.dispatch.queue("create_experiment_rc", rc_ok("rc_cand"))
    ctx.dispatch.queues["batch_plan"].clear()
    ctx.dispatch.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1", residual_gate=False))),
        ok(make_plan(loop_exit="done")),
    )
    await orchestrate(ctx, "better", timeout_mins=180)
    (est,) = events(ctx, "arm_estimates")
    assert (est["status"], est["stop_kind"]) == ("stopped", "futility")
    (row,) = est["per_eval"]
    # It did not fix the parent's one failure, and the rest of the split
    # was never judged.
    assert row["judged"] < row["planned"] == 30
    assert row["fixed"] == 0
    # A stopped arm has no analysis bundle.
    assert events(ctx, "arm_results") == []


async def test_arm_estimates_carry_the_paired_timing(tmp_path):
    ctx = _speed_ctx(tmp_path, cand_ms=400.0, train_n=30, parent_ms=900.0)
    await orchestrate(ctx, "go fast", timeout_mins=180)
    (est,) = events(ctx, "arm_estimates")
    assert est["parent_rc_id"] == "rc_fast"
    assert est["latency"]["method"] == "paired_drive"
    assert est["latency"]["median_ratio"] < 1.0
    assert est["cost"]["metric"] == "cost_usd"
    # The parent is not the run's baseline, so the arm is timed against it too.
    assert est["latency_vs_parent"]["median_ratio"] < 1.0
    assert est["cost_vs_parent"] is not None
    json.dumps(est)


def test_arm_estimates_of_a_hand_built_measurement():
    judged = [
        Judged("e1", "a", FAIL, BLOCK_DISC, False, True),
        Judged("e1", "b", PASS, BLOCK_DISC, True, False),
        Judged("e2", "c", PASS, BLOCK_DISC, True, True),
    ]
    m = Measured(
        rc_id="rc_c",
        label="w1-1",
        status="capped",
        stop_kind="max_usd",
        evidence=Evidence(judged=judged, eval_names={"e1": "Tone"}),
        per_eval={"e1": Estimate(net=0.1, se=0.05, rate=0.8, n=2, complete=False)},
        planned_per_eval={"e1": 10, "e2": 5},
        spend_usd=1.5,
    )
    fields = arm_estimates(m, "w1-1", "rc_p")
    (row,) = fields["per_eval"]  # e2 has no estimate: no row
    assert row == {
        "eval_id": "e1",
        "eval_name": "Tone",
        "net": 0.1,
        "ci_low": pytest.approx(0.1 - 1.96 * 0.05),
        "ci_high": pytest.approx(0.1 + 1.96 * 0.05),
        "rate": 0.8,
        "n": 2,
        "complete": False,
        "judged": 2,
        "planned": 10,
        "fixed": 1,
        "broke": 1,
    }
    assert fields["pooled"] is None
    assert fields["cap_usd"] is None
    assert (fields["status"], fields["stop_kind"], fields["spend_usd"]) == (
        "capped",
        "max_usd",
        1.5,
    )
    for name in ("latency", "cost", "latency_vs_parent", "cost_vs_parent"):
        assert fields[name] is None


async def test_arm_results_dump_the_analysis_bundle(tmp_path):
    ctx = await quality_run(tmp_path)
    (res,) = events(ctx, "arm_results")
    order = kinds(ctx)
    assert order.index("arm_estimates") < order.index("arm_results")
    assert order.index("arm_results") < order.index("item_outcome")
    assert (res["item_id"], res["rc_id"], res["parent_rc_id"]) == (
        "w1-1",
        "rc_cand",
        "rc_base",
    )
    (row,) = res["per_eval"]
    assert (row["split"], row["compared_to"], row["score_key"]) == (
        "train",
        "parent",
        "accuracy",
    )
    # The parent's absolute value, for the Start tier.
    assert row["baseline_score"] == pytest.approx(29 / 30)
    assert row["candidate_score"] == 1.0
    assert row["delta"] == pytest.approx(1 / 30)
    assert res["errors"] == [] and res["latency"] is None
    json.dumps(res)


# ------------------------------------------------- change 3: chunk counts


async def test_measure_chunk_counts_and_cells(tmp_path):
    ctx = await quality_run(tmp_path)
    chunks = events(ctx, "measure_chunk")
    assert len(chunks) > 1
    judged = 0
    for chunk in chunks:
        judged += len(chunk["cells"])
        assert chunk["judged"] == judged
        assert chunk["planned"] == 30
        assert chunk["cap_usd"] > 0 and chunk["spend_usd"] >= 0
        assert chunk["line"].startswith(f"w1-1: chunk {chunk['chunk']}/")
    assert [c["decision"] for c in chunks] == ["continue"] * (len(chunks) - 1) + [
        "done"
    ]
    cells = [cell for chunk in chunks for cell in chunk["cells"]]
    assert len({cell[1] for cell in cells}) == len(cells) == 30
    # The parent's one failure is the fix; every other cell held.
    assert [c for c in cells if c[2] is not True] == [["eval_1", "train0", False, True]]
    assert all(c[3] is True for c in cells)
    assert (chunks[-1]["fixed"], chunks[-1]["broke"]) == (1, 0)
    # The guard counts are for speed and cost only.
    assert not {"lost", "guards", "fixes", "fails"} & set(chunks[-1])
    json.dumps(chunks)


async def test_measure_chunk_guard_counts_under_speed(tmp_path):
    ctx = _speed_ctx(tmp_path, cand_ms=400.0, train_n=30)
    await orchestrate(ctx, "go fast", timeout_mins=180)
    last = events(ctx, "measure_chunk")[-1]
    assert (last["lost"], last["guards"], last["fixes"], last["fails"]) == (0, 29, 1, 1)
    assert last["spend_usd"] > 0


def test_chunk_counts_of_a_hand_built_evidence():
    rows = [
        Judged("e1", "a", FAIL, BLOCK_DISC, False, False),
        Judged("e1", "b", PASS, BLOCK_DISC, True, None),
        Judged("e1", "c", PASS, BLOCK_DISC, True, True),
    ]
    counts = chunk_counts(Evidence(judged=rows), since=1)
    assert counts == {
        "judged": 3,
        "fixed": 0,
        "broke": 1,  # a missing candidate row counts as a fail
        "cells": [["e1", "b", True, None], ["e1", "c", True, True]],
    }
    counts = chunk_counts(Evidence(objective="cost", judged=rows), since=3)
    assert counts["cells"] == []
    assert (counts["lost"], counts["guards"], counts["fixes"], counts["fails"]) == (
        1,
        2,
        0,
        1,
    )


# ---------------------------------------- changes 2 and 4: the val gate


async def test_gate_fills_then_the_val_tables_under_quality(tmp_path):
    ctx = await quality_run(tmp_path)
    (planned,) = events(ctx, "gate_fills_planned")
    assert planned == {
        "baseline_rc_id": "rc_base",
        "rc_ids": ["rc_cand"],
        "eval_ids": ["eval_1"],
        "fills": 2,
    }
    fills = events(ctx, "gate_fill")
    assert sorted((f["rc_id"], f["role"]) for f in fills) == [
        ("rc_base", "baseline"),
        ("rc_cand", "candidate"),
    ]
    assert [f["done"] for f in fills] == [1, 2]
    for fill in fills:
        assert (fill["eval_id"], fill["total"]) == ("eval_1", 2)
        assert (fill["stored"], fill["expected"], fill["missing"]) == (2, 2, 0)
        assert fill["clean"] is True
    (gate,) = events(ctx, "gate_val_tables")
    assert (gate["baseline_rc_id"], gate["objective"]) == ("rc_base", "quality")
    (table,) = gate["tables"]
    assert table["rc_id"] == "rc_cand"
    # Dumped after the integrity checks.
    assert table["integrity_checked"] is True
    (row,) = table["per_eval"]
    assert (row["split"], row["baseline_score"], row["candidate_score"]) == (
        "val",
        1.0,
        1.0,
    )
    order = kinds(ctx)
    assert order.index("gate_fills_planned") < order.index("gate_fill")
    assert order.index("gate_fill") < order.index("gate_val_tables")
    assert order.index("gate_val_tables") < order.index("promotion")
    json.dumps(gate)


async def test_the_val_tables_under_speed_carry_the_val_speed_pass(tmp_path):
    ctx = _speed_ctx(tmp_path, cand_ms=400.0, train_n=30)
    await orchestrate(ctx, "go fast", timeout_mins=180)
    (gate,) = events(ctx, "gate_val_tables")
    assert gate["objective"] == "speed"
    (table,) = gate["tables"]
    assert table["latency"]["method"] == "paired_drive"
    assert table["latency"]["split"] == "val"
    order = kinds(ctx)
    assert order.index("speed_result") < order.index("gate_val_tables")
    json.dumps(gate)


async def test_a_failed_val_fill_counts_toward_the_gate_progress(tmp_path):
    ctx = _speed_ctx(tmp_path, cand_ms=1000.0, objective="quality", train_n=30)
    measured = ctx.analyzer.ensure_measured

    async def ensure_measured(eval_id, eval_config_id, rc_id, split):
        if split == "val" and rc_id == "rc_cand":
            raise MeasurementFailed(
                ErrorInfo(error_class="provider_error", detail="the job died")
            )
        if split == "val":  # finish after the failed fill
            for _ in range(5):
                await asyncio.sleep(0)
        return await measured(eval_id, eval_config_id, rc_id, split)

    ctx.analyzer.ensure_measured = ensure_measured
    await orchestrate(ctx, "better", timeout_mins=180)
    (failed,) = events(ctx, "val_fill_failed")
    assert failed["rc_id"] == "rc_cand"
    (fill,) = events(ctx, "gate_fill")
    assert (fill["rc_id"], fill["done"], fill["total"]) == ("rc_base", 2, 2)
    (gate,) = events(ctx, "gate_val_tables")
    assert gate["tables"][0]["ineligible"] is True


# --------------------------------------------- change 6: open questions


def _probe_plan(*questions: OpenUnknown) -> BatchPlan:
    probe = ProbeItem(
        id="w1-p1",
        type="probe",
        question="Does the model call the link tool?",
        method="fork, attach, read three traces",
        reason="gate the link arm",
    )
    return BatchPlan(
        items=[probe],
        rejected_summary="w1: probes first",
        open_questions=list(questions),
        probe_phase=True,
    )


async def test_open_questions_after_each_planner_write():
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    second = QUESTION.model_copy(update={"question": "Is a quote a link?"})
    ctx.dispatch.queue(
        "batch_plan",
        ok(_probe_plan(QUESTION)),
        ok(make_plan(loop_exit="nothing to build", open_questions=[QUESTION, second])),
    )
    ctx.dispatch.queue(
        "probe", ok(ProbeResult(answer="no", evidence="0 calls in 3 drives", drives=3))
    )
    await orchestrate(ctx, "", timeout_mins=180)
    plan, replan = events(ctx, "open_questions")
    assert (plan["wave"], plan["phase"]) == (1, "plan")
    (asked,) = plan["questions"]
    assert asked == {
        "question": "Does the forum allow links in a post?",  # as stored
        "default_assumed": "no links",
        "gates": "the link-policy arm",
        "reask_when": "a moderator answers it",
        "constraint_id": asked["constraint_id"],
    }
    assert "constraint" in ctx.kiln.memories[asked["constraint_id"]]["tags"]
    assert replan["phase"] == "replan"
    again, new = replan["questions"]
    # A repeated question names the same record.
    assert again["constraint_id"] == asked["constraint_id"]
    assert new["constraint_id"] != asked["constraint_id"]


async def test_no_open_questions_event_for_an_empty_list(tmp_path):
    ctx = await quality_run(tmp_path)
    assert events(ctx, "open_questions") == []


async def test_open_questions_after_a_rescreen(tmp_path):
    ctx = _ctx(tmp_path, objective="quality")
    ctx.gate_agent = "auto"
    ctx.kiln.run_configs.append(_rc("rc_prior"))
    for n in (1, 2):
        await ctx.memory.record_screen_result(_result(round_no=n))
    screen = make_plan().model_copy(
        update={
            "items": [],
            "screen": _request(ScreenCandidate(id="s1-1", rc_id="rc_prior")),
        }
    )
    ctx.dispatch.queue(
        "batch_plan",
        ok(screen),
        ok(make_plan(loop_exit="done", open_questions=[QUESTION])),
    )
    await orchestrate(ctx, "g", timeout_mins=180)
    (rescreen,) = events(ctx, "open_questions")
    assert (rescreen["wave"], rescreen["phase"]) == (1, "rescreen")
    assert rescreen["questions"][0]["default_assumed"] == "no links"


# ------------------------------------------------ change 7: memory lines


def test_the_feed_forwards_each_complete_line_once(tmp_path):
    path = tmp_path / MEMORY_FILE
    feed = MemoryFeed(path)
    log = RecordingLog()
    assert feed.forward(log, "batch_plan-w1") == 0  # no file yet
    first = memory_line()
    second = memory_line(
        op="update", id="424242500002", tags=["dead_end"], overview="DEAD END: x"
    )
    third = memory_line(id="424242500003", tags=[], overview="OPEN: is a quote a link?")
    # A gateway can be in the middle of its append.
    path.write_text(first + second + third[:20])
    assert feed.forward(log, "batch_plan-w1") == 2
    assert feed.forward(log, "create_experiment_rc-w1-1") == 0
    with path.open("a") as f:
        f.write(third[20:])
    assert feed.forward(log, "exit") == 1
    assert [k for k, _f in log.events] == ["memory_written"] * 3
    rows = [fields for _k, fields in log.events]
    assert rows[0] == {
        "id": "424242500001",
        "op": "create",
        "role": "batch_plan",
        "scope": "project",
        "tags": ["lesson"],
        "overview": "LESSON: name the skill to make it read",
        "record_kind": "lesson",
        "written_at": "2026-11-10T14:00:00",
        "forwarded_by": "batch_plan-w1",
    }
    assert [r["record_kind"] for r in rows] == ["lesson", "dead_end", "constraint"]
    assert [r["forwarded_by"] for r in rows] == ["batch_plan-w1"] * 2 + ["exit"]


def test_the_feed_skips_a_line_that_is_not_a_record(tmp_path, caplog):
    path = tmp_path / MEMORY_FILE
    path.write_text("not json\n[1, 2]\n" + memory_line(tags="lesson"))
    log = RecordingLog()
    assert MemoryFeed(path).forward(log, "s") == 1
    (fields,) = [f for _k, f in log.events]
    assert (fields["tags"], fields["record_kind"]) == ([], "other")
    assert caplog.text.count("skipped a line that is not a record") == 2


async def test_pending_memory_lines_go_out_just_before_the_first_exit(tmp_path):
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    path = tmp_path / MEMORY_FILE
    path.write_text(memory_line())
    ctx.memory_feed = MemoryFeed(path)
    await orchestrate(ctx, "", timeout_mins=180)
    (written,) = events(ctx, "memory_written")
    assert written["forwarded_by"] == "exit"
    order = kinds(ctx)
    assert order.index("memory_written") == order.index("exit") - 1


# ------------------------------------------------- the plain path


def _without_new_data(recorded: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    """The events as the loop emitted them before these changes."""
    out = []
    for kind, fields in recorded:
        if kind in NEW_KINDS:
            continue
        if kind == "measure_chunk":
            fields = {
                k: fields[k] for k in ("item_id", "rc_id", "chunk", "chunks", "line")
            }
        elif kind == "wave_start":
            items = [
                {k: item[k] for k in ("item_id", "kind", "label")}
                for item in fields["items"]
            ]
            fields = {**fields, "items": items}
        out.append((kind, fields))
    return out


def _plain(recorded: list[tuple[str, dict]]):
    out = io.StringIO()
    tracker = make_tracker(ManualClock())
    stamp = datetime(2026, 11, 10, 14, 0)
    console = Console(file=out, width=120)
    with PlainUi(tracker, console=console, now=lambda: stamp) as ui:
        for kind, fields in recorded:
            tracker.event(kind, **fields)
            ui.event(kind, **fields)
    return out.getvalue(), tracker.snapshot()


async def test_the_plain_path_prints_the_same_with_and_without_the_new_data(
    tmp_path,
):
    ctx = _speed_ctx(tmp_path, cand_ms=400.0, train_n=30)
    ctx.dispatch.queues["batch_plan"].clear()
    ctx.dispatch.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"), open_questions=[QUESTION])),
        ok(make_plan(loop_exit="done")),
    )
    path = tmp_path / MEMORY_FILE
    path.write_text(memory_line())
    ctx.memory_feed = MemoryFeed(path)
    await orchestrate(ctx, "go fast", timeout_mins=180)
    assert NEW_KINDS <= set(kinds(ctx))  # every new kind is in the replay
    text, status = _plain(ctx.log.events)
    assert "w1-1: chunk 1/" in text
    assert (text, status) == _plain(_without_new_data(ctx.log.events))
