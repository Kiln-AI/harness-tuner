"""The smell detectors in code (`kiln_harness_tuner.smells`): each detector
on each side of its threshold, the run step and its events, the planner
input, the tracker and the report's smells appendix."""

from __future__ import annotations

import ast
import inspect
import re

import pytest
from conftest import make_ctx, make_plan, make_tracker
from fake_dispatcher import ok
from fake_kiln import make_eval, make_run
from test_orchestrator import seed_baseline

from kiln_harness_tuner.orchestrator import orchestrate
from kiln_harness_tuner.prompt_text import RcText, SkillText
from kiln_harness_tuner.report import render_smells_appendix
from kiln_harness_tuner.schemas import BaselineRef, Detection
from kiln_harness_tuner.smells import (
    DETECTORS,
    Detector,
    DetectorInputs,
    detector_smells,
    ensure_detections,
    run_detectors,
    thresholds,
)
from kiln_harness_tuner.smells.detectors.long_skill_description import (
    skill_description,
)
from kiln_harness_tuner.smells.thresholds import (
    LONG_PROMPT_CHARS,
    LONG_SKILL_DESCRIPTION_CHARS,
    LONG_TOOL_DESCRIPTION_CHARS,
)

BY_ID = {d.id: d for d in DETECTORS}


def inputs(
    prompt: str | None = "short",
    tools: list[dict] | None = None,
    skills: dict[str, SkillText] | None = None,
) -> DetectorInputs:
    return DetectorInputs(
        rc_id="rc_base",
        properties={},
        texts=RcText(prompt=prompt, skill_ids=tuple(skills or ()), skills=skills or {}),
        tool_definitions=tools or [],
    )


def tool(name: str, chars: int) -> dict:
    return {"tool_id": f"mcp::{name}", "name": name, "description": "d" * chars}


def skill(name: str, chars: int) -> SkillText:
    body = f"---\nname: {name}\ndescription: {'d' * chars}\n---\n\n# {name}\n"
    return SkillText(skill_id=f"id_{name}", name=name, files={"SKILL.md": body})


# Each detector, with an input at the reference and one just above it.
CASES = {
    "long_prompt": (
        lambda n: inputs(prompt="p" * n),
        LONG_PROMPT_CHARS,
        "The system prompt has",
    ),
    "long_tool_description": (
        lambda n: inputs(tools=[tool("search_all", n)]),
        LONG_TOOL_DESCRIPTION_CHARS,
        "The description of the tool `search_all` has",
    ),
    "long_skill_description": (
        lambda n: inputs(skills={"id_guide": skill("guide", n)}),
        LONG_SKILL_DESCRIPTION_CHARS,
        "The description of the skill `guide` has",
    ),
}


def _silent_at_the_reference(detector_id: str) -> None:
    make, reference, _ = CASES[detector_id]
    assert BY_ID[detector_id].run(make(reference)) == []


def _reports_above_the_reference(detector_id: str) -> None:
    make, reference, start = CASES[detector_id]
    (detection,) = BY_ID[detector_id].run(make(reference + 1))
    assert detection.detector == detector_id
    assert detection.smell == BY_ID[detector_id].smell
    assert detection.scope == "harness"
    assert detection.value == reference + 1
    assert detection.reference == reference
    assert detection.reference_source
    assert detection.fact.startswith(start)
    assert f"{reference + 1:,} characters" in detection.fact
    assert f"The reference is {reference:,}." in detection.fact


def test_long_prompt_is_silent_at_the_reference():
    _silent_at_the_reference("long_prompt")


def test_long_prompt_reports_above_the_reference_with_value_and_source():
    _reports_above_the_reference("long_prompt")


def test_long_tool_description_is_silent_at_the_reference():
    _silent_at_the_reference("long_tool_description")


def test_long_tool_description_reports_above_the_reference_with_value_and_source():
    _reports_above_the_reference("long_tool_description")


def test_long_skill_description_is_silent_at_the_reference():
    _silent_at_the_reference("long_skill_description")


def test_long_skill_description_reports_above_the_reference_with_value_and_source():
    _reports_above_the_reference("long_skill_description")


def test_a_detection_names_each_tool_and_each_skill_over_the_reference():
    over = LONG_TOOL_DESCRIPTION_CHARS + 1
    found = BY_ID["long_tool_description"].run(
        inputs(tools=[tool("a", over), tool("b", 10), tool("c", over)])
    )
    assert [d.fact.split("`")[1] for d in found] == ["a", "c"]


def test_a_detector_is_silent_when_the_text_cannot_be_read():
    unread = DetectorInputs(
        rc_id="rc_base",
        properties={},
        texts=RcText(prompt=None, skill_ids=("s1",), skills=None),
        tool_definitions=[{"tool_id": "t", "name": "t", "description": None}],
    )
    assert [d.run(unread) for d in DETECTORS] == [[], [], []]


def test_skill_description_reads_the_front_matter():
    assert skill_description("---\nname: a\ndescription: Reads X.\n---\nbody") == (
        "Reads X."
    )
    assert skill_description("no front matter") is None
    assert skill_description("---\nname: [unclosed\n---\n") is None
    assert skill_description("---\ndescription: 12\n---\n") is None
    assert skill_description("---\ndescription: open\n") is None


# Words of judgement or blame. A detection states a fact and its numbers;
# the smell document and the agents interpret it.
JUDGEMENT_WORDS = {
    "too",
    "bloated",
    "excessive",
    "bad",
    "poor",
    "wrong",
    "should",
    "must",
    "problem",
    "smell",
    "violates",
    "exceeds",
    "huge",
}


def test_a_detection_fact_has_no_judgement_word():
    for detector_id, (make, reference, _) in CASES.items():
        (detection,) = BY_ID[detector_id].run(make(reference * 3))
        words = set(re.findall(r"[a-z]+", detection.fact.lower()))
        assert not words & JUDGEMENT_WORDS, (detector_id, detection.fact)
    assert set(CASES) == set(BY_ID), "a registered detector has no case here"


def test_every_registered_detector_has_a_unique_id():
    ids = [d.id for d in DETECTORS]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)*", i) for i in ids)
    assert detector_smells() == {d.id: d.smell for d in DETECTORS}


def test_every_threshold_has_a_source_comment():
    source = inspect.getsource(thresholds)
    lines = source.splitlines()
    names = []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        (target,) = node.targets
        if isinstance(target, ast.Name) and isinstance(node.value.value, int):
            names.append(target.id)
            assert lines[node.end_lineno].lstrip().startswith("#"), target.id
            text = getattr(thresholds, f"{target.id}_SOURCE", "")
            assert isinstance(text, str) and text.strip(), f"{target.id}_SOURCE"
    assert len(names) == 3


# ---- run_detectors, with the fake Kiln ----


def detection(smell: str = "very-long-prompt", detector: str = "x") -> Detection:
    return Detection(smell=smell, detector=detector, scope="harness", fact="A fact.")


def baseline_row(prompt: str, tools: tuple[str, ...] = ()) -> dict:
    return {
        "id": "rc_base",
        "name": "Baseline",
        "prompt": {"prompt": prompt},
        "run_config_properties": {"tools_config": {"tools": list(tools)}},
    }


def events(ctx, kind: str) -> list[dict]:
    return [fields for k, fields in ctx.log.events if k == kind]


async def test_run_detectors_reads_the_baseline_and_emits_the_detections():
    ctx = make_ctx()
    ctx.kiln.run_configs = [
        baseline_row(
            "p" * (LONG_PROMPT_CHARS + 1), ("mcp::a", "mcp::b", "kiln_tool::skill::s1")
        )
    ]
    ctx.kiln.tool_definitions["mcp::a"] = {
        "tool_id": "mcp::a",
        "function_name": "search_all",
        "description": "d" * (LONG_TOOL_DESCRIPTION_CHARS + 1),
        "parameters": {},
    }
    found = await run_detectors(ctx, "rc_base")
    assert [(d.detector, d.smell) for d in found] == [
        ("long_prompt", "very-long-prompt"),
        ("long_tool_description", "very-long-tool-description"),
    ]
    assert "`search_all`" in found[1].fact
    (event,) = events(ctx, "smell_detections")
    assert event["rc"] == "rc_base"
    assert event["count"] == 2
    assert event["detections"] == found
    assert event["unread_tools"] == ["mcp::b"]
    # A skill is read from its text, never from the tool-definition route.
    assert not [c for c in ctx.kiln.calls if c[1].endswith("skill::s1/definition")]
    # One listing of the run configs serves the properties and the texts.
    assert len([c for c in ctx.kiln.calls if c[1].endswith("/run_configs")]) == 1


async def test_run_detectors_skips_when_the_run_config_cannot_be_read():
    ctx = make_ctx()
    assert await run_detectors(ctx, "rc_missing") == []
    (event,) = events(ctx, "smell_detection_skipped")
    assert event["rc"] == "rc_missing"
    assert "rc_missing" in event["reason"]
    assert events(ctx, "smell_detections") == []


async def test_a_detector_that_raises_is_skipped_and_the_others_run():
    def broken(_inputs):
        raise RuntimeError("boom")

    ctx = make_ctx()
    ctx.kiln.run_configs = [baseline_row("p" * (LONG_PROMPT_CHARS + 1))]
    detectors = (Detector("broken", "very-long-prompt", broken), *DETECTORS)
    found = await run_detectors(ctx, "rc_base", detectors=detectors)
    assert [d.detector for d in found] == ["long_prompt"]
    (failed,) = events(ctx, "smell_detector_failed")
    assert failed["detector"] == "broken"
    assert "boom" in failed["reason"]


async def test_a_detection_of_an_unknown_smell_is_dropped():
    ctx = make_ctx()
    ctx.kiln.run_configs = [baseline_row("short")]
    detectors = (
        Detector(
            "odd", "no-such-smell", lambda _i: [detection("no-such-smell", "odd")]
        ),
        Detector(
            "known", "tool-search", lambda _i: [detection("tool-search", "known")]
        ),
    )
    found = await run_detectors(ctx, "rc_base", detectors=detectors)
    assert [d.detector for d in found] == ["known"]
    (unknown,) = events(ctx, "smell_unknown")
    assert unknown == {"detector": "odd", "smell": "no-such-smell"}


async def test_trace_stats_are_read_only_for_a_detector_that_reads_traces():
    seen: list[list[dict]] = []

    def record(given: DetectorInputs) -> list[Detection]:
        seen.append(given.trace_stats)
        return []

    ctx = make_ctx()
    ctx.kiln.run_configs = [baseline_row("short")]
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    ctx.kiln.specs = [{"id": "spec_1", "eval_id": "eval_1", "status": "active"}]
    run = make_run("t1", accuracy=1.0)
    run["task_run_trace"] = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    ctx.kiln.results[("eval_1", "rc_base", "train")] = [run]

    await run_detectors(ctx, "rc_base", detectors=(Detector("plain", "x", record),))
    assert seen == [[]]
    assert not [c for c in ctx.kiln.calls if c[1].endswith("/results")]

    traced = Detector("traced", "x", record, reads_traces=True)
    await run_detectors(ctx, "rc_base", detectors=(traced,))
    (stats,) = seen[1]
    assert stats["eval_id"] == "eval_1"
    assert stats["item_id"] == "t1"
    assert stats["assistant_turns"] == 1


async def test_ensure_detections_runs_once_per_baseline():
    ctx = make_ctx()
    long = "p" * (LONG_PROMPT_CHARS + 1)
    ctx.kiln.run_configs = [baseline_row(long), {**baseline_row("x"), "id": "rc_new"}]
    await ensure_detections(ctx, BaselineRef(rc_id="rc_base"))
    await ensure_detections(ctx, BaselineRef(rc_id="rc_base"))
    assert len(events(ctx, "smell_detections")) == 1
    assert [d.detector for d in ctx.detections] == ["long_prompt"]
    # A promotion moves the baseline: the detections are replaced.
    await ensure_detections(ctx, BaselineRef(rc_id="rc_new"))
    assert [e["rc"] for e in events(ctx, "smell_detections")] == ["rc_base", "rc_new"]
    assert ctx.detections == []


async def test_the_planner_input_carries_the_detections():
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    ctx.kiln.run_configs = [baseline_row("p" * (LONG_PROMPT_CHARS + 1))]
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    await orchestrate(ctx, "g", timeout_mins=180)
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert [d.detector for d in plan_input.detections] == ["long_prompt"]
    order = [
        k if k != "phase_start" else f"phase_start {f.get('name')}"
        for k, f in ctx.log.events
    ]
    assert order.index("smell_detections") < order.index("phase_start planning")


# ---- the tracker and the report ----


def test_the_tracker_keeps_the_detections_of_each_baseline():
    tracker = make_tracker()
    first = detection(detector="long_prompt")
    tracker.event("smell_detections", rc="rc_1", detections=[first, {"smell": 1}])
    tracker.event(
        "smell_detections", rc="rc_2", detections=[first.model_dump(mode="json")]
    )
    tracker.event("smell_detections", rc=None, detections=[first])
    status = tracker.snapshot()
    assert list(status.detections_by_rc) == ["rc_1", "rc_2"]
    assert status.detections_by_rc["rc_1"] == [first]
    assert status.detections_by_rc["rc_2"] == [first]


@pytest.fixture
def detected_status():
    (found,) = BY_ID["long_prompt"].run(inputs(prompt="p" * (LONG_PROMPT_CHARS + 5)))
    tracker = make_tracker()
    tracker.event("smell_detections", rc="rc_base", detections=[found])
    tracker.event("smell_detections", rc="rc_next", detections=[])
    return tracker.snapshot()


def test_the_report_has_the_smells_appendix(detected_status, tmp_path):
    text = render_smells_appendix(detected_status)
    assert text.startswith("## Design review\n")
    # No review event: the run had no reviewer (an old log).
    assert "Baseline `rc_base`. Design review: not run." in text
    assert "rc_next" not in text
    assert "| very-long-prompt | The system prompt has 200,005 characters." in text
    assert "| 200,000 |" in text
    assert thresholds.LONG_PROMPT_CHARS_SOURCE in text


def test_the_smells_appendix_is_left_out_without_a_detection():
    assert render_smells_appendix(make_tracker().snapshot()) == ""
    tracker = make_tracker()
    tracker.event("smell_detections", rc="rc_base", detections=[])
    assert render_smells_appendix(tracker.snapshot()) == ""


async def test_the_written_report_carries_the_smells_appendix(tmp_path):
    from kiln_harness_tuner.report import ReportWriter

    (found,) = BY_ID["long_prompt"].run(inputs(prompt="p" * (LONG_PROMPT_CHARS + 1)))
    tracker = make_tracker()
    tracker.event("smell_detections", rc="rc_base", detections=[found])
    writer = ReportWriter(tracker, "r1", tmp_path / "reports")
    path = await writer.write_fallback(make_ctx(), "wrap_up")
    assert "\n## Design review\n" in open(path, encoding="utf-8").read()


async def test_an_unreadable_knowledge_skill_keeps_every_detection(monkeypatch):
    from kiln_harness_tuner.knowledge.load import KnowledgeError

    def unreadable():
        raise KnowledgeError("no references/")

    monkeypatch.setattr("kiln_harness_tuner.smells.run.load", unreadable)
    ctx = make_ctx()
    ctx.kiln.run_configs = [baseline_row("short", ("mcp::a",))]
    # The OpenAI form of a definition is read too.
    ctx.kiln.tool_definitions["mcp::a"] = {
        "type": "function",
        "function": {"name": "fetch", "description": "d" * 5000},
    }
    odd = Detector("odd", "no-such-smell", lambda _i: [detection("no-such-smell")])
    found = await run_detectors(ctx, "rc_base", detectors=(odd, *DETECTORS))
    assert [d.smell for d in found] == ["no-such-smell", "very-long-tool-description"]
    assert "`fetch`" in found[1].fact
    assert events(ctx, "knowledge_unreadable") == [{"reason": "no references/"}]


async def test_a_quota_error_is_not_swallowed(monkeypatch):
    from kiln_harness_tuner.provider_errors import ProviderQuotaError
    from kiln_harness_tuner.schemas import ErrorInfo

    async def quota(*_args, **_kwargs):
        raise ProviderQuotaError(
            ErrorInfo(error_class="quota_exhausted", detail="out of credits")
        )

    monkeypatch.setattr("kiln_harness_tuner.smells.run.rc_texts", quota)
    ctx = make_ctx()
    ctx.kiln.run_configs = [baseline_row("short")]
    with pytest.raises(ProviderQuotaError):
        await run_detectors(ctx, "rc_base")
    with pytest.raises(ProviderQuotaError):
        await ensure_detections(ctx, BaselineRef(rc_id="rc_base"))


async def test_a_detector_that_returns_no_detection_object_is_skipped():
    ctx = make_ctx()
    ctx.kiln.run_configs = [baseline_row("p" * (LONG_PROMPT_CHARS + 1))]
    odd = Detector("odd", "very-long-prompt", lambda _i: ["not a detection"])
    found = await run_detectors(ctx, "rc_base", detectors=(odd, *DETECTORS))
    assert [d.detector for d in found] == ["long_prompt"]
    assert [e["detector"] for e in events(ctx, "smell_detector_failed")] == ["odd"]


async def test_a_knowledge_read_error_keeps_every_detection(monkeypatch):
    def unreadable():
        raise PermissionError("references/ cannot be read")

    monkeypatch.setattr("kiln_harness_tuner.smells.run.load", unreadable)
    ctx = make_ctx()
    ctx.kiln.run_configs = [baseline_row("short")]
    odd = Detector("odd", "no-such-smell", lambda _i: [detection("no-such-smell")])
    assert [d.smell for d in await run_detectors(ctx, "rc_base", detectors=(odd,))] == [
        "no-such-smell"
    ]
    assert len(events(ctx, "knowledge_unreadable")) == 1


async def test_ensure_detections_never_raises(monkeypatch):
    async def broken(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("kiln_harness_tuner.smells.run.run_detectors", broken)
    ctx = make_ctx()
    ctx.detections = [detection()]
    await ensure_detections(ctx, BaselineRef(rc_id="rc_base"))
    assert ctx.detections == []
    (skipped,) = events(ctx, "smell_detection_skipped")
    assert skipped == {"rc": "rc_base", "reason": "RuntimeError: boom"}


async def test_an_eval_whose_train_rows_cannot_be_read_gives_no_trace_stats():
    seen: list[list[dict]] = []

    def record(given: DetectorInputs) -> list[Detection]:
        seen.append(given.trace_stats)
        return []

    ctx = make_ctx()
    ctx.kiln.run_configs = [baseline_row("short")]
    for eval_id in ("eval_1", "eval_2"):
        ctx.kiln.evals[eval_id] = make_eval(eval_id, f"ec_{eval_id}")
    ctx.kiln.specs = [
        {"id": f"spec_{e}", "eval_id": e, "status": "active"}
        for e in ("eval_1", "eval_2")
    ]
    ctx.kiln.results_behaviors[("eval_1", "rc_base", "train")] = ["http500"] * 5
    # A row with no trace gives no stats.
    ctx.kiln.results[("eval_2", "rc_base", "train")] = [make_run("t1", accuracy=1)]
    traced = Detector("traced", "x", record, reads_traces=True)
    await run_detectors(ctx, "rc_base", detectors=(traced,))
    assert seen == [[]]
