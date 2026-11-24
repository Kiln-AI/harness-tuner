"""Questions to the owner (`kiln_harness_tuner.owner_questions`): the
schema, the memory record, the step in the loop with a display that asks
and with none, what the planner gets, a rerun on the same task, and the
report's section."""

from __future__ import annotations

import pytest
from conftest import make_plan, make_tracker
from fake_dispatcher import ok
from pydantic import ValidationError
from test_baseline_review import finding, reviewing_ctx

from kiln_harness_tuner.memory_schema import MemoryRecord
from kiln_harness_tuner.orchestrator import orchestrate
from kiln_harness_tuner.owner_questions import (
    AFTER_WAVE_1,
    LEFT_FOR_LATER,
    NO_DISPLAY,
    OWNER_ANSWER_WAIT_SECS,
    OWNER_QUESTION_TAG,
    OWNER_QUESTIONS_MAX,
    OwnerReplies,
    OwnerReply,
    owner_decision_from_record,
    owner_record_content,
    owner_record_overview,
    planner_open_questions,
    planner_owner_answers,
)
from kiln_harness_tuner.report import (
    OWNER_QUESTIONS_HEADING,
    render_owner_questions_appendix,
)
from kiln_harness_tuner.schemas import (
    BaselineReviewResult,
    OwnerDecision,
    OwnerOption,
    OwnerQuestion,
)
from kiln_harness_tuner.ui import SETUP_LINE_EVENTS, own_line


def question(text: str = "Which rule about refunds wins?", **kwargs) -> OwnerQuestion:
    fields = {
        "why": "The prompt says to refund at once; the skill `refunds` says to ask.",
        "smell": "conflicting-requirements",
        "options": [
            OwnerOption(label="Leave both texts as they are", effect="No change."),
            OwnerOption(label="The prompt wins", effect="The skill is changed."),
            OwnerOption(label="The skill wins", effect="The prompt is changed."),
        ],
        **kwargs,
    }
    return OwnerQuestion(question=text, **fields)


def review_with(*questions: OwnerQuestion):
    return ok(
        BaselineReviewResult(
            findings=[finding("conflicting-requirements")],
            owner_questions=list(questions),
        )
    )


class FakeOwner:
    """`Ctx.ask_owner`: answers with `replies`, and keeps what it was asked."""

    def __init__(self, *replies: OwnerReply | None, reason: str = ""):
        self.replies = replies
        self.reason = reason
        self.asked: list[tuple[list[OwnerQuestion], float]] = []

    async def __call__(self, questions, wait_secs):
        self.asked.append((list(questions), wait_secs))
        replies = list(self.replies)[: len(questions)]
        replies += [None] * (len(questions) - len(replies))
        return OwnerReplies(tuple(replies), self.reason)


def owner_memories(kiln) -> list[dict]:
    return [m for m in kiln.memories.values() if OWNER_QUESTION_TAG in m["tags"]]


def events(ctx, kind: str) -> list[dict]:
    return [fields for k, fields in ctx.log.events if k == kind]


async def run_once(ctx, *questions: OwnerQuestion) -> None:
    ctx.dispatch.queue("baseline_review", review_with(*questions))
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "keep costs low", timeout_mins=180)


def report_of(ctx) -> str:
    tracker = make_tracker()
    for kind, fields in ctx.log.events:
        tracker.event(kind, **fields)
    return render_owner_questions_appendix(tracker.snapshot())


# ---- the schema ----


def test_a_question_needs_two_to_four_options():
    with pytest.raises(ValidationError):
        question(options=[OwnerOption(label="Only one")])
    with pytest.raises(ValidationError):
        question(options=[OwnerOption(label=f"o{i}") for i in range(5)])


@pytest.mark.parametrize("label", ["Other", " other "])
def test_no_option_is_labelled_other(label):
    with pytest.raises(ValidationError, match="Other"):
        question(options=[OwnerOption(label="Yes"), OwnerOption(label=label)])


def test_each_option_needs_its_own_label():
    with pytest.raises(ValidationError, match="own label"):
        question(options=[OwnerOption(label="Yes"), OwnerOption(label="yes")])


def test_the_default_is_one_of_the_options():
    with pytest.raises(ValidationError, match="default"):
        question(default=3)
    assert question(default=2).default == 2


def test_a_review_result_with_no_questions_still_validates():
    assert BaselineReviewResult.model_validate({}).owner_questions == []


# ---- the memory record ----


def record_of(decision: OwnerDecision, memory_id: str = "mem_9") -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        overview=owner_record_overview(decision),
        content=owner_record_content(decision),
        tags=["constraint", OWNER_QUESTION_TAG],
        scope="task::t1",
    )


def test_an_open_question_is_an_open_unknown():
    decision = OwnerDecision(question=question())
    assert owner_record_overview(decision) == "OPEN: Which rule about refunds wins?"
    content = owner_record_content(decision)
    lines = content.splitlines()
    assert lines[:5] == [
        "question: Which rule about refunds wins?",
        "default: Leave both texts as they are (No change.)",
        "gates: The prompt says to refund at once; the skill `refunds` says to ask.",
        "source: assumption",
        "re-ask when: a run that can ask the owner starts",
    ]
    assert owner_decision_from_record(record_of(decision)) == decision.model_copy(
        update={"memory_id": "mem_9"}
    )


def test_an_answer_strips_the_prefix_and_reads_back():
    decision = OwnerDecision(
        question=question(),
        status="answered",
        answer="The skill wins",
        answer_kind="option",
        answered_on="2026-07-22",
        this_run=True,
    )
    assert owner_record_overview(decision) == (
        "Which rule about refunds wins? → The skill wins"
    )
    assert "source: human" in owner_record_content(decision)
    back = owner_decision_from_record(record_of(decision))
    assert back == decision.model_copy(update={"memory_id": "mem_9", "this_run": False})


def test_an_answer_edited_in_kiln_is_read():
    """The owner removes `OPEN: ` and writes the answer after the question,
    with no `answer` line."""
    record = record_of(OwnerDecision(question=question()))
    record.overview = "Which rule about refunds wins? — The prompt wins"
    back = owner_decision_from_record(record)
    assert (back.status, back.answer, back.answer_kind) == (
        "answered",
        "The prompt wins",
        "option",
    )
    record.overview = "Which rule about refunds wins?"
    assert owner_decision_from_record(record).status == "open"


def test_a_record_with_no_question_json_is_not_an_owner_question():
    record = record_of(OwnerDecision(question=question()))
    record.content = "question: hand-written"
    assert owner_decision_from_record(record) is None


# ---- the step, with a display that asks ----


async def test_the_owner_is_asked_after_the_review_and_before_the_planner():
    ctx = reviewing_ctx()
    ctx.ask_owner = FakeOwner(OwnerReply("option", option=2))
    await run_once(ctx, question())

    ((asked, wait_secs),) = ctx.ask_owner.asked
    assert [q.question for q in asked] == ["Which rule about refunds wins?"]
    assert wait_secs == OWNER_ANSWER_WAIT_SECS
    steps = [
        f"{kind} {fields.get('name')}" if kind.startswith("phase_") else kind
        for kind, fields in ctx.log.events
    ]
    assert (
        steps.index("baseline_review")
        < steps.index("phase_start owner_questions")
        < steps.index("phase_end owner_questions")
        < steps.index("owner_questions")
        < steps.index("phase_start planning")
    )
    # The planner gets the answer next to the guidance.
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert plan_input.guidance == "keep costs low"
    (answer,) = plan_input.owner_answers
    assert (answer.question, answer.answer, answer.kind, answer.effect) == (
        "Which rule about refunds wins?",
        "The skill wins",
        "option",
        "The prompt is changed.",
    )
    assert answer.smell == "conflicting-requirements"
    assert plan_input.open_owner_questions == []
    # One record, answered in place.
    (memory,) = owner_memories(ctx.kiln)
    assert memory["scope"] == "task::t1"
    assert memory["tags"] == ["constraint", OWNER_QUESTION_TAG]
    assert memory["overview"] == "Which rule about refunds wins? → The skill wins"
    assert answer.memory_id == memory["id"]
    (event,) = events(ctx, "owner_questions")
    assert event["line"] == "Questions for the owner: 1 answered."


async def test_an_other_answer_reaches_the_planner_in_the_owners_words():
    ctx = reviewing_ctx()
    ctx.ask_owner = FakeOwner(OwnerReply("other", text="  Ask first   over 50 USD "))
    await run_once(ctx, question())
    (answer,) = ctx.dispatch.calls_to("batch_plan")[0].owner_answers
    assert (answer.answer, answer.kind, answer.effect) == (
        "Ask first over 50 USD",
        "other",
        "",
    )


async def test_a_question_left_for_later_takes_its_default():
    ctx = reviewing_ctx()
    ctx.ask_owner = FakeOwner(None, reason=LEFT_FOR_LATER)
    await run_once(ctx, question())
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert plan_input.owner_answers == []
    (opened,) = plan_input.open_owner_questions
    assert (opened.default, opened.default_effect) == (
        "Leave both texts as they are",
        "No change.",
    )
    (memory,) = owner_memories(ctx.kiln)
    assert memory["overview"].startswith("OPEN: ")
    (event,) = events(ctx, "owner_questions")
    assert event["status"].asked
    assert event["line"] == (
        "Questions for the owner: 1 with no answer (the owner left the questions"
        " for later); the run takes the default of each."
    )


async def test_a_run_asks_at_most_the_limit():
    ctx = reviewing_ctx()
    ctx.ask_owner = FakeOwner()
    many = [question(f"Question number {i} about the rules?") for i in range(7)]
    await run_once(ctx, *many)
    ((asked, _),) = ctx.ask_owner.asked
    assert len(asked) == OWNER_QUESTIONS_MAX
    assert len(owner_memories(ctx.kiln)) == OWNER_QUESTIONS_MAX
    assert len(events(ctx, "owner_question_dropped")) == 2


# ---- the step, with no display that asks ----


async def test_a_run_that_cannot_ask_records_the_question_and_goes_on():
    ctx = reviewing_ctx()
    assert ctx.ask_owner is None
    await run_once(ctx, question())
    (memory,) = owner_memories(ctx.kiln)
    assert memory["overview"] == "OPEN: Which rule about refunds wins?"
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert [q.question for q in plan_input.open_owner_questions] == [
        "Which rule about refunds wins?"
    ]
    (event,) = events(ctx, "owner_questions")
    assert not event["status"].asked
    assert event["status"].reason == NO_DISPLAY
    # The report lists it, with the default and how to answer it.
    text = report_of(ctx)
    assert text.startswith(OWNER_QUESTIONS_HEADING)
    assert "No answer. The run took the default: **Leave both texts as they" in text
    assert "remove `OPEN: ` from its overview" in text


async def test_no_question_no_event():
    ctx = reviewing_ctx()
    await run_once(ctx)
    assert events(ctx, "owner_questions") == []
    assert report_of(ctx) == ""


# ---- a rerun on the same task ----


async def test_a_rerun_asks_the_open_question_again_and_keeps_the_answer():
    first = reviewing_ctx()
    await run_once(first, question())

    second = reviewing_ctx(kiln=first.kiln)
    second.ask_owner = FakeOwner(OwnerReply("option", option=1))
    # The baseline has its smell issue, so no review runs: the open question
    # comes from memory, and it is asked.
    await run_once(second)
    assert second.dispatch.calls_to("baseline_review") == []
    ((asked, _),) = second.ask_owner.asked
    assert len(asked) == 1
    (memory,) = owner_memories(first.kiln)
    assert memory["overview"] == "Which rule about refunds wins? → The prompt wins"

    third = reviewing_ctx(kiln=first.kiln)
    third.ask_owner = FakeOwner()
    await run_once(third)
    # An answered question is never asked again; it still guides the planner.
    assert third.ask_owner.asked == []
    (answer,) = third.dispatch.calls_to("batch_plan")[0].owner_answers
    assert answer.answer == "The prompt wins"
    (event,) = events(third, "owner_questions")
    assert event["line"] == "Questions for the owner: 1 answered in an earlier run."
    assert "from the owner in an earlier run" in report_of(third)


async def test_the_reviewer_knows_the_questions_in_memory_and_none_is_doubled():
    ctx = reviewing_ctx()
    await ctx.memory.record_owner_question(question())
    ctx.ask_owner = FakeOwner()
    await run_once(ctx, question(), question("Is a short answer correct here?"))
    (review_input,) = ctx.dispatch.calls_to("baseline_review")
    (known,) = review_input.known_owner_questions
    assert (known.question, known.status) == ("Which rule about refunds wins?", "open")
    assert len(owner_memories(ctx.kiln)) == 2
    ((asked, _),) = ctx.ask_owner.asked
    assert [q.question for q in asked] == [
        "Which rule about refunds wins?",
        "Is a short answer correct here?",
    ]


async def test_a_question_after_wave_1_is_recorded_and_not_asked():
    from kiln_harness_tuner.owner_questions import settle_owner_questions
    from kiln_harness_tuner.schemas import BaselineRef, BaselineReview

    ctx = reviewing_ctx()
    ctx.ask_owner = FakeOwner()
    ctx.baseline_reviews.append(
        BaselineReview(rc_id="rc_2", status="done", owner_questions=[question()])
    )
    await settle_owner_questions(ctx, BaselineRef(rc_id="rc_2"), wave=2)
    assert ctx.ask_owner.asked == []
    (event,) = events(ctx, "owner_questions")
    assert event["status"].reason == AFTER_WAVE_1
    assert [d.status for d in ctx.owner_decisions] == ["open"]


async def test_a_memory_read_that_fails_does_not_stop_the_run(monkeypatch):
    ctx = reviewing_ctx()

    async def boom():
        raise RuntimeError("memory is down")

    monkeypatch.setattr(ctx.memory, "owner_decisions", boom)
    assert await run_once(ctx, question()) is None
    (skipped,) = events(ctx, "owner_questions_skipped")
    assert skipped["reason"] == "RuntimeError: memory is down"
    assert len(ctx.dispatch.calls_to("batch_plan")) == 1


# ---- the planner's view, the plain line, the report ----


def test_the_planner_gets_answers_and_open_questions_apart():
    answered = OwnerDecision(
        question=question("Is a short answer correct here?"),
        status="answered",
        answer="My own words",
        answer_kind="other",
    )
    opened = OwnerDecision(question=question())
    assert [a.answer for a in planner_owner_answers([answered, opened])] == [
        "My own words"
    ]
    assert [q.question for q in planner_open_questions([answered, opened])] == [
        "Which rule about refunds wins?"
    ]


def test_the_plain_display_prints_the_line():
    assert "owner_questions" in SETUP_LINE_EVENTS
    line = "Questions for the owner: 1 answered."
    assert own_line("owner_questions", {"line": line}) == line


async def test_the_report_shows_the_question_options_and_answer():
    ctx = reviewing_ctx()
    ctx.ask_owner = FakeOwner(OwnerReply("option", option=2))
    await run_once(ctx, question())
    text = report_of(ctx)
    assert "1 question(s): 1 answered in this run." in text
    assert "### 1. Which rule about refunds wins?" in text
    assert "  - Leave both texts as they are: No change. (the default)" in text
    assert "  - Other: an answer in the owner's own words" in text
    assert (
        "- Answer: **The skill wins**, from the owner in this run, 2026-07-22." in text
    )
    assert "remove `OPEN: `" not in text
