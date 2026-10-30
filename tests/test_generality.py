"""The generality check between the build and the train measurement:
one no-tool review of what a build added to its parent's prompt; a leak gets
ONE rebuild, a second leak ends the arm before any train spend."""

from __future__ import annotations

import json

from conftest import fix_item, make_ctx, make_result
from fake_dispatcher import expected, infra, ok
from test_items import BASELINE, seed_measurement
from test_prompt_text import install_skill, on_disk, skill_md, skill_rc
from test_quarantine import TASK, Project

from kiln_harness_tuner.generality import (
    TrainDoc,
    added_text,
    audit_baseline,
    check_generality,
    code_tool_changes,
    exact_matches,
    skill_changes,
    train_documents,
    train_expected_lines,
    train_input_lines,
)
from kiln_harness_tuner.items import execute_plan_item
from kiln_harness_tuner.memory_schema import BASELINE_AUDIT_TAG
from kiln_harness_tuner.probes import TrainInput
from kiln_harness_tuner.prompt_text import RcText, SkillText, rc_texts
from kiln_harness_tuner.schemas import GeneralityReviewResult, RcBuildResult

BASE_PROMPT = "You are a freight depot assistant. Answer the operator's question."
GENERAL = BASE_PROMPT + " Check the manifest before you state a count."
LEAKY = BASE_PROMPT + " For the Halden depot, the answer is always 14 pallets."


def _ctx(*prompts: tuple[str, str]):
    ctx = make_ctx()
    ctx.kiln.run_configs = [
        {"id": "rc_base", "name": "b", "prompt": {"prompt": BASE_PROMPT}},
        *(
            {"id": rc_id, "name": rc_id, "prompt": {"prompt": text}}
            for rc_id, text in prompts
        ),
    ]
    return ctx


def _built(rc_id: str):
    return ok(RcBuildResult(rc_id=rc_id, build_notes="built"))


def _review(*leaks: str, notes: str = ""):
    return ok(GeneralityReviewResult(leaks=list(leaks), notes=notes))


def _view(record: dict):
    from kiln_harness_tuner.memory_schema import ExperimentView, MemoryRecord

    return ExperimentView.from_record(MemoryRecord.from_api(record))


def _states(ctx) -> list[str]:
    return [f["state"] for k, f in ctx.log.events if k == "item_state"]


async def test_a_changed_prompt_is_reviewed_before_the_measurement():
    ctx = _ctx(("rc_new", GENERAL))
    seed_measurement(ctx.kiln)
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    ctx.dispatch.queue("generality_review", _review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert outcome.rc_id == "rc_new"
    (review,) = ctx.dispatch.calls_to("generality_review")
    assert review.item_id == "w1-1"
    assert review.candidate_rc_id == "rc_new"
    assert review.candidate_instructions == GENERAL
    assert review.parent_instructions == BASE_PROMPT
    assert review.hypothesis == "retry-skill for 429s"
    assert _states(ctx) == ["building_rc", "reviewing", "measuring", "analyzing"]
    spend = [f for k, f in ctx.log.events if k == "spend"]
    assert any(f["source"] == "generality_review" for f in spend)
    # A clean first check writes nothing on the record.
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    assert "generality:" not in (record["content"] or "")


async def test_an_unchanged_prompt_is_not_reviewed():
    ctx = _ctx(("rc_new", BASE_PROMPT))
    seed_measurement(ctx.kiln)
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)
    assert outcome.status == "completed"
    assert ctx.dispatch.calls_to("generality_review") == []
    assert "reviewing" not in _states(ctx)


async def test_a_leak_gets_one_rebuild_with_the_findings():
    ctx = _ctx(("rc_new", LEAKY), ("rc_new2", GENERAL))
    seed_measurement(ctx.kiln, "rc_new2")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    ctx.dispatch.queue(
        "generality_review",
        _review('"For the Halden depot … 14 pallets" states item t7\'s answer'),
        _review(),
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert outcome.rc_id == "rc_new2"  # the rebuild is what was measured
    first, rebuild = ctx.dispatch.calls_to("create_experiment_rc")
    assert first.rejected_rc_id is None and first.generality_findings == []
    assert rebuild.rejected_rc_id == "rc_new"
    assert rebuild.generality_findings == [
        '"For the Halden depot … 14 pallets" states item t7\'s answer'
    ]
    (analysis,) = ctx.dispatch.calls_to("analyze_experiment_results")
    assert analysis.rc_id == "rc_new2"
    # The finding outlives the verdict: the planner reads it on the record.
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    content = record["content"]
    assert "generality: first build rc_new leaked train specifics" in content
    assert "rebuilt once as rc_new2 — clean" in content
    # The record names the REBUILD as its run config after the verdict: a
    # planner reuses or merges what `rc:` names, never the refused build.
    assert _view(record).rc_id == "rc_new2"
    assert content.count("rc: ") == 1


async def test_a_rebuild_that_still_leaks_is_a_dead_end_before_any_train_spend():
    ctx = _ctx(("rc_new", LEAKY), ("rc_new2", LEAKY))
    seed_measurement(ctx.kiln, "rc_new2")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    ctx.dispatch.queue(
        "generality_review", _review("names the Halden depot"), _review("still")
    )
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "dead_end"
    assert outcome.rc_id == "rc_new2"
    assert ctx.dispatch.calls_to("analyze_experiment_results") == []
    assert "measuring" not in _states(ctx)
    assert not [f for k, f in ctx.log.events if k == "queue_drawn"]
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    assert record["overview"].endswith(
        "— DEAD END (generality: still leaks train specifics after one rebuild)"
    )
    assert "the rebuild rc_new2 still leaked (still)" in record["content"]
    # No verdict replaced the content here: the replacement itself did it.
    assert _view(record).rc_id == "rc_new2"


async def test_a_rebuild_the_builder_refuses_is_a_dead_end():
    ctx = _ctx(("rc_new", LEAKY))
    ctx.dispatch.queue(
        "create_experiment_rc",
        _built("rc_new"),
        expected("the fix only works by naming the depot"),
    )
    ctx.dispatch.queue("generality_review", _review("names the Halden depot"))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)
    assert outcome.status == "dead_end"
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    assert "generality: the fix only works by naming the depot" in record["overview"]


async def test_a_rebuild_that_fails_on_infra_fails_the_item_and_says_so():
    ctx = _ctx(("rc_new", LEAKY))
    ctx.dispatch.queue(
        "create_experiment_rc", _built("rc_new"), infra("parse_failure", "garbled")
    )
    ctx.dispatch.queue("generality_review", _review("names the Halden depot"))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "failed_infra"
    assert outcome.rc_id == "rc_new"
    assert ctx.dispatch.calls_to("analyze_experiment_results") == []
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    assert record["overview"].endswith("— FAILED (infra: parse_failure)")
    assert (
        "generality: first build rc_new leaked train specifics (names the Halden"
        " depot); the rebuild did not land" in record["content"]
    )
    assert _view(record).rc_id == "rc_new"


async def test_a_rebuild_back_to_the_parents_prompt_is_measured_and_says_so():
    ctx = _ctx(("rc_new", LEAKY), ("rc_new2", BASE_PROMPT))
    seed_measurement(ctx.kiln, "rc_new2")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    ctx.dispatch.queue("generality_review", _review("names the Halden depot"))
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert outcome.rc_id == "rc_new2"
    assert len(ctx.dispatch.calls_to("generality_review")) == 1
    content = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
    assert (
        "rebuilt once as rc_new2 — its instructions now match the parent's" in content
    )


def _reused_item():
    item = fix_item()
    item.work.existing_rc_id = "rc_old"
    return item


async def test_a_clean_reused_run_config_is_measured():
    ctx = _ctx(("rc_old", GENERAL))
    seed_measurement(ctx.kiln, "rc_old")
    ctx.dispatch.queue("generality_review", _review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, _reused_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert outcome.rc_id == "rc_old"
    assert ctx.dispatch.calls_to("create_experiment_rc") == []
    (review,) = ctx.dispatch.calls_to("generality_review")
    assert review.candidate_rc_id == "rc_old"


async def test_a_leak_on_a_reused_run_config_ends_the_item_without_a_rebuild():
    ctx = _ctx(("rc_old", LEAKY))
    seed_measurement(ctx.kiln, "rc_old")
    ctx.dispatch.queue("generality_review", _review("names the Halden depot"))
    outcome = await execute_plan_item(ctx, _reused_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "dead_end"
    assert outcome.rc_id == "rc_old"
    assert ctx.dispatch.calls_to("create_experiment_rc") == []
    assert ctx.dispatch.calls_to("analyze_experiment_results") == []
    assert "measuring" not in _states(ctx)
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    assert record["overview"].endswith(
        "— DEAD END (generality: the reused run config leaks train specifics)"
    )
    content = record["content"]
    assert (
        "generality: reused run config rc_old leaked train specifics (names the"
        " Halden depot); a reused run config is not rebuilt" in content
    )
    assert "first build" not in content
    view = _view(record)
    assert view.rc_id == "rc_old"
    assert view.reused_rc_id == "rc_old"


async def test_the_reviewers_notes_go_on_the_generality_event():
    ctx = _ctx(("rc_new", GENERAL))
    seed_measurement(ctx.kiln)
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    ctx.dispatch.queue("generality_review", _review(notes="unsure about 'manifest'"))
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    (event,) = [f for k, f in ctx.log.events if k == "generality"]
    assert event["notes"] == "unsure about 'manifest'"
    content = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
    assert "manifest'" not in content


async def test_the_reviewer_is_told_which_train_inputs_are_targets(monkeypatch):
    inputs = [
        TrainInput(id=f"t{i}", structured=None, plaintext=f"input {i}", size=7)
        for i in range(30)
    ]
    monkeypatch.setattr(
        "kiln_harness_tuner.generality.split_inputs", lambda *_a, **_k: list(inputs)
    )
    ctx = _ctx(("rc_new", GENERAL))
    ctx.dispatch.queue("generality_review", _review())
    item = fix_item()
    item.work.target_items = ["t7", "missing", "t3", "t7"]
    await check_generality(ctx, item, "rc_new", "rc_base")

    (review,) = ctx.dispatch.calls_to("generality_review")
    assert review.target_items == ["t7", "t3"]
    assert review.train_inputs[:2] == ["t7: input 7", "t3: input 3"]


async def test_a_review_that_cannot_run_lets_the_item_go_on_and_says_so():
    ctx = _ctx(("rc_new", GENERAL))
    seed_measurement(ctx.kiln)
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    ctx.dispatch.queue("generality_review", infra("parse_failure", "garbled"))
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)
    assert outcome.status == "completed"
    content = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
    assert "generality: not checked — the review did not run (garbled)" in content


async def test_unreadable_instructions_are_unchecked_not_a_pass():
    ctx = make_ctx()  # no run config rows at all
    check = await check_generality(ctx, fix_item(), "rc_new", "rc_base")
    assert check.status == "unchecked"
    assert "could not be read" in check.note
    assert ctx.dispatch.calls_to("generality_review") == []


def test_the_reviewer_sees_the_target_items_first(monkeypatch):
    inputs = [
        TrainInput(id=f"t{i}", structured=None, plaintext=f"input {i}", size=7)
        for i in range(30)
    ]
    inputs.append(
        TrainInput(id="ts", structured={"q": "x" * 2000}, plaintext=None, size=9)
    )
    monkeypatch.setattr(
        "kiln_harness_tuner.generality.split_inputs", lambda *_a, **_k: list(inputs)
    )
    lines = train_input_lines(make_ctx(), ["t7", "ts", "missing"])
    assert lines[0] == "t7: input 7"
    assert lines[1].startswith('ts: {"q": "xxx')
    assert len(lines[1]) < 700  # clipped
    assert len(lines) == 12
    assert len(set(lines)) == 12


def test_train_inputs_that_cannot_be_read_leave_the_text_alone(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("no project")

    monkeypatch.setattr("kiln_harness_tuner.generality.split_inputs", boom)
    ctx = make_ctx()
    assert train_input_lines(ctx, []) == []
    assert [k for k, _ in ctx.log.events if k == "generality_inputs_unavailable"]


# ---- the attached skills' text ----

SKILL_LINES = ("Call the API once per question.", "Retry once on a 429.")
SKILL_LEAK = "For the Halden depot, the answer is always 14 pallets."
SKILL_GENERAL = "Check the manifest before you state a count."
REFERENCE = "Code 7 means a held pallet.\n"


def _skill_ctx(project, *candidates: dict):
    ctx = make_ctx()
    on_disk(ctx, project)
    install_skill(
        project,
        "sk1",
        "depot-api",
        skill_md("depot-api", *SKILL_LINES),
        {"codes.md": REFERENCE},
    )
    ctx.kiln.run_configs = [skill_rc("rc_base", BASE_PROMPT, "sk1"), *candidates]
    return ctx


def _new_version(project, skill_id: str, name: str, line: str) -> None:
    install_skill(
        project,
        skill_id,
        name,
        skill_md(name, *SKILL_LINES, line),
        {"codes.md": REFERENCE},
        derived_from=("sk1",),
    )


async def test_a_changed_skill_is_reviewed_when_the_prompt_is_not(tmp_path):
    ctx = _skill_ctx(tmp_path, skill_rc("rc_new", BASE_PROMPT, "sk2"))
    _new_version(tmp_path, "sk2", "depot-api-v2", SKILL_GENERAL)
    seed_measurement(ctx.kiln)
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    ctx.dispatch.queue("generality_review", _review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    (review,) = ctx.dispatch.calls_to("generality_review")
    assert review.candidate_instructions == review.parent_instructions
    # One block: the changed SKILL.md, diffed against the version it
    # replaces. The unchanged reference file is not shown.
    (block,) = review.skill_changes
    head, _, diff = block.partition("\n")
    assert head == (
        "skill depot-api-v2 (sk2), SKILL.md: changed from skill depot-api (sk1)"
        " — unified diff"
    )
    assert f"+{SKILL_GENERAL}" in diff.splitlines()
    assert "+name: depot-api-v2" in diff.splitlines()


async def test_a_new_skill_beside_its_kept_source_is_diffed_against_it(tmp_path):
    ctx = _skill_ctx(tmp_path, skill_rc("rc_new", BASE_PROMPT, "sk1", "sk2"))
    _new_version(tmp_path, "sk2", "depot-api-v2", SKILL_GENERAL)
    texts = await rc_texts(ctx, ["rc_base", "rc_new"])
    (block,) = skill_changes(texts["rc_base"], texts["rc_new"])
    assert block.partition("\n")[0].endswith(
        "changed from skill depot-api (sk1) — unified diff"
    )


async def test_a_new_skill_is_reviewed_in_full(tmp_path):
    ctx = _skill_ctx(tmp_path, skill_rc("rc_new", BASE_PROMPT, "sk1", "sk5"))
    install_skill(tmp_path, "sk5", "manifest", skill_md("manifest", SKILL_GENERAL))
    texts = await rc_texts(ctx, ["rc_base", "rc_new"])
    (block,) = skill_changes(texts["rc_base"], texts["rc_new"])
    assert block.startswith(
        "skill manifest (sk5), SKILL.md: new file (new skill) — whole text\n"
    )
    assert SKILL_GENERAL in block


async def test_a_leak_in_a_skill_gets_one_rebuild_then_ends_the_arm(tmp_path):
    ctx = _skill_ctx(
        tmp_path,
        skill_rc("rc_new", BASE_PROMPT, "sk2"),
        skill_rc("rc_new2", BASE_PROMPT, "sk3"),
    )
    _new_version(tmp_path, "sk2", "depot-api-v2", SKILL_LEAK)
    _new_version(tmp_path, "sk3", "depot-api-v3", SKILL_LEAK)
    seed_measurement(ctx.kiln, "rc_new2")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    ctx.dispatch.queue(
        "generality_review",
        _review("skill depot-api-v2 SKILL.md names the Halden depot"),
        _review("skill depot-api-v3 SKILL.md still names it"),
    )
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "dead_end"
    first, second = ctx.dispatch.calls_to("generality_review")
    assert any(SKILL_LEAK in block for block in first.skill_changes)
    assert any(SKILL_LEAK in block for block in second.skill_changes)
    (_, rebuild) = ctx.dispatch.calls_to("create_experiment_rc")
    assert rebuild.generality_findings == [
        "skill depot-api-v2 SKILL.md names the Halden depot"
    ]
    assert ctx.dispatch.calls_to("analyze_experiment_results") == []
    assert "measuring" not in _states(ctx)


async def test_unreadable_skills_with_the_same_prompt_are_unchecked(tmp_path):
    ctx = _skill_ctx(tmp_path, skill_rc("rc_new", BASE_PROMPT, "sk_gone"))
    seed_measurement(ctx.kiln)
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert ctx.dispatch.calls_to("generality_review") == []
    content = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
    assert "generality: not checked — the skills' text could not be read" in content


async def test_unreadable_skills_leave_the_prompt_to_be_checked_alone(tmp_path):
    ctx = _skill_ctx(tmp_path, skill_rc("rc_new", GENERAL, "sk_gone"))
    seed_measurement(ctx.kiln)
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    ctx.dispatch.queue("generality_review", _review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    (review,) = ctx.dispatch.calls_to("generality_review")
    assert review.skill_changes == []
    content = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
    assert (
        "generality: the skills' text could not be read — only the prompt was"
        " checked" in content
    )
    (event,) = [f for k, f in ctx.log.events if k == "generality"]
    assert event["line"] == (
        "generality rc_new: clean — the skills' text could not be read — only"
        " the prompt was checked"
    )


async def test_a_second_leak_found_without_the_skills_says_so(tmp_path):
    ctx = _skill_ctx(
        tmp_path,
        skill_rc("rc_new", LEAKY, "sk_gone"),
        skill_rc("rc_new2", LEAKY, "sk_gone"),
    )
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    ctx.dispatch.queue("generality_review", _review("names it"), _review("still"))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "dead_end"
    content = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
    assert (
        "the rebuild rc_new2 still leaked (still) (the skills' text could not be"
        " read — only the prompt was checked)" in content
    )


async def test_a_leak_found_without_the_skills_says_they_were_not_read(tmp_path):
    ctx = _skill_ctx(
        tmp_path,
        skill_rc("rc_new", LEAKY, "sk_gone"),
        skill_rc("rc_new2", GENERAL, "sk_gone"),
    )
    seed_measurement(ctx.kiln, "rc_new2")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    ctx.dispatch.queue(
        "generality_review", _review("names the Halden depot"), _review()
    )
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    content = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
    assert (
        "first build rc_new leaked train specifics (names the Halden depot)"
        " (the skills' text could not be read — only the prompt was checked)" in content
    )


def test_skill_changes_past_the_size_cap_are_counted_not_shown(monkeypatch):
    monkeypatch.setattr("kiln_harness_tuner.generality.SKILL_CHANGE_CHARS", 100)
    parent = RcText(prompt="p", skill_ids=(), skills={})
    files = {f"references/r{i}.md": "x" * 80 for i in range(3)}
    new = SkillText("sk5", "big", {"SKILL.md": "y" * 10, **files})
    candidate = RcText(prompt="p", skill_ids=("sk5",), skills={"sk5": new})
    shown = skill_changes(parent, candidate)
    assert shown is not None
    assert shown[-1] == "… 2 more changed file(s) not shown"
    assert all(len(block) <= 100 for block in shown[:-1])


# ---- code tools, the exact-match pre-check, expected outputs ----


def install_code_tool(
    project, tool_id: str, name: str, code: str, derived_from: tuple[str, ...] = ()
) -> None:
    """One code tool on disk, in the layout the studio server writes."""
    directory = project / "code_tools" / f"{tool_id} - {name}"
    directory.mkdir(parents=True)
    (directory / "tool.py").write_text(code, encoding="utf-8")
    (directory / "code_tool.kiln").write_text(
        json.dumps(
            {
                "id": tool_id,
                "name": name,
                "tool_function_name": name,
                "tool_description": "Look up a value.",
                "parameters_schema": {"type": "object"},
                "provenance": {"derived_from_ids": list(derived_from)},
            }
        ),
        encoding="utf-8",
    )


def tool_rc(rc_id: str, prompt: str, *tool_ids: str) -> dict:
    tools = [f"kiln_tool::code::{i}" for i in tool_ids]
    return {
        "id": rc_id,
        "name": rc_id,
        "prompt": {"prompt": prompt},
        "run_config_properties": {"tools_config": {"tools": tools}},
    }


TOOL_CODE = "def lookup(code):\n    return {'A': 1}.get(code)\n"
TOOL_LEAK = "def lookup(code):\n    return {'Halden': 14}.get(code)\n"


def _tool_ctx(project, *candidates: dict):
    ctx = make_ctx()
    on_disk(ctx, project)
    install_code_tool(project, "t1", "lookup", TOOL_CODE)
    ctx.kiln.run_configs = [tool_rc("rc_base", BASE_PROMPT, "t1"), *candidates]
    return ctx


async def test_a_changed_code_tool_is_reviewed_when_the_prompt_is_not(tmp_path):
    ctx = _tool_ctx(tmp_path, tool_rc("rc_new", BASE_PROMPT, "t2"))
    install_code_tool(tmp_path, "t2", "lookup-v2", TOOL_LEAK, derived_from=("t1",))
    seed_measurement(ctx.kiln)
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    ctx.dispatch.queue("generality_review", _review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    (review,) = ctx.dispatch.calls_to("generality_review")
    assert review.skill_changes == []
    # The code, and the description (its function name changed with it).
    assert len(review.code_tool_changes) == 2
    (block,) = [b for b in review.code_tool_changes if ", tool.py:" in b]
    head, _, diff = block.partition("\n")
    assert head == (
        "code tool lookup-v2 (t2), tool.py: changed from code tool lookup (t1)"
        " — unified diff"
    )
    assert "+    return {'Halden': 14}.get(code)" in diff.splitlines()


async def test_a_new_code_tool_is_reviewed_in_full_with_its_description(tmp_path):
    ctx = _tool_ctx(tmp_path, tool_rc("rc_new", BASE_PROMPT, "t1", "t5"))
    install_code_tool(tmp_path, "t5", "answers", TOOL_LEAK)
    texts = await rc_texts(ctx, ["rc_base", "rc_new"])
    blocks = code_tool_changes(texts["rc_base"], texts["rc_new"])
    heads = sorted(b.partition("\n")[0] for b in blocks)
    assert heads == [
        "code tool answers (t5), description: new file (new code tool) — whole text",
        "code tool answers (t5), tool.py: new file (new code tool) — whole text",
    ]
    assert any('"tool_description": "Look up a value."' in b for b in blocks)


async def test_unchanged_code_tools_and_prompt_are_not_reviewed(tmp_path):
    ctx = _tool_ctx(tmp_path, tool_rc("rc_new", BASE_PROMPT, "t1"))
    texts = await rc_texts(ctx, ["rc_base", "rc_new"])
    assert code_tool_changes(texts["rc_base"], texts["rc_new"]) == []


DOCS = [
    TrainDoc(
        id="424242500001",
        input_text=(
            "Invoice No. INV-58213-C from Quillfeather Tools. Total due 1,080.00."
            " Please remit by the end of the month to our usual account."
        ),
        reference={
            "expected": {
                "supplier_name": "Quillfeather Industrial Supply Ltd",
                "total_cents": 108000,
                "currency": "CAD",
            }
        },
    ),
    *(
        TrainDoc(
            id=f"42424250001{i}",
            input_text=f"Invoice INV-9{i}0 from Common Supplier. Payment terms apply.",
            reference={"expected": {"currency": "EUR"}},
        )
        for i in range(2, 9)
    ),
]


def test_exact_matches_find_ids_runs_and_expected_values():
    added = {
        "the prompt": (
            "For INV-58213-C the supplier is Quillfeather Industrial Supply Ltd.\n"
            "Please remit by the end of the month to our usual account."
        )
    }
    found = exact_matches(added, DOCS)
    text = "\n".join(found)
    assert '"inv-58213-c" in the prompt is copied from train item 424242500001' in text
    assert "quillfeather industrial supply ltd" in text
    assert "(its expected output)" in text
    # One finding for the copied sentence, its earliest run.
    (run,) = [f for f in found if f.endswith("(its input)")]
    assert run.startswith('"please remit by the end of the month" in the prompt')


def test_exact_matches_skip_general_text_boilerplate_and_known_text():
    general = {
        "the prompt": (
            "Book a credit note as a negative total. Use the legal name from the"
            " footer, not the trading name. Payment terms apply."
        )
    }
    assert exact_matches(general, DOCS) == []
    # A long number from an expected output is specific.
    assert exact_matches({"the prompt": "Book 108000 as the total."}, DOCS)
    # The same text, when the task's instructions already say it.
    assert (
        exact_matches({"the prompt": "Book 108000 as the total."}, DOCS, "108000") == []
    )


def test_a_line_the_parent_has_is_not_added():
    parent = RcText(prompt="Line one.\nRef INV-58213-C.", skill_ids=(), skills={})
    candidate = RcText(
        prompt="Line one.\nRef INV-58213-C.\nA new rule.", skill_ids=(), skills={}
    )
    assert added_text(parent, candidate) == {"the prompt": "A new rule."}


def _train_project(tmp_path):
    p = Project(tmp_path / "proj")
    p.eval("e1", "a")
    p.eval_input(
        "424242500001",
        ["train_a"],
        text="Invoice No. INV-58213-C from Quillfeather Tools. Total 1,080.00.",
        reference={"expected": {"supplier_name": "Quillfeather Industrial Supply Ltd"}},
    )
    p.eval_input(
        "424242500002",
        ["train_a"],
        text="Invoice INV-77 from Common Supplier.",
        reference={"expected": {"currency": "EUR"}},
    )
    p.eval_input(
        "424242500003",
        ["val_a"],
        text="SECRET val invoice VAL-55555.",
        reference={"expected": "SECRET"},
    )
    return p


def _on_project(ctx, p) -> None:
    ctx.target = ctx.target.model_copy(
        update={"project_path": str(p.root), "task_id": TASK}
    )


async def test_an_exact_match_is_a_leak_with_no_review(tmp_path):
    p = _train_project(tmp_path)
    ctx = _ctx(
        ("rc_new", BASE_PROMPT + " Invoice INV-58213-C is from Quillfeather."),
        ("rc_new2", GENERAL),
    )
    _on_project(ctx, p)
    seed_measurement(ctx.kiln, "rc_new2")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    ctx.dispatch.queue("generality_review", _review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert outcome.rc_id == "rc_new2"
    _first, rebuild = ctx.dispatch.calls_to("create_experiment_rc")
    (finding,) = rebuild.generality_findings
    assert finding.startswith('exact match: "inv-58213-c" in the prompt')
    assert "424242500001" in finding
    # Only the rebuild needed the reviewer: the match itself was code.
    (review,) = ctx.dispatch.calls_to("generality_review")
    assert review.candidate_rc_id == "rc_new2"
    assert [k for k, _ in ctx.log.events if k == "generality_exact_match"]


async def test_an_id_the_parent_already_has_is_not_an_exact_match(tmp_path):
    # The parent's text names INV-58213-C. A new line that repeats it adds
    # nothing from train that the parent did not carry: reviewed, not a leak.
    p = _train_project(tmp_path)
    parent = BASE_PROMPT + "\nInvoices look like INV-58213-C."
    ctx = _ctx(("rc_new", parent + "\nLook up INV-58213-C before you book it."))
    ctx.kiln.run_configs[0]["prompt"] = {"prompt": parent}
    ctx.policy_exempt_rcs.add("rc_base")
    _on_project(ctx, p)
    seed_measurement(ctx.kiln)
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    ctx.dispatch.queue("generality_review", _review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert outcome.rc_id == "rc_new"
    assert len(ctx.dispatch.calls_to("create_experiment_rc")) == 1
    assert not [k for k, _ in ctx.log.events if k == "generality_exact_match"]
    assert [f for k, f in ctx.log.events if k == "generality_parent_known"]


async def test_a_parent_off_the_baseline_chain_gives_no_known_text(tmp_path):
    # The same build on a parent that was never promoted: its text is not
    # known, so the repeated id is still an exact match.
    p = _train_project(tmp_path)
    parent = BASE_PROMPT + "\nInvoices look like INV-58213-C."
    ctx = _ctx(
        ("rc_new", parent + "\nLook up INV-58213-C before you book it."),
        ("rc_new2", GENERAL),
    )
    ctx.kiln.run_configs[0]["prompt"] = {"prompt": parent}
    _on_project(ctx, p)
    seed_measurement(ctx.kiln, "rc_new2")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    ctx.dispatch.queue("generality_review", _review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert [k for k, _ in ctx.log.events if k == "generality_exact_match"]


async def test_the_reviewer_sees_train_expected_outputs_and_no_val(tmp_path):
    p = _train_project(tmp_path)
    ctx = _ctx(("rc_new", GENERAL))
    _on_project(ctx, p)
    seed_measurement(ctx.kiln)
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    ctx.dispatch.queue("generality_review", _review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    (review,) = ctx.dispatch.calls_to("generality_review")
    shown = [line.split(": ", 1)[0] for line in review.train_inputs]
    assert sorted(shown) == ["424242500001", "424242500002"]
    assert [line.split(": ", 1)[0] for line in review.train_expected] == shown
    assert any("Quillfeather Industrial Supply Ltd" in x for x in review.train_expected)
    assert "SECRET" not in json.dumps(review.model_dump())


def test_train_documents_read_the_train_set_only(tmp_path):
    p = _train_project(tmp_path)
    ctx = make_ctx()
    _on_project(ctx, p)
    docs = train_documents(ctx)
    assert [d.id for d in docs] == ["424242500001", "424242500002"]
    assert "INV-58213-C" in docs[0].input_text
    assert train_expected_lines(docs, ["424242500002", "nope"]) == [
        '424242500002: {"expected": {"currency": "EUR"}}'
    ]


def test_train_documents_that_cannot_be_read_leave_the_check_going():
    ctx = make_ctx()  # its project path does not exist
    assert train_documents(ctx) == []


async def test_an_unreadable_code_tool_leaves_the_prompt_checked(tmp_path):
    ctx = _tool_ctx(tmp_path, tool_rc("rc_new", GENERAL, "t9"))  # t9 not on disk
    ctx.dispatch.queue("generality_review", _review())
    check = await check_generality(ctx, fix_item(), "rc_new", "rc_base")
    assert check.status == "clean"
    assert check.note == (
        "the code tools' text could not be read — only the prompt was checked"
    )
    (review,) = ctx.dispatch.calls_to("generality_review")
    assert review.code_tool_changes == []


# ---- the baseline audit ----


def _audit_ctx(tmp_path, prompt: str = BASE_PROMPT, *, skill_line: str | None = None):
    p = _train_project(tmp_path)
    ctx = make_ctx()
    _on_project(ctx, p)
    if skill_line is None:
        ctx.kiln.run_configs = [
            {"id": "rc_base", "name": "b", "prompt": {"prompt": prompt}}
        ]
        return ctx
    install_skill(p.root, "sk1", "depot-api", skill_md("depot-api", skill_line))
    ctx.kiln.run_configs = [skill_rc("rc_base", prompt, "sk1")]
    return ctx


def _audit_issues(ctx) -> list[dict]:
    return [m for m in ctx.kiln.memories.values() if BASELINE_AUDIT_TAG in m["tags"]]


async def test_a_clean_baseline_is_reviewed_whole_and_writes_nothing(tmp_path):
    ctx = _audit_ctx(tmp_path, skill_line=SKILL_GENERAL)
    ctx.dispatch.queue("generality_review", _review())
    audit = await audit_baseline(ctx, "rc_base")

    assert audit.status == "clean"
    (review,) = ctx.dispatch.calls_to("generality_review")
    assert review.item_id == "baseline-audit"
    # The whole text is the "added" text: no parent, every file new.
    assert review.parent_instructions == ""
    assert review.candidate_instructions == BASE_PROMPT
    (block,) = review.skill_changes
    assert block.startswith(
        "skill depot-api (sk1), SKILL.md: new file (new skill) — whole text"
    )
    assert len(review.train_expected) == len(review.train_inputs) == 2
    assert _audit_issues(ctx) == []
    (event,) = [f for k, f in ctx.log.events if k == "baseline_audit"]
    assert event["status"] == "clean"


async def test_an_inherited_copy_is_a_scrub_issue_with_no_review(tmp_path):
    ctx = _audit_ctx(
        tmp_path, skill_line="Invoice INV-58213-C is from Quillfeather Tools."
    )
    audit = await audit_baseline(ctx, "rc_base")

    assert audit.status == "leaked"
    assert ctx.dispatch.calls_to("generality_review") == []
    (finding,) = audit.findings
    assert finding.startswith('exact match: "inv-58213-c" in skill depot-api')
    (issue,) = _audit_issues(ctx)
    assert issue["id"] == audit.issue_id
    assert "issue" in issue["tags"] and "audited_rc_rc_base" in issue["tags"]
    assert issue["overview"].startswith("issue: the baseline's own text copies")
    assert "inv-58213-c" in issue["content"]
    assert "needs_eval false" in issue["content"]


async def test_the_audit_runs_once_per_baseline(tmp_path):
    ctx = _audit_ctx(
        tmp_path, skill_line="Invoice INV-58213-C is from Quillfeather Tools."
    )
    first = await audit_baseline(ctx, "rc_base")
    again = await audit_baseline(ctx, "rc_base")
    assert again.status == "known"
    assert again.issue_id == first.issue_id
    assert len(_audit_issues(ctx)) == 1
    assert ctx.dispatch.calls_to("generality_review") == []


async def test_a_review_finding_is_a_scrub_issue(tmp_path):
    ctx = _audit_ctx(tmp_path, LEAKY)
    ctx.dispatch.queue(
        "generality_review", _review("the prompt names the Halden depot")
    )
    audit = await audit_baseline(ctx, "rc_base")
    assert audit.status == "leaked"
    (issue,) = _audit_issues(ctx)
    assert "the prompt names the Halden depot" in issue["content"]


async def test_a_review_that_cannot_run_writes_nothing(tmp_path):
    ctx = _audit_ctx(tmp_path)
    ctx.dispatch.queue("generality_review", *[infra("agent_crash", "boom")] * 4)
    audit = await audit_baseline(ctx, "rc_base")
    assert audit.status == "unchecked"
    assert "did not run" in audit.note
    assert _audit_issues(ctx) == []


async def test_an_unreadable_baseline_is_not_audited(tmp_path):
    ctx = _audit_ctx(tmp_path)
    ctx.kiln.run_configs = []
    audit = await audit_baseline(ctx, "rc_base")
    assert audit.status == "unchecked"
    assert ctx.dispatch.calls_to("generality_review") == []
