"""OpenCodeCliDispatcher against canned `opencode run --format json` event
streams (the shapes a live `opencode 1.19.3` printed): the
command, the per-session config and its permission rules, the output read,
the shared repair and error paths, the transcript export. No live calls."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_dispatch_claude_cli import (
    BATCH_PLAN_INPUT,
    PLAN_DATA,
    TARGET,
    FakeRunner,
    envelope_text,
    ok_proc,
)

from kiln_harness_tuner.cli import GATEWAY_MODULE, render_run_configs
from kiln_harness_tuner.dispatch.base import AgentDispatcher
from kiln_harness_tuner.dispatch.claude_cli import SubprocessResult
from kiln_harness_tuner.dispatch.opencode_cli import (
    MCP_TIMEOUT_MS,
    OpenCodeCliDispatcher,
    path_patterns,
    read_opencode_output,
)
from kiln_harness_tuner.gateway.ops import GATEWAY_SERVER
from kiln_harness_tuner.paths import StatePaths
from kiln_harness_tuner.provider_errors import ProviderQuotaError
from kiln_harness_tuner.schemas import TargetBlock

SESSION = "ses_ea21e5e4affe408oVOyU7M1r44"


def step(tokens: int = 11_000, cost: float = 0.0, reason: str = "stop") -> list[dict]:
    return [
        {"type": "step_finish", "sessionID": SESSION,
         "part": {"type": "step-finish", "reason": reason,
                  "tokens": {"total": tokens, "input": tokens - 10, "output": 10},
                  "cost": cost}},
    ]  # fmt: skip


def opencode_events(
    final: str | None, *, cost: float = 0.0, error: str | None = None
) -> str:
    events: list[dict] = [
        {"type": "step_start", "sessionID": SESSION, "part": {"type": "step-start"}},
        {"type": "text", "sessionID": SESSION,
         "part": {"type": "text", "text": "Reading the skill."}},
        {"type": "tool_use", "sessionID": SESSION,
         "part": {"type": "tool", "tool": "read", "state": {"status": "completed"}}},
        *step(5_000, cost, "tool-calls"),
    ]  # fmt: skip
    if error is not None:
        events.append(
            {
                "type": "error",
                "sessionID": SESSION,
                "error": {"name": "APIError", "data": {"message": error}},
            }
        )
    else:
        events.append(
            {"type": "step_start", "sessionID": SESSION, "part": {"type": "step-start"}}
        )
        if final is not None:
            events.append(
                {"type": "text", "sessionID": SESSION,
                 "part": {"type": "text", "text": final}}
            )  # fmt: skip
        events += step(6_000, cost)
    return "\n".join(json.dumps(e) for e in events) + "\n"


def agents_dir(tmp_path: Path) -> Path:
    """The run's agents folder for OpenCode, as `install_for_run` lays it
    out."""
    agents = tmp_path / "state" / "agents" / "opencode" / "0.1.0"
    (agents / ".agents" / "skills").mkdir(parents=True, exist_ok=True)
    return agents


def run_dir(tmp_path: Path) -> Path:
    return tmp_path / "state" / "runs" / "r1"


def make(tmp_path: Path, script, **kwargs):
    runner = FakeRunner(script=list(script))
    kwargs.setdefault("run_dir", run_dir(tmp_path))
    dispatcher = OpenCodeCliDispatcher(
        TARGET, agents_dir(tmp_path), runner=runner, **kwargs
    )
    return dispatcher, runner


def under(path: Path) -> str:
    """The relative rule for everything under `path` with no git root above
    it (the state folder's case): relative to `/`."""
    return f"{path.resolve().as_posix().lstrip('/')}/**"


def config_of(argv: list[str]) -> dict:
    (assignment,) = [a for a in argv if a.startswith("OPENCODE_CONFIG=")]
    return json.loads(Path(assignment.partition("=")[2]).read_text())


def test_it_is_an_agent_dispatcher(tmp_path):
    dispatcher, _ = make(tmp_path, [])
    assert isinstance(dispatcher, AgentDispatcher)


class TestReadingTheOutput:
    def test_session_final_message_and_tokens(self):
        cli = read_opencode_output(opencode_events("the answer"))
        assert cli is not None
        assert cli.session_id == SESSION
        # The last step's text, not the first step's.
        assert cli.result_text == "the answer"
        assert cli.error is None
        assert cli.usage.tokens == 11_000
        assert cli.usage.cost_usd == 0

    def test_a_priced_session_books_its_cost(self):
        cli = read_opencode_output(opencode_events("x", cost=0.02))
        assert cli is not None
        assert cli.usage.cost_usd == pytest.approx(0.04)

    def test_an_error_event_is_the_sessions_error(self):
        cli = read_opencode_output(opencode_events(None, error="Insufficient balance"))
        assert cli is not None
        assert cli.error == "Insufficient balance"

    @pytest.mark.parametrize("stdout", ["", "not json\n"])
    def test_no_event_is_no_output(self, stdout):
        assert read_opencode_output(stdout) is None


class TestTheCommand:
    def test_the_switches(self, tmp_path):
        dispatcher, _ = make(
            tmp_path,
            [],
            default_model="zai-coding-plan/glm-5.3-flash",
            cli_options=("--thinking",),
        )
        argv = dispatcher._argv("batch_plan", label="b")
        assert argv[0] == "env"
        assert argv[2:5] == ["opencode", "run", "--thinking"]
        for flag in ("--pure", "--auto"):
            assert flag in argv
        assert argv[argv.index("--format") + 1] == "json"
        assert argv[argv.index("--variant") + 1] == "high"
        assert argv[argv.index("-m") + 1] == "zai-coding-plan/glm-5.3-flash"

    def test_the_workspace_is_named_with_dir(self, tmp_path):
        # Without it OpenCode takes its project folder from PWD, and finds no
        # skills when that is the folder the tuner was started in.
        dispatcher, _ = make(tmp_path, [])
        argv = dispatcher._argv("batch_plan", label="b")
        assert argv[argv.index("--dir") + 1] == str(dispatcher._cwd("b"))

    def test_no_model_option_by_default(self, tmp_path):
        dispatcher, _ = make(tmp_path, [])
        assert "-m" not in dispatcher._argv("generality_review", label="g")

    def test_resume_names_the_session(self, tmp_path):
        dispatcher, _ = make(tmp_path, [])
        argv = dispatcher._argv("batch_plan", SESSION, label="b")
        assert argv[argv.index("--session") + 1] == SESSION

    def test_the_session_runs_in_its_workspace(self, tmp_path):
        dispatcher, _ = make(tmp_path, [])
        assert dispatcher._cwd("x") == run_dir(tmp_path) / "scratch" / "x"

    def test_the_workspace_finds_the_agents_folders_skills_and_instructions(
        self, tmp_path
    ):
        """An installed tuner has no git root above the session for OpenCode
        to find the skills and `AGENTS.md` through: the workspace holds a link
        to the skills and a copy of the instructions, as Codex's does."""
        (agents_dir(tmp_path) / "AGENTS.md").write_text("the loop\n")
        dispatcher, _ = make(tmp_path, [])
        workspace = dispatcher._cwd("w1")
        link = workspace / ".agents" / "skills"
        assert link.is_symlink()
        assert link.resolve() == agents_dir(tmp_path) / ".agents" / "skills"
        assert (workspace / "AGENTS.md").read_text() == "the loop\n"
        dispatcher._cwd("w1")  # a second session of the same label reuses it

    def test_max_turns_is_the_agents_step_cap(self, tmp_path):
        dispatcher, _ = make(tmp_path, [], max_turns=40)
        config = config_of(dispatcher._argv("batch_plan", label="b"))
        assert config["agent"] == {"build": {"steps": 40}}


class TestThePermissions:
    def test_an_allow_list(self, tmp_path):
        dispatcher, _ = make(tmp_path, [])
        permission = config_of(dispatcher._argv("batch_plan", label="b"))["permission"]
        assert permission["*"] == "deny"
        assert permission["skill"] == "allow"
        read = permission["read"]
        assert read["*"] == "deny"
        # No git root above the state folder: relative to `/`, the form
        # OpenCode matches then, and absolute.
        workspace = run_dir(tmp_path) / "scratch" / "b"
        skills = agents_dir(tmp_path) / ".agents" / "skills"
        for path in (workspace, skills):
            assert read[under(path)] == "allow"
            assert read[f"{path.resolve().as_posix()}/**"] == "allow"
        assert permission["edit"][under(workspace)] == "allow"
        assert under(skills) not in permission["edit"]
        for tool in ("bash", "webfetch", "websearch", "grep", "glob", "task"):
            assert tool not in permission  # left to "*": deny

    @pytest.mark.parametrize("git_root", [False, True])
    @pytest.mark.parametrize(
        "role", ["create_experiment_rc", "probe", "batch_plan", "eval_build"]
    )
    def test_no_writer_may_edit_through_the_skills_link(self, tmp_path, role, git_root):
        """The workspace's `.agents/skills` is a link to the agents folder,
        which every later session and every run on the version reads (FS
        8.2). OpenCode's fence is a rule list, not an OS sandbox, so the
        workspace allow must not reach through the link: `.agents/` is
        denied after it, in both forms, longest and last."""
        if git_root:
            (tmp_path / ".git").mkdir()
        dispatcher, _ = make(tmp_path, [])
        edit = config_of(dispatcher._argv(role, label="w"))["permission"]["edit"]
        allows = [k for k, v in edit.items() if v == "allow"]
        denies = [k for k, v in edit.items() if v == "deny" and k != "*"]
        assert len(allows) == 2 and len(denies) == 2
        for allow, deny in zip(allows, denies, strict=True):
            assert deny == allow.removesuffix("/**") + "/.agents/**"
        assert list(edit)[-2:] == denies
        workspace = (run_dir(tmp_path) / "scratch" / "w").as_posix()
        assert f"{workspace}/.agents/**" in denies
        # Built from the workspace's own path: the link is not followed.
        assert not any("state/agents" in rule for rule in edit)
        # Reading through the link stays allowed: the skills load.
        read = config_of(dispatcher._argv(role, label="w"))["permission"]["read"]
        assert read[f"{workspace}/**"] == "allow"

    def test_the_trace_readers_may_read_the_trace_folder(self, tmp_path):
        """`read` opens the gateway's trace folder to the roles
        that read traces, and to no other; `edit` never opens it."""
        dispatcher, _ = make(tmp_path, [])
        rule = under(run_dir(tmp_path) / "scratch" / "traces")
        for role in ("batch_plan", "analyze_experiment_results", "probe"):
            permission = config_of(dispatcher._argv(role, label=role))["permission"]
            assert permission["read"][rule] == "allow", role
            assert permission["external_directory"][rule] == "allow", role
            assert rule not in permission["edit"], role
        for role in ("promote_best", "describe_eval"):
            permission = config_of(dispatcher._argv(role, label=role))["permission"]
            assert rule not in permission["read"], role

    @pytest.mark.parametrize("role", ["promote_best", "report_writer"])
    def test_a_val_reader_may_not_write(self, tmp_path, role):
        dispatcher, _ = make(tmp_path, [])
        permission = config_of(dispatcher._argv(role, label=role))["permission"]
        assert permission["edit"] == "deny"

    @pytest.mark.parametrize("role", ["integrity_review", "generality_review"])
    def test_a_no_tool_role_gets_nothing(self, tmp_path, role):
        dispatcher, _ = make(tmp_path, [])
        config = config_of(dispatcher._argv(role, label=role))
        assert config["permission"] == {"*": "deny"}
        assert config["mcp"] == {}

    def test_the_config_is_outside_the_workspace(self, tmp_path):
        dispatcher, _ = make(tmp_path, [])
        argv = dispatcher._argv("batch_plan", label="b")
        (assignment,) = [a for a in argv if a.startswith("OPENCODE_CONFIG=")]
        path = Path(assignment.partition("=")[2])
        assert not path.is_relative_to(dispatcher._cwd("b"))

    def test_with_no_worktree_the_pattern_is_relative_to_root(self, tmp_path):
        assert path_patterns(Path("/a/b"), None) == ["a/b/**", "/a/b/**"]

    def test_inside_a_git_worktree_the_rules_are_relative_to_it(self, tmp_path):
        # A state folder set inside a repository: OpenCode matches against
        # the path relative to that git root.
        (tmp_path / ".git").mkdir()
        dispatcher, _ = make(tmp_path, [])
        read = config_of(dispatcher._argv("batch_plan", label="b"))["permission"][
            "read"
        ]
        assert read["state/runs/r1/scratch/b/**"] == "allow"
        assert read["state/agents/opencode/0.1.0/.agents/skills/**"] == "allow"


class TestTheMcpServers:
    def _wired(self, tmp_path):
        target = TargetBlock(
            kiln_host="http://localhost:8757",
            project_id="P",
            task_id="T",
            project_path=str(tmp_path / "project"),
        )
        state = StatePaths(tmp_path / "state")
        mcp, _ = render_run_configs(
            run_dir(tmp_path), target, state=state, agents_dir=agents_dir(tmp_path)
        )
        return make(tmp_path, [], mcp_config_path=mcp)[0]

    def test_each_role_gets_its_servers_and_tools(self, tmp_path):
        dispatcher = self._wired(tmp_path)
        config = config_of(dispatcher._argv("create_experiment_rc", label="rc"))
        assert set(config["mcp"]) == {GATEWAY_SERVER, "skill_installer"}
        gateway = config["mcp"][GATEWAY_SERVER]
        assert gateway["type"] == "local"
        # The tuner's own interpreter, with the session's workspace (its
        # cwd, which the agent writes) kept off `sys.path`.
        assert gateway["command"][:4] == [sys.executable, "-P", "-m", GATEWAY_MODULE]
        assert "create_experiment_rc" in gateway["command"]
        assert gateway["timeout"] == MCP_TIMEOUT_MS
        permission = config["permission"]
        assert permission[f"{GATEWAY_SERVER}_*"] == "allow"
        assert permission["skill_installer_install_skill_bundle"] == "allow"
        assert permission["skill_installer_export_skill_bundle"] == "allow"

    def test_the_probe_gets_only_the_install(self, tmp_path):
        dispatcher = self._wired(tmp_path)
        permission = config_of(dispatcher._argv("probe", label="p"))["permission"]
        assert permission["skill_installer_install_skill_bundle"] == "allow"
        assert "skill_installer_export_skill_bundle" not in permission

    def test_the_planner_gets_no_installer(self, tmp_path):
        dispatcher = self._wired(tmp_path)
        config = config_of(dispatcher._argv("batch_plan", label="b"))
        assert set(config["mcp"]) == {GATEWAY_SERVER}


class TestDispatching:
    async def test_ok(self, tmp_path):
        dispatcher, runner = make(
            tmp_path, [ok_proc(opencode_events(envelope_text(PLAN_DATA)))]
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "ok", result
        assert result.usage.tokens == 11_000
        (call,) = runner.calls
        assert Path(call.cwd).parent == run_dir(tmp_path) / "scratch"

    async def test_a_bad_envelope_is_repaired_in_the_same_session(self, tmp_path):
        dispatcher, runner = make(
            tmp_path,
            [
                ok_proc(opencode_events("no fence here")),
                ok_proc(opencode_events(envelope_text(PLAN_DATA))),
            ],
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "ok"
        repair = runner.calls[1].argv
        assert repair[repair.index("--session") + 1] == SESSION

    async def test_a_quota_failure_stops_the_run(self, tmp_path):
        stdout = opencode_events(None, error="You have exceeded your usage limit")
        dispatcher, _ = make(tmp_path, [SubprocessResult(1, stdout, "")])
        with pytest.raises(ProviderQuotaError):
            await dispatcher.batch_plan(BATCH_PLAN_INPUT)

    async def test_an_exit_with_no_event_is_a_crash(self, tmp_path):
        dispatcher, _ = make(
            tmp_path,
            [SubprocessResult(1, "", "boom")] * 4,
        )
        result = await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert result.status == "failed_infra"
        assert result.error.error_class == "agent_crash"


class TestTranscripts:
    async def test_the_export_is_copied_redacted(self, tmp_path, monkeypatch):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            body = {
                "info": {"id": SESSION},
                "token": "sk-abcdefghijklmnopqrstuvwxyz0123",
            }
            return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")

        monkeypatch.setattr(
            "kiln_harness_tuner.dispatch.opencode_cli.subprocess.run", fake_run
        )
        dispatcher, _ = make(
            tmp_path,
            [ok_proc(opencode_events(envelope_text(PLAN_DATA)))],
            run_id="r1",
            traces_root=tmp_path / "traces",
        )
        await dispatcher.batch_plan(BATCH_PLAN_INPUT)
        assert calls == [["opencode", "export", SESSION]]
        (copied,) = (tmp_path / "traces").rglob(f"*{SESSION}.jsonl")
        assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in copied.read_text()
