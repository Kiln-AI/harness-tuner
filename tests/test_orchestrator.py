"""Loop end-to-end tests: whole waves against FakeDispatcher +
FakeKiln — promotion, head-to-head, tripwire, deadline → wrap-up, janitor,
loop_exit, planner failure, and the named N=1 degenerate case.
"""

import json
from datetime import datetime

import pytest
from conftest import (
    FakeClock,
    baseline_record,
    eval_only_item,
    fix_item,
    make_ctx,
    make_plan,
    make_result,
    measure_item,
)
from fake_dispatcher import infra, ok
from fake_kiln import make_eval, make_job_error, make_run, make_run_config, make_score

from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.memory_schema import VAL_STATS_OVERVIEW, VAL_STATS_TAG
from kiln_harness_tuner.orchestrator import (
    SpendMeter,
    _active_evals,
    _eval_train_sizes,
    _run_wave,
    orchestrate,
    should_wrap,
)
from kiln_harness_tuner.schemas import (
    BaselineRef,
    EvalBuildReport,
    EvalTarget,
    ExperimentItem,
    IngredientOutcome,
    MergeWork,
    OpenUnknown,
    PromotionDecision,
    RcBuildResult,
    RegressedIssue,
    WinRef,
)
from kiln_harness_tuner.setup import EvalSplitAuditError, SetupPreconditionError
from kiln_harness_tuner.status import item_summary
from kiln_harness_tuner.wave_log import FanoutLog

BASELINE_OVERVIEW = "Baseline: run config rc_base — promoted 07-15 by EXP 0715-w1-1"


def seed_baseline(kiln, overview: str = BASELINE_OVERVIEW) -> str:
    """The singleton the loop reads every wave. The rc id lives in the
    `rc_<id>` tag; the overview is prose nothing parses."""
    return baseline_record(
        kiln, overview, content="rc_base", rc_id="rc_base", memory_id="mem_baseline"
    )


def seed_active_eval(kiln) -> None:
    kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    kiln.specs = [{"id": "spec_1", "eval_id": "eval_1", "status": "active"}]


def seed_train(kiln, candidate_rc: str, parent_rc: str = "rc_base") -> None:
    kiln.results[("eval_1", parent_rc, "train")] = [
        make_run("t1", accuracy=0.0),
        make_run("t2", accuracy=1.0),
    ]
    kiln.pending[("eval_1", candidate_rc, "train")] = [
        make_run("t1", accuracy=1.0),
        make_run("t2", accuracy=1.0),
    ]


def seed_val(kiln, *rc_ids: str) -> None:
    for rc_id in rc_ids:
        score = 0.5 if rc_id == "rc_base" else 1.0
        kiln.pending[("eval_1", rc_id, "val")] = [
            make_run("v1", accuracy=0.0),
            make_run("v2", accuracy=score),
        ]


def seed_issue(kiln, issue_id: str = "issue_1") -> str:
    return kiln.add_memory(
        f"issue body for {issue_id}", "task::t1", ["issue"], memory_id=issue_id
    )


def rc_ok(rc_id: str):
    return ok(RcBuildResult(rc_id=rc_id, build_notes="built"))


def decision(winner_rc_id, rationale="+9% mean win rate vs rc_base", **kwargs):
    return ok(
        PromotionDecision(
            winner_rc_id=winner_rc_id,
            winner_experiment_memory_id=kwargs.pop("winner_experiment_memory_id", None),
            rationale=rationale,
            **kwargs,
        )
    )


def eval_report(reproduced: bool = True) -> EvalBuildReport:
    return EvalBuildReport(
        eval_id="eval_1",
        eval_config_id="ec_1",
        eval_type="llm_judge",
        case_count=20,
        reproduced=reproduced,
        baseline_headline="62% pass",
        priority="p1",
    )


# ---- promotion ----


async def test_promotion_happy_path():
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    # Two more evals that must stay OUT of the val pass (active-set rule):
    # no default config / spec status not "active".
    kiln.evals["eval_2"] = make_eval("eval_2", None)
    kiln.evals["eval_3"] = make_eval("eval_3", "ec_3")
    kiln.specs.append({"id": "spec_3", "eval_id": "eval_3", "status": "future"})
    seed_issue(kiln)
    seed_train(kiln, "rc_cand")
    seed_val(kiln, "rc_base", "rc_cand")

    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"))),
        ok(make_plan(loop_exit="out of experiments")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))
    d.queue("promote_best", decision("rc_cand"))

    result = await orchestrate(ctx, "focus evals X", timeout_mins=180)
    assert result == "out of experiments"

    # The val pass ran candidate + baseline fill on split=val, active eval only.
    val_jobs = [j for j in kiln.job_invocations if j[2] == "val"]
    assert ("eval_1", "rc_cand", "val") in val_jobs
    assert ("eval_1", "rc_base", "val") in val_jobs
    assert all(j[0] == "eval_1" for j in val_jobs)

    # promote_best saw the code-assembled table + the baseline ref, which is
    # the tag's rc id and nothing else.
    (promo_req,) = d.calls_to("promote_best")
    assert promo_req.baseline.rc_id == "rc_base"
    (table,) = promo_req.candidates
    assert table.rc_id == "rc_cand"
    row = table.per_eval[0]
    assert row.split == "val"
    assert row.compared_to == "prior_baseline"
    assert row.baseline_rc_id == "rc_base"
    # `_assemble_val_tables` calls `ensure_measured` for the baseline AND
    # every candidate unconditionally — there is no stored-results
    # short-circuit — so BOTH sides of a val row always name a job.
    # `kiln-harness-tuner-promote` states this as load-bearing ("on the val lane a
    # non-null id says nothing about whether that side was newly measured"); a future
    # caching optimisation that broke it would make the skill text false.
    assert row.candidate_job_id is not None
    assert row.baseline_job_id is not None
    assert row.candidate_job_id != row.baseline_job_id

    # Apply writes. The `baseline` record is the pointer and
    # is METRIC-FREE; the val summary lands in the `val` record.
    promoted = kiln.memories["mem_baseline"]
    assert promoted["overview"] == (
        "Baseline: run config rc_cand — promoted 07-22 by EXP 0722-w1-1"
    )
    assert promoted["content"] == "rc_cand\nrc_base"
    assert promoted["tags"] == ["baseline", "rc_rc_cand"]
    (val_record,) = [m for m in kiln.memories.values() if "val" in m["tags"]]
    assert val_record["overview"] == VAL_STATS_OVERVIEW
    assert val_record["content"] == (
        "rc_cand (07-22, EXP 0722-w1-1) ← rc_base — +9% mean win rate vs rc_base"
    )
    issue = kiln.memories["issue_1"]
    assert issue["overview"] == "FIXED: issue body for issue_1"
    assert issue["content"] == "- fixed 07-22 by rc_cand, EXP 0722-w1-1"
    exp_overview = [
        m["overview"] for m in kiln.memories.values() if "experiment" in m["tags"]
    ]
    assert exp_overview == [
        "EXP 0722-w1-1: retry-skill — +18%, n=2, p=.03 — PROMOTED 07-22"
    ]

    # Strict apply order: baseline PATCH → issue → stamp.
    patched_ids = [mid for mid, _ in kiln.memory_patches]
    assert patched_ids.index("mem_baseline") < patched_ids.index("issue_1")
    exp_id = next(
        mid
        for mid, body in kiln.memory_patches
        if "PROMOTED" in (body.get("overview") or "")
    )
    assert patched_ids.index("issue_1") < len(patched_ids) - 1
    assert patched_ids[-1] == exp_id

    # Wave 2's planner got the PROMOTED baseline — fresh read each wave.
    plan_inputs = d.calls_to("batch_plan")
    assert plan_inputs[0].baseline.rc_id == "rc_base"
    assert plan_inputs[1].baseline.rc_id == "rc_cand"


async def test_head_to_head_two_candidates_only_the_winners_issue_closes():
    ctx = make_ctx()
    kiln = ctx.kiln
    _two_candidate_scenario(ctx)
    d = ctx.dispatch
    d.queue("promote_best", decision("rc_w1-1"))

    await orchestrate(ctx, "", timeout_mins=180)

    (promo_req,) = d.calls_to("promote_best")
    assert [t.rc_id for t in promo_req.candidates] == ["rc_w1-1", "rc_w1-2"]
    # The gathered val matrix is content-identical to the sequential path:
    # each candidate's table pairs its own fill against the shared baseline
    # fill (seed_val: baseline v2=0.5 → pass; candidates v2=1.0 → delta 0).
    for table in promo_req.candidates:
        [row] = table.per_eval
        assert row.eval_id == "eval_1"
        assert row.split == "val"
        assert row.compared_to == "prior_baseline"
        assert row.baseline_rc_id == "rc_base"
        assert row.n_items == 2
        assert row.delta == pytest.approx(0.0)
        assert row.n_expected == 2
        assert row.coverage == pytest.approx(1.0)
        assert row.missing_item_ids == []
    # Winner's issue closed; the loser's ISSUE record gets no writes (train
    # verdicts stand, the win stays merge-eligible). Its EXPERIMENT
    # record does get the val-gate rejection stamp, asserted in
    # test_gate_losers_are_stamped_after_the_winner_applies.
    assert kiln.memories["issue_1"]["overview"].startswith("FIXED: ")
    assert not kiln.memories["issue_2"]["overview"].startswith("FIXED: ")
    stamped = [
        m["overview"] for m in kiln.memories.values() if "— PROMOTED" in m["overview"]
    ]
    assert len(stamped) == 1


async def test_zero_winner_decision_leaves_the_baseline_untouched():
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_train(kiln, "rc_cand")
    seed_val(kiln, "rc_base", "rc_cand")
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))
    d.queue("promote_best", decision(None, rationale="val regressed; no winner"))

    await orchestrate(ctx, "", timeout_mins=180)
    # Scoped to the BASELINE record: zero winners means no promotion apply.
    # The candidate's own experiment record IS written (the rejection
    # stamp) — test_zero_winner_gate_stamps_every_candidate.
    assert kiln.memories["mem_baseline"]["overview"] == BASELINE_OVERVIEW


async def test_promote_best_hard_fail_skips_promotion_and_continues():
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_train(kiln, "rc_cand")
    seed_val(kiln, "rc_base", "rc_cand")
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))
    d.queue("promote_best", *([infra("provider_error")] * 5))

    result = await orchestrate(ctx, "", timeout_mins=180)
    # Skip promotion, wins stay pooled; the run continues to wave 2.
    assert result == "done"
    assert len(d.calls_to("promote_best")) == 5  # loop ladder: 4 retries
    assert ctx.sleeps == [30.0, 60.0, 120.0, 300.0]
    assert kiln.memories["mem_baseline"]["overview"] == BASELINE_OVERVIEW
    assert "promotion_skipped" in [k for k, _ in ctx.log.events]


async def test_val_measurement_failure_becomes_missing_rows_not_a_crash():
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_train(kiln, "rc_cand")
    seed_val(kiln, "rc_base")
    # Candidate's val job fails through the whole ladder (2 attempts)
    # with nothing stored — the zero-rows case.
    kiln.job_behaviors[("eval_1", "rc_cand", "val")] = ["fail", "fail"]
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))
    d.queue("promote_best", decision(None, rationale="no evidence"))

    await orchestrate(ctx, "", timeout_mins=180)
    (promo_req,) = d.calls_to("promote_best")
    assert promo_req.candidates[0].per_eval == []  # missing evidence, judged
    assert "val_fill_failed" in [k for k, _ in ctx.log.events]


async def test_partial_baseline_val_fill_does_not_drop_the_eval():
    # At the gate: the baseline's val job errors on v2
    # both attempts but stores v1 — the eval is NOT dropped for the
    # candidate; rows pair on the intersection and carry coverage.
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_train(kiln, "rc_cand")
    seed_val(kiln, "rc_cand")
    key = ("eval_1", "rc_base", "val")
    kiln.partial_results[key] = [make_run("v1", accuracy=0.0)]
    kiln.job_behaviors[key] = ["errored_items", "errored_items"]
    kiln.job_errors[key] = [make_job_error("v2", "serialization flake")]
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))
    d.queue("promote_best", decision(None, rationale="coverage too thin"))

    await orchestrate(ctx, "", timeout_mins=180)

    (promo_req,) = d.calls_to("promote_best")
    [row] = promo_req.candidates[0].per_eval
    assert row.split == "val"
    assert row.n_items == 1  # v1 only — the intersection
    assert row.n_expected == 2  # candidate side measured both val items
    assert row.coverage == pytest.approx(0.5)
    assert row.missing_item_ids == ["v2"]
    # The analyzer's eval_job_errors event reached the shared log (wiring).
    assert "eval_job_errors" in [k for k, _ in ctx.log.events]
    assert "val_fill_failed" not in [k for k, _ in ctx.log.events]


async def test_val_fill_hard_error_propagates_after_siblings_land():
    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.dispatch.queue("promote_best", decision(None))
    # 422 is a real answer — raised out of the gate, after the
    # concurrent sibling fills have landed.
    ctx.kiln.job_behaviors[("eval_1", "rc_base", "val")] = ["http422"]

    with pytest.raises(KilnAPIError) as exc:
        await orchestrate(ctx, "", timeout_mins=180)

    assert exc.value.status_code == 422
    assert ("eval_1", "rc_cand", "val") in ctx.kiln.job_invocations


# The experiment record `_promotion_scenario` produces, by id. The fake hands
# out `mem_<n>` in creation order, and setup runs inside every
# `orchestrate` test: on this scenario's single active-but-unbound eval it
# seeds one `UNMEASURED:` issue (mem_1) BEFORE the wave opens its experiment.
# Only the two tests that must script `memory_patch_behaviors` — a per-id
# queue set up before the run, so `_experiment_memories` can't be used — need
# to name it.
_SCENARIO_EXP_MEM = "mem_2"


def _promotion_scenario(ctx):
    """One wave, one successful candidate, decision rc_cand; wave 2 exits."""
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln)
    seed_train(kiln, "rc_cand")
    seed_val(kiln, "rc_base", "rc_cand")
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))


def _two_candidate_scenario(ctx):
    """One wave, two successful candidates (rc_w1-1 / rc_w1-2); wave 2 exits."""
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln, "issue_1")
    seed_issue(kiln, "issue_2")
    seed_train(kiln, "rc_w1-1")
    seed_train(kiln, "rc_w1-2")
    seed_val(kiln, "rc_base", "rc_w1-1", "rc_w1-2")
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(
            make_plan(
                fix_item("w1-1", issue_memory_id="issue_1"),
                fix_item("w1-2", issue_memory_id="issue_2"),
            )
        ),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", *([lambda req: rc_ok(f"rc_{req.item.id}")] * 2))
    d.queue("analyze_experiment_results", *([ok(make_result())] * 2))


async def test_promotion_write_ladder_recovers_mid_way():
    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.dispatch.queue("promote_best", decision("rc_cand"))
    # The baseline PATCH — apply step (a), the authoritative pointer — fails
    # once, then succeeds on the ladder (3 calls, 20s backoff).
    ctx.kiln.memory_patch_behaviors["mem_baseline"] = ["http500"]
    result = await orchestrate(ctx, "", timeout_mins=180)
    assert result == "done"
    assert ctx.kiln.memories["mem_baseline"]["overview"].startswith(
        "Baseline: run config rc_cand"
    )
    assert 20.0 in ctx.sleeps


async def test_promotion_write_ladder_exhaustion_is_fatal():
    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.dispatch.queue("promote_best", decision("rc_cand"))
    ctx.kiln.memory_patch_behaviors["mem_baseline"] = ["http500"] * 3
    with pytest.raises(KilnAPIError) as exc:
        await orchestrate(ctx, "", timeout_mins=180)
    assert exc.value.status_code == 500
    assert ctx.sleeps == [20.0, 20.0]  # 3 calls, exhaustion propagates


async def test_promotion_writes_the_baseline_pointer_and_the_val_stats_record():
    # The baseline/val split, end to end: the pointer record stays metric-free
    # and the agent's val summary lands in the `val` record instead.
    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.dispatch.queue(
        "promote_best", decision("rc_cand", rationale="+9% mean win rate vs rc_base")
    )
    await orchestrate(ctx, "", timeout_mins=180)

    pointer = ctx.kiln.memories["mem_baseline"]
    assert pointer["tags"] == ["baseline", "rc_rc_cand"]
    assert pointer["content"] == "rc_cand\nrc_base"
    assert "9%" not in f"{pointer['overview']}{pointer['content']}"

    (stats,) = [m for m in ctx.kiln.memories.values() if VAL_STATS_TAG in m["tags"]]
    assert stats["scope"] == "task::t1"
    assert stats["overview"] == VAL_STATS_OVERVIEW
    # The line names both ends of the move, so the record reads as a lineage.
    assert stats["content"].endswith("← rc_base — +9% mean win rate vs rc_base")
    assert stats["content"].startswith("rc_cand (")


async def test_refused_issue_write_is_logged_and_the_apply_completes():
    # The analyzer/planner named a record of the wrong kind as the landed
    # issue. The refusal is deterministic: no 20s ladder, no fatal — the
    # apply logs it and still stamps the winner (a moved baseline pointer
    # with an unstamped winner would be the worse state).
    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.kiln.memories["issue_1"]["tags"] = ["experiment"]  # wrong kind
    ctx.kiln.memories["issue_1"]["overview"] = "EXP 0722-w0-9: x — +1pp, n=2, p=.5"
    before = dict(ctx.kiln.memories["issue_1"])
    ctx.dispatch.queue("promote_best", decision("rc_cand"))

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "done"
    assert 20.0 not in ctx.sleeps  # refused, not retried
    assert ctx.kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_cand"]
    assert ctx.kiln.memories["issue_1"] == before  # untouched
    assert any("— PROMOTED" in m["overview"] for m in ctx.kiln.memories.values())
    (failure,) = [f for k, f in ctx.log.events if k == "memory_write_failed"]
    assert failure["write"] == "mark_fixed"
    assert failure["issue_memory_id"] == "issue_1"
    assert "RecordKindError" in failure["error"]


async def test_refused_verdict_and_stamp_writes_are_logged_and_the_run_goes_on():
    """A refused verdict write on one arm is logged, the item keeps its
    outcome, and the gate goes on; the winner's stamp, refused in turn (its
    record holds no verdict), is logged too."""
    from kiln_harness_tuner.memory import ExperimentStateError

    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.dispatch.queue("promote_best", decision("rc_cand"))

    async def refuse(exp_mem_id, res):
        raise ExperimentStateError(
            f"experiment {exp_mem_id} is verdict — refusing to record a verdict"
            " (allowed moves: none)"
        )

    ctx.memory.record_verdict = refuse

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "done"
    assert "fatal_cancel" not in [k for k, _ in ctx.log.events]
    outcomes = [f["outcome"] for k, f in ctx.log.events if k == "item_outcome"]
    assert outcomes and all(o.status == "completed" for o in outcomes)
    assert ctx.kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_cand"]
    writes = [f["write"] for k, f in ctx.log.events if k == "memory_write_failed"]
    assert writes.count("record_verdict") == len(outcomes)
    assert "stamp_promoted" in writes


async def test_val_stats_write_failure_leaves_the_promotion_intact():
    # Reporting-only by construction: the stats write gets the same 3-call
    # ladder as every other apply write, but exhaustion logs and continues —
    # a promotion must not fail because a stats write 500'd.
    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.dispatch.queue("promote_best", decision("rc_cand"))
    ctx.kiln.memory_post_behaviors[VAL_STATS_TAG] = ["http500"] * 3

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "done"  # the run survives
    # …and it is the SAME 3-call/20s ladder every other apply write gets —
    # only its exhaustion differs. Without this, dropping `_promotion_write`
    # and calling `record_val_stats` once would pass every other assertion.
    assert ctx.sleeps.count(20.0) >= 2
    # (a) landed, and so did (b)/(c) — the failure is scoped to the stats write.
    assert ctx.kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_cand"]
    assert ctx.kiln.memories["issue_1"]["overview"].startswith("FIXED: ")
    assert any("— PROMOTED" in m["overview"] for m in ctx.kiln.memories.values())
    assert not [m for m in ctx.kiln.memories.values() if VAL_STATS_TAG in m["tags"]]
    # …and it is not silent: the generic swallowed-write event names it.
    (failure,) = [f for k, f in ctx.log.events if k == "memory_write_failed"]
    assert (failure["write"], failure["rc_id"]) == ("val_stats", "rc_cand")
    assert failure["what"] == "val stats record"


async def test_regressed_issue_note_lands_in_log_never_in_memory():
    # End to end: the flag reopens the issue with the code-composed
    # line; the agent's free-prose note reaches ONLY the log.
    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.kiln.add_memory(
        "FIXED: stale ticket status", "task::t1", ["issue"], memory_id="issue_2"
    )
    ctx.dispatch.queue(
        "promote_best",
        decision(
            "rc_cand",
            regressed_issues=[
                RegressedIssue(issue_memory_id="issue_2", note="judge saw 2 fails")
            ],
        ),
    )
    await orchestrate(ctx, "", timeout_mins=180)
    issue = ctx.kiln.memories["issue_2"]
    assert issue["overview"] == "stale ticket status"  # reopened
    assert issue["content"] == (
        "- reopened when promoting baseline rc_cand. Issue repros on rc_cand."
    )
    assert "judge saw 2 fails" not in (issue["content"] or "")
    assert (
        "issue_regressed",
        {
            "issue_memory_id": "issue_2",
            "note": "judge saw 2 fails",
        },
    ) in ctx.log.events


async def test_landed_and_regressed_issue_gets_one_partial_line():
    # The winner's landed issue ALSO shows up in
    # regressed_issues → ONE merged mark_partial line — no FIXED:, no
    # reopen churn; the free-prose note still lands in the log only.
    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.dispatch.queue(
        "promote_best",
        decision(
            "rc_cand",
            regressed_issues=[
                RegressedIssue(issue_memory_id="issue_1", note="still 1 fail on val")
            ],
        ),
    )
    await orchestrate(ctx, "", timeout_mins=180)
    issue = ctx.kiln.memories["issue_1"]
    assert issue["overview"] == "issue body for issue_1"  # never FIXED:
    assert issue["content"] == (
        "- landed in merge rc_cand — reduced, not closed; still repros."
    )
    assert "- fixed" not in issue["content"]
    assert "reopened" not in issue["content"]
    assert "still 1 fail on val" not in issue["content"]
    assert (
        "issue_regressed",
        {"issue_memory_id": "issue_1", "note": "still 1 fail on val"},
    ) in ctx.log.events
    # The winner's experiment memory still gets stamped — only the issue
    # write is merged, the rest of the apply order is unchanged.
    assert any("— PROMOTED" in m["overview"] for m in ctx.kiln.memories.values())


async def test_merge_winner_mixed_issue_writes():
    # Merge winner landing issue_1 + issue_2 while the decision regresses
    # issue_2 + issue_3: landed-only → FIXED:, landed ∧ regressed → the
    # merged partial line, regressed-only → flag_regressed (unchanged).
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    for issue_id in ("issue_1", "issue_2"):
        seed_issue(kiln, issue_id)
    kiln.add_memory(
        "FIXED: stale ticket status", "task::t1", ["issue"], memory_id="issue_3"
    )
    seed_train(kiln, "rc_cand")
    seed_val(kiln, "rc_base", "rc_cand")
    merge_item = ExperimentItem(
        id="w1-1",
        type="experiment",
        evaluation_target=[EvalTarget(eval_id="eval_1")],
        promotion_candidate=True,
        work=MergeWork(
            wins=[
                WinRef(experiment_memory_id="mem_a", rc_id="rc_a"),
                WinRef(experiment_memory_id="mem_b", rc_id="rc_b"),
            ]
        ),
    )
    d = ctx.dispatch
    d.queue("batch_plan", ok(make_plan(merge_item)), ok(make_plan(loop_exit="done")))
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue(
        "analyze_experiment_results",
        ok(
            make_result(
                per_ingredient_outcomes=[
                    IngredientOutcome(
                        issue_memory_id="issue_1", landed=True, note="held"
                    ),
                    IngredientOutcome(
                        issue_memory_id="issue_2", landed=True, note="held"
                    ),
                ]
            )
        ),
    )
    d.queue(
        "promote_best",
        decision(
            "rc_cand",
            regressed_issues=[
                RegressedIssue(issue_memory_id="issue_2", note="1 val fail"),
                RegressedIssue(issue_memory_id="issue_3", note="2 val fails"),
            ],
        ),
    )
    await orchestrate(ctx, "", timeout_mins=180)
    assert kiln.memories["issue_1"]["overview"] == "FIXED: issue body for issue_1"
    assert kiln.memories["issue_1"]["content"].startswith("- fixed 07-22 by rc_cand")
    assert kiln.memories["issue_2"]["overview"] == "issue body for issue_2"
    assert kiln.memories["issue_2"]["content"] == (
        "- landed in merge rc_cand — reduced, not closed; still repros."
    )
    assert kiln.memories["issue_3"]["overview"] == "stale ticket status"  # reopened
    assert kiln.memories["issue_3"]["content"] == (
        "- reopened when promoting baseline rc_cand. Issue repros on rc_cand."
    )
    # Strict apply order: baseline PATCH → issue writes → stamp.
    # (Earlier patches exist — the analyzer's verdict write predates the gate.)
    patched_ids = [mid for mid, _ in kiln.memory_patches]
    baseline_at = patched_ids.index("mem_baseline")
    for issue_id in ("issue_1", "issue_2", "issue_3"):
        assert baseline_at < patched_ids.index(issue_id) < len(patched_ids) - 1
    assert "— PROMOTED" in kiln.memory_patches[-1][1]["overview"]


async def test_a_fix_item_whose_issue_did_not_land_is_not_marked_fixed():
    # A FixWork winner can be `successful` without its issue
    # closing — a partial win, or a gain that came from somewhere other than
    # the targeted issue. `kiln-harness-tuner-batch-selection` teaches planners to skip
    # `FIXED:` records as fix candidates, so a false closure hides a KNOWN
    # WORKING CURE from every later wave. An explicit landed=False row for the
    # item's own issue suppresses the write; the promotion itself is untouched.
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln)
    seed_train(kiln, "rc_cand")
    seed_val(kiln, "rc_base", "rc_cand")
    d = ctx.dispatch
    d.queue("batch_plan", ok(make_plan(fix_item("w1-1"))), ok(make_plan(loop_exit="d")))
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue(
        "analyze_experiment_results",
        ok(
            make_result(
                per_ingredient_outcomes=[
                    IngredientOutcome(
                        issue_memory_id="issue_1",
                        landed=False,
                        note="win came from the retry skill, not the issue",
                    )
                ]
            )
        ),
    )
    d.queue("promote_best", decision("rc_cand"))
    await orchestrate(ctx, "", timeout_mins=180)

    issue = kiln.memories["issue_1"]
    assert issue["overview"] == "issue body for issue_1"  # no FIXED: prefix
    assert "fixed 07-22" not in (issue["content"] or "")
    # The promotion still applied in full — this suppresses one issue write,
    # not the pointer, the stats record or the stamp.
    assert kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_cand"]
    assert "— PROMOTED" in kiln.memory_patches[-1][1]["overview"]


async def test_a_fix_item_without_ingredient_rows_still_closes_its_issue():
    # The unchanged default, pinned beside the new branch: rows are optional,
    # and an analyzer that emits none must not silently stop closing issues.
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln)
    seed_train(kiln, "rc_cand")
    seed_val(kiln, "rc_base", "rc_cand")
    d = ctx.dispatch
    d.queue("batch_plan", ok(make_plan(fix_item("w1-1"))), ok(make_plan(loop_exit="d")))
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))
    d.queue("promote_best", decision("rc_cand"))
    await orchestrate(ctx, "", timeout_mins=180)
    assert kiln.memories["issue_1"]["overview"] == "FIXED: issue body for issue_1"


async def test_a_fix_items_rows_about_other_issues_stay_ignored():
    # Only a row naming the item's OWN issue is
    # read. A row about some other issue is informative, never a write.
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln)
    seed_issue(kiln, "issue_2")
    seed_train(kiln, "rc_cand")
    seed_val(kiln, "rc_base", "rc_cand")
    d = ctx.dispatch
    d.queue("batch_plan", ok(make_plan(fix_item("w1-1"))), ok(make_plan(loop_exit="d")))
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue(
        "analyze_experiment_results",
        ok(
            make_result(
                per_ingredient_outcomes=[
                    IngredientOutcome(
                        issue_memory_id="issue_2", landed=True, note="also improved"
                    )
                ]
            )
        ),
    )
    d.queue("promote_best", decision("rc_cand"))
    await orchestrate(ctx, "", timeout_mins=180)
    assert kiln.memories["issue_1"]["overview"] == "FIXED: issue body for issue_1"
    assert kiln.memories["issue_2"]["overview"] == "issue body for issue_2"


async def test_a_winner_that_was_not_a_candidate_is_never_promoted():
    # A winner rc that no candidate carries has no val table and no integrity
    # check, so code refuses it: nothing is written, the candidates stay pooled.
    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.dispatch.queue(
        "promote_best",
        lambda req: decision(
            "rc_foreign",
            winner_experiment_memory_id=req.candidates[0].experiment_memory_id,
        ),
    )
    result = await orchestrate(ctx, "", timeout_mins=180)
    assert result == "done"
    assert "rc_rc_base" in ctx.kiln.memories["mem_baseline"]["tags"]
    assert "rc_foreign" not in ctx.kiln.memories["mem_baseline"]["overview"]
    (blocked,) = [f for k, f in ctx.log.events if k == "promotion_blocked"]
    assert blocked["winner_rc_id"] == "rc_foreign"
    assert blocked["reasons"] == ["integrity checks did not run on the winner"]
    assert not [m for m in ctx.kiln.memories.values() if VAL_STATS_TAG in m["tags"]]


# ---- val-gate rejection writeback ----

REJECTED_LINE = (
    "- rejected at the val gate 07-22 vs baseline rc_base — did not beat it; "
    "details in the run report."
)


def _experiment_memories(kiln) -> list[dict]:
    return [m for m in kiln.memories.values() if "experiment" in m["tags"]]


async def test_zero_winner_gate_stamps_every_candidate():
    # The zero-winner case: nothing is promoted, but both candidates were
    # compared and passed over — each record says so, in memory, at decision
    # time (no more "CONFIRMED … send to the val gate" after losing it).
    ctx = make_ctx()
    _two_candidate_scenario(ctx)
    ctx.dispatch.queue("promote_best", decision(None, rationale="neither beat val"))

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    records = _experiment_memories(ctx.kiln)
    assert len(records) == 2
    for record in records:
        assert record["overview"].endswith(" — REJECTED AT VAL 07-22")
        assert record["content"].endswith(REJECTED_LINE)
        assert "— PROMOTED" not in record["overview"]
    # Zero winners writes neither promotion record: the `baseline` pointer
    # does not move, and no `val` stats line is added.
    assert ctx.kiln.memories["mem_baseline"]["overview"] == BASELINE_OVERVIEW
    assert not [m for m in ctx.kiln.memories.values() if VAL_STATS_TAG in m["tags"]]
    recorded = [f for k, f in ctx.log.events if k == "rejection_recorded"]
    assert sorted(f["rc_id"] for f in recorded) == ["rc_w1-1", "rc_w1-2"]
    assert {f["exp_mem_id"] for f in recorded} == {r["id"] for r in records}


async def test_gate_losers_are_stamped_after_the_winner_applies():
    # A gate that promotes someone else still stamps the losers — and the
    # rejection writes land AFTER the promotion apply (a missing stamp is
    # recoverable noise; a missing promotion is not).
    ctx = make_ctx()
    _two_candidate_scenario(ctx)
    ctx.dispatch.queue("promote_best", decision("rc_w1-1"))

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    records = _experiment_memories(ctx.kiln)
    promoted = [r for r in records if "— PROMOTED 07-22" in r["overview"]]
    rejected = [r for r in records if "— REJECTED AT VAL 07-22" in r["overview"]]
    assert len(promoted) == 1
    assert len(rejected) == 1
    assert REJECTED_LINE not in promoted[0]["content"]
    assert rejected[0]["content"].endswith(REJECTED_LINE)
    # The loser's issue record still gets no writes (train verdicts
    # stand, the win stays merge-eligible as a re-designed experiment).
    assert not ctx.kiln.memories["issue_2"]["overview"].startswith("FIXED: ")
    overviews = [body.get("overview") or "" for _, body in ctx.kiln.memory_patches]
    promoted_at = next(i for i, o in enumerate(overviews) if "— PROMOTED" in o)
    rejected_at = next(i for i, o in enumerate(overviews) if "— REJECTED AT VAL" in o)
    assert promoted_at < rejected_at


async def test_a_candidate_that_never_reached_the_gate_is_not_stamped():
    # Edge case: only candidates a PromotionDecision actually
    # passed over are stamped. A build that failed never reached val — its
    # record keeps its own terminal state and says nothing about the gate.
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln, "issue_1")
    seed_issue(kiln, "issue_2")
    seed_train(kiln, "rc_w1-1")
    seed_val(kiln, "rc_base", "rc_w1-1")
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(
            make_plan(
                fix_item("w1-1", issue_memory_id="issue_1"),
                fix_item("w1-2", issue_memory_id="issue_2"),
            )
        ),
        ok(make_plan(loop_exit="done")),
    )

    def build(req):
        # parse_failure is terminal (no retry ladder) — w1-2 never builds an rc.
        return rc_ok("rc_w1-1") if req.item.id == "w1-1" else infra("parse_failure")

    d.queue("create_experiment_rc", build, build)
    d.queue("analyze_experiment_results", ok(make_result()))
    d.queue("promote_best", decision(None, rationale="no winner"))

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    records = _experiment_memories(kiln)
    assert len(records) == 2
    gated = [r for r in records if "— REJECTED AT VAL" in r["overview"]]
    assert len(gated) == 1
    (never_gated,) = [r for r in records if r["id"] != gated[0]["id"]]
    assert never_gated["overview"].endswith("— FAILED (infra: parse_failure)")
    assert REJECTED_LINE not in (never_gated["content"] or "")
    recorded = [f for k, f in ctx.log.events if k == "rejection_recorded"]
    assert [f["rc_id"] for f in recorded] == ["rc_w1-1"]


async def test_a_gate_blocked_on_a_constraint_holds_instead_of_rejecting():
    # When the promote agent escalates on an OPEN constraint, the
    # candidate is stamped HELD (naming the constraint), never REJECTED —
    # the same rc may be resubmitted unchanged once a human answers.
    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.dispatch.queue(
        "promote_best",
        decision(
            None,
            rationale="blocked on OPEN 424242093572: may a wrapper tool be promoted?",
            blocked_on="424242093572",
        ),
    )

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    record = ctx.kiln.memories[_SCENARIO_EXP_MEM]
    assert record["overview"].endswith(" — HELD (constraint 424242093572) 07-22")
    assert "REJECTED AT VAL" not in record["overview"]
    assert (
        "- held at the val gate 07-22 on OPEN constraint 424242093572"
        in record["content"]
    )
    holds = [f for k, f in ctx.log.events if k == "hold_recorded"]
    assert [(h["rc_id"], h["constraint_id"]) for h in holds] == [
        ("rc_cand", "424242093572")
    ]
    assert not [k for k, _ in ctx.log.events if k == "rejection_recorded"]
    # The baseline did not move.
    assert ctx.kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_base"]


async def test_planner_input_carries_capacity_and_measured_lever_costs():
    # The planner sees how many arms can run at once and what
    # each lever class cost to build so far this run (empty on wave 1).
    ctx = make_ctx()
    ctx.max_concurrent_agents = 4
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    seed_issue(ctx.kiln)
    seed_train(ctx.kiln, "rc_cand")
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result(successful=False)))

    await orchestrate(ctx, "", timeout_mins=180)

    first, second = d.calls_to("batch_plan")
    assert first.max_concurrent_agents == 4
    assert first.lever_costs == {}
    assert set(second.lever_costs) == {"prompt"}  # fix_item's technique
    assert second.lever_costs["prompt"].n == 1
    assert second.lever_costs["prompt"].mean_build_usd >= 0


async def test_rejection_write_failure_never_fails_the_gate():
    # Log-and-continue: the stamp is bookkeeping over a decision already
    # made. Both writes 500 (the verdict PATCH before them succeeds).
    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.dispatch.queue("promote_best", decision(None, rationale="no winner"))
    # PATCH order on the record: rc-built line, verdict, stamp, note (a
    # residual block that passes writes nothing).
    ctx.kiln.memory_patch_behaviors[_SCENARIO_EXP_MEM] = [
        "ok",
        "ok",
        "http500",
        "http500",
    ]

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    failures = [f for k, f in ctx.log.events if k == "memory_write_failed"]
    assert [f["write"] for f in failures] == ["stamp", "note"]
    assert all(
        f["rc_id"] == "rc_cand" and f["exp_mem_id"] == _SCENARIO_EXP_MEM
        for f in failures
    )
    # Nothing was written, so nothing is claimed: no rejection_recorded.
    assert not [k for k, _ in ctx.log.events if k == "rejection_recorded"]
    assert "REJECTED AT VAL" not in ctx.kiln.memories[_SCENARIO_EXP_MEM]["overview"]


async def test_half_written_rejection_is_not_claimed_as_recorded():
    # The admissible half-write: the two writes fail
    # independently, so the stamp can land while the note 500s. The record is
    # partly written — `rejection_recorded` must NOT claim otherwise, or a
    # later reader trusts a content line that was never appended.
    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.dispatch.queue("promote_best", decision(None, rationale="no winner"))
    # rc-built line, verdict, stamp, note.
    ctx.kiln.memory_patch_behaviors[_SCENARIO_EXP_MEM] = [
        "ok",
        "ok",
        "ok",
        "http500",
    ]

    assert await orchestrate(ctx, "", timeout_mins=180) == "done"

    failures = [f for k, f in ctx.log.events if k == "memory_write_failed"]
    assert [f["write"] for f in failures] == ["note"]
    assert not [k for k, _ in ctx.log.events if k == "rejection_recorded"]
    record = ctx.kiln.memories[_SCENARIO_EXP_MEM]
    assert record["overview"].endswith(" — REJECTED AT VAL 07-22")  # stamp landed
    assert REJECTED_LINE not in (record["content"] or "")  # note did not


# ---- tripwire ----


async def test_tripwire_aborts_wave_backs_off_and_continues():
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"), fix_item("w1-2"))),
        ok(make_plan(loop_exit="done")),
    )
    # parse_failure is terminal (no ladder) — both items fail the same class.
    d.queue("create_experiment_rc", infra("parse_failure"), infra("parse_failure"))

    result = await orchestrate(ctx, "", timeout_mins=180)
    assert result == "done"  # single tripwire continues
    assert 300.0 in ctx.sleeps  # the long backoff
    tripwires = [f for k, f in ctx.log.events if k == "tripwire"]
    assert tripwires == [{"wave": 1, "error_class": "parse_failure", "failed": 2}]
    # Both experiment memories re-stamped — ABANDONED (never the outcome).
    overviews = [
        m["overview"] for m in kiln.memories.values() if "experiment" in m["tags"]
    ]
    assert all(
        o.endswith("— ABANDONED (wave tripwire: parse_failure)") for o in overviews
    )
    assert len(overviews) == 2


async def test_tripwire_stamps_each_memory_with_its_own_error_class():
    # A minority-class failure riding a tripped wave keeps its OWN class in
    # the — ABANDONED reason, not the majority class that tripped the wave.
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"), fix_item("w1-2"), fix_item("w1-3"))),
        ok(make_plan(loop_exit="done")),
    )

    def rc_response(req):
        if req.item.id == "w1-3":
            return infra("rate_limit")  # the minority class (ladder retries)
        return infra("parse_failure")  # terminal — 2 of 3, trips the wave

    d.queue("create_experiment_rc", *([rc_response] * 6))  # 1 + 1 + 4 calls

    result = await orchestrate(ctx, "", timeout_mins=180)
    assert result == "done"
    # The trip event names the majority class and ITS count (2 of 3).
    tripwires = [f for k, f in ctx.log.events if k == "tripwire"]
    assert tripwires == [{"wave": 1, "error_class": "parse_failure", "failed": 2}]
    by_item = {
        m["overview"].split(" ")[1]: m["overview"]
        for m in kiln.memories.values()
        if "experiment" in m["tags"]
    }
    assert by_item["0722-w1-1"].endswith("— ABANDONED (wave tripwire: parse_failure)")
    assert by_item["0722-w1-2"].endswith("— ABANDONED (wave tripwire: parse_failure)")
    assert by_item["0722-w1-3"].endswith("— ABANDONED (wave tripwire: rate_limit)")


async def test_double_tripwire_exits_after_promotion_runs():
    # A measured winning candidate on the tripped second wave still
    # promotes before the tripwire exit.
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln)  # the winner's issue — mark_fixed reads then patches it
    seed_train(kiln, "rc_good")
    seed_val(kiln, "rc_base", "rc_good")
    d = ctx.dispatch

    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"), fix_item("w1-2"))),
        ok(make_plan(fix_item("w2-1"), fix_item("w2-2"), fix_item("w2-3"))),
    )

    def rc_response(req):
        if req.item.id == "w2-3":
            return rc_ok("rc_good")
        return infra("parse_failure")

    d.queue("create_experiment_rc", *([rc_response] * 5))
    d.queue("analyze_experiment_results", ok(make_result()))
    d.queue("promote_best", decision("rc_good"))

    result = await orchestrate(ctx, "", timeout_mins=180)
    assert result == "tripwire"
    # Promotion applied before the exit (baseline moved to the winner).
    assert kiln.memories["mem_baseline"]["overview"].startswith(
        "Baseline: run config rc_good"
    )
    assert [k for k, _ in ctx.log.events].count("tripwire") == 2
    # Exactly one 300s backoff (after wave 1; the exit path never sleeps).
    assert ctx.sleeps.count(300.0) == 1


# ---- budget / wrap-up ----


async def test_wrap_up_wave_is_planned_and_is_the_last():
    # clock: deadline calc, the two pre-flight phases (preflight + setup)
    # start/end, w1 boundary, w1 should_wrap, w1 planning phase start/end, w2
    # boundary, w2 should_wrap (1200s left: under the 30-min window, over
    # the 15-min floor → wrap-up).
    ctx = make_ctx(clock=FakeClock(0, 0, 0, 0, 0, 0, 0, 9600, 9600), eval_building=True)
    seed_baseline(ctx.kiln)
    seed_issue(ctx.kiln)
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(eval_only_item("w1-1"))),
        ok(make_plan(eval_only_item("w2-1"))),
    )
    d.queue("eval_build", ok(eval_report()), ok(eval_report()))

    result = await orchestrate(ctx, "", timeout_mins=180)
    assert result == "wrap_up"
    plan_inputs = d.calls_to("batch_plan")
    assert len(plan_inputs) == 2  # never a second wrap-up wave
    assert [p.should_wrap_up for p in plan_inputs] == [False, True]


async def test_under_the_floor_no_wave_is_planned():
    # 800s left is inside the wrap-up window
    # but under the 15-min floor — a planner session then costs $2 for a
    # plan whose arms are cancelled on arrival. Exit to the report instead.
    ctx = make_ctx(
        clock=FakeClock(0, 0, 0, 0, 0, 0, 0, 10000, 10000), eval_building=True
    )
    seed_baseline(ctx.kiln)
    seed_issue(ctx.kiln)
    d = ctx.dispatch
    d.queue("batch_plan", ok(make_plan(eval_only_item("w1-1"))))
    d.queue("eval_build", ok(eval_report()))

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "timeout"
    assert len(d.calls_to("batch_plan")) == 1  # no wrap-up planner session
    (exit_event,) = [f for k, f in ctx.log.events if k == "exit"]
    assert "floor" in exit_event["detail"]


async def test_hard_deadline_no_wave_starts_past_it():
    ctx = make_ctx(clock=FakeClock(0, 10800))
    seed_baseline(ctx.kiln)
    result = await orchestrate(ctx, "", timeout_mins=180)
    assert result == "timeout"
    assert ctx.dispatch.calls == []  # not even a planner call


# ---- startup ----


async def test_a_cold_task_is_seeded_by_setup_instead_of_dying():
    # The setup phase runs BEFORE require_baseline_record, so a task with no
    # `baseline` record does not die on it — it gets one, and the same run
    # uses it.
    ctx = make_ctx()
    ctx.kiln.run_configs = [make_run_config("rc_seeded")]
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))

    await orchestrate(ctx, "", timeout_mins=180)

    events = dict(ctx.log.events)
    assert events["baseline_seeded"]["rc_id"] == "rc_seeded"
    assert events["baseline_found"]["rc_id"] == "rc_seeded"
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert plan_input.baseline.rc_id == "rc_seeded"


async def test_a_task_without_run_configs_is_fatal_before_any_paid_dispatch():
    # The one state with no mechanical answer. It is what a bare FakeKiln
    # hits: BaselineRecordMissingError stays as the fail-fast for a record
    # deleted mid-run, but setup gets there first.
    ctx = make_ctx()
    with pytest.raises(SetupPreconditionError, match="has no run configs"):
        await orchestrate(ctx, "", timeout_mins=180)
    assert ctx.dispatch.calls == []
    assert ctx.kiln.memory_posts == []
    # "Paid" excludes the once-per-run MCP-lane preflight, which by design
    # runs first and costs pennies — it is a real `claude -p` session, and
    # `calls` alone would not have said so.
    assert ctx.dispatch.preflight_calls == 1


async def test_janitor_stamps_crash_debris_at_startup():
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    stale = kiln.add_memory(
        "EXP 0721-w9-9 (prompt): stale — RUNNING", "task::t1", ["experiment"]
    )
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "", timeout_mins=180)
    assert kiln.memories[stale]["overview"] == (
        "EXP 0721-w9-9 (prompt): stale — ABANDONED (orchestrator restart 07-22)"
    )
    # The sweep result is an event (count + ids), not silence.
    events = dict(ctx.log.events)
    assert events["janitor"] == {"count": 1, "memory_ids": [stale]}


async def test_preflight_events_voice_baseline_and_clean_janitor():
    # run_start leads, then the pre-flight steps each say what they found —
    # baseline rc + provenance, and a zero-count janitor event (the UIs'
    # "pre-flight checks passed").
    # Order: MCP lane, setup, janitor, baseline.
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "", timeout_mins=180)
    kinds = [k for k, _ in ctx.log.events]
    assert kinds[:9] == [
        "run_start",
        "phase_start",  # MCP-lane preflight — before setup, which dispatches
        "phase_end",
        "preflight",
        "spend",
        "phase_start",  # setup
        "setup_ok",  # nothing to sync on a task with no evals
        "phase_end",
        "janitor",
    ]
    assert kinds[9] == "baseline_found"
    events = dict(ctx.log.events)
    # The rc id alone — the record's overview is free prose, so there is no
    # bounded provenance string to carry.
    assert events["baseline_found"] == {"rc_id": "rc_base"}
    assert events["janitor"] == {"count": 0, "memory_ids": []}


# ---- planner-driven exits ----


async def test_loop_exit_writes_open_unknowns_before_exit():
    # An exit plan carries no items by schema (items + loop_exit is a
    # ValidationError), but its open_questions still land as OPEN: records
    # before the run ends.
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    question = OpenUnknown(
        question="is latency in scope",
        default_assumed="no",
        gates="tool experiments",
        reask_when="re-ask at kickoff review",
    )
    ctx.dispatch.queue(
        "batch_plan",
        ok(make_plan(loop_exit="plateau", open_questions=[question])),
    )
    result = await orchestrate(ctx, "", timeout_mins=180)
    assert result == "plateau"
    assert ctx.dispatch.calls_to("create_experiment_rc") == []
    open_records = [
        m for m in ctx.kiln.memories.values() if m["overview"].startswith("OPEN: ")
    ]
    assert len(open_records) == 1
    assert open_records[0]["scope"] == "project"


async def test_empty_plan_exits():
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    ctx.dispatch.queue("batch_plan", ok(make_plan()))
    assert await orchestrate(ctx, "", timeout_mins=180) == "empty_plan"


async def test_planner_hard_fail_exits_after_loop_ladder():
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    ctx.dispatch.queue("batch_plan", *([infra("provider_error")] * 5))
    result = await orchestrate(ctx, "", timeout_mins=180)
    assert result == "planner_failed"
    assert len(ctx.dispatch.calls_to("batch_plan")) == 5
    assert ctx.sleeps == [30.0, 60.0, 120.0, 300.0]


async def test_rejected_summary_is_the_one_in_context_carry():
    ctx = make_ctx(eval_building=True)
    seed_baseline(ctx.kiln)
    seed_issue(ctx.kiln)
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(
            make_plan(
                eval_only_item("w1-1"),
                rejected_summary="passed on mem_84 again — 2nd time, eval-build cost",
            )
        ),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("eval_build", ok(eval_report()))
    await orchestrate(ctx, "", timeout_mins=180)
    plan_inputs = d.calls_to("batch_plan")
    assert plan_inputs[0].prior_rejected_summary is None
    assert plan_inputs[1].prior_rejected_summary == (
        "passed on mem_84 again — 2nd time, eval-build cost"
    )
    # The kickoff guidance rides every wave verbatim.
    assert {p.guidance for p in plan_inputs} == {""}


# ---- eval sizes on the planner's input ----


async def test_batch_plan_input_carries_a_count_per_active_eval():
    # Counted from the BASELINE's stored train runs: what a comparison can
    # actually pair, not the dataset's nominal size.
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_issue(kiln)
    seed_active_eval(kiln)
    kiln.evals["eval_2"] = make_eval("eval_2", "ec_2")
    kiln.specs.append({"id": "spec_2", "eval_id": "eval_2", "status": "active"})
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run(f"t{i}") for i in range(17)
    ]
    kiln.results[("eval_2", "rc_base", "train")] = [make_run("t1"), make_run("t2")]
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "", timeout_mins=180)
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert plan_input.eval_sizes == {"eval_1": 17, "eval_2": 2}


async def test_eval_sizes_count_pairable_items_not_stored_records():
    # CR: `len(runs)` is a RECORD count. `compare_rows` pairs by item id, so
    # duplicate records for one item collapse and a record with no item id
    # drops out entirely — and both inflate the number exactly in the
    # partial-coverage case this feature exists to expose.
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_issue(kiln)
    seed_active_eval(kiln)
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("t1"),
        make_run("t1"),  # the retry ladder re-ran this item
        make_run("t2"),
        {"scores": {}, "task_run_usage": {}, "eval_usage": {}},  # no item id
    ]
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "", timeout_mins=180)
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert plan_input.eval_sizes == {"eval_1": 2}


async def test_eval_sizes_count_the_train_split_only():
    # Handing the planner val-split structure buys nothing and costs the
    # information-flow guarantee.
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_issue(kiln)
    seed_active_eval(kiln)
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("t1", accuracy=1.0),
        make_run("t2", accuracy=1.0),
    ]
    kiln.results[("eval_1", "rc_base", "val")] = [
        make_run(f"v{i}", accuracy=0.0) for i in range(9)
    ]
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "", timeout_mins=180)
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert plan_input.eval_sizes == {"eval_1": 2}
    assert all(
        query[2] == "train"
        for query in kiln.results_queries
        if query[:2] == ("eval_1", "rc_base")
    )
    # The per-score detectability rows read the same TRAIN rows
    # — n and room from the two train items, never the nine val ones.
    (row,) = plan_input.train_detectability
    assert (row.n, row.room) == (2, 0)


async def test_eval_sizes_are_refetched_every_wave():
    # An `eval_only` item can build a new eval mid-run, so a wave-1 snapshot
    # would go stale exactly when it matters.
    ctx = make_ctx(eval_building=True)
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_issue(kiln)
    seed_active_eval(kiln)
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("t1"), make_run("t2")]

    def build_the_second_eval(_req):
        kiln.evals["eval_2"] = make_eval("eval_2", "ec_2")
        kiln.specs.append({"id": "spec_2", "eval_id": "eval_2", "status": "active"})
        kiln.results[("eval_2", "rc_base", "train")] = [make_run("t1")]
        return ok(eval_report())

    ctx.dispatch.queue(
        "batch_plan",
        ok(make_plan(eval_only_item("w1-1"))),
        ok(make_plan(loop_exit="done")),
    )
    ctx.dispatch.queue("eval_build", build_the_second_eval)
    await orchestrate(ctx, "", timeout_mins=180)
    wave1, wave2 = ctx.dispatch.calls_to("batch_plan")
    assert wave1.eval_sizes == {"eval_1": 2}
    assert wave2.eval_sizes == {"eval_1": 2, "eval_2": 1}


async def test_a_failed_size_read_never_costs_a_wave():
    # Advisory input on the critical path: a 500 on a count read drops that
    # eval from the map (absent, not zero — zero would read as a measured
    # emptiness the planner should act on) and the wave plans anyway.
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_issue(kiln)
    seed_active_eval(kiln)
    kiln.evals["eval_2"] = make_eval("eval_2", "ec_2")
    kiln.specs.append({"id": "spec_2", "eval_id": "eval_2", "status": "active"})
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("t1")]
    kiln.results_behaviors[("eval_2", "rc_base", "train")] = ["http500"]
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    result = await orchestrate(ctx, "", timeout_mins=180)
    assert result == "done"
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert plan_input.eval_sizes == {"eval_1": 1}
    assert ("eval_size_unavailable", {"eval_id": "eval_2"}) in [
        (name, body) for name, body in ctx.log.events
    ]


async def test_a_failed_active_set_listing_degrades_to_an_empty_map():
    # The other half of the same guarantee, asserted on the helper directly:
    # the active-set listing is a NEW read on the critical path before the
    # plan dispatch, so it gets the same treatment as the per-eval counts.
    # (Not driven through `orchestrate`: the SETUP phase reads `/evals` too,
    # where a 500 is correctly fatal — a whole-run stub could not tell the two
    # readers apart.)
    ctx = make_ctx()
    seed_active_eval(ctx.kiln)
    inner_call = ctx.kiln.call

    async def fail_the_eval_listing(method, url_path, **kwargs):
        if url_path.endswith("/evals"):
            raise KilnAPIError(method, url_path, 500, {"detail": "flaky"})
        return await inner_call(method, url_path, **kwargs)

    ctx.kiln.call = fail_the_eval_listing
    assert await _eval_train_sizes(ctx, "rc_base") == {}
    assert ("eval_sizes_unavailable", {}) in [
        (name, body) for name, body in ctx.log.events
    ]


async def test_a_reshaped_spec_listing_fails_the_active_set_loudly():
    # Read as "no specs", an envelope would make every archived eval active.
    ctx = make_ctx()
    seed_active_eval(ctx.kiln)
    ctx.kiln.specs = {"specs": [{"eval_id": "eval_1", "status": "archived"}]}
    with pytest.raises(SetupPreconditionError, match="/specs did not return"):
        await _active_evals(ctx)


async def test_an_empty_spec_listing_body_reads_as_no_specs():
    ctx = make_ctx()
    seed_active_eval(ctx.kiln)
    ctx.kiln.specs = None
    assert [(e["id"], c) for e, c in await _active_evals(ctx)] == [("eval_1", "ec_1")]


async def test_the_active_set_leaves_out_the_evals_kiln_cant_load():
    ctx = make_ctx()
    seed_active_eval(ctx.kiln)
    ctx.kiln.evals["eval_2"] = make_eval("eval_2", "ec_2")
    ctx.unloadable_evals = {"eval_2": ("Tone", "Invalid Jinja2 template")}
    assert [(e["id"], c) for e, c in await _active_evals(ctx)] == [("eval_1", "ec_1")]


async def test_a_run_goes_on_without_an_eval_kiln_cant_load():
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    ctx.kiln.evals["eval_2"] = make_eval("eval_2", "ec_2", name="Tone")
    ctx.kiln.eval_configs["ec_2"] = KilnAPIError(
        "GET", "config", 422, {"error_messages": ["Invalid Jinja2 template"]}
    )
    inner_call = ctx.kiln.call
    eval_2_reads: list[str] = []

    async def kiln_cant_load_eval_2(method, url_path, *args, **kwargs):
        # Kiln answers every read that loads the eval's config the same way.
        if "/evals/eval_2/eval_config/ec_2/" in url_path:
            eval_2_reads.append(url_path)
            raise KilnAPIError(method, url_path, 422, {"detail": "unloadable"})
        return await inner_call(method, url_path, *args, **kwargs)

    ctx.kiln.call = kiln_cant_load_eval_2
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))

    await orchestrate(ctx, "", timeout_mins=180)

    assert ctx.unloadable_evals == {"eval_2": ("Tone", "Invalid Jinja2 template")}
    assert [f["eval_id"] for k, f in ctx.log.events if k == "eval_unloadable"] == [
        "eval_2"
    ]
    assert "contract_ok" in [k for k, _ in ctx.log.events]
    assert eval_2_reads == []


async def test_a_run_goes_on_past_an_archived_eval_kiln_cant_load():
    # Listed first, so the contract preflight would probe its stored results
    # first; Kiln answers that read the same way as its config.
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    ctx.kiln.evals["eval_0"] = make_eval("eval_0", "ec_0", name="Old")
    seed_active_eval(ctx.kiln)
    ctx.kiln.specs.append({"id": "spec_0", "eval_id": "eval_0", "status": "archived"})
    ctx.kiln.eval_configs["ec_0"] = KilnAPIError(
        "GET", "config", 400, {"error_messages": ["Invalid Jinja2 template"]}
    )
    inner_call = ctx.kiln.call
    eval_0_reads: list[str] = []

    async def kiln_cant_load_eval_0(method, url_path, *args, **kwargs):
        if "/evals/eval_0/eval_config/ec_0/" in url_path:
            eval_0_reads.append(url_path)
            raise KilnAPIError(method, url_path, 422, {"detail": "unloadable"})
        return await inner_call(method, url_path, *args, **kwargs)

    ctx.kiln.call = kiln_cant_load_eval_0
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))

    await orchestrate(ctx, "", timeout_mins=180)

    assert ctx.unloadable_eval_ids == frozenset({"eval_0"})
    # Not in the run, so not news.
    assert ctx.unloadable_evals == {}
    assert "eval_unloadable" not in [k for k, _ in ctx.log.events]
    assert "contract_ok" in [k for k, _ in ctx.log.events]
    assert eval_0_reads == []


async def test_hard_4xx_from_measurement_is_fatal():
    # The 422 missing-split answer propagates out.
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    # The parent's stored train rows order the engine's queue.
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("t1", accuracy=0.0)]
    kiln.job_behaviors[("eval_1", "rc_cand", "train")] = ["http422"]
    d = ctx.dispatch
    d.queue("batch_plan", ok(make_plan(fix_item("w1-1"))))
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    with pytest.raises(KilnAPIError) as exc:
        await orchestrate(ctx, "", timeout_mins=180)
    assert exc.value.status_code == 422


# ---- eval building: experimental, off by default ----


def builds_an_eval_fix(item_id: str, issue_memory_id: str):
    return fix_item(
        item_id,
        promotion_candidate=False,
        needs_eval=True,
        eval_id=None,
        issue_memory_id=issue_memory_id,
    )


def dropped_events(ctx) -> list[dict]:
    return [f for k, f in ctx.log.events if k == "eval_item_dropped"]


async def test_eval_building_off_drops_eval_items_and_runs_the_rest(caplog):
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    seed_train(ctx.kiln, "rc_cand")
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(
            make_plan(
                fix_item("w1-1", promotion_candidate=False),
                eval_only_item("w1-2", "issue_2"),
                builds_an_eval_fix("w1-3", "issue_3"),
            )
        ),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))

    with caplog.at_level("WARNING", logger="kiln_harness_tuner.items"):
        result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "done"
    assert d.calls_to("eval_build") == []
    # The planner is told, every wave, that eval building is off.
    assert [p.eval_building for p in d.calls_to("batch_plan")] == [False, False]
    dropped = dropped_events(ctx)
    assert [(f["item_id"], f["item_kind"], f["issue_memory_id"]) for f in dropped] == [
        ("w1-2", "eval", "issue_2"),
        ("w1-3", "fix", "issue_3"),
    ]
    assert dropped[0]["line"] == (
        "dropped w1-2: it builds a new eval for issue issue_2, and eval"
        " building is off (experimental: --experimental-eval-building turns it on)"
    )
    # One log line per dropped item, the same words.
    assert [
        r.getMessage() for r in caplog.records if r.name == "kiln_harness_tuner.items"
    ] == [f["line"] for f in dropped]
    # The next planner is told what was dropped, so it knows they never ran.
    assert [p.dropped_eval_items for p in d.calls_to("batch_plan")] == [
        [],
        [f["line"] for f in dropped],
    ]
    # Some items were left, so the wave ran without a second plan.
    assert not [k for k, _ in ctx.log.events if k == "plan_all_items_dropped"]
    # The wave runs, and shows, only the item that is left.
    (wave_start,) = [f for k, f in ctx.log.events if k == "wave_start"]
    assert [i["item_id"] for i in wave_start["items"]] == ["w1-1"]
    assert wave_start["plan_headline"] == "1 experiment"
    outcomes = [f["outcome"] for k, f in ctx.log.events if k == "item_outcome"]
    assert [(o.item.id, o.status) for o in outcomes] == [("w1-1", "completed")]
    assert dict(ctx.log.events)["run_start"]["eval_building"] is False


async def test_eval_building_off_a_plan_of_eval_items_only_is_planned_again():
    # One planner mistake must not end the run: when code drops every item,
    # the planner plans the same wave once more, told what was dropped.
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    seed_train(ctx.kiln, "rc_cand")
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(eval_only_item("w1-1"))),
        ok(make_plan(fix_item("w1-2", promotion_candidate=False))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "done"
    assert d.calls_to("eval_build") == []
    (dropped,) = dropped_events(ctx)
    assert dropped["item_id"] == "w1-1"
    assert [p.dropped_eval_items for p in d.calls_to("batch_plan")] == [
        [],
        [dropped["line"]],
        [dropped["line"]],
    ]
    (all_dropped,) = [f for k, f in ctx.log.events if k == "plan_all_items_dropped"]
    assert all_dropped == {"wave": 1, "phase": "plan", "dropped": 1}
    (wave_start,) = [f for k, f in ctx.log.events if k == "wave_start"]
    assert [i["item_id"] for i in wave_start["items"]] == ["w1-2"]


async def test_eval_building_off_a_second_plan_of_eval_items_only_exits_empty():
    # The second plan is the last one for the wave: no third dispatch.
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(eval_only_item("w1-1"))),
        ok(make_plan(eval_only_item("w1-2"))),
    )

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "empty_plan"
    assert d.calls_to("eval_build") == []
    assert len(d.calls_to("batch_plan")) == 2
    assert [f["item_id"] for f in dropped_events(ctx)] == ["w1-1", "w1-2"]
    assert len([k for k, _ in ctx.log.events if k == "plan_all_items_dropped"]) == 1


async def test_eval_building_off_the_wave_runner_drops_what_any_path_brings():
    # `_run_wave` is the one way into `execute_plan_item`: an eval item that
    # did not come through the planning call is dropped there too.
    ctx = make_ctx()

    outcomes, deadline_hit = await _run_wave(
        ctx, [eval_only_item("w1-1")], BaselineRef(rc_id="rc_base"), [], 1
    )

    assert (outcomes, deadline_hit) == ([], False)
    assert ctx.dispatch.calls == []
    assert [f["item_id"] for f in dropped_events(ctx)] == ["w1-1"]


async def test_eval_building_on_runs_eval_items_as_planned():
    ctx = make_ctx(eval_building=True)
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    seed_train(ctx.kiln, "rc_cand")
    seed_issue(ctx.kiln, "issue_1")
    seed_issue(ctx.kiln, "issue_2")
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(
            make_plan(
                eval_only_item("w1-1", "issue_2"),
                builds_an_eval_fix("w1-2", "issue_1"),
            )
        ),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("eval_build", ok(eval_report()), ok(eval_report()))
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "done"
    assert [p.eval_building for p in d.calls_to("batch_plan")] == [True, True]
    assert sorted(r.issue_memory_id for r in d.calls_to("eval_build")) == [
        "issue_1",
        "issue_2",
    ]
    assert dropped_events(ctx) == []
    outcomes = [f["outcome"] for k, f in ctx.log.events if k == "item_outcome"]
    assert sorted((o.item.id, o.status) for o in outcomes) == [
        ("w1-1", "completed"),
        ("w1-2", "completed"),
    ]
    assert dict(ctx.log.events)["run_start"]["eval_building"] is True


# ---- the N=1 degenerate case ----


async def test_n1_single_item_waves():
    """Single-item waves with no batch overhead beyond one planner call
    per wave: the full pipeline (build → measure → analyze → promote) runs,
    then a knowledge-only wave, then the planner ends the loop."""
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln, "issue_1")
    seed_issue(kiln, "issue_2")
    seed_train(kiln, "rc_cand")
    seed_val(kiln, "rc_base", "rc_cand")
    # Wave 2's fix flows from the NEW baseline (rc_cand was published to the
    # train cache by wave 1's measurement — the stored-parent compare is free).
    kiln.pending[("eval_1", "rc_2", "train")] = [
        make_run("t1", accuracy=1.0),
        make_run("t2", accuracy=1.0),
    ]

    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1", issue_memory_id="issue_1"))),
        ok(
            make_plan(
                fix_item("w2-1", issue_memory_id="issue_2", promotion_candidate=False)
            )
        ),
        ok(make_plan(loop_exit="out of worthwhile experiments")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"), rc_ok("rc_2"))
    d.queue(
        "analyze_experiment_results",
        ok(make_result()),
        ok(make_result(overview="EXP 0722-w2-1: knowledge-only — +2%, n=2, p=.4")),
    )
    d.queue("promote_best", decision("rc_cand"))

    result = await orchestrate(ctx, "one wave, cheapest issues", timeout_mins=180)
    assert result == "out of worthwhile experiments"
    # Wave 1 promoted; wave 2 (promotion_candidate=False) never paid val.
    assert len(d.calls_to("promote_best")) == 1
    assert kiln.memories["mem_baseline"]["overview"].startswith(
        "Baseline: run config rc_cand"
    )
    val_jobs = [j for j in kiln.job_invocations if j[2] == "val"]
    assert ("eval_1", "rc_2", "val") not in val_jobs
    # Wave 2's planner and analysis flowed from the promoted baseline.
    assert d.calls_to("batch_plan")[1].baseline.rc_id == "rc_cand"


# ---- instrumentation events ----


async def test_run_start_and_enriched_wave_start_events():
    ctx = make_ctx(clock=FakeClock(0.0), eval_building=True)
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    seed_train(ctx.kiln, "rc_cand")
    wave_plan = make_plan(
        fix_item("w1-1", promotion_candidate=False),
        eval_only_item("w1-2", "issue_2"),
    )
    ctx.dispatch.queue(
        "batch_plan",
        ok(wave_plan, cost_usd=0.4),
        ok(make_plan(loop_exit="done")),
    )
    ctx.dispatch.queue("create_experiment_rc", rc_ok("rc_cand"))
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    ctx.dispatch.queue("eval_build", ok(eval_report()))
    seed_issue(ctx.kiln, "issue_2")

    await orchestrate(ctx, "focus evals X", timeout_mins=180)

    run_start = dict(ctx.log.events)["run_start"]
    assert run_start["timeout_mins"] == 180
    assert run_start["deadline"] == 180 * 60.0  # Ctx.clock units (FakeClock@0)
    # Audit-trail twin: wall-clock ISO, parseable (a raw monotonic float is
    # uninterpretable in the wave log).
    datetime.fromisoformat(run_start["deadline_iso"])
    (wave_start,) = [f for k, f in ctx.log.events if k == "wave_start"]
    assert {k: v for k, v in wave_start.items() if k != "items"} == {
        "wave": 1,
        "should_wrap_up": False,
        "baseline_rc_id": "rc_base",
        "plan_headline": "1 experiment, 1 eval-only",
        "rejected_summary": "nothing else worth it",
    }
    assert [
        {k: item[k] for k in ("item_id", "kind", "label")}
        for item in wave_start["items"]
    ] == [
        {"item_id": "w1-1", "kind": "fix", "label": "retry-skill for 429s (issue_1)"},
        {"item_id": "w1-2", "kind": "eval", "label": "build eval for issue_2"},
    ]
    # Each entry also carries the item's details (`status.item_details`).
    assert wave_start["items"] == [item_summary(i) for i in wave_plan.items]
    # Dispatch spend rides the wave log/UI via `spend` events.
    spends = [(f["source"], f["cost_usd"]) for k, f in ctx.log.events if k == "spend"]
    assert ("batch_plan", 0.4) in spends


async def test_planning_phase_brackets_every_batch_plan_dispatch():
    # Every batch_plan gets a phase_start with a display label and a matching
    # phase_end with elapsed; the wrap-up wave says so. Four leading zeros:
    # the preflight and setup phases each read the clock twice before wave 1.
    ctx = make_ctx(
        clock=FakeClock(0, 0, 0, 0, 0, 0, 0, 0, 0, 9600, 9600), eval_building=True
    )
    seed_baseline(ctx.kiln)
    seed_issue(ctx.kiln)
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(eval_only_item("w1-1"))),
        ok(make_plan(eval_only_item("w2-1"))),
    )
    d.queue("eval_build", ok(eval_report()), ok(eval_report()))
    await orchestrate(ctx, "", timeout_mins=180)

    starts = [f for k, f in ctx.log.events if k == "phase_start"]
    ends = [f for k, f in ctx.log.events if k == "phase_end"]
    assert [s["name"] for s in starts] == ["preflight", "setup", "planning", "planning"]
    starts = starts[2:]
    assert starts[0]["wave"] == 1
    assert starts[0]["label"] == (
        "Planning wave 1… (a full agent session — typically 3-10 min)"
    )
    assert starts[1]["label"].startswith("Planning wrap-up wave 2…")
    assert [e["name"] for e in ends] == ["preflight", "setup", "planning", "planning"]
    ends = ends[2:]
    assert ends[0]["wave"] == 1
    assert ends[0]["elapsed_secs"] == 0.0  # FakeClock: same reading both ends
    # Ordering: the phase closes before the wave (or exit) event lands.
    kinds = [k for k, _ in ctx.log.events]
    assert kinds.index("phase_end") < kinds.index("wave_start")
    # Every wave ends with wave_end — the UI's scrollback-flush trigger —
    # even without promotion candidates (eval_only waves).
    assert [f["wave"] for k, f in ctx.log.events if k == "wave_end"] == [1, 2]


async def test_planning_phase_ends_even_when_dispatcher_raises():
    # A raise (or Ctrl-C → CancelledError) mid-dispatch must not
    # leave a dangling phase_start in the audit trail.
    ctx = make_ctx()
    seed_baseline(ctx.kiln)

    def boom(_req):
        raise RuntimeError("dispatcher blew up")

    ctx.dispatch.queue("batch_plan", boom)
    with pytest.raises(RuntimeError):
        await orchestrate(ctx, "", timeout_mins=180)
    # 3 phases: the startup preflight + setup, then the (aborted) planning
    # phase — starts and ends still pair up exactly.
    kinds = [k for k, _ in ctx.log.events]
    assert kinds.count("phase_start") == kinds.count("phase_end") == 3


async def test_planning_phase_ends_even_on_planner_hard_fail():
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    ctx.dispatch.queue("batch_plan", *([infra("provider_error")] * 5))
    await orchestrate(ctx, "", timeout_mins=180)
    # 3 phases: the startup preflight + setup, then the failed planning phase.
    kinds = [k for k, _ in ctx.log.events]
    assert kinds.count("phase_start") == kinds.count("phase_end") == 3
    assert kinds.index("phase_end") < kinds.index("exit")
    # The loop ladder's 4 retries are voiced too.
    retries = [f for k, f in ctx.log.events if k == "dispatch_retry"]
    assert [r["backoff_secs"] for r in retries] == [30.0, 60.0, 120.0, 300.0]
    assert [r["attempt"] for r in retries] == [1, 2, 3, 4]
    assert all(
        r["source"] == "batch_plan"
        and r["item_id"] is None
        and r["error_class"] == "provider_error"
        for r in retries
    )


async def test_promotion_gate_phase_brackets_val_pass_and_decision():
    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.dispatch.queue("promote_best", decision("rc_cand"))
    await orchestrate(ctx, "", timeout_mins=180)
    starts = [f for k, f in ctx.log.events if k == "phase_start"]
    gate = next(s for s in starts if s["name"] == "promotion_gate")
    assert gate["label"] == "Promotion gate: measuring 1 candidate on val…"
    assert gate["wave"] == 1  # gate segments attribute to their wave (CR)
    gate_end_fields = next(
        f
        for k, f in ctx.log.events
        if k == "phase_end" and f.get("name") == "promotion_gate"
    )
    assert gate_end_fields["wave"] == 1
    kinds = [k for k, _ in ctx.log.events]
    gate_start = next(
        i
        for i, (_k, f) in enumerate(ctx.log.events)
        if f.get("name") == "promotion_gate"
    )
    # The gate opens before any val work and closes after the promotion event.
    assert gate_start < kinds.index("promotion")
    gate_end = next(
        i
        for i, (k, f) in enumerate(ctx.log.events)
        if k == "phase_end" and f.get("name") == "promotion_gate"
    )
    assert gate_end > kinds.index("promotion")
    # Scrollback ordering: wave_end lands AFTER the promotion
    # event (banner final) and BEFORE the gate's phase_end, so the UI freezes
    # the block before the done-line prints.
    wave_end = kinds.index("wave_end")
    assert kinds.index("promotion") < wave_end < gate_end


async def test_promotion_gate_phase_ends_even_on_fatal_apply_write():
    # try/finally: the write-ladder exhaustion still closes the phase —
    # and still emits wave_end (the block freezes even on the fatal path).
    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.dispatch.queue("promote_best", decision("rc_cand"))
    ctx.kiln.memory_patch_behaviors["mem_baseline"] = ["http500"] * 3
    with pytest.raises(KilnAPIError):
        await orchestrate(ctx, "", timeout_mins=180)
    ends = [f for k, f in ctx.log.events if k == "phase_end"]
    assert any(e["name"] == "promotion_gate" for e in ends)
    assert [f["wave"] for k, f in ctx.log.events if k == "wave_end"] == [1]


async def test_tripwire_backoff_phase_brackets_the_long_sleep():
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"), fix_item("w1-2"))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", infra("parse_failure"), infra("parse_failure"))
    await orchestrate(ctx, "", timeout_mins=180)
    starts = [f for k, f in ctx.log.events if k == "phase_start"]
    backoff = next(s for s in starts if s["name"] == "tripwire_backoff")
    assert backoff["wave"] == 1
    assert backoff["label"] == "Tripwire backoff — pausing 5m before the next wave…"
    assert any(
        f.get("name") == "tripwire_backoff"
        for k, f in ctx.log.events
        if k == "phase_end"
    )
    # The wave freezes (wave_end) before the backoff phase opens.
    kinds = [k for k, _ in ctx.log.events]
    backoff_start = next(
        i
        for i, (k, f) in enumerate(ctx.log.events)
        if k == "phase_start" and f.get("name") == "tripwire_backoff"
    )
    assert kinds.index("wave_end") < backoff_start


async def test_promotion_event_carries_full_decision():
    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.dispatch.queue("promote_best", decision("rc_cand"))
    await orchestrate(ctx, "", timeout_mins=180)
    promotions = [f for k, f in ctx.log.events if k == "promotion"]
    assert len(promotions) == 1
    assert promotions[0]["winner_rc_id"] == "rc_cand"
    assert promotions[0]["decision"].winner_rc_id == "rc_cand"  # WaveStatus.promotion


async def test_val_pass_spend_reaches_accounting():
    # The val pass is real measurement spend — one `spend`
    # event (source=val_pass) sums the assembled tables' row spend.
    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.kiln.pending[("eval_1", "rc_base", "val")] = [
        make_run("v1", cost=0.1, accuracy=0.0),
        make_run("v2", cost=0.1, accuracy=0.5),
    ]
    ctx.kiln.pending[("eval_1", "rc_cand", "val")] = [
        make_run("v1", cost=0.2, accuracy=1.0),
        make_run("v2", cost=0.2, accuracy=1.0),
    ]
    ctx.dispatch.queue("promote_best", decision("rc_cand"))
    await orchestrate(ctx, "", timeout_mins=180)
    spends = {f["source"]: f["cost_usd"] for k, f in ctx.log.events if k == "spend"}
    assert spends["val_pass"] == pytest.approx(0.6)  # candidate 0.4 + fill 0.2
    # Cost split: val jobs are provider money; the
    # promote_best dispatch is subscription-equivalent quota burn.
    lanes = {f["source"]: f["lane"] for k, f in ctx.log.events if k == "spend"}
    assert lanes["val_pass"] == "provider"
    assert lanes["promote_best"] == "subscription_equivalent"
    assert lanes["batch_plan"] == "subscription_equivalent"


# ---- once-per-run MCP-lane preflight ----


async def test_preflight_runs_first_before_setup_and_any_planning():
    # The MCP check runs AHEAD of the janitor because the setup phase
    # dispatches agents — a broken lane must fail before that, not during it.
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "", timeout_mins=180)
    assert ctx.dispatch.preflight_calls == 1
    kinds = [k for k, _ in ctx.log.events]

    def phase_start_index(name: str) -> int:
        return next(
            i
            for i, (k, f) in enumerate(ctx.log.events)
            if k == "phase_start" and f["name"] == name
        )

    preflight_start = phase_start_index("preflight")
    assert preflight_start < phase_start_index("setup")
    assert preflight_start < kinds.index("janitor")
    assert preflight_start < phase_start_index("planning")
    # Durable wave-log evidence the lane was proven (standalone event,
    # symmetric with baseline_found/janitor).
    (preflight_fields,) = [f for k, f in ctx.log.events if k == "preflight"]
    assert preflight_fields["kiln_host"] == "http://h"
    # The preflight's cost reaches the spend accounting, lane-tagged.
    assert ("preflight", "subscription_equivalent", 0.0) in [
        (f["source"], f["lane"], f["cost_usd"])
        for k, f in ctx.log.events
        if k == "spend"
    ]


async def test_preflight_failure_aborts_before_any_paid_dispatch():
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    ctx.dispatch.preflight_error = RuntimeError(
        "preflight failed — the call_kiln_api MCP lane is not working"
    )
    with pytest.raises(RuntimeError, match="MCP lane"):
        await orchestrate(ctx, "", timeout_mins=180)
    # No planner (or any other) dispatch ever ran.
    assert ctx.dispatch.calls == []
    # The preflight phase still closed (no dangling phase_start).
    kinds = [k for k, _ in ctx.log.events]
    assert kinds.count("phase_start") == kinds.count("phase_end") == 1


# ---- the setup phase in pre-flight ----


async def test_setup_syncs_the_issue_store_before_the_first_wave():
    # The whole point of the phase: a task whose evals have no issue memories
    # gets them — with agent-written descriptions — before the planner is
    # ever asked what to work on.
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))

    await orchestrate(ctx, "", timeout_mins=180)

    (issue,) = [m for m in kiln.memories.values() if "issue" in m["tags"]]
    assert issue["tags"] == ["issue", "p1", "eval_eval_1"]
    assert issue["overview"].startswith("UNMEASURED: issue: ")
    # The sync is reported and the phase is bracketed like every other
    # quiet stretch — the describe fan-out is minutes of silence on a cold
    # project.
    events = dict(ctx.log.events)
    assert events["setup_sync"]["seeded"] == 1
    assert events["setup_sync"]["report"].seeded_eval_ids == ["eval_1"]
    phases = [f["name"] for k, f in ctx.log.events if k == "phase_start"]
    assert phases[:2] == ["preflight", "setup"]


async def test_setup_failure_closes_its_phase_and_stops_the_run():
    # A fatal setup error must not leave a dangling phase_start, and must
    # land before the planner is dispatched (nothing paid for).
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    kiln.evals["eval_1"] = make_eval("eval_1", "ec_1", val_filter=None)

    with pytest.raises(EvalSplitAuditError, match="missing a train/val split"):
        await orchestrate(ctx, "", timeout_mins=180)

    kinds = [k for k, _ in ctx.log.events]
    assert kinds.count("phase_start") == kinds.count("phase_end") == 2
    assert ctx.dispatch.calls_to("batch_plan") == []


async def test_setup_runs_before_the_janitor_and_the_baseline_read():
    # The record setup seeds has to be visible to the same run, so the
    # baseline read comes after it.
    ctx = make_ctx()
    ctx.kiln.run_configs = [make_run_config("rc_seeded")]
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))

    await orchestrate(ctx, "", timeout_mins=180)

    kinds = [k for k, _ in ctx.log.events]
    assert kinds.index("setup_ok") < kinds.index("janitor")
    assert kinds.index("janitor") < kinds.index("baseline_found")


async def test_a_measure_item_runs_in_a_wave_and_never_reaches_promotion():
    # End to end: the planner's cheap lever runs alongside a
    # real experiment, settles the issue in memory with no agent of its own,
    # and — with result=None — is never a promotion candidate.
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_train(kiln, "rc_cand")
    seed_val(kiln, "rc_cand")
    seed_issue(kiln)  # the fix item's issue
    kiln.add_memory(
        "UNMEASURED: issue: replies drop the handoff block",
        "task::t1",
        ["issue", "p1"],
        memory_id="issue_2",
    )
    ctx.dispatch.queue(
        "batch_plan",
        ok(
            make_plan(fix_item("w1-1"), measure_item("w1-2", issue_memory_id="issue_2"))
        ),
        ok(make_plan(loop_exit="done")),
    )
    ctx.dispatch.queue("create_experiment_rc", rc_ok("rc_cand"))
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    ctx.dispatch.queue("promote_best", decision("rc_cand"))

    await orchestrate(ctx, "", timeout_mins=180)

    # The measure item settled the issue: baseline scores 1 of 2, so it
    # reproduces and the UNMEASURED: prefix is gone.
    issue = kiln.memories["issue_2"]
    assert issue["overview"] == "issue: replies drop the handoff block"
    assert "- reproduced on baseline rc_base" in issue["content"]
    # …and it cost no dispatch of its own: only the three agents the fix item
    # and the wave need.
    assert sorted({name for name, _ in ctx.dispatch.calls}) == [
        "analyze_experiment_results",
        "batch_plan",
        "create_experiment_rc",
        "describe_eval",
        "promote_best",
    ]
    # Only the experiment was weighed for promotion (the type guard).
    (candidates,) = ctx.dispatch.calls_to("promote_best")
    assert [c.rc_id for c in candidates.candidates] == ["rc_cand"]
    (wave_start,) = [f for k, f in ctx.log.events if k == "wave_start"]
    assert wave_start["plan_headline"] == "1 experiment, 1 baseline measurement"


# ---- gate floor, budget, always-a-report ----


def test_should_wrap_uses_the_projected_wave_time():
    clock = lambda: 0.0  # noqa: E731
    assert not should_wrap(3600.0, clock)  # 60 min left, static 30-min window
    assert should_wrap(3600.0, clock, projected_wave_secs=4000.0)
    assert should_wrap(1000.0, clock)  # inside the static window
    assert not should_wrap(0.0, clock, projected_wave_secs=4000.0)  # past T-0


async def test_candidate_fill_failure_puts_the_hole_on_the_wire():
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_train(kiln, "rc_cand")
    seed_val(kiln, "rc_base")
    kiln.job_behaviors[("eval_1", "rc_cand", "val")] = ["fail", "fail"]
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))
    d.queue("promote_best", decision(None, rationale="no evidence"))

    await orchestrate(ctx, "", timeout_mins=180)

    (promo_req,) = d.calls_to("promote_best")
    (table,) = promo_req.candidates
    assert table.ineligible is True
    [err] = table.errors
    assert "candidate val fill failed for eval eval_1" in err.detail
    assert "gate_candidate_ineligible" in [k for k, _ in ctx.log.events]
    # The val_fill_failed event now carries the failure's substance.
    (fill_event,) = [f for k, f in ctx.log.events if k == "val_fill_failed"]
    assert fill_event["detail"]


async def test_ineligible_winner_is_blocked_at_the_gate():
    # A silently under-covered gate can change which config ships, in both
    # directions. The agent's decision does not override the floor: an
    # ineligible winner is blocked, the baseline stays, and the wins stay
    # pooled (no rejection stamps).
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_train(kiln, "rc_cand")
    seed_val(kiln, "rc_cand")
    key = ("eval_1", "rc_base", "val")
    kiln.partial_results[key] = [make_run("v1", accuracy=0.0)]
    kiln.job_behaviors[key] = ["errored_items", "errored_items"]
    kiln.job_errors[key] = [make_job_error("v2", "serialization flake")]
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))
    d.queue("promote_best", decision("rc_cand"))

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "done"  # the run continues past the blocked gate
    (promo_req,) = d.calls_to("promote_best")
    (table,) = promo_req.candidates
    assert table.ineligible is True
    assert any("below the 95% floor" in e.detail for e in table.errors)
    # Blocked: baseline untouched, block event logged, nothing stamped.
    assert kiln.memories["mem_baseline"]["overview"] == BASELINE_OVERVIEW
    (blocked,) = [f for k, f in ctx.log.events if k == "promotion_blocked"]
    assert blocked["winner_rc_id"] == "rc_cand"
    assert not any("REJECTED" in m["overview"] for m in kiln.memories.values())


async def test_budget_exhausted_exits_before_a_wave():
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    ctx.spend = SpendMeter()
    ctx.spend.total = 12.0  # prior waves' spend, already over
    ctx.budget_usd = 10.0

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "budget_exhausted"
    assert ctx.dispatch.calls_to("batch_plan") == []  # not even a planner call
    (exit_event,) = [f for k, f in ctx.log.events if k == "exit"]
    assert exit_event["reason"] == "budget_exhausted"
    assert exit_event["spent_usd"] == pytest.approx(12.0)


async def test_budget_within_one_projected_wave_forces_wrap_up():
    ctx = make_ctx(eval_building=True)
    seed_baseline(ctx.kiln)
    seed_issue(ctx.kiln)
    recording = ctx.log
    meter = SpendMeter()
    ctx.log = FanoutLog(recording, meter)
    ctx.analyzer.log = ctx.log
    ctx.spend = meter
    ctx.budget_usd = 10.0
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(eval_only_item("w1-1"))),
        ok(make_plan(loop_exit="stop")),
    )
    d.queue("eval_build", ok(eval_report(), cost_usd=6.0))

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "stop"
    # Wave 1 cost $6; $4 of the $10 budget remains < the $6 mean wave cost —
    # wave 2 is the wrap-up even though the clock has hours left.
    plan_inputs = d.calls_to("batch_plan")
    assert [p.should_wrap_up for p in plan_inputs] == [False, True]


async def test_fatal_error_emits_exit_and_writes_the_fallback_report():
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln)
    # A 422 at job creation is a hard error — fatal.
    # The parent's stored train rows order the engine's queue.
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("t1", accuracy=0.0)]
    kiln.job_behaviors[("eval_1", "rc_cand", "train")] = ["http422"]
    d = ctx.dispatch
    d.queue("batch_plan", ok(make_plan(fix_item("w1-1"))))
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    fallbacks: list[str] = []

    async def fake_fallback(_ctx, reason: str) -> str:
        fallbacks.append(reason)
        return "reports/stub.md"

    ctx.write_fallback = fake_fallback

    with pytest.raises(KilnAPIError):
        await orchestrate(ctx, "", timeout_mins=180)

    # 17 of 29 historical runs ended with no exit event and no report.
    assert fallbacks == ["fatal:KilnAPIError"]
    exit_events = [f for k, f in ctx.log.events if k == "exit"]
    assert exit_events[-1]["reason"] == "fatal:KilnAPIError"


async def test_quota_circuit_break_exits_cleanly_with_the_fallback_report():
    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln)
    # The parent's stored train rows order the engine's queue.
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("t1", accuracy=0.0)]
    key = ("eval_1", "rc_cand", "train")
    kiln.job_behaviors[key] = ["errored_items"]
    kiln.job_errors[key] = [
        make_job_error(
            "t1",
            "This request requires more credits, or fewer max_tokens."
            " Visit https://openrouter.ai/settings/keys",
        )
    ]
    d = ctx.dispatch
    d.queue("batch_plan", ok(make_plan(fix_item("w1-1"))))
    d.queue("create_experiment_rc", rc_ok("rc_cand"))

    async def fake_fallback(_ctx, reason: str) -> str:
        return f"reports/fallback-{reason}.md"

    ctx.write_fallback = fake_fallback

    result = await orchestrate(ctx, "", timeout_mins=180)

    # A 402 credit-exhaustion error stops the run after ONE attempt and the
    # run still writes a report.
    assert result == "reports/fallback-quota_exhausted.md"
    assert len([j for j in kiln.job_invocations if j == key]) == 1
    (exit_event,) = [f for k, f in ctx.log.events if k == "exit"]
    assert exit_event["reason"] == "quota_exhausted"
    assert "requires more credits" in exit_event["detail"]


async def test_run_wave_grants_an_analyzing_item_its_grace_at_the_deadline():
    # An item ANALYZING at the deadline has paid for its build
    # and drives; the analysis is the one session between that spend and a
    # stored verdict. It keeps running (bounded); a MEASURING sibling is
    # cancelled at once.
    import asyncio

    from kiln_harness_tuner.orchestrator import _run_wave
    from kiln_harness_tuner.schemas import BaselineRef, ItemOutcome, RemeasureItem

    ctx = make_ctx()
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    ctx.kiln.job_behaviors[("eval_1", "rc_old", "train")] = ["hang"]
    measuring = RemeasureItem(
        id="w1-1", type="remeasure", rc_id="rc_old", eval_ids=["eval_1"], reason="r"
    )

    finished = asyncio.Event()

    async def analyzing_item(ctx_, item, baseline, lineage, wave):
        ctx_.item_states[item.id] = "analyzing"
        await asyncio.sleep(0.2)  # past the deadline, inside the grace
        finished.set()
        return ItemOutcome(
            item=item,
            status="completed",
            rc_id="rc_x",
            experiment_memory_id=None,
            result=None,
            infra_error=None,
        )

    import kiln_harness_tuner.orchestrator as orch

    real = orch.execute_plan_item

    async def routed(ctx_, item, baseline, lineage, wave):
        if item.id == "w1-2":
            return await analyzing_item(ctx_, item, baseline, lineage, wave)
        return await real(ctx_, item, baseline, lineage, wave)

    orch.execute_plan_item = routed
    try:
        analyzing = RemeasureItem(
            id="w1-2", type="remeasure", rc_id="rc_y", eval_ids=["eval_1"], reason="r"
        )
        outcomes, deadline_hit = await _run_wave(
            ctx,
            [measuring, analyzing],
            BaselineRef(rc_id="rc_base"),
            [],
            1,
            deadline=0.05,
        )
    finally:
        orch.execute_plan_item = real

    assert deadline_hit is True
    assert finished.is_set()
    by_id = {o.item.id: o for o in outcomes}
    assert by_id["w1-1"].status == "failed_infra"  # cancelled at the deadline
    assert by_id["w1-2"].status == "completed"  # graced through its analysis
    kinds = [k for k, _ in ctx.log.events]
    assert "deadline_grace" in kinds
    (cancel,) = [f for k, f in ctx.log.events if k == "deadline_cancel"]
    assert cancel["item_ids"] == ["w1-1"]


async def test_run_wave_cancels_in_flight_items_at_the_deadline():
    from kiln_harness_tuner.orchestrator import _run_wave
    from kiln_harness_tuner.schemas import BaselineRef, RemeasureItem

    ctx = make_ctx()
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    ctx.kiln.job_behaviors[("eval_1", "rc_old", "train")] = ["hang"]
    item = RemeasureItem(
        id="w1-1",
        type="remeasure",
        rc_id="rc_old",
        eval_ids=["eval_1"],
        reason="top up",
    )

    outcomes, deadline_hit = await _run_wave(
        ctx, [item], BaselineRef(rc_id="rc_base"), [], 1, deadline=0.05
    )

    assert deadline_hit is True
    [outcome] = outcomes
    assert outcome.status == "failed_infra"
    assert outcome.infra_error.error_class == "timeout"
    assert "deadline" in outcome.infra_error.detail
    (cancel_event,) = [f for k, f in ctx.log.events if k == "deadline_cancel"]
    assert cancel_event["item_ids"] == ["w1-1"]


# ---- the code-rule gate (gate_agent="auto"), gate isolation,
# ---- the hard deadline at the gate, the contract preflight, sibling cancel


def _seed_val_win(kiln, cand: str = "rc_cand") -> None:
    """Val rows where the candidate really improves: baseline 1/2 pass,
    candidate 2/2 — `seed_val` leaves both at 1/2 (a flat delta)."""
    kiln.pending[("eval_1", "rc_base", "val")] = [
        make_run("v1", accuracy=0.0),
        make_run("v2", accuracy=1.0),
    ]
    kiln.pending[("eval_1", cand, "val")] = [
        make_run("v1", accuracy=1.0),
        make_run("v2", accuracy=1.0),
    ]


def _auto_scenario(ctx) -> None:
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln)
    seed_train(kiln, "rc_cand")
    _seed_val_win(kiln)
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(fix_item("w1-1"))),
        ok(make_plan(loop_exit="done")),
    )
    d.queue("create_experiment_rc", rc_ok("rc_cand"))
    d.queue("analyze_experiment_results", ok(make_result()))


async def test_code_rule_promotes_a_single_candidate_without_the_agent():
    ctx = make_ctx(gate_agent="auto")
    _auto_scenario(ctx)

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "done"
    assert ctx.dispatch.calls_to("promote_best") == []  # no $1 session
    assert ctx.kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_cand"]
    (rule,) = [f for k, f in ctx.log.events if k == "promotion_rule"]
    assert rule["decision"].winner_rc_id == "rc_cand"
    assert rule["decision"].rationale.startswith("code rule: promote")
    (promo,) = [f for k, f in ctx.log.events if k == "promotion"]
    assert promo["winner_rc_id"] == "rc_cand"
    # The winner's issue is closed from the train-side result, as before.
    assert ctx.kiln.memories["issue_1"]["overview"].startswith("FIXED:")


async def test_code_rule_rejects_on_a_regression_beyond_doubt():
    ctx = make_ctx(gate_agent="auto")
    _auto_scenario(ctx)
    # The val comparison's CI sits entirely below zero (the fake serves one
    # stats shape for every call; the train-side rule is not the subject here,
    # so the analyzer's train verdict is scripted around it).
    ctx.kiln.stats_responses = [
        # the contract preflight's two fixture calls come first
        {**ctx.kiln.mcnemar_response},
        {**ctx.kiln.compare_paired_response},
        # train comparison: a clean win
        {**ctx.kiln.mcnemar_response},
        # val comparison: proven regression
        {**ctx.kiln.mcnemar_response, "ci_low": -0.6, "ci_high": -0.1},
    ]

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "done"
    assert ctx.dispatch.calls_to("promote_best") == []
    assert ctx.kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_base"]
    (promo,) = [f for k, f in ctx.log.events if k == "promotion"]
    assert promo["winner_rc_id"] is None
    assert "regressed beyond doubt" in promo["rationale"]
    # A real val loss is stamped, like an agent rejection.
    assert "rejection_recorded" in [k for k, _ in ctx.log.events]


async def test_code_rule_blocks_an_ineligible_candidate_and_stamps_nothing():
    ctx = make_ctx(gate_agent="auto")
    _auto_scenario(ctx)
    ctx.kiln.job_behaviors[("eval_1", "rc_cand", "val")] = ["fail", "fail"]

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "done"
    kinds = [k for k, _ in ctx.log.events]
    assert "promotion_blocked" in kinds
    assert "promotion" not in kinds
    assert "rejection_recorded" not in kinds  # wins stay pooled
    assert ctx.kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_base"]


async def test_code_rule_flags_a_nominal_val_slip_on_the_bound_issue():
    ctx = make_ctx(gate_agent="auto")
    _auto_scenario(ctx)
    kiln = ctx.kiln
    # A second active eval (a Kiln-shaped numeric id — the issue binding tag
    # is `eval_<digits>`), bound to its own issue, that slips nominally on
    # val (CI straddles zero — the fake's default CI does) by less than the
    # primary eval gains, so the code rule still promotes.
    kiln.evals["222222"] = make_eval("222222", "ec_2")
    kiln.specs.append({"id": "spec_2", "eval_id": "222222", "status": "active"})
    kiln.add_memory(
        "issue two", "task::t1", ["issue", "p1", "eval_222222"], memory_id="issue_2"
    )
    kiln.pending[("222222", "rc_base", "val")] = [
        make_run(f"v{i}", accuracy=1.0) for i in range(4)
    ]
    kiln.pending[("222222", "rc_cand", "val")] = [
        make_run("v0", accuracy=0.0),
        *[make_run(f"v{i}", accuracy=1.0) for i in range(1, 4)],
    ]

    await orchestrate(ctx, "", timeout_mins=180)

    assert kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_cand"]
    flagged = [f for k, f in ctx.log.events if k == "issue_regressed"]
    assert [f["issue_memory_id"] for f in flagged] == ["issue_2"]
    assert "repros on baseline rc_cand" in kiln.memories["issue_2"]["content"]


async def test_gate_skips_an_uncomparable_eval_instead_of_crashing():
    ctx = make_ctx(gate_agent="auto")
    _auto_scenario(ctx)
    kiln = ctx.kiln
    # A shape seen in the wild: an active eval whose only score is `custom`.
    kiln.evals["eval_c"] = make_eval(
        "eval_c", "ec_c", scores=[make_score("Cost", "custom", "lower_is_better")]
    )
    kiln.specs.append({"id": "spec_c", "eval_id": "eval_c", "status": "active"})

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "done"  # not fatal:UnsupportedScoreTypeError
    (skip,) = [f for k, f in ctx.log.events if k == "gate_evals_skipped"]
    assert skip["skipped"] == [
        "eval_c/cost: score type 'custom' has no statistics operation"
    ]
    (rule,) = [f for k, f in ctx.log.events if k == "promotion_rule"]
    assert rule["skipped"] == skip["skipped"]
    assert "Not compared: eval_c/cost" in rule["decision"].rationale
    # No val job was even started for the eval nobody can compare.
    assert not [j for j in kiln.job_invocations if j[0] == "eval_c"]
    assert kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_cand"]


async def test_gate_is_skipped_past_the_deadline_and_wins_stay_pooled():
    state = {"t": 0.0}
    ctx = make_ctx(gate_agent="auto", clock=lambda: state["t"])  # type: ignore[arg-type]
    _auto_scenario(ctx)

    def analysis_then_jump(req):
        state["t"] = 10**9  # the deadline passes while the item was measuring
        return ok(make_result())

    ctx.dispatch.queues["analyze_experiment_results"] = [analysis_then_jump]

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "timeout"
    (skipped,) = [f for k, f in ctx.log.events if k == "promotion_skipped"]
    assert skipped["reason"].startswith("deadline reached")
    assert skipped["candidate_rc_ids"] == ["rc_cand"]
    assert not [j for j in ctx.kiln.job_invocations if j[2] == "val"]
    assert ctx.kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_base"]


async def test_contract_preflight_fails_fast_on_a_moved_statistics_shape():
    from kiln_harness_tuner.contract import ContractPreflightError

    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    ctx.kiln.stats_response = {"p_value": 0.5}  # neither live shape

    with pytest.raises(ContractPreflightError, match="p_exact"):
        await orchestrate(ctx, "", timeout_mins=180)

    # Nothing was planned or paid for.
    assert ctx.dispatch.calls_to("batch_plan") == []
    assert "contract_ok" not in [k for k, _ in ctx.log.events]


async def test_contract_preflight_is_logged_when_the_shapes_hold():
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))

    await orchestrate(ctx, "", timeout_mins=180)

    (contract,) = [f for k, f in ctx.log.events if k == "contract_ok"]
    assert contract["statistics"] == "ok" and contract["evals"] == 1


async def test_a_hard_error_on_one_item_cancels_its_siblings():
    """The wave cancels its siblings on a hard error instead of letting each
    finish before raising — a 30-minute build session after the run has
    already decided to exit."""
    import asyncio

    from fake_dispatcher import FakeDispatcher

    hold = asyncio.Event()
    cancelled: list[str] = []

    class HoldingDispatcher(FakeDispatcher):
        async def create_experiment_rc(self, req):
            if req.item.id == "w1-2":
                try:
                    await hold.wait()
                except asyncio.CancelledError:
                    cancelled.append(req.item.id)
                    raise
            raise KilnAPIError("POST", "/x", 422, {"detail": "val split unset"})

    ctx = make_ctx(dispatcher=HoldingDispatcher())
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    seed_issue(ctx.kiln)
    ctx.dispatch.queue("batch_plan", ok(make_plan(fix_item("w1-1"), fix_item("w1-2"))))

    with pytest.raises(KilnAPIError):
        await asyncio.wait_for(orchestrate(ctx, "", timeout_mins=180), 5)

    assert cancelled == ["w1-2"]
    (event,) = [f for k, f in ctx.log.events if k == "fatal_cancel"]
    assert event["item_ids"] == ["w1-2"]


async def test_planner_receives_lever_attempts_from_the_prior_wave():
    ctx = make_ctx()
    _promotion_scenario(ctx)
    ctx.dispatch.queue("promote_best", decision(None, rationale="not yet"))

    await orchestrate(ctx, "", timeout_mins=180)

    first, second = ctx.dispatch.calls_to("batch_plan")
    assert first.lever_attempts == {}
    assert second.lever_attempts == {"issue_1": {"prompt": 1}}


# ---- the code rule decides on the TARGET beyond doubt,
# ---- hands a nominal-only win to the agent, and never averages mixed units


async def test_code_rule_hands_a_nominal_only_val_win_to_the_agent():
    """A positive delta with a CI straddling zero and p above .05 is what a
    no-op shows at n=25 most of the time — the code rule never promotes on
    it; the promote agent weighs headline and cost, as its skill says."""
    ctx = make_ctx(gate_agent="auto")
    _auto_scenario(ctx)
    ctx.kiln.stats_responses = [
        {**ctx.kiln.mcnemar_response},  # contract preflight
        {**ctx.kiln.compare_paired_response},
        {**ctx.kiln.mcnemar_response},  # train: a clean win
        {**ctx.kiln.mcnemar_response, "p_exact": 0.4, "ci_low": -0.2, "ci_high": 0.7},
    ]
    ctx.dispatch.queue("promote_best", decision("rc_cand"))

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "done"
    (rule,) = [f for k, f in ctx.log.events if k == "promotion_rule"]
    assert rule["decision"] is None  # undecided, not blocked
    assert "promotion_blocked" not in [k for k, _ in ctx.log.events]
    assert len(ctx.dispatch.calls_to("promote_best")) == 1
    assert ctx.kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_cand"]


async def test_code_rule_rejects_when_the_target_falls_and_a_guard_rises():
    """The old mean over every row let a `five_star` guard (+0.6) outvote a
    `pass_fail` target (-0.04): units differ, and the target is the point."""
    ctx = make_ctx(gate_agent="auto")
    _auto_scenario(ctx)
    kiln = ctx.kiln
    # The target slips on val: baseline 2/2, candidate 1/2 (CI straddles zero).
    kiln.pending[("eval_1", "rc_base", "val")] = [
        make_run("v1", accuracy=1.0),
        make_run("v2", accuracy=1.0),
    ]
    kiln.pending[("eval_1", "rc_cand", "val")] = [
        make_run("v1", accuracy=0.0),
        make_run("v2", accuracy=1.0),
    ]
    # A guard eval on a rating scale that rises a lot.
    kiln.evals["222222"] = make_eval(
        "222222", "ec_2", scores=[make_score("Tone", "five_star", "higher_is_better")]
    )
    kiln.specs.append({"id": "spec_2", "eval_id": "222222", "status": "active"})
    kiln.pending[("222222", "rc_base", "val")] = [
        make_run(f"v{i}", tone=2.0) for i in range(4)
    ]
    kiln.pending[("222222", "rc_cand", "val")] = [
        make_run(f"v{i}", tone=5.0) for i in range(4)
    ]

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "done"
    assert ctx.dispatch.calls_to("promote_best") == []
    (promo,) = [f for k, f in ctx.log.events if k == "promotion"]
    assert promo["winner_rc_id"] is None
    assert "no targeted row improved" in promo["rationale"]
    assert ctx.kiln.memories["mem_baseline"]["tags"] == ["baseline", "rc_rc_base"]


async def test_agent_lane_breaker_books_the_spend_that_hit_the_wall():
    """The session that met the usage wall still cost money: the breaker
    carries its usage and the exit books it."""
    from kiln_harness_tuner.provider_errors import ProviderQuotaError
    from kiln_harness_tuner.schemas import ErrorInfo, Usage

    ctx = make_ctx()
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln)

    def wall(req):
        raise ProviderQuotaError(
            ErrorInfo(error_class="quota_exhausted", detail="usage limit reached"),
            usage=Usage(cost_usd=1.25, tokens=10, unmeasured_tokens=7),
        )

    ctx.dispatch.queue("batch_plan", wall)

    async def fake_fallback(_ctx, reason: str) -> str:
        return f"reports/fallback-{reason}.md"

    ctx.write_fallback = fake_fallback

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "reports/fallback-quota_exhausted.md"
    (spend,) = [
        f
        for k, f in ctx.log.events
        if k == "spend" and f["source"] == "circuit_breaker"
    ]
    assert spend["cost_usd"] == 1.25 and spend["unmeasured_tokens"] == 7


async def test_snapshot_memory_writes_the_store_and_logs_it(tmp_path):
    # The step-through view replays a decision from the memory
    # snapshot taken before it; the orchestrator takes one around the
    # planner and the gate. With no run_dir the hook is a no-op.
    from kiln_harness_tuner.orchestrator import snapshot_memory

    ctx = make_ctx()
    ctx.kiln.add_memory("issue: x", "task::t1", ["issue", "p0"], memory_id="m1")
    ctx.kiln.add_memory("lesson: y", "project", ["lesson"], memory_id="m2")
    await snapshot_memory(ctx, "w1-plan-before")
    assert not (tmp_path / "snapshots").exists()

    ctx.run_dir = tmp_path
    await snapshot_memory(ctx, "w1-plan-before")
    ctx.kiln.add_memory("OPEN: q?", "task::t1", ["constraint"], memory_id="m3")
    await snapshot_memory(ctx, "w1-plan-after")

    files = sorted(p.name for p in (tmp_path / "snapshots").glob("*.json"))
    assert files == ["001-w1-plan-before.json", "002-w1-plan-after.json"]
    before = json.loads((tmp_path / "snapshots" / files[0]).read_text())
    after = json.loads((tmp_path / "snapshots" / files[1]).read_text())
    assert {r["id"] for r in before["records"]} == {"m1", "m2"}
    assert {r["id"] for r in after["records"]} == {"m1", "m2", "m3"}
    assert all("content" in r and "tags" in r for r in before["records"])
    events = [f for k, f in ctx.log.events if k == "memory_snapshot"]
    assert [e["label"] for e in events] == ["w1-plan-before", "w1-plan-after"]
    assert events[1]["records"] == 3 and events[1]["file"] == files[1]


def test_the_spend_meter_leaves_an_uncounted_agent_lane_out_of_the_budget():
    # Claude Code's reported cost is not money the operator pays: the meter
    # books it apart, and the budget reads the Kiln lane alone.
    meter = SpendMeter(agent_cost_in_budget=False)
    meter.event(
        "spend", source="batch_plan", lane="subscription_equivalent", cost_usd=4.0
    )
    meter.event("spend", source="preflight", cost_usd=1.0)  # lane from source
    meter.event("spend", source="eval_jobs", lane="provider", cost_usd=0.5)
    assert meter.total == pytest.approx(0.5)
    assert meter.uncounted == pytest.approx(5.0)
    assert meter.provider_total == pytest.approx(0.5)
    counted = SpendMeter()
    counted.event(
        "spend", source="batch_plan", lane="subscription_equivalent", cost_usd=4.0
    )
    counted.event("spend", source="eval_jobs", lane="provider", cost_usd=0.5)
    assert counted.total == pytest.approx(4.5)
    assert counted.uncounted == 0.0


async def test_an_uncounted_agent_lane_leaves_the_budget_alone():
    # The wrap-up test's run, with the agents' cost not counted: the $6 build
    # session is not budget spend, so wave 2 is not the wrap-up.
    ctx = make_ctx(eval_building=True)
    seed_baseline(ctx.kiln)
    seed_issue(ctx.kiln)
    recording = ctx.log
    meter = SpendMeter(agent_cost_in_budget=False)
    ctx.log = FanoutLog(recording, meter)
    ctx.analyzer.log = ctx.log
    ctx.spend = meter
    ctx.budget_usd = 10.0
    d = ctx.dispatch
    d.queue(
        "batch_plan",
        ok(make_plan(eval_only_item("w1-1"))),
        ok(make_plan(loop_exit="stop")),
    )
    d.queue("eval_build", ok(eval_report(), cost_usd=6.0))

    result = await orchestrate(ctx, "", timeout_mins=180)

    assert result == "stop"
    plan_inputs = d.calls_to("batch_plan")
    assert [p.should_wrap_up for p in plan_inputs] == [False, False]
    assert meter.total == pytest.approx(0.0)
    assert meter.uncounted == pytest.approx(6.0)
    (run_start,) = [f for k, f in recording.events if k == "run_start"]
    assert run_start["agent_cost_in_budget"] is False
