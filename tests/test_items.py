"""execute_plan_item tests: the per-item pipeline — eval_only and FixWork
paths, the dispatch retry ladder, and the lifecycle stamps.
"""

import pytest
from conftest import eval_only_item, fix_item, make_ctx, make_result, measure_item
from fake_dispatcher import expected, infra, ok
from fake_kiln import make_eval, make_job_error, make_run, make_score

from kiln_harness_tuner.items import call_with_retries, execute_plan_item
from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.schemas import (
    AgentResult,
    BaselineRef,
    ErrorInfo,
    EvalBuildReport,
    RcBuildResult,
    Usage,
)

BASELINE = BaselineRef(rc_id="rc_base")


def eval_report(reproduced: bool = True, reason: str | None = None) -> EvalBuildReport:
    return EvalBuildReport(
        eval_id="eval_1",
        eval_config_id="ec_1",
        eval_type="llm_judge",
        case_count=20,
        reproduced=reproduced,
        not_reproduced_reason=reason,
        baseline_headline="62% pass",
        priority="p1",
    )


def seed_measurement(kiln, candidate_rc: str = "rc_new") -> None:
    """Stored parent + pending candidate on eval_1/train — the free-compare
    path through the decision tree."""
    kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("item_1", accuracy=0.0),
        make_run("item_2", accuracy=1.0),
    ]
    kiln.pending[("eval_1", candidate_rc, "train")] = [
        make_run("item_1", accuracy=1.0),
        make_run("item_2", accuracy=1.0),
    ]


def seed_issue(kiln, issue_id: str = "issue_1") -> str:
    return kiln.add_memory(
        "the client drops 429 retries", "task::t1", ["issue"], memory_id=issue_id
    )


# ---- eval_only ----


async def test_eval_only_success_completes_with_no_result():
    ctx = make_ctx()
    seed_issue(ctx.kiln)
    ctx.dispatch.queue("eval_build", ok(eval_report()))
    outcome = await execute_plan_item(ctx, eval_only_item(), BASELINE, ["rc_base"], 1)
    assert outcome.status == "completed"
    assert outcome.result is None
    assert outcome.rc_id is None
    # The durable eval↔issue link landed on the issue record.
    assert ctx.kiln.memories["issue_1"]["content"] == (
        "- eval: eval_1 (built 07-22, reproduced)"
    )
    # The eval-build boundary: issue id + baseline ONLY.
    (req,) = ctx.dispatch.calls_to("eval_build")
    assert req.model_dump() == {
        "issue_memory_id": "issue_1",
        "baseline": BASELINE.model_dump(),
    }


async def test_eval_only_link_on_a_wrong_kind_id_fails_the_item_not_the_wave():
    # The planner named an experiment record as the issue. The eval build
    # runs and is paid for; the eval↔issue link then refuses (RecordKindError),
    # and that ends the ITEM as failed_infra — the same handling as the
    # not-reproduced branch of the same fork — never the wave.
    ctx = make_ctx()
    ctx.kiln.add_memory(
        "EXP 0722-w0-1 (prompt): not an issue — RUNNING",
        "task::t1",
        ["experiment"],
        memory_id="issue_1",
    )
    before = dict(ctx.kiln.memories["issue_1"])
    ctx.dispatch.queue("eval_build", ok(eval_report()))
    outcome = await execute_plan_item(ctx, eval_only_item(), BASELINE, ["rc_base"], 1)
    assert outcome.status == "failed_infra"
    assert outcome.infra_error is not None
    assert "not tagged `issue`" in outcome.infra_error.detail
    assert ctx.kiln.memories["issue_1"] == before  # nothing written


async def test_eval_only_not_reproduced_writes_wont_fix():
    ctx = make_ctx()
    seed_issue(ctx.kiln)
    ctx.dispatch.queue(
        "eval_build", ok(eval_report(reproduced=False, reason="cannot repro on n=12"))
    )
    outcome = await execute_plan_item(ctx, eval_only_item(), BASELINE, ["rc_base"], 1)
    assert outcome.status == "skipped_not_reproduced"
    record = ctx.kiln.memories["issue_1"]
    assert record["overview"] == "WONT_FIX: the client drops 429 retries"
    assert "cannot repro on n=12" in record["content"]


async def test_eval_only_infra_failure():
    ctx = make_ctx()
    ctx.dispatch.queue("eval_build", *([infra("rate_limit")] * 4))
    outcome = await execute_plan_item(ctx, eval_only_item(), BASELINE, ["rc_base"], 1)
    assert outcome.status == "failed_infra"
    assert outcome.infra_error.error_class == "rate_limit"


# ---- FixWork ----


async def test_fix_happy_path_records_verdict():
    ctx = make_ctx()
    seed_measurement(ctx.kiln)
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_new", build_notes="built"))
    )
    result = make_result()
    ctx.dispatch.queue("analyze_experiment_results", ok(result))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)
    assert outcome.status == "completed"
    assert outcome.rc_id == "rc_new"
    assert outcome.result == result
    exp = ctx.kiln.memories[outcome.experiment_memory_id]
    assert exp["overview"] == result.memory_overview  # verdict PATCHed by code
    # The analyzer input carried the train bundle + the memory id.
    (req,) = ctx.dispatch.calls_to("analyze_experiment_results")
    assert req.rc_id == "rc_new"
    assert req.experiment_memory_id == outcome.experiment_memory_id
    assert req.bundle.per_eval[0].compared_to == "parent"
    assert req.bundle.per_eval[0].split == "train"


async def test_fix_needs_eval_fills_eval_id_and_links():
    ctx = make_ctx()
    seed_issue(ctx.kiln)
    seed_measurement(ctx.kiln)
    ctx.dispatch.queue("eval_build", ok(eval_report()))
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_new", build_notes=""))
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    item = fix_item(needs_eval=True, eval_id=None)
    outcome = await execute_plan_item(ctx, item, BASELINE, ["rc_base"], 1)
    assert outcome.status == "completed"
    assert item.work.eval_id == "eval_1"  # filled from the build report
    assert (
        "- eval: eval_1 (built 07-22, reproduced)"
        in (ctx.kiln.memories["issue_1"]["content"])
    )


async def test_fix_needs_eval_not_reproduced_skips_before_building():
    ctx = make_ctx()
    seed_issue(ctx.kiln)
    ctx.dispatch.queue(
        "eval_build", ok(eval_report(reproduced=False, reason="not seen"))
    )
    outcome = await execute_plan_item(
        ctx, fix_item(needs_eval=True, eval_id=None), BASELINE, ["rc_base"], 1
    )
    assert outcome.status == "skipped_not_reproduced"
    assert ctx.dispatch.calls_to("create_experiment_rc") == []
    assert ctx.kiln.memories["issue_1"]["overview"].startswith("WONT_FIX: ")


async def test_rc_build_infra_stamps_memory_failed():
    ctx = make_ctx()
    ctx.dispatch.queue("create_experiment_rc", *([infra("provider_error")] * 4))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)
    assert outcome.status == "failed_infra"
    assert outcome.experiment_memory_id is not None
    # Stamped the moment the item went terminally failed_infra.
    assert ctx.kiln.memories[outcome.experiment_memory_id]["overview"].endswith(
        "— FAILED (infra: provider_error)"
    )
    # Dispatch ladder: 4 calls total, backoffs 30/60/120.
    assert len(ctx.dispatch.calls_to("create_experiment_rc")) == 4
    assert ctx.sleeps == [30.0, 60.0, 120.0]


async def test_ladder_recovers_mid_way():
    ctx = make_ctx()
    seed_measurement(ctx.kiln)
    ctx.dispatch.queue(
        "create_experiment_rc",
        infra("timeout"),
        ok(RcBuildResult(rc_id="rc_new", build_notes="")),
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)
    assert outcome.status == "completed"
    assert ctx.sleeps == [30.0]


async def test_parse_failure_is_terminal_not_retried():
    ctx = make_ctx()
    ctx.dispatch.queue("create_experiment_rc", infra("parse_failure"))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)
    assert outcome.status == "failed_infra"
    assert outcome.infra_error.error_class == "parse_failure"
    assert len(ctx.dispatch.calls_to("create_experiment_rc")) == 1
    assert ctx.sleeps == []


async def test_failed_expected_rc_build_is_a_dead_end_not_infra():
    # A failed_expected build is a
    # PROVEN dead-end (a decision), never retryable infra — the experiment
    # memory gets `— DEAD END (…)` so planners stop re-proposing the lever.
    ctx = make_ctx()
    ctx.dispatch.queue("create_experiment_rc", expected("cannot build: eval missing"))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)
    assert outcome.status == "dead_end"
    assert outcome.infra_error is None
    assert outcome.experiment_memory_id is not None
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    assert record["overview"].endswith("— DEAD END (cannot build: eval missing)")
    assert (
        len(ctx.dispatch.calls_to("create_experiment_rc")) == 1
    )  # not infra → no retry


# ---- instrumentation events ----


async def test_happy_path_emits_state_spend_and_outcome_events():
    ctx = make_ctx()
    seed_measurement(ctx.kiln)
    ctx.dispatch.queue(
        "create_experiment_rc",
        ok(RcBuildResult(rc_id="rc_new", build_notes="built"), cost_usd=1.0),
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result(), cost_usd=0.5))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    states = [f["state"] for k, f in ctx.log.events if k == "item_state"]
    assert states == ["building_rc", "measuring", "analyzing"]
    spends = [(f["source"], f["cost_usd"]) for k, f in ctx.log.events if k == "spend"]
    # The measurement engine books a spend event per chunk that
    # cost anything; the fake's jobs cost nothing, so only the analysis's
    # own (zero) booking of the stored rows shows.
    assert spends == [
        ("create_experiment_rc", 1.0),
        ("eval_jobs", 0.0),
        ("analyze_experiment_results", 0.5),
    ]
    # Cost split: dispatches ride the subscription-
    # equivalent lane, eval jobs the provider lane.
    assert {f["source"]: f["lane"] for k, f in ctx.log.events if k == "spend"} == {
        "create_experiment_rc": "subscription_equivalent",
        "eval_jobs": "provider",
        "analyze_experiment_results": "subscription_equivalent",
    }
    assert all(
        f["item_id"] == "w1-1"
        for k, f in ctx.log.events
        if k in ("item_state", "item_detail", "spend", "item_outcome")
    )
    # Per-eval start/done detail lines —
    # after the measuring state and before the analyzing state.
    details = [f["detail"] for k, f in ctx.log.events if k == "item_detail"]
    # The arm's spending cap, then one engine progress line per
    # chunk come first.
    assert details == [
        "w1-1: budget $20.00 (default: expected full measurement $0.00, no run budget)",
        "w1-1: chunk 1/1 · 2 judged · +1/−0 vs baseline · done",
        "measuring eval_1…",
        "eval_1 done",
    ]
    kinds = [k for k, _ in ctx.log.events]
    assert kinds.index("item_detail") > kinds.index("item_state")
    # Exactly one item_outcome, carrying the returned outcome (the UI's
    # "replace running status with finding").
    outcomes = [f["outcome"] for k, f in ctx.log.events if k == "item_outcome"]
    assert outcomes == [outcome]


async def test_eval_only_emits_building_eval_and_outcome_events():
    ctx = make_ctx()
    seed_issue(ctx.kiln)
    ctx.dispatch.queue("eval_build", ok(eval_report(), cost_usd=2.0))
    await execute_plan_item(ctx, eval_only_item(), BASELINE, ["rc_base"], 1)
    kinds = [k for k, _ in ctx.log.events]
    assert kinds == ["item_state", "spend", "item_outcome"]
    assert ctx.log.events[0][1] == {"item_id": "w1-1", "state": "building_eval"}
    assert ctx.log.events[1][1] == {
        "item_id": "w1-1",
        "source": "eval_build",
        "lane": "subscription_equivalent",
        "cost_usd": 2.0,
        "unmeasured_tokens": 0,
    }


async def test_infra_failure_still_emits_item_outcome():
    ctx = make_ctx()
    ctx.dispatch.queue("create_experiment_rc", infra("parse_failure"))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)
    outcomes = [f["outcome"] for k, f in ctx.log.events if k == "item_outcome"]
    assert outcomes == [outcome]


async def test_item_dispatch_retry_emits_event_before_backoff():
    # A ladder rung is 30s–2min of silence — each retry is an
    # event carrying source, item, attempt, backoff, and the failure class.
    ctx = make_ctx()
    seed_measurement(ctx.kiln)
    ctx.dispatch.queue(
        "create_experiment_rc",
        infra("timeout"),
        ok(RcBuildResult(rc_id="rc_new", build_notes="")),
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)
    retries = [f for k, f in ctx.log.events if k == "dispatch_retry"]
    assert retries == [
        {
            "source": "create_experiment_rc",
            "item_id": "w1-1",
            "attempt": 1,
            "backoff_secs": 30.0,
            "error_class": "timeout",
        }
    ]


async def test_parse_failure_emits_no_retry_event():
    ctx = make_ctx()
    ctx.dispatch.queue("create_experiment_rc", infra("parse_failure"))
    await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)
    assert not any(k == "dispatch_retry" for k, _ in ctx.log.events)


async def test_retried_attempts_usage_reaches_spend_event():
    # Failed tries cost real money — the ladder sums usage
    # across every attempt before the spend event fires.
    ctx = make_ctx()
    seed_measurement(ctx.kiln)
    failed = AgentResult(
        status="failed_infra",
        data=None,
        error=ErrorInfo(error_class="timeout", detail="hung"),
        usage=Usage(cost_usd=0.3, tokens=10, wall_secs=5.0),
    )
    ctx.dispatch.queue(
        "create_experiment_rc",
        failed,
        ok(RcBuildResult(rc_id="rc_new", build_notes="built"), cost_usd=1.0),
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)
    spends = {f["source"]: f["cost_usd"] for k, f in ctx.log.events if k == "spend"}
    assert spends["create_experiment_rc"] == pytest.approx(1.3)
    assert failed.usage.cost_usd == 0.3  # scripted result not mutated


# ---- the ladder reports its own attempt count ----


async def ladder(*results, backoffs=(30.0, 60.0)) -> tuple[int, list[float]]:
    """Run the dispatch ladder over a scripted dispatch sequence; return what a
    caller can see of what it spent — the attempt count and the backoffs."""
    queue = list(results)
    sleeps: list[float] = []

    async def dispatch(_req):
        return queue.pop(0)

    async def sleep(secs: float) -> None:
        sleeps.append(secs)

    result = await call_with_retries(
        dispatch, object(), backoffs=backoffs, sleep=sleep, source="describe_eval"
    )
    return result.attempts, sleeps


def rc_ok() -> AgentResult:
    return ok(RcBuildResult(rc_id="rc_1", build_notes=""))


async def test_attempts_counts_one_when_the_first_call_succeeds():
    assert await ladder(rc_ok()) == (1, [])


async def test_attempts_counts_every_dispatch_the_ladder_spent():
    assert await ladder(infra("timeout"), infra("timeout"), rc_ok()) == (
        3,
        [30.0, 60.0],
    )


async def test_attempts_counts_the_exhausted_ladder():
    attempts, sleeps = await ladder(*([infra("timeout")] * 3))
    assert (attempts, sleeps) == (3, [30.0, 60.0])  # first call + one per rung


async def test_attempts_is_one_for_both_terminal_cases():
    # The two rows a caller must NOT re-derive from `status`: a
    # `failed_expected` verdict, and `parse_failure` — which IS failed_infra
    # and still never retried (its in-session repair already ran). Setup's
    # fatal message got each of them wrong in turn by inferring.
    assert await ladder(expected("cannot read the eval")) == (1, [])
    assert await ladder(infra("parse_failure", "no fenced block")) == (1, [])


# ---- measure_issue_baseline — pure code, no dispatch ----


def seed_baseline_measurement(
    kiln, eval_id: str = "eval_1", **scores_by_item: float
) -> None:
    """An eval with a STORED train-side result on the baseline rc — the
    already-measured path the cheap check depends on."""
    kiln.evals[eval_id] = make_eval(eval_id, "ec_1")
    kiln.results[(eval_id, "rc_base", "train")] = [
        make_run(item_id, accuracy=value) for item_id, value in scores_by_item.items()
    ]


async def measure(ctx, item=None) -> object:
    return await execute_plan_item(
        ctx, item or measure_item(), BASELINE, ["rc_base"], 1
    )


async def test_measure_issue_baseline_reproduced_strips_unmeasured():
    # A REAL (numeric) eval id, so the binding tag matches `eval_\\d{6,}` and
    # this walks the bound branch end to end — with `eval_1` the tag cannot
    # parse and the writer's binding check is skipped entirely.
    ctx = make_ctx()
    ctx.kiln.add_memory(
        "UNMEASURED: issue: replies drop the handoff block",
        "task::t1",
        ["issue", "p1", "eval_424242966189"],
        memory_id="issue_1",
    )
    seed_baseline_measurement(ctx.kiln, "424242966189", item_1=0.0, item_2=1.0)

    outcome = await measure(ctx, measure_item(eval_id="424242966189"))

    assert outcome.status == "completed"
    assert outcome.result is None  # never a promotion candidate
    record = ctx.kiln.memories["issue_1"]
    assert record["overview"] == "issue: replies drop the handoff block"
    assert record["content"] == (
        "- reproduced on baseline rc_base 07-22"
        " (accuracy 0.5, n=2/2 — normalized, 1.00 = best)"
    )


async def test_measure_issue_baseline_not_reproduced_writes_wont_fix():
    ctx = make_ctx()
    ctx.kiln.add_memory(
        "UNMEASURED: issue: replies drop the handoff block",
        "task::t1",
        ["issue", "p1"],
        memory_id="issue_1",
    )
    seed_baseline_measurement(ctx.kiln, item_1=1.0, item_2=1.0)

    outcome = await measure(ctx)

    assert outcome.status == "completed"
    record = ctx.kiln.memories["issue_1"]
    assert record["overview"].startswith("WONT_FIX: ")
    assert (
        "- not reproduced on baseline rc_base 07-22 (accuracy 1, n=2/2"
        in (record["content"])
    )


async def test_measure_issue_baseline_dispatches_no_agent():
    # Execution is pure code —
    # no builder, no analyzer, no eval-build.
    ctx = make_ctx()
    seed_issue(ctx.kiln)
    seed_baseline_measurement(ctx.kiln, item_1=0.0)

    await measure(ctx)

    assert ctx.dispatch.calls == []
    assert ctx.sleeps == []  # and no retry ladder


async def test_measure_issue_baseline_costs_one_job_and_one_write():
    # Reuse `ensure_measured`, so an already-measured (eval, config, rc, train)
    # pair is a "free cache read". The client always ASKS — the saving is
    # server-side (an eval job over a fully-measured split re-scores
    # nothing), so what this pins is that the item adds no machinery of its
    # own: one job invocation, one memory PATCH, nothing else.
    ctx = make_ctx()
    seed_issue(ctx.kiln)
    seed_baseline_measurement(ctx.kiln, item_1=0.0)

    await measure(ctx)

    assert ctx.kiln.job_invocations == [("eval_1", "rc_base", "train")]
    assert len(ctx.kiln.memory_patches) == 1
    assert ctx.kiln.memory_posts == []
    spends = [f["cost_usd"] for k, f in ctx.log.events if k == "spend"]
    assert spends == [0.0]  # stored runs carry no fresh provider cost


async def test_measure_issue_baseline_runs_the_eval_when_nothing_is_stored():
    ctx = make_ctx()
    seed_issue(ctx.kiln)
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    ctx.kiln.pending[("eval_1", "rc_base", "train")] = [
        make_run("item_1", cost=0.2, eval_cost=0.05, accuracy=0.0)
    ]

    outcome = await measure(ctx)

    assert outcome.status == "completed"
    assert ctx.kiln.job_invocations == [("eval_1", "rc_base", "train")]
    # Provider lane, subject + judge cost, from the runs.
    (spend,) = [f for k, f in ctx.log.events if k == "spend"]
    assert (spend["source"], spend["lane"]) == ("eval_jobs", "provider")
    assert spend["cost_usd"] == pytest.approx(0.25)


async def test_measure_issue_baseline_targets_a_score_subset():
    ctx = make_ctx()
    seed_issue(ctx.kiln)
    ctx.kiln.evals["eval_1"] = make_eval(
        "eval_1",
        "ec_1",
        scores=[make_score("Accuracy"), make_score("Tone", "five_star")],
    )
    ctx.kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("item_1", accuracy=0.0, tone=5.0)
    ]

    await measure(ctx, measure_item(scores=["Tone"]))

    # Only the targeted score reached the rule — the perfect one — so the
    # failing sibling never made this reproduce.
    record = ctx.kiln.memories["issue_1"]
    assert record["overview"].startswith("WONT_FIX: ")
    assert "tone 1, n=1/1" in record["content"]


async def test_all_informational_eval_is_failed_infra_and_leaves_the_issue_alone():
    # TargetExpansionError surfaces as failed_infra for this item,
    # never a run kill — and the issue keeps its UNMEASURED: state.
    ctx = make_ctx()
    ctx.kiln.add_memory(
        "UNMEASURED: issue: something", "task::t1", ["issue"], memory_id="issue_1"
    )
    ctx.kiln.evals["eval_1"] = make_eval(
        "eval_1", "ec_1", scores=[make_score("Tool Calls", "custom", "informational")]
    )

    outcome = await measure(ctx)

    assert outcome.status == "failed_infra"
    assert outcome.infra_error.error_class == "provider_error"
    assert ctx.kiln.memory_patches == []
    assert ctx.kiln.memories["issue_1"]["overview"].startswith("UNMEASURED: ")


async def test_a_missing_default_eval_config_is_failed_infra():
    ctx = make_ctx()
    seed_issue(ctx.kiln)
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", config_id=None)

    outcome = await measure(ctx)

    assert outcome.status == "failed_infra"
    assert "current_config_id" in outcome.infra_error.detail
    assert ctx.kiln.memory_patches == []


async def test_a_measurement_failure_leaves_the_issue_unmeasured():
    ctx = make_ctx()
    ctx.kiln.add_memory(
        "UNMEASURED: issue: something", "task::t1", ["issue"], memory_id="issue_1"
    )
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    ctx.kiln.job_behaviors[("eval_1", "rc_base", "train")] = ["fail", "fail"]

    outcome = await measure(ctx)

    assert outcome.status == "failed_infra"
    assert ctx.kiln.memory_patches == []
    assert ctx.kiln.memories["issue_1"]["overview"].startswith("UNMEASURED: ")


async def test_measure_issue_baseline_emits_state_detail_and_outcome_events():
    ctx = make_ctx()
    seed_issue(ctx.kiln)
    seed_baseline_measurement(ctx.kiln, item_1=0.0, item_2=1.0)

    outcome = await measure(ctx)

    kinds = [k for k, _ in ctx.log.events]
    assert kinds == ["item_state", "spend", "item_detail", "item_outcome"]
    assert ctx.log.events[0][1] == {"item_id": "w1-1", "state": "measuring"}
    # The verdict the frozen item row cannot carry (result=None)
    # lives in the wave log instead.
    assert ctx.log.events[2][1]["detail"] == (
        "reproduced on baseline rc_base (accuracy 0.5, n=2/2 — normalized, 1.00 = best)"
    )
    assert [f["outcome"] for k, f in ctx.log.events if k == "item_outcome"] == [outcome]


async def test_a_verdict_failure_still_logs_what_the_measurement_cost():
    # The measurement landed (and was paid for); only the verdict could not
    # be formed — the spend event must not be lost with the item.
    ctx = make_ctx()
    ctx.kiln.add_memory(
        "UNMEASURED: issue: something", "task::t1", ["issue"], memory_id="issue_1"
    )
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    # Runs exist and cost money, but carry no value for the targeted score.
    ctx.kiln.pending[("eval_1", "rc_base", "train")] = [
        make_run("item_1", cost=0.4, unrelated=1.0)
    ]

    outcome = await measure(ctx)

    assert outcome.status == "failed_infra"
    assert "cannot conclude" in outcome.infra_error.detail
    (spend,) = [f for k, f in ctx.log.events if k == "spend"]
    assert spend["cost_usd"] == pytest.approx(0.4)
    assert ctx.kiln.memory_patches == []


async def test_a_partially_measured_split_is_failed_infra_not_a_wont_fix():
    # `ensure_measured` returns partial results on ladder
    # exhaustion. If the handful of items that DID run happen to score
    # perfectly, treating that as evidence writes a terminal WONT_FIX: off a
    # fraction of the split — and `n=3` reads like a small eval, not a broken
    # run. This lane writes terminal state, so an incomplete split is an
    # eval error.
    ctx = make_ctx()
    ctx.kiln.add_memory(
        "UNMEASURED: issue: something", "task::t1", ["issue"], memory_id="issue_1"
    )
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    key = ("eval_1", "rc_base", "train")
    ctx.kiln.job_behaviors[key] = ["errored_items", "errored_items"]
    # 3 of 24 landed, and all three passed.
    ctx.kiln.partial_results[key] = [
        make_run(f"item_{i}", cost=0.1, accuracy=1.0) for i in range(3)
    ]
    ctx.kiln.job_errors[key] = [
        make_job_error(f"item_{i}", "judge 500") for i in range(21)
    ]

    outcome = await measure(ctx)

    assert outcome.status == "failed_infra"
    assert "partial:" in outcome.infra_error.detail
    # No terminal state written, and the spend still reached the accounting.
    assert ctx.kiln.memory_patches == []
    assert ctx.kiln.memories["issue_1"]["overview"].startswith("UNMEASURED: ")
    (spend,) = [f for k, f in ctx.log.events if k == "spend"]
    assert spend["cost_usd"] == pytest.approx(0.3)


async def test_a_missing_split_422_stays_fatal():
    # No degradation, enforced by the exception tuple rather than by a
    # docstring: a 4xx from the eval job is a real answer and kills the run,
    # exactly as in the experiment lane.
    ctx = make_ctx()
    seed_issue(ctx.kiln)
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    ctx.kiln.job_behaviors[("eval_1", "rc_base", "train")] = ["http422"]

    with pytest.raises(KilnAPIError) as excinfo:
        await measure(ctx)

    assert excinfo.value.status_code == 422
    assert ctx.kiln.memory_patches == []


async def test_a_foreign_eval_id_never_writes_terminal_state():
    # The planner supplies issue id and eval id independently; the binding
    # tag makes the pair checkable, so a mismatch fails the item instead of
    # stamping WONT_FIX: on the wrong issue from the wrong numbers.
    ctx = make_ctx()
    ctx.kiln.add_memory(
        "UNMEASURED: issue: something",
        "task::t1",
        ["issue", "eval_424242999999"],
        memory_id="issue_1",
    )
    seed_baseline_measurement(ctx.kiln, item_1=1.0)

    outcome = await measure(ctx)

    assert outcome.status == "failed_infra"
    assert "424242999999" in outcome.infra_error.detail
    assert ctx.kiln.memory_patches == []
    assert ctx.kiln.memories["issue_1"]["overview"].startswith("UNMEASURED: ")


async def test_an_unknown_issue_id_fails_the_item_not_the_run():
    # `MemoryWriter._get` raises KeyError for a missing record; letting it
    # out would kill the wave through the gather AFTER the job was paid for.
    ctx = make_ctx()
    seed_baseline_measurement(ctx.kiln, item_1=0.0)

    outcome = await measure(ctx, measure_item(issue_memory_id="nope"))

    assert outcome.status == "failed_infra"
    assert "nope" in outcome.infra_error.detail


async def test_a_closed_issue_is_never_reopened_by_a_measurement():
    # The planner may point an item at a FIXED: issue — the skill only says
    # closed issues are skipped as FIX candidates. Rewriting it to WONT_FIX:
    # would mislabel a landed fix as a decision and lose the citing rc.
    ctx = make_ctx()
    ctx.kiln.add_memory(
        "FIXED: issue: replies drop the handoff block",
        "task::t1",
        ["issue", "p1"],
        content="- fixed 07-20 by rc_207, EXP 0720-w3-1",
        memory_id="issue_1",
    )
    seed_baseline_measurement(ctx.kiln, item_1=1.0)

    outcome = await measure(ctx)

    assert outcome.status == "failed_infra"
    assert "already fixed" in outcome.infra_error.detail
    assert ctx.kiln.memory_patches == []
    record = ctx.kiln.memories["issue_1"]
    assert record["overview"] == "FIXED: issue: replies drop the handoff block"
    assert record["content"] == "- fixed 07-20 by rc_207, EXP 0720-w3-1"


async def test_a_score_measured_on_one_row_never_closes_the_issue():
    # One level down from a partial split: a CLEAN job whose judge emitted the
    # targeted score on 1 of 24 rows. health.clean does not catch it; per-score
    # coverage does.
    ctx = make_ctx()
    ctx.kiln.add_memory(
        "UNMEASURED: issue: something", "task::t1", ["issue"], memory_id="issue_1"
    )
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    ctx.kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("item_0", accuracy=1.0)
    ] + [make_run(f"item_{i}", unrelated=1.0) for i in range(1, 24)]

    outcome = await measure(ctx)

    assert outcome.status == "failed_infra"
    assert "accuracy (1/24)" in outcome.infra_error.detail
    assert ctx.kiln.memory_patches == []
    assert ctx.kiln.memories["issue_1"]["overview"].startswith("UNMEASURED: ")


async def test_an_eval_build_never_reopens_a_closed_issue():
    # The loop's OTHER WONT_FIX: writer, through the eval_only lane: an
    # agent that cannot reproduce a FIXED: issue must not relabel the fix as
    # a decision. Both mark_wont_fix call sites sit before open_experiment,
    # so the refusal is a plain failed_infra with nothing written.
    ctx = make_ctx()
    ctx.kiln.add_memory(
        "FIXED: the client drops 429 retries",
        "task::t1",
        ["issue"],
        content="- fixed 07-20 by rc_207, EXP 0720-w3-1",
        memory_id="issue_1",
    )
    ctx.dispatch.queue("eval_build", ok(eval_report(reproduced=False, reason="gone")))

    outcome = await execute_plan_item(ctx, eval_only_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "failed_infra"
    assert "already fixed" in outcome.infra_error.detail
    assert ctx.kiln.memory_patches == []
    record = ctx.kiln.memories["issue_1"]
    assert record["overview"] == "FIXED: the client drops 429 retries"
    assert record["content"] == "- fixed 07-20 by rc_207, EXP 0720-w3-1"


async def test_a_fix_items_eval_build_never_reopens_a_closed_issue():
    ctx = make_ctx()
    ctx.kiln.add_memory(
        "WONT_FIX: the client drops 429 retries",
        "task::t1",
        ["issue"],
        memory_id="issue_1",
    )
    ctx.dispatch.queue("eval_build", ok(eval_report(reproduced=False, reason="gone")))

    outcome = await execute_plan_item(
        ctx, fix_item(needs_eval=True, eval_id=None), BASELINE, ["rc_base"], 1
    )

    assert outcome.status == "failed_infra"
    assert "already wont_fix" in outcome.infra_error.detail
    # Refused before open_experiment — no experiment memory, no rc build.
    assert ctx.kiln.memory_posts == []
    assert ctx.dispatch.calls_to("create_experiment_rc") == []


async def test_a_capped_results_read_never_closes_the_issue():
    # The fourth denominator: a CLEAN job ("succeeded", 0 errors) whose
    # progress total outruns what the results route returns. health.clean
    # passes, every stored row carries the score, and the summary would have
    # read n=2/2 while the split held 24.
    ctx = make_ctx()
    ctx.kiln.add_memory(
        "UNMEASURED: issue: something", "task::t1", ["issue"], memory_id="issue_1"
    )
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    key = ("eval_1", "rc_base", "train")
    ctx.kiln.pending[key] = [make_run(f"item_{i}", accuracy=1.0) for i in range(2)]
    ctx.kiln.job_progress[key] = [{"success": 24, "error": 0, "total": 24}]

    outcome = await measure(ctx)

    assert outcome.status == "failed_infra"
    assert "accuracy (2/24)" in outcome.infra_error.detail
    assert ctx.kiln.memory_patches == []
    assert ctx.kiln.memories["issue_1"]["overview"].startswith("UNMEASURED: ")


# ---- total-hole guard, remeasure, auto-target ----


async def test_total_measurement_hole_skips_the_analyzer_dispatch():
    # A 429 storm can leave ZERO measured rows, and paying an analysis agent
    # to report "no verdict possible" is waste. A bundle with no rows and
    # only holes fails the item without dispatching the analyzer. The
    # engine's first chunk storing no row is the hole.
    ctx = make_ctx()
    seed_issue(ctx.kiln)
    seed_measurement(ctx.kiln)
    ctx.kiln.job_behaviors[("eval_1", "rc_new", "train")] = ["fail", "fail"]
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_new", build_notes=""))
    )
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)
    assert outcome.status == "failed_infra"
    assert "stored no row for rc_new" in outcome.infra_error.detail
    assert ctx.dispatch.calls_to("analyze_experiment_results") == []
    # The experiment memory was still stamped terminally.
    assert ctx.kiln.memories[outcome.experiment_memory_id]["overview"].endswith(
        "— FAILED (infra: provider_error)"
    )


async def test_fix_item_reuses_an_existing_rc_without_a_build():
    # A prior run built AND measured this rc and lost only its
    # analysis to the deadline. The planner names it; no build session, no
    # build spend; the item measures and analyzes it, and its record says so.
    ctx = make_ctx()
    seed_issue(ctx.kiln)
    seed_measurement(ctx.kiln, "rc_old")
    item = fix_item()
    item.work.existing_rc_id = "rc_old"
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))

    outcome = await execute_plan_item(ctx, item, BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert outcome.rc_id == "rc_old"
    assert ctx.dispatch.calls_to("create_experiment_rc") == []
    assert not [
        f
        for k, f in ctx.log.events
        if k == "spend" and f.get("source") == "create_experiment_rc"
    ]
    details = [f["detail"] for k, f in ctx.log.events if k == "item_detail"]
    assert "reusing rc rc_old" in details
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    assert "reused rc: rc_old" in record["content"]
    assert "rc: rc_old" in record["content"]  # carried through the verdict


async def test_build_records_the_rc_and_the_change_on_the_experiment_record():
    # The rc id lands on the RUNNING record the moment the build
    # returns (an abandoned record must name what it built), with the build
    # agent's one-line change summary; both survive the verdict, and the
    # structured change is logged for the UIs.
    from kiln_harness_tuner.schemas import RcBuildResult, RcChange

    ctx = make_ctx()
    seed_issue(ctx.kiln)
    seed_measurement(ctx.kiln, "rc_new")
    change = RcChange(
        summary="wrapper v8 gates PATCH on a read-first check",
        changes=["tool 424242705051 replaced by wrapper 555 (read-first on PATCH)"],
        unchanged=["prompt", "model", "get_operator_context"],
        consistency_check="parameter description vs code: consistent",
    )
    ctx.dispatch.queue(
        "create_experiment_rc",
        ok(RcBuildResult(rc_id="rc_new", build_notes="built", change=change)),
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))

    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    assert "rc: rc_new" in record["content"]
    assert "change: wrapper v8 gates PATCH on a read-first check" in record["content"]
    (event,) = [f for k, f in ctx.log.events if k == "item_change"]
    assert event["item_id"] == "w1-1"
    assert event["change"]["summary"] == change.summary


async def test_remeasure_item_tops_up_an_existing_rc_in_pure_code():
    from kiln_harness_tuner.schemas import RemeasureItem

    ctx = make_ctx()
    kiln = ctx.kiln
    kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    kiln.pending[("eval_1", "rc_old", "train")] = [
        make_run("i1", accuracy=1.0),
        make_run("i2", accuracy=0.0),
    ]
    item = RemeasureItem(
        id="w2-4",
        type="remeasure",
        rc_id="rc_old",
        eval_ids=["eval_1"],
        reason="429 storm wiped this arm's coverage",
    )
    outcome = await execute_plan_item(ctx, item, BASELINE, ["rc_base"], 2)
    assert outcome.status == "completed"
    assert outcome.rc_id == "rc_old"
    assert outcome.result is None  # never a promotion candidate
    assert kiln.job_invocations == [("eval_1", "rc_old", "train")]
    assert ctx.dispatch.calls == []  # pure code — no agent session
    details = [f["detail"] for k, f in ctx.log.events if k == "item_detail"]
    assert details == ["eval_1: 2/2 stored"]


async def test_remeasure_failure_is_infra_and_names_the_hole():
    from kiln_harness_tuner.schemas import RemeasureItem

    ctx = make_ctx()
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    ctx.kiln.job_behaviors[("eval_1", "rc_old", "train")] = ["fail", "fail"]
    item = RemeasureItem(
        id="w2-4",
        type="remeasure",
        rc_id="rc_old",
        eval_ids=["eval_1"],
        reason="finish the arm",
    )
    outcome = await execute_plan_item(ctx, item, BASELINE, ["rc_base"], 2)
    assert outcome.status == "failed_infra"
    assert outcome.rc_id == "rc_old"
    assert ctx.kiln.memory_posts == []  # nothing written — retryable for free


async def test_commissioned_eval_joins_the_evaluation_target():
    # An item that commissions its own eval must MEASURE it: the eval does
    # not exist at plan time, so it must still enter evaluation_target.
    ctx = make_ctx()
    seed_issue(ctx.kiln)
    seed_measurement(ctx.kiln)  # eval_1 parent stored + candidate pending
    kiln = ctx.kiln
    kiln.evals["eval_2"] = make_eval("eval_2", "ec_2")
    kiln.results[("eval_2", "rc_base", "train")] = [make_run("item_1", accuracy=0.0)]
    kiln.pending[("eval_2", "rc_new", "train")] = [make_run("item_1", accuracy=1.0)]
    commissioned = EvalBuildReport(
        eval_id="eval_2",
        eval_config_id="ec_2",
        eval_type="llm_judge",
        case_count=5,
        reproduced=True,
        baseline_headline="40% pass",
        priority="p1",
    )
    ctx.dispatch.queue("eval_build", ok(commissioned))
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_new", build_notes=""))
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    item = fix_item(needs_eval=True, eval_id=None)

    outcome = await execute_plan_item(ctx, item, BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert [t.eval_id for t in item.evaluation_target] == ["eval_1", "eval_2"]
    train_jobs = {j for j in kiln.job_invocations if j[2] == "train"}
    assert ("eval_2", "rc_new", "train") in train_jobs  # actually measured


async def test_analyzer_success_is_vetoed_by_the_code_rule_on_a_flat_delta():
    """`successful` = analyzer AND code rule. A flat train result the
    analyzer calls a success must not buy a full val pass."""
    ctx = make_ctx()
    kiln = ctx.kiln
    kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("t1", accuracy=1.0),
        make_run("t2", accuracy=0.0),
    ]
    kiln.pending[("eval_1", "rc_cand", "train")] = [
        make_run("t1", accuracy=1.0),
        make_run("t2", accuracy=0.0),
    ]
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_cand", build_notes="b"))
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result(successful=True)))

    outcome = await execute_plan_item(
        ctx, fix_item(residual_gate=False), BASELINE, ["rc_base"], 1
    )

    assert outcome.status == "completed"
    assert outcome.result is not None and outcome.result.successful is False
    assert "[code rule] not successful" in outcome.result.findings
    (rule,) = [f for k, f in ctx.log.events if k == "success_rule"]
    assert rule["analyzer_successful"] is True and rule["rule_ok"] is False
    assert any("did not improve" in r for r in rule["reasons"])


async def test_analyzer_veto_survives_a_passing_code_rule():
    ctx = make_ctx()
    kiln = ctx.kiln
    kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("t1", accuracy=0.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("t1", accuracy=1.0)]
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_cand", build_notes="b"))
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result(successful=False)))

    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.result is not None and outcome.result.successful is False
    (rule,) = [f for k, f in ctx.log.events if k == "success_rule"]
    assert rule["rule_ok"] is True and rule["analyzer_successful"] is False


async def test_a_prediction_miss_veto_is_overridden_when_the_code_rule_passes():
    """A real +3.3pp win must not be vetoed for missing a +15pp prediction
    that the baseline's 11.7pp of headroom made impossible: the veto would
    make every later wave treat the lever as refuted. A missed prediction
    is caution, never the verdict."""
    ctx = make_ctx()
    kiln = ctx.kiln
    kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("t1", accuracy=0.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("t1", accuracy=1.0)]
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_cand", build_notes="b"))
    )
    ctx.dispatch.queue(
        "analyze_experiment_results",
        ok(make_result(successful=False, veto_reason="prediction_miss")),
    )

    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.result is not None and outcome.result.successful is True
    assert "[code rule] successful" in outcome.result.findings
    (rule,) = [f for k, f in ctx.log.events if k == "success_rule"]
    assert rule["rule_ok"] is True and rule["analyzer_successful"] is False
    (overridden,) = [f for k, f in ctx.log.events if k == "veto_overridden"]
    assert overridden["veto_reason"] == "prediction_miss"


async def test_other_veto_reasons_still_stand_over_a_passing_code_rule():
    ctx = make_ctx()
    kiln = ctx.kiln
    kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("t1", accuracy=0.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("t1", accuracy=1.0)]
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_cand", build_notes="b"))
    )
    ctx.dispatch.queue(
        "analyze_experiment_results",
        ok(make_result(successful=False, veto_reason="dominated_by_ingredient")),
    )

    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.result is not None and outcome.result.successful is False
    assert [k for k, _ in ctx.log.events if k == "veto_overridden"] == []


# ---- an analysis error is the ITEM's failure; the ladder
# ---- keeps the spend of a session that hit the wall


async def test_an_uncomparable_target_fails_the_item_not_the_run():
    """The gate skips an eval with no comparable score; the train side used
    to raise out of the wave for the same shape (an efficiency metric eval)."""
    ctx = make_ctx()
    kiln = ctx.kiln
    kiln.evals["eval_1"] = make_eval(
        "eval_1", "ec_1", scores=[make_score("Tokens", "custom", "lower_is_better")]
    )
    kiln.results[("eval_1", "rc_base", "train")] = [make_run("t1", tokens=3.0)]
    kiln.pending[("eval_1", "rc_cand", "train")] = [make_run("t1", tokens=2.0)]
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_cand", build_notes="b"))
    )

    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "failed_infra"
    assert outcome.infra_error is not None
    assert "custom" in outcome.infra_error.detail
    assert outcome.experiment_memory_id is not None
    stored = kiln.memories[outcome.experiment_memory_id]["overview"]
    assert "FAILED" in stored
    assert ctx.dispatch.calls_to("analyze_experiment_results") == []


async def test_call_with_retries_adds_the_ladder_spend_to_the_breaker():
    from kiln_harness_tuner.provider_errors import ProviderQuotaError
    from kiln_harness_tuner.schemas import ErrorInfo, Usage

    async def noop_sleep(_secs: float) -> None:
        return None

    calls = 0

    async def fn(_req):
        nonlocal calls
        calls += 1
        if calls == 1:
            return infra("provider_error").model_copy(
                update={"usage": Usage(cost_usd=2.0)}
            )
        raise ProviderQuotaError(
            ErrorInfo(error_class="quota_exhausted", detail="wall"),
            usage=Usage(cost_usd=0.5, unmeasured_tokens=3),
        )

    with pytest.raises(ProviderQuotaError) as info:
        await call_with_retries(fn, object(), backoffs=[0.0], sleep=noop_sleep)
    assert info.value.usage.cost_usd == 2.5
    assert info.value.usage.unmeasured_tokens == 3


# ---- named targets gate the train run (through the engine:
# ---- the named items are the queue's first block)


def _targeted_fix(targets):
    item = fix_item()
    item.work.target_items = targets
    return item


async def test_trial_landed_then_full_train_measurement_and_analysis():
    ctx = make_ctx()
    seed_measurement(ctx.kiln)  # item_1 fails on the baseline, item_2 passes
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_new", build_notes="built"))
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))

    outcome = await execute_plan_item(
        ctx, _targeted_fix(["item_1"]), BASELINE, ["rc_base"], 1
    )

    assert outcome.status == "completed"
    assert outcome.measurement is not None
    assert outcome.measurement.status == "completed"
    # Per eval, the items judged of the items planned — here
    # the eval's whole train split (the report's stop-reason appendix).
    assert outcome.measurement.planned_per_eval == {"eval_1": 2}
    assert outcome.measurement.judged_per_eval == {"eval_1": 2}
    # The named item leads the queue; the analysis re-drives nothing.
    (drawn,) = [f for k, f in ctx.log.events if k == "queue_drawn"]
    assert drawn["queue"][0] == ["eval_1", "item_1", "named", "named"]
    jobs = [
        c[3]
        for c in ctx.kiln.calls
        if c[1] == "/api/jobs/evals/run" and c[3]["run_config_id"] == "rc_new"
    ]
    assert jobs[0]["item_ids"][0] == "item_1"
    assert not ctx.kiln.pending.get(("eval_1", "rc_new", "train"))


async def test_trial_not_landed_skips_the_train_run_and_stamps_the_memory():
    ctx = make_ctx()
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    ctx.kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("item_1", accuracy=0.0),
        make_run("item_2", accuracy=1.0),
    ]
    # The candidate still fails item_1: the build missed its target.
    ctx.kiln.pending[("eval_1", "rc_new", "train")] = [
        make_run("item_1", accuracy=0.0),
        make_run("item_2", accuracy=1.0),
    ]
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_new", build_notes="built"))
    )

    outcome = await execute_plan_item(
        ctx, _targeted_fix(["item_1"]), BASELINE, ["rc_base"], 1
    )

    assert outcome.status == "not_landed" and outcome.rc_id == "rc_new"
    assert outcome.measurement is not None
    assert outcome.measurement.stop_kind == "targets"
    # No analyzer session, no further job: the first chunk only.
    assert ctx.dispatch.calls_to("analyze_experiment_results") == []
    jobs = [
        c[3]
        for c in ctx.kiln.calls
        if c[1] == "/api/jobs/evals/run" and c[3]["run_config_id"] == "rc_new"
    ]
    assert len(jobs) == 1 and jobs[0]["item_ids"][0] == "item_1"
    exp = ctx.kiln.memories[outcome.experiment_memory_id]
    # The pre-engine overview shape is kept.
    assert exp["overview"].endswith(" — NOT LANDED (0/1 targets fixed)")


async def test_fix_without_target_items_runs_no_trial():
    ctx = make_ctx()
    seed_measurement(ctx.kiln)
    ctx.dispatch.queue(
        "create_experiment_rc", ok(RcBuildResult(rc_id="rc_new", build_notes="built"))
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)
    assert outcome.status == "completed" and outcome.trial is None
    assert "item_trial" not in [k for k, _ in ctx.log.events]
