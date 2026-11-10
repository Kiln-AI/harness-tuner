"""ClaudeCliDispatcher plumbing against canned subprocess transcripts —
recorded `claude -p --output-format json` outputs driving prompt render,
fenced-JSON extraction, the repair-retry path, and usage parsing; no live
calls."""

import asyncio
import json
import signal
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from kiln_harness_tuner.cli import render_run_configs
from kiln_harness_tuner.dispatch.base import AgentDispatcher
from kiln_harness_tuner.dispatch.claude_cli import (
    AGENT_MODEL_DEFAULTS,
    AGENT_TIMEOUTS_SECS,
    DEFAULT_ALLOWED_TOOLS,
    DEFAULT_TEMPLATES_DIR,
    AsyncioSubprocessRunner,
    ClaudeCliDispatcher,
    DispatchTimeoutError,
    PreflightError,
    SubprocessResult,
)
from kiln_harness_tuner.paths import StatePaths
from kiln_harness_tuner.schemas import (
    AnalysisBundle,
    AnalysisInput,
    BaselineRef,
    BatchPlan,
    BatchPlanInput,
    DescribeEvalInput,
    EvalBuildInput,
    EvalDescription,
    EvalTarget,
    ExperimentItem,
    ExperimentResult,
    FixWork,
    PromotionDecision,
    PromotionInput,
    RcBuildInput,
    RcBuildResult,
    ReportInput,
    ReportMarkdown,
    RunStatus,
    TargetBlock,
    Usage,
)

# The agents folder a run installs into the state folder, and every Claude
# session's working folder. Its Claude projects slug maps `/` and `.` alike.
AGENTS_DIR = Path("/state/agents/claude/0.1.0")
AGENTS_SLUG = "-state-agents-claude-0-1-0"
PROJECT_PATH = "../../Kestrel Depot"

TARGET = TargetBlock(
    project_path=PROJECT_PATH,
    project_id="424242748186",
    task_id="424242613304",
    kiln_host="http://localhost:8757",
)

BASELINE = BaselineRef(rc_id="rc_207")

BATCH_PLAN_INPUT = BatchPlanInput(
    guidance="focus evals X, Y",
    baseline=BASELINE,
    should_wrap_up=False,
    eval_sizes={"eval_1": 17},
)

PLAN_DATA = {
    "items": [
        {
            "id": "w1-1",
            "type": "experiment",
            "evaluation_target": [{"eval_id": "eval_1"}],
            "promotion_candidate": True,
            "work": {
                "issue_memory_id": "mem_61",
                "hypothesis": "tone fix helps",
                "technique": "prompt",
                "predicted_effect": "+5%",
                "needs_eval": False,
                "eval_id": "eval_1",
            },
        }
    ],
    "rejected_summary": "passed on mem_84 again — 3rd time, eval-build cost",
    "open_questions": [],
}


def cli_payload(
    result_text: str,
    *,
    session_id: str = "sess-1",
    cost_usd: float = 0.10,
    input_tokens: int = 1000,
    output_tokens: int = 200,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    duration_ms: int = 45_000,
    is_error: bool = False,
) -> str:
    """A canned `claude -p --output-format json` stdout payload."""
    return json.dumps(
        {
            "type": "result",
            "subtype": "success" if not is_error else "error_during_execution",
            "is_error": is_error,
            "duration_ms": duration_ms,
            "duration_api_ms": duration_ms - 5_000,
            "num_turns": 3,
            "result": result_text,
            "session_id": session_id,
            "total_cost_usd": cost_usd,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read_input_tokens,
                "cache_creation_input_tokens": cache_creation_input_tokens,
            },
        },
        ensure_ascii=False,
    )


def envelope_text(data, status="ok", prose="Here is my plan:", detail=None) -> str:
    block = json.dumps(
        {"status": status, "data": data, "detail": detail},
        indent=2,
        ensure_ascii=False,
    )
    return f"{prose}\n```json\n{block}\n```"


@dataclass
class FakeCall:
    argv: list[str]
    stdin_text: str
    timeout_secs: float
    cwd: str


@dataclass
class FakeRunner:
    """Scripted SubprocessRunner: pops one canned result per invocation."""

    script: list[SubprocessResult | Exception]
    calls: list[FakeCall] = field(default_factory=list)

    async def run(self, argv, *, stdin_text, timeout_secs, cwd):
        self.calls.append(FakeCall(argv, stdin_text, timeout_secs, cwd))
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def ok_proc(stdout: str) -> SubprocessResult:
    return SubprocessResult(returncode=0, stdout=stdout, stderr="")


def make_dispatcher(script, **kwargs) -> tuple[ClaudeCliDispatcher, FakeRunner]:
    runner = FakeRunner(script=list(script))
    dispatcher = ClaudeCliDispatcher(TARGET, AGENTS_DIR, runner=runner, **kwargs)
    return dispatcher, runner


def make_wired_dispatcher(
    tmp_path: Path, script, run_id: str = "260731-0900"
) -> tuple[ClaudeCliDispatcher, FakeRunner]:
    """A dispatcher with the full run wiring: run dir, rendered
    config paths, traces root, and a fake Claude projects dir."""
    runner = FakeRunner(script=list(script))
    dispatcher = ClaudeCliDispatcher(
        TARGET,
        AGENTS_DIR,
        runner=runner,
        run_id=run_id,
        run_dir=tmp_path / "run",
        mcp_config_path=tmp_path / "run" / "mcp_config.json",
        settings_path=tmp_path / "run" / "settings.json",
        traces_root=tmp_path / "traces",
        claude_projects_dir=tmp_path / "projects",
    )
    return dispatcher, runner


def write_transcript(tmp_path: Path, session_id: str, text: str = "{}\n") -> Path:
    """Drop a fake session transcript where the trace copy will look for it:
    <projects>/<slug of the agents folder>/<session_id>.jsonl."""
    src_dir = tmp_path / "projects" / AGENTS_SLUG
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / f"{session_id}.jsonl"
    src.write_text(text)
    return src


def traces_dir(tmp_path: Path, run_id: str = "260731-0900") -> Path:
    return tmp_path / "traces" / TARGET.project_id / TARGET.task_id / run_id


class TestSuccessPath:
    async def test_batch_plan_ok_with_typed_data(self):
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "ok"
        assert isinstance(result.data, BatchPlan)
        assert result.data.items[0].id == "w1-1"
        assert result.error is None
        assert len(runner.calls) == 1

    async def test_argv_and_cwd(self):
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        call = runner.calls[0]
        assert call.argv[0] == "claude"
        assert "-p" in call.argv
        fmt_idx = call.argv.index("--output-format")
        assert call.argv[fmt_idx + 1] == "json"
        tools_idx = call.argv.index("--allowedTools")
        assert "mcp__kiln_harness_tuner_gateway__get_task" in call.argv[tools_idx + 1]
        assert "kiln_api_mcp" not in call.argv[tools_idx + 1]
        # The agents folder: its `.claude/skills/` and `CLAUDE.md` load.
        assert call.cwd == str(AGENTS_DIR)

    def test_transcripts_are_read_from_the_agents_folders_project(self, tmp_path):
        # Claude Code names a session's project folder after its cwd.
        dispatcher, _ = make_wired_dispatcher(tmp_path, [])
        assert dispatcher._transcript_dir() == tmp_path / "projects" / AGENTS_SLUG

    async def test_prompt_render_contains_all_blocks(self):
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        prompt = runner.calls[0].stdin_text
        # template text (thin placeholder names the skill to load)
        assert "kiln-harness-tuner-batch-selection" in prompt
        # the standard target block — no agent ever guesses ids
        assert "424242748186" in prompt
        assert "424242613304" in prompt
        # the project tree is read-denied, so its path stays out of the
        # prompt: it would only invite a refused read.
        assert PROJECT_PATH not in prompt
        # the input payload
        assert "focus evals X, Y" in prompt
        assert "rc_207" in prompt
        # output requirements: envelope + schema
        assert "failed_expected" in prompt
        assert "BatchPlan" in prompt
        assert "rejected_summary" in prompt  # from model_json_schema()

    @pytest.mark.parametrize("on", [False, True])
    async def test_planner_prompt_states_the_eval_building_setting(self, on):
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(
            BATCH_PLAN_INPUT.model_copy(update={"eval_building": on})
        )
        prompt = runner.calls[0].stdin_text
        # The template says what the setting means; the input says which.
        assert "**Eval building is experimental.**" in prompt
        assert "plan no `eval_only` item" in prompt
        assert f'"eval_building": {str(on).lower()}' in prompt

    async def test_prompt_schema_rendered_without_ascii_escapes(self):
        # ensure_ascii=False (project standard): the schemas' descriptions carry
        # em dashes; —-style escapes would be needless prompt noise.
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        prompt = runner.calls[0].stdin_text
        assert "\\u2014" not in prompt
        assert "—" in prompt  # e.g. EvalTarget's docstring-derived description

    async def test_usage_parsed_from_cli_payload(self):
        dispatcher, _ = make_dispatcher(
            [
                ok_proc(
                    cli_payload(
                        envelope_text(PLAN_DATA),
                        cost_usd=0.42,
                        input_tokens=1500,
                        output_tokens=500,
                        duration_ms=90_000,
                    )
                )
            ]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.usage == Usage(cost_usd=0.42, tokens=2000, wall_secs=90.0)

    async def test_usage_includes_cache_tokens(self):
        # Cache reads typically dominate for skill-loading agents —
        # omitting them would materially undercount Usage.tokens.
        dispatcher, _ = make_dispatcher(
            [
                ok_proc(
                    cli_payload(
                        envelope_text(PLAN_DATA),
                        input_tokens=1000,
                        output_tokens=200,
                        cache_read_input_tokens=50_000,
                        cache_creation_input_tokens=8_000,
                    )
                )
            ]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.usage.tokens == 59_200


async def test_failed_expected_passes_through():
    report_data = {
        "eval_id": "eval_9",
        "eval_config_id": "cfg_1",
        "eval_type": "llm_judge",
        "case_count": 0,
        "reproduced": False,
        "not_reproduced_reason": "cannot trigger the 429 path",
        "baseline_headline": "n/a",
        "priority": "p2",
    }
    dispatcher, _ = make_dispatcher(
        [
            ok_proc(
                cli_payload(
                    envelope_text(
                        report_data,
                        status="failed_expected",
                        detail="issue does not reproduce on the baseline",
                    )
                )
            )
        ]
    )
    result = await dispatcher.eval_build(
        EvalBuildInput(issue_memory_id="mem_102", baseline=BASELINE)
    )
    assert result.status == "failed_expected"
    assert result.data.reproduced is False
    assert result.error is None
    assert result.detail == "issue does not reproduce on the baseline"


async def test_failed_expected_with_null_data_carries_detail():
    # data=null is allowed by the rendered contract on failed_expected — the
    # envelope's detail is then the only *why* the caller gets.
    dispatcher, _ = make_dispatcher(
        [
            ok_proc(
                cli_payload(
                    envelope_text(
                        None,
                        status="failed_expected",
                        detail="no plausible fix; issue is stale",
                    )
                )
            )
        ]
    )
    result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
    assert result.status == "failed_expected"
    assert result.data is None
    assert result.error is None
    assert result.detail == "no plausible fix; issue is stale"


class TestRepairRetry:
    async def test_malformed_then_repaired(self):
        dispatcher, runner = make_dispatcher(
            [
                ok_proc(
                    cli_payload("Sure! The plan is: {not json", session_id="sess-7")
                ),
                ok_proc(cli_payload(envelope_text(PLAN_DATA), cost_usd=0.05)),
            ]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "ok"
        assert isinstance(result.data, BatchPlan)
        assert len(runner.calls) == 2
        # the repair call resumes the same session with the validation errors
        repair = runner.calls[1]
        resume_idx = repair.argv.index("--resume")
        assert repair.argv[resume_idx + 1] == "sess-7"
        assert "did not satisfy the output requirements" in repair.stdin_text

    async def test_schema_invalid_data_triggers_repair(self):
        bad_plan = {"items": [], "open_questions": []}  # missing rejected_summary
        dispatcher, runner = make_dispatcher(
            [
                ok_proc(cli_payload(envelope_text(bad_plan))),
                ok_proc(cli_payload(envelope_text(PLAN_DATA))),
            ]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "ok"
        assert "rejected_summary" in runner.calls[1].stdin_text

    async def test_items_plus_loop_exit_triggers_repair(self):
        # A composed wave returned through the exit channel. The BatchPlan
        # validator rejects it, the repair retry carries the instructive
        # message, and the corrected items-only plan flows through unchanged.
        conflated = {**PLAN_DATA, "loop_exit": "end the run after these items"}
        dispatcher, runner = make_dispatcher(
            [
                ok_proc(cli_payload(envelope_text(conflated), session_id="sess-9")),
                ok_proc(cli_payload(envelope_text(PLAN_DATA))),
            ]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "ok"
        assert isinstance(result.data, BatchPlan)
        assert result.data.loop_exit is None
        assert len(result.data.items) == 1
        assert len(runner.calls) == 2
        assert "nothing-to-do-NOW" in runner.calls[1].stdin_text

    async def test_usage_summed_across_repair(self):
        dispatcher, _ = make_dispatcher(
            [
                ok_proc(
                    cli_payload(
                        "no fence here",
                        cost_usd=0.10,
                        input_tokens=100,
                        output_tokens=50,
                        duration_ms=10_000,
                    )
                ),
                # A resumed `claude -p` reports the cost of the whole session
                # so far ($0.10 + $0.20), and its tokens for the round alone.
                ok_proc(
                    cli_payload(
                        envelope_text(PLAN_DATA),
                        cost_usd=0.30,
                        input_tokens=200,
                        output_tokens=100,
                        duration_ms=20_000,
                    )
                ),
            ]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.usage.cost_usd == pytest.approx(0.30)  # not 0.40
        assert result.usage.tokens == 450
        assert result.usage.wall_secs == pytest.approx(30.0)

    async def test_repair_also_failing_is_parse_failure(self):
        dispatcher, runner = make_dispatcher(
            [
                ok_proc(cli_payload("still not json")),
                ok_proc(cli_payload("STILL not json")),
            ]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "failed_infra"
        assert result.data is None
        assert result.error.error_class == "parse_failure"
        assert len(runner.calls) == 2  # exactly one repair retry


class TestInfraFailures:
    async def test_timeout_maps_to_failed_infra_timeout(self):
        dispatcher, _ = make_dispatcher([DispatchTimeoutError("900s exceeded")])
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "failed_infra"
        assert result.error.error_class == "timeout"

    async def test_per_role_timeouts(self):
        # 15 min for batch_plan / promote_best / report_writer;
        # 30 min for eval_build / create_experiment_rc / analyze
        dispatcher, runner = make_dispatcher(
            [
                ok_proc(cli_payload(envelope_text(PLAN_DATA))),
                DispatchTimeoutError("expired"),
            ]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert runner.calls[0].timeout_secs == 15 * 60
        item = ExperimentItem(
            id="w1-1",
            type="experiment",
            evaluation_target=[EvalTarget(eval_id="eval_1")],
            promotion_candidate=False,
            work=FixWork(
                issue_memory_id="mem_61",
                hypothesis="h",
                technique="prompt",
                predicted_effect="e",
                needs_eval=False,
                eval_id="eval_1",
            ),
        )
        await dispatcher.analyze_experiment_results(
            AnalysisInput(
                item=item,
                rc_id="rc_300",
                bundle=AnalysisBundle(per_eval=[], spend_total_usd=0.0, errors=[]),
                experiment_memory_id="mem_400",
            )
        )
        assert runner.calls[1].timeout_secs == 30 * 60

    async def test_nonzero_exit_is_agent_crash(self):
        dispatcher, _ = make_dispatcher(
            [SubprocessResult(returncode=1, stdout="", stderr="MCP server not found")]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "failed_infra"
        assert result.error.error_class == "agent_crash"
        assert "MCP server not found" in result.error.detail

    async def test_unparseable_cli_stdout_is_agent_crash(self):
        dispatcher, _ = make_dispatcher(
            [SubprocessResult(returncode=0, stdout="garbage not json", stderr="")]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "failed_infra"
        assert result.error.error_class == "agent_crash"

    async def test_cli_is_error_is_provider_error(self):
        dispatcher, _ = make_dispatcher(
            [ok_proc(cli_payload("upstream provider unavailable", is_error=True))]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "failed_infra"
        assert result.error.error_class == "provider_error"

    async def test_cli_rate_limit_text_classified(self):
        dispatcher, _ = make_dispatcher(
            [ok_proc(cli_payload("API rate limit reached, retry later", is_error=True))]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "failed_infra"
        assert result.error.error_class == "rate_limit"

    async def test_http_429_text_classified_as_rate_limit(self):
        dispatcher, _ = make_dispatcher(
            [ok_proc(cli_payload("provider returned HTTP 429", is_error=True))]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.error.error_class == "rate_limit"

    async def test_incidental_429_digits_stay_provider_error(self):
        # a bare "429" substring match would false-positive here
        dispatcher, _ = make_dispatcher(
            [ok_proc(cli_payload("failed to load memory mem_4290", is_error=True))]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "failed_infra"
        assert result.error.error_class == "provider_error"

    async def test_error_word_does_not_bridge_onto_ids(self):
        # "error" is not an introducer — it would bridge onto ids
        dispatcher, _ = make_dispatcher(
            [ok_proc(cli_payload("error: mem_429 unavailable", is_error=True))]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.error.error_class == "provider_error"


class TestProtocolSurface:
    def test_satisfies_agent_dispatcher_protocol(self):
        dispatcher, _ = make_dispatcher([])
        assert isinstance(dispatcher, AgentDispatcher)

    async def test_all_methods_dispatch_with_their_output_models(self):
        rc_data = {"rc_id": "rc_301", "build_notes": "cloned rc_207, prompt v2"}
        promo_data = {
            "winner_rc_id": None,
            "winner_experiment_memory_id": None,
            "rationale": "no candidate beat the baseline on val",
            "regressed_issues": [],
        }
        dispatcher, runner = make_dispatcher(
            [
                ok_proc(cli_payload(envelope_text(rc_data))),
                ok_proc(cli_payload(envelope_text(promo_data))),
            ]
        )
        item = ExperimentItem(
            id="w1-1",
            type="experiment",
            evaluation_target=[EvalTarget(eval_id="eval_1")],
            promotion_candidate=True,
            work=FixWork(
                issue_memory_id="mem_61",
                hypothesis="h",
                technique="prompt",
                predicted_effect="e",
                needs_eval=False,
                eval_id="eval_1",
            ),
        )
        rc_result = await dispatcher.create_experiment_rc(
            RcBuildInput(item=item, baseline=BASELINE)
        )
        assert isinstance(rc_result.data, RcBuildResult)
        promo_result = await dispatcher.promote_best(
            PromotionInput(candidates=[], baseline=BASELINE)
        )
        assert isinstance(promo_result.data, PromotionDecision)
        assert promo_result.data.winner_rc_id is None
        # each role rendered its own template
        assert "kiln-harness-tuner-experiment-build" in runner.calls[0].stdin_text
        assert "kiln-harness-tuner-promote" in runner.calls[1].stdin_text


def test_templates_exist_for_all_agents():
    for agent in [
        "batch_plan",
        "eval_build",
        "create_experiment_rc",
        "analyze_experiment_results",
        "promote_best",
        "report_writer",
    ]:
        path = DEFAULT_TEMPLATES_DIR / f"{agent}.md"
        assert path.is_file(), f"missing template {path}"


class TestFencedBlockExtraction:
    async def test_final_fenced_block_wins_over_earlier_ones(self):
        # agents often emit intermediate fenced JSON; the FINAL block is the
        # contract
        text = (
            'First draft:\n```json\n{"scratch": true}\n```\n'
            f"{envelope_text(PLAN_DATA, prose='Final answer:')}"
        )
        dispatcher, _ = make_dispatcher([ok_proc(cli_payload(text))])
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "ok"
        assert isinstance(result.data, BatchPlan)

    async def test_other_language_fence_does_not_mispair(self):
        # A ```python block's fences must not pair with the
        # envelope's — the envelope after it still parses, with no repair round
        text = (
            "I checked with a script:\n```python\nprint(check())\n```\n"
            f"{envelope_text(PLAN_DATA, prose='All good. Final answer:')}"
        )
        dispatcher, runner = make_dispatcher([ok_proc(cli_payload(text))])
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "ok"
        assert isinstance(result.data, BatchPlan)
        assert len(runner.calls) == 1  # no repair round burned

    async def test_stale_valid_draft_never_wins_over_broken_final(self):
        # An earlier schema-valid draft envelope must not be
        # silently accepted when the FINAL block is broken — that failure goes
        # to the repair retry.
        stale_draft = dict(PLAN_DATA, rejected_summary="STALE DRAFT — superseded")
        text = (
            f"{envelope_text(stale_draft, prose='Draft:')}\n"
            "Wait, revising…\n```json\n{broken final\n```"
        )
        repaired = envelope_text(PLAN_DATA, prose="Corrected:")
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(text)), ok_proc(cli_payload(repaired))]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert len(runner.calls) == 2  # the repair retry ran
        assert result.status == "ok"
        assert result.data.rejected_summary != "STALE DRAFT — superseded"

    async def test_truncated_final_block_never_falls_back_to_stale_draft(self):
        # A final ```json block cut off mid-JSON with NO closing
        # fence (exactly what a max-token cutoff produces) must not let the
        # earlier complete draft win — it goes to the repair retry.
        stale_draft = dict(PLAN_DATA, rejected_summary="STALE DRAFT — superseded")
        text = (
            f"{envelope_text(stale_draft, prose='Draft:')}\n"
            'Final answer:\n```json\n{"status": "ok", "data": {"items": ['
        )
        repaired = envelope_text(PLAN_DATA, prose="Corrected:")
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(text)), ok_proc(cli_payload(repaired))]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert len(runner.calls) == 2  # the repair retry ran
        assert "incomplete" in runner.calls[1].stdin_text
        assert result.status == "ok"
        assert result.data.rejected_summary != "STALE DRAFT — superseded"

    async def test_indented_final_fences_never_fall_back_to_stale_draft(self):
        # Indented fences (common inside list items) don't match
        # the line-anchored fence regex — the opener probe (which permits
        # leading indentation) must still catch them so the stale draft never
        # wins.
        stale_draft = dict(PLAN_DATA, rejected_summary="STALE DRAFT — superseded")
        indented_block = json.dumps({"status": "ok", "data": PLAN_DATA})
        text = (
            f"{envelope_text(stale_draft, prose='Draft:')}\n"
            f"- Final answer:\n  ```json\n  {indented_block}\n  ```"
        )
        repaired = envelope_text(PLAN_DATA, prose="Corrected:")
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(text)), ok_proc(cli_payload(repaired))]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert len(runner.calls) == 2  # the repair retry ran
        assert result.status == "ok"
        assert result.data.rejected_summary != "STALE DRAFT — superseded"

    async def test_prose_mention_of_json_fence_is_not_a_false_positive(self):
        # A mid-sentence ```json mention after the valid final
        # block must not trip the opener probe — that would burn a repair
        # round on output that was complete and valid.
        text = (
            f"{envelope_text(PLAN_DATA, prose='Final answer:')}\n"
            "Note: I put the result in a ```json block above."
        )
        dispatcher, runner = make_dispatcher([ok_proc(cli_payload(text))])
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "ok"
        assert isinstance(result.data, BatchPlan)
        assert len(runner.calls) == 1  # no repair round burned

    async def test_jsonc_fence_is_not_a_false_positive(self):
        # Other fence tags (```jsonc/```json5) after the valid
        # final block are not ```json openers.
        text = (
            f"{envelope_text(PLAN_DATA, prose='Final answer:')}\n"
            "For reference, annotated:\n```jsonc\n{// comment\n}\n```"
        )
        dispatcher, runner = make_dispatcher([ok_proc(cli_payload(text))])
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "ok"
        assert isinstance(result.data, BatchPlan)
        assert len(runner.calls) == 1  # no repair round burned

    async def test_truncated_only_block_is_parse_error_then_repaired(self):
        # no complete block at all + an opener → clear parse error, repair runs
        text = 'Answer:\n```json\n{"status": "ok", "data"'
        repaired = envelope_text(PLAN_DATA, prose="Corrected:")
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(text)), ok_proc(cli_payload(repaired))]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert len(runner.calls) == 2
        assert "no complete fenced block" in runner.calls[1].stdin_text
        assert result.status == "ok"


async def test_report_writer_dispatch():
    run_status = RunStatus(
        project_id="424242748186",
        task_id="424242613304",
        guidance="one wave, cheapest issues",
        baseline_history=["rc_207"],
        waves=[],
        spend_total_usd=3.5,
        time_remaining_secs=0.0,
        exit_reason="wrap_up",
    )
    # The agent returns MARKDOWN; code (report.py) writes the file.
    dispatcher, runner = make_dispatcher(
        [
            ok_proc(
                cli_payload(
                    envelope_text({"markdown": "# Kiln Harness Tuner run report\n…"})
                )
            )
        ]
    )
    result = await dispatcher.report_writer(ReportInput(run_status=run_status))
    assert result.status == "ok"
    assert isinstance(result.data, ReportMarkdown)
    assert result.data.markdown == "# Kiln Harness Tuner run report\n…"
    assert runner.calls[0].timeout_secs == 15 * 60
    assert "kiln-harness-tuner-report" in runner.calls[0].stdin_text


async def test_experiment_result_output_model():
    res_data = {
        "successful": True,
        "findings": "retry-skill removes 429 failures",
        "memory_overview": "EXP 0722-w1-1: retry-skill for 429s — +18%, n=34, p=.04",
        "memory_content": "full table…",
    }
    item = ExperimentItem(
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
    dispatcher, _ = make_dispatcher([ok_proc(cli_payload(envelope_text(res_data)))])
    result = await dispatcher.analyze_experiment_results(
        AnalysisInput(
            item=item,
            rc_id="rc_300",
            bundle=AnalysisBundle(per_eval=[], spend_total_usd=1.8, errors=[]),
            experiment_memory_id="mem_400",
        )
    )
    assert result.status == "ok"
    assert isinstance(result.data, ExperimentResult)
    assert result.data.per_ingredient_outcomes is None


class TestCliArgv:
    async def test_model_and_effort_pinned(self):
        # Smoke-test issue 8: one pin for every machine.
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        argv = runner.calls[0].argv
        assert argv[argv.index("--model") + 1] == "opus"
        assert argv[argv.index("--effort") + 1] == "high"

    async def test_mcp_and_settings_flags_when_wired(self, tmp_path):
        dispatcher, runner = make_wired_dispatcher(
            tmp_path, [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        argv = runner.calls[0].argv
        mcp_idx = argv.index("--mcp-config")
        assert argv[mcp_idx + 1] == str(tmp_path / "run" / "mcp_config.json")
        assert "--strict-mcp-config" in argv
        assert argv[argv.index("--settings") + 1] == str(
            tmp_path / "run" / "settings.json"
        )

    async def test_each_role_gets_its_own_mcp_config_and_settings(self, tmp_path):
        # The role's own files, when rendered, win over the shared
        # ones — its MCP config holds only its gateway, its hook knows the role.
        run = tmp_path / "run"
        run.mkdir()
        for name in ("mcp_config.batch_plan.json", "settings.batch_plan.json"):
            (run / name).write_text("{}")
        dispatcher, runner = make_wired_dispatcher(
            tmp_path, [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        argv = runner.calls[0].argv
        assert argv[argv.index("--mcp-config") + 1] == str(
            run / "mcp_config.batch_plan.json"
        )
        assert argv[argv.index("--settings") + 1] == str(
            run / "settings.batch_plan.json"
        )

    async def test_mcp_and_settings_flags_absent_when_unwired(self):
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        argv = runner.calls[0].argv
        assert "--mcp-config" not in argv
        assert "--strict-mcp-config" not in argv
        assert "--settings" not in argv

    async def test_uniform_tool_surface_no_bare_write_edit(self):
        # Smoke-test issue 1 (revised): every role keeps Bash (OS-sandboxed
        # via settings); bare Write/Edit never ride --allowedTools — that
        # would be an allow-anywhere grant defeating the scratch confinement.
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        argv = runner.calls[0].argv
        tools = argv[argv.index("--allowedTools") + 1].split(",")
        assert "Bash" in tools
        assert "mcp__kiln_harness_tuner_gateway__get_train_results" in tools
        assert not any("kiln_api_mcp" in t for t in tools)
        assert "Write" not in tools
        assert "Edit" not in tools


def render_into(tmp_path: Path, target: TargetBlock) -> None:
    """A real run's render, into `tmp_path / "run"`."""
    state = StatePaths(tmp_path / "state")
    render_run_configs(
        tmp_path / "run", target, state=state, agents_dir=state.agents / "claude"
    )


class TestGrantedMcpToolsExist:
    """Every `mcp__<server>__<tool>` granted to a role names a server the run
    actually renders.

    The defect this replaces: three tools of a model-lookup server were
    granted to every role, on a comment saying the server already shipped in
    the rendered MCP config. It did not — `.mcp.json` declares the Kiln server
    alone and sessions run under `--strict-mcp-config` — so every role held
    entries for tools that could not exist. An allowlist is not a diagnostic,
    so nothing said so. The grant list and the rendered config are two ends of
    one contract, and this is the only test holding both at once."""

    def _rendered_servers(self, tmp_path, role: str) -> set[str]:
        """The server keys a REAL run renders into the role's MCP config."""
        target = TargetBlock(
            project_path=str(tmp_path / "proj"),
            project_id="424242000001",
            task_id="424242000002",
            kiln_host="http://localhost:8757",
        )
        render_into(tmp_path, target)
        path = tmp_path / "run" / f"mcp_config.{role}.json"
        return set(json.loads(path.read_text())["mcpServers"])

    def test_every_granted_mcp_server_is_rendered(self, tmp_path):
        from kiln_harness_tuner.dispatch.claude_cli import NO_TOOL_ROLES

        for role, tools in DEFAULT_ALLOWED_TOOLS.items():
            if role in NO_TOOL_ROLES:
                continue
            rendered = self._rendered_servers(tmp_path / role, role)
            granted = {t.split("__")[1] for t in tools if t.startswith("mcp__")}
            assert granted <= rendered, (role, granted, rendered)

    def test_every_role_gets_its_gateway_operations_and_no_generic_kiln_tool(self):
        from kiln_harness_tuner.gateway.ops import role_tools

        for role, tools in DEFAULT_ALLOWED_TOOLS.items():
            assert set(role_tools(role)) <= set(tools), role
            assert not any("kiln_api_mcp" in t for t in tools), role

    def test_the_reviewers_have_no_gateway_operations(self, tmp_path):
        # The integrity reviewer and the generality reviewer each read their
        # input and nothing else — no tool, no gateway, no MCP config of
        # their own.
        from kiln_harness_tuner.dispatch.claude_cli import NO_TOOL_ROLES
        from kiln_harness_tuner.gateway.ops import ROLE_OPS

        assert NO_TOOL_ROLES == {"integrity_review", "generality_review"}
        target = TargetBlock(
            project_path=str(tmp_path / "proj"),
            project_id="424242000001",
            task_id="424242000002",
            kiln_host="http://localhost:8757",
        )
        render_into(tmp_path, target)
        for role in NO_TOOL_ROLES:
            assert DEFAULT_ALLOWED_TOOLS[role] == []
            assert role not in ROLE_OPS
            assert not (tmp_path / "run" / f"mcp_config.{role}.json").exists()

    def test_preflight_has_its_own_list(self):
        # `preflight` must not inherit the shared list: it proves the Kiln
        # lane and must not depend on a server whose first spawn pays an env
        # sync. A tool meant for it is added there deliberately, or not at all.
        assert (
            DEFAULT_ALLOWED_TOOLS["preflight"]
            is not DEFAULT_ALLOWED_TOOLS["batch_plan"]
        )


class TestSkillInstallerGrant:
    """The skill-bundle installer tool is granted to the roles that STAGE
    skills — the builder, and the probe ("does this model read a
    skill file" needs a throwaway one). Every other role has no business
    writing skills, and preflight must not depend on a server that spawns a
    uv env sync."""

    STAGING_ROLES = frozenset({"create_experiment_rc", "probe"})

    def test_only_the_staging_roles_have_the_grant(self):
        tool = "mcp__skill_installer__install_skill_bundle"
        for role in self.STAGING_ROLES:
            assert tool in DEFAULT_ALLOWED_TOOLS[role]
        for role, tools in DEFAULT_ALLOWED_TOOLS.items():
            if role in self.STAGING_ROLES:
                continue
            assert tool not in tools, f"role {role!r} must not have the grant"
        # The grant must NOT leak into the
        # shared file-tool list (that would hand every role a write lane).
        from kiln_harness_tuner.dispatch.claude_cli import _FILE_TOOLS

        assert tool not in _FILE_TOOLS

    async def test_the_grant_reaches_the_rendered_argv(self):
        rc_data = {"rc_id": "rc_9", "build_notes": "n"}
        dispatcher, runner = make_dispatcher(
            [
                ok_proc(cli_payload(envelope_text(rc_data))),
                ok_proc(cli_payload(envelope_text(PLAN_DATA))),
            ]
        )
        item = ExperimentItem(
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
        await dispatcher.create_experiment_rc(
            RcBuildInput(item=item, baseline=BASELINE)
        )
        argv = runner.calls[0].argv
        tools = argv[argv.index("--allowedTools") + 1].split(",")
        assert "mcp__skill_installer__install_skill_bundle" in tools
        # …and not in a non-build role's argv.
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        argv = runner.calls[1].argv
        tools = argv[argv.index("--allowedTools") + 1].split(",")
        assert "mcp__skill_installer__install_skill_bundle" not in tools


class TestRenderedTargetBlock:
    """The rendered block was the ONLY path by which a host
    reached an agent, and all 61 curl calls in the 07-30 run copied it from
    there. The field survives on the model for the code that needs it."""

    async def test_no_host_reaches_the_prompt(self):
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        prompt = runner.calls[0].stdin_text
        assert "kiln_host" not in prompt
        assert TARGET.kiln_host not in prompt
        assert "localhost" not in prompt
        assert "8757" not in prompt

    async def test_the_ids_render_and_the_project_path_does_not(self):
        # The project tree is read-denied to every agent, so its
        # path is withheld from the prompt; the ids still render.
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        prompt = runner.calls[0].stdin_text
        assert TARGET.project_id in prompt
        assert TARGET.task_id in prompt
        assert TARGET.project_path not in prompt

    def test_the_model_still_carries_the_host(self):
        # Four live consumers: the per-run MCP host injection, KilnClient, the
        # preflight wave-log event, and the preflight failure message (asserted
        # in TestPreflight, where the preflight helpers live). Dropping the
        # field breaks the run's ability to reach Kiln at all.
        assert TARGET.model_dump()["kiln_host"] == "http://localhost:8757"


class TestScratchDir:
    async def test_created_and_injected_into_prompt(self, tmp_path):
        dispatcher, runner = make_wired_dispatcher(
            tmp_path, [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        scratch = tmp_path / "run" / "scratch" / "batch_plan-loop"
        assert scratch.is_dir()
        prompt = runner.calls[0].stdin_text
        assert f"scratch_dir: {scratch}" in prompt
        assert "you can only write files here" in prompt
        # Issue-1 guidance rides the shared render, not each template.
        assert "Reach the project through your gateway operations only" in prompt
        assert "Avoid writing files unless there's a good reason" in prompt

    async def test_item_dispatch_scratch_named_by_item_id(self, tmp_path):
        rc_data = {"rc_id": "rc_301", "build_notes": "cloned"}
        dispatcher, _ = make_wired_dispatcher(
            tmp_path, [ok_proc(cli_payload(envelope_text(rc_data)))]
        )
        item = ExperimentItem(
            id="w2-3",
            type="experiment",
            evaluation_target=[EvalTarget(eval_id="eval_1")],
            promotion_candidate=False,
            work=FixWork(
                issue_memory_id="mem_61",
                hypothesis="h",
                technique="prompt",
                predicted_effect="e",
                needs_eval=False,
                eval_id="eval_1",
            ),
        )
        await dispatcher.create_experiment_rc(
            RcBuildInput(item=item, baseline=BASELINE)
        )
        assert (tmp_path / "run" / "scratch" / "create_experiment_rc-w2-3").is_dir()

    async def test_unwired_dispatcher_has_no_scratch_line(self):
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert "scratch_dir:" not in runner.calls[0].stdin_text


class TestTraceCopy:
    async def test_success_copies_transcript(self, tmp_path):
        write_transcript(tmp_path, "sess-1", '{"turn": 1}\n')
        dispatcher, _ = make_wired_dispatcher(
            tmp_path, [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        dest = traces_dir(tmp_path) / "batch_plan-loop-sess-1.jsonl"
        assert dest.read_text() == '{"turn": 1}\n'
        # Success leaves no stderr file (issue 9: failures only).
        assert not list(traces_dir(tmp_path).glob("*-stderr.txt"))

    async def test_eval_build_labeled_by_issue_id(self, tmp_path):
        write_transcript(tmp_path, "sess-1")
        report_data = {
            "eval_id": "eval_9",
            "eval_config_id": "cfg_1",
            "eval_type": "llm_judge",
            "case_count": 3,
            "reproduced": True,
            "baseline_headline": "40% pass",
            "priority": "p1",
        }
        dispatcher, _ = make_wired_dispatcher(
            tmp_path, [ok_proc(cli_payload(envelope_text(report_data)))]
        )
        await dispatcher.eval_build(
            EvalBuildInput(issue_memory_id="mem_102", baseline=BASELINE)
        )
        assert (traces_dir(tmp_path) / "eval_build-mem_102-sess-1.jsonl").is_file()

    async def test_repair_retry_recopies_same_session(self, tmp_path):
        src = write_transcript(tmp_path, "sess-7", "first\n")
        dispatcher, runner = make_wired_dispatcher(
            tmp_path,
            [
                ok_proc(cli_payload("not json at all", session_id="sess-7")),
                ok_proc(cli_payload(envelope_text(PLAN_DATA), session_id="sess-7")),
            ],
        )

        # The transcript grows between rounds; the second copy overwrites.
        class GrowingRunner:
            async def run(self, argv, *, stdin_text, timeout_secs, cwd):
                result = await runner.run(
                    argv,
                    stdin_text=stdin_text,
                    timeout_secs=timeout_secs,
                    cwd=cwd,
                )
                src.write_text("first\nsecond\n")
                return result

        dispatcher._runner = GrowingRunner()
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "ok"
        dest = traces_dir(tmp_path) / "batch_plan-loop-sess-7.jsonl"
        assert dest.read_text() == "first\nsecond\n"

    async def test_missing_transcript_never_fails_dispatch(self, tmp_path):
        # No transcript written — the copy logs-and-continues (issue 12).
        dispatcher, _ = make_wired_dispatcher(
            tmp_path, [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "ok"
        assert not list(traces_dir(tmp_path).glob("*.jsonl"))

    async def test_nonzero_exit_persists_stderr(self, tmp_path):
        dispatcher, _ = make_wired_dispatcher(
            tmp_path,
            [SubprocessResult(returncode=1, stdout="", stderr="MCP spawn failed")],
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "failed_infra"
        stderr_file = traces_dir(tmp_path) / "batch_plan-loop-nosession-stderr.txt"
        assert stderr_file.read_text() == "MCP spawn failed"

    async def test_cli_is_error_persists_stderr_and_trace(self, tmp_path):
        write_transcript(tmp_path, "sess-1")
        dispatcher, runner = make_wired_dispatcher(
            tmp_path,
            [
                SubprocessResult(
                    returncode=0,
                    stdout=cli_payload("rate limit reached", is_error=True),
                    stderr="upstream 429",
                )
            ],
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "failed_infra"
        assert (traces_dir(tmp_path) / "batch_plan-loop-sess-1.jsonl").is_file()
        stderr_file = traces_dir(tmp_path) / "batch_plan-loop-sess-1-stderr.txt"
        assert stderr_file.read_text() == "upstream 429"

    async def test_unwired_dispatcher_copies_nothing(self, tmp_path):
        dispatcher, _ = make_dispatcher(
            [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert not (tmp_path / "traces").exists()


def preflight_payload(ping=None, status: str = "ok", detail=None) -> str:
    """`ping` is whatever the gateway's ping tool handed back: its JSON text
    `{"ok": true}` by default."""
    if ping is None:
        ping = '{"ok": true}'
    data = {"ping": ping} if status == "ok" else None
    return cli_payload(envelope_text(data, status=status, detail=detail))


class TestPreflight:
    async def test_ok_returns_usage(self, tmp_path):
        dispatcher, runner = make_wired_dispatcher(
            tmp_path, [ok_proc(preflight_payload())]
        )
        usage = await dispatcher.preflight()
        assert usage.cost_usd == pytest.approx(0.10)
        call = runner.calls[0]
        # The preflight session gets the gateway's ping tool and nothing
        # else, a 3-min timeout, and the ping instruction (no template).
        argv = call.argv
        tools = argv[argv.index("--allowedTools") + 1].split(",")
        assert tools == ["mcp__kiln_harness_tuner_gateway__ping"]
        assert call.timeout_secs == 3 * 60
        assert "`ping` tool" in call.stdin_text
        assert "--mcp-config" in argv and "--strict-mcp-config" in argv

    async def test_tool_missing_raises_instructive_error(self, tmp_path):
        dispatcher, _ = make_wired_dispatcher(
            tmp_path,
            [
                ok_proc(
                    preflight_payload(
                        status="failed_expected", detail="ping tool is not available"
                    )
                )
            ],
        )
        with pytest.raises(PreflightError) as exc_info:
            await dispatcher.preflight()
        message = str(exc_info.value)
        assert "ping tool is not available" in message
        assert str(tmp_path / "run" / "mcp_config.json") in message
        assert "http://localhost:8757" in message
        assert "studio server is not running" in message

    async def test_infra_failure_raises(self, tmp_path):
        dispatcher, _ = make_wired_dispatcher(
            tmp_path,
            [SubprocessResult(returncode=1, stdout="", stderr="claude not found")],
        )
        with pytest.raises(PreflightError) as exc_info:
            await dispatcher.preflight()
        assert "agent_crash" in str(exc_info.value)

    @pytest.mark.parametrize(
        "ping",
        [
            "404 not found",
            "pong",
            '{"error": "ConnectError: refused"}',
            '{"ok": false}',
            {"ok": "yes"},
            {"status_code": 200, "body": '"pong"'},
        ],
    )
    async def test_anything_but_ok_true_raises(self, tmp_path, ping):
        dispatcher, _ = make_wired_dispatcher(
            tmp_path, [ok_proc(preflight_payload(ping=ping))]
        )
        with pytest.raises(PreflightError) as exc_info:
            await dispatcher.preflight()
        assert "unexpected ping response" in str(exc_info.value)
        assert TARGET.kiln_host in str(exc_info.value)

    async def test_a_parsed_object_passes(self, tmp_path):
        dispatcher, _ = make_wired_dispatcher(
            tmp_path, [ok_proc(preflight_payload(ping={"ok": True}))]
        )
        assert (await dispatcher.preflight()).cost_usd == pytest.approx(0.10)

    async def test_preflight_trace_copied(self, tmp_path):
        write_transcript(tmp_path, "sess-1")
        dispatcher, _ = make_wired_dispatcher(tmp_path, [ok_proc(preflight_payload())])
        await dispatcher.preflight()
        assert (traces_dir(tmp_path) / "preflight-loop-sess-1.jsonl").is_file()


async def test_analyze_label_uses_item_id_not_memory_id(tmp_path):
    # AnalysisInput carries both `item` and `experiment_memory_id` — the
    # label precedence must pick item.id (getattr-chain order matters).
    write_transcript(tmp_path, "sess-1")
    res_data = {
        "successful": True,
        "findings": "f",
        "memory_overview": "EXP 0731-w3-2: x — +1%, n=2, p=.5",
        "memory_content": "c",
    }
    dispatcher, _ = make_wired_dispatcher(
        tmp_path, [ok_proc(cli_payload(envelope_text(res_data)))]
    )
    item = ExperimentItem(
        id="w3-2",
        type="experiment",
        evaluation_target=[EvalTarget(eval_id="eval_1")],
        promotion_candidate=False,
        work=FixWork(
            issue_memory_id="mem_61",
            hypothesis="h",
            technique="prompt",
            predicted_effect="e",
            needs_eval=False,
            eval_id="eval_1",
        ),
    )
    await dispatcher.analyze_experiment_results(
        AnalysisInput(
            item=item,
            rc_id="rc_300",
            bundle=AnalysisBundle(per_eval=[], spend_total_usd=0.0, errors=[]),
            experiment_memory_id="mem_400",
        )
    )
    dest = traces_dir(tmp_path) / "analyze_experiment_results-w3-2-sess-1.jsonl"
    assert dest.is_file()
    assert (tmp_path / "run" / "scratch" / "analyze_experiment_results-w3-2").is_dir()


async def test_describe_eval_round_trip_and_per_eval_label(tmp_path):
    # The label matters more here than anywhere else: the
    # fan-out runs SETUP_DESCRIBE_CONCURRENCY sessions at once, and the
    # per-session scratch sandbox is keyed by it — a shared `describe_eval-
    # loop` tree would put concurrent sessions in each other's files.
    write_transcript(tmp_path, "sess-1")
    description = {
        "overview": "replies drop the <handoff> block",
        "description": "Every reply must end with a <handoff> block.",
    }
    dispatcher, runner = make_wired_dispatcher(
        tmp_path, [ok_proc(cli_payload(envelope_text(description)))]
    )

    result = await dispatcher.describe_eval(
        DescribeEvalInput(
            eval_id="424242966189", eval_name="Handoff Block", spec_id="424242331708"
        )
    )

    assert result.status == "ok"
    assert isinstance(result.data, EvalDescription)
    assert result.data.overview == "replies drop the <handoff> block"
    assert (traces_dir(tmp_path) / "describe_eval-424242966189-sess-1.jsonl").is_file()
    assert (tmp_path / "run" / "scratch" / "describe_eval-424242966189").is_dir()
    # The template was rendered, with the read-only tool set and its own
    # timeout — a describe session never needs longer than the loop's own.
    assert "Kiln Harness Tuner describe-eval agent" in runner.calls[0].stdin_text
    assert runner.calls[0].timeout_secs == AGENT_TIMEOUTS_SECS["describe_eval"]


class TestAsyncioSubprocessRunner:
    """The one place the real runner is exercised against a real child — the
    unit tier is otherwise scripted."""

    async def spawn(self, monkeypatch, script: str, timeout_secs: float = 30.0):
        """Start a real `runner.run` on a child that outlives the test, and
        hand back (task, process). Only the process HANDLE is intercepted —
        the spawn, the kill and the reap are the real thing."""
        spawned: list = []
        started = asyncio.Event()
        real_exec = asyncio.create_subprocess_exec

        async def capture(*argv, **kwargs):
            proc = await real_exec(*argv, **kwargs)
            spawned.append(proc)
            started.set()
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
        task = asyncio.create_task(
            AsyncioSubprocessRunner().run(
                [sys.executable, "-c", script],
                stdin_text="",
                timeout_secs=timeout_secs,
                cwd=".",
            )
        )
        # Bounded: if the spawn itself fails (sandboxed CI, fd exhaustion),
        # the runner task dies and `started` is never set — wait_for turns
        # that into a fast, legible failure instead of a spin that no
        # pytest-timeout is installed to break.
        try:
            await asyncio.wait_for(started.wait(), 5)
        except TimeoutError:
            task.cancel()
            raise AssertionError(
                f"the child never spawned: {task.exception() if task.done() else task}"
            ) from None
        return task, spawned[0]

    async def test_the_child_s_pwd_is_its_working_folder(self, tmp_path, monkeypatch):
        # OpenCode takes its project folder from PWD: with the tuner's own
        # PWD it does not find the session folder's skills.
        monkeypatch.setenv("PWD", "/somewhere/else")
        result = await AsyncioSubprocessRunner().run(
            [sys.executable, "-c", "import os; print(os.environ['PWD'])"],
            stdin_text="",
            timeout_secs=30,
            cwd=str(tmp_path),
        )
        assert result.stdout.strip() == str(tmp_path)

    async def test_cancelling_kills_and_reaps_the_child(self, monkeypatch):
        # Without the kill, a cancelled dispatch leaves a live `claude -p`
        # session spending for the rest of its timeout — setup's describe
        # fan-out cancels siblings on a fatal, so this is a live path.
        task, proc = await self.spawn(monkeypatch, "import time; time.sleep(30)")

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # The SIGNAL, not just "it ended": `is not None` would also pass on a
        # child that exited by itself, so it would not notice a future
        # kill→terminate change silently failing on a child ignoring SIGTERM.
        assert proc.returncode == -signal.SIGKILL  # killed AND reaped

    async def test_a_timeout_kills_and_reaps_the_child(self, monkeypatch):
        # The branch with real production traffic, and the OTHER caller of
        # `_kill_and_reap`. The scripted SubprocessRunner seam raises
        # DispatchTimeoutError itself, so it cannot see the kill at all —
        # this is the blind spot the class exists to close.
        task, proc = await self.spawn(
            monkeypatch, "import time; time.sleep(30)", timeout_secs=0.2
        )

        with pytest.raises(DispatchTimeoutError, match="wall-clock timeout"):
            await task

        assert proc.returncode == -signal.SIGKILL  # not left running for 30s

    async def test_the_child_leads_its_own_process_group(self, monkeypatch):
        # start_new_session: the kill reaches MCP servers and
        # Bash children because they share the child's group.
        task, _ = await self.spawn(
            monkeypatch,
            "import os, sys; sys.stdout.write(str(os.getpgid(0) == os.getpid()))",
        )
        result = await task
        assert result.stdout == "True"

    async def test_secret_looking_env_names_are_withheld(self, monkeypatch):
        monkeypatch.setenv("KILN_HARNESS_TUNER_TEST_SECRET_TOKEN", "hunter2")
        monkeypatch.setenv("KILN_HARNESS_TUNER_TEST_PLAIN", "visible")
        task, _ = await self.spawn(
            monkeypatch,
            "import os, sys; sys.stdout.write("
            "','.join(k for k in "
            "('KILN_HARNESS_TUNER_TEST_SECRET_TOKEN','KILN_HARNESS_TUNER_TEST_PLAIN') "
            "if k in os.environ))",
        )
        result = await task
        assert result.stdout == "KILN_HARNESS_TUNER_TEST_PLAIN"

    async def test_a_normal_run_returns_the_childs_output(self, monkeypatch):
        task, proc = await self.spawn(
            monkeypatch, "import sys; sys.stdout.write('hi'); sys.exit(0)"
        )

        result = await task

        assert (result.returncode, result.stdout) == (0, "hi")
        assert proc.returncode == 0


class TestRunLogging:
    """The dispatcher's lifecycle moments reach `logging`, which
    `kiln_harness_tuner.logbook` points at `runs/<run_id>/kiln-harness-tuner.log`. A
    dispatch is the loop's longest silence — up to 30 minutes of one subprocess — so
    spawn, exit, timeout, crash and cost each have to leave a line."""

    async def test_spawn_and_completion_are_logged_with_cost_and_session(self, caplog):
        dispatcher, _ = make_dispatcher(
            [ok_proc(cli_payload(envelope_text(PLAN_DATA), cost_usd=1.25))]
        )
        with caplog.at_level("INFO", logger="kiln_harness_tuner.dispatch.claude_cli"):
            await dispatcher.batch_plan(BATCH_PLAN_INPUT)

        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "dispatch batch_plan-loop: spawning claude -p (timeout 900s)" in text
        assert "dispatch batch_plan-loop: claude -p exited 0 after" in text
        assert "dispatch batch_plan-loop: session sess-1 · 1200 tokens" in text

    async def test_a_timeout_is_logged_at_error(self, caplog):
        dispatcher, _ = make_dispatcher(
            [DispatchTimeoutError("claude -p exceeded 60s")]
        )
        with caplog.at_level("INFO", logger="kiln_harness_tuner.dispatch.claude_cli"):
            result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)

        assert result.status == "failed_infra"
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert any("TIMEOUT after" in r.getMessage() for r in errors)

    async def test_a_nonzero_exit_logs_the_stderr_tail(self, caplog):
        crash = SubprocessResult(
            returncode=1,
            stdout="",
            stderr='MCP server "kiln_api_mcp" failed to start\nTraceback…',
        )
        dispatcher, _ = make_dispatcher([crash])
        with caplog.at_level("INFO", logger="kiln_harness_tuner.dispatch.claude_cli"):
            result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)

        assert result.status == "failed_infra"
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "NONZERO EXIT 1 — stderr tail:" in text
        # The process-layer detail nothing else keeps.
        assert 'MCP server "kiln_api_mcp" failed to start' in text

    async def test_a_cli_error_and_a_rejected_envelope_are_logged(self, caplog):
        dispatcher, _ = make_dispatcher(
            [ok_proc(cli_payload("rate limit reached", is_error=True))]
        )
        with caplog.at_level("INFO", logger="kiln_harness_tuner.dispatch.claude_cli"):
            await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert any(
            "CLI reported an error (rate_limit)" in r.getMessage()
            for r in caplog.records
        )

        caplog.clear()
        # No fence at all, twice — the repair retry then the parse_failure.
        dispatcher, _ = make_dispatcher(
            [ok_proc(cli_payload("no json here")), ok_proc(cli_payload("still none"))]
        )
        with caplog.at_level("INFO", logger="kiln_harness_tuner.dispatch.claude_cli"):
            await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 2  # the first attempt and the repair retry
        assert all("envelope rejected" in r.getMessage() for r in warnings)

    async def test_a_stderr_tail_on_a_clean_exit_is_debug_only(self, caplog):
        noisy = SubprocessResult(
            returncode=0,
            stdout=cli_payload(envelope_text(PLAN_DATA)),
            stderr="deprecation: --foo is going away",
        )
        dispatcher, _ = make_dispatcher([noisy])
        with caplog.at_level("INFO", logger="kiln_harness_tuner.dispatch.claude_cli"):
            await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert "deprecation" not in "\n".join(r.getMessage() for r in caplog.records)

        caplog.clear()
        dispatcher, _ = make_dispatcher([noisy])
        with caplog.at_level("DEBUG", logger="kiln_harness_tuner.dispatch.claude_cli"):
            await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert "deprecation" in "\n".join(r.getMessage() for r in caplog.records)


# ---- per-role model/effort + turn cap ----


class TestRoleOverridesAndCaps:
    async def test_default_argv_pins_model_and_effort_with_no_turn_cap(self):
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        argv = runner.calls[0].argv
        assert argv[argv.index("--model") + 1] == "opus"
        assert argv[argv.index("--effort") + 1] == "high"
        # Uncapped by default: long sessions are productive, not loops —
        # --max-turns is an opt-in operator knob.
        assert "--max-turns" not in argv

    async def test_per_role_overrides_reach_argv(self):
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(envelope_text(PLAN_DATA)))],
            models={"batch_plan": "sonnet"},
            efforts={"batch_plan": "medium"},
            max_turns=100,
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        argv = runner.calls[0].argv
        assert argv[argv.index("--model") + 1] == "sonnet"
        assert argv[argv.index("--effort") + 1] == "medium"
        assert argv[argv.index("--max-turns") + 1] == "100"

    async def test_unlisted_role_keeps_the_pinned_defaults(self):
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(envelope_text(PLAN_DATA)))],
            models={"describe_eval": "haiku"},
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        argv = runner.calls[0].argv
        assert argv[argv.index("--model") + 1] == "opus"

    def test_every_per_role_model_default_names_a_real_role(self):
        assert set(AGENT_MODEL_DEFAULTS) <= set(AGENT_TIMEOUTS_SECS)


class TestSecretHygiene:
    """Transcripts are committed and agents hold Bash: known
    credential shapes are blanked on copy, secret-looking env names never
    reach the child."""

    def test_redact_secrets_blanks_known_shapes_and_nothing_else(self):
        from kiln_harness_tuner.dispatch.claude_cli import redact_secrets

        text = (
            "key sk-ant-api03-abcdefghijklmnop rest; "
            "or sk-or-v1-0123456789abcdef0123; "
            "gh ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345; "
            "parse r:abcdefghijklmnopqrstuvwxyz0123; "
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig; "
            '{\\"Authorization\\": \\"r:zyxwvutsrqponmlkjihgfedcba9876\\"} '
            '{"api_key": "plain-secret-value"} '
            "tokens 1200 error: mem_429 sk-short"
        )
        out = redact_secrets(text)
        for leaked in (
            "abcdefghijklmnop",
            "0123456789abcdef0123",
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
            "abcdefghijklmnopqrstuvwxyz0123",
            "eyJhbGciOiJIUzI1NiJ9",
            "zyxwvutsrqponmlkjihgfedcba9876",
            "plain-secret-value",
        ):
            assert leaked not in out, leaked
        assert out.count("[REDACTED]") == 7
        # Ordinary text, token COUNTS and short ids survive byte-for-byte.
        for kept in ("tokens 1200", "error: mem_429", "sk-short", "Bearer "):
            assert kept in out, kept
        assert redact_secrets("nothing secret here") == "nothing secret here"

    def test_child_env_withholds_secret_names_unless_kept(self):
        from kiln_harness_tuner.dispatch.claude_cli import child_env

        env, withheld = child_env(
            {
                "PATH": "/bin",
                "ANTHROPIC_API_KEY": "sk-ant-x",
                "GITHUB_TOKEN": "ghp_x",
                "DB_PASSWORD": "x",
                "CLAUDE_CODE_OAUTH_TOKEN": "keep-me",
                "HTTPS_PROXY": "http://proxy",
                "KILN_HARNESS_TUNER_CHILD_ENV_KEEP": "GITHUB_TOKEN",
            }
        )
        assert withheld == ["ANTHROPIC_API_KEY", "DB_PASSWORD"]
        assert set(env) == {
            "PATH",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "HTTPS_PROXY",
            "KILN_HARNESS_TUNER_CHILD_ENV_KEEP",
            "GITHUB_TOKEN",
        }

    async def test_trace_copy_is_redacted(self, tmp_path):
        write_transcript(
            tmp_path, "sess-1", '{"tool_result": "Bearer abcdefghijklmnopqrstu"}\n'
        )
        dispatcher, _ = make_wired_dispatcher(
            tmp_path, [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        dest = traces_dir(tmp_path) / "batch_plan-loop-sess-1.jsonl"
        assert dest.read_text() == '{"tool_result": "Bearer [REDACTED]"}\n'


class TestTimeoutRecovery:
    """A session killed at its cap never prints `total_cost_usd` or a
    session id: the most expensive dispatch shape would book $0 and copy
    nothing."""

    TRANSCRIPT = (
        '{"type":"user","message":{"role":"user","content":"hi"}}\n'
        '{"type":"assistant","message":{"usage":{"input_tokens":100,'
        '"output_tokens":20,"cache_read_input_tokens":300,'
        '"cache_creation_input_tokens":5}}}\n'
        '{"type":"assistant","message":{"usage":{"input_tokens":50,'
        '"output_tokens":25,"token":"sk-ant-api03-abcdefghijklmnop"}}}\n'
        "not json\n"
    )

    def test_transcript_usage_tokens_sums_every_turn(self, tmp_path):
        from kiln_harness_tuner.dispatch.claude_cli import transcript_usage_tokens

        path = tmp_path / "s.jsonl"
        path.write_text(self.TRANSCRIPT)
        assert transcript_usage_tokens(path) == 100 + 20 + 300 + 5 + 50 + 25

    async def test_timeout_recovers_the_transcript_at_the_chosen_session_id(
        self, tmp_path
    ):
        src_dir = tmp_path / "projects" / AGENTS_SLUG
        src_dir.mkdir(parents=True)
        # A sibling still RUNNING (its transcript is newer than ours) — the
        # find-by-elimination would pick it, and the resume path would then
        # resume the sibling.
        transcript = self.TRANSCRIPT
        chosen: list[str] = []

        class WritesThenTimesOut:
            async def run(self, argv, *, stdin_text, timeout_secs, cwd):
                session_id = argv[argv.index("--session-id") + 1]
                chosen.append(session_id)
                (src_dir / f"{session_id}.jsonl").write_text(transcript)
                (src_dir / "sibling-still-running.jsonl").write_text(transcript * 3)
                raise DispatchTimeoutError("claude -p exceeded 1s wall-clock timeout")

        dispatcher, _ = make_wired_dispatcher(tmp_path, [])
        dispatcher._runner = WritesThenTimesOut()

        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)

        assert result.status == "failed_infra"
        assert result.error is not None and result.error.error_class == "timeout"
        assert result.usage.cost_usd == 0.0
        assert result.usage.tokens == 500  # ours, not the sibling's
        (session_id,) = chosen
        uuid.UUID(session_id)  # a valid uuid, as the CLI requires
        dest = traces_dir(tmp_path) / f"batch_plan-loop-{session_id}-timeout.jsonl"
        assert dest.is_file()
        assert "sk-ant-api03-abcdefghijklmnop" not in dest.read_text()
        assert "[REDACTED]" in dest.read_text()

    async def test_timeout_with_no_new_transcript_books_no_tokens(self, tmp_path):
        dispatcher, _ = make_wired_dispatcher(
            tmp_path, [DispatchTimeoutError("claude -p exceeded 1s")]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "failed_infra"
        assert result.usage.tokens == 0
        assert not list(traces_dir(tmp_path).glob("*-timeout.jsonl"))


class TestAgentLaneBreaker:
    """Quota/auth from `claude -p` stop the run like the eval lane's
    breaker; they are not `provider_error`, which walks the ladder."""

    def test_classifier_knows_quota_auth_and_the_cli_usage_limit(self):
        from kiln_harness_tuner.dispatch.claude_cli import _classify_cli_error

        assert _classify_cli_error("Claude AI usage limit reached|1757400000") == (
            "quota_exhausted"
        )
        assert _classify_cli_error(
            '{"code":402,"message":"requires more credits"}'
        ) == ("quota_exhausted")
        assert _classify_cli_error("Invalid API key · Please run /login") == "auth"
        assert (
            _classify_cli_error("API rate limit reached, retry later") == "rate_limit"
        )
        assert _classify_cli_error("something else broke") == "provider_error"

    async def test_quota_error_raises_the_circuit_breaker(self):
        from kiln_harness_tuner.provider_errors import ProviderQuotaError

        dispatcher, _ = make_dispatcher(
            [
                ok_proc(
                    json.dumps(
                        {
                            "is_error": True,
                            "result": "Claude AI usage limit reached|1757400000",
                            "session_id": "sess-q",
                        }
                    )
                )
            ]
        )
        with pytest.raises(ProviderQuotaError, match="usage limit"):
            await dispatcher.batch_plan(BATCH_PLAN_INPUT)


class TestResumeOnTimeout:
    """A build session killed at its cap is resumed once with a short cap
    and told to finish with what it already created — a fresh retry would
    duplicate every artifact it had POSTed."""

    async def test_timed_out_build_is_resumed_once(self, tmp_path):
        src_dir = tmp_path / "projects" / AGENTS_SLUG
        src_dir.mkdir(parents=True)
        calls: list[list[str]] = []
        payload = cli_payload(
            envelope_text({"rc_id": "rc_new", "build_notes": "finished on resume"}),
            session_id="orphan-7",
        )

        class TimesOutThenFinishes:
            async def run(self, argv, *, stdin_text, timeout_secs, cwd):
                calls.append(list(argv))
                if len(calls) == 1:
                    session_id = argv[argv.index("--session-id") + 1]
                    (src_dir / f"{session_id}.jsonl").write_text(
                        '{"message":{"usage":{"input_tokens":10,"output_tokens":5}}}\n'
                    )
                    raise DispatchTimeoutError("claude -p exceeded 1800s")
                assert timeout_secs == 600.0  # the short resume cap
                assert "finish" in stdin_text.lower()
                return ok_proc(payload)

        from conftest import fix_item

        dispatcher, _ = make_wired_dispatcher(tmp_path, [])
        dispatcher._runner = TimesOutThenFinishes()

        result = await dispatcher.create_experiment_rc(
            RcBuildInput(item=fix_item(), baseline=BASELINE)
        )

        assert result.status == "ok"
        assert result.data is not None and result.data.rc_id == "rc_new"
        assert len(calls) == 2
        chosen = calls[0][calls[0].index("--session-id") + 1]
        assert calls[1][calls[1].index("--resume") + 1] == chosen
        assert "--session-id" not in calls[1]  # resume names the session
        assert result.usage.tokens == 15 + 1200  # the transcript, then the resume

    async def test_roles_that_create_nothing_are_not_resumed(self, tmp_path):
        src_dir = tmp_path / "projects" / AGENTS_SLUG
        src_dir.mkdir(parents=True)
        calls = 0

        class TimesOut:
            async def run(self, argv, *, stdin_text, timeout_secs, cwd):
                nonlocal calls
                calls += 1
                (src_dir / "orphan-1.jsonl").write_text("{}\n")
                raise DispatchTimeoutError("claude -p exceeded 900s")

        dispatcher, _ = make_wired_dispatcher(tmp_path, [])
        dispatcher._runner = TimesOut()
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "failed_infra"
        assert calls == 1


# ---- env/redaction gaps, bare status numbers, stale timed-out entries,
# ---- the breaker's usage


class TestReviewFollowUps:
    def test_child_env_withholds_pat_key_auth_names_and_credential_urls(self):
        from kiln_harness_tuner.dispatch.claude_cli import child_env

        env, withheld = child_env(
            {
                "PATH": "/bin",
                "GH_PAT": "github_pat_x",
                "OPENROUTER_KEY": "sk-or-x",
                "NPM_AUTH": "x",
                "DATABASE_URL": "postgres://u:pw@h/db",
                "PUBLIC_URL": "https://example.com/path",
                "PATTERN": "not a pat",
                "KEYBOARD": "not a key",
            }
        )
        assert withheld == ["DATABASE_URL", "GH_PAT", "NPM_AUTH", "OPENROUTER_KEY"]
        assert set(env) == {"PATH", "PUBLIC_URL", "PATTERN", "KEYBOARD"}

    def test_redact_secrets_covers_fine_grained_pats_unquoted_keys_and_urls(self):
        from kiln_harness_tuner.dispatch.claude_cli import redact_secrets

        text = (
            "github_pat_11ABCDEFG0123456789abcdefghij; "
            "token: yaml-style-secret-value\n"
            "api_key=ini-style-secret-value\n"
            "postgres://alice:s3cretpw@db.example/app; "
            "token: short; tokens: 1200"
        )
        out = redact_secrets(text)
        for leaked in (
            "11ABCDEFG0123456789abcdefghij",
            "yaml-style-secret-value",
            "ini-style-secret-value",
            "s3cretpw",
        ):
            assert leaked not in out, leaked
        assert "postgres://alice:[REDACTED]@db.example/app" in out
        assert "token: short" in out and "tokens: 1200" in out

    def test_a_bare_status_number_in_agent_text_is_not_the_breaker(self):
        from kiln_harness_tuner.dispatch.claude_cli import _classify_cli_error
        from kiln_harness_tuner.provider_errors import classify_provider_message

        text = "the probe returned 401 from the staging host; see line 402"
        assert _classify_cli_error(text) == "provider_error"
        # The job-error lane keeps reading the number: those bodies are
        # provider responses and nothing else.
        assert classify_provider_message("HTTP 402") == "quota_exhausted"
        assert classify_provider_message("HTTP 401") == "auth"

    async def test_quota_error_carries_the_sessions_usage(self):
        from kiln_harness_tuner.provider_errors import ProviderQuotaError

        payload = json.dumps(
            {
                "is_error": True,
                "result": "Claude AI usage limit reached|1757400000",
                "session_id": "sess-q",
                "total_cost_usd": 0.75,
            }
        )
        dispatcher, _ = make_dispatcher([ok_proc(payload)])
        with pytest.raises(ProviderQuotaError) as info:
            await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert info.value.usage is not None and info.value.usage.cost_usd == 0.75

    async def test_a_stale_timed_out_entry_is_not_resumed_by_the_next_attempt(
        self, tmp_path
    ):
        from conftest import fix_item

        dispatcher, _ = make_wired_dispatcher(
            tmp_path, [DispatchTimeoutError("claude -p exceeded 1800s")]
        )
        # Debris from an earlier attempt of the same label.
        dispatcher._timed_out_sessions["create_experiment_rc-w1-1"] = "old-session"
        result = await dispatcher.create_experiment_rc(
            RcBuildInput(item=fix_item(), baseline=BASELINE)
        )
        assert result.status == "failed_infra"
        assert len(dispatcher._runner.calls) == 1  # no resume of "old-session"
        assert "create_experiment_rc-w1-1" not in dispatcher._timed_out_sessions


async def test_every_dispatch_leaves_a_step_record(tmp_path):
    # runs/<id>/steps/NNN-<label>.json carries the request the
    # agent was given and the result it returned — the viewer's step-through.
    dispatcher, _runner = make_wired_dispatcher(
        tmp_path,
        [
            ok_proc(
                cli_payload(
                    envelope_text(
                        {
                            "items": [],
                            "rejected_summary": "nothing",
                            "open_questions": [],
                        }
                    ),
                    session_id="s1",
                )
            )
        ],
    )
    write_transcript(tmp_path, "s1")
    result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
    assert result.status == "ok"
    (path,) = sorted((tmp_path / "run" / "steps").glob("*.json"))
    assert path.name == "001-batch_plan-loop.json"
    record = json.loads(path.read_text())
    assert record["agent"] == "batch_plan" and record["label"] == "batch_plan-loop"
    assert record["request"]["baseline"] == BATCH_PLAN_INPUT.baseline.model_dump()
    assert record["result"]["status"] == "ok"
    assert record["result"]["data"]["rejected_summary"] == "nothing"
    assert record["elapsed_secs"] >= 0


async def test_the_session_end_hook_runs_after_the_step_record(tmp_path):
    # cli.py forwards the gateway's memory lines from this hook, so it runs
    # once per session, after the session's step is on disk.
    dispatcher, _runner = make_wired_dispatcher(
        tmp_path, [ok_proc(cli_payload(envelope_text(PLAN_DATA), session_id="s1"))]
    )
    write_transcript(tmp_path, "s1")
    seen = []

    def hook(agent: str, label: str) -> None:
        steps = sorted(p.name for p in (tmp_path / "run" / "steps").glob("*.json"))
        seen.append((agent, label, steps))

    dispatcher.on_session_end = hook
    await dispatcher.batch_plan(BATCH_PLAN_INPUT)
    assert seen == [("batch_plan", "batch_plan-loop", ["001-batch_plan-loop.json"])]


async def test_a_failing_session_end_hook_does_not_fail_the_dispatch(caplog):
    dispatcher, _runner = make_dispatcher(
        [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
    )

    def hook(agent: str, label: str) -> None:
        raise RuntimeError("the hook broke")

    dispatcher.on_session_end = hook
    result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
    assert result.status == "ok"
    assert "session-end hook failed for batch_plan-loop" in caplog.text


class TestReadOnlyRoles:
    """The val readers run under `settings.readonly.json` and get
    no scratch; every other role keeps the ordinary settings and a scratch."""

    PROMO = {
        "winner_rc_id": None,
        "winner_experiment_memory_id": None,
        "rationale": "none",
        "regressed_issues": [],
    }

    def _dispatcher(self, tmp_path, script):
        runner = FakeRunner(script=list(script))
        dispatcher = ClaudeCliDispatcher(
            TARGET,
            AGENTS_DIR,
            runner=runner,
            run_id="260924-0900",
            run_dir=tmp_path / "run",
            mcp_config_path=tmp_path / "run" / "mcp_config.json",
            settings_path=tmp_path / "run" / "settings.json",
            readonly_settings_path=tmp_path / "run" / "settings.readonly.json",
            traces_root=tmp_path / "traces",
            claude_projects_dir=tmp_path / "projects",
        )
        return dispatcher, runner

    @staticmethod
    def _settings(argv: list[str]) -> str:
        return argv[argv.index("--settings") + 1]

    async def test_the_promotion_agent_is_read_only(self, tmp_path):
        from kiln_harness_tuner.dispatch.claude_cli import READ_ONLY_ROLES

        assert READ_ONLY_ROLES == {
            "promote_best",
            "report_writer",
            "integrity_review",
            "generality_review",
        }
        dispatcher, runner = self._dispatcher(
            tmp_path, [ok_proc(cli_payload(envelope_text(self.PROMO)))]
        )
        await dispatcher.promote_best(PromotionInput(candidates=[], baseline=BASELINE))
        (call,) = runner.calls
        assert self._settings(call.argv).endswith("settings.readonly.json")
        assert "scratch_dir:" not in call.stdin_text
        assert not (tmp_path / "run" / "scratch").exists()

    async def test_the_planner_keeps_the_ordinary_settings(self, tmp_path):
        dispatcher, runner = self._dispatcher(
            tmp_path, [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        (call,) = runner.calls
        assert self._settings(call.argv).endswith("/settings.json")
        assert "scratch_dir:" in call.stdin_text


class TestRunDirDeniedByDefault:
    """An entry of the run directory that did not exist when the
    settings were rendered — listed in RUN_DIR_ENTRIES or not — is denied
    before the next session starts. Only scratch/ stays open."""

    def _run_dir(self, tmp_path):
        run = tmp_path / "run"
        run.mkdir()
        for name in ("settings.json", "settings.readonly.json"):
            (run / name).write_text(json.dumps({"permissions": {"deny": ["WebFetch"]}}))
        return run

    async def test_a_new_entry_is_denied_before_the_next_session(self, tmp_path):
        run = self._run_dir(tmp_path)
        (run / "late_writer.json").write_text("{}")
        (run / "late_dir").mkdir()
        dispatcher, _runner = TestReadOnlyRoles()._dispatcher(
            tmp_path, [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        root = str(run.resolve()).lstrip("/")
        for name in ("settings.json", "settings.readonly.json"):
            deny = json.loads((run / name).read_text())["permissions"]["deny"]
            assert f"Read(//{root}/late_writer.json)" in deny, name
            assert f"Read(//{root}/late_dir/**)" in deny, name
            assert not any("/scratch" in rule for rule in deny), name
            assert deny[0] == "WebFetch"  # the rest of the settings kept

    def test_it_is_idempotent(self, tmp_path):
        from kiln_harness_tuner.dispatch.run_fence import refresh_run_dir_denials

        run = self._run_dir(tmp_path)
        (run / "x.json").write_text("{}")
        files = [run / "settings.json"]
        assert refresh_run_dir_denials(run, files)
        assert refresh_run_dir_denials(run, files) == []


class TestTheIntegrityReviewer:
    """The reviewer runs with no MCP server and every built-in
    tool refused, under the read-only settings and with no scratch."""

    async def test_no_tools_no_mcp_read_only(self, tmp_path):
        from kiln_harness_tuner.schemas import IntegrityReviewInput

        dispatcher, runner = TestReadOnlyRoles()._dispatcher(
            tmp_path, [ok_proc(cli_payload(envelope_text({"flags": [], "notes": ""})))]
        )
        await dispatcher.integrity_review(
            IntegrityReviewInput(
                candidate_rc_id="r",
                candidate_instructions="c",
                baseline_instructions="b",
                judge_texts={"j": "t"},
            )
        )
        (call,) = runner.calls
        argv = call.argv
        assert "--mcp-config" not in argv and "--strict-mcp-config" in argv
        disallowed = argv[argv.index("--disallowedTools") + 1].split(",")
        assert {"Bash", "Read", "Grep", "Glob", "WebFetch"} <= set(disallowed)
        assert "--allowedTools" not in argv
        assert argv[argv.index("--settings") + 1].endswith("settings.readonly.json")
        assert "scratch_dir:" not in call.stdin_text


class TestTheGeneralityReviewer:
    """It runs like the integrity reviewer — no MCP server,
    every built-in tool refused, read-only settings, no scratch."""

    @staticmethod
    def _assert_no_tools(call) -> None:
        argv = call.argv
        assert "--mcp-config" not in argv and "--strict-mcp-config" in argv
        disallowed = argv[argv.index("--disallowedTools") + 1].split(",")
        assert {"Bash", "Read", "Grep", "Glob", "WebFetch"} <= set(disallowed)
        assert "--allowedTools" not in argv
        assert argv[argv.index("--settings") + 1].endswith("settings.readonly.json")
        assert "scratch_dir:" not in call.stdin_text

    async def test_the_generality_reviewer(self, tmp_path):
        from kiln_harness_tuner.schemas import GeneralityReviewInput

        dispatcher, runner = TestReadOnlyRoles()._dispatcher(
            tmp_path, [ok_proc(cli_payload(envelope_text({"leaks": ["x"]})))]
        )
        result = await dispatcher.generality_review(
            GeneralityReviewInput(
                item_id="w2-3",
                candidate_rc_id="r",
                candidate_instructions="c",
                parent_instructions="p",
            )
        )
        assert result.data is not None and result.data.leaks == ["x"]
        (call,) = runner.calls
        self._assert_no_tools(call)
        # The template leads, and each item's review keeps its own label.
        assert call.stdin_text.startswith(
            "# Kiln Harness Tuner generality-review agent"
        )
        steps = sorted((tmp_path / "run" / "steps").glob("*.json"))
        assert steps[-1].name.endswith("generality_review-w2-3.json")

    async def _review_model(self, **kwargs) -> str:
        from kiln_harness_tuner.schemas import GeneralityReviewInput

        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(envelope_text({"leaks": []})))], **kwargs
        )
        await dispatcher.generality_review(
            GeneralityReviewInput(
                item_id="w2-3",
                candidate_rc_id="r",
                candidate_instructions="c",
                parent_instructions="p",
            )
        )
        argv = runner.calls[0].argv
        assert argv[argv.index("--effort") + 1] == "high"
        return argv[argv.index("--model") + 1]

    async def test_it_runs_on_sonnet_by_default(self):
        assert await self._review_model() == "sonnet"

    async def test_an_agent_model_override_replaces_sonnet(self):
        models = {"generality_review": "opus"}
        assert await self._review_model(models=models) == "opus"

    async def test_an_override_for_another_role_keeps_sonnet(self):
        models = {"batch_plan": "haiku"}
        assert await self._review_model(models=models) == "sonnet"

    async def test_another_role_still_gets_opus(self):
        dispatcher, runner = make_dispatcher(
            [ok_proc(cli_payload(envelope_text(PLAN_DATA)))]
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        argv = runner.calls[0].argv
        assert argv[argv.index("--model") + 1] == "opus"
