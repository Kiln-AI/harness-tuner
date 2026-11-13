"""`--agent-cli`: parsing the command, choosing the adapter, and the start-up
check that sends one short prompt through it. No live calls."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest
from test_dispatch_claude_cli import TARGET, FakeRunner, ok_proc

from kiln_harness_tuner.cli import (
    DISPATCHERS,
    ConfigRenderError,
    build_parser,
    check_agent_cli,
    check_role_efforts,
    make_dispatcher,
)
from kiln_harness_tuner.dispatch.agent_cli import DEFAULT_AGENT_CLI, parse_agent_cli
from kiln_harness_tuner.dispatch.claude_cli import (
    CLI_CHECK_PROMPT,
    CLI_CHECK_WORD,
    ClaudeCliDispatcher,
    DispatchTimeoutError,
    PreflightError,
    SubprocessResult,
)
from kiln_harness_tuner.dispatch.codex_cli import CodexCliDispatcher
from kiln_harness_tuner.dispatch.opencode_cli import OpenCodeCliDispatcher

AGENTS_DIR = Path("/nonexistent/state/agents/claude/0.1.0")
REQUIRED = ["--project-path", "p", "--project-id", "1", "--task-id", "2"]


class TestParsing:
    def test_the_default_is_claude(self):
        assert build_parser().parse_args(REQUIRED).agent_cli == DEFAULT_AGENT_CLI
        cli = parse_agent_cli(DEFAULT_AGENT_CLI)
        assert (cli.kind, cli.bin, cli.options, cli.model) == (
            "claude",
            "claude",
            (),
            None,
        )

    def test_codex_with_a_model(self):
        cli = parse_agent_cli("codex exec -m gpt-6.1-sol --oss")
        assert cli.kind == "codex"
        assert cli.model == "gpt-6.1-sol"
        assert cli.options == ("--oss",)

    @pytest.mark.parametrize(
        "effort_option",
        [
            ["-c", "model_reasoning_effort=xhigh"],
            ["--config", 'model_reasoning_effort="xhigh"'],
            ["-c=model_reasoning_effort=xhigh"],
            ["-c", "model_reasoning_effort=low", "-c", "model_reasoning_effort=xhigh"],
        ],
    )
    def test_codex_effort_is_its_reasoning_effort_config(self, effort_option):
        text = shlex.join(
            ["codex", "exec", "-m", "gpt-6.1-sol", *effort_option, "-c", "a.b=1"]
        )
        cli = parse_agent_cli(text)
        assert cli.effort == "xhigh"
        assert cli.options == ("-c", "a.b=1")

    def test_an_unknown_claude_effort_is_refused(self):
        # Claude Code would ignore it with a warning and use its own default.
        with pytest.raises(ValueError, match="not a Claude Code effort level"):
            parse_agent_cli("claude -p --effort extreme")

    def test_opencode_with_a_model_and_a_variant(self):
        cli = parse_agent_cli(
            "opencode run -m zai-coding-plan/glm-5.3-flash --variant high"
        )
        assert cli.kind == "opencode"
        assert cli.model == "zai-coding-plan/glm-5.3-flash"
        assert cli.effort == "high"
        assert cli.options == ()

    def test_claude_flags_anywhere_and_equals_form(self):
        cli = parse_agent_cli("claude --model=sonnet -p --effort medium --verbose")
        assert (cli.model, cli.effort, cli.options) == (
            "sonnet",
            "medium",
            ("--verbose",),
        )

    def test_the_subcommand_may_be_left_out(self):
        assert parse_agent_cli("codex -m x").model == "x"
        assert parse_agent_cli("opencode").options == ()

    def test_a_path_to_the_executable(self):
        cli = parse_agent_cli("/opt/bin/codex exec")
        assert (cli.kind, cli.bin) == ("codex", "/opt/bin/codex")

    @pytest.mark.parametrize("text", ["", "aider --yes", "codex exec -m"])
    def test_refused(self, text):
        with pytest.raises(ValueError):
            parse_agent_cli(text)

    def test_each_kind_has_an_adapter(self):
        assert {k: v[0] for k, v in DISPATCHERS.items()} == {
            "claude": ClaudeCliDispatcher,
            "codex": CodexCliDispatcher,
            "opencode": OpenCodeCliDispatcher,
        }

    def test_the_command_reaches_the_adapter(self):
        dispatcher = make_dispatcher(
            parse_agent_cli("codex exec -m m1 --oss"), TARGET, AGENTS_DIR
        )
        assert isinstance(dispatcher, CodexCliDispatcher)
        argv = dispatcher._argv("batch_plan", label="b")
        assert argv[argv.index("-m") + 1] == "m1"
        assert "--oss" in argv


class TestTheRunWideModel:
    def test_claude_keeps_its_pinned_defaults(self):
        dispatcher = make_dispatcher(parse_agent_cli("claude -p"), TARGET, AGENTS_DIR)
        argv = dispatcher._argv("generality_review")
        assert argv[argv.index("--model") + 1] == "sonnet"
        argv = dispatcher._argv("batch_plan")
        assert argv[argv.index("--model") + 1] == "opus"

    def test_a_named_model_replaces_them(self):
        dispatcher = make_dispatcher(
            parse_agent_cli("claude -p --model haiku --effort low"),
            TARGET,
            AGENTS_DIR,
            models={"batch_plan": "opus"},
        )
        argv = dispatcher._argv("generality_review")
        assert argv[argv.index("--model") + 1] == "haiku"
        assert argv[argv.index("--effort") + 1] == "low"
        argv = dispatcher._argv("batch_plan")
        assert argv[argv.index("--model") + 1] == "opus"
        assert argv.count("-p") == 1


def test_a_codex_effort_reaches_every_session():
    dispatcher = make_dispatcher(
        parse_agent_cli("codex exec -c model_reasoning_effort=xhigh"),
        TARGET,
        AGENTS_DIR,
        efforts={"batch_plan": "low"},
    )
    argv = dispatcher._argv("generality_review")
    assert "model_reasoning_effort=" not in " ".join(dispatcher._cli_options)
    assert 'model_reasoning_effort="xhigh"' in argv
    assert 'model_reasoning_effort="low"' in dispatcher._argv("batch_plan")


def test_a_role_effort_claude_code_lacks_is_refused():
    claude = parse_agent_cli("claude -p")
    check_role_efforts(claude, {"batch_plan": "max"})
    with pytest.raises(ConfigRenderError, match="--agent-effort batch_plan"):
        check_role_efforts(claude, {"batch_plan": "ultra"})
    # Codex checks its own levels in the start-up prompt.
    check_role_efforts(parse_agent_cli("codex exec"), {"batch_plan": "ultra"})


def claude_answer(text: str, is_error: bool = False) -> str:
    return json.dumps(
        {
            "session_id": "s",
            "result": text,
            "is_error": is_error,
            "total_cost_usd": 0.01,
        }
    )


class TestTheCheck:
    async def test_an_answer_passes(self):
        runner = FakeRunner(script=[ok_proc(claude_answer(CLI_CHECK_WORD))])
        dispatcher = ClaudeCliDispatcher(TARGET, AGENTS_DIR, runner=runner)
        usage = await dispatcher.check_cli("claude -p")
        assert usage.cost_usd == pytest.approx(0.01)
        (call,) = runner.calls
        assert call.stdin_text == CLI_CHECK_PROMPT
        # No tools and no MCP server.
        assert "--strict-mcp-config" in call.argv
        assert "--mcp-config" not in call.argv

    async def test_an_answer_in_a_verbose_event_list_passes(self):
        # With verbose on, `claude -p --output-format json` prints a list of
        # events. The check reads the last result event.
        events = [
            {"type": "system", "subtype": "init", "session_id": "s"},
            {"type": "assistant", "message": {"content": []}, "session_id": "s"},
            json.loads(claude_answer(CLI_CHECK_WORD)) | {"type": "result"},
        ]
        runner = FakeRunner(script=[ok_proc(json.dumps(events))])
        dispatcher = ClaudeCliDispatcher(TARGET, AGENTS_DIR, runner=runner)
        usage = await dispatcher.check_cli("claude -p")
        assert usage.cost_usd == pytest.approx(0.01)

    @pytest.mark.parametrize(
        ("outcome", "reason"),
        [
            (SubprocessResult(1, "", "command not found: model"), "exited 1"),
            (ok_proc("not json"), "not what claude -p prints"),
            (
                ok_proc(claude_answer("Invalid API key", is_error=True)),
                "Invalid API key",
            ),
            (ok_proc(claude_answer("Sure! Here you go.")), "it answered"),
            (DispatchTimeoutError("cap"), "no answer in 3 minutes"),
            (FileNotFoundError("claude"), "could not be started"),
        ],
    )
    async def test_a_failure_refuses_the_start(self, outcome, reason):
        runner = FakeRunner(script=[outcome])
        dispatcher = ClaudeCliDispatcher(TARGET, AGENTS_DIR, runner=runner)
        with pytest.raises(PreflightError) as info:
            await dispatcher.check_cli("claude -p")
        message = str(info.value)
        assert reason in message
        assert "--agent-cli 'claude -p'" in message
        assert "fix:" in message

    async def test_a_missing_executable_refuses_before_any_spawn(self, monkeypatch):
        monkeypatch.setattr("kiln_harness_tuner.cli.shutil.which", lambda _name: None)
        with pytest.raises(PreflightError, match="`codex` is not on PATH"):
            await check_agent_cli(parse_agent_cli("codex exec"), TARGET, AGENTS_DIR)
