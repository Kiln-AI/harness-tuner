"""Code probes, the probe phase and the reconnaissance battery.

A `check` on a probe item makes it code: fork the parent, attach a
throwaway surface, drive train items read from the project on disk, count
in the traces, answer mechanically — no agent. A `probe_phase` plan runs
its probes first and the planner is called again with the answers. The
setup battery runs the standing code probes on the baseline as wave 0.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import fix_item, make_ctx, make_plan
from fake_dispatcher import ok
from fake_kiln import make_eval, make_probe_run, make_run_config
from test_orchestrator import seed_active_eval, seed_baseline

from kiln_harness_tuner.items import execute_plan_item
from kiln_harness_tuner.memory_schema import PROBE_TAG
from kiln_harness_tuner.orchestrator import orchestrate
from kiln_harness_tuner.probes import (
    DriveSet,
    count_metric,
    decide,
    load_train_inputs,
    recon_items,
    select_inputs,
)
from kiln_harness_tuner.schemas import (
    PROVIDER_LANE,
    BaselineRef,
    BatchPlan,
    ProbeCheck,
    ProbeItem,
    ProbeResult,
    TargetBlock,
)

pytestmark = pytest.mark.anyio


def write_input(root: Path, item_id: str, text: str, tags: list[str]) -> None:
    d = root / "tasks" / "t1 - Fake task" / "eval_inputs" / f"{item_id} - x"
    d.mkdir(parents=True, exist_ok=True)
    (d / "eval_input.kiln").write_text(
        json.dumps(
            {
                "id": item_id,
                "data": json.dumps(
                    {"type": "single_turn", "user_message": {"text": text}}
                ),
                "tags": tags,
            }
        ),
        encoding="utf-8",
    )


def project_with_train_inputs(tmp_path: Path, n: int = 3) -> Path:
    for i in range(n):
        write_input(
            tmp_path, f"in_{i}", json.dumps({"q": "x" * (10 * (i + 1))}), ["train"]
        )
    write_input(tmp_path, "in_val", json.dumps({"q": "v"}), ["val"])
    return tmp_path


def code_probe(check: ProbeCheck, item_id: str = "w1-1", drives: int = 3) -> ProbeItem:
    return ProbeItem(
        id=item_id,
        type="probe",
        question="Does the model call an attached tool under json_schema?",
        method="code",
        reason="gate the skill arm",
        for_idea="reference cards into a skill",
        max_drives=drives,
        check=check,
    )


def probe_ctx(tmp_path: Path):
    ctx = make_ctx()
    ctx.target = TargetBlock(
        project_path=str(project_with_train_inputs(tmp_path)),
        project_id="p1",
        task_id="t1",
        kiln_host="http://h",
    )
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    ctx.kiln.specs = [{"id": "spec_1", "eval_id": "eval_1", "status": "active"}]
    rc = make_run_config("rc_base")
    rc["run_config_properties"] = {
        "model_name": "m",
        "model_provider_name": "p",
        "structured_output_mode": "json_schema",
        "thinking_level": "low",
        "tools_config": {"tools": []},
    }
    ctx.kiln.run_configs = [rc]
    # Level probes use only levels the server offers.
    ctx.kiln.available_models = [
        {
            "provider_id": "p",
            "models": [
                {
                    "id": "m",
                    "available_thinking_levels": {
                        "Off/None": "none",
                        "Low": "low",
                        "Medium": "medium",
                        "High": "high",
                    },
                }
            ],
        }
    ]
    return ctx


# ------------------------------------------------------------- schemas


def test_probe_phase_plan_carries_only_probes():
    probe = code_probe(ProbeCheck(attach="probe_tool", count="tool_calls"))
    plan = BatchPlan.model_validate(
        {
            "items": [probe.model_dump()],
            "rejected_summary": "w1: probes first",
            "open_questions": [],
            "probe_phase": True,
        }
    )
    assert plan.probe_phase and isinstance(plan.items[0], ProbeItem)
    assert (
        plan.items[0].check is not None and plan.items[0].check.attach == "probe_tool"
    )
    with pytest.raises(ValueError, match="ONLY probe"):
        make_plan(fix_item("w1-1"), probe).model_copy(update={"probe_phase": True})
        BatchPlan(
            items=[fix_item("w1-1"), probe],
            rejected_summary="x",
            open_questions=[],
            probe_phase=True,
        )
    with pytest.raises(ValueError, match="at least one"):
        BatchPlan(items=[], rejected_summary="x", open_questions=[], probe_phase=True)


def test_probe_check_rules_default_by_count():
    assert ProbeCheck(count="tool_calls").effective_rule == "any"
    assert ProbeCheck(count="json_valid").effective_rule == "all"
    assert ProbeCheck(count="json_valid", rule="any").effective_rule == "any"


# ------------------------------------------------------------- counting


def test_count_metric_reads_the_trace_and_the_output():
    run = make_probe_run(
        tool_calls=2, skill_reads=1, reasoning="abc", output='{"a": 1}'
    )
    assert count_metric(run, "tool_calls") == 3.0  # skill reads are tool calls too
    assert count_metric(run, "skill_reads") == 1.0
    assert count_metric(run, "reasoning_chars") == 3.0
    assert count_metric(run, "output_chars") == 8.0
    assert count_metric(run, "json_valid") == 1.0
    assert count_metric(make_probe_run(output="not json"), "json_valid") == 0.0
    assert count_metric({}, "tool_calls") == 0.0


def test_decide_any_all_and_compare():
    any_check = ProbeCheck(count="tool_calls")
    assert decide(any_check, DriveSet([0, 0, 1], ["a", "b", "c"], []), None)[0] == "yes"
    assert decide(any_check, DriveSet([0, 0, 0], ["a", "b", "c"], []), None)[0] == "no"
    assert decide(any_check, DriveSet([], [], ["boom"]), None)[0] == "unclear"
    all_check = ProbeCheck(count="output_chars", rule="all")
    assert decide(all_check, DriveSet([5, 0], ["a", "b"], []), None)[0] == "no"
    assert decide(all_check, DriveSet([5, 9], ["a", "b"], []), None)[0] == "yes"
    cmp = ProbeCheck(
        overrides={"structured_output_mode": "json_instructions"},
        count="reasoning_chars",
        compare_to_parent=True,
    )
    fork, parent = (
        DriveSet([300, 320], ["a", "b"], []),
        DriveSet([100, 110], ["a", "b"], []),
    )
    answer, sentence = decide(cmp, fork, parent)
    assert answer == "yes" and "moved" in sentence
    assert decide(cmp, DriveSet([100, 120], ["a", "b"], []), parent)[0] == "no"
    assert (
        decide(cmp, DriveSet([0, 0], ["a", "b"], []), DriveSet([0, 0], ["a", "b"], []))[
            0
        ]
        == "no"
    )
    assert decide(cmp, DriveSet([300], ["a"], []), parent)[0] == "unclear"


def test_train_inputs_come_from_disk_by_split_tag(tmp_path: Path):
    project_with_train_inputs(tmp_path)
    inputs = load_train_inputs(str(tmp_path), "t1", "tag::train")
    assert [i.id for i in inputs] == ["in_0", "in_1", "in_2"]
    assert inputs[0].structured == {"q": "x" * 10} and inputs[0].plaintext is None
    assert [i.id for i in select_inputs(inputs, 2, "longest")] == ["in_2", "in_1"]
    assert load_train_inputs(str(tmp_path), "t1", "tag::nope") == []


# ---------------------------------------------------------- code probe


async def test_code_probe_forks_drives_counts_and_answers_without_an_agent(tmp_path):
    ctx = probe_ctx(tmp_path)
    ctx.kiln.run_responses = [
        make_probe_run(tool_calls=1, cost=0.002),
        make_probe_run(tool_calls=0, cost=0.002),
        make_probe_run(tool_calls=2, cost=0.002),
    ]
    item = code_probe(ProbeCheck(attach="probe_tool", count="tool_calls"))
    outcome = await execute_plan_item(
        ctx, item, BaselineRef(rc_id="rc_base"), ["rc_base"], wave=1
    )
    assert outcome.status == "completed"
    assert outcome.probe is not None and outcome.probe.answer == "yes"
    assert "tool_calls > 0 on 2/3 drives" in outcome.probe.evidence
    assert outcome.probe.drives == 3 and outcome.probe.ephemeral_rc_ids == [
        "rc_probe_1"
    ]
    # no agent was dispatched
    assert not ctx.dispatch.calls_to("probe")
    # the surface: code trust granted, the throwaway tool created once, attached
    assert ctx.kiln.code_trust_posts == 1 and len(ctx.kiln.code_tools) == 1
    fork = ctx.kiln.created_run_configs[0]
    assert fork["name"].startswith("PROBE w1-1 - ")
    # Stamped as the loop's own objects; the fork sits under its parent.
    assert fork["provenance"] == {
        "notes": fork["description"],
        "derived_from_ids": ["rc_base"],
        "origin": "agent",
    }
    assert ctx.kiln.code_tools[0]["provenance"]["origin"] == "agent"
    assert ctx.kiln.code_tools[0]["provenance"]["derived_from_ids"] == []
    assert fork["run_config_properties"]["tools_config"]["tools"] == [
        "kiln_tool::code::tool_1"
    ]
    assert fork["run_config_properties"]["structured_output_mode"] == "json_schema"
    # the drives: train items, structured input, pinned to the fork, tagged
    assert len(ctx.kiln.run_posts) == 3
    post = ctx.kiln.run_posts[0]
    assert post["task_run_config_id"] == "rc_probe_1"
    assert post["structured_input"] == {"q": "x" * 10}
    assert "tuner_probe" in post["tags"]
    # provider-lane spend from the drives' usage cost
    spend = [
        f for k, f in ctx.log.events if k == "spend" and f.get("source") == "probe"
    ]
    assert spend[0]["lane"] == PROVIDER_LANE and spend[0]["cost_usd"] == pytest.approx(
        0.006
    )
    # the answer reaches memory and the planner's running list
    lessons = [m for m in ctx.kiln.memories.values() if PROBE_TAG in m["tags"]]
    assert len(lessons) == 1 and lessons[0]["overview"].endswith(" → YES")
    assert ctx.probe_answers[-1].item_id == "w1-1"
    assert ctx.probe_answers[-1].answer == "yes"
    assert ctx.probe_answers[-1].for_idea == "reference cards into a skill"


async def test_code_probe_with_overrides_and_parent_comparison(tmp_path):
    ctx = probe_ctx(tmp_path)
    ctx.kiln.run_responses = [
        make_probe_run(reasoning="r" * 900),
        make_probe_run(reasoning="r" * 1000),
        make_probe_run(reasoning="r" * 100),  # parent drives
        make_probe_run(reasoning="r" * 120),
    ]
    item = code_probe(
        ProbeCheck(
            overrides={"structured_output_mode": "json_instructions"},
            count="reasoning_chars",
            compare_to_parent=True,
        ),
        drives=2,
    )
    outcome = await execute_plan_item(
        ctx, item, BaselineRef(rc_id="rc_base"), ["rc_base"], wave=1
    )
    assert outcome.probe is not None and outcome.probe.answer == "yes"
    assert outcome.probe.drives == 4
    fork = ctx.kiln.created_run_configs[0]["run_config_properties"]
    assert fork["structured_output_mode"] == "json_instructions"
    assert "tools_config" in fork
    assert ctx.kiln.code_trust_posts == 0  # nothing attached
    pinned = [p["task_run_config_id"] for p in ctx.kiln.run_posts]
    assert pinned == ["rc_probe_1", "rc_probe_1", "rc_base", "rc_base"]
    assert "parent rc_base on the same items" in outcome.probe.evidence


async def test_code_probe_attaches_the_throwaway_skill_once(tmp_path):
    ctx = probe_ctx(tmp_path)
    ctx.kiln.run_responses = [make_probe_run(skill_reads=1)] * 2
    item = code_probe(ProbeCheck(attach="probe_skill", count="skill_reads"), drives=2)
    outcome = await execute_plan_item(
        ctx, item, BaselineRef(rc_id="rc_base"), ["rc_base"], wave=1
    )
    assert outcome.probe is not None and outcome.probe.answer == "yes"
    assert len(ctx.kiln.skills) == 1 and ctx.kiln.skills[0]["name"] == "probe-checklist"
    assert ctx.kiln.skills[0]["provenance"]["origin"] == "agent"
    fork = ctx.kiln.created_run_configs[0]["run_config_properties"]
    assert fork["tools_config"]["tools"] == ["kiln_tool::skill::skill_1"]
    # a second skill probe reuses the skill
    ctx.kiln.run_responses = [make_probe_run(skill_reads=0)] * 2
    await execute_plan_item(
        ctx,
        code_probe(ProbeCheck(attach="probe_skill", count="skill_reads"), "w1-2", 2),
        BaselineRef(rc_id="rc_base"),
        ["rc_base"],
        wave=1,
    )
    assert len(ctx.kiln.skills) == 1


async def test_code_probe_without_train_inputs_is_unclear_not_infra(tmp_path):
    ctx = probe_ctx(tmp_path)
    ctx.target = ctx.target.model_copy(update={"project_path": str(tmp_path / "empty")})
    outcome = await execute_plan_item(
        ctx,
        code_probe(ProbeCheck(count="json_valid")),
        BaselineRef(rc_id="rc_base"),
        ["rc_base"],
        wave=1,
    )
    assert outcome.status == "completed"
    assert outcome.probe is not None and outcome.probe.answer == "unclear"
    assert "no drivable eval inputs" in outcome.probe.evidence
    assert not ctx.kiln.run_posts and not ctx.kiln.created_run_configs


async def test_code_probe_refuses_a_fork_outside_the_model_policy(tmp_path):
    """The policy binds code too — a probe fork drives the task on
    its model, so a model the policy refuses is never forked or driven."""
    from kiln_harness_tuner.model_policy import ModelPolicy

    ctx = probe_ctx(tmp_path)
    ctx.model_policy = ModelPolicy.from_json(
        {"allow": [{"provider": "openai", "model": "*"}]}
    )
    outcome = await execute_plan_item(
        ctx,
        code_probe(ProbeCheck(count="json_valid")),
        BaselineRef(rc_id="rc_base"),
        ["rc_base"],
        wave=1,
    )
    assert outcome.probe is not None and outcome.probe.answer == "unclear"
    assert "outside the model policy" in outcome.probe.evidence
    assert not ctx.kiln.run_posts and not ctx.kiln.created_run_configs


async def test_the_probe_agent_is_handed_train_inputs_and_nothing_else(tmp_path):
    """The agents read no project file, so code hands the probe
    its train items — and only train items."""
    from kiln_harness_tuner.schemas import ProbeResult

    ctx = probe_ctx(tmp_path)
    evals = tmp_path / "tasks" / "t1 - Fake task" / "evals" / "eval_1 - E"
    evals.mkdir(parents=True)
    splits = {
        n: {"source": "eval_input", "filter_id": f"tag::{n}"}
        for n in ("train", "val", "test")
    }
    (evals / "eval.kiln").write_text(
        json.dumps({"id": "eval_1", "splits": splits}), encoding="utf-8"
    )
    item = code_probe(ProbeCheck(count="json_valid")).model_copy(
        update={"check": None, "method": "agent reads the traces"}
    )
    ctx.dispatch.queue(
        "probe", ok(ProbeResult(answer="yes", evidence="2/3 drives", drives=3))
    )
    await execute_plan_item(ctx, item, BaselineRef(rc_id="rc_base"), ["rc_base"], 1)
    (req,) = ctx.dispatch.calls_to("probe")
    assert sorted(i.id for i in req.train_inputs) == ["in_0", "in_1", "in_2"]
    assert all(i.structured_input is not None for i in req.train_inputs)


async def test_code_probe_drive_errors_are_counted_not_fatal(tmp_path):
    ctx = probe_ctx(tmp_path)
    ctx.kiln.run_responses = [
        "http500",
        make_probe_run(output=""),
        make_probe_run(output="x"),
    ]
    outcome = await execute_plan_item(
        ctx,
        code_probe(ProbeCheck(count="output_chars", rule="all")),
        BaselineRef(rc_id="rc_base"),
        ["rc_base"],
        wave=1,
    )
    assert outcome.probe is not None and outcome.probe.answer == "no"
    assert "1 drive error(s)" in outcome.probe.evidence and outcome.probe.drives == 2


# ------------------------------------------------------------ the loop


def _probe_phase_plan(*probes: ProbeItem) -> BatchPlan:
    return BatchPlan(
        items=list(probes),
        rejected_summary="w1: probes first — the skill arm rests on a tool call",
        open_questions=[],
        probe_phase=True,
    )


async def test_probe_phase_runs_its_probes_then_replans_with_the_answers():
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    agent_probe = ProbeItem(
        id="w1-p1",
        type="probe",
        question="Does the card change when the skill is read?",
        method="fork, attach, read six traces",
        reason="gate the wildcard",
        for_idea="progressive disclosure",
    )
    ctx.dispatch.queue(
        "batch_plan",
        ok(_probe_phase_plan(agent_probe)),
        ok(make_plan(loop_exit="the probe said no; nothing worth building")),
    )
    ctx.dispatch.queue(
        "probe",
        ok(ProbeResult(answer="no", evidence="0 reads in 3 of 3 drives", drives=3)),
    )
    assert await orchestrate(ctx, "raise the evals", timeout_mins=180) == (
        "the probe said no; nothing worth building"
    )
    plans = ctx.dispatch.calls_to("batch_plan")
    assert [p.phase for p in plans] == ["plan", "replan"]
    assert plans[0].probe_results == []
    assert plans[1].prior_rejected_summary.startswith("w1: probes first")
    assert [a.item_id for a in plans[1].probe_results] == ["w1-p1"]
    assert plans[1].probe_results[0].answer == "no"
    assert plans[1].probe_results[0].for_idea == "progressive disclosure"
    starts = [f for k, f in ctx.log.events if k == "wave_start"]
    assert len(starts) == 1 and starts[0]["probe_phase"] is True
    assert starts[0]["plan_headline"].startswith("probe phase — 1 probe")
    ends = [f for k, f in ctx.log.events if k == "wave_end"]
    assert ends == [{"wave": 1, "probe_phase": True}]
    lessons = [m for m in ctx.kiln.memories.values() if PROBE_TAG in m["tags"]]
    assert len(lessons) == 1


async def test_recon_battery_runs_at_setup_as_wave_zero(tmp_path):
    ctx = probe_ctx(tmp_path)
    ctx.recon = True
    # A baseline with a tool and a skill attached: the tool and skill probes run.
    ctx.kiln.run_configs[0]["run_config_properties"]["tools_config"] = {
        "tools": ["kiln_tool::code::t1", "kiln_tool::skill::s1"]
    }
    seed_baseline(ctx.kiln)
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    assert await orchestrate(ctx, "", timeout_mins=180) == "done"
    starts = [f for k, f in ctx.log.events if k == "wave_start"]
    assert starts and starts[0]["wave"] == 0 and starts[0]["probe_phase"] is True
    assert starts[0]["plan_headline"].startswith("reconnaissance — 5 probes")
    ids = [i["item_id"] for i in starts[0]["items"]]
    assert ids == ["r0-1", "r0-2", "r0-3", "r0-6", "r0-7"]
    # every answer reached the planner's first call and memory
    plan_input = ctx.dispatch.calls_to("batch_plan")[0]
    assert sorted(a.item_id for a in plan_input.probe_results) == ids
    lessons = [m for m in ctx.kiln.memories.values() if PROBE_TAG in m["tags"]]
    assert len(lessons) == 5
    # the alternate-mode probe forked with json_instructions; no thinking probe
    # (a thinking level is a screened variant)
    forks = {rc["name"].split(" - ")[0]: rc for rc in ctx.kiln.created_run_configs}
    assert forks["PROBE r0-3"]["run_config_properties"]["structured_output_mode"] == (
        "json_instructions"
    )
    assert "PROBE r0-4" not in forks


def test_recon_items_follow_the_baseline_shape():
    tools = {"tools": ["kiln_tool::code::t1", "kiln_tool::skill::s1"]}
    items = recon_items(
        {
            "structured_output_mode": "json_schema",
            "thinking_level": "low",
            "tools_config": tools,
        }
    )
    assert [i.id for i in items] == ["r0-1", "r0-2", "r0-3", "r0-6", "r0-7"]
    plain = recon_items({"thinking_level": "high"})
    assert [i.id for i in plain] == ["r0-7"]
    assert all(i.check is not None for i in items)


def test_recon_skips_tool_and_skill_probes_on_a_bare_baseline():
    """Recon does not probe tools and skills on a baseline that has neither."""
    bare = recon_items(
        {"structured_output_mode": "json_schema", "tools_config": {"tools": []}}
    )
    counts = {i.check.count for i in bare if i.check}
    assert "tool_calls" not in counts and "skill_reads" not in counts
    only_skill = recon_items({"tools_config": {"tools": ["kiln_tool::skill::s1"]}})
    assert [i.id for i in only_skill] == ["r0-2", "r0-7"]


def test_recon_never_probes_a_thinking_level():
    """A thinking level is a candidate variant, screened in code
    — recon keeps to mechanism questions."""
    for props in (
        {"structured_output_mode": "json_schema", "thinking_level": "medium"},
        {"thinking_level": "low"},
    ):
        for item in recon_items(props):
            assert "thinking_level" not in (item.check.overrides if item.check else {})


@pytest.mark.parametrize(
    "overrides",
    [
        {"thinking_level": "none"},
        {"model_name": "fast_m"},
        {"model_provider_name": "p"},
        {"temperature": 0.2},
        {"top_p": 0.5},
    ],
)
def test_a_variant_is_screened_never_probed(overrides):
    with pytest.raises(ValueError, match="screened in code"):
        ProbeCheck(overrides=overrides, count="json_valid")
    # A mechanism question about the output mode stays a probe.
    ProbeCheck(overrides={"structured_output_mode": "json_mode"}, count="json_valid")


async def test_under_speed_a_code_probe_drives_no_parent_and_times_nothing(
    tmp_path,
):
    """A probe answers a mechanism question. Speed is the
    engine's, so a code probe no longer drives the parent beside the fork
    (unless the check compares to the parent) and has no speed field."""
    ctx = probe_ctx(tmp_path)
    ctx.objective = "speed"
    check = ProbeCheck(
        overrides={"structured_output_mode": "json_mode"}, count="json_valid"
    )
    outcome = await execute_plan_item(
        ctx, code_probe(check), BaselineRef(rc_id="rc_base"), ["rc_base"], wave=1
    )
    assert outcome.probe is not None
    assert "latency" not in type(outcome.probe).model_fields
    assert all(p["task_run_config_id"] != "rc_base" for p in ctx.kiln.run_posts)


async def test_recon_is_off_unless_asked():
    ctx = make_ctx()
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    assert await orchestrate(ctx, "", timeout_mins=180) == "done"
    assert not [f for k, f in ctx.log.events if k == "wave_start"]
    assert ctx.dispatch.calls_to("batch_plan")[0].probe_results == []
