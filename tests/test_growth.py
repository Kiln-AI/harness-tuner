"""The growth limit at build time: a build that adds more harness text to
its parent's than `GROWTH_LIMIT_CHARS` is refused before any train spend.
It gets ONE rebuild; a rebuild still over the limit is a dead end."""

from __future__ import annotations

import pytest
from conftest import fix_item, make_ctx, make_result
from fake_dispatcher import ok
from test_items import BASELINE, seed_measurement
from test_prompt_text import install_skill, on_disk, skill_md, skill_rc

import kiln_harness_tuner.growth as growth
from kiln_harness_tuner.growth import (
    check_growth,
    growth_findings,
    harness_size,
)
from kiln_harness_tuner.items import execute_plan_item
from kiln_harness_tuner.memory_schema import ExperimentView, MemoryRecord
from kiln_harness_tuner.prompt_text import RcText, SkillText
from kiln_harness_tuner.schemas import GeneralityReviewResult, RcBuildResult

BASE = "You are a freight depot assistant. Answer the operator's question."
GENERAL = BASE + " Check the manifest before you state a count."
LIMIT = 400  # the tests' limit, in place of the real one
# General text, with no eight-word run and no id: only its size is a fault.
LONG = BASE + " Check the manifest." * 30


@pytest.fixture(autouse=True)
def small_limit(monkeypatch):
    monkeypatch.setattr(growth, "GROWTH_LIMIT_CHARS", LIMIT)


def _ctx(*prompts: tuple[str, str]):
    ctx = make_ctx()
    ctx.kiln.run_configs = [
        {"id": "rc_base", "name": "b", "prompt": {"prompt": BASE}},
        *(
            {"id": rc_id, "name": rc_id, "prompt": {"prompt": text}}
            for rc_id, text in prompts
        ),
    ]
    return ctx


def _built(rc_id: str):
    return ok(RcBuildResult(rc_id=rc_id, build_notes="built"))


def _clean_review():
    return ok(GeneralityReviewResult(leaks=[]))


def _view(record: dict) -> ExperimentView:
    return ExperimentView.from_record(MemoryRecord.from_api(record))


def _states(ctx) -> list[str]:
    return [f["state"] for k, f in ctx.log.events if k == "item_state"]


def _rc(prompt: str, **skill_files: str) -> RcText:
    skills = (
        {"s1": SkillText(skill_id="s1", name="depot", files=dict(skill_files))}
        if skill_files
        else {}
    )
    return RcText(prompt=prompt, skill_ids=tuple(skills), skills=skills, code_tools={})


# ---- the rule ----


def test_the_real_limit_refuses_only_a_clear_case(monkeypatch):
    """The value is the owner's decision. This test holds what the module
    says about it: it is far above a full-size SKILL.md and far below the
    skill installer's cap on one bundle."""
    from kiln_harness_tuner.skill_installer.bundle import MAX_BUNDLE_BYTES

    monkeypatch.undo()
    assert 5 * 20_000 <= growth.GROWTH_LIMIT_CHARS <= MAX_BUNDLE_BYTES // 4


def test_growth_is_the_net_change_of_the_whole_text():
    parent = _rc("p" * 100, **{"SKILL.md": "s" * 1000})
    assert harness_size(parent) == 1100
    # At the limit is not over it.
    assert not growth_findings(parent, _rc("p" * 500, **{"SKILL.md": "s" * 1000}))
    assert growth_findings(parent, _rc("p" * 501, **{"SKILL.md": "s" * 1000}))
    # A skill replaced by one of the same size adds nothing.
    swapped = _rc("p" * 100, **{"SKILL.md": "t" * 1000})
    assert growth_findings(parent, swapped) == []
    # Text moved from a skill into the prompt adds nothing.
    assert growth_findings(parent, _rc("p" * 1100)) == []
    # A smaller harness is never a finding.
    assert growth_findings(parent, _rc("p" * 10)) == []


def test_the_finding_gives_the_sizes_the_limit_and_the_places():
    parent = _rc("p" * 100, **{"SKILL.md": "s" * 50})
    candidate = _rc(
        "p" * 150, **{"SKILL.md": "s" * 50, "references/table.md": "r" * 900}
    )
    (finding,) = growth_findings(parent, candidate)
    assert finding == (
        "the build adds 950 characters of harness text to its parent's (150 to"
        " 1,100); the limit for one change is 400. The largest additions: skill"
        " depot, references/table.md (+900), the prompt (+50)"
    )


async def test_a_file_the_text_readers_leave_out_counts_in_bytes(tmp_path):
    """A binary asset, or one over the text cap, is not text the model is
    shown as a whole, but it is still part of the harness: its bytes count."""
    install_skill(tmp_path, "424242000001", "depot", skill_md("depot", "Line."))
    install_skill(
        tmp_path, "424242000002", "depot", skill_md("depot", "Line."), {"t.md": "x"}
    )
    directory = tmp_path / "skills" / "424242000002 - depot" / "assets"
    directory.mkdir(parents=True)
    (directory / "table.bin").write_bytes(b"\xff\xfe" + bytes(LIMIT))
    ctx = make_ctx()
    on_disk(ctx, tmp_path)
    ctx.kiln.run_configs = [
        skill_rc("rc_base", BASE, "424242000001"),
        skill_rc("rc_new", BASE, "424242000002"),
    ]
    check = await check_growth(ctx, "rc_new", "rc_base")
    assert check.status == "over"
    assert "skill depot, assets/table.bin (+402)" in check.findings[0]


async def test_a_build_over_the_limit_is_not_sent_to_the_paid_review():
    ctx = _ctx(("rc_new", LONG), ("rc_new2", GENERAL))
    seed_measurement(ctx.kiln, "rc_new2")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    ctx.dispatch.queue("generality_review", _clean_review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    (review,) = ctx.dispatch.calls_to("generality_review")
    assert review.candidate_rc_id == "rc_new2"
    events = [f for k, f in ctx.log.events if k == "generality"]
    assert events[0]["status"] == "unchecked"
    assert "the growth limit already refuses this build" in events[0]["line"]


async def test_text_that_cannot_be_read_is_unchecked_not_within(tmp_path):
    install_skill(tmp_path, "424242000001", "depot", skill_md("depot", "Line."))
    ctx = make_ctx()
    on_disk(ctx, tmp_path)
    ctx.kiln.run_configs = [
        skill_rc("rc_base", BASE, "424242000001"),
        skill_rc("rc_new", LONG, "424242000009"),  # a skill that is not on disk
    ]
    check = await check_growth(ctx, "rc_new", "rc_base")
    assert check.status == "unchecked"
    assert check.note == (
        "not checked — the candidate's skills' text could not be read"
    )


# ---- the item path ----


async def test_a_build_within_the_limit_is_measured_with_no_line():
    ctx = _ctx(("rc_new", GENERAL))
    seed_measurement(ctx.kiln, "rc_new")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    ctx.dispatch.queue("generality_review", _clean_review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    (event,) = [f for k, f in ctx.log.events if k == "growth"]
    assert event["status"] == "within"
    assert event["added_chars"] == len(GENERAL) - len(BASE)
    assert event["line"] == "growth rc_new: within"
    details = [f["detail"] for k, f in ctx.log.events if k == "item_detail"]
    assert not [d for d in details if d.startswith("growth:")]
    assert _view(ctx.kiln.memories[outcome.experiment_memory_id]).growth is None


async def test_a_build_over_the_limit_gets_one_rebuild_with_the_finding():
    ctx = _ctx(("rc_new", LONG), ("rc_new2", GENERAL))
    seed_measurement(ctx.kiln, "rc_new2")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    ctx.dispatch.queue("generality_review", _clean_review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert outcome.rc_id == "rc_new2"  # the rebuild is what was measured
    first, rebuild = ctx.dispatch.calls_to("create_experiment_rc")
    assert first.growth_findings == []
    assert rebuild.rejected_rc_id == "rc_new"
    assert rebuild.generality_findings == [] and rebuild.judge_copy_findings == []
    (finding,) = rebuild.growth_findings
    added = len(LONG) - len(BASE)
    assert finding.startswith(f"the build adds {added} characters of harness text")
    assert "the limit for one change is 400" in finding
    assert finding.endswith(f"The largest additions: the prompt (+{added})")
    # What the run shows: an event with a line, and a detail line.
    events = [f for k, f in ctx.log.events if k == "growth"]
    assert [e["status"] for e in events] == ["over", "within"]
    assert events[0]["line"].startswith("growth rc_new: over — the build adds")
    details = [f["detail"] for k, f in ctx.log.events if k == "item_detail"]
    assert any(d.startswith("growth: over — the build adds") for d in details)
    # The finding outlives the verdict, on the record the planner reads.
    view = _view(ctx.kiln.memories[outcome.experiment_memory_id])
    assert view.growth is not None
    assert view.growth.startswith("first build rc_new is over the growth limit")
    assert "rebuilt once as rc_new2 — within the limit" in view.growth
    assert view.rc_id == "rc_new2"


async def test_a_rebuild_still_over_the_limit_is_a_dead_end_before_any_spend():
    ctx = _ctx(("rc_new", LONG), ("rc_new2", LONG + " More."))
    seed_measurement(ctx.kiln, "rc_new2")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "dead_end"
    assert outcome.rc_id == "rc_new2"
    assert ctx.dispatch.calls_to("analyze_experiment_results") == []
    assert "measuring" not in _states(ctx)
    assert not [f for k, f in ctx.log.events if k == "queue_drawn"]
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    assert record["overview"].endswith(
        "— DEAD END (growth: still over the growth limit after one rebuild)"
    )
    content = record["content"]
    assert "growth: first build rc_new is over the growth limit" in content
    assert "the rebuild rc_new2 is still over the limit" in content


async def test_a_reused_run_config_over_the_limit_is_not_rebuilt():
    ctx = _ctx(("rc_old", LONG))
    item = fix_item()
    item.work.existing_rc_id = "rc_old"
    outcome = await execute_plan_item(ctx, item, BASELINE, ["rc_base"], 1)

    assert outcome.status == "dead_end"
    assert ctx.dispatch.calls_to("create_experiment_rc") == []
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    assert (
        "growth: the reused run config is over the growth limit" in (record["overview"])
    )
    assert "a reused run config is not rebuilt" in record["content"]


def test_the_terminal_app_names_the_finding_that_forced_the_rebuild():
    from kiln_harness_tuner.tui.model.fold import CHECK_FINDINGS
    from kiln_harness_tuner.tui.model.words import FINDING_LOG, FINDINGS

    assert CHECK_FINDINGS["growth"] == "over"
    assert set(FINDINGS) == set(FINDING_LOG) == set(CHECK_FINDINGS)
