"""The reference check at build time: a build whose prompt or
skills name a skill or a tool it does not attach. Code only. `warn` records
the findings and measures; `refuse` gives one rebuild, then a dead end;
`off` skips the check. A code tool's allowlist is never compared: Kiln lets
a code tool call an allowlisted tool that is not attached."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import fix_item, make_ctx, make_result
from fake_dispatcher import ok
from fake_kiln import make_eval, make_eval_config, make_run
from test_items import BASELINE
from test_prompt_text import install_skill, on_disk, skill_md

from kiln_harness_tuner.cli import build_parser
from kiln_harness_tuner.items import execute_plan_item
from kiln_harness_tuner.memory_schema import ExperimentView, MemoryRecord
from kiln_harness_tuner.prompt_text import CodeToolText, RcText, SkillText
from kiln_harness_tuner.reference_check import (
    ProjectSkills,
    check_references,
    names_in,
    project_skills,
    reference_findings,
)
from kiln_harness_tuner.schemas import GeneralityReviewResult, RcBuildResult

BASE = "You are a freight depot assistant. Answer the operator's question."
JUDGE = "Score PASS when the reply answers the operator's question."
SQL = "kiln_tool::code::t_sql"
API = "kiln_tool::code::t_api"
WRAP = "kiln_tool::code::t_wrap"
WORLD = "kiln_tool::world::424242500001::append_note"


# ---- the matcher ----


class TestNames:
    def test_a_versioned_name_is_not_found_inside_its_successor(self):
        text = "Load the `inventory-read-v2` skill first."
        assert names_in(text, ["inventory-read"]) == []
        assert names_in(text, ["inventory-read-v2"]) == ["inventory-read-v2"]

    def test_a_successor_is_not_found_inside_the_old_name(self):
        assert names_in("load inventory-read now", ["inventory-read-v2"]) == []

    def test_a_tool_name_matches_on_token_boundaries(self):
        assert names_in("Call sql_query once.", ["sql_query"]) == ["sql_query"]
        assert names_in("Call sql_query_v2 once.", ["sql_query"]) == []
        assert names_in("Call my_sql_query once.", ["sql_query"]) == []
        assert names_in("(see sql_query).", ["sql_query"]) == ["sql_query"]

    def test_a_plain_word_name_needs_a_marker(self):
        # `billing` is a skill name and an English word.
        assert names_in("Answer billing questions politely.", ["billing"]) == []
        assert names_in("Load the `billing` skill.", ["billing"]) == ["billing"]
        assert names_in("Load the billing skill.", ["billing"]) == ["billing"]
        assert names_in("Use billing skills for that.", ["billing"]) == ["billing"]
        assert names_in("Call the lookup tool.", ["lookup"]) == ["lookup"]
        assert names_in('Call "lookup" first.', ["lookup"]) == ["lookup"]
        assert names_in("Call 'lookup' first.", ["lookup"]) == ["lookup"]

    def test_a_json_key_is_not_a_reference(self):
        assert names_in('{"query": "SELECT 1"}', ["query"]) == []
        assert names_in("{'query' : 1}", ["query"]) == []

    def test_the_word_tool_before_a_name_is_not_a_reference(self):
        # "the tool result" does not name a dropped tool called `result`.
        assert names_in("Read the tool result first.", ["result"]) == []
        assert names_in("Use skill billing for that.", ["billing"]) == []
        assert names_in("Read the result tool first.", ["result"]) == ["result"]

    def test_a_distinctive_name_needs_no_marker(self):
        assert names_in("Read manifest-read first.", ["manifest-read"]) == [
            "manifest-read"
        ]

    def test_a_name_outside_the_token_characters_still_matches(self):
        assert names_in("Call depot.get_slot now.", ["depot.get_slot"]) == [
            "depot.get_slot"
        ]
        assert names_in("Call depot.get_slot_v2 now.", ["depot.get_slot"]) == []

    def test_many_names_keep_their_order(self):
        text = "Load `billing`, then inventory-read, then call sql_query."
        names = ["sql_query", "billing", "returns", "inventory-read"]
        assert names_in(text, names) == ["sql_query", "billing", "inventory-read"]


# ---- the two kinds, on read text ----


def _skill(skill_id: str, name: str, *lines: str) -> SkillText:
    return SkillText(skill_id=skill_id, name=name, files={"SKILL.md": "\n".join(lines)})


def _tool(tool_id: str, function: str) -> CodeToolText:
    return CodeToolText(
        tool_id=tool_id, name=function, files={}, function_name=function
    )


def _rc(
    prompt: str,
    skills: tuple[SkillText, ...] = (),
    tools: tuple[CodeToolText, ...] = (),
    other: tuple[str, ...] = (),
) -> RcText:
    skill_ids = tuple(s.skill_id for s in skills)
    tool_ids = tuple(t.tool_id for t in tools)
    return RcText(
        prompt=prompt,
        skill_ids=skill_ids,
        skills={s.skill_id: s for s in skills},
        code_tool_ids=tool_ids,
        code_tools={t.tool_id: t for t in tools},
        tool_ids=(
            *(f"kiln_tool::code::{i}" for i in tool_ids),
            *other,
            *(f"kiln_tool::skill::{i}" for i in skill_ids),
        ),
    )


SQL_TOOL = _tool("t_sql", "sql_query")
API_TOOL = _tool("t_api", "kestrel_api")


class TestFindings:
    def test_a_skill_named_and_not_attached_is_found(self):
        v2 = _skill("sk2", "inventory-read-v2", "Read stock levels.")
        flow = _skill("sk3", "pallet-flow", "First load `inventory-read`.")
        cand = _rc(BASE, (v2, flow))
        skills = {
            "sk1": "inventory-read",
            "sk2": "inventory-read-v2",
            "sk3": "pallet-flow",
        }
        (finding,) = reference_findings(cand, None, skills)
        assert finding.startswith(
            "skill `inventory-read` is named in skill pallet-flow, SKILL.md"
            " but is not attached"
        )
        assert "(attached: `inventory-read-v2`)" in finding

    def test_a_skill_only_the_parent_attached_counts_too(self):
        old = _skill("sk_old", "dock-schedule", "Book dock slots.")
        parent = _rc(BASE, (old,))
        cand = _rc(f"{BASE} Use dock-schedule for slots.")
        (finding,) = reference_findings(cand, parent, {})
        assert finding.startswith("skill `dock-schedule` is named in the prompt")

    def test_a_dangling_reference_the_parent_has_is_not_the_builds(self):
        # The parent's attached skill points at `returns-flow`, which the
        # parent does not attach either. A prompt-only change keeps it.
        flow = _skill("sk1", "pallet-flow", "See also the `returns-flow` skill.")
        skills = {"sk1": "pallet-flow", "sk2": "returns-flow"}
        parent = _rc(BASE, (flow,))
        cand = _rc(f"{BASE} Be brief.", (flow,))
        assert reference_findings(cand, parent, skills) == []

    def test_a_dangling_reference_the_build_adds_is_found(self):
        flow = _skill("sk1", "pallet-flow", "See also the `returns-flow` skill.")
        skills = {"sk1": "pallet-flow", "sk2": "returns-flow"}
        parent = _rc(BASE, (flow,))
        cand = _rc(f"{BASE} Load returns-flow for returns.", (flow,))
        (finding,) = reference_findings(cand, parent, skills)
        # Only the new place is reported; the inherited one is the parent's.
        assert finding == (
            "skill `returns-flow` is named in the prompt but is not attached"
        )

    def test_a_skill_the_build_detached_is_found_where_the_parent_named_it(self):
        read = _skill("sk2", "inventory-read", "Read stock levels.")
        skills = {"sk2": "inventory-read"}
        parent = _rc(f"{BASE} Load inventory-read.", (read,))
        cand = _rc(f"{BASE} Load inventory-read.")
        (finding,) = reference_findings(cand, parent, skills)
        assert finding.startswith("skill `inventory-read` is named in the prompt")

    def test_an_archived_skill_says_so(self):
        project = ProjectSkills(
            names={"sk1": "dock-schedule"}, archived=frozenset({"dock-schedule"})
        )
        cand = _rc(f"{BASE} Use dock-schedule for slots.")
        (finding,) = reference_findings(cand, None, project)
        assert finding.startswith("skill `dock-schedule` (archived) is named in")

    def test_a_plain_tool_name_after_the_word_tool_is_not_found(self):
        parent = _rc(BASE, tools=(_tool("t_res", "result"),))
        cand = _rc(f'{BASE} Read the tool result. Send {{"result": 1}}.')
        assert reference_findings(cand, parent, {}) == []

    def test_an_attached_skill_is_not_a_finding(self):
        read = _skill("sk1", "inventory-read", "Read stock levels.")
        cand = _rc(f"{BASE} Load inventory-read.", (read,))
        assert reference_findings(cand, None, {"sk1": "inventory-read"}) == []

    def test_a_word_that_is_no_known_name_is_never_a_finding(self):
        cand = _rc(f"{BASE} Use the `cargo-ledger` skill and call `lookup_all`.")
        assert reference_findings(cand, _rc(BASE), {"sk1": "inventory-read"}) == []

    def test_a_tool_the_build_detached_and_a_skill_still_names_is_found(self):
        flow = _skill("sk1", "manifest-flow", "Call `sql_query` to list the manifest.")
        parent = _rc(BASE, (flow,), (SQL_TOOL, API_TOOL))
        cand = _rc(BASE, (flow,), (API_TOOL,))
        (finding,) = reference_findings(cand, parent, {"sk1": "manifest-flow"})
        assert finding == (
            "tool `sql_query` is named in skill manifest-flow, SKILL.md; the"
            " parent attaches it and this run config does not"
        )

    def test_a_world_tool_is_named_by_its_function(self):
        parent = _rc(BASE, other=(WORLD,))
        cand = _rc(f"{BASE} Log each answer with append_note.")
        (finding,) = reference_findings(cand, parent, {})
        assert finding.startswith("tool `append_note` is named in the prompt")

    def test_a_kept_tool_is_not_a_finding(self):
        parent = _rc(BASE, tools=(SQL_TOOL,))
        cand = _rc(f"{BASE} Call sql_query.", tools=(SQL_TOOL,))
        assert reference_findings(cand, parent, {}) == []

    def test_a_renamed_tool_shows_its_successor(self):
        parent = _rc(BASE, tools=(SQL_TOOL,))
        cand = _rc(f"{BASE} Call sql_query.", tools=(_tool("t_v2", "sql_query_v2"),))
        (finding,) = reference_findings(cand, parent, {})
        assert finding.endswith("(attached: `sql_query_v2`)")

    def test_a_code_tool_text_is_not_read_for_names(self):
        # A wrapper's description names the tool it replaces by design.
        wrap = CodeToolText(
            tool_id="t_wrap",
            name="manifest_lookup",
            files={"description": "Prefer this over sql_query."},
            function_name="manifest_lookup",
        )
        parent = _rc(BASE, tools=(SQL_TOOL,))
        assert reference_findings(_rc(BASE, tools=(wrap,)), parent, {}) == []

    def test_unread_code_tools_skip_the_tool_kind(self):
        # A code tool with no known function name would read as dropped.
        parent = _rc(BASE, tools=(SQL_TOOL,))
        cand = _rc(f"{BASE} Call sql_query.", tools=(SQL_TOOL,))
        unread = RcText(
            prompt=cand.prompt,
            skill_ids=(),
            skills={},
            code_tool_ids=cand.code_tool_ids,
            code_tools=None,
            tool_ids=cand.tool_ids,
        )
        assert reference_findings(unread, parent, {}) == []


# ---- on disk, through `check_references` ----


def install_code_tool(
    project: Path, tool_id: str, function: str, allowlist: tuple[str, ...] = ()
) -> None:
    """One code tool on disk. `allowlist`: the tool ids its code may call."""
    directory = project / "code_tools" / f"{tool_id} - {function}"
    directory.mkdir(parents=True)
    (directory / "tool.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (directory / "code_tool.kiln").write_text(
        json.dumps(
            {
                "id": tool_id,
                "name": function,
                "tool_function_name": function,
                "tool_description": "A depot tool.",
                "parameters_schema": {"type": "object"},
                "tool_allowlist": list(allowlist),
            }
        ),
        encoding="utf-8",
    )


def _row(rc_id: str, prompt: str, *tools: str) -> dict:
    return {
        "id": rc_id,
        "name": rc_id,
        "prompt": {"prompt": prompt},
        "run_config_properties": {"tools_config": {"tools": list(tools)}},
    }


FLOW = "kiln_tool::skill::sk_flow"


def _ctx(project: Path, *candidates: dict, mode: str = "warn"):
    """The baseline attaches `sql_query`, `kestrel_api` and the skill
    `manifest-flow`, which tells the model to call `sql_query`."""
    ctx = make_ctx()
    ctx.reference_check = mode
    on_disk(ctx, project)
    kiln = ctx.kiln
    kiln.evals["eval_1"] = make_eval("eval_1", "ec_1", name="Answer quality")
    kiln.eval_configs["ec_1"] = make_eval_config("ec_1", prompt_template=JUDGE)
    install_code_tool(project, "t_sql", "sql_query")
    install_code_tool(project, "t_api", "kestrel_api")
    install_code_tool(project, "t_wrap", "manifest_lookup", allowlist=(SQL,))
    install_skill(
        project,
        "sk_flow",
        "manifest-flow",
        skill_md("manifest-flow", "Call `sql_query` to list the manifest."),
    )
    kiln.run_configs = [_row("rc_base", BASE, SQL, API, FLOW), *candidates]
    return ctx


async def test_check_references_reads_both_kinds_from_disk(tmp_path):
    install_skill(
        tmp_path, "sk_old", "dock-schedule", skill_md("dock-schedule", "Book slots.")
    )
    ctx = _ctx(
        tmp_path,
        _row("rc_new", f"{BASE} Book slots with dock-schedule.", API, WRAP, FLOW),
    )
    check = await check_references(ctx, "rc_new", "rc_base")
    assert check.status == "found"
    assert check.note == ""
    # The wrapper allowlists the detached `sql_query`: that is not a finding.
    skill, tool = check.findings
    assert skill.startswith("skill `dock-schedule` is named in the prompt")
    assert tool.startswith("tool `sql_query` is named in skill manifest-flow")


async def test_a_wrapper_that_calls_a_detached_tool_is_clean(tmp_path):
    # `manifest_lookup` allowlists `sql_query`, which is not attached; the
    # prompt and the skills name only attached tools.
    ctx = _ctx(tmp_path, _row("rc_new", f"{BASE} Use manifest_lookup.", API, WRAP))
    check = await check_references(ctx, "rc_new", "rc_base")
    assert check.status == "clean"


async def test_an_inherited_dangling_reference_is_clean_on_disk(tmp_path):
    # The baseline's skill names `returns-flow`, a project skill it does not
    # attach. A build that changes only the prompt is not flagged for it.
    ctx = _ctx(tmp_path)
    install_skill(
        tmp_path, "sk_ret", "returns-flow", skill_md("returns-flow", "Returns.")
    )
    install_skill(
        tmp_path,
        "sk_pal",
        "pallet-flow",
        skill_md("pallet-flow", "See also the `returns-flow` skill."),
    )
    pal = "kiln_tool::skill::sk_pal"
    ctx.kiln.run_configs = [
        _row("rc_base", BASE, SQL, API, FLOW, pal),
        _row("rc_new", f"{BASE} Be brief.", SQL, API, FLOW, pal),
        _row("rc_add", f"{BASE} Load returns-flow.", SQL, API, FLOW, pal),
    ]
    assert (await check_references(ctx, "rc_new", "rc_base")).status == "clean"
    added = await check_references(ctx, "rc_add", "rc_base")
    assert added.findings == [
        "skill `returns-flow` is named in the prompt but is not attached"
    ]


def test_project_skills_reads_archived_skills(tmp_path):
    install_skill(tmp_path, "sk1", "dock-schedule", skill_md("dock-schedule", "x"))
    sidecar = tmp_path / "skills" / "sk1 - dock-schedule" / "skill.kiln"
    record = json.loads(sidecar.read_text())
    sidecar.write_text(json.dumps({**record, "is_archived": True}))
    install_skill(tmp_path, "sk2", "pallet-flow", skill_md("pallet-flow", "x"))
    found = project_skills(tmp_path)
    assert found.names == {"sk1": "dock-schedule", "sk2": "pallet-flow"}
    assert found.archived == {"dock-schedule"}


async def test_an_unreadable_candidate_is_unchecked(tmp_path):
    ctx = _ctx(tmp_path)
    check = await check_references(ctx, "rc_missing", "rc_base")
    assert check.status == "unchecked"


# ---- the item path ----


def _seed(kiln, candidate: str) -> None:
    kiln.results[("eval_1", "rc_base", "train")] = [
        make_run("item_1", accuracy=0.0),
        make_run("item_2", accuracy=1.0),
    ]
    kiln.pending[("eval_1", candidate, "train")] = [
        make_run("item_1", accuracy=1.0),
        make_run("item_2", accuracy=1.0),
    ]


def _built(rc_id: str):
    return ok(RcBuildResult(rc_id=rc_id, build_notes="built"))


def _reviews(ctx, n: int = 2) -> None:
    ctx.dispatch.queue(
        "generality_review", *(ok(GeneralityReviewResult(leaks=[])) for _ in range(n))
    )


def _view(ctx, outcome) -> ExperimentView:
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    return ExperimentView.from_record(MemoryRecord.from_api(record))


DETACHED = _row("rc_new", BASE, API, FLOW)  # dropped sql_query; the skill names it
KEPT = _row("rc_new2", BASE, SQL, API, FLOW)


async def test_warn_records_the_findings_and_measures_the_build(tmp_path):
    ctx = _ctx(tmp_path, DETACHED)
    _seed(ctx.kiln, "rc_new")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    _reviews(ctx)
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert outcome.rc_id == "rc_new"
    assert len(ctx.dispatch.calls_to("create_experiment_rc")) == 1
    (finding,) = outcome.reference_findings
    assert finding.startswith("tool `sql_query` is named in skill manifest-flow")
    (analysis,) = ctx.dispatch.calls_to("analyze_experiment_results")
    assert analysis.reference_findings == [finding]
    (event,) = [f for k, f in ctx.log.events if k == "reference_check"]
    assert event["status"] == "found"
    assert event["mode"] == "warn"
    assert event["line"].startswith("reference check rc_new: found — tool `sql_query`")
    details = [f["detail"] for k, f in ctx.log.events if k == "item_detail"]
    assert any(d.startswith("reference check: found — tool") for d in details)
    view = _view(ctx, outcome)
    assert view.reference_check is not None
    assert view.reference_check.startswith(
        "first build rc_new names what it does not attach (tool `sql_query`"
    )
    assert view.reference_check.endswith("a warning, not a refusal")
    assert ctx.reference_findings == {}  # handed to the outcome


async def test_warn_after_a_rebuild_for_another_check_says_the_rebuild(tmp_path):
    # The first build drops `sql_query` (a warning) and copies the judge (a
    # refusal). The rebuild keeps `sql_query`: the one record line says it
    # is clean, as the analysis input does.
    copied = _row("rc_new", f"{BASE} {JUDGE}", API, FLOW)
    ctx = _ctx(tmp_path, copied, KEPT)
    _seed(ctx.kiln, "rc_new2")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    _reviews(ctx, 4)
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert outcome.rc_id == "rc_new2"
    assert outcome.reference_findings == []
    _, rebuild = ctx.dispatch.calls_to("create_experiment_rc")
    assert rebuild.judge_copy_findings
    assert rebuild.reference_findings == []  # warn sends nothing back
    (analysis,) = ctx.dispatch.calls_to("analyze_experiment_results")
    assert analysis.reference_findings == []
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    lines = [
        line
        for line in record["content"].splitlines()
        if line.startswith("reference check:")
    ]
    assert len(lines) == 1
    view = _view(ctx, outcome)
    assert view.reference_check is not None
    assert view.reference_check.startswith("first build rc_new names what it")
    assert view.reference_check.endswith("rebuilt once as rc_new2 — clean")


async def test_refuse_rebuilds_once_with_the_findings(tmp_path):
    ctx = _ctx(tmp_path, DETACHED, KEPT, mode="refuse")
    _seed(ctx.kiln, "rc_new2")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    _reviews(ctx, 4)
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert outcome.rc_id == "rc_new2"
    assert outcome.reference_findings == []
    first, rebuild = ctx.dispatch.calls_to("create_experiment_rc")
    assert first.reference_findings == []
    assert rebuild.rejected_rc_id == "rc_new"
    (finding,) = rebuild.reference_findings
    assert finding.startswith("tool `sql_query`")
    view = _view(ctx, outcome)
    assert view.reference_check is not None
    assert "rebuilt once as rc_new2 — clean" in view.reference_check


async def test_refuse_ends_a_second_hit_as_a_dead_end(tmp_path):
    again = _row("rc_new2", BASE, API, FLOW)
    ctx = _ctx(tmp_path, DETACHED, again, mode="refuse")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"), _built("rc_new2"))
    _reviews(ctx, 4)
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "dead_end"
    assert outcome.rc_id == "rc_new2"
    assert outcome.reference_findings
    assert ctx.dispatch.calls_to("analyze_experiment_results") == []
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    assert record["overview"].endswith(
        "— DEAD END (reference check: still names what it does not attach"
        " after one rebuild)"
    )


async def test_refuse_does_not_rebuild_a_reused_run_config(tmp_path):
    ctx = _ctx(tmp_path, _row("rc_old", BASE, API, FLOW), mode="refuse")
    _reviews(ctx)
    item = fix_item()
    item.work.existing_rc_id = "rc_old"
    outcome = await execute_plan_item(ctx, item, BASELINE, ["rc_base"], 1)

    assert outcome.status == "dead_end"
    assert ctx.dispatch.calls_to("create_experiment_rc") == []
    record = ctx.kiln.memories[outcome.experiment_memory_id]
    assert record["overview"].endswith(
        "— DEAD END (reference check: the reused run config names what it does"
        " not attach)"
    )


async def test_off_skips_the_check(tmp_path):
    ctx = _ctx(tmp_path, DETACHED, mode="off")
    _seed(ctx.kiln, "rc_new")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    _reviews(ctx)
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert outcome.reference_findings == []
    assert not [f for k, f in ctx.log.events if k == "reference_check"]
    assert _view(ctx, outcome).reference_check is None


async def test_a_clean_build_writes_no_line(tmp_path):
    ctx = _ctx(tmp_path, _row("rc_new", f"{BASE} Be brief.", SQL, API, FLOW))
    _seed(ctx.kiln, "rc_new")
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    _reviews(ctx)
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    (event,) = [f for k, f in ctx.log.events if k == "reference_check"]
    assert event["status"] == "clean"
    assert _view(ctx, outcome).reference_check is None


def test_the_flag_defaults_to_warn():
    parser = build_parser()
    base = ["--project-path", "p", "--project-id", "1", "--task-id", "2"]
    assert parser.parse_args(base).reference_check == "warn"
    args = parser.parse_args([*base, "--reference-check", "refuse"])
    assert args.reference_check == "refuse"


def test_the_fallback_report_lists_the_findings():
    from test_status import WAVE1_ITEMS, completed_outcome, make_tracker, start_wave

    from kiln_harness_tuner.report import render_fallback_report

    tracker = make_tracker()
    tracker.event("run_start", deadline=100.0)
    start_wave(tracker, items=WAVE1_ITEMS)
    outcome = completed_outcome()
    outcome.reference_findings = ["tool `sql_query` is named in the prompt"]
    tracker.event("item_outcome", item_id="w1-1", outcome=outcome)
    text = render_fallback_report(tracker.snapshot())
    assert "  - reference check: tool `sql_query` is named in the prompt" in text
