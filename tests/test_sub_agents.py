"""Sub-agents: other Kiln tasks that the subject task calls as tools
(`kiln_task::<tool server id>`).

What the loop's own code does with one: it reads the sub-agent behind a
tool, it holds the sub-agent's model to the model policy, it reads the
sub-agent's text in the integrity checks, it checks that the tool was
called, it adds the usage of the sub-agent's runs to a drive, and it lists
what a run created when the run ends. The gateway's operation is tested in
`test_gateway.py`. Every id is invented."""

from __future__ import annotations

import json

import pytest
from conftest import fix_item, make_ctx, make_result
from fake_dispatcher import ok
from fake_kiln import FakeKiln
from test_generality import (
    BASE_PROMPT,
    _built,
    _on_project,
    _review,
    _train_project,
)
from test_items import BASELINE, seed_measurement
from test_judge_copy import BASE as JUDGE_BASE
from test_judge_copy import COPY
from test_judge_copy import _ctx as judge_ctx
from test_speed_objective import _project, _rc

from kiln_harness_tuner.generality import (
    audit_baseline,
    check_generality,
    sub_agent_changes,
)
from kiln_harness_tuner.items import (
    SUB_AGENT_NOT_MEASURED_NOTE,
    _changes_sub_agents,
    execute_plan_item,
)
from kiln_harness_tuner.judge_copy import check_judge_copies
from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.latency import (
    paired_drive_pass,
    sample_inputs,
    split_inputs,
    timed_drives,
)
from kiln_harness_tuner.levers import (
    MIN_SUB_AGENT_DRIVES,
    lever_evidence,
    lever_not_applied,
    lever_verdict,
)
from kiln_harness_tuner.model_policy import ModelPolicy, keep_policy
from kiln_harness_tuner.orchestrator import (
    close_sub_agents,
    pin_baseline_model,
    policy_refusal,
)
from kiln_harness_tuner.prompt_text import (
    SUB_AGENT_DESCRIPTION,
    SUB_AGENT_OUTPUT_SCHEMA,
    SUB_AGENT_PROMPT,
    rc_texts,
    sub_agent_texts,
    whole_texts,
)
from kiln_harness_tuner.report import render_sub_agents_appendix
from kiln_harness_tuner.schemas import RunStatus, SubAgentRecord
from kiln_harness_tuner.status import RunStatusTracker
from kiln_harness_tuner.sub_agents import (
    CREATED_FILE,
    LATENCY_NOT_MEASURED,
    SUB_AGENT_RUN_CONFIG_KIND,
    SUB_AGENT_TASK_KIND,
    SUB_AGENT_TOOL_KIND,
    CreatedSubAgent,
    SubAgentUsage,
    calls_a_sub_agent,
    calls_by_tool,
    cost_measured,
    created_sub_agents,
    latency_measured,
    read_sub_agent,
    read_sub_agent_tree,
    read_sub_agent_usage,
    sub_agent_calls,
    sub_agent_tool_ids,
    usage_line,
)

TOOL = "kiln_task::424242900001"
SERVER = "424242900001"
SUB_TASK = "424242900002"
SUB_RC = "424242900003"
SUB_RUN = "424242900004"
PROJECT = "p1"  # `conftest.TARGET`


def _attaches(rc_id: str, prompt: str, *tools: str, **props) -> dict:
    """A run config of the subject task that attaches `tools`."""
    return {
        "id": rc_id,
        "name": rc_id,
        "prompt": {"prompt": prompt},
        "run_config_properties": {"tools_config": {"tools": list(tools)}, **props},
    }


def _tool_message(
    run_id: str | None = SUB_RUN, tool: str = TOOL, usage: dict | None = None
) -> dict:
    """The tool message of a sub-agent call. `usage`: what a Kiln with
    Kiln-AI/Kiln#2077 writes there, the sub-agent's tokens and cost, which
    it also adds to the run's usage."""
    message = {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "the sub-agent's answer",
        "kiln_task_tool_data": f"{PROJECT}:::{tool}:::{SUB_TASK}:::{run_id}",
    }
    if usage is not None:
        message["usage"] = usage
    return message


def _run(*messages: dict, cost: float = 0.01, latency: float = 300) -> dict:
    return {
        "id": "424242900010",
        "trace": [{"role": "user", "content": "u"}, *messages],
        "output": {"output": "done"},
        "usage": {"cost": cost, "total_llm_latency_ms": latency, "output_tokens": 40},
    }


# ---- the sub-agent behind a tool ----


def test_only_kiln_task_tools_are_sub_agents():
    props = {
        "tools_config": {
            "tools": ["kiln_tool::code::c1", TOOL, "mcp::remote::m::t", "kiln_task::b"]
        }
    }
    assert sub_agent_tool_ids(props) == (TOOL, "kiln_task::b")
    assert sub_agent_tool_ids({}) == ()
    assert sub_agent_tool_ids(None) == ()


async def test_a_sub_agent_is_its_task_and_the_run_config_the_tool_pins():
    kiln = FakeKiln()
    kiln.add_sub_agent(SERVER, SUB_TASK, SUB_RC, provider="groq", model="kimi")
    sub = await read_sub_agent(kiln.call, PROJECT, TOOL)

    assert (sub.tool_id, sub.tool_name) == (TOOL, "ask_expert")
    assert (sub.task_id, sub.run_config_id) == (SUB_TASK, SUB_RC)
    assert sub.pair == ("groq", "kimi")
    assert sub.task["instruction"].startswith("You are an expert")
    # A bare tool server id reads the same.
    assert (await read_sub_agent(kiln.call, PROJECT, SERVER)).tool_id == TOOL
    # The task's runs and evals can hold held-out items: never read.
    assert not any("/runs" in p or "/evals" in p for _m, p, *_ in kiln.calls)


async def test_a_server_of_another_kind_is_no_sub_agent():
    kiln = FakeKiln()
    kiln.tool_servers["m1"] = {"id": "m1", "type": "remote_mcp", "properties": {}}
    assert await read_sub_agent(kiln.call, PROJECT, "m1") is None


async def test_a_tool_whose_run_config_is_gone_has_no_model():
    kiln = FakeKiln()
    kiln.add_sub_agent(SERVER, SUB_TASK, SUB_RC)
    kiln.sub_tasks[SUB_TASK]["run_configs"] = []
    (sub,) = (await read_sub_agent_tree(kiln.call, PROJECT, _props(TOOL))).values()
    assert sub.run_config is None and sub.pair == (None, None)


def _props(*tools: str) -> dict:
    return {"tools_config": {"tools": list(tools)}}


# ---- the calls a run made, and their usage ----


def test_the_calls_are_the_tool_messages_that_name_a_sub_agent():
    run = _run(
        {"role": "assistant", "tool_calls": [{"id": "c1"}]},
        _tool_message(),
        {"role": "tool", "tool_call_id": "c2", "content": "a code tool's answer"},
        _tool_message(run_id=None),
    )
    first, second = sub_agent_calls(run)
    assert (first.tool_id, first.task_id, first.run_id) == (TOOL, SUB_TASK, SUB_RUN)
    assert second.run_id is None  # Kiln saved no run for it
    assert calls_by_tool(run) == {TOOL: 2}
    assert calls_by_tool(_run()) == {}


def test_a_run_with_no_trace_shows_nothing_about_its_calls():
    assert sub_agent_calls({"usage": {"cost": 1}}) is None
    assert calls_by_tool({}) is None


async def test_the_usage_of_a_sub_agents_own_run_is_read_and_never_added():
    """The fix for the cost of a sub-agent is in Kiln. The tuner reads the
    sub-agent's own usage for information and leaves the run's alone, so
    nothing is counted twice when Kiln adds it."""
    kiln = FakeKiln()
    kiln.task_runs[SUB_RUN] = {
        "id": SUB_RUN,
        "usage": {"cost": 0.02, "total_llm_latency_ms": 700, "output_tokens": 10},
    }
    run = _run(_tool_message())
    info = await read_sub_agent_usage(kiln.call, PROJECT, run)

    assert (info.calls, info.unread) == (1, 0)
    assert (info.cost, info.latency_ms) == (0.02, 700.0)
    assert info.usage == {
        "cost": 0.02,
        "total_llm_latency_ms": 700,
        "output_tokens": 10,
    }
    assert "it is not in `usage`" in info.note and not info.in_run_usage
    assert run["usage"]["cost"] == 0.01  # the run's usage is what Kiln gave
    # The sub-agent's run is read under the sub-agent's task.
    assert (
        kiln.calls[-1][1] == f"/api/projects/{PROJECT}/tasks/{SUB_TASK}/runs/{SUB_RUN}"
    )


async def test_a_call_with_no_saved_run_is_counted_as_unread():
    kiln = FakeKiln()
    run = _run(_tool_message(run_id=None), _tool_message(run_id="424242900099"))
    info = await read_sub_agent_usage(kiln.call, PROJECT, run)

    assert (info.calls, info.unread, info.usage) == (2, 2, None)
    assert "2 sub-agent call(s) have no usage record" in info.note


async def test_a_sub_agents_own_sub_agent_is_read_too():
    kiln = FakeKiln()
    kiln.task_runs[SUB_RUN] = {
        "id": SUB_RUN,
        "usage": {"cost": 0.02},
        "trace": [_tool_message(run_id="424242900005", tool="kiln_task::n")],
    }
    kiln.task_runs["424242900005"] = {"id": "424242900005", "usage": {"cost": 0.04}}
    info = await read_sub_agent_usage(kiln.call, PROJECT, _run(_tool_message()))

    assert round(info.cost, 6) == 0.06
    assert (info.calls, info.unread) == (2, 0)


async def test_a_run_with_no_sub_agent_call_reads_nothing():
    kiln = FakeKiln()
    info = await read_sub_agent_usage(kiln.call, PROJECT, _run())
    assert (info.calls, info.note) == (0, "")
    assert kiln.calls == []


async def test_usage_that_kiln_wrote_on_the_tool_message_needs_no_saved_run():
    """A Kiln with Kiln-AI/Kiln#2077 writes the sub-agent's usage on the
    tool message and adds it to the run's usage, in an eval job too."""
    kiln = FakeKiln()
    run = _run(_tool_message(run_id=None, usage={"cost": 0.02, "total_tokens": 90}))
    info = await read_sub_agent_usage(kiln.call, PROJECT, run)

    assert (info.calls, info.unread, info.cost) == (1, 0, 0.02)
    assert info.in_run_usage and "already in `usage`" in info.note
    assert "never includes a sub-agent's time" in info.note
    assert kiln.calls == []


def test_the_cost_of_a_run_is_trusted_only_with_usage_on_every_tool_message():
    counted = _tool_message(usage={"cost": 0.02})
    assert cost_measured(_run()) and latency_measured(_run())
    assert cost_measured(_run(counted, counted))
    assert not cost_measured(_run(counted, _tool_message()))
    assert not cost_measured(_run(_tool_message(usage={})))
    # The latency of a run never holds a sub-agent's time, on any Kiln.
    assert not latency_measured(_run(counted))
    # A stored eval-run row is tested the same way.
    assert cost_measured({"task_run_trace": json.dumps([counted])})
    assert not cost_measured({"task_run_trace": json.dumps([_tool_message()])})


def test_a_stored_eval_run_shows_its_sub_agent_calls_too():
    """In an eval job Kiln saves no run for the sub-agent. The proof of a
    call is the tool message of the parent's trace."""
    row = {"task_run_trace": json.dumps([_tool_message(run_id=None)])}
    assert calls_a_sub_agent(row)
    assert calls_a_sub_agent({"task_run_trace": [_tool_message()]})
    assert not calls_a_sub_agent({"task_run_trace": "[]"})
    assert not calls_a_sub_agent({"task_run_trace": "not json"})
    assert not calls_a_sub_agent({})


def _drive_ctx(tmp_path):
    ctx = make_ctx()
    ctx.target = ctx.target.model_copy(update={"project_path": str(_project(tmp_path))})
    ctx.kiln.run_configs = [_rc("rc_base"), _rc("rc_cand")]
    ctx.kiln.task_runs[SUB_RUN] = {
        "id": SUB_RUN,
        "usage": {"cost": 0.02, "total_llm_latency_ms": 700},
    }
    inputs = sample_inputs(split_inputs(ctx.target.project_path, "t1", "train"))[:4]
    return ctx, inputs


async def test_a_run_config_that_calls_a_sub_agent_has_no_cost_and_no_latency(
    tmp_path,
):
    """Kiln's usage of a run leaves the sub-agent's calls out. So the drives
    of such a run config give no number to compare: not a sum made here,
    and not the numbers of the requests that happened to call no
    sub-agent."""
    ctx, inputs = _drive_ctx(tmp_path)
    first = inputs[0].plaintext

    def drive(body):
        calls = body["task_run_config_id"] == "rc_cand" and (
            body["plaintext_input"] == first
        )
        return _run(_tool_message()) if calls else _run()

    ctx.kiln.run_handler = drive
    drives = await timed_drives(ctx, ["rc_base", "rc_cand"], inputs, "x")

    # One of the candidate's four drives calls the sub-agent: all four go.
    assert drives.latencies["rc_cand"] == {} and drives.costs["rc_cand"] == {}
    assert set(drives.failures["rc_cand"].values()) == {LATENCY_NOT_MEASURED}
    assert len(drives.failures["rc_cand"]) == 4
    assert drives.unmeasured_latency == {"rc_cand"}
    assert drives.unmeasured_cost == {"rc_cand"}
    # The baseline calls none: its numbers stand.
    assert set(drives.latencies["rc_base"].values()) == {300.0}
    assert drives.failures["rc_base"] == {}
    # The drive ran, so it is not retried, and no failure is diagnosed.
    assert len(ctx.kiln.run_posts) == 8
    assert ctx.kiln.job_invocations == []
    # The spend is what Kiln reports for each run: nothing is added to it.
    assert round(drives.cost, 6) == 0.08
    # For information: the sub-agent's own run, and the lever evidence.
    (info,) = drives.sub_agents["rc_cand"]
    assert (info.calls, info.cost, info.latency_ms) == (1, 0.02, 700.0)
    assert drives.evidence["rc_cand"][inputs[0].id]["sub_agent_calls"] == {TOOL: 1}


async def test_a_paired_pass_with_a_sub_agent_side_gives_no_verdict(tmp_path):
    """The gate's paired pass and the baseline timing read the same drives:
    a comparison with a side that calls a sub-agent is incomplete, and an
    incomplete comparison is never "clearly faster" or "clearly cheaper"."""
    ctx, inputs = _drive_ctx(tmp_path)

    def drive(body):  # the candidate is far cheaper and faster on paper
        if body["task_run_config_id"] == "rc_cand":
            return _run(_tool_message(), cost=0.0001, latency=10)
        return _run(cost=0.05, latency=5000)

    ctx.kiln.run_handler = drive
    paired = await paired_drive_pass(ctx, "rc_base", ["rc_cand"], inputs, "train", "x")
    for comparison in (paired.latency["rc_cand"], paired.cost["rc_cand"]):
        assert comparison.n_items == 0 and comparison.incomplete
        assert not comparison.clearly_faster
        assert set(comparison.dropped_items.values()) == {LATENCY_NOT_MEASURED}


async def test_with_the_kiln_fix_the_cost_is_read_as_kiln_gives_it(tmp_path):
    """When each sub-agent tool message has a `usage` key, the run's usage
    holds the sub-agent's cost. The tuner reads it from the same place as
    before and adds nothing, so nothing is counted twice. The latency
    stays not measured: no Kiln puts a sub-agent's time in it."""
    ctx, inputs = _drive_ctx(tmp_path)
    counted = _tool_message(usage={"cost": 0.02})
    ctx.kiln.run_handler = lambda _body: _run(counted, cost=0.03, latency=1000)
    drives = await timed_drives(ctx, ["rc_cand"], inputs, "x")

    assert set(drives.costs["rc_cand"].values()) == {0.03}
    assert drives.unmeasured_cost == frozenset()
    assert drives.latencies["rc_cand"] == {}
    assert drives.unmeasured_latency == {"rc_cand"}
    assert round(drives.cost, 6) == 0.12  # the spend: Kiln's figure, once
    (info, *_rest) = drives.sub_agents["rc_cand"]
    assert info.in_run_usage


async def test_one_run_without_the_usage_key_withholds_the_cost_of_all(tmp_path):
    ctx, inputs = _drive_ctx(tmp_path)
    first = inputs[0].plaintext

    def drive(body):
        if body["plaintext_input"] == first:
            return _run(_tool_message())  # this run does not hold the usage
        return _run(_tool_message(usage={"cost": 0.02}), cost=0.03)

    ctx.kiln.run_handler = drive
    drives = await timed_drives(ctx, ["rc_cand"], inputs, "x")
    assert drives.costs["rc_cand"] == {} and drives.unmeasured_cost == {"rc_cand"}


async def test_a_drive_with_no_trace_of_a_sub_agent_run_config_is_withheld(
    tmp_path,
):
    """A run with no trace cannot show that it called no sub-agent. For a
    run config that attaches one, its numbers are not measured."""
    ctx, inputs = _drive_ctx(tmp_path)
    with_sub = _rc("rc_cand")
    with_sub["run_config_properties"]["tools_config"] = {"tools": [TOOL]}
    ctx.kiln.run_configs = [_rc("rc_base"), with_sub]

    def no_trace(_body):
        run = _run()
        del run["trace"]
        return run

    ctx.kiln.run_handler = no_trace
    drives = await timed_drives(ctx, ["rc_base", "rc_cand"], inputs, "x")
    assert drives.latencies["rc_cand"] == {} and drives.costs["rc_cand"] == {}
    assert drives.unmeasured_cost == {"rc_cand"}
    # A run config with no sub-agent and no trace keeps its numbers.
    assert set(drives.latencies["rc_base"].values()) == {300.0}


async def test_a_baseline_that_calls_a_sub_agent_is_a_lower_bound(tmp_path):
    """The baseline's numbers leave its sub-agent out, so they are lower
    than the request's. A candidate that calls no sub-agent and beats them
    beats the baseline: the comparison stands. A candidate that calls one
    stays withheld."""
    ctx, inputs = _drive_ctx(tmp_path)

    def drive(body):
        if body["task_run_config_id"] == "rc_base":
            return _run(_tool_message(), cost=0.05, latency=5000)
        return _run(cost=0.001, latency=100)

    ctx.kiln.run_handler = drive
    paired = await paired_drive_pass(ctx, "rc_base", ["rc_cand"], inputs, "train", "x")
    latency, cost = paired.latency["rc_cand"], paired.cost["rc_cand"]
    # Four pairs, all present: none is dropped for the sub-agent.
    assert latency.n_items == 4 and latency.dropped_items == {}
    assert latency.baseline_median_ms == 5000.0 and latency.candidate_median_ms == 100.0
    assert cost.n_items == 4 and cost.baseline_median_ms == 0.05
    drives = await timed_drives(ctx, ["rc_base"], inputs, "y")
    assert drives.latencies["rc_base"] == {}  # never the arm's own number
    assert set(drives.reference_latencies("rc_base").values()) == {5000.0}


def test_the_line_on_a_record_says_what_is_measured():
    left_out = [
        SubAgentUsage(calls=1, cost=0.02, latency_ms=700.0, in_run_usage=False),
        SubAgentUsage(calls=2, cost=0.04, latency_ms=900.0, in_run_usage=False),
    ]
    assert usage_line(left_out, 15) == (
        "2 of 15 drives call a sub-agent: latency and cost not measured"
        " (the sub-agent's calls: median $0.0300 and 800 ms per request)"
    )
    counted = [SubAgentUsage(calls=1, cost=0.02)]
    assert usage_line(counted, 3) == (
        "1 of 3 drives call a sub-agent: latency not measured, cost includes"
        " the sub-agent (the sub-agent's calls: median $0.0200 per request)"
    )
    unread = usage_line([SubAgentUsage(calls=1, unread=1, in_run_usage=False)], 3)
    assert unread.endswith("(the sub-agent's calls have no usage record)")
    assert usage_line([], 15) == ""


# ---- the lever check: the traces show that the tool was called ----


def _drives(*counts: int | None) -> dict:
    """item -> evidence, one drive per count: the calls of TOOL in its
    trace, or None for a drive with no trace."""
    return {
        f"i{n}": {"sub_agent_calls": None if count is None else {TOOL: count}}
        for n, count in enumerate(counts)
    }


class TestTheLeverCheck:
    WITH = _props(TOOL)
    WITHOUT = _props("kiln_tool::code::c1")

    def test_the_evidence_of_a_drive_names_the_sub_agents_it_called(self):
        assert lever_evidence(_run(_tool_message()))["sub_agent_calls"] == {TOOL: 1}
        assert lever_evidence({"usage": {}})["sub_agent_calls"] is None

    def test_a_called_sub_agent_is_applied_and_the_verdict_says_how_often(self):
        v = lever_verdict(self.WITH, _drives(2, 0, 1), self.WITHOUT)
        assert v.status == "applied"
        assert v.reason == f"sub-agent tool {TOOL} called in 2 of 3 drives"

    def test_a_sub_agent_no_drive_calls_is_not_applied(self):
        v = lever_verdict(self.WITH, _drives(0, 0, 0), self.WITHOUT)
        assert v.status == "not_applied"
        assert f"attaches the sub-agent tool {TOOL}, but 0 of 3 drives call it" in (
            v.reason
        )
        assert lever_not_applied(self.WITH, _drives(0, 0, 0), self.WITHOUT)

    def test_too_few_traced_drives_cannot_show_it_is_never_called(self):
        few = _drives(*([0] * (MIN_SUB_AGENT_DRIVES - 1)), None)
        v = lever_verdict(self.WITH, few, self.WITHOUT)
        assert v.status == "unverified" and "none calls it" in v.reason
        assert lever_not_applied(self.WITH, few, self.WITHOUT) is None

    def test_a_sub_agent_the_parent_already_has_is_not_this_arms_lever(self):
        v = lever_verdict(self.WITH, _drives(0, 0, 0), self.WITH)
        assert (v.status, v.reason) == ("applied", "")

    def test_no_parent_means_nothing_was_declared(self):
        assert lever_verdict(self.WITH, _drives(0, 0, 0)).status == "applied"


# ---- an arm that changes the sub-agents is timed ----


async def test_an_arm_that_adds_or_drops_a_sub_agent_is_timed():
    ctx = make_ctx()
    ctx.kiln.run_configs = [
        _attaches("rc_base", "p"),
        _attaches("rc_with", "p", TOOL),
        _attaches("rc_other", "p", "kiln_task::other"),
        _attaches("rc_code", "p", "kiln_tool::code::c1"),
    ]
    assert await _changes_sub_agents(ctx, "rc_base", "rc_with")
    assert await _changes_sub_agents(ctx, "rc_with", "rc_base")
    assert await _changes_sub_agents(ctx, "rc_with", "rc_other")
    assert not await _changes_sub_agents(ctx, "rc_base", "rc_code")
    assert not await _changes_sub_agents(ctx, "rc_with", "rc_with")


async def test_an_arm_on_a_sub_agent_baseline_has_no_cost_or_latency_number():
    """The parent already attaches the sub-agent, so the arm gets no timing
    drives. The stored eval runs call the sub-agent, so their cost and
    latency are withheld from the analysis table, and the record and the
    analyzer are told why."""
    ctx = make_ctx()
    ctx.kiln.add_sub_agent(SERVER, SUB_TASK, SUB_RC)
    ctx.kiln.run_configs = [
        _attaches("rc_base", BASE_PROMPT, TOOL),
        _attaches("rc_new", BASE_PROMPT + " Check the manifest first.", TOOL),
    ]
    seed_measurement(ctx.kiln)
    usage = {"cost": 0.01, "total_llm_latency_ms": 300}
    for rows in (*ctx.kiln.results.values(), *ctx.kiln.pending.values()):
        for row in rows:
            row["task_run_usage"] = dict(usage)
            row["task_run_trace"] = json.dumps([_tool_message(run_id=None)])
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    ctx.dispatch.queue("generality_review", _review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed"
    assert ctx.kiln.run_posts == []  # not timed
    note = SUB_AGENT_NOT_MEASURED_NOTE.format(tools=TOOL)
    (analysis,) = ctx.dispatch.calls_to("analyze_experiment_results")
    assert analysis.measurement_notes == [note]
    assert all(r.cost is None and r.latency is None for r in analysis.bundle.per_eval)
    record = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
    assert note in record


async def test_the_notes_survive_a_long_analysis_in_the_stored_record():
    """The notes on what is not measured are code's lines: they count in the
    analyzer's room, and the clamp of the verdict keeps them when the
    analyzer's text is over the record's cap."""
    from kiln_harness_tuner.memory_schema import CONTENT_MAX_CHARS

    ctx = make_ctx()
    ctx.kiln.add_sub_agent(SERVER, SUB_TASK, SUB_RC)
    ctx.kiln.run_configs = [
        _attaches("rc_base", BASE_PROMPT, TOOL),
        _attaches("rc_new", BASE_PROMPT + " Check the manifest first.", TOOL),
    ]
    seed_measurement(ctx.kiln)
    for rows in (*ctx.kiln.results.values(), *ctx.kiln.pending.values()):
        for row in rows:
            row["task_run_usage"] = {"cost": 0.01, "total_llm_latency_ms": 300}
            row["task_run_trace"] = json.dumps([_tool_message(run_id=None)])
    long_text = "\n".join(f"reading {n}: " + "x" * 90 for n in range(80))
    assert len(long_text) > CONTENT_MAX_CHARS
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    ctx.dispatch.queue("generality_review", _review())
    result = make_result().model_copy(update={"memory_content": long_text})
    ctx.dispatch.queue("analyze_experiment_results", ok(result))
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    note = SUB_AGENT_NOT_MEASURED_NOTE.format(tools=TOOL)
    (analysis,) = ctx.dispatch.calls_to("analyze_experiment_results")
    # Counted in the room the analyzer is told: less than the whole cap.
    assert analysis.content_budget < CONTENT_MAX_CHARS
    record = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
    assert len(record) <= CONTENT_MAX_CHARS
    assert note in record.splitlines()
    assert "[… truncated …]" in record


async def test_stored_runs_with_no_sub_agent_call_keep_their_passive_numbers():
    ctx = make_ctx()
    ctx.kiln.run_configs = [
        _attaches("rc_base", BASE_PROMPT),
        _attaches("rc_new", BASE_PROMPT + " Check the manifest first."),
    ]
    seed_measurement(ctx.kiln)
    for rows in (*ctx.kiln.results.values(), *ctx.kiln.pending.values()):
        for row in rows:
            row["task_run_usage"] = {"cost": 0.01, "total_llm_latency_ms": 300}
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    ctx.dispatch.queue("generality_review", _review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    (analysis,) = ctx.dispatch.calls_to("analyze_experiment_results")
    assert analysis.measurement_notes == []
    assert all(r.cost is not None for r in analysis.bundle.per_eval)


async def test_stored_runs_that_hold_the_sub_agents_usage_keep_their_cost():
    """With the Kiln fix, the cost of a stored run is the cost of the
    request. Its latency is not, so the rows carry a cost and no latency."""
    ctx = make_ctx()
    ctx.kiln.add_sub_agent(SERVER, SUB_TASK, SUB_RC)
    ctx.kiln.run_configs = [
        _attaches("rc_base", BASE_PROMPT, TOOL),
        _attaches("rc_new", BASE_PROMPT + " Check the manifest first.", TOOL),
    ]
    seed_measurement(ctx.kiln)
    counted = json.dumps([_tool_message(run_id=None, usage={"cost": 0.02})])
    for rows in (*ctx.kiln.results.values(), *ctx.kiln.pending.values()):
        for row in rows:
            row["task_run_usage"] = {"cost": 0.03, "total_llm_latency_ms": 300}
            row["task_run_trace"] = counted
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    ctx.dispatch.queue("generality_review", _review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    (analysis,) = ctx.dispatch.calls_to("analyze_experiment_results")
    assert all(r.latency is None for r in analysis.bundle.per_eval)
    assert all(r.cost is not None for r in analysis.bundle.per_eval)


# ---- the model policy ----


OPENAI_ONLY = ModelPolicy.from_json({"allow": [{"provider": "openai", "model": "*"}]})


def _policy_ctx(sub_provider: str):
    ctx = make_ctx()
    ctx.model_policy = OPENAI_ONLY
    ctx.kiln.add_sub_agent(SERVER, SUB_TASK, SUB_RC, provider=sub_provider, model="m")
    own = {"model_provider_name": "openai", "model_name": "gpt-test"}
    ctx.kiln.run_configs = [
        _attaches("rc_plain", "p", **own),
        _attaches("rc_sub", "p", TOOL, **own),
    ]
    return ctx


async def test_a_run_config_is_refused_for_the_model_of_its_sub_agent():
    ctx = _policy_ctx("groq")
    assert await policy_refusal(ctx, "rc_plain") is None
    refusal = await policy_refusal(ctx, "rc_sub")
    assert refusal.startswith(f"rc rc_sub: sub-agent tool {TOOL}: ")
    assert "outside the model policy" in refusal


async def test_a_sub_agent_inside_the_policy_is_allowed():
    ctx = _policy_ctx("openai")
    assert await policy_refusal(ctx, "rc_sub") is None


async def test_the_baseline_chain_is_measured_whatever_its_sub_agent_runs_on():
    ctx = _policy_ctx("groq")
    ctx.policy_exempt_rcs.add("rc_sub")
    assert await policy_refusal(ctx, "rc_sub") is None


async def test_a_tool_that_names_no_sub_agent_is_refused():
    ctx = _policy_ctx("openai")
    ctx.kiln.tool_servers.clear()
    refusal = await policy_refusal(ctx, "rc_sub")
    assert refusal is not None and TOOL in refusal


def test_to_keep_the_models_is_to_keep_the_sub_agents_models():
    """The sub-agent may keep its model. The subject may not move to it:
    the user chose to keep the subject's model."""
    policy = ModelPolicy.from_json(
        keep_policy("openai", "gpt-test", [("groq", "kimi"), ("openai", "gpt-test")])
    )
    assert policy.refusal("openai", "gpt-test") is None
    assert policy.refusal("openai", "gpt-test", sub_agent=True) is None
    assert policy.refusal("groq", "kimi", sub_agent=True) is None
    assert policy.refusal("groq", "kimi") is not None  # the subject
    assert policy.refusal("groq", "other", sub_agent=True) is not None
    assert "sub_agent_exceptions" not in keep_policy("openai", "gpt-test")
    assert policy.summary()["sub_agent_exceptions"] == [
        "groq/kimi (the model of a sub-agent of the baseline)"
    ]


def test_a_sub_agent_exception_must_be_one_exact_pair():
    with pytest.raises(ValueError, match="wildcard"):
        ModelPolicy.from_json(
            {
                "exceptions": [{"provider": "openai", "model": "m"}],
                "sub_agent_exceptions": [{"provider": "groq", "model": "*"}],
            }
        )


async def test_models_keep_pins_the_baselines_sub_agent_model_too(tmp_path):
    ctx = make_ctx()
    ctx.keep_model = True
    ctx.run_dir = tmp_path
    ctx.kiln.add_sub_agent(SERVER, SUB_TASK, SUB_RC, provider="groq", model="kimi")
    own = {"model_provider_name": "openai", "model_name": "gpt-test"}
    ctx.kiln.run_configs = [
        _attaches("rc_base", "p", TOOL, **own),
        # A candidate that moves the subject to the sub-agent's model.
        _attaches("rc_moved", "p", TOOL, model_provider_name="groq", model_name="kimi"),
    ]
    await pin_baseline_model(ctx, "rc_base")

    assert ctx.model_policy.refusal("groq", "kimi", sub_agent=True) is None
    assert ctx.model_policy.refusal("groq", "other", sub_agent=True) is not None
    written = json.loads((tmp_path / "model_policy.json").read_text())
    assert [(e["provider"], e["model"]) for e in written["exceptions"]] == [
        ("openai", "gpt-test")
    ]
    assert [(e["provider"], e["model"]) for e in written["sub_agent_exceptions"]] == [
        ("groq", "kimi")
    ]
    # The baseline, with its sub-agent, is allowed; the moved subject is not.
    assert await policy_refusal(ctx, "rc_base") is None
    refusal = await policy_refusal(ctx, "rc_moved")
    assert refusal is not None and "groq/kimi" in refusal and "sub-agent" not in refusal


async def test_the_policy_holds_for_a_sub_agent_of_a_sub_agent():
    """A sub-agent that the user made can attach one of its own. That one
    gets the task's data too."""
    ctx = _policy_ctx("openai")
    ctx.kiln.sub_tasks[SUB_TASK]["run_configs"][0]["run_config_properties"][
        "tools_config"
    ] = {"tools": ["kiln_task::inner"]}
    ctx.kiln.add_sub_agent("inner", "424242900031", "424242900032", provider="groq")
    refusal = await policy_refusal(ctx, "rc_sub")
    assert refusal is not None and "sub-agent tool kiln_task::inner" in refusal


async def test_a_sub_agent_that_cannot_be_read_is_refused_not_a_crash():
    """The gate and the measurement guard call this: one failed read must
    refuse the candidate, never end the run."""
    ctx = _policy_ctx("openai")
    real = ctx.kiln.call

    async def down(method, path, *args, **kwargs):
        if "tool_servers" in path:
            raise KilnAPIError(method, path, 500, {"detail": "down"})
        return await real(method, path, *args, **kwargs)

    ctx.kiln.call = down
    refusal = await policy_refusal(ctx, "rc_sub")
    assert refusal is not None and "could not be read" in refusal
    # The failed read is not kept: the next check reads again.
    ctx.kiln.call = real
    assert await policy_refusal(ctx, "rc_sub") is None


async def test_a_quota_stop_still_archives_an_orphan_sub_agent_tool(
    tmp_path, monkeypatch
):
    from kiln_harness_tuner import orchestrator as orch
    from kiln_harness_tuner.provider_errors import ProviderQuotaError
    from kiln_harness_tuner.schemas import ErrorInfo

    ctx = make_ctx()
    ctx.run_dir = tmp_path
    ctx.kiln.add_sub_agent(SERVER, SUB_TASK, SUB_RC)
    ctx.kiln.run_configs = [_attaches("rc_base", "p")]
    _created(tmp_path)

    async def quota(*_args, **_kwargs):
        raise ProviderQuotaError(
            ErrorInfo(error_class="quota_exhausted", detail="out of credits")
        )

    monkeypatch.setattr(orch, "_run_loop", quota)
    assert await orch.orchestrate(ctx, "g", timeout_mins=10) == "quota_exhausted"
    assert ctx.kiln.tool_server_archives == [SERVER]
    assert [k for k, _ in ctx.log.events if k == "sub_agents"]


async def test_a_failed_close_never_masks_the_exit(monkeypatch):
    from kiln_harness_tuner import orchestrator as orch

    async def broken(_ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(orch, "close_sub_agents", broken)
    await orch._close_sub_agents_safely(make_ctx())  # does not raise


# ---- the integrity checks read the sub-agent's text ----


async def test_a_run_configs_text_includes_its_sub_agents():
    ctx = make_ctx()
    ctx.kiln.add_sub_agent(
        SERVER,
        SUB_TASK,
        SUB_RC,
        tool_name="read_manifest",
        description="Read a manifest and return its counts.",
        instruction="You read freight manifests.",
        output_json_schema=json.dumps(
            {"type": "object", "properties": {"pallets": {"type": "integer"}}}
        ),
    )
    ctx.kiln.run_configs = [_attaches("rc_new", "Answer the operator.", TOOL)]
    text = (await rc_texts(ctx, ["rc_new"]))["rc_new"]

    assert text.sub_agent_ids == (TOOL,)
    files = text.sub_agents[TOOL].files
    assert files[SUB_AGENT_PROMPT] == "You read freight manifests."
    assert "Read a manifest and return its counts." in files[SUB_AGENT_DESCRIPTION]
    assert '"pallets"' in files[SUB_AGENT_OUTPUT_SCHEMA]
    places = whole_texts(text)
    assert places["sub-agent read_manifest, prompt"] == "You read freight manifests."
    assert "sub-agent read_manifest, description" in places


async def test_a_frozen_prompt_on_the_sub_agents_run_config_is_its_text():
    ctx = make_ctx()
    ctx.kiln.add_sub_agent(
        SERVER, SUB_TASK, SUB_RC, prompt={"prompt": "Frozen prompt."}, prompt_id="id::p"
    )
    ctx.kiln.run_configs = [_attaches("rc_new", "p", TOOL)]
    text = (await rc_texts(ctx, ["rc_new"]))["rc_new"]
    assert text.sub_agents[TOOL].files[SUB_AGENT_PROMPT] == "Frozen prompt."


async def test_a_prompt_built_from_stored_runs_is_not_read():
    """A few-shot generator builds its text from the sub-agent task's
    stored runs, which the loop does not split. It is never rendered, and
    the check says that the text was not read."""
    ctx = judge_ctx()
    ctx.kiln.add_sub_agent(
        SERVER, SUB_TASK, SUB_RC, prompt_id="few_shot_prompt_builder"
    )
    ctx.kiln.run_configs.append(_attaches("rc_new", JUDGE_BASE, TOOL))
    text = (await rc_texts(ctx, ["rc_new"]))["rc_new"]

    assert text.sub_agents is None and sub_agent_texts(text) is None
    assert not any("gen_prompt" in p for _m, p, *_ in ctx.kiln.calls)
    check = await check_judge_copies(ctx, "rc_new", "rc_base")
    assert check.status == "clean"
    assert "the sub-agents' text could not be read" in check.note


async def test_a_run_config_with_no_sub_agent_has_none_to_read():
    ctx = make_ctx()
    ctx.kiln.run_configs = [_attaches("rc_new", "p", "kiln_tool::code::c1")]
    text = (await rc_texts(ctx, ["rc_new"]))["rc_new"]
    assert text.sub_agents == {} and sub_agent_texts(text) == {}


async def test_a_train_value_in_a_sub_agents_prompt_is_a_leak(tmp_path):
    p = _train_project(tmp_path)
    ctx = make_ctx()
    _on_project(ctx, p)
    ctx.kiln.add_sub_agent(
        SERVER,
        SUB_TASK,
        SUB_RC,
        instruction="Invoice INV-58213-C is from Quillfeather. Say so.",
    )
    ctx.kiln.run_configs = [
        _attaches("rc_base", BASE_PROMPT),
        _attaches("rc_new", BASE_PROMPT, TOOL),
    ]
    check = await check_generality(ctx, fix_item(), "rc_new", "rc_base")

    assert check.status == "leaked"
    (leak,) = check.leaks
    assert leak.startswith('exact match: "inv-58213-c" in sub-agent ask_expert, prompt')
    assert ctx.dispatch.calls_to("generality_review") == []  # code found it


async def test_a_new_sub_agent_is_reviewed_in_full_when_the_prompt_is_not_changed():
    ctx = make_ctx()
    ctx.kiln.add_sub_agent(SERVER, SUB_TASK, SUB_RC, instruction="You are an expert.")
    ctx.kiln.run_configs = [
        _attaches("rc_base", BASE_PROMPT),
        _attaches("rc_new", BASE_PROMPT, TOOL),
    ]
    ctx.dispatch.queue("generality_review", _review())
    check = await check_generality(ctx, fix_item(), "rc_new", "rc_base")

    assert check.status == "clean"
    (review,) = ctx.dispatch.calls_to("generality_review")
    blocks = review.sub_agent_changes
    assert any(
        b.startswith(f"sub-agent ask_expert ({TOOL}), prompt: new file (new sub-agent)")
        and "You are an expert." in b
        for b in blocks
    )
    assert any(", description:" in b.splitlines()[0] for b in blocks)


async def test_the_same_sub_agent_on_both_run_configs_is_no_change():
    ctx = make_ctx()
    ctx.kiln.add_sub_agent(SERVER, SUB_TASK, SUB_RC)
    ctx.kiln.run_configs = [
        _attaches("rc_base", BASE_PROMPT, TOOL),
        _attaches("rc_new", BASE_PROMPT, TOOL),
    ]
    texts = await rc_texts(ctx, ["rc_base", "rc_new"])
    assert sub_agent_changes(texts["rc_base"], texts["rc_new"]) == []
    check = await check_generality(ctx, fix_item(), "rc_new", "rc_base")
    assert check.status == "unchanged"


async def test_a_sub_agent_that_replaces_one_is_diffed_against_it():
    ctx = make_ctx()
    ctx.kiln.add_sub_agent(SERVER, SUB_TASK, SUB_RC, instruction="Line one.")
    ctx.kiln.add_sub_agent(
        "424242900021", "424242900022", "424242900023", instruction="Line one.\nLine 2."
    )
    ctx.kiln.run_configs = [
        _attaches("rc_base", BASE_PROMPT, TOOL),
        _attaches("rc_new", BASE_PROMPT, "kiln_task::424242900021"),
    ]
    texts = await rc_texts(ctx, ["rc_base", "rc_new"])
    (block,) = sub_agent_changes(texts["rc_base"], texts["rc_new"])
    assert "prompt: changed from sub-agent ask_expert" in block.splitlines()[0]
    assert "+Line 2." in block and "+Line one." not in block


async def test_eval_text_in_a_sub_agents_prompt_is_a_copy():
    ctx = judge_ctx()
    ctx.kiln.add_sub_agent(SERVER, SUB_TASK, SUB_RC, instruction=COPY)
    ctx.kiln.run_configs.append(_attaches("rc_new", JUDGE_BASE, TOOL))
    check = await check_judge_copies(ctx, "rc_new", "rc_base")

    assert check.status == "copied"
    (finding,) = check.findings
    assert finding.startswith(
        "sub-agent ask_expert, prompt quotes the spec of eval Count accuracy"
    )


async def test_a_sub_agent_the_baseline_attaches_is_exempt_from_the_copy_check():
    ctx = judge_ctx()
    ctx.kiln.add_sub_agent(SERVER, SUB_TASK, SUB_RC, instruction=COPY)
    ctx.kiln.run_configs[0] = _attaches("rc_base", JUDGE_BASE, TOOL)
    ctx.kiln.run_configs.append(_attaches("rc_new", JUDGE_BASE + " Be brief.", TOOL))
    check = await check_judge_copies(ctx, "rc_new", "rc_base")
    assert check.status == "clean"


async def test_the_baseline_audit_reads_the_baselines_sub_agent(tmp_path):
    p = _train_project(tmp_path)
    ctx = make_ctx()
    _on_project(ctx, p)
    ctx.kiln.add_sub_agent(
        SERVER,
        SUB_TASK,
        SUB_RC,
        instruction="Invoice INV-58213-C is from Quillfeather.",
    )
    ctx.kiln.run_configs = [_attaches("rc_base", BASE_PROMPT, TOOL)]
    audit = await audit_baseline(ctx, "rc_base")

    assert audit.status == "leaked"
    assert any("sub-agent ask_expert, prompt" in f for f in audit.findings)


# ---- what a run created, at the end of the run ----


def _registry(run_dir, *rows: tuple[str, str, str]) -> None:
    lines = [
        json.dumps(
            {"id": f"{kind}:{rid}", "kind": kind, "split": "meta", "eval_id": task}
        )
        for kind, rid, task in rows
    ]
    (run_dir / CREATED_FILE).write_text("\n".join(lines) + "\n")


def _created(run_dir, server=SERVER, task=SUB_TASK, rc=SUB_RC) -> None:
    _registry(
        run_dir,
        (SUB_AGENT_TASK_KIND, task, ""),
        (SUB_AGENT_RUN_CONFIG_KIND, rc, task),
        (SUB_AGENT_TOOL_KIND, server, task),
    )


def test_the_registry_names_each_sub_agent_a_run_created(tmp_path):
    _created(tmp_path)
    with (tmp_path / CREATED_FILE).open("a") as f:
        # A train record of the same run, a half-made sub-agent and a bad line.
        f.write(
            json.dumps({"id": "424242900050", "kind": "task_run", "split": "train"})
        )
        f.write("\n")
        f.write(
            json.dumps(
                {
                    "id": f"{SUB_AGENT_TASK_KIND}:424242900060",
                    "kind": SUB_AGENT_TASK_KIND,
                    "split": "meta",
                }
            )
        )
        f.write("\nnot json\n")
    assert created_sub_agents(tmp_path) == [
        CreatedSubAgent(SUB_TASK, SUB_RC, TOOL),
        CreatedSubAgent("424242900060"),
    ]
    assert created_sub_agents(tmp_path / "absent") == []


async def test_the_run_end_keeps_an_attached_tool_and_archives_an_orphan(tmp_path):
    ctx = make_ctx()
    ctx.run_dir = tmp_path
    ctx.kiln.add_sub_agent(SERVER, SUB_TASK, SUB_RC, provider="groq", model="kimi")
    ctx.kiln.add_sub_agent("424242900021", "424242900022", "424242900023")
    _registry(
        tmp_path,
        (SUB_AGENT_TASK_KIND, SUB_TASK, ""),
        (SUB_AGENT_RUN_CONFIG_KIND, SUB_RC, SUB_TASK),
        (SUB_AGENT_TOOL_KIND, SERVER, SUB_TASK),
        (SUB_AGENT_TASK_KIND, "424242900022", ""),
        (SUB_AGENT_RUN_CONFIG_KIND, "424242900023", "424242900022"),
        (SUB_AGENT_TOOL_KIND, "424242900021", "424242900022"),
    )
    # An experiment's run config attaches the first tool. It was not promoted.
    ctx.kiln.run_configs = [_attaches("rc_base", "p"), _attaches("rc_arm", "p", TOOL)]

    assert await close_sub_agents(ctx) == 1
    assert ctx.kiln.tool_server_archives == ["424242900021"]
    (event,) = [f for k, f in ctx.log.events if k == "sub_agents"]
    kept, orphan = event["sub_agents"]
    assert kept["tool_id"] == TOOL and kept["attached_by"] == ["rc_arm"]
    assert (kept["model_provider_name"], kept["model_name"]) == ("groq", "kimi")
    assert not kept["archived"]
    assert orphan["archived"] and orphan["attached_by"] == []
    assert "archived 1 tool(s)" in event["line"]


async def test_the_run_end_does_nothing_when_the_run_created_no_sub_agent(tmp_path):
    ctx = make_ctx()
    ctx.run_dir = tmp_path
    assert await close_sub_agents(ctx) == 0
    assert ctx.kiln.calls == []
    assert not [k for k, _ in ctx.log.events if k == "sub_agents"]


async def test_the_run_end_archives_nothing_it_cannot_prove_unattached(tmp_path):
    ctx = make_ctx()
    ctx.run_dir = tmp_path
    ctx.kiln.add_sub_agent(SERVER, SUB_TASK, SUB_RC)
    _created(tmp_path)
    real = ctx.kiln.call

    async def no_run_configs(method, path, *args, **kwargs):
        if path.endswith("/tasks/t1/run_configs"):
            raise KilnAPIError(method, path, 500, {"detail": "down"})
        return await real(method, path, *args, **kwargs)

    ctx.kiln.call = no_run_configs
    assert await close_sub_agents(ctx) == 0
    assert ctx.kiln.tool_server_archives == []
    assert [k for k, _ in ctx.log.events if k == "sub_agents_unread"]


def _status(**fields) -> RunStatus:
    return RunStatus(
        project_id="p1",
        task_id="t1",
        guidance="",
        baseline_history=["rc_base", "rc_arm"],
        waves=[],
        spend_total_usd=0.0,
        time_remaining_secs=0.0,
        exit_reason="wrap_up",
        **fields,
    )


def test_the_report_names_each_sub_agent_and_its_state():
    records = [
        SubAgentRecord(
            task_id=SUB_TASK,
            tool_id=TOOL,
            tool_name="ask_expert",
            run_config_id=SUB_RC,
            model_name="kimi",
            model_provider_name="groq",
            attached_by=["rc_arm"],
        ),
        SubAgentRecord(
            task_id="424242900022",
            tool_id="kiln_task::424242900021",
            tool_name="read_manifest",
            attached_by=["rc_lost"],
        ),
        SubAgentRecord(
            task_id="424242900032", tool_id="kiln_task::424242900031", archived=True
        ),
        SubAgentRecord(task_id="424242900042"),
    ]
    text = render_sub_agents_appendix(_status(sub_agents=records))
    rows = [line for line in text.splitlines() if line.startswith("| ")][1:]

    assert text.startswith("## Sub-agents (code-computed)\n")
    assert f"`{TOOL}`" in rows[0] and f"`{SUB_TASK}`" in rows[0]
    assert "groq/kimi" in rows[0] and "in the final baseline `rc_arm`" in rows[0]
    assert "not in the final baseline" in rows[1] and "`rc_lost`" in rows[1]
    assert "archived: no run config attaches it" in rows[2]
    assert "not complete" in rows[3]
    assert "cannot win on speed" in text and "it cannot win on cost" in text
    assert render_sub_agents_appendix(_status()) == ""


def test_the_tracker_keeps_the_sub_agents_of_the_run():
    tracker = RunStatusTracker(project_id="p1", task_id="t1", guidance="")
    tracker.event(
        "sub_agents",
        sub_agents=[{"task_id": SUB_TASK, "tool_id": TOOL}, {"no_task": True}],
        archived=0,
        line="",
    )
    (record,) = tracker.snapshot().sub_agents
    assert (record.task_id, record.tool_id) == (SUB_TASK, TOOL)


# ---- a sub-agent arm, from the build to its outcome ----


def _sub_agent_arm(tmp_path, *, called: bool):
    """A quality run whose arm `rc_new` attaches a new sub-agent. Every
    drive of `rc_new` calls it once, or never."""
    from test_orchestrator import seed_active_eval, seed_baseline, seed_issue
    from test_speed_objective import _seed_rows

    ctx = make_ctx()
    ctx.target = ctx.target.model_copy(update={"project_path": str(_project(tmp_path))})
    kiln = ctx.kiln
    seed_baseline(kiln)
    seed_active_eval(kiln)
    seed_issue(kiln)
    _seed_rows(kiln, "rc_base", [0.0] + [1.0] * 7, stored=True)
    _seed_rows(kiln, "rc_new", [1.0] * 8)
    kiln.add_sub_agent(SERVER, SUB_TASK, SUB_RC)
    kiln.task_runs[SUB_RUN] = {
        "id": SUB_RUN,
        "usage": {"cost": 0.02, "total_llm_latency_ms": 700},
    }
    own = {"model_provider_name": "openai", "model_name": "m"}
    kiln.run_configs = [
        _attaches("rc_base", BASE_PROMPT, **own),
        _attaches("rc_new", BASE_PROMPT, TOOL, **own),
    ]

    def drive(body):
        if called and body["task_run_config_id"] == "rc_new":
            return _run(_tool_message())
        return _run()

    kiln.run_handler = drive
    ctx.dispatch.queue("create_experiment_rc", _built("rc_new"))
    ctx.dispatch.queue("generality_review", _review())
    ctx.dispatch.queue("analyze_experiment_results", ok(make_result()))
    return ctx


async def test_a_sub_agent_arm_is_timed_and_its_record_says_the_tool_was_called(
    tmp_path,
):
    ctx = _sub_agent_arm(tmp_path, called=True)
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed", outcome
    # Under the quality objective an arm is timed only for its lever.
    assert [p for p in ctx.kiln.run_posts if p["task_run_config_id"] == "rc_new"]
    (applied,) = [f for k, f in ctx.log.events if k == "lever_applied"]
    assert f"sub-agent tool {TOOL} called in" in applied["line"]
    record = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
    assert f"sub-agent tool {TOOL} called in" in record
    # Its cost and latency are not measured: no paired comparison reaches
    # the analyzer, and the record says why, with the sub-agent's own usage.
    assert outcome.measurement is not None
    (analysis,) = ctx.dispatch.calls_to("analyze_experiment_results")
    assert analysis.bundle.latency is None and analysis.bundle.cost is None
    latency_note, cost_note = analysis.measurement_notes
    assert latency_note.startswith(
        "latency not measured: the drives of the candidate call a sub-agent"
    )
    assert cost_note.startswith(
        "cost not measured: the drives of the candidate call a sub-agent"
    )
    assert "drives call a sub-agent: latency and cost not measured" in record
    assert "the sub-agent's calls: median $0.0200 and 700 ms per request" in record


async def test_a_sub_agent_arm_cannot_win_on_cost(tmp_path):
    """Under the cost objective, the candidate's own model calls cost far
    less than the baseline's, because the work moved into the sub-agent.
    That is not a measured saving: the arm stops, and it is not successful."""
    ctx = _sub_agent_arm(tmp_path, called=True)
    ctx.objective = "cost"

    def drive(body):
        if body["task_run_config_id"] == "rc_new":
            return _run(_tool_message(), cost=0.0001, latency=10)
        return _run(cost=0.05, latency=5000)

    ctx.kiln.run_handler = drive
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "not_landed", outcome
    assert (
        "cost not measured: the drives of the candidate call a sub-agent"
        in outcome.speed_verdict
    )
    assert ctx.dispatch.calls_to("analyze_experiment_results") == []
    assert not [j for j in ctx.kiln.job_invocations if j[1] == "rc_new"]  # no judge


def _fixed_kiln_drives(ctx) -> None:
    """Drives as a Kiln with Kiln-AI/Kiln#2077 gives them: the candidate's
    run holds its sub-agent's cost, and it is cheaper than the baseline."""

    def drive(body):
        if body["task_run_config_id"] == "rc_new":
            counted = _tool_message(usage={"cost": 0.004})
            return _run(counted, cost=0.01, latency=10)
        return _run(cost=0.05, latency=5000)

    ctx.kiln.run_handler = drive


async def test_with_the_kiln_fix_a_sub_agent_arm_is_compared_on_cost(tmp_path):
    ctx = _sub_agent_arm(tmp_path, called=True)
    ctx.objective = "cost"
    _fixed_kiln_drives(ctx)
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed", outcome
    (analysis,) = ctx.dispatch.calls_to("analyze_experiment_results")
    cost = analysis.bundle.cost
    assert cost.candidate_median_ms == 0.01 and cost.baseline_median_ms == 0.05
    assert analysis.bundle.latency is None  # never measured with a sub-agent
    (note,) = analysis.measurement_notes
    assert note.startswith("latency not measured: the drives of the candidate")
    record = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
    assert "latency not measured, cost includes the sub-agent" in record


async def test_with_the_kiln_fix_a_sub_agent_arm_still_cannot_win_on_speed(tmp_path):
    ctx = _sub_agent_arm(tmp_path, called=True)
    ctx.objective = "speed"
    _fixed_kiln_drives(ctx)
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "not_landed", outcome
    assert (
        "latency not measured: the drives of the candidate call a sub-agent"
        in outcome.speed_verdict
    )
    assert ctx.dispatch.calls_to("analyze_experiment_results") == []


async def test_a_sub_agent_no_drive_calls_ends_the_arm_before_it_is_judged(tmp_path):
    ctx = _sub_agent_arm(tmp_path, called=False)
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "dead_end"
    assert outcome.speed_verdict.startswith(
        f"lever not applied: the run config attaches the sub-agent tool {TOOL}"
    )
    assert [k for k, _ in ctx.log.events if k == "lever_not_applied"]
    assert ctx.dispatch.calls_to("analyze_experiment_results") == []
    assert not [j for j in ctx.kiln.job_invocations if j[1] == "rc_new"]


async def test_a_baseline_sub_agent_outside_the_policy_is_said_once_at_the_start():
    from kiln_harness_tuner.orchestrator import _note_baseline_policy

    ctx = _policy_ctx("groq")
    await _note_baseline_policy(ctx, "rc_sub")
    (event,) = [f for k, f in ctx.log.events if k == "baseline_outside_policy"]
    assert event["reason"].startswith(f"sub-agent tool {TOOL}: ")
    ctx = _policy_ctx("openai")
    await _note_baseline_policy(ctx, "rc_sub")
    assert not [k for k, _ in ctx.log.events if k == "baseline_outside_policy"]


async def test_dropping_the_baselines_sub_agent_can_win_on_speed(tmp_path):
    """The baseline calls a sub-agent; the candidate calls none. The
    baseline's latency is a lower bound, and the candidate beats it: the
    candidate is clearly faster, and its record says why the comparison
    holds."""
    ctx = _sub_agent_arm(tmp_path, called=False)
    ctx.objective = "speed"
    own = {"model_provider_name": "openai", "model_name": "m"}
    ctx.kiln.run_configs = [
        _attaches("rc_base", BASE_PROMPT, TOOL, **own),
        _attaches("rc_new", BASE_PROMPT, **own),
    ]

    def drive(body):
        if body["task_run_config_id"] == "rc_base":
            return _run(_tool_message(), cost=0.05, latency=5000)
        return _run(cost=0.001, latency=100)

    ctx.kiln.run_handler = drive
    outcome = await execute_plan_item(ctx, fix_item(), BASELINE, ["rc_base"], 1)

    assert outcome.status == "completed", outcome
    (analysis,) = ctx.dispatch.calls_to("analyze_experiment_results")
    assert analysis.bundle.latency.clearly_faster
    assert analysis.measurement_notes == []
    record = ctx.kiln.memories[outcome.experiment_memory_id]["content"]
    assert "the baseline's cost and latency leave its sub-agent out" in record
