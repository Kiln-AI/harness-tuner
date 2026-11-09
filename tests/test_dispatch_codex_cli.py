"""CodexCliDispatcher against canned `codex exec --json` event streams (the
shapes a live `codex-cli 0.155` printed): the command and its
fence, the output read, the shared repair and error paths, the transcript
copy. No live calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_dispatch_claude_cli import (
    BASELINE,
    BATCH_PLAN_INPUT,
    PLAN_DATA,
    TARGET,
    FakeRunner,
    envelope_text,
    ok_proc,
)

from kiln_harness_tuner.cli import render_run_configs
from kiln_harness_tuner.dispatch.base import AgentDispatcher
from kiln_harness_tuner.dispatch.claude_cli import (
    DispatchTimeoutError,
    SubprocessResult,
)
from kiln_harness_tuner.dispatch.codex_cli import (
    DISABLED_FEATURES,
    CodexCliDispatcher,
    codex_install_paths,
    permission_profile,
    read_codex_output,
    toml_value,
)
from kiln_harness_tuner.gateway.ops import ROLE_OPS
from kiln_harness_tuner.paths import StatePaths
from kiln_harness_tuner.provider_errors import ProviderQuotaError
from kiln_harness_tuner.schemas import ExperimentItem, RcBuildInput, TargetBlock

THREAD = "01a0ec67-0a77-7cd0-b931-bfa7114ba47a"


def codex_events(
    final: str | None,
    *,
    thread: str = THREAD,
    input_tokens: int = 60_000,
    output_tokens: int = 250,
    failed: str | None = None,
) -> str:
    events: list[dict] = [{"type": "thread.started", "thread_id": thread}]
    events.append({"type": "turn.started"})
    events.append(
        {
            "type": "item.completed",
            "item": {"id": "item_0", "type": "agent_message", "text": "Working."},
        }
    )
    events.append(
        {
            "type": "item.completed",
            "item": {
                "id": "item_1",
                "type": "command_execution",
                "command": "/bin/zsh -lc 'cat /elsewhere'",
                "aggregated_output": "cat: /elsewhere: Operation not permitted\n",
                "exit_code": 1,
                "status": "completed",
            },
        }
    )
    if final is not None:
        events.append(
            {
                "type": "item.completed",
                "item": {"id": "item_2", "type": "agent_message", "text": final},
            }
        )
    if failed is not None:
        events.append({"type": "error", "message": failed})
        events.append({"type": "turn.failed", "error": {"message": failed}})
    else:
        events.append(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": input_tokens // 2,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": 0,
                },
            }
        )
    return "\n".join(json.dumps(e) for e in events) + "\n"


def agents_dir(tmp_path: Path) -> Path:
    """The run's agents folder for Codex, as `install_for_run` lays it out."""
    agents = tmp_path / "state" / "agents" / "codex" / "0.1.0"
    (agents / ".agents" / "skills").mkdir(parents=True, exist_ok=True)
    return agents


def make(tmp_path: Path, script, **kwargs) -> tuple[CodexCliDispatcher, FakeRunner]:
    runner = FakeRunner(script=list(script))
    dispatcher = CodexCliDispatcher(
        TARGET,
        agents_dir(tmp_path),
        runner=runner,
        run_dir=tmp_path / "run",
        codex_sessions_dir=tmp_path / "sessions",
        **kwargs,
    )
    return dispatcher, runner


def config_of(argv: list[str]) -> dict[str, str]:
    """`-c key=value` pairs of an argv, by key."""
    out = {}
    for flag, value in zip(argv, argv[1:], strict=False):
        if flag == "-c":
            key, _, rest = value.partition("=")
            out[key] = rest
    return out


class TestTheProtocol:
    def test_it_is_an_agent_dispatcher(self, tmp_path):
        dispatcher, _ = make(tmp_path, [])
        assert isinstance(dispatcher, AgentDispatcher)


class TestReadingTheOutput:
    def test_session_final_message_and_tokens(self):
        cli = read_codex_output(codex_events("the answer"))
        assert cli is not None
        assert cli.session_id == THREAD
        # The LAST agent message, not the first progress note.
        assert cli.result_text == "the answer"
        assert cli.error is None
        # Cached input is inside `input_tokens`; no cost is reported, so
        # every token is unmeasured.
        assert cli.usage.tokens == 60_250
        assert cli.usage.unmeasured_tokens == 60_250
        assert cli.usage.cost_usd == 0.0

    def test_a_failed_turn_is_the_sessions_error(self):
        cli = read_codex_output(codex_events(None, failed="usage limit reached"))
        assert cli is not None and cli.error == "usage limit reached"

    def test_an_item_level_error_is_only_a_warning(self):
        stream = codex_events("ok") + json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "w", "type": "error", "message": "fallback metadata"},
            }
        )
        cli = read_codex_output(stream)
        assert cli is not None and cli.error is None

    @pytest.mark.parametrize("stdout", ["", "not json\n", "[1, 2]\n"])
    def test_no_event_is_no_output(self, stdout):
        assert read_codex_output(stdout) is None


class TestTheCommand:
    def test_the_fence_and_the_switches(self, tmp_path):
        dispatcher, _ = make(tmp_path, [])
        argv = dispatcher._argv("batch_plan", label="batch_plan-loop")
        assert argv[:2] == ["codex", "exec"]
        assert argv[-1] == "-"  # the prompt comes on stdin
        for flag in (
            "--json",
            "--ignore-user-config",
            "--strict-config",
            "--skip-git-repo-check",
        ):
            assert flag in argv
        workspace = tmp_path / "run" / "scratch" / "batch_plan-loop"
        assert argv[argv.index("-C") + 1] == str(workspace)
        config = config_of(argv)
        assert config["default_permissions"] == '"kiln_harness_tuner"'
        assert config["approval_policy"] == '"never"'
        assert config["web_search"] == '"disabled"'
        assert config["model_reasoning_effort"] == '"high"'
        profile = config["permissions.kiln_harness_tuner"]
        assert '":root" = "deny"' in profile
        assert '":minimal" = "read"' in profile
        assert '":workspace_roots" = {"." = "write"}' in profile
        skills = agents_dir(tmp_path) / ".agents" / "skills"
        assert f'"{skills.resolve()}" = "read"' in profile
        assert '"network" = {"enabled" = false}' in profile
        disabled = [v for f, v in zip(argv, argv[1:], strict=False) if f == "--disable"]
        assert disabled == list(DISABLED_FEATURES)
        assert {"apps", "plugins", "browser_use", "computer_use"} <= set(disabled)
        # No model named: Codex's own default.
        assert "-m" not in argv

    def test_model_and_effort_overrides(self, tmp_path):
        dispatcher, _ = make(
            tmp_path,
            [],
            models={"describe_eval": "gpt-5.5-mini"},
            efforts={"describe_eval": "low"},
        )
        argv = dispatcher._argv("describe_eval", label="d")
        assert argv[argv.index("-m") + 1] == "gpt-5.5-mini"
        assert config_of(argv)["model_reasoning_effort"] == '"low"'

    @pytest.mark.parametrize(
        "role", ["promote_best", "report_writer", "integrity_review"]
    )
    def test_a_val_reader_workspace_is_read_only(self, tmp_path, role):
        dispatcher, _ = make(tmp_path, [])
        profile = config_of(dispatcher._argv(role, label=role))[
            "permissions.kiln_harness_tuner"
        ]
        assert '":workspace_roots" = {"." = "read"}' in profile
        assert '"write"' not in profile

    def test_the_trace_readers_may_read_the_trace_folder(self, tmp_path):
        """The gateway's trace folder is readable to the roles
        that read traces, and to no other."""
        dispatcher, _ = make(tmp_path, [])
        traces = (tmp_path / "run" / "scratch" / "traces").resolve()
        line = f'"{traces}" = "read"'
        for role, reads in (("batch_plan", True), ("probe", True)):
            profile = config_of(dispatcher._argv(role, label=role))[
                "permissions.kiln_harness_tuner"
            ]
            assert (line in profile) == reads, role
        for role in ("promote_best", "describe_eval"):
            profile = config_of(dispatcher._argv(role, label=role))[
                "permissions.kiln_harness_tuner"
            ]
            assert str(traces) not in profile, role

    def test_the_workspace_links_the_agents_folders_skills(self, tmp_path):
        dispatcher, _ = make(tmp_path, [])
        dispatcher._argv("batch_plan", label="w1")
        link = tmp_path / "run" / "scratch" / "w1" / ".agents" / "skills"
        assert link.is_symlink()
        assert link.resolve() == (agents_dir(tmp_path) / ".agents" / "skills")
        # Idempotent: a second session of the same label reuses it.
        dispatcher._argv("batch_plan", label="w1")

    def test_the_workspace_holds_the_agents_folders_instructions(self, tmp_path):
        (agents_dir(tmp_path) / "AGENTS.md").write_text("the rules\n")
        dispatcher, _ = make(tmp_path, [])
        dispatcher._argv("batch_plan", label="w1")
        copied = tmp_path / "run" / "scratch" / "w1" / "AGENTS.md"
        assert copied.read_text() == "the rules\n"

    def test_a_link_planted_as_the_instructions_is_not_written_through(self, tmp_path):
        (agents_dir(tmp_path) / "AGENTS.md").write_text("the rules\n")
        outside = tmp_path / "outside.txt"
        outside.write_text("keep\n")
        dispatcher, _ = make(tmp_path, [])
        dispatcher._argv("batch_plan", label="w1")
        copied = tmp_path / "run" / "scratch" / "w1" / "AGENTS.md"
        copied.unlink()
        copied.symlink_to(outside)
        dispatcher._argv("batch_plan", label="w1")  # the repair round's restage
        assert outside.read_text() == "keep\n"
        assert not copied.is_symlink() and copied.read_text() == "the rules\n"

    def test_resume_names_the_thread_and_keeps_the_fence(self, tmp_path):
        dispatcher, _ = make(tmp_path, [])
        argv = dispatcher._argv("batch_plan", THREAD, label="b")
        assert argv[:3] == ["codex", "exec", "resume"]
        assert argv[-2:] == [THREAD, "-"]
        assert "-C" not in argv  # `resume` takes none; the cwd is the workspace
        assert '":root" = "deny"' in config_of(argv)["permissions.kiln_harness_tuner"]

    def test_the_session_runs_in_its_workspace(self, tmp_path):
        dispatcher, _ = make(tmp_path, [])
        assert dispatcher._cwd("x") == tmp_path / "run" / "scratch" / "x"


class TestTheMcpServers:
    def _wired(self, tmp_path):
        target = TargetBlock(
            project_path=str(tmp_path / "proj"),
            project_id="424242000001",
            task_id="424242000002",
            kiln_host="http://localhost:8757",
        )
        (tmp_path / "proj").mkdir()
        state = StatePaths(tmp_path / "state")
        mcp_path, _ = render_run_configs(
            state.runs / "r1", target, state=state, agents_dir=agents_dir(tmp_path)
        )
        return make(tmp_path, [], mcp_config_path=mcp_path)[0]

    def test_each_role_gets_its_rendered_servers_and_only_its_tools(self, tmp_path):
        dispatcher = self._wired(tmp_path)
        for role in ROLE_OPS:
            servers = dispatcher._mcp_servers(role)
            assert set(servers) >= {"kiln_harness_tuner_gateway"}, role
            gateway = servers["kiln_harness_tuner_gateway"]
            assert gateway["enabled_tools"] == list(ROLE_OPS[role])
            assert gateway["default_tools_approval_mode"] == "approve"
            assert "--role" in gateway["args"]
            assert gateway["args"][gateway["args"].index("--role") + 1] == role

    def test_the_installer_goes_to_the_staging_roles_only(self, tmp_path):
        dispatcher = self._wired(tmp_path)
        assert dispatcher._mcp_servers("create_experiment_rc")["skill_installer"][
            "enabled_tools"
        ] == ["install_skill_bundle", "export_skill_bundle"]
        assert dispatcher._mcp_servers("probe")["skill_installer"]["enabled_tools"] == [
            "install_skill_bundle"
        ]
        assert "skill_installer" not in dispatcher._mcp_servers("batch_plan")

    def test_the_no_tool_role_gets_no_server(self, tmp_path):
        dispatcher = self._wired(tmp_path)
        assert dispatcher._mcp_servers("integrity_review") == {}
        argv = dispatcher._argv("integrity_review", label="i")
        assert not any(k.startswith("mcp_servers.") for k in config_of(argv))

    def test_the_servers_ride_the_command(self, tmp_path):
        dispatcher = self._wired(tmp_path)
        config = config_of(dispatcher._argv("batch_plan", label="b"))
        server = config["mcp_servers.kiln_harness_tuner_gateway"]
        # The tuner's own interpreter starts the gateway: no `uv`, no checkout.
        assert server.startswith("{")
        assert f'"command" = {json.dumps(sys.executable)}' in server
        assert '"args" = ["-P", "-m", "kiln_harness_tuner.gateway.server"' in server

    def test_the_servers_get_the_runs_uv_and_python_settings(
        self, tmp_path, monkeypatch
    ):
        # Codex gives an MCP server a small default environment; the gateway
        # needs the run's Python settings to behave the same way.
        monkeypatch.setenv("UV_FROZEN", "1")
        monkeypatch.setenv("PYTHONPATH", "/somewhere/src")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-not-forwarded")
        dispatcher = self._wired(tmp_path)
        env = dispatcher._mcp_servers("batch_plan")["kiln_harness_tuner_gateway"]["env"]
        assert env["UV_FROZEN"] == "1" and env["PYTHONPATH"] == "/somewhere/src"
        assert "OPENAI_API_KEY" not in env


class TestTomlValues:
    def test_forms(self):
        assert toml_value(True) == "true"
        assert toml_value(3) == "3"
        assert toml_value('a "quoted" path\\x') == '"a \\"quoted\\" path\\\\x"'
        assert toml_value(["a", 1]) == '["a", 1]'
        assert toml_value({"k": {"n": False}}) == '{"k" = {"n" = false}}'

    def test_the_profile_is_an_allow_list(self, tmp_path):
        profile = permission_profile(tmp_path, writable=True)
        assert profile["filesystem"][":root"] == "deny"
        assert profile["network"] == {"enabled": False}


class TestDispatching:
    async def test_ok(self, tmp_path):
        dispatcher, runner = make(
            tmp_path, [ok_proc(codex_events(envelope_text(PLAN_DATA)))]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "ok"
        assert result.data is not None and result.data.items[0].id == "w1-1"
        assert result.usage.unmeasured_tokens == 60_250
        assert result.usage.cost_usd == 0.0
        (call,) = runner.calls
        assert call.cwd == str(tmp_path / "run" / "scratch" / "batch_plan-loop")
        # The envelope contract rides the prompt, as for Claude.
        assert "Output requirements" in call.stdin_text

    async def test_a_bad_envelope_is_repaired_in_the_same_thread(self, tmp_path):
        dispatcher, runner = make(
            tmp_path,
            [
                ok_proc(codex_events("no json here")),
                ok_proc(codex_events(envelope_text(PLAN_DATA))),
            ],
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "ok"
        repair = runner.calls[1].argv
        assert repair[:3] == ["codex", "exec", "resume"]
        assert THREAD in repair
        assert result.usage.unmeasured_tokens == 2 * 60_250

    async def test_a_quota_failure_stops_the_run(self, tmp_path):
        failed = SubprocessResult(
            returncode=1,
            stdout=codex_events(None, failed="You've hit your usage limit."),
            stderr="",
        )
        dispatcher, _ = make(tmp_path, [failed])
        with pytest.raises(ProviderQuotaError):
            await dispatcher.batch_plan(BATCH_PLAN_INPUT)

    async def test_a_provider_error_rides_the_ladder(self, tmp_path):
        message = '{"status":400,"error":{"message":"model not supported"}}'
        failed = SubprocessResult(
            returncode=1, stdout=codex_events(None, failed=message), stderr=""
        )
        dispatcher, _ = make(tmp_path, [failed])
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "failed_infra"
        assert result.error is not None
        assert result.error.error_class == "provider_error"

    async def test_an_exit_with_no_event_is_a_crash(self, tmp_path):
        crashed = SubprocessResult(returncode=2, stdout="", stderr="boom")
        dispatcher, _ = make(tmp_path, [crashed])
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.error is not None
        assert result.error.error_class == "agent_crash"
        assert result.error.detail.startswith("codex exited 2")

    async def test_a_timeout_is_not_resumed(self, tmp_path):
        # The builder is a resume-on-timeout role under Claude; Codex cannot
        # name the killed session, so the ladder re-runs it instead.
        dispatcher, runner = make(
            tmp_path, [DispatchTimeoutError("codex exec exceeded 1800s")]
        )
        item = ExperimentItem.model_validate(PLAN_DATA["items"][0])
        result = await dispatcher.create_experiment_rc(
            RcBuildInput(item=item, baseline=BASELINE)
        )
        assert result.error is not None and result.error.error_class == "timeout"
        assert len(runner.calls) == 1


class TestTranscripts:
    async def test_the_rollout_is_copied_redacted(self, tmp_path):
        day = tmp_path / "sessions" / "2026" / "09" / "29"
        day.mkdir(parents=True)
        (day / f"rollout-2026-09-29T17-02-50-{THREAD}.jsonl").write_text(
            '{"line": "api_key=sk-abcdefghijklmnopqrstuvwxyz0123"}\n'
        )
        dispatcher, _ = make(
            tmp_path,
            [ok_proc(codex_events(envelope_text(PLAN_DATA)))],
            traces_root=tmp_path / "traces",
            run_id="260929-1700",
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        (copied,) = (tmp_path / "traces").rglob(f"*{THREAD}.jsonl")
        assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in copied.read_text()


class TestTheInstallPaths:
    """codex-cli 0.162 re-runs its own binary under the profile, by the path
    it was started from."""

    def _standalone(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        release = home / ".codex/packages/standalone/releases/0.162.1"
        (release / "bin").mkdir(parents=True)
        (release / "bin" / "codex").write_text("")
        current = home / ".codex/packages/standalone/current"
        current.symlink_to(release, target_is_directory=True)
        (home / ".local/bin").mkdir(parents=True)
        link = home / ".local/bin/codex"
        link.symlink_to(current / "bin" / "codex")
        monkeypatch.setattr(
            "kiln_harness_tuner.dispatch.codex_cli.Path.home", lambda: home
        )
        monkeypatch.setattr(
            "kiln_harness_tuner.dispatch.codex_cli.shutil.which", lambda _b: str(link)
        )
        return home, release

    def test_every_folder_on_the_link_chain_and_the_release(
        self, tmp_path, monkeypatch
    ):
        home, release = self._standalone(tmp_path, monkeypatch)
        paths = codex_install_paths("codex")
        assert paths == [
            home / ".local/bin",
            home / ".codex/packages/standalone",
            release.resolve(),
        ]
        # Never the home directory, nor `~/.codex` with its auth.json.
        assert home not in paths
        assert home / ".codex" not in paths

    def test_not_on_path(self, monkeypatch):
        monkeypatch.setattr(
            "kiln_harness_tuner.dispatch.codex_cli.shutil.which", lambda _b: None
        )
        assert codex_install_paths("codex") == []

    def test_a_binary_straight_in_home_opens_nothing_above_it(
        self, tmp_path, monkeypatch
    ):
        home = tmp_path / "home"
        home.mkdir()
        (home / "codex").write_text("")
        monkeypatch.setattr(
            "kiln_harness_tuner.dispatch.codex_cli.Path.home", lambda: home
        )
        monkeypatch.setattr(
            "kiln_harness_tuner.dispatch.codex_cli.shutil.which",
            lambda _b: str(home / "codex"),
        )
        assert codex_install_paths("codex") == []

    def test_the_profile_reads_them(self, tmp_path):
        profile = permission_profile(
            tmp_path, writable=True, install_paths=[Path("/opt/codex")]
        )
        assert profile["filesystem"]["/opt/codex"] == "read"


class TestTheModel:
    def test_no_model_option_by_default(self, tmp_path):
        # Codex's own default model — never a Claude model name.
        dispatcher, _ = make(tmp_path, [])
        for role in ("batch_plan", "generality_review"):
            argv = dispatcher._argv(role, label=role)
            assert "-m" not in argv

    def test_the_run_wide_model_and_the_operators_options(self, tmp_path):
        dispatcher, _ = make(
            tmp_path,
            [],
            default_model="gpt-6.1-sol",
            cli_options=("--oss",),
            models={"describe_eval": "gpt-6.1-mini"},
        )
        argv = dispatcher._argv("batch_plan", label="b")
        assert argv[:3] == ["codex", "exec", "--oss"]
        assert argv[argv.index("-m") + 1] == "gpt-6.1-sol"
        argv = dispatcher._argv("describe_eval", label="d")
        assert argv[argv.index("-m") + 1] == "gpt-6.1-mini"
