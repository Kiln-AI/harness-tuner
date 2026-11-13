"""The baseline reviewer (`smells.run.ensure_baseline_reviewed`): when it
runs in a wave, what it is given, the smell issues code writes from its
findings, what happens when it cannot run, and the smells appendix of the
report."""

from __future__ import annotations

import pytest
from conftest import make_ctx, make_plan, make_tracker
from fake_dispatcher import infra, ok
from fake_kiln import make_eval, make_run
from test_orchestrator import _promotion_scenario, decision, seed_baseline
from test_smells import baseline_row, events

from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.memory_schema import IssueState, set_state_prefix
from kiln_harness_tuner.orchestrator import orchestrate
from kiln_harness_tuner.provider_errors import ProviderQuotaError
from kiln_harness_tuner.report import (
    read_issues_without_eval,
    render_smells_appendix,
)
from kiln_harness_tuner.schemas import (
    BaselineRef,
    BaselineReview,
    BaselineReviewResult,
    Detection,
    DetectionCheck,
    ErrorInfo,
    SmellFinding,
    TraceSample,
)
from kiln_harness_tuner.setup import build_sync_plan
from kiln_harness_tuner.smells import (
    baseline_trace_samples,
    ensure_baseline_reviewed,
)
from kiln_harness_tuner.smells.run import (
    REVIEW_ITEMS_PER_EVAL,
    REVIEW_MAX_FINDINGS,
    REVIEW_MAX_ITEMS,
    flagged_smells,
    review_line,
)
from kiln_harness_tuner.smells.thresholds import LONG_PROMPT_CHARS
from kiln_harness_tuner.ui import SETUP_LINE_EVENTS, own_line

LONG_PROMPT = "p" * (LONG_PROMPT_CHARS + 1)


def finding(smell: str = "fixed-call-chain", evidence: str = "") -> SmellFinding:
    return SmellFinding(
        smell=smell,
        evidence=evidence or "Each of the 4 sampled traces calls search, then fetch.",
        where=["t1", "search"],
        confidence="High: every sampled trace shows it.",
    )


def reviewing_ctx(**kwargs):
    ctx = make_ctx(**kwargs)
    ctx.baseline_review = True
    seed_baseline(ctx.kiln)
    ctx.kiln.run_configs = [baseline_row(LONG_PROMPT)]
    return ctx


def smell_memories(kiln) -> list[dict]:
    return [m for m in kiln.memories.values() if "smell" in m["tags"]]


def order(ctx) -> list[str]:
    return [
        f"{k} {f.get('name')}" if k in ("phase_start", "phase_end") else k
        for k, f in ctx.log.events
    ]


# ---- the step in the loop ----


async def test_the_review_runs_before_the_first_plan():
    ctx = reviewing_ctx()
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    ctx.kiln.specs = [{"id": "spec_1", "eval_id": "eval_1", "status": "active"}]
    ctx.kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("t2", accuracy=1.0),
        make_run("t1", accuracy=0.0),
    ]
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "g", timeout_mins=180)

    (req,) = ctx.dispatch.calls_to("baseline_review")
    assert req.baseline.rc_id == "rc_base"
    assert [d.detector for d in req.detections] == ["long_prompt"]
    assert req.samples == [TraceSample(eval_id="eval_1", item_ids=["t1", "t2"])]
    assert {"very-long-prompt", "tool-search"} <= set(req.known_smells)
    assert req.known_smells == sorted(req.known_smells)
    names = [name for name, _ in ctx.dispatch.calls]
    assert names.index("baseline_review") < names.index("batch_plan")
    steps = order(ctx)
    assert (
        steps.index("phase_start baseline_review")
        < steps.index("smell_detections")
        < steps.index("baseline_review")
        < steps.index("phase_end baseline_review")
        < steps.index("phase_start planning")
    )
    (spend,) = [f for k, f in ctx.log.events if f.get("source") == "baseline_review"]
    assert spend["lane"] == "subscription_equivalent"
    (review,) = ctx.baseline_reviews
    assert (review.rc_id, review.status) == ("rc_base", "done")


async def test_the_planner_input_carries_the_detections():
    ctx = reviewing_ctx()
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "g", timeout_mins=180)
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert [d.detector for d in plan_input.detections] == ["long_prompt"]


async def test_each_finding_becomes_one_smell_issue_with_its_tags():
    ctx = reviewing_ctx()
    second = finding("tool-search", "The agent calls tool_search before each call.")
    ctx.dispatch.queue(
        "baseline_review",
        ok(BaselineReviewResult(findings=[finding(), second], notes="none")),
    )
    await ensure_baseline_reviewed(ctx, BaselineRef(rc_id="rc_base"))

    first, other = smell_memories(ctx.kiln)
    assert first["tags"] == [
        "issue",
        "p1",
        "smell",
        "smell_fixed-call-chain",
        "audited_rc_rc_base",
    ]
    assert first["scope"] == "task::t1"
    assert first["overview"].startswith(f"{IssueState.OPEN.prefix}issue: ")
    assert (
        "— Each of the 4 sampled traces calls search, then fetch."
        in (first["overview"])
    )
    assert first["content"].splitlines() == [
        "Each of the 4 sampled traces calls search, then fetch.",
        "where: t1, search",
        "confidence: High: every sampled trace shows it.",
        "This is the smell `fixed-call-chain`. Its document gives the remedies.",
    ]
    assert other["tags"][3] == "smell_tool-search"
    # No eval binding: a smell is about the harness.
    assert not [t for t in first["tags"] if t.startswith("eval_")]
    (review,) = ctx.baseline_reviews
    assert review.issue_ids == [first["id"], other["id"]]
    assert review.notes == "none"
    (event,) = events(ctx, "baseline_review")
    assert event["review"] == review
    # The line counts the findings and the code detections, each smell once.
    assert event["flagged"] == ["fixed-call-chain", "tool-search", "very-long-prompt"]
    assert event["line"] == (
        "Design review complete: 3 possible issues flagged (fixed call chain,"
        " tool search, very long prompt)."
    )


async def test_the_overview_names_the_smell_by_its_title():
    ctx = reviewing_ctx()
    ctx.dispatch.queue(
        "baseline_review", ok(BaselineReviewResult(findings=[finding()]))
    )
    await ensure_baseline_reviewed(ctx, BaselineRef(rc_id="rc_base"))
    (memory,) = smell_memories(ctx.kiln)
    title = memory["overview"].split("issue: ", 1)[1].split(" — ")[0]
    assert title and title != "fixed-call-chain"


async def test_a_second_review_of_the_same_baseline_writes_nothing():
    ctx = reviewing_ctx()
    ctx.dispatch.queue(
        "baseline_review", ok(BaselineReviewResult(findings=[finding()]))
    )
    await ensure_baseline_reviewed(ctx, BaselineRef(rc_id="rc_base"))
    # The same wave loop again: the guard returns at once.
    await ensure_baseline_reviewed(ctx, BaselineRef(rc_id="rc_base"))
    assert len(ctx.dispatch.calls_to("baseline_review")) == 1

    # A resumed lineage: a new run, the same baseline, its smell issue in
    # memory. No session runs, and nothing is written.
    resumed = make_ctx(kiln=ctx.kiln)
    resumed.baseline_review = True
    await ensure_baseline_reviewed(resumed, BaselineRef(rc_id="rc_base"))
    assert resumed.dispatch.calls_to("baseline_review") == []
    (memory,) = smell_memories(ctx.kiln)
    (review,) = resumed.baseline_reviews
    assert (review.status, review.reason) == ("skipped", "already reviewed")
    assert review.issue_ids == [memory["id"]]
    (skipped,) = events(resumed, "baseline_review_skipped")
    assert skipped["reason"] == "already reviewed"

    # The dedup of one write: an open issue of the same smell and rc.
    issue_id = await ctx.memory.record_smell(finding(), "rc_base")
    assert issue_id == memory["id"]
    assert len(smell_memories(ctx.kiln)) == 1


@pytest.mark.parametrize("state", [IssueState.FIXED, IssueState.WONT_FIX])
async def test_a_baseline_whose_smell_issue_is_closed_is_not_reviewed_again(state):
    ctx = reviewing_ctx()
    ctx.dispatch.queue(
        "baseline_review", ok(BaselineReviewResult(findings=[finding()]))
    )
    await ensure_baseline_reviewed(ctx, BaselineRef(rc_id="rc_base"))
    (memory,) = smell_memories(ctx.kiln)
    memory["overview"] = set_state_prefix(memory["overview"], state)
    resumed = make_ctx(kiln=ctx.kiln)
    resumed.baseline_review = True
    await ensure_baseline_reviewed(resumed, BaselineRef(rc_id="rc_base"))
    assert resumed.dispatch.calls_to("baseline_review") == []
    (review,) = resumed.baseline_reviews
    assert (review.status, review.reason) == ("skipped", "already reviewed")
    assert review.issue_ids == [memory["id"]]
    assert len(smell_memories(ctx.kiln)) == 1


async def test_the_review_runs_again_after_a_promotion():
    ctx = make_ctx()
    ctx.baseline_review = True
    _promotion_scenario(ctx)
    ctx.dispatch.queue("promote_best", decision("rc_cand"))
    ctx.dispatch.queue(
        "baseline_review",
        ok(BaselineReviewResult(findings=[finding()])),
        ok(BaselineReviewResult(findings=[finding()])),
    )
    await orchestrate(ctx, "g", timeout_mins=180)

    reqs = ctx.dispatch.calls_to("baseline_review")
    assert [r.baseline.rc_id for r in reqs] == ["rc_base", "rc_cand"]
    assert [r.rc_id for r in ctx.baseline_reviews] == ["rc_base", "rc_cand"]
    # One issue for each baseline; the earlier one stays as it is.
    audited = sorted(m["tags"][-1] for m in smell_memories(ctx.kiln))
    assert audited == ["audited_rc_rc_base", "audited_rc_rc_cand"]
    plans = ctx.dispatch.calls_to("batch_plan")
    assert [p.baseline.rc_id for p in plans] == ["rc_base", "rc_cand"]


async def test_a_failed_review_does_not_stop_the_run_and_is_in_the_report():
    ctx = reviewing_ctx()
    ctx.dispatch.queue("baseline_review", *([infra("provider_error", "down")] * 4))
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    assert await orchestrate(ctx, "g", timeout_mins=180) == "done"

    (review,) = ctx.baseline_reviews
    assert review.status == "failed"
    assert review.reason == "the review did not run (failed_infra provider_error: down)"
    # At most one retry: the review is on the run's clock before the planner.
    assert len(ctx.dispatch.calls_to("baseline_review")) == 2
    retries = [f for f in events(ctx, "dispatch_retry")]
    assert [r["backoff_secs"] for r in retries] == [30.0]
    (skipped,) = events(ctx, "baseline_review_skipped")
    assert skipped == {"rc": "rc_base", "status": "failed", "reason": review.reason}
    # The run goes on with the code detections.
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert [d.detector for d in plan_input.detections] == ["long_prompt"]
    assert smell_memories(ctx.kiln) == []

    tracker = make_tracker()
    for kind, fields in ctx.log.events:
        tracker.event(kind, **fields)
    text = render_smells_appendix(tracker.snapshot())
    assert (
        "Baseline `rc_base`. Design review: failed (the review did not run"
        " (failed_infra provider_error: down))." in text
    )


async def test_a_review_that_raises_is_recorded_as_failed():
    def boom(_req):
        raise RuntimeError("the session broke")

    ctx = reviewing_ctx()
    ctx.dispatch.queue("baseline_review", boom)
    await ensure_baseline_reviewed(ctx, BaselineRef(rc_id="rc_base"))
    (review,) = ctx.baseline_reviews
    assert (review.status, review.reason) == (
        "failed",
        "RuntimeError: the session broke",
    )
    steps = order(ctx)
    assert steps.count("phase_start baseline_review") == 1
    assert steps.count("phase_end baseline_review") == 1


async def test_a_quota_error_in_the_review_still_ends_the_run():
    def quota(_req):
        raise ProviderQuotaError(
            ErrorInfo(error_class="quota_exhausted", detail="out of credits")
        )

    ctx = reviewing_ctx()
    ctx.dispatch.queue("baseline_review", quota)
    assert await orchestrate(ctx, "g", timeout_mins=180) == "quota_exhausted"
    assert ctx.dispatch.calls_to("batch_plan") == []
    assert ctx.baseline_reviews == []
    steps = order(ctx)
    assert steps.count("phase_end baseline_review") == 1
    (exit_event,) = events(ctx, "exit")
    assert exit_event["reason"] == "quota_exhausted"


async def test_the_flag_turns_the_review_off_and_keeps_the_detections():
    ctx = reviewing_ctx()
    ctx.baseline_review = False
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "g", timeout_mins=180)
    assert ctx.dispatch.calls_to("baseline_review") == []
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert [d.detector for d in plan_input.detections] == ["long_prompt"]
    (review,) = ctx.baseline_reviews
    assert (review.status, review.reason) == ("off", "turned off")
    # No session runs, so no phase opens. The parser and `build_ctx` are in
    # `test_cli.test_the_baseline_audit_and_review_are_on_unless_turned_off`.
    assert "phase_start baseline_review" not in order(ctx)


async def test_a_finding_with_an_unknown_smell_is_dropped():
    ctx = reviewing_ctx()
    ctx.dispatch.queue(
        "baseline_review",
        ok(BaselineReviewResult(findings=[finding("no-such-smell"), finding()])),
    )
    await ensure_baseline_reviewed(ctx, BaselineRef(rc_id="rc_base"))
    (review,) = ctx.baseline_reviews
    assert [f.smell for f in review.findings] == ["fixed-call-chain"]
    assert len(smell_memories(ctx.kiln)) == 1
    (unknown,) = events(ctx, "smell_unknown")
    assert unknown == {"source": "baseline_review", "smell": "no-such-smell"}


async def test_at_most_fifteen_findings_are_kept(monkeypatch):
    ids = [f"smell-{n:02d}" for n in range(REVIEW_MAX_FINDINGS + 3)]
    monkeypatch.setattr(
        "kiln_harness_tuner.smells.run._smell_titles",
        lambda _ctx: {i: i for i in ids},
    )
    ctx = reviewing_ctx()
    many = [finding(i, evidence=f"Sign {n}.") for n, i in enumerate(ids)]
    ctx.dispatch.queue("baseline_review", ok(BaselineReviewResult(findings=many)))
    await ensure_baseline_reviewed(ctx, BaselineRef(rc_id="rc_base"))
    (review,) = ctx.baseline_reviews
    assert len(review.findings) == REVIEW_MAX_FINDINGS
    assert review.findings[-1].evidence == f"Sign {REVIEW_MAX_FINDINGS - 1}."


async def test_a_failed_write_keeps_the_other_findings():
    ctx = reviewing_ctx()
    ctx.kiln.memory_post_behaviors["smell_fixed-call-chain"] = ["http500"]
    second = finding("tool-search", "The agent calls tool_search first.")
    ctx.dispatch.queue(
        "baseline_review", ok(BaselineReviewResult(findings=[finding(), second]))
    )
    await ensure_baseline_reviewed(ctx, BaselineRef(rc_id="rc_base"))
    (memory,) = smell_memories(ctx.kiln)
    assert "smell_tool-search" in memory["tags"]
    (review,) = ctx.baseline_reviews
    assert review.status == "done"
    assert review.issue_ids == ["", memory["id"]]
    assert review.reason == (
        "1 of 2 finding(s) could not be written as issues: fixed-call-chain"
    )
    (failed,) = events(ctx, "smell_issue_write_failed")
    assert failed["smell"] == "fixed-call-chain"


async def test_a_memory_list_that_raises_fails_the_review(monkeypatch):
    ctx = reviewing_ctx()

    async def broken(_rc_id):
        raise KilnAPIError("GET", "/api/projects/p1/memories", 500, "down")

    monkeypatch.setattr(ctx.memory, "find_smell_issues", broken)
    await ensure_baseline_reviewed(ctx, BaselineRef(rc_id="rc_base"))
    assert ctx.dispatch.calls_to("baseline_review") == []
    (review,) = ctx.baseline_reviews
    assert review.status == "failed"
    assert review.reason.startswith("KilnAPIError: ")
    assert smell_memories(ctx.kiln) == []
    # The code detections are kept for the planner.
    assert [d.detector for d in ctx.detections] == ["long_prompt"]


async def test_a_quota_error_while_writing_a_smell_issue_is_raised(monkeypatch):
    ctx = reviewing_ctx()
    written: list[str] = []

    async def quota(one, _rc_id, *, title=None):
        written.append(one.smell)
        raise ProviderQuotaError(
            ErrorInfo(error_class="quota_exhausted", detail="out of credits")
        )

    monkeypatch.setattr(ctx.memory, "record_smell", quota)
    second = finding("tool-search", "The agent calls tool_search first.")
    ctx.dispatch.queue(
        "baseline_review", ok(BaselineReviewResult(findings=[finding(), second]))
    )
    with pytest.raises(ProviderQuotaError):
        await ensure_baseline_reviewed(ctx, BaselineRef(rc_id="rc_base"))
    # The first write stops the loop: the next finding is not tried.
    assert written == ["fixed-call-chain"]
    assert ctx.baseline_reviews == []
    assert events(ctx, "smell_issue_write_failed") == []
    assert order(ctx).count("phase_end baseline_review") == 1


async def test_two_findings_of_one_smell_are_merged():
    ctx = reviewing_ctx()
    again = SmellFinding(
        smell="fixed-call-chain",
        evidence="The same chain runs on t3.",
        where=["t3", "search"],
        confidence="Medium.",
    )
    ctx.dispatch.queue(
        "baseline_review", ok(BaselineReviewResult(findings=[finding(), again]))
    )
    await ensure_baseline_reviewed(ctx, BaselineRef(rc_id="rc_base"))
    (memory,) = smell_memories(ctx.kiln)
    (merged,) = ctx.baseline_reviews[0].findings
    assert merged.evidence.endswith("then fetch. The same chain runs on t3.")
    assert merged.where == ["t1", "search", "t3"]
    assert merged.confidence == "High: every sampled trace shows it. / Medium."
    assert "The same chain runs on t3." in memory["content"]


async def test_an_unreadable_knowledge_skill_keeps_only_ids_that_fit_a_tag(
    monkeypatch,
):
    from kiln_harness_tuner.knowledge.load import KnowledgeError

    def unreadable():
        raise KnowledgeError("no references/")

    monkeypatch.setattr("kiln_harness_tuner.smells.run.load", unreadable)
    ctx = reviewing_ctx()
    ctx.dispatch.queue(
        "baseline_review",
        ok(
            BaselineReviewResult(
                findings=[finding("not a tag!"), finding("some-new-smell")]
            )
        ),
    )
    await ensure_baseline_reviewed(ctx, BaselineRef(rc_id="rc_base"))
    (req,) = ctx.dispatch.calls_to("baseline_review")
    assert req.known_smells == []
    (review,) = ctx.baseline_reviews
    assert [f.smell for f in review.findings] == ["some-new-smell"]
    (memory,) = smell_memories(ctx.kiln)
    assert "smell_some-new-smell" in memory["tags"]
    (unknown,) = events(ctx, "smell_unknown")
    assert unknown["smell"] == "not a tag!"


# ---- the run's clock ----


class Clock:
    """A clock the scripted review moves."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def slow_review(clock: Clock, secs: float):
    def review(_req):
        clock.now += secs
        return ok(BaselineReviewResult())

    return review


async def test_a_review_past_the_time_limit_still_leads_to_the_plan():
    clock = Clock()
    ctx = reviewing_ctx(clock=clock)
    # The run has 180 minutes; the review ends 10 minutes after the limit.
    # The limit is checked at wave boundaries only, and the review is part
    # of wave 1, so wave 1 is a normal wave.
    ctx.dispatch.queue("baseline_review", slow_review(clock, 190 * 60))
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    assert await orchestrate(ctx, "g", timeout_mins=180) == "done"
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert plan_input.should_wrap_up is False
    assert [d.detector for d in plan_input.detections] == ["long_prompt"]


@pytest.mark.parametrize(
    ("error_class", "detail"),
    [
        ("timeout", "the session hit its 20-minute cap"),
        ("parse_failure", "the in-session repair gave no valid result"),
    ],
)
async def test_a_review_that_ends_on_a_terminal_error_is_not_tried_again(
    error_class, detail
):
    """A session that hit its time cap, or whose in-session repair already
    ran, is not tried again: the review sits before the planner."""
    clock = Clock()
    ctx = reviewing_ctx(clock=clock)

    def ended(_req):
        clock.now += 20 * 60
        return infra(error_class, detail)

    ctx.dispatch.queue("baseline_review", ended, ok(BaselineReviewResult()))
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    assert await orchestrate(ctx, "g", timeout_mins=180) == "done"
    assert len(ctx.dispatch.calls_to("baseline_review")) == 1
    assert events(ctx, "dispatch_retry") == []
    (review,) = ctx.baseline_reviews
    assert review.status == "failed"
    assert review.reason == (
        f"the review did not run (failed_infra {error_class}: {detail})"
    )
    # The planner of the wave still runs.
    assert len(ctx.dispatch.calls_to("batch_plan")) == 1


async def test_a_wrap_up_wave_after_a_promotion_pays_for_no_review():
    clock = Clock()
    ctx = make_ctx(clock=clock)
    ctx.baseline_review = True
    _promotion_scenario(ctx)

    def late_decision(_req):
        # The promotion lands after the time limit: wave 2 is the wrap-up.
        clock.now = 180 * 60 + 60
        return decision("rc_cand")

    ctx.dispatch.queue("promote_best", late_decision)
    await orchestrate(ctx, "g", timeout_mins=180)
    reqs = ctx.dispatch.calls_to("baseline_review")
    assert [r.baseline.rc_id for r in reqs] == ["rc_base"]
    late = ctx.baseline_reviews[-1]
    assert (late.rc_id, late.status, late.reason) == (
        "rc_cand",
        "skipped",
        "a wrap-up wave",
    )
    # The detectors still ran on the new baseline, and the wrap-up wave ran.
    assert [e["rc"] for e in events(ctx, "smell_detections")] == [
        "rc_base",
        "rc_cand",
    ]
    plans = ctx.dispatch.calls_to("batch_plan")
    assert [(p.baseline.rc_id, p.should_wrap_up) for p in plans] == [
        ("rc_base", False),
        ("rc_cand", True),
    ]


async def test_a_short_run_still_reviews_its_baseline():
    # No time check skips the review: before the time limit, every wave is
    # a normal wave, and its planner reads the review.
    ctx = reviewing_ctx()
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "g", timeout_mins=5)
    assert len(ctx.dispatch.calls_to("baseline_review")) == 1
    (review,) = ctx.baseline_reviews
    assert review.status == "done"
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert plan_input.should_wrap_up is False


# ---- the trace sample ----


def seed_evals(ctx, rows_by_eval: dict[str, int]) -> None:
    for eval_id, count in rows_by_eval.items():
        ctx.kiln.evals[eval_id] = make_eval(eval_id, f"ec_{eval_id}")
        # Stored in reverse order: the sample sorts them.
        ctx.kiln.results[(eval_id, "rc_base", "train")] = [
            make_run(f"{eval_id}_item{n:02d}", accuracy=float(n % 2))
            for n in reversed(range(count))
        ]
    ctx.kiln.specs = [
        {"id": f"spec_{e}", "eval_id": e, "status": "active"} for e in rows_by_eval
    ]


async def test_the_sample_is_stable_and_inside_its_limits():
    ctx = make_ctx()
    seed_evals(ctx, {f"eval_{n}": 6 for n in range(7)} | {"eval_7": 2, "eval_8": 0})
    first = await baseline_trace_samples(ctx, "rc_base")
    assert first == await baseline_trace_samples(ctx, "rc_base")
    sizes = {s.eval_id: len(s.item_ids) for s in first}
    assert sum(sizes.values()) == REVIEW_MAX_ITEMS
    assert max(sizes.values()) <= REVIEW_ITEMS_PER_EVAL
    # An eval with fewer items keeps them; one with none has an empty sample.
    assert sizes["eval_7"] == 2
    assert sizes["eval_8"] == 0
    # The shares are cut in turn: no eval with items has two fewer than another.
    full = [n for e, n in sizes.items() if e not in ("eval_7", "eval_8")]
    assert max(full) - min(full) <= 1
    # The items are the first ones by id, whatever the score.
    (eval_0,) = [s for s in first if s.eval_id == "eval_0"]
    assert eval_0.item_ids == sorted(eval_0.item_ids)
    assert eval_0.item_ids[0] == "eval_0_item00"


async def test_a_small_task_samples_each_eval_up_to_its_share():
    ctx = make_ctx()
    seed_evals(ctx, {"eval_a": 9, "eval_b": 1})
    samples = await baseline_trace_samples(ctx, "rc_base")
    assert [(s.eval_id, len(s.item_ids)) for s in samples] == [
        ("eval_a", REVIEW_ITEMS_PER_EVAL),
        ("eval_b", 1),
    ]


async def test_an_eval_whose_rows_cannot_be_read_gives_an_empty_sample():
    ctx = make_ctx()
    seed_evals(ctx, {"eval_a": 3, "eval_b": 3})
    ctx.kiln.results_behaviors[("eval_a", "rc_base", "train")] = ["http500"] * 5
    samples = await baseline_trace_samples(ctx, "rc_base")
    assert [(s.eval_id, len(s.item_ids)) for s in samples] == [
        ("eval_a", 0),
        ("eval_b", 3),
    ]


# ---- the smell issues elsewhere ----


async def test_a_smell_issue_is_not_listed_as_an_issue_with_no_eval():
    ctx = reviewing_ctx()
    ctx.dispatch.queue(
        "baseline_review", ok(BaselineReviewResult(findings=[finding()]))
    )
    await ensure_baseline_reviewed(ctx, BaselineRef(rc_id="rc_base"))
    other = ctx.kiln.add_memory("issue: replies drop the block", "task::t1", ["issue"])
    listed = await read_issues_without_eval(ctx)
    assert [i.issue_memory_id for i in listed] == [other]
    plan = await build_sync_plan(ctx)
    assert [i.id for i in plan.unbound_unresolved] == [other]
    assert plan.repair == []


# ---- the tracker, the displays and the report ----


def detection() -> Detection:
    return Detection(
        smell="very-long-prompt",
        detector="long_prompt",
        scope="harness",
        fact="The system prompt has 200,005 characters.",
        reference=200_000,
        reference_source="a default",
    )


def done_review(rc_id: str = "rc_base") -> BaselineReview:
    return BaselineReview(
        rc_id=rc_id,
        status="done",
        findings=[finding()],
        issue_ids=["mem_9"],
        detection_checks=[
            DetectionCheck(
                smell="very-long-prompt",
                detector="long_prompt",
                holds=True,
                note="Most of it is one pasted table.",
            )
        ],
        notes="The output schema has no field for the reason.",
    )


def test_the_tracker_keeps_each_review():
    tracker = make_tracker()
    tracker.event("baseline_review", review=done_review())
    tracker.event("baseline_review", review=done_review("rc_2").model_dump())
    tracker.event("baseline_review", review={"status": "odd"})
    status = tracker.snapshot()
    assert [r.rc_id for r in status.baseline_reviews] == ["rc_base", "rc_2"]


def test_the_review_line_is_printed_by_the_plain_display():
    review = BaselineReview(rc_id="rc_base", status="off", reason="turned off")
    line = review_line(review, ["very-long-prompt"])
    assert line == "Design review skipped: turned off."
    assert "baseline_review" in SETUP_LINE_EVENTS
    assert own_line("baseline_review", {"line": line}) == line


@pytest.mark.parametrize(
    "status,reason,flagged,line",
    [
        ("done", "", [], "Design review complete: nothing flagged."),
        (
            "done",
            "",
            ["very-long-prompt"],
            "Design review complete: 1 possible issue flagged (very long prompt).",
        ),
        (
            "done",
            "1 of 1 finding(s) could not be written as issues: tool-search",
            ["tool-search"],
            "Design review complete: 1 possible issue flagged (tool search);"
            " 1 of 1 finding(s) could not be written as issues: tool-search.",
        ),
        ("failed", "timeout", [], "Design review failed: timeout."),
        (
            "failed",
            "ValueError: bad input.",
            [],
            "Design review failed: ValueError: bad input.",
        ),
        ("skipped", "already reviewed", [], "Design review skipped: already reviewed."),
    ],
)
def test_the_review_line_names_what_was_flagged(status, reason, flagged, line):
    review = BaselineReview(rc_id="rc_base", status=status, reason=reason)
    assert review_line(review, flagged) == line


def test_the_flagged_smells_name_each_smell_once():
    review = BaselineReview(
        rc_id="rc_base",
        status="done",
        findings=[finding("tool-search"), finding("very-long-prompt")],
    )
    assert flagged_smells(review, [detection(), detection()]) == [
        "tool-search",
        "very-long-prompt",
    ]


def test_the_report_has_the_smells_appendix():
    tracker = make_tracker()
    tracker.event("smell_detections", rc="rc_base", detections=[detection()])
    tracker.event("baseline_review", review=done_review())
    tracker.event("smell_detections", rc="rc_next", detections=[])
    tracker.event(
        "baseline_review",
        review=BaselineReview(rc_id="rc_next", status="off", reason="turned off"),
    )
    text = render_smells_appendix(tracker.snapshot())
    assert text.startswith("## Design review\n")
    assert "Baseline `rc_base`. Design review: done, 1 finding(s)." in text
    assert (
        "| very-long-prompt | The system prompt has 200,005 characters. | 200,000"
        " | a default | holds: Most of it is one pasted table. |"
    ) in text
    assert (
        "| fixed-call-chain | Each of the 4 sampled traces calls search, then"
        " fetch. | t1, search | High: every sampled trace shows it. | `mem_9` |"
    ) in text
    assert "Reviewer's notes: The output schema has no field for the reason." in text
    # A baseline with nothing found is shown when its review did not run.
    assert "Baseline `rc_next`. Design review: off (turned off)." in text
    # The intro says which parts code computed and which the reviewer wrote.
    assert "Code renders this section." in text
    assert "in the reviewer's words" in text


def test_a_skipped_review_lists_its_smell_issues():
    tracker = make_tracker()
    skipped = BaselineReview(
        rc_id="rc_base",
        status="skipped",
        reason="already reviewed",
        issue_ids=["mem_1", "mem_2"],
    )
    tracker.event("baseline_review", review=skipped)
    assert (
        "Baseline `rc_base`. Design review: skipped (already reviewed). Its"
        " smell issues in memory: `mem_1`, `mem_2`."
    ) in render_smells_appendix(tracker.snapshot())


def test_the_smells_appendix_is_left_out_when_every_review_found_nothing():
    tracker = make_tracker()
    tracker.event("smell_detections", rc="rc_base", detections=[])
    tracker.event(
        "baseline_review", review=BaselineReview(rc_id="rc_base", status="done")
    )
    assert render_smells_appendix(tracker.snapshot()) == ""


@pytest.mark.parametrize(
    "holds,word", [(False, "does not hold"), (None, "could not tell")]
)
def test_the_appendix_gives_the_reading_of_each_detection(holds, word):
    review = done_review()
    review.detection_checks[0].holds = holds
    review.detection_checks[0].note = ""
    tracker = make_tracker()
    tracker.event("smell_detections", rc="rc_base", detections=[detection()])
    tracker.event("baseline_review", review=review)
    assert f"| a default | {word} |" in render_smells_appendix(tracker.snapshot())
