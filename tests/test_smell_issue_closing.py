"""The smell issues of earlier baselines (`smells.run.close_earlier_smell_issues`):
what "the new baseline no longer shows the smell" means for a detector in
code and for a reading by the baseline reviewer, who decides, the write that
closes an issue, and what the run shows of it."""

from __future__ import annotations

import re

import pytest
from conftest import make_ctx, make_tracker
from fake_dispatcher import ok
from test_baseline_review import finding, smell_memories
from test_orchestrator import _promotion_scenario, decision, seed_baseline
from test_smells import baseline_row, events, inputs, tool_ids

from kiln_harness_tuner.memory_schema import IssueState, issue_state, set_state_prefix
from kiln_harness_tuner.orchestrator import orchestrate
from kiln_harness_tuner.prompt_text import RcText
from kiln_harness_tuner.provider_errors import ProviderQuotaError
from kiln_harness_tuner.report import render_smells_appendix
from kiln_harness_tuner.schemas import (
    BaselineRef,
    BaselineReview,
    BaselineReviewResult,
    Detection,
    ErrorInfo,
    SmellIssueCheck,
    SmellIssueClosure,
)
from kiln_harness_tuner.smells import (
    DETECTORS,
    Detector,
    DetectorInputs,
    ModelFacts,
    ensure_baseline_reviewed,
    run_detectors,
)
from kiln_harness_tuner.smells.run import (
    REVIEW_MAX_EARLIER_ISSUES,
    earlier_issue_verdict,
    review_line,
)
from kiln_harness_tuner.smells.thresholds import LONG_PROMPT_CHARS, MANY_TOOLS

BY_ID = {d.id: d for d in DETECTORS}
SURE = frozenset(BY_ID)
# The smells an agent can detect, as `_smells_for_a_reader` gives them.
READERS = frozenset({"fixed-call-chain", "very-long-prompt"})
DONE = BaselineReview(rc_id="rc_new", status="done")
LINEAGE = ["rc_new", "rc_base"]
NEW = BaselineRef(rc_id="rc_new")


def detection(smell: str, detector: str) -> Detection:
    return Detection(smell=smell, detector=detector, scope="harness", fact="A fact.")


def gone(issue_id: str = "m", note: str = "") -> SmellIssueCheck:
    return SmellIssueCheck(issue_id=issue_id, shows=False, note=note)


def verdict(smell: str, **changes):
    given = {
        "detections": [],
        "sure_detectors": SURE,
        "readers": READERS,
        "review": DONE,
        "check": None,
        "found": (),
    }
    return earlier_issue_verdict(smell, **{**given, **changes})


# ---- the rule: a smell that only code detects ----


def test_code_closes_a_code_smell_when_its_detector_is_sure_and_silent():
    assert verdict("too-many-tools") == (
        "code",
        "the detector `many_tools` did not fire",
    )


@pytest.mark.parametrize("status", ["off", "skipped", "failed"])
def test_code_closes_a_code_smell_without_a_review(status):
    review = BaselineReview(rc_id="rc_new", status=status)
    assert verdict("too-many-tools", review=review)[0] == "code"


def test_a_code_smell_stays_open_while_its_detector_fires():
    fires = [detection("too-many-tools", "many_tools")]
    assert verdict("too-many-tools", detections=fires) == (
        None,
        "the detector `many_tools` fires on this baseline",
    )


def test_a_code_smell_stays_open_when_its_detector_could_not_tell():
    """A detector that raised, or that could not read what it needs, gives
    an empty answer too. That answer does not show that the smell is gone."""
    assert verdict("too-many-tools", sure_detectors=SURE - {"many_tools"}) == (
        None,
        "the detector `many_tools` could not tell on this baseline",
    )


def test_a_finding_of_a_code_smell_in_the_same_review_keeps_its_issue_open():
    """The reviewer can name a smell that only code detects, when it saw
    more than the detector states. The detector is silent on the new
    baseline, and the review names the smell there: the two disagree."""
    assert verdict("too-many-tools", found={"too-many-tools"}) == (
        None,
        "the design review names the smell on this baseline",
    )


def test_a_smell_with_no_detector_and_no_reader_stays_open():
    assert verdict("a-smell-with-no-document")[0] is None


def test_every_issue_stays_open_when_the_smell_documents_cannot_be_read():
    assert verdict("too-many-tools", readers=None) == (
        None,
        "the smell documents cannot be read",
    )


# ---- the rule: a smell that an agent detects ----


def test_the_reviewer_decides_for_a_smell_that_needs_a_reader():
    assert verdict("fixed-call-chain", check=gone()) == (
        "review",
        "the design review read the issue again and did not see the smell",
    )


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({}, "the design review did not read this issue again"),
        (
            {"check": SmellIssueCheck(issue_id="m", shows=None)},
            "the design review could not tell",
        ),
        (
            {"check": SmellIssueCheck(issue_id="m", shows=True)},
            "the design review still sees the smell",
        ),
        # A reading of "gone" and a finding of the same smell disagree.
        (
            {"check": gone(), "found": {"fixed-call-chain"}},
            "the design review names the smell on this baseline",
        ),
        (
            {
                "check": gone(),
                "review": BaselineReview(rc_id="rc_new", status="failed"),
            },
            "no design review of this baseline was done",
        ),
    ],
)
def test_a_reader_smell_stays_open_without_a_clear_reading(changes, reason):
    assert verdict("fixed-call-chain", **changes) == (None, reason)


def test_a_smell_of_both_kinds_needs_the_detector_and_the_reviewer():
    smell = "very-long-prompt"
    fires = [detection(smell, "long_prompt")]
    assert verdict(smell, check=gone(), detections=fires)[0] is None
    assert verdict(smell, check=gone(), sure_detectors=frozenset())[0] is None
    # A silent detector alone does not close it: a reader finds the other
    # form of the smell at any length.
    assert verdict(smell)[0] is None
    assert verdict(smell, check=gone()) == (
        "review",
        "the design review read the issue again and did not see the smell;"
        " the detector `long_prompt` did not fire",
    )


# ---- which detectors are sure ----


def test_a_detector_is_blind_when_it_cannot_read_what_it_needs():
    unread = DetectorInputs(
        rc_id="rc",
        properties={},
        texts=RcText(prompt=None, skill_ids=("s1",), skills=None),
        unread_tools=("mcp::a",),
    )
    blind = {d.id for d in DETECTORS if d.blind is not None and d.blind(unread)}
    assert blind == {
        "long_prompt",
        "long_tool_description",
        "long_skill_description",
        "old_model",
    }
    # The two counts need only the run config.
    assert BY_ID["many_tools"].blind is None
    assert BY_ID["many_skills"].blind is None


def test_no_detector_is_blind_on_inputs_that_it_can_read():
    from datetime import date

    readable = DetectorInputs(
        rc_id="rc",
        properties={},
        texts=inputs().texts,
        model=ModelFacts("openai", "m", "M", date(2026, 1, 1)),
        run_date=date(2026, 2, 1),
    )
    assert not [d.id for d in DETECTORS if d.blind is not None and d.blind(readable)]


async def test_run_detectors_names_the_detectors_whose_answer_is_sure():
    def boom(_inputs):
        raise RuntimeError("boom")

    ctx = make_ctx()
    ctx.kiln.run_configs = [baseline_row("short")]
    detectors = (
        Detector("fine", "very-long-prompt", lambda _i: []),
        Detector("broken", "very-long-prompt", boom),
        Detector("blind", "very-long-prompt", lambda _i: [], blind=lambda _i: True),
    )
    sure: set[str] = set()
    await run_detectors(ctx, "rc_base", detectors=detectors, sure=sure)
    assert sure == {"fine"}


async def test_a_tool_with_no_readable_definition_makes_its_detector_unsure():
    ctx = make_ctx()
    ctx.kiln.run_configs = [baseline_row("short", ("mcp::unread",))]
    sure: set[str] = set()
    await run_detectors(ctx, "rc_base", sure=sure)
    assert "long_tool_description" not in sure
    assert {"long_prompt", "many_tools", "many_skills"} <= sure


# ---- the step, on a fake Kiln ----


def row(rc_id: str, prompt: str = "short", tools: tuple[str, ...] = ()) -> dict:
    return {**baseline_row(prompt, tools), "id": rc_id}


def new_baseline_ctx(new_row: dict | None = None, *, review: bool = False):
    """A task whose baseline moved from `rc_base` to `rc_new`."""
    ctx = make_ctx()
    ctx.baseline_review = review
    seed_baseline(ctx.kiln)
    ctx.kiln.run_configs = [row("rc_base"), new_row or row("rc_new")]
    return ctx


async def earlier_issue(ctx, smell: str, rc_id: str = "rc_base") -> str:
    return await ctx.memory.record_smell(finding(smell), rc_id)


def state(ctx, issue_id: str) -> IssueState:
    return issue_state(ctx.kiln.memories[issue_id]["overview"])


async def test_code_closes_the_issue_of_a_smell_that_its_detector_no_longer_shows():
    ctx = new_baseline_ctx()
    issue = await earlier_issue(ctx, "too-many-tools")
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)

    record = ctx.kiln.memories[issue]
    assert record["overview"].startswith("FIXED: issue: ")
    assert re.fullmatch(
        r"- no longer shown on baseline rc_new \([^)]+\): the detector"
        r" `many_tools` did not fire",
        record["content"].splitlines()[-1],
    )
    closure = SmellIssueClosure(
        issue_id=issue,
        smell="too-many-tools",
        found_in_rc="rc_base",
        closed_by="code",
        basis="the detector `many_tools` did not fire",
    )
    (review,) = ctx.baseline_reviews
    assert (review.status, review.closed_issues) == ("off", [closure])
    (checked,) = events(ctx, "smell_issues_checked")
    assert checked == {"rc": "rc_new", "closed": [closure], "kept": []}
    (event,) = events(ctx, "baseline_review")
    assert event["line"] == (
        "Design review skipped: turned off. 1 earlier smell issue closed."
    )


async def test_the_issue_stays_open_while_the_detector_fires_on_the_new_baseline():
    ctx = new_baseline_ctx(row("rc_new", tools=tool_ids(MANY_TOOLS + 1)))
    issue = await earlier_issue(ctx, "too-many-tools")
    before = dict(ctx.kiln.memories[issue])
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)

    assert ctx.kiln.memories[issue] == before
    (review,) = ctx.baseline_reviews
    assert review.closed_issues == []
    (checked,) = events(ctx, "smell_issues_checked")
    assert checked["kept"] == [
        {
            "issue": issue,
            "smell": "too-many-tools",
            "reason": "the detector `many_tools` fires on this baseline",
        }
    ]


async def test_an_unread_tool_definition_keeps_a_tool_description_issue_open():
    ctx = new_baseline_ctx(row("rc_new", tools=("mcp::unread",)))
    issue = await earlier_issue(ctx, "very-long-tool-description")
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)
    assert state(ctx, issue) is IssueState.OPEN
    (checked,) = events(ctx, "smell_issues_checked")
    assert checked["kept"][0]["reason"] == (
        "the detector `long_tool_description` could not tell on this baseline"
    )


async def test_detectors_that_did_not_run_close_nothing():
    """The run config of the new baseline cannot be read, so no detector
    ran. An empty list of detections then shows nothing."""
    ctx = new_baseline_ctx()
    ctx.kiln.run_configs = [row("rc_base")]
    issue = await earlier_issue(ctx, "too-many-tools")
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)
    assert events(ctx, "smell_detection_skipped")
    assert state(ctx, issue) is IssueState.OPEN


async def test_only_the_issues_of_the_lineage_are_read():
    ctx = new_baseline_ctx()
    elsewhere = await earlier_issue(ctx, "too-many-tools", "rc_other")
    present = await earlier_issue(ctx, "too-many-tools", "rc_new")
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)
    assert state(ctx, elsewhere) is IssueState.OPEN
    assert state(ctx, present) is IssueState.OPEN
    assert events(ctx, "smell_issues_checked") == []


async def test_the_first_baseline_of_a_lineage_reads_no_issue(monkeypatch):
    ctx = new_baseline_ctx()

    async def never(*_args):
        raise AssertionError("no earlier baseline, so no read")

    monkeypatch.setattr(ctx.memory, "open_smell_issues", never)
    await ensure_baseline_reviewed(ctx, BaselineRef(rc_id="rc_base"))
    await ensure_baseline_reviewed(ctx, NEW, lineage=["rc_new"])
    assert events(ctx, "smell_issue_check_skipped") == []


@pytest.mark.parametrize("closed", [IssueState.FIXED, IssueState.WONT_FIX])
async def test_an_issue_that_is_closed_already_is_not_written_again(closed):
    ctx = new_baseline_ctx()
    issue = await earlier_issue(ctx, "too-many-tools")
    record = ctx.kiln.memories[issue]
    record["overview"] = set_state_prefix(record["overview"], closed)
    before = dict(record)
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)
    assert ctx.kiln.memories[issue] == before
    assert await ctx.memory.close_smell(issue, "rc_new", "a basis") is False
    assert ctx.kiln.memories[issue] == before


async def test_a_memory_read_that_fails_keeps_the_run_going(monkeypatch):
    ctx = new_baseline_ctx()

    async def broken(_rc_ids):
        raise RuntimeError("memory is down")

    monkeypatch.setattr(ctx.memory, "open_smell_issues", broken)
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)
    (skipped,) = events(ctx, "smell_issue_check_skipped")
    assert skipped == {"rc": "rc_new", "reason": "RuntimeError: memory is down"}
    (review,) = ctx.baseline_reviews
    assert review.closed_issues == []


async def test_a_write_that_fails_keeps_that_issue_and_closes_the_others(monkeypatch):
    ctx = new_baseline_ctx()
    first = await earlier_issue(ctx, "too-many-tools")
    second = await earlier_issue(ctx, "old-model")
    # `old-model` cannot tell here (no model on the row), so use a second
    # smell that the detectors can read.
    ctx.kiln.memories[second]["tags"][3] = "smell_too-many-skills"
    close = ctx.memory.close_smell

    async def flaky(issue_id, rc_id, basis):
        if issue_id == first:
            raise RuntimeError("write refused")
        return await close(issue_id, rc_id, basis)

    monkeypatch.setattr(ctx.memory, "close_smell", flaky)
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)
    assert state(ctx, first) is IssueState.OPEN
    assert state(ctx, second) is IssueState.FIXED
    (checked,) = events(ctx, "smell_issues_checked")
    assert [c.issue_id for c in checked["closed"]] == [second]
    assert checked["kept"] == [
        {
            "issue": first,
            "smell": "too-many-tools",
            "reason": "the write failed (RuntimeError: write refused)",
        }
    ]


async def test_an_issue_that_another_writer_closed_first_is_kept_as_it_is(monkeypatch):
    """`close_smell` answers False when the issue is closed between the
    read and the write. It is then not counted as closed by this step."""
    ctx = new_baseline_ctx()
    issue = await earlier_issue(ctx, "too-many-tools")

    async def closed_already(*_args):
        return False

    monkeypatch.setattr(ctx.memory, "close_smell", closed_already)
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)
    (review,) = ctx.baseline_reviews
    assert review.closed_issues == []
    (checked,) = events(ctx, "smell_issues_checked")
    assert checked["kept"] == [
        {
            "issue": issue,
            "smell": "too-many-tools",
            "reason": "the issue is closed already",
        }
    ]


async def test_an_error_in_the_step_keeps_every_issue_and_the_review(monkeypatch):
    ctx = new_baseline_ctx()
    issue = await earlier_issue(ctx, "too-many-tools")

    def broken(*_args, **_kwargs):
        raise RuntimeError("a bug in the rule")

    monkeypatch.setattr("kiln_harness_tuner.smells.run.earlier_issue_verdict", broken)
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)
    assert state(ctx, issue) is IssueState.OPEN
    (skipped,) = events(ctx, "smell_issue_check_skipped")
    assert skipped == {"rc": "rc_new", "reason": "RuntimeError: a bug in the rule"}
    (review,) = ctx.baseline_reviews
    assert (review.status, review.closed_issues) == ("off", [])


async def test_a_quota_error_while_reading_the_earlier_issues_is_raised(monkeypatch):
    ctx = new_baseline_ctx()

    async def quota(_rc_ids):
        raise ProviderQuotaError(
            ErrorInfo(error_class="quota_exhausted", detail="out of credits")
        )

    monkeypatch.setattr(ctx.memory, "open_smell_issues", quota)
    with pytest.raises(ProviderQuotaError):
        await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)


async def test_a_quota_error_while_closing_an_issue_is_raised(monkeypatch):
    ctx = new_baseline_ctx()
    await earlier_issue(ctx, "too-many-tools")

    async def quota(*_args):
        raise ProviderQuotaError(
            ErrorInfo(error_class="quota_exhausted", detail="out of credits")
        )

    monkeypatch.setattr(ctx.memory, "close_smell", quota)
    with pytest.raises(ProviderQuotaError):
        await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)


# ---- the step, with the reviewer ----


async def test_the_reviewer_is_given_the_earlier_issues_that_need_a_reader():
    long_prompt = "p" * (LONG_PROMPT_CHARS + 1)
    ctx = new_baseline_ctx(row("rc_new", prompt=long_prompt), review=True)
    reader = await earlier_issue(ctx, "fixed-call-chain")
    # Code decides this one alone.
    await earlier_issue(ctx, "too-many-tools")
    # The detector fires on the new baseline: the smell is still there.
    await earlier_issue(ctx, "very-long-prompt")
    ctx.dispatch.queue("baseline_review", ok(BaselineReviewResult()))
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)

    (request,) = ctx.dispatch.calls_to("baseline_review")
    (asked,) = request.earlier_issues
    assert (asked.issue_id, asked.smell, asked.rc_id) == (
        reader,
        "fixed-call-chain",
        "rc_base",
    )
    assert asked.summary.startswith(
        "Each of the 4 sampled traces calls search, then fetch.\nwhere: t1, search"
    )


def old_lineage(count: int) -> list[str]:
    """`rc_new`, then `count` earlier baselines, the newest first."""
    return ["rc_new", *(f"rc_old_{n}" for n in range(count))]


async def test_the_reviewer_reads_at_most_fifteen_issues_the_newest_baseline_first():
    """A reading of an issue that was not asked about is dropped, so an
    issue past the limit stays open, whatever the reviewer returns."""
    over = REVIEW_MAX_EARLIER_ISSUES + 2
    ctx = new_baseline_ctx(review=True)
    # Written oldest baseline first, so the listing order is not the answer.
    issues = {
        n: await earlier_issue(ctx, "fixed-call-chain", f"rc_old_{n}")
        for n in reversed(range(over))
    }
    ctx.dispatch.queue(
        "baseline_review",
        ok(BaselineReviewResult(issue_checks=[gone(i) for i in issues.values()])),
    )
    await ensure_baseline_reviewed(ctx, NEW, lineage=old_lineage(over))

    (request,) = ctx.dispatch.calls_to("baseline_review")
    assert [i.rc_id for i in request.earlier_issues] == [
        f"rc_old_{n}" for n in range(REVIEW_MAX_EARLIER_ISSUES)
    ]
    (review,) = ctx.baseline_reviews
    assert len(review.issue_checks) == REVIEW_MAX_EARLIER_ISSUES
    states = {n: state(ctx, issue) for n, issue in issues.items()}
    assert states == {
        n: IssueState.FIXED if n < REVIEW_MAX_EARLIER_ISSUES else IssueState.OPEN
        for n in range(over)
    }


async def test_an_issue_whose_detector_could_not_tell_is_not_given_to_the_reviewer():
    """The prompt of the new baseline cannot be read, so `long_prompt` is
    blind. No reading can close a `very-long-prompt` issue then."""
    ctx = new_baseline_ctx(review=True)
    ctx.kiln.run_configs[1] = {**row("rc_new"), "prompt": None}
    ctx.kiln.run_configs[1]["run_config_properties"]["prompt_id"] = "id::missing"
    issue = await earlier_issue(ctx, "very-long-prompt")
    ctx.dispatch.queue(
        "baseline_review", ok(BaselineReviewResult(issue_checks=[gone(issue)]))
    )
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)
    assert "long_prompt" not in ctx.sure_detectors
    (request,) = ctx.dispatch.calls_to("baseline_review")
    assert request.earlier_issues == []
    assert state(ctx, issue) is IssueState.OPEN


async def test_a_reading_of_gone_closes_the_issue_and_code_writes_it():
    ctx = new_baseline_ctx(review=True)
    issue = await earlier_issue(ctx, "fixed-call-chain")
    note = "The new prompt lets the agent choose the second call."
    ctx.dispatch.queue(
        "baseline_review",
        ok(BaselineReviewResult(issue_checks=[gone(issue, note)])),
    )
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)

    record = ctx.kiln.memories[issue]
    assert record["overview"].startswith("FIXED: issue: ")
    last = record["content"].splitlines()[-1]
    assert last.startswith("- no longer shown on baseline rc_new (")
    assert last.endswith(
        "): the design review read the issue again and did not see the smell"
    )
    # The reviewer's own words stay out of the issue. The report has them.
    assert note not in record["content"]
    (review,) = ctx.baseline_reviews
    (closure,) = review.closed_issues
    assert (closure.issue_id, closure.closed_by) == (issue, "review")
    assert review.issue_checks == [gone(issue, note)]
    (event,) = events(ctx, "baseline_review")
    assert event["line"] == (
        "Design review complete: nothing flagged. 1 earlier smell issue closed."
    )


@pytest.mark.parametrize("shows", [True, None])
async def test_any_other_reading_keeps_the_issue_open(shows):
    ctx = new_baseline_ctx(review=True)
    issue = await earlier_issue(ctx, "fixed-call-chain")
    check = SmellIssueCheck(issue_id=issue, shows=shows)
    ctx.dispatch.queue(
        "baseline_review", ok(BaselineReviewResult(issue_checks=[check]))
    )
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)
    assert state(ctx, issue) is IssueState.OPEN


async def test_an_issue_with_no_reading_stays_open():
    ctx = new_baseline_ctx(review=True)
    issue = await earlier_issue(ctx, "fixed-call-chain")
    ctx.dispatch.queue("baseline_review", ok(BaselineReviewResult()))
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)
    assert state(ctx, issue) is IssueState.OPEN
    (checked,) = events(ctx, "smell_issues_checked")
    assert checked["kept"][0]["reason"] == (
        "the design review did not read this issue again"
    )


async def test_a_finding_of_the_same_smell_keeps_the_earlier_issue_open():
    """The reviewer says "gone" and also names the smell on the new
    baseline. The two disagree, so the earlier issue stays open, and the new
    baseline gets its own issue."""
    ctx = new_baseline_ctx(review=True)
    issue = await earlier_issue(ctx, "fixed-call-chain")
    ctx.dispatch.queue(
        "baseline_review",
        ok(BaselineReviewResult(findings=[finding()], issue_checks=[gone(issue)])),
    )
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)
    assert state(ctx, issue) is IssueState.OPEN
    audited = sorted(m["tags"][-1] for m in smell_memories(ctx.kiln))
    assert audited == ["audited_rc_rc_base", "audited_rc_rc_new"]


async def test_two_readings_of_one_issue_that_disagree_keep_it_open():
    ctx = new_baseline_ctx(review=True)
    issue = await earlier_issue(ctx, "fixed-call-chain")
    still = SmellIssueCheck(issue_id=issue, shows=True)
    ctx.dispatch.queue(
        "baseline_review",
        ok(BaselineReviewResult(issue_checks=[still, gone(issue)])),
    )
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)
    assert state(ctx, issue) is IssueState.OPEN
    (checked,) = events(ctx, "smell_issues_checked")
    assert checked["kept"][0]["reason"] == "the design review could not tell"


async def test_a_failed_review_closes_only_what_code_can_decide():
    ctx = new_baseline_ctx(review=True)
    by_code = await earlier_issue(ctx, "too-many-tools")
    by_reader = await earlier_issue(ctx, "fixed-call-chain")

    async def broken(_req):
        raise RuntimeError("no session")

    ctx.dispatch.baseline_review = broken
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)
    (review,) = ctx.baseline_reviews
    assert review.status == "failed"
    assert state(ctx, by_code) is IssueState.FIXED
    assert state(ctx, by_reader) is IssueState.OPEN


async def test_a_baseline_that_was_reviewed_before_closes_only_what_code_can_decide():
    """A resumed lineage: memory holds a smell issue of `rc_new`, so no
    session runs, and no reading of the earlier issues exists."""
    ctx = new_baseline_ctx(review=True)
    by_code = await earlier_issue(ctx, "too-many-tools")
    by_reader = await earlier_issue(ctx, "fixed-call-chain")
    await earlier_issue(ctx, "tool-search", "rc_new")
    await ensure_baseline_reviewed(ctx, NEW, lineage=LINEAGE)
    assert ctx.dispatch.calls_to("baseline_review") == []
    (review,) = ctx.baseline_reviews
    assert (review.status, review.reason) == ("skipped", "already reviewed")
    assert [c.issue_id for c in review.closed_issues] == [by_code]
    assert state(ctx, by_reader) is IssueState.OPEN


async def test_a_wrap_up_wave_closes_what_code_can_decide_with_no_session():
    ctx = new_baseline_ctx(review=True)
    by_code = await earlier_issue(ctx, "too-many-tools")
    by_reader = await earlier_issue(ctx, "fixed-call-chain")
    await ensure_baseline_reviewed(ctx, NEW, wrap_up=True, lineage=LINEAGE)
    assert ctx.dispatch.calls_to("baseline_review") == []
    (review,) = ctx.baseline_reviews
    assert (review.status, review.reason) == ("skipped", "a wrap-up wave")
    assert state(ctx, by_code) is IssueState.FIXED
    assert state(ctx, by_reader) is IssueState.OPEN


# ---- a whole run ----


async def test_a_promotion_closes_the_smell_issue_that_the_new_baseline_lost(tmp_path):
    ctx = make_ctx()
    ctx.baseline_review = True
    _promotion_scenario(ctx)
    ctx.dispatch.queue("promote_best", decision("rc_cand"))
    note = "The agent now chooses each call from the result before it."
    ctx.dispatch.queue(
        "baseline_review",
        ok(BaselineReviewResult(findings=[finding()])),
        lambda req: ok(
            BaselineReviewResult(
                issue_checks=[gone(req.earlier_issues[0].issue_id, note)]
            )
        ),
    )
    await orchestrate(ctx, "g", timeout_mins=180)

    first, second = ctx.dispatch.calls_to("baseline_review")
    assert first.earlier_issues == []
    (asked,) = second.earlier_issues
    assert (asked.smell, asked.rc_id) == ("fixed-call-chain", "rc_base")
    (memory,) = smell_memories(ctx.kiln)
    assert memory["id"] == asked.issue_id
    assert memory["overview"].startswith("FIXED: issue: ")
    assert [len(r.closed_issues) for r in ctx.baseline_reviews] == [0, 1]


# ---- the report ----


def closed_review() -> BaselineReview:
    return BaselineReview(
        rc_id="rc_new",
        status="done",
        issue_checks=[gone("mem_4", "The prompt | now has one topic.")],
        closed_issues=[
            SmellIssueClosure(
                issue_id="mem_4",
                smell="very-long-prompt",
                found_in_rc="rc_base",
                closed_by="review",
                basis="the design review read the issue again and did not see"
                " the smell; the detector `long_prompt` did not fire",
            ),
            SmellIssueClosure(
                issue_id="mem_5",
                smell="too-many-tools",
                found_in_rc="rc_base",
                closed_by="code",
                basis="the detector `many_tools` did not fire",
            ),
        ],
    )


def test_the_report_lists_the_closed_smell_issues():
    tracker = make_tracker()
    tracker.event("baseline_review", review=closed_review())
    text = render_smells_appendix(tracker.snapshot())
    assert "Baseline `rc_new`. Design review: done, 0 finding(s)." in text
    assert (
        "Smell issues of earlier baselines that this baseline no longer shows."
        " Code closed each one."
    ) in text
    assert (
        "| `mem_4` | very-long-prompt | `rc_base` | review | the design review"
        " read the issue again and did not see the smell; the detector"
        " `long_prompt` did not fire | The prompt \\| now has one topic. |"
    ) in text
    assert (
        "| `mem_5` | too-many-tools | `rc_base` | code | the detector"
        " `many_tools` did not fire | — |"
    ) in text


def test_the_review_line_counts_the_closed_issues():
    assert review_line(closed_review(), []) == (
        "Design review complete: nothing flagged. 2 earlier smell issues closed."
    )
    assert review_line(DONE, []) == "Design review complete: nothing flagged."
