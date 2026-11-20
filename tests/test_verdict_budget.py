"""The analyzer's content room and the verdict clamp: code adds lines after
the analyzer's `memory_content`, the analyzer is told the room they leave
(`AnalysisInput.content_budget`), the same session is asked to shorten an
over-budget content, and the verdict clamp keeps the lines code reads back
and `next:`."""

from __future__ import annotations

import json

from fake_kiln import FakeKiln
from test_dispatch_claude_cli import (
    cli_payload,
    envelope_text,
    make_dispatcher,
    ok_proc,
)
from test_memory import (
    BASELINE,
    _view,
    fix_item,
    make_verdict_result,
    make_writer,
)

from kiln_harness_tuner.dispatch.claude_cli import SHORTEN_TURNS
from kiln_harness_tuner.memory import verdict_content, verdict_room
from kiln_harness_tuner.memory_schema import (
    CONTENT_MAX_CHARS,
    TRUNCATION_MARKER,
    ExperimentState,
)
from kiln_harness_tuner.schemas import (
    AnalysisBundle,
    AnalysisInput,
    EvalTarget,
    ExperimentItem,
    FixWork,
    RcChange,
)

FINDING = "the build names a tool it does not attach " + "x" * 340
CODE_LINES = [
    "measurement: parent, 3 evals, 40 items",
    "latency (train, paired, n=40): median 900→850 ms — ok",
    "cost (train, paired, n=40): median $0.0010→$0.0009 per request — ok",
]


async def _running_record(kiln: FakeKiln) -> str:
    """An open record with an rc and three check findings: about 1500 chars
    of carried lines."""
    writer = make_writer(kiln)
    mem_id = await writer.open_experiment(fix_item(), wave=1, baseline=BASELINE)
    await writer.record_rc_built(
        mem_id, "rc_cand", RcChange(summary="c" * 280, changes=["x"])
    )
    await writer.record_generality(mem_id, FINDING)
    await writer.record_judge_copy(mem_id, FINDING)
    await writer.record_reference_check(mem_id, FINDING)
    return mem_id


def _analyzer_content(length: int) -> str:
    """An analyzer content of about `length` chars that re-cites no carried
    line: an opening, a table, prose, and `next:` last."""
    opening = ["hypothesis: retry-skill for 429s", "eval: eval_1"]
    table = [f"| eval_1 | row {i} | delta=+0.0{i} n=40 |" for i in range(6)]
    tail = "next: rerun with n=80 against rc_cand"
    fixed = sum(len(line) + 1 for line in [*opening, *table, tail])
    prose = []
    while fixed + sum(len(p) + 1 for p in prose) < length:
        prose.append(f"interpretation {len(prose)}: " + "y" * 90)
    return "\n".join([*opening, *table, *prose, tail])


async def test_an_over_cap_verdict_keeps_the_ids_the_table_and_next():
    # The review's case A: 2600 chars of analyzer content plus the code lines
    # and three check findings. The old clamp kept a 1200-char tail, and the
    # record read back with no rc, issue or technique.
    kiln = FakeKiln()
    mem_id = await _running_record(kiln)
    content = _analyzer_content(2600) + "\n" + "\n".join(CODE_LINES)
    await make_writer(kiln).record_verdict(mem_id, make_verdict_result(content))
    stored = kiln.memories[mem_id]["content"]
    assert len(stored) <= CONTENT_MAX_CHARS
    assert TRUNCATION_MARKER in stored
    view = _view(kiln.memories[mem_id])
    assert view.state is ExperimentState.VERDICT
    assert view.rc_id == "rc_cand"
    assert view.issue_id == "issue_1"
    assert view.technique == "prompt"
    assert view.generality and view.judge_copy and view.reference_check
    assert "next: rerun with n=80 against rc_cand" in stored
    for line in CODE_LINES:
        assert line in stored
    # The prose goes first: the opening and the whole table stay.
    for i in range(6):
        assert f"| eval_1 | row {i} |" in stored
    assert "interpretation 0:" in stored


async def test_a_content_within_the_room_is_stored_whole():
    kiln = FakeKiln()
    mem_id = await _running_record(kiln)
    writer = make_writer(kiln)
    room = await writer.analyzer_room(mem_id, CODE_LINES)
    assert 1500 < room < CONTENT_MAX_CHARS - 1000
    analyzer = _analyzer_content(room - 60)
    assert len(analyzer) <= room
    content = analyzer + "\n" + "\n".join(CODE_LINES)
    await writer.record_verdict(mem_id, make_verdict_result(content))
    stored = kiln.memories[mem_id]["content"]
    assert TRUNCATION_MARKER not in stored
    assert stored.startswith(analyzer)


async def test_the_room_counts_every_carried_line():
    kiln = FakeKiln()
    mem_id = await _running_record(kiln)
    view = _view(kiln.memories[mem_id])
    room = verdict_room(view, CODE_LINES)
    composed = verdict_content(view, "\n".join(CODE_LINES))
    assert room == CONTENT_MAX_CHARS - len(composed) - 1
    for line in ("rc: rc_cand", "issue: issue_1", "technique: prompt"):
        assert line in composed


async def test_the_input_default_is_the_whole_cap():
    item = _item()
    req = AnalysisInput(
        item=item,
        rc_id="rc_1",
        bundle=AnalysisBundle(per_eval=[], spend_total_usd=0.0, errors=[]),
        experiment_memory_id="mem_1",
    )
    assert req.content_budget == CONTENT_MAX_CHARS


# --- the shorten turns ---


def _item() -> ExperimentItem:
    return ExperimentItem(
        id="w1-1",
        type="experiment",
        evaluation_target=[EvalTarget(eval_id="eval_1")],
        promotion_candidate=True,
        work=FixWork(
            issue_memory_id="mem_61",
            hypothesis="h",
            technique="skill",
            predicted_effect="e",
            needs_eval=False,
            eval_id="eval_1",
        ),
    )


def _result(content: str, successful: bool = True) -> dict:
    return {
        "successful": successful,
        "findings": "f",
        "memory_overview": "EXP 0722-w1-1: x — +18%, n=34, p=.04",
        "memory_content": content,
    }


def _input(budget: int) -> AnalysisInput:
    return AnalysisInput(
        item=_item(),
        rc_id="rc_300",
        bundle=AnalysisBundle(per_eval=[], spend_total_usd=1.8, errors=[]),
        experiment_memory_id="mem_400",
        content_budget=budget,
    )


def _reply(data: dict, session_id: str = "sess-9") -> object:
    return ok_proc(cli_payload(envelope_text(data), session_id=session_id))


async def test_an_over_budget_content_is_shortened_in_the_same_session():
    dispatcher, runner = make_dispatcher(
        [
            _reply(_result("a" * 1200)),
            # The shorten reply flips `successful`: only its content is taken.
            _reply(_result("b" * 900, successful=False)),
        ]
    )
    result = await dispatcher.analyze_experiment_results(_input(1000))
    assert result.status == "ok"
    assert result.data.memory_content == "b" * 900
    assert result.data.successful is True
    assert len(runner.calls) == 2
    shorten = runner.calls[1]
    assert shorten.argv[shorten.argv.index("--resume") + 1] == "sess-9"
    assert "Your memory_content is 1200 chars" in shorten.stdin_text
    assert "budget is 1000 chars" in shorten.stdin_text


async def test_a_content_within_budget_takes_no_shorten_turn():
    dispatcher, runner = make_dispatcher([_reply(_result("a" * 1000))])
    result = await dispatcher.analyze_experiment_results(_input(1000))
    assert result.data.memory_content == "a" * 1000
    assert len(runner.calls) == 1


async def test_the_shorten_turns_stop_at_their_limit():
    replies = [_reply(_result("a" * 1300))] + [
        _reply(_result("a" * (1200 - turn))) for turn in range(SHORTEN_TURNS)
    ]
    dispatcher, runner = make_dispatcher(replies)
    result = await dispatcher.analyze_experiment_results(_input(1000))
    assert len(runner.calls) == 1 + SHORTEN_TURNS
    # Still over: the shortest content goes on, and code clamps it.
    assert result.data.memory_content == "a" * (1200 - SHORTEN_TURNS + 1)


async def test_a_bad_shorten_reply_keeps_the_first_content():
    dispatcher, runner = make_dispatcher(
        [
            _reply(_result("a" * 1200)),
            ok_proc(cli_payload("I could not do it {not json", session_id="sess-9")),
        ]
    )
    result = await dispatcher.analyze_experiment_results(_input(1000))
    assert result.status == "ok"
    assert result.data.memory_content == "a" * 1200
    assert len(runner.calls) == 2


async def test_a_longer_shorten_reply_is_not_taken():
    dispatcher, _ = make_dispatcher(
        [
            _reply(_result("a" * 1200)),
            _reply(_result("c" * 1500)),
            _reply(_result("d" * 1100)),
        ]
    )
    result = await dispatcher.analyze_experiment_results(_input(1000))
    assert result.data.memory_content == "d" * 1100


def test_the_shorten_prompt_is_json_safe():
    # The prompt asks for the envelope; its example must be valid JSON once
    # the placeholder is filled.
    from kiln_harness_tuner.dispatch.claude_cli import shorten_prompt

    text = shorten_prompt(1200, 1000)
    example = text[text.index("{") : text.rindex("}") + 1].replace(
        "<the whole output object>", "{}"
    )
    assert json.loads(example) == {"status": "ok", "data": {}, "detail": None}
