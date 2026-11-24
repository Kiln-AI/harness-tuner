"""Questions to the owner: what only the owner of the agent can decide,
such as which of two conflicting requirements wins.

`settle_owner_questions` is one step of a wave. It runs once for each
baseline, after the design review and before the planner. It never raises,
apart from `ProviderQuotaError`.

- **Who writes a question.** The baseline reviewer, from a finding
  (`BaselineReviewResult.owner_questions`). Each one offers two to four
  options, and names the conservative one as its default. The display adds
  "Other", with free text.
- **Where a question is kept.** In memory, as a task-scope `constraint`
  record with the tag `owner_question`. Its overview starts with `OPEN: `
  until the owner answers. So a later run on the task reads the answer, and
  asks only what is still open.
- **Who asks.** A run with a display that can ask (`Ctx.ask_owner`: the
  terminal app, unless `--no-interaction`) shows the open questions one
  time, before wave 1, and waits at most `OWNER_ANSWER_WAIT_SECS`. Every
  other run records each question as open and goes on. A question with no
  answer takes its default.
- **Who reads an answer.** Each planner, in `BatchPlanInput.owner_answers`,
  next to the run's guidance. An answer is the owner's policy for the
  agent. It is not eval text, and it is not a measurement.

This module holds the record's text (`owner_record_content`,
`owner_decision_from_record`), which `memory.MemoryWriter` writes and reads.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError

from kiln_harness_tuner.memory_schema import (
    OPEN_UNKNOWN_PREFIX,
    MemoryRecord,
    canonical_question,
    kv_lines,
    one_line,
    question_key,
    render_kv,
)
from kiln_harness_tuner.provider_errors import ProviderQuotaError
from kiln_harness_tuner.schemas import (
    BaselineRef,
    KnownOwnerQuestion,
    OpenOwnerQuestion,
    OwnerAnswer,
    OwnerDecision,
    OwnerQuestion,
    OwnerQuestionsStatus,
)

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

# The tag of an owner question's memory record, beside `constraint`.
OWNER_QUESTION_TAG = "owner_question"
# How many questions a run shows the owner, and how many new ones it keeps
# from one review. The reviewer's skill asks for the same number.
OWNER_QUESTIONS_MAX = 5
# How long an interactive run waits for the answers. The owner can be away,
# and the run's time limit already counts.
OWNER_ANSWER_WAIT_SECS = 10 * 60.0
# The longest "Other" answer that is kept, in characters.
OWNER_OTHER_MAX_CHARS = 500
REASON_CHARS = 300

# Why a run did not ask, or stopped waiting.
NO_DISPLAY = "this run cannot ask: it has no interactive display"
AFTER_WAVE_1 = "the question came after wave 1"
NO_ANSWER_IN_TIME = "no answer in the time allowed"
LEFT_FOR_LATER = "the owner left the questions for later"
DISPLAY_CLOSED = "the display closed"


@dataclass(frozen=True)
class OwnerReply:
    """What the owner answered to one question: one of its options, by
    index, or the owner's own text."""

    kind: Literal["option", "other"]
    option: int | None = None
    text: str = ""


@dataclass(frozen=True)
class OwnerReplies:
    """The owner's replies, one for each question asked, in order. None is a
    question with no answer. `reason` says why one or more have none."""

    replies: tuple[OwnerReply | None, ...]
    reason: str = ""

    @classmethod
    def none(cls, count: int, reason: str) -> OwnerReplies:
        return cls((None,) * count, reason)


# A display that can ask the owner: the questions and the longest wait, in
# seconds. It returns when the owner answered, left the questions, or the
# wait ended.
OwnerAsker = Callable[[Sequence[OwnerQuestion], float], Awaitable[OwnerReplies]]


# ---- the memory record ----


def owner_record_overview(decision: OwnerDecision) -> str:
    """`OPEN: <question>` with no answer, then `<question> → <answer>`. The
    memory skill's rule for a constraint: an answer strips the prefix."""
    question = canonical_question(decision.question.question)
    if decision.status == "open":
        return f"{OPEN_UNKNOWN_PREFIX}{question}"
    return f"{question} → {one_line(decision.answer)}"


def owner_record_content(decision: OwnerDecision) -> str:
    """The record's content. The first five lines are the lines of every
    open unknown (`memory_schema.OpenUnknownContent`), so an agent reads it
    as one. The `record` line is the question as one JSON line, which code
    reads back."""
    question = decision.question
    default = question.options[question.default]
    answered = decision.status == "answered"
    pairs = [
        ("question", canonical_question(question.question)),
        ("default", _with_effect(default.label, default.effect)),
        ("gates", one_line(question.why) or "-"),
        ("source", "human" if answered else "assumption"),
        (
            "re-ask when",
            "never: the owner answered"
            if answered
            else "a run that can ask the owner starts",
        ),
    ]
    if answered:
        pairs += [
            ("answer", one_line(decision.answer)),
            ("answer kind", decision.answer_kind or "other"),
            ("answered", decision.answered_on),
        ]
    if question.smell:
        pairs.append(("smell", question.smell))
    pairs.append(("record", question.model_dump_json()))
    return render_kv(pairs)


def owner_decision_from_record(record: MemoryRecord) -> OwnerDecision | None:
    """The decision that a memory record holds, or None when its `record`
    line cannot be read. A record whose overview has no `OPEN: ` prefix is
    answered: the owner can also answer in Kiln, by an edit of the record.
    The answer is then the `answer` line, or the text after `→` in the
    overview, or what the overview holds after the question. With none of
    the three, the question stays open."""
    found: dict[str, str] = {}
    for key, value in kv_lines(record.content):
        found.setdefault(key.strip().casefold(), value)
    try:
        question = OwnerQuestion.model_validate(json.loads(found.get("record", "")))
    except (ValueError, ValidationError):
        return None
    opened = OwnerDecision(question=question, memory_id=record.id)
    if record.overview.startswith(OPEN_UNKNOWN_PREFIX):
        return opened
    answer = found.get("answer") or _answer_in_overview(record.overview, question)
    if not answer:
        return opened
    labels = [option.label for option in question.options]
    kind = found.get("answer kind")
    if kind not in ("option", "other"):
        kind = "option" if answer in labels else "other"
    return opened.model_copy(
        update={
            "status": "answered",
            "answer": answer,
            "answer_kind": kind,
            "answered_on": found.get("answered", ""),
        }
    )


def _answer_in_overview(overview: str, question: OwnerQuestion) -> str:
    """The answer that an overview with no `OPEN: ` prefix states: the text
    after `→`, or what follows the question."""
    _, arrow, after = overview.rpartition("→")
    if arrow:
        return after.strip()
    asked = canonical_question(question.question)
    rest = overview[len(asked) :] if overview.startswith(asked) else ""
    return rest.strip(" -—:.")


def _with_effect(label: str, effect: str) -> str:
    return f"{one_line(label)} ({one_line(effect)})" if effect else one_line(label)


# ---- what the agents get ----


def known_owner_questions(
    decisions: Sequence[OwnerDecision],
) -> list[KnownOwnerQuestion]:
    """The owner questions of the task, for the baseline reviewer."""
    return [
        KnownOwnerQuestion(
            question=d.question.question, status=d.status, answer=d.answer
        )
        for d in decisions
    ]


def planner_owner_answers(decisions: Sequence[OwnerDecision]) -> list[OwnerAnswer]:
    """The owner's answers, for the planner: owner guidance."""
    answers = []
    for decision in decisions:
        if decision.status != "answered":
            continue
        chosen = next(
            (o for o in decision.question.options if o.label == decision.answer),
            None,
        )
        kind = decision.answer_kind or ("option" if chosen is not None else "other")
        answers.append(
            OwnerAnswer(
                question=decision.question.question,
                answer=decision.answer,
                kind=kind,
                effect=chosen.effect if chosen is not None and kind == "option" else "",
                smell=decision.question.smell,
                answered_on=decision.answered_on,
                memory_id=decision.memory_id,
            )
        )
    return answers


def planner_open_questions(
    decisions: Sequence[OwnerDecision],
) -> list[OpenOwnerQuestion]:
    """The owner questions with no answer, for the planner, each with the
    default that the run takes."""
    opened = []
    for decision in decisions:
        if decision.status != "open":
            continue
        question = decision.question
        default = question.options[question.default]
        opened.append(
            OpenOwnerQuestion(
                question=question.question,
                why=question.why,
                default=default.label,
                default_effect=default.effect,
                smell=question.smell,
                memory_id=decision.memory_id,
            )
        )
    return opened


def answered(
    decision: OwnerDecision, reply: OwnerReply, *, on: str
) -> OwnerDecision | None:
    """`decision` with the owner's reply, or None when the reply names no
    option and holds no text."""
    options = decision.question.options
    if reply.kind == "option":
        if reply.option is None or not 0 <= reply.option < len(options):
            return None
        answer = options[reply.option].label
    else:
        answer = one_line(reply.text)[:OWNER_OTHER_MAX_CHARS]
        if not answer:
            return None
    return decision.model_copy(
        update={
            "status": "answered",
            "answer": answer,
            "answer_kind": reply.kind,
            "answered_on": on,
            "this_run": True,
        }
    )


# ---- the display lines ----


def owner_questions_line(status: OwnerQuestionsStatus) -> str:
    """The step's one plain line for the displays."""
    opened = [d for d in status.decisions if d.status == "open"]
    now = [d for d in status.decisions if d.this_run]
    earlier = [d for d in status.decisions if d.status == "answered" and not d.this_run]
    parts = []
    if now:
        parts.append(f"{len(now)} answered")
    if earlier:
        parts.append(f"{len(earlier)} answered in an earlier run")
    if opened:
        why = f" ({status.reason.rstrip('.')})" if status.reason else ""
        parts.append(
            f"{len(opened)} with no answer{why}; the run takes the default of each"
        )
    return "Questions for the owner: " + "; ".join(parts) + "."


# ---- the step ----


async def settle_owner_questions(
    ctx: Ctx, baseline: BaselineRef, *, wave: int, wrap_up: bool = False
) -> None:
    """Record the reviewer's questions for the owner, ask what a display can
    ask, and keep every decision of the task for the planner
    (`ctx.owner_decisions`). Once for each baseline, as the design review.

    The owner is asked one time for each run, before wave 1. A question of a
    later review (after a promotion) is recorded as open: the owner can be
    away by then. A wrap-up wave asks nothing.

    It never raises, apart from `ProviderQuotaError`: a raise before wave 1
    would end the run with no report."""
    if ctx.owner_questions_rc_id == baseline.rc_id:
        return
    ctx.owner_questions_rc_id = baseline.rc_id
    try:
        await _settle(ctx, baseline.rc_id, wave=wave, wrap_up=wrap_up)
    except ProviderQuotaError:
        raise
    except Exception as exc:
        ctx.log.event(
            "owner_questions_skipped",
            rc=baseline.rc_id,
            reason=f"{type(exc).__name__}: {exc}"[:REASON_CHARS],
        )


async def _settle(ctx: Ctx, rc_id: str, *, wave: int, wrap_up: bool) -> None:
    decisions = await ctx.memory.owner_decisions()
    known = {question_key(d.question.question) for d in decisions}
    kept = 0
    for question in _review_questions(ctx, rc_id):
        key = question_key(question.question)
        if key in known:
            continue  # asked before: the record in memory holds it
        if kept == OWNER_QUESTIONS_MAX:
            ctx.log.event(
                "owner_question_dropped",
                rc=rc_id,
                question=question.question,
                reason=f"more than {OWNER_QUESTIONS_MAX} questions from one review",
            )
            continue
        known.add(key)
        kept += 1
        decision = OwnerDecision(question=question)
        try:
            decision.memory_id = await ctx.memory.record_owner_question(question)
        except ProviderQuotaError:
            raise
        except Exception as exc:
            # The question is still asked and still reaches the planner; a
            # later run does not find it.
            ctx.log.event(
                "owner_question_write_failed",
                rc=rc_id,
                question=question.question,
                reason=f"{type(exc).__name__}: {exc}"[:REASON_CHARS],
            )
        decisions.append(decision)
    opened = [i for i, d in enumerate(decisions) if d.status == "open"]
    asked, reason = False, ""
    if opened:
        if wrap_up:
            reason = "a wrap-up wave"
        elif wave != 1:
            reason = AFTER_WAVE_1
        elif ctx.ask_owner is None:
            reason = NO_DISPLAY
        else:
            asked = True
            reason = await _ask(ctx, decisions, opened[:OWNER_QUESTIONS_MAX])
            if not reason and len(opened) > OWNER_QUESTIONS_MAX:
                reason = f"a run asks at most {OWNER_QUESTIONS_MAX} questions"
    ctx.owner_decisions = decisions
    if not decisions:
        return
    status = OwnerQuestionsStatus(asked=asked, reason=reason, decisions=decisions)
    ctx.log.event(
        "owner_questions",
        rc=rc_id,
        wave=wave,
        status=status,
        line=owner_questions_line(status),
    )


def _review_questions(ctx: Ctx, rc_id: str) -> list[OwnerQuestion]:
    """The questions of the last `done` review of `rc_id`."""
    review = next(
        (
            r
            for r in reversed(ctx.baseline_reviews)
            if r.rc_id == rc_id and r.status == "done"
        ),
        None,
    )
    return list(review.owner_questions) if review is not None else []


async def _ask(ctx: Ctx, decisions: list[OwnerDecision], indexes: list[int]) -> str:
    """Show the open questions at `indexes` to the owner, and write each
    answer to memory and into `decisions`. Returns why one or more have no
    answer, or "" when each has one."""
    assert ctx.ask_owner is not None
    questions = [decisions[i].question for i in indexes]
    started = ctx.clock()
    ctx.log.event(
        "phase_start",
        name="owner_questions",
        label=f"Waiting for the owner's answers ({len(questions)} question(s))…",
    )
    try:
        got = await ctx.ask_owner(questions, OWNER_ANSWER_WAIT_SECS)
    finally:
        ctx.log.event(
            "phase_end", name="owner_questions", elapsed_secs=ctx.clock() - started
        )
    replies = list(got.replies)[: len(indexes)]
    replies += [None] * (len(indexes) - len(replies))
    today = ctx.memory.today()
    missing = 0
    for index, reply in zip(indexes, replies, strict=True):
        decision = answered(decisions[index], reply, on=today) if reply else None
        if decision is None:
            missing += 1
            continue
        try:
            decision.memory_id = await ctx.memory.record_owner_answer(decision)
        except ProviderQuotaError:
            raise
        except Exception as exc:
            # The answer still holds for this run; a later run asks again.
            ctx.log.event(
                "owner_answer_write_failed",
                question=decision.question.question,
                reason=f"{type(exc).__name__}: {exc}"[:REASON_CHARS],
            )
        decisions[index] = decision
    if not missing:
        return ""
    return got.reason or LEFT_FOR_LATER
