"""The smell detectors in code (`kiln_harness_tuner.smells`): each detector
on each side of its threshold, the run step and its events, the planner
input, the tracker and the report's smells appendix."""

from __future__ import annotations

import ast
import inspect
import re
from dataclasses import replace
from datetime import date, datetime

import pytest
from conftest import make_ctx, make_plan, make_tracker
from fake_dispatcher import ok
from fake_kiln import make_eval, make_run
from test_orchestrator import seed_baseline

from kiln_harness_tuner.model_catalog import Catalog
from kiln_harness_tuner.model_catalog.schema import CatalogModel
from kiln_harness_tuner.orchestrator import orchestrate
from kiln_harness_tuner.prompt_text import RcText, SkillText
from kiln_harness_tuner.report import render_smells_appendix
from kiln_harness_tuner.schemas import BaselineRef, Detection
from kiln_harness_tuner.smells import (
    DETECTORS,
    Detector,
    DetectorInputs,
    ModelFacts,
    detector_smells,
    ensure_detections,
    run_detectors,
    thresholds,
)
from kiln_harness_tuner.smells.detectors.long_skill_description import (
    skill_description,
)
from kiln_harness_tuner.smells.detectors.old_model import add_months, whole_months
from kiln_harness_tuner.smells.thresholds import (
    LONG_PROMPT_CHARS,
    LONG_SKILL_DESCRIPTION_CHARS,
    LONG_TOOL_DESCRIPTION_CHARS,
    OLD_MODEL_MONTHS,
    OLD_MODEL_MONTHS_SOURCE,
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
    assert [d.run(unread) for d in DETECTORS] == [[] for _ in DETECTORS]


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
    firing = {
        detector_id: make(reference * 3)
        for detector_id, (make, reference, _) in CASES.items()
    }
    firing["old_model"] = aged(date(2024, 1, 10), date(2026, 1, 10))
    for detector_id, given in firing.items():
        (detection,) = BY_ID[detector_id].run(given)
        words = set(re.findall(r"[a-z]+", detection.fact.lower()))
        assert not words & JUDGEMENT_WORDS, (detector_id, detection.fact)
    assert set(firing) == set(BY_ID), "a registered detector has no case here"


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
    assert len(names) == 4


# ---- old_model: the release date against the run date ----


def aged(
    released: date | None,
    run_date: date | None,
    name: str = "GPT 4o Mini",
    model: str = "gpt_4o_mini",
) -> DetectorInputs:
    return replace(
        inputs(), model=ModelFacts("openai", model, name, released), run_date=run_date
    )


def old_model(given: DetectorInputs) -> list[Detection]:
    return BY_ID["old_model"].run(given)


def test_old_model_fires_after_eight_months():
    (detection,) = old_model(aged(date(2025, 1, 10), date(2025, 9, 11)))
    assert (detection.smell, detection.detector) == ("old-model", "old_model")
    assert detection.scope == "harness"
    assert detection.value == 8
    assert detection.reference == OLD_MODEL_MONTHS == 8
    assert detection.reference_source == OLD_MODEL_MONTHS_SOURCE
    assert detection.fact == (
        "The baseline's model, GPT 4o Mini (gpt_4o_mini), was released on"
        " 2025-01-10, 8 months before this run. The reference is 8 months."
    )
    (later,) = old_model(aged(date(2025, 1, 10), date(2026, 3, 9)))
    assert later.value == 13


def test_old_model_is_silent_at_exactly_eight_months():
    assert old_model(aged(date(2025, 1, 10), date(2025, 9, 10))) == []
    assert old_model(aged(date(2025, 1, 10), date(2025, 2, 1))) == []


def test_old_model_is_silent_without_a_date():
    assert old_model(aged(None, date(2026, 1, 1))) == []
    assert old_model(aged(date(2020, 1, 1), None)) == []
    assert old_model(replace(inputs(), run_date=date(2026, 1, 1))) == []


def test_old_model_clamps_the_day_at_month_end():
    (detection,) = old_model(aged(date(2025, 5, 31), date(2026, 2, 1)))
    assert detection.value == 8
    assert add_months(date(2025, 6, 30), 8) == date(2026, 2, 28)
    assert add_months(date(2023, 6, 30), 8) == date(2024, 2, 29)
    assert add_months(date(2025, 11, 15), 2) == date(2026, 1, 15)
    # 30 June plus 8 months is 28 February: exactly the reference.
    assert old_model(aged(date(2025, 6, 30), date(2026, 2, 28))) == []
    (next_day,) = old_model(aged(date(2025, 6, 30), date(2026, 3, 1)))
    assert next_day.value == 8
    assert whole_months(date(2025, 5, 31), date(2025, 6, 30)) == 1
    assert whole_months(date(2025, 5, 31), date(2025, 6, 29)) == 0


def test_old_model_cuts_a_long_name():
    (detection,) = old_model(
        aged(date(2020, 1, 1), date(2026, 1, 1), name="n" * 200, model="m" * 200)
    )
    assert "n" * 80 + " (" + "m" * 80 + ")" in detection.fact
    assert len(detection.fact) <= 300


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


def dated_catalog(model: str, release_date: str | None) -> Catalog:
    return Catalog(
        models=[CatalogModel(model=model, name="GPT 4o", release_date=release_date)]
    )


def model_row(model: str = "gpt_4o") -> dict:
    row = baseline_row("short")
    row["run_config_properties"].update(model_name=model, model_provider_name="openai")
    return row


async def test_collect_reads_the_release_date_from_the_catalogue():
    ctx = make_ctx()
    ctx.kiln.run_configs = [model_row()]
    ctx.model_catalog = dated_catalog("gpt_4o", "2024-05-13")
    ctx.run_started_at = datetime(2025, 2, 14, 9, 30)
    found = await run_detectors(ctx, "rc_base")
    (detection,) = [d for d in found if d.detector == "old_model"]
    assert detection.value == 9
    assert "GPT 4o (gpt_4o), was released on 2024-05-13" in detection.fact

    seen: list[DetectorInputs] = []

    def record(given: DetectorInputs) -> list[Detection]:
        seen.append(given)
        return []

    await run_detectors(ctx, "rc_base", detectors=(Detector("r", "x", record),))
    assert seen[-1].model == ModelFacts("openai", "gpt_4o", "GPT 4o", date(2024, 5, 13))
    assert seen[-1].run_date == date(2025, 2, 14)
    # The catalogue is the snapshot: the detector makes no call to Kiln for it.
    assert not [c for c in ctx.kiln.calls if "available_models" in c[1]]


@pytest.mark.parametrize(
    ("catalog", "model"),
    [
        (None, "gpt_4o"),
        (dated_catalog("gpt_4o", "2024-05-13"), "ft_custom_1"),
        (dated_catalog("gpt_4o", "not a date"), "gpt_4o"),
        (dated_catalog("gpt_4o", None), "gpt_4o"),
    ],
    ids=["no catalogue", "a model it lacks", "a bad date", "no date"],
)
async def test_old_model_is_silent_when_the_catalogue_has_no_date(catalog, model):
    ctx = make_ctx()
    ctx.kiln.run_configs = [model_row(model)]
    ctx.model_catalog = catalog
    ctx.run_started_at = datetime(2030, 1, 1)
    found = await run_detectors(ctx, "rc_base")
    assert not [d for d in found if d.detector == "old_model"]


async def test_a_run_config_without_a_model_gives_no_model_facts():
    seen: list[DetectorInputs] = []

    def record(given: DetectorInputs) -> list[Detection]:
        seen.append(given)
        return []

    ctx = make_ctx()
    ctx.kiln.run_configs = [baseline_row("short")]
    await run_detectors(ctx, "rc_base", detectors=(Detector("r", "x", record),))
    (given,) = seen
    assert given.model is None
    assert given.run_date is None
