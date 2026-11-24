"""The smell detectors that read the baseline's stored train runs
(`large_tool_result`, `repeated_call`, `run_near_limit`, `unused_tool`,
`unused_skill`), the harness detector `small_skill`, and the trace counts
they read: each one at and around its reference."""

from __future__ import annotations

import importlib.util
import re
from dataclasses import replace
from pathlib import Path

from conftest import make_ctx
from fake_kiln import make_eval, make_run
from test_smells import JUDGEMENT_WORDS, baseline_row, events
from trace_detector_cases import (
    FIRING,
    given,
    plain_runs,
    run,
    same_call,
    skill,
    tool,
    trace,
)

from kiln_harness_tuner.gateway.trace_files import trace_stats
from kiln_harness_tuner.prompt_text import SkillText
from kiln_harness_tuner.schemas import Detection
from kiln_harness_tuner.smells import (
    DETECTORS,
    Detector,
    DetectorInputs,
    run_detectors,
)
from kiln_harness_tuner.smells.detectors.run_near_limit import share_of
from kiln_harness_tuner.smells.detectors.small_skill import skill_body
from kiln_harness_tuner.smells.thresholds import (
    KILN_TOOL_STEPS_PER_TURN,
    LARGE_TOOL_RESULT_CHARS,
    LARGE_TOOL_RESULT_CHARS_SOURCE,
    NEAR_LIMIT_PERCENT,
    REPEATED_CALL_COUNT,
    SMALL_SKILL_BODY_CHARS,
    UNUSED_MIN_RUNS,
    UNUSED_MIN_RUNS_SOURCE,
)
from kiln_harness_tuner.smells.traces import item_count, listed

BY_ID = {d.id: d for d in DETECTORS}
TRACE_DETECTORS = (
    "large_tool_result",
    "repeated_call",
    "run_near_limit",
    "unused_tool",
    "unused_skill",
)


def detect(detector_id: str, inputs: DetectorInputs) -> list[Detection]:
    return BY_ID[detector_id].run(inputs)


# ---- the trace counts that the detectors read ----


def test_trace_stats_counts_the_same_call_by_its_arguments():
    steps = [
        ("lookup", {"id": 7, "full": True}, "row"),
        ("lookup", {"full": True, "id": 7}, "row"),  # the same call: key order
        ("lookup", {"id": 8}, "row"),
        ("search", {"q": "a"}, "none"),
    ]
    stats = trace_stats(trace(*steps))
    # `search` was made one time, so it is not listed.
    assert stats["same_call_max"] == {"lookup": 2}
    assert stats["max_tool_steps_in_turn"] == 4


def test_trace_stats_reads_arguments_that_are_not_json():
    messages = trace(("lookup", {}, "row"), ("lookup", {}, "row"))
    for message in messages:
        for call in message.get("tool_calls") or []:
            call["function"]["arguments"] = " id=7 "
    assert trace_stats(messages)["same_call_max"] == {"lookup": 2}


def test_trace_stats_counts_the_tool_steps_of_each_turn_apart():
    first = trace(*[("step", {"n": n}, "ok") for n in range(3)])
    second = trace(*[("step", {"n": n}, "ok") for n in range(2)])
    # The second conversation turn starts at its user message.
    stats = trace_stats([*first, *second[1:]])
    assert stats["tool_calls"] == 5
    assert stats["max_tool_steps_in_turn"] == 3


def test_trace_stats_counts_parallel_calls_as_one_step():
    # Kiln counts a model call that asks for tools, not each tool call.
    messages = trace(("a", {}, "ok"), ("b", {}, "ok"))
    first, second = messages[2], messages[4]
    first["tool_calls"] += second["tool_calls"]
    del messages[4]
    stats = trace_stats(messages)
    assert stats["tool_calls"] == 2
    assert stats["max_tool_steps_in_turn"] == 1


def test_trace_stats_reads_the_answer_tool_as_kiln_does():
    # Kiln does not count a step that calls `task_response`, and the same
    # answer in each turn of a conversation is not a loop.
    answer = ("task_response", {"label": "ok"}, "")
    one = trace(("lookup", {"id": 1}, "row"), answer)
    stats = trace_stats([*one, *one[1:], *one[1:], *one[1:], *one[1:]])
    assert stats["max_tool_steps_in_turn"] == 1
    assert stats["same_call_max"] == {"lookup": 5}
    assert "task_response" in stats["tools"]


def test_trace_stats_leaves_a_call_with_no_name_out_of_the_same_calls():
    messages = trace(*[("x", {}, "ok")] * 6)
    for message in messages:
        for call in message.get("tool_calls") or []:
            del call["function"]["name"]
    stats = trace_stats(messages)
    assert stats["same_call_max"] == {}
    assert stats["max_tool_steps_in_turn"] == 6
    assert detect("repeated_call", given([{"item_id": "i", **stats}])) == []


def test_run_near_limit_is_silent_when_parallel_calls_keep_the_steps_low():
    calls = [
        {"id": f"c{n}", "function": {"name": "step", "arguments": f'{{"n": {n}}}'}}
        for n in range(KILN_TOOL_STEPS_PER_TURN * 2)
    ]
    messages = [
        {"role": "user", "content": "A request."},
        {"role": "assistant", "content": None, "tool_calls": calls},
        *({"role": "tool", "tool_call_id": c["id"], "content": "ok"} for c in calls),
        {"role": "assistant", "content": "Done."},
    ]
    stats = {"eval_id": "e", "item_id": "i", **trace_stats(messages)}
    assert detect("run_near_limit", given([stats])) == []


def test_trace_stats_counts_the_skills_that_a_run_opens():
    steps = [
        ("skill", {"name": "returns"}, "body"),
        ("skill", {"name": "returns", "resource": "references/a.md"}, "text"),
        ("skill", {"name": ""}, "error"),
        ("skill", {}, "error"),
        ("lookup", {"name": "not a skill"}, "row"),
    ]
    assert trace_stats(trace(*steps))["skills_opened"] == {"returns": 2}
    assert trace_stats(trace())["skills_opened"] == {}


# ---- every detector that reads traces ----


def test_each_trace_detector_reads_traces_and_reports_on_its_case():
    for detector_id in TRACE_DETECTORS:
        assert BY_ID[detector_id].reads_traces, detector_id
        found = detect(detector_id, FIRING[detector_id])
        assert found, detector_id
        assert {d.scope for d in found} == {"train"}
        assert all(d.detector == detector_id for d in found)
        assert all(d.smell == BY_ID[detector_id].smell for d in found)
        assert all(d.reference_source for d in found)
    assert not BY_ID["small_skill"].reads_traces
    assert {d.id for d in DETECTORS if d.reads_traces} == set(TRACE_DETECTORS)


def test_each_trace_detector_is_silent_with_no_stored_run():
    for detector_id in TRACE_DETECTORS:
        without = replace(FIRING[detector_id], trace_stats=[])
        assert detect(detector_id, without) == [], detector_id


def test_a_fact_of_these_detectors_has_no_judgement_word():
    for detector_id, inputs in FIRING.items():
        for detection in detect(detector_id, inputs):
            words = set(re.findall(r"[a-z]+", detection.fact.lower()))
            assert not words & JUDGEMENT_WORDS, (detector_id, detection.fact)


def test_a_fact_fits_its_limit_with_the_longest_names():
    # A `Detection` refuses a fact over 300 characters, so each detector
    # must return here. The names and ids are longer than Kiln allows.
    name, item, eval_id = "n" * 90, "i" * 60, "e" * 60
    big = "r" * (LARGE_TOOL_RESULT_CHARS + 1_000_000)
    runs = [
        run(item, (name, {}, big), *[(name, {"id": 1}, "x")] * 99, eval_id=eval_id)
    ] * 1_200
    tools = [tool(f"{index:02d}{name}") for index in range(60)]
    skills = [skill(f"{index:02d}{name}") for index in range(60)]
    inputs = given(runs + plain_runs(UNUSED_MIN_RUNS), tools, skills, window=2_000_000)
    inputs.trace_stats[0]["turn_usage"] = [{"input_tokens": 1_999_999}]
    for detector_id in FIRING:
        found = detect(detector_id, inputs)
        assert found, detector_id
        assert all(len(d.fact) <= 300 for d in found)


def test_a_fact_fits_its_limit_with_the_largest_numbers():
    # The ids at their cut, and counts far past any real run: a fact must
    # still fit, or the detector loses every detection of its call.
    item, eval_id = "i" * 60, "e" * 60
    many = 10**9
    run_stats = {
        "item_id": item,
        "eval_id": eval_id,
        "tools": {"n" * 90: {"max_result_chars": many}},
        "same_call_max": {"n" * 90: many},
        "max_tool_steps_in_turn": many,
        "turn_usage": [{"input_tokens": many}],
    }
    inputs = given([run_stats] * 100_000, window=many)
    for detector_id in ("large_tool_result", "repeated_call", "run_near_limit"):
        found = detect(detector_id, inputs)
        assert found, detector_id
        assert all(len(d.fact) <= 300 for d in found), detector_id


def test_listed_counts_the_names_that_do_not_fit():
    assert listed(["a", "b"], 50) == "`a`, `b`"
    assert listed(["a" * 20, "b" * 20, "c" * 20], 40) == f"`{'a' * 20}` and 2 more"
    # The first name is always shown, cut at the length of a tool name.
    assert listed(["a" * 200], 10) == f"`{'a' * 64}`"
    assert listed([], 10) == ""


def test_item_count_counts_each_train_item_one_time():
    runs = [
        {"item_id": "t1", "eval_id": "e1"},
        {"item_id": "t1", "eval_id": "e2"},
        {"item_id": "t2", "eval_id": "e1"},
        {"item_id": None},
    ]
    assert item_count(runs) == 3


# ---- large_tool_result ----


def big_result(chars: int, item: str = "item_1") -> dict:
    return run(item, ("list_orders", {}, "r" * chars))


def test_large_tool_result_is_silent_at_the_reference():
    runs = [big_result(LARGE_TOOL_RESULT_CHARS)]
    assert detect("large_tool_result", given(runs)) == []


def test_large_tool_result_reports_above_the_reference():
    runs = [big_result(LARGE_TOOL_RESULT_CHARS + 1), big_result(10, "item_2")]
    (found,) = detect("large_tool_result", given(runs))
    assert found.smell == "tool-results-flood-the-context"
    assert found.value == LARGE_TOOL_RESULT_CHARS + 1
    assert found.reference == LARGE_TOOL_RESULT_CHARS
    assert found.reference_source == LARGE_TOOL_RESULT_CHARS_SOURCE
    assert found.fact == (
        f"The tool `list_orders` returned {LARGE_TOOL_RESULT_CHARS + 1:,}"
        " characters in one result (train item `item_1`, eval `eval_1`). 1 of 2"
        " stored train runs have a result of it over the reference. The"
        f" reference is {LARGE_TOOL_RESULT_CHARS:,}."
    )


def test_large_tool_result_gives_one_detection_for_each_tool_largest_first():
    over = LARGE_TOOL_RESULT_CHARS + 1
    runs = [
        run("item_1", ("a", {}, "r" * over), ("b", {}, "r" * (over + 5))),
        run("item_2", ("a", {}, "r" * (over + 9)), ("c", {}, "short")),
    ]
    found = detect("large_tool_result", given(runs))
    assert [(d.fact.split("`")[1], d.value) for d in found] == [
        ("a", over + 9),
        ("b", over + 5),
    ]
    assert "train item `item_2`" in found[0].fact
    assert "2 of 2 stored train runs" in found[0].fact


def test_large_tool_result_skips_a_result_with_no_known_call():
    messages = trace(("list_orders", {}, "r" * (LARGE_TOOL_RESULT_CHARS + 1)))
    for message in messages:
        if message["role"] == "tool":
            message["tool_call_id"] = "no_such_call"
    stats = {"eval_id": "eval_1", "item_id": "item_1", **trace_stats(messages)}
    assert detect("large_tool_result", given([stats])) == []


# ---- repeated_call ----


def test_repeated_call_is_silent_under_the_reference():
    runs = [run("item_1", *same_call(REPEATED_CALL_COUNT - 1))]
    assert detect("repeated_call", given(runs)) == []


def test_repeated_call_reports_at_the_reference():
    runs = [
        run("item_1", *same_call(REPEATED_CALL_COUNT)),
        run("item_2", *same_call(REPEATED_CALL_COUNT + 2)),
        run("item_3", ("lookup", {"id": 7}, "row 7")),
    ]
    (found,) = detect("repeated_call", given(runs))
    assert found.smell == "repeated-identical-calls"
    assert found.value == REPEATED_CALL_COUNT + 2
    assert found.reference == REPEATED_CALL_COUNT
    assert found.fact == (
        f"One run called the tool `lookup` {REPEATED_CALL_COUNT + 2} times with"
        " the same arguments (train item `item_2`, eval `eval_1`). 2 of 3 stored"
        f" train runs have {REPEATED_CALL_COUNT} or more such calls. The"
        f" reference is {REPEATED_CALL_COUNT}."
    )


def test_repeated_call_counts_calls_that_are_not_in_a_row():
    cycle = [("lookup", {"id": 7}, "row"), ("check", {"id": 7}, "ok")]
    runs = [run("item_1", *cycle * REPEATED_CALL_COUNT)]
    found = detect("repeated_call", given(runs))
    assert sorted(d.fact.split("`")[1] for d in found) == ["check", "lookup"]


def test_repeated_call_does_not_count_calls_with_other_arguments():
    steps = [("lookup", {"id": n}, "row") for n in range(REPEATED_CALL_COUNT * 4)]
    assert detect("repeated_call", given([run("item_1", *steps)])) == []


# ---- run_near_limit ----


def many_calls(count: int, item: str = "item_1", **kwargs) -> dict:
    return run(item, *[("step", {"n": n}, "ok") for n in range(count)], **kwargs)


def test_the_reference_share_is_rounded_up():
    assert NEAR_LIMIT_PERCENT == 80
    assert share_of(100) == 80
    assert share_of(101) == 81
    assert share_of(200_000) == 160_000


def test_run_near_limit_is_silent_under_the_share_of_the_tool_call_limit():
    under = share_of(KILN_TOOL_STEPS_PER_TURN) - 1
    assert detect("run_near_limit", given([many_calls(under)])) == []


def test_run_near_limit_reports_at_the_share_of_the_tool_call_limit():
    reference = share_of(KILN_TOOL_STEPS_PER_TURN)
    runs = [many_calls(reference), many_calls(reference + 3, "item_2"), many_calls(2)]
    (found,) = detect("run_near_limit", given(runs))
    assert found.smell == "runs-close-to-a-limit"
    assert found.value == reference + 3
    assert found.reference == reference
    assert "MAX_TOOL_CALLS_PER_TURN" in found.reference_source
    assert found.fact == (
        f"One turn of a run has {reference + 3} tool steps (train item `item_2`,"
        f" eval `eval_1`). Kiln stops a turn at {KILN_TOOL_STEPS_PER_TURN} tool"
        f" steps. 2 of 3 stored train runs have a turn with {reference} or more."
        f" The reference is {NEAR_LIMIT_PERCENT}% of the limit."
    )


def test_run_near_limit_counts_the_tool_steps_of_one_turn():
    # Two conversation turns, each under the reference: the total is over it.
    half = share_of(KILN_TOOL_STEPS_PER_TURN) // 2 + 1
    one = trace(*[("step", {"n": n}, "ok") for n in range(half)])
    stats = {"eval_id": "e", "item_id": "i", **trace_stats([*one, *one[1:]])}
    assert stats["tool_calls"] >= share_of(KILN_TOOL_STEPS_PER_TURN)
    assert detect("run_near_limit", given([stats])) == []


def test_run_near_limit_is_silent_under_the_share_of_the_context_window():
    runs = [many_calls(1, input_tokens=share_of(1_000) - 1)]
    assert detect("run_near_limit", given(runs, window=1_000)) == []


def test_run_near_limit_reports_at_the_share_of_the_context_window():
    runs = [
        many_calls(1, input_tokens=share_of(1_000)),
        many_calls(1, "item_2", input_tokens=10),
        many_calls(1, "item_3"),  # no usage for each call: not counted
    ]
    (found,) = detect("run_near_limit", given(runs, window=1_000))
    assert found.value == 800
    assert found.reference == 800
    assert "model catalogue" in found.reference_source
    assert found.fact == (
        "One model call read 800 input tokens (train item `item_1`, eval"
        " `eval_1`). The context window of the model is 1,000 tokens. 1 of 2"
        " stored train runs with usage for each call reach 800. The reference is"
        " 80% of the window."
    )


def test_run_near_limit_does_not_read_the_window_when_it_is_not_known():
    runs = [many_calls(1, input_tokens=5_000_000)]
    assert detect("run_near_limit", given(runs, window=None)) == []
    assert detect("run_near_limit", replace(given(runs), model=None)) == []


def test_run_near_limit_can_report_on_both_limits():
    runs = [many_calls(KILN_TOOL_STEPS_PER_TURN - 1, input_tokens=999)]
    found = detect("run_near_limit", given(runs, window=1_000))
    assert [d.fact.split(" ")[1] for d in found] == ["turn", "model"]


def test_the_tool_call_limit_is_the_one_of_the_installed_kiln():
    # The constant is read from the file, with no import of the adapter.
    spec = importlib.util.find_spec("kiln_ai")
    assert spec is not None and spec.submodule_search_locations
    adapter = Path(spec.submodule_search_locations[0]).joinpath(
        "adapters", "model_adapters", "litellm_adapter.py"
    )
    match = re.search(r"^MAX_TOOL_CALLS_PER_TURN = (\d+)$", adapter.read_text(), re.M)
    assert match is not None
    assert int(match.group(1)) == KILN_TOOL_STEPS_PER_TURN


# ---- unused_tool ----

TOOLS = [tool("lookup"), tool("delete_all"), tool("export")]


def test_unused_tool_is_silent_under_the_reference_count_of_items():
    runs = plain_runs(UNUSED_MIN_RUNS - 1, ("lookup", {}, "row"))
    assert detect("unused_tool", given(runs, TOOLS)) == []


def test_unused_tool_reports_at_the_reference_count_of_items():
    runs = plain_runs(UNUSED_MIN_RUNS, ("lookup", {}, "row"))
    (found,) = detect("unused_tool", given(runs, TOOLS))
    assert found.smell == "unnecessary-tools"
    assert found.value == UNUSED_MIN_RUNS
    assert found.reference == UNUSED_MIN_RUNS
    assert found.reference_source == UNUSED_MIN_RUNS_SOURCE
    assert found.fact == (
        "2 of 3 attached tools are called in no stored train run, over"
        f" {UNUSED_MIN_RUNS} train items: `delete_all`, `export`. The reference"
        f" is {UNUSED_MIN_RUNS} items or more."
    )


def test_unused_tool_counts_train_items_not_stored_runs():
    # One item with a stored run on many evals is one item.
    runs = [
        run("item_1", ("lookup", {}, "row"), eval_id=f"eval_{n}")
        for n in range(UNUSED_MIN_RUNS * 2)
    ]
    assert detect("unused_tool", given(runs, TOOLS)) == []


def test_unused_tool_is_silent_when_one_run_calls_each_tool():
    runs = plain_runs(UNUSED_MIN_RUNS, ("lookup", {}, "row"))
    runs.append(run("last", ("delete_all", {}, "ok"), ("export", {}, "ok")))
    assert detect("unused_tool", given(runs, TOOLS)) == []


def test_unused_tool_reads_only_the_tools_with_a_definition():
    runs = plain_runs(UNUSED_MIN_RUNS, ("lookup", {}, "row"))
    assert detect("unused_tool", given(runs, [tool("lookup")])) == []
    assert detect("unused_tool", given(runs, [])) == []
    unnamed = [{"tool_id": "mcp::x", "name": None, "description": None}]
    assert detect("unused_tool", given(runs, unnamed)) == []


# ---- unused_skill ----

SKILLS = [skill("returns"), skill("shipping")]
OPENS = ("skill", {"name": "returns"}, "body")


def test_unused_skill_is_silent_under_the_reference_count_of_items():
    runs = plain_runs(UNUSED_MIN_RUNS - 1, OPENS)
    assert detect("unused_skill", given(runs, skills=SKILLS)) == []


def test_unused_skill_reports_at_the_reference_count_of_items():
    runs = plain_runs(UNUSED_MIN_RUNS, OPENS)
    (found,) = detect("unused_skill", given(runs, skills=SKILLS))
    assert found.smell == "skill-never-opened"
    assert found.value == UNUSED_MIN_RUNS
    assert found.reference == UNUSED_MIN_RUNS
    assert found.fact == (
        "1 of 2 attached skills are opened in no stored train run, over"
        f" {UNUSED_MIN_RUNS} train items: `shipping`. The reference is"
        f" {UNUSED_MIN_RUNS} items or more."
    )


def test_unused_skill_is_silent_when_each_skill_is_opened_or_none_is_read():
    runs = plain_runs(UNUSED_MIN_RUNS, OPENS)
    runs.append(run("last", ("skill", {"name": "shipping"}, "body")))
    assert detect("unused_skill", given(runs, skills=SKILLS)) == []
    assert detect("unused_skill", given(runs, skills=[])) == []
    unread = given(runs)
    unread = replace(unread, texts=replace(unread.texts, skills=None))
    assert detect("unused_skill", unread) == []


# ---- small_skill ----


def test_skill_body_is_the_text_after_the_front_matter():
    assert skill_body("---\nname: a\ndescription: d\n---\n\n# A\n\nText.\n") == (
        "# A\n\nText."
    )
    assert skill_body("no front matter\n") == "no front matter"
    assert skill_body("---\nname: never closed\n") == "---\nname: never closed"
    assert skill_body("") == ""
    assert skill_body("\ufeff---\nname: a\n---\nText.") == "Text."


def test_small_skill_is_silent_at_the_reference():
    at = skill("tone", "b" * SMALL_SKILL_BODY_CHARS)
    assert detect("small_skill", given(skills=[at])) == []


def test_small_skill_reports_under_the_reference():
    under = skill("tone", "b" * (SMALL_SKILL_BODY_CHARS - 1))
    (found,) = detect("small_skill", given(skills=[under, skill("long", "b" * 5_000)]))
    assert found.smell == "skill-too-small-to-earn-its-place"
    assert found.scope == "harness"
    assert found.value == SMALL_SKILL_BODY_CHARS - 1
    assert found.reference == SMALL_SKILL_BODY_CHARS
    assert found.fact == (
        f"The body of the skill `tone` has {SMALL_SKILL_BODY_CHARS - 1}"
        " characters, and the skill has no other file. The reference is"
        f" {SMALL_SKILL_BODY_CHARS}."
    )


def test_small_skill_is_silent_for_a_skill_with_another_file():
    routed = skill("guide", "See the file.", {"references/a.md": "text"})
    assert detect("small_skill", given(skills=[routed])) == []
    no_skill_md = SkillText(skill_id="id_x", name="x", files={})
    assert detect("small_skill", given(skills=[no_skill_md])) == []


# ---- run_detectors: the stored train runs of the baseline ----


def traced_ctx(rows: list[dict]):
    ctx = make_ctx()
    ctx.kiln.run_configs = [baseline_row("short")]
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    ctx.kiln.specs = [{"id": "spec_1", "eval_id": "eval_1", "status": "active"}]
    ctx.kiln.results[("eval_1", "rc_base", "train")] = rows
    return ctx


def stored_row(item: str, *steps) -> dict:
    row = make_run(item, accuracy=1.0)
    row["task_run_trace"] = trace(*steps)
    return row


async def test_run_detectors_reads_the_stored_train_runs_of_the_baseline():
    big = ("list_orders", {}, "r" * (LARGE_TOOL_RESULT_CHARS + 1))
    ctx = traced_ctx([stored_row("t1", big), stored_row("t2", *same_call(2))])
    # A val row of the baseline with a larger result: it is never read.
    ctx.kiln.results[("eval_1", "rc_base", "val")] = [
        stored_row("v1", ("list_orders", {}, "r" * (LARGE_TOOL_RESULT_CHARS * 2)))
    ]
    (found,) = await run_detectors(ctx, "rc_base")
    assert found.detector == "large_tool_result"
    assert found.scope == "train"
    assert found.value == LARGE_TOOL_RESULT_CHARS + 1
    assert "train item `t1`, eval `eval_1`" in found.fact
    assert "1 of 2 stored train runs" in found.fact
    reads = [c for c in ctx.kiln.calls if c[1].endswith("/results")]
    assert reads and all(c[2] == {"split": "train"} for c in reads)
    # No drive and no judge call: the detectors read what is stored.
    assert not [c for c in ctx.kiln.calls if c[0] == "POST"]
    (event,) = events(ctx, "smell_detections")
    assert event["count"] == 1


async def test_run_detectors_is_silent_on_traces_when_no_train_run_is_stored():
    small = skill("tone", "A few words.")
    ctx = traced_ctx([])
    ctx.kiln.run_configs = [
        baseline_row("short", ("kiln_tool::skill::id_tone",)),
    ]

    seen: list[DetectorInputs] = []

    def record(inputs: DetectorInputs) -> list[Detection]:
        seen.append(inputs)
        return []

    found = await run_detectors(
        ctx, "rc_base", detectors=(*DETECTORS, Detector("probe", "x", record))
    )
    assert found == []
    assert seen[0].trace_stats == []
    # With the skill's text given, only the harness detector reports.
    with_skill = replace(seen[0], texts=replace(seen[0].texts, skills={"s": small}))
    found = [d for detector in DETECTORS for d in detector.run(with_skill)]
    assert [d.detector for d in found] == ["small_skill"]
