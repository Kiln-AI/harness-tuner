"""CLI tests: the flags parse, wire a TargetBlock, and main runs the
orchestrator (monkeypatched — no live Kiln)."""

import argparse
import json
from pathlib import Path

import pytest

import kiln_harness_tuner.cli
from kiln_harness_tuner.cli import (
    PROG,
    build_parser,
    main,
    render_run_configs,
    target_from_args,
)
from kiln_harness_tuner.detectability import UnderpoweredEvalsError
from kiln_harness_tuner.dispatch.claude_cli import PreflightError
from kiln_harness_tuner.memory import BaselineRecordMissingError
from kiln_harness_tuner.model_preflight import ModelPreflightError
from kiln_harness_tuner.onboarding import OnboardingError, OnboardingStopped
from kiln_harness_tuner.report import ReportWriter
from kiln_harness_tuner.schemas import TargetBlock
from kiln_harness_tuner.setup import (
    EvalSplitAuditError,
    SetupDescribeError,
    SetupPreconditionError,
    no_run_configs_message,
)
from kiln_harness_tuner.wave_log import FanoutLog

ARGS = [
    "--project-path",
    "../../Kestrel Depot",
    "--project-id",
    "424242748186",
    "--task-id",
    "424242613304",
]


@pytest.fixture(autouse=True)
def no_onboarding(monkeypatch):
    """`main` runs the start-up checks against a live Kiln before the run.
    These tests have none: the checks pass the flags through unchanged.
    `tests/test_onboarding_*.py` test the checks on a fake Kiln."""
    monkeypatch.setattr(
        kiln_harness_tuner.cli, "prepare_start", lambda args, _argv, **_kwargs: args
    )


def test_flags_build_target_block_with_defaults():
    args = build_parser().parse_args(ARGS)
    assert target_from_args(args) == TargetBlock(
        project_path="../../Kestrel Depot",
        project_id="424242748186",
        task_id="424242613304",
        kiln_host="http://localhost:8757",
    )
    assert args.timeout_mins == 180  # default timeout
    assert args.no_ui is False
    assert args.guidance == ""


def test_overrides():
    args = build_parser().parse_args(
        ARGS
        + [
            "--kiln-host",
            "http://localhost:9999",
            "--timeout-mins",
            "30",
            "--guidance",
            "one wave, cheapest issues",
            "--no-ui",
        ]
    )
    assert args.kiln_host == "http://localhost:9999"
    assert args.timeout_mins == 30
    assert args.no_ui is True


def test_objective_and_model_policy_flags(tmp_path):
    """`--objective` defaults to quality; a bad policy
    file is a start-up error with the fix, never a policy silently dropped."""
    from kiln_harness_tuner.cli import ConfigRenderError, build_ctx, load_model_policy
    from kiln_harness_tuner.kiln import KilnClient

    args = build_parser().parse_args(ARGS)
    assert args.objective == "quality" and args.model_policy is None
    args = build_parser().parse_args(ARGS + ["--objective", "speed"])
    assert args.objective == "speed"
    with pytest.raises(SystemExit):
        build_parser().parse_args(ARGS + ["--objective", "cheap"])

    assert load_model_policy(None) is None
    example = Path(kiln_harness_tuner.cli.REPO_ROOT) / "examples" / "model-policy.json"
    assert load_model_policy(str(example)) is not None
    bad = tmp_path / "bad.json"
    bad.write_text('{"allow": []}')
    with pytest.raises(ConfigRenderError, match="fix: "):
        load_model_policy(str(bad))

    policy = load_model_policy(str(example))
    target = target_from_args(build_parser().parse_args(ARGS))
    run_dir = tmp_path / "runs" / "r"
    ctx = build_ctx(
        target,
        KilnClient("http://localhost:1"),
        run_id="r",
        run_dir=run_dir,
        mcp_config_path=run_dir / "mcp_config.json",
        settings_path=run_dir / "settings.json",
        objective="speed",
        model_policy=policy,
    )
    assert ctx.objective == "speed" and ctx.model_policy is policy
    # The quarantine lands in a tree the read fence denies.
    assert ctx.quarantine_root is not None
    assert (
        ctx.quarantine_root.parent.parent == kiln_harness_tuner.cli.REPO_ROOT / "runs"
    )
    assert ctx.train_set_path == run_dir / "train_set.json"


def test_detectability_and_noise_flags(tmp_path):
    """The check is always on; `--min-effect`, the override and
    the paid noise check are opt-in, and a bad value fails at parse time."""
    from kiln_harness_tuner.cli import build_ctx
    from kiln_harness_tuner.kiln import KilnClient

    args = build_parser().parse_args(ARGS)
    assert args.min_effect is None and args.allow_underpowered is False
    assert args.noise_check == 0
    args = build_parser().parse_args(
        ARGS + ["--min-effect", "0.1", "--allow-underpowered", "--noise-check", "12"]
    )
    assert (args.min_effect, args.allow_underpowered, args.noise_check) == (
        0.1,
        True,
        12,
    )
    for bad in (["--min-effect", "0"], ["--min-effect", "1.5"], ["--noise-check", "0"]):
        with pytest.raises(SystemExit):
            build_parser().parse_args(ARGS + bad)
    run_dir = tmp_path / "runs" / "r"
    ctx = build_ctx(
        target_from_args(args),
        KilnClient("http://localhost:1"),
        run_id="r",
        run_dir=run_dir,
        mcp_config_path=run_dir / "mcp_config.json",
        settings_path=run_dir / "settings.json",
        min_effect=args.min_effect,
        allow_underpowered=args.allow_underpowered,
        noise_check_items=args.noise_check,
    )
    assert ctx.detectability_check is True
    assert (ctx.min_effect, ctx.allow_underpowered, ctx.noise_check_items) == (
        0.1,
        True,
        12,
    )


def test_the_baseline_audit_is_on_unless_turned_off(tmp_path):
    from kiln_harness_tuner.cli import build_ctx
    from kiln_harness_tuner.kiln import KilnClient

    assert build_parser().parse_args(ARGS).no_baseline_audit is False
    assert build_parser().parse_args([*ARGS, "--no-baseline-audit"]).no_baseline_audit
    run_dir = tmp_path / "runs" / "r"

    def ctx_with(**kwargs):
        return build_ctx(
            target_from_args(build_parser().parse_args(ARGS)),
            KilnClient("http://localhost:1"),
            run_id="r",
            run_dir=run_dir,
            mcp_config_path=run_dir / "mcp_config.json",
            settings_path=run_dir / "settings.json",
            **kwargs,
        )

    assert ctx_with().baseline_audit is True
    assert ctx_with(baseline_audit=False).baseline_audit is False


def test_the_ids_and_the_path_are_optional():
    """The start-up checks fill in what is missing (`onboarding.choose`)."""
    args = build_parser().parse_args([])
    assert (args.project_id, args.task_id, args.project_path) == (None, None, None)
    args = build_parser().parse_args(["--task-id", "424242613304"])
    assert (args.project_id, args.task_id) == (None, "424242613304")


def test_the_parser_records_every_long_option():
    parser = build_parser()
    every_long_option = {
        option
        for action in parser._actions
        for option in action.option_strings
        if option.startswith("--")
    }
    assert sorted(parser.long_options) == sorted(every_long_option)
    assert {"--help", "--project-id", "--task-id", "--project-path"} <= set(
        parser.long_options
    )


def test_the_parser_is_named_by_prog():
    assert PROG == "kiln-harness-tuner"
    assert build_parser().prog == PROG


def test_main_runs_the_start_up_checks_before_the_run(monkeypatch, capsys):
    seen = {}

    def fake_prepare_start(args, argv, *, prog, parser_long_options):
        seen["prog"] = prog
        seen["argv"] = argv
        seen["long_options"] = parser_long_options
        seen["given"] = args
        returned = argparse.Namespace(**vars(args))
        returned.guidance = "filled in by the checks"
        seen["returned"] = returned
        return returned

    async def fake_run(args):
        seen["run"] = args
        return "wrap_up"

    monkeypatch.setattr(kiln_harness_tuner.cli, "prepare_start", fake_prepare_start)
    monkeypatch.setattr(kiln_harness_tuner.cli, "_run", fake_run)
    assert main(ARGS) == 0
    assert seen["prog"] == PROG
    assert seen["argv"] == ARGS
    assert seen["long_options"] == build_parser().long_options
    assert seen["run"] is seen["returned"] and seen["run"] is not seen["given"]
    assert seen["run"].guidance == "filled in by the checks"
    assert "kiln-harness-tuner: run finished — wrap_up" in capsys.readouterr().out


def test_main_hands_the_start_up_the_command_line_as_typed(monkeypatch):
    """With no `argv`, the command line is `sys.argv`, and the start-up
    rebuilds the rerun command from it."""
    seen = {}

    def fake_prepare_start(args, argv, **_kwargs):
        seen["argv"] = argv
        return args

    async def fake_run(args):
        return "wrap_up"

    monkeypatch.setattr(kiln_harness_tuner.cli, "prepare_start", fake_prepare_start)
    monkeypatch.setattr(kiln_harness_tuner.cli, "_run", fake_run)
    monkeypatch.setattr("sys.argv", [PROG, "--task-id", "424242613304"])
    assert main() == 0
    assert seen["argv"] == ["--task-id", "424242613304"]


def _never_run(seen):
    async def fake_run(args):
        seen.append(args)
        return "unreachable"

    return fake_run


def test_main_prints_an_onboarding_error_and_exits_1(monkeypatch, capsys):
    def fails(args, _argv, **_kwargs):
        raise OnboardingError("Kiln isn't running at http://localhost:8757.")

    ran: list = []
    monkeypatch.setattr(kiln_harness_tuner.cli, "prepare_start", fails)
    monkeypatch.setattr(kiln_harness_tuner.cli, "_run", _never_run(ran))
    assert main(ARGS) == 1
    assert capsys.readouterr().err == (
        "kiln-harness-tuner: Kiln isn't running at http://localhost:8757.\n"
    )
    assert ran == []


@pytest.mark.parametrize("exit_code", [1, 130])
def test_main_returns_the_stop_code(monkeypatch, capsys, exit_code):
    def stops(args, _argv, **_kwargs):
        raise OnboardingStopped(exit_code)

    ran: list = []
    monkeypatch.setattr(kiln_harness_tuner.cli, "prepare_start", stops)
    monkeypatch.setattr(kiln_harness_tuner.cli, "_run", _never_run(ran))
    assert main(ARGS) == exit_code
    assert capsys.readouterr().err == "kiln-harness-tuner: stopped.\n"
    assert ran == []


def _write_mcp_json_fixture(tmp_path):
    """render_run_configs reads the repo's committed .mcp.json at startup —
    give the fake repo root one shaped like the real file."""
    (tmp_path / "repo").mkdir(parents=True, exist_ok=True)
    (tmp_path / "repo" / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "kiln_api_mcp": {
                        "type": "stdio",
                        "command": "kiln_api_mcp",
                        "args": [],
                    },
                    "kiln_chat_mcp": {
                        "type": "stdio",
                        "command": "kiln_chat_mcp",
                        "args": [],
                    },
                    "model_info_mcp": {
                        "type": "stdio",
                        "command": "kiln_model_info_mcp",
                        "args": [],
                    },
                }
            }
        )
    )


def _redirect_roots(monkeypatch, tmp_path):
    """Keep test runs' wave logs/reports out of the real repository tree."""
    monkeypatch.setattr(kiln_harness_tuner.cli, "REPO_ROOT", tmp_path / "repo")
    _write_mcp_json_fixture(tmp_path)


def test_main_runs_orchestrate_with_wired_args(capsys, monkeypatch, tmp_path):
    _redirect_roots(monkeypatch, tmp_path)
    seen = {}

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        seen["ctx"] = ctx
        seen["guidance"] = guidance
        seen["timeout_mins"] = timeout_mins
        return "wrap_up"

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    code = main(ARGS + ["--guidance", "one wave", "--timeout-mins", "30"])
    assert code == 0
    assert "wrap_up" in capsys.readouterr().out
    assert seen["guidance"] == "one wave"
    assert seen["timeout_mins"] == 30
    ctx = seen["ctx"]
    assert ctx.target.task_id == "424242613304"
    # Wiring: fanout log (wave log → tracker → UI) + the report hook.
    assert isinstance(ctx.log, FanoutLog)
    assert isinstance(ctx.write_report, ReportWriter)
    assert ctx.write_report.report_path.parent == tmp_path / "repo" / "reports"
    # The wave log landed under <repo>/runs/<run_id>/.
    (run_dir,) = [d for d in (tmp_path / "repo" / "runs").iterdir() if d.is_dir()]
    assert (run_dir / "wave_log.jsonl").exists()
    # …and beside it the run's diagnostics file, already carrying
    # the run's opening line — the bootstrap runs before anything can fail.
    log_text = (run_dir / "kiln-harness-tuner.log").read_text()
    assert f"run {run_dir.name} starting" in log_text
    assert "project 424242748186 task 424242613304" in log_text


def test_main_missing_baseline_prints_instructive_error(monkeypatch, capsys, tmp_path):
    _redirect_roots(monkeypatch, tmp_path)

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        raise BaselineRecordMissingError(
            "no baseline record for task 424242613304 — run baseline setup"
        )

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    code = main(ARGS)
    assert code == 1
    assert "run baseline setup" in capsys.readouterr().err


def test_main_setup_precondition_error_prints_instructive_error(
    monkeypatch, capsys, tmp_path
):
    _redirect_roots(monkeypatch, tmp_path)

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        raise SetupPreconditionError(no_run_configs_message("424242613304"))

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    assert main(ARGS) == 1
    # The instructive message lands, not a traceback (same treatment as the
    # other startup errors).
    assert "Create a run config for this task" in capsys.readouterr().err


@pytest.mark.parametrize(
    "error,expected",
    [
        (
            EvalSplitAuditError("2 evals are missing a train/val split"),
            "missing a train/val split",
        ),
        (
            SetupDescribeError(
                "424242966189", "Handoff Block", "rate_limit: 429", attempts=2
            ),
            "could not describe eval 424242966189",
        ),
        (
            UnderpoweredEvalsError(
                "No active eval score can show a significant win on val"
            ),
            "No active eval score can show a significant win on val",
        ),
        (
            ModelPreflightError(
                "model preflight: the baseline rc_base runs on openai/base_model"
            ),
            "model preflight: the baseline rc_base runs on openai/base_model",
        ),
    ],
)
def test_main_prints_every_setup_error_instructively(
    monkeypatch, capsys, tmp_path, error, expected
):
    # All three setup fatals share `SetupError` and the same treatment: the
    # message, no traceback, exit 1.
    _redirect_roots(monkeypatch, tmp_path)

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        raise error

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    assert main(ARGS) == 1
    assert expected in capsys.readouterr().err


# An invented sibling task id: this tree is a fixture, so nothing in it needs
# to name a real task.
SIBLING_TASK_ID = "424242111222"


def _fixture_project(tmp_path, task_id):
    """A Kiln project tree on disk: the run's own task plus one sibling.

    The read fence globs the project for the run's own task directory and
    replaces the whole-tree deny with a per-sibling one when it finds it, so
    a project path that does not exist silences the rule this class asserts.
    """
    project = tmp_path / "Subject Project"
    tasks = project / "tasks"
    (tasks / f"{task_id} - Subject Task").mkdir(parents=True, exist_ok=True)
    (tasks / f"{SIBLING_TASK_ID} - Sibling Task").mkdir(parents=True, exist_ok=True)
    (project / "assistant_memory").mkdir(exist_ok=True)
    return project


class TestRenderRunConfigs:
    TASK_ID = "424242613304"

    def make(self, tmp_path):
        _write_mcp_json_fixture(tmp_path)
        target = TargetBlock(
            project_path=str(_fixture_project(tmp_path, self.TASK_ID)),
            project_id="424242748186",
            task_id=self.TASK_ID,
            kiln_host="http://localhost:9999",
        )
        run_dir = tmp_path / "repo" / "runs" / "260731-0900"
        return render_run_configs(run_dir, target, tmp_path / "repo"), run_dir

    def test_each_role_gets_the_gateway_and_no_generic_kiln_server(self, tmp_path):
        """Loop sessions reach Kiln through the loop gateway only.
        No role's MCP config holds `kiln_api_mcp` (or any server of the
        committed .mcp.json); each holds the gateway started for that role."""
        from kiln_harness_tuner.gateway.ops import ROLE_OPS

        (mcp_path, _), run_dir = self.make(tmp_path)
        assert mcp_path == run_dir / "mcp_config.json"
        project_root = _fixture_project(tmp_path, self.TASK_ID).resolve()
        for role in ROLE_OPS:
            servers = json.loads((run_dir / f"mcp_config.{role}.json").read_text())[
                "mcpServers"
            ]
            expected = {"kiln_harness_tuner_gateway"} | (
                {"skill_installer"}
                if role in ("create_experiment_rc", "probe")
                else set()
            )
            assert set(servers) == expected, role
            gateway = servers["kiln_harness_tuner_gateway"]
            assert gateway["command"] == "uv"
            args = gateway["args"]
            assert args[:4] == [
                "run",
                "--project",
                str(tmp_path / "repo"),
                "kiln-harness-tuner-gateway-mcp",
            ]
            assert args[args.index("--role") + 1] == role
            assert args[args.index("--run-dir") + 1] == str(run_dir.resolve())
            assert args[args.index("--kiln-host") + 1] == "http://localhost:9999"
            assert args[args.index("--project-path") + 1] == str(project_root)
            text = json.dumps(servers)
            assert "kiln_api_mcp" not in text and "call_kiln_api" not in text
        preflight = json.loads(mcp_path.read_text())["mcpServers"]
        assert set(preflight) == {"kiln_harness_tuner_gateway"}
        # No cap given: the gateway gets no cap.
        assert (
            "--max-concurrent-evals"
            not in preflight["kiln_harness_tuner_gateway"]["args"]
        )

    def test_the_gateway_gets_the_eval_job_cap(self, tmp_path):
        """The gateway's drives through eval jobs take
        `--max-concurrent-evals` too."""
        _write_mcp_json_fixture(tmp_path)
        target = TargetBlock(
            project_path=str(_fixture_project(tmp_path, self.TASK_ID)),
            project_id="424242748186",
            task_id=self.TASK_ID,
            kiln_host="http://localhost:9999",
        )
        run_dir = tmp_path / "repo" / "runs" / "260731-0900"
        mcp_path, _ = render_run_configs(
            run_dir, target, tmp_path / "repo", max_concurrent_evals=3
        )
        args = json.loads(mcp_path.read_text())["mcpServers"][
            "kiln_harness_tuner_gateway"
        ]["args"]
        assert args[args.index("--max-concurrent-evals") + 1] == "3"

    def test_skill_installer_server_rendered(self, tmp_path):
        # Run-scoped: absolute uv --project path plus the run's resolved
        # project + scratch roots — in the builder's and the probe's configs.
        (_, _s), run_dir = self.make(tmp_path)
        server = json.loads(
            (run_dir / "mcp_config.create_experiment_rc.json").read_text()
        )["mcpServers"]["skill_installer"]
        assert server["type"] == "stdio"
        assert server["command"] == "uv"
        project_root = _fixture_project(tmp_path, self.TASK_ID).resolve()
        assert server["args"] == [
            "run",
            "--project",
            str(tmp_path / "repo"),
            "kiln-harness-tuner-skill-installer-mcp",
            "--project-path",
            str(project_root),
            "--scratch-root",
            str((run_dir / "scratch").resolve()),
        ]

    def test_settings_deny_project_tree(self, tmp_path):
        # The Write-tool leak closure: the Kiln
        # project tree is deny-ruled for ALL file tools; deny beats both the
        # scratch allow and the implicit cwd grant. Scratch lives under
        # <repo>/runs/, disjoint, so the allow is untouched.
        (_, settings_path), run_dir = self.make(tmp_path)
        settings = json.loads(settings_path.read_text())
        project_root = _fixture_project(tmp_path, self.TASK_ID).resolve()
        # First rule is the project-tree Edit deny; the read fence and the
        # web-tool denies follow it.
        deny_rule = settings["permissions"]["deny"][0]
        assert deny_rule == f"Edit(//{str(project_root).lstrip('/')}/**)"
        assert "///" not in deny_rule
        assert all("///" not in rule for rule in settings["permissions"]["deny"])
        scratch = (run_dir / "scratch").resolve()
        assert settings["permissions"]["allow"] == [
            f"Edit(//{str(scratch).lstrip('/')}/**)"
        ]

    def test_the_trace_readers_may_read_the_trace_folder(self, tmp_path):
        """The gateway writes train traces to the run's
        `scratch/traces/`. Each role that reads traces gets an explicit Read
        allow on it (Read rules cover Grep and Glob); no other role does,
        and no file tool may write it — only the gateway does."""
        from kiln_harness_tuner.gateway.ops import ROLE_OPS, TRACE_READER_ROLES

        assert TRACE_READER_ROLES == {
            "batch_plan",
            "create_experiment_rc",
            "probe",
            "analyze_experiment_results",
            "eval_build",
        }
        (mcp_path, settings_path), run_dir = self.make(tmp_path)
        traces = (run_dir / "scratch" / "traces").resolve()
        assert traces.is_dir()
        body = str(traces).lstrip("/")
        read_rule, edit_deny = f"Read(//{body}/**)", f"Edit(//{body}/**)"
        for role in ROLE_OPS:
            settings = json.loads((run_dir / f"settings.{role}.json").read_text())
            allow = settings["permissions"]["allow"]
            assert (read_rule in allow) == (role in TRACE_READER_ROLES), role
            # Nothing else is widened: the scratch write grant, or nothing.
            assert set(allow) - {read_rule} <= {
                f"Edit(//{str((run_dir / 'scratch').resolve()).lstrip('/')}/**)"
            }, role
            assert edit_deny in settings["permissions"]["deny"], role
            args = json.loads((run_dir / f"mcp_config.{role}.json").read_text())[
                "mcpServers"
            ]["kiln_harness_tuner_gateway"]["args"]
            assert args[args.index("--traces-dir") + 1] == str(traces)
        for shared in (settings_path, run_dir / "settings.readonly.json"):
            assert (
                read_rule not in json.loads(shared.read_text())["permissions"]["allow"]
            )

    def test_settings_scope_writes_to_scratch(self, tmp_path):
        (_, settings_path), run_dir = self.make(tmp_path)
        settings = json.loads(settings_path.read_text())
        scratch = (run_dir / "scratch").resolve()
        # File tools: ONE path-scoped Edit allow rule (Edit rules cover all
        # file-editing tools incl. Write; path-scoped Write rules are never
        # consulted). `//` REPLACES the absolute path's leading slash —
        # `Edit(//home/…)`, never `Edit(///home/…)`. In -p mode everything
        # else is denied.
        (edit_rule,) = settings["permissions"]["allow"]
        assert edit_rule == f"Edit(//{str(scratch).lstrip('/')}/**)"
        assert "///" not in edit_rule
        # Bash: OS sandbox on, loud when unavailable, no unsandboxed escape
        # hatch; repo denied, scratch re-opened.
        sandbox = settings["sandbox"]
        assert sandbox["enabled"] is True
        assert sandbox["failIfUnavailable"] is True
        assert sandbox["allowUnsandboxedCommands"] is False
        assert sandbox["filesystem"]["denyWrite"] == [
            str((tmp_path / "repo").resolve())
        ]
        assert sandbox["filesystem"]["allowWrite"] == [str(scratch)]

    def test_each_roles_settings_arm_the_gateway_hook_for_that_role(self, tmp_path):
        """The hook admits only the role's own MCP tools; the
        shared settings files carry role "none" (no MCP tool at all)."""
        from kiln_harness_tuner.gateway.ops import ROLE_OPS

        (_, settings_path), run_dir = self.make(tmp_path)
        for name, role in [
            ("settings.json", "none"),
            ("settings.readonly.json", "none"),
        ] + [(f"settings.{r}.json", r) for r in ROLE_OPS]:
            settings = json.loads((run_dir / name).read_text())
            (entry,) = settings["hooks"]["PreToolUse"]
            assert entry["matcher"] == "mcp__.*"
            (hook,) = entry["hooks"]
            assert hook["command"].endswith(
                f"-m kiln_harness_tuner.gateway.hook --role {role}"
            )
            assert hook["timeout"] == 10
            assert "mcp__kiln_api_mcp" in settings["permissions"]["deny"]

    def test_no_side_door_in_any_roles_settings(self, tmp_path):
        """Every role's settings keep the empty network
        allowlist and the file fence over the project and the run directory
        — the gateway is the only lane to Kiln."""
        from kiln_harness_tuner.gateway.ops import ROLE_OPS

        (_, _s), run_dir = self.make(tmp_path)
        project = _fixture_project(tmp_path, self.TASK_ID).resolve()
        for role in ROLE_OPS:
            settings = json.loads((run_dir / f"settings.{role}.json").read_text())
            assert settings["sandbox"]["network"] == {
                "strictAllowlist": True,
                "allowedDomains": [],
            }, role
            deny = settings["sandbox"]["filesystem"]["denyRead"]
            assert str(project) in deny, role
            rules = settings["permissions"]["deny"]
            assert f"Read(//{str(project).lstrip('/')}/**)" in rules, role
            run = run_dir.resolve()
            assert any(str(run / "train_set.json") in r for r in rules), role

    def test_readonly_settings_grant_no_write(self, tmp_path):
        # The val readers run under their own read-only files.
        (_, settings_path), run_dir = self.make(tmp_path)
        normal = json.loads(settings_path.read_text())
        for name in (
            "settings.readonly.json",
            "settings.promote_best.json",
            "settings.report_writer.json",
        ):
            readonly = json.loads((run_dir / name).read_text())
            assert readonly["permissions"]["allow"] == []
            scratch = (run_dir / "scratch").resolve()
            assert (
                f"Edit(//{str(scratch).lstrip('/')}/**)"
                in readonly["permissions"]["deny"]
            )
            assert readonly["sandbox"]["filesystem"]["allowWrite"] == []
            for rule in normal["permissions"]["deny"]:
                assert rule in readonly["permissions"]["deny"]

    def test_settings_fence_reads_and_the_network(self, tmp_path):
        # An agent reads its own task, the project artifacts, the repo's
        # skills and CLAUDE.md, and its own run scratch — never other tasks,
        # other Kiln projects, other runs, reports, `examples/` (the answer
        # key) or other Claude sessions' transcripts; no internet, no web
        # tools.
        (_, settings_path), run_dir = self.make(tmp_path)
        settings = json.loads(settings_path.read_text())
        fs = settings["sandbox"]["filesystem"]
        repo = (tmp_path / "repo").resolve()
        project = _fixture_project(tmp_path, self.TASK_ID).resolve()
        # The run's own project is denied WHOLE — it holds the
        # eval inputs and results of every split.
        assert str(project) in fs["denyRead"]
        assert str(repo / "runs") in fs["denyRead"]
        assert str(repo / "traces") in fs["denyRead"]
        assert str(repo / "reports") in fs["denyRead"]
        # This fixture's repo has no `examples/`, so the name is denied
        # whole; the per-level rule has its own tests below
        # (`test_examples_are_denied_whole_except_the_runs_own_project` and
        # `test_the_examples_deny_recurses_to_the_projects_own_depth`).
        assert str(repo / "examples") in fs["denyRead"]
        assert str(Path.home() / ".claude" / "projects") in fs["denyRead"]
        # Only the scratch of this run — not its wave log, steps or
        # snapshots, which carry val tables.
        assert str((run_dir / "scratch").resolve()) in fs["allowRead"]
        assert str(run_dir.resolve()) not in fs["allowRead"]
        assert str(repo / ".claude" / "skills") in fs["allowRead"]
        assert str(repo / "CLAUDE.md") in fs["allowRead"]
        assert settings["sandbox"]["network"] == {
            "strictAllowlist": True,
            "allowedDomains": [],
        }
        deny = settings["permissions"]["deny"]
        assert "WebFetch" in deny and "WebSearch" in deny
        assert any(rule.startswith("Read(//") and "reports" in rule for rule in deny)
        # The project is one static rule — nothing inside it is
        # re-opened, so later files are covered too.
        read_rules = [r for r in deny if r.startswith("Read(//")]
        assert f"Read(//{str(project).lstrip('/')}/**)" in read_rules
        # The run directory: every entry but the scratch, files included.
        run = run_dir.resolve()
        for entry in ("wave_log.jsonl", "steps", "snapshots", "train_set.json"):
            assert any(str(run / entry) in rule for rule in read_rules), entry
        assert not any(str(run / "scratch") in rule for rule in read_rules)

    def test_fence_denies_a_kiln_project_nested_under_the_repo(self, tmp_path):
        """A project can sit two levels down (`examples/<name>/`), so
        the one-level glob this replaced could not see it — and a project the
        fence cannot see is a project whose runs and memory store an agent can
        read."""
        other = tmp_path / "repo" / "examples" / "Other Project"
        other.mkdir(parents=True)
        (other / "project.kiln").write_text("v1: 1\n")
        (_, settings_path), _ = self.make(tmp_path)
        denied = json.loads(settings_path.read_text())["sandbox"]["filesystem"][
            "denyRead"
        ]
        assert str(other.resolve()) in denied


def test_kiln_project_dirs_finds_every_depth_and_prunes(tmp_path):
    from kiln_harness_tuner.cli import kiln_project_dirs

    for rel in ("Top", "examples/Nested", ".hidden/Buried"):
        (tmp_path / rel).mkdir(parents=True)
        (tmp_path / rel / "project.kiln").write_text("v1: 1\n")
    # A project's own tree holds tasks, runs and eval runs, never a second
    # project — descending into it is wasted work on a large fixture tree.
    inner = tmp_path / "Top" / "tasks" / "1 - a"
    inner.mkdir(parents=True)
    (inner / "project.kiln").write_text("v1: 1\n")
    assert kiln_project_dirs(tmp_path) == [
        tmp_path / "Top",
        tmp_path / "examples" / "Nested",
    ]


def test_kiln_project_dirs_follows_a_symlinked_project(tmp_path):
    """A project checked out elsewhere and symlinked in is what a symlink is
    for, and the glob this walk replaced found it. `os.walk` lists a symlinked
    directory and does not descend into it by default, so without
    `followlinks` that project's runs and memory store stay readable."""
    from kiln_harness_tuner.cli import kiln_project_dirs

    elsewhere = tmp_path / "elsewhere" / "Linked"
    elsewhere.mkdir(parents=True)
    (elsewhere / "project.kiln").write_text("v1: 1\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Linked").symlink_to(elsewhere, target_is_directory=True)
    assert kiln_project_dirs(repo) == [repo / "Linked"]


def test_kiln_project_dirs_survives_a_symlink_cycle(tmp_path):
    # Following links is only safe with the visited set that stops a cycle.
    repo = tmp_path / "repo"
    (repo / "a").mkdir(parents=True)
    (repo / "a" / "loop").symlink_to(repo, target_is_directory=True)
    (repo / "a" / "P").mkdir()
    (repo / "a" / "P" / "project.kiln").write_text("v1: 1\n")
    from kiln_harness_tuner.cli import kiln_project_dirs

    assert kiln_project_dirs(repo) == [repo / "a" / "P"]


def test_kiln_project_dirs_never_returns_or_prunes_at_the_root(tmp_path):
    """A repository that is itself a Kiln project would otherwise deny itself
    whole — and when it IS the run's own project, pruning at the root ended
    the walk with nothing denied at all."""
    from kiln_harness_tuner.cli import kiln_project_dirs

    (tmp_path / "project.kiln").write_text("v1: 1\n")
    nested = tmp_path / "examples" / "Other"
    nested.mkdir(parents=True)
    (nested / "project.kiln").write_text("v1: 1\n")
    assert kiln_project_dirs(tmp_path) == [nested]


def test_examples_are_denied_whole_except_the_runs_own_project(tmp_path):
    """An example's labels, its README and its build scripts are the answer
    key as much as its Kiln project. Denied by convention, so no name below
    `examples/` is hardcoded."""
    from kiln_harness_tuner.cli import example_denials

    repo = tmp_path / "repo"
    examples = repo / "examples"
    own = examples / "Forum Moderation"
    for rel in ("authoring", "moderation", "Forum Moderation", "Other Project"):
        (examples / rel).mkdir(parents=True)
    (examples / "moderation" / "posts.jsonl").write_text("{}\n")
    assert example_denials(repo, own) == [
        examples / "Other Project",
        examples / "authoring",
        examples / "moderation",
    ]


def test_the_examples_deny_recurses_to_the_projects_own_depth(tmp_path):
    """A project one level deeper leaves its example's files BESIDE it. The
    directory containing the project is not denied, because it is an
    ancestor, so its other children are denied one by one. The
    sibling-project rule does not cover them: they are not a Kiln project."""
    from kiln_harness_tuner.cli import example_denials

    repo = tmp_path / "repo"
    examples = repo / "examples"
    own = examples / "env" / "Forum Moderation"
    for rel in ("env/Forum Moderation", "env/moderation", "env/authoring", "other"):
        (examples / rel).mkdir(parents=True)
    denied = example_denials(repo, own)
    assert denied == [
        examples / "other",
        examples / "env" / "authoring",
        examples / "env" / "moderation",
    ]
    assert own not in denied
    assert examples / "env" not in denied


def test_the_examples_name_is_denied_when_the_directory_is_missing(tmp_path):
    from kiln_harness_tuner.cli import example_denials

    repo = tmp_path / "repo"
    repo.mkdir()
    assert example_denials(repo, repo / "elsewhere") == [repo / "examples"]


def test_all_examples_are_denied_when_the_project_is_outside_them(tmp_path):
    # A run against a project somewhere else has no reason to read any of it.
    from kiln_harness_tuner.cli import example_denials

    repo = tmp_path / "repo"
    (repo / "examples" / "moderation").mkdir(parents=True)
    assert example_denials(repo, tmp_path / "Elsewhere") == [repo / "examples"]


def test_all_examples_are_denied_when_the_project_is_examples_itself(tmp_path):
    """The degenerate layout fails CLOSED, like the root case in
    `kiln_project_dirs`. There is no path to walk down, so nothing can be left
    open without leaving the whole answer key open — the run loses its own
    project and breaks loudly, which is the outcome to prefer."""
    from kiln_harness_tuner.cli import example_denials

    repo = tmp_path / "repo"
    examples = repo / "examples"
    (examples / "moderation").mkdir(parents=True)
    (examples / "project.kiln").write_text("v1: 1\n")
    assert example_denials(repo, examples) == [examples]


def test_a_missing_directory_on_the_project_path_names_the_flag(tmp_path):
    """`--project-path` is validated nowhere upstream, so a typo or a project
    not yet checked out reaches the fence — inside the startup path that
    change 10 rewrote to be instructive. It raised a raw FileNotFoundError."""
    from kiln_harness_tuner.cli import ConfigRenderError, example_denials

    repo = tmp_path / "repo"
    (repo / "examples" / "moderation").mkdir(parents=True)
    with pytest.raises(ConfigRenderError) as raised:
        example_denials(repo, repo / "examples" / "typo" / "Forum Moderation")
    message = str(raised.value)
    assert "\nfix: " in message
    assert "--project-path" in message
    assert str(repo / "examples" / "typo") in message


def test_read_fence_denies_the_runs_own_project_whole(tmp_path):
    from kiln_harness_tuner.cli import read_fence_for

    repo = tmp_path / "repo"
    own = repo / "examples" / "Forum Moderation"
    (own / "tasks" / "42 - t").mkdir(parents=True)
    (own / "project.kiln").write_text("v1: 1\n")
    other = repo / "examples" / "Other"
    other.mkdir(parents=True)
    (other / "project.kiln").write_text("v1: 1\n")
    fence = read_fence_for(
        repo_root=repo,
        project_root=own,
        run_dir=repo / "runs" / "260731-0900",
        task_id="42",
        home=tmp_path / "home",
    )
    assert other in fence.deny
    # The run's own project, its own task included, is denied.
    assert own in fence.deny
    assert not any(own == a or own in a.parents for a in fence.allow)
    assert fence.rule_paths()[0] == own


def test_read_fence_rule_paths_open_the_allowed_child(tmp_path):
    from kiln_harness_tuner.cli import ReadFence

    tasks = tmp_path / "tasks"
    for name in ("1 - a", "2 - b", "3 - c"):
        (tasks / name).mkdir(parents=True)
    fence = ReadFence(deny=(tasks, tmp_path / "reports"), allow=(tasks / "2 - b",))
    (tasks / ".DS_Store").write_text("")
    # Files beside the allowed path are fenced too — the wave
    # log beside a run's scratch is a file.
    assert fence.rule_paths() == (
        tasks / ".DS_Store",
        tasks / "1 - a",
        tasks / "3 - c",
        tmp_path / "reports",
    )


def test_read_fence_rule_paths_recurse_down_to_the_allowed_path(tmp_path):
    """The run's scratch sits two levels under the denied `runs/`: every
    sibling at each level — the other runs, this run's wave log, steps and
    snapshots — is fenced, and nothing on the path to the scratch is."""
    from kiln_harness_tuner.cli import ReadFence

    runs = tmp_path / "runs"
    run = runs / "260924-0900"
    (run / "scratch" / "x").mkdir(parents=True)
    (run / "steps").mkdir()
    (run / "wave_log.jsonl").write_text("")
    (runs / "260923-0800").mkdir()
    (runs / ".quarantine").mkdir()
    fence = ReadFence(deny=(runs,), allow=(run / "scratch",))
    assert set(fence.rule_paths()) == {
        runs / ".quarantine",
        runs / "260923-0800",
        run / "steps",
        run / "wave_log.jsonl",
    }


def test_main_sweeps_stale_staging_dirs(monkeypatch, tmp_path):
    # Crash debris from a dead run's skill install is reaped at run start
    # (the run lock guarantees no live install races the sweep).
    _redirect_roots(monkeypatch, tmp_path)
    project = tmp_path / "proj"
    staging = project / "skills" / ".staging-424242999999"
    staging.mkdir(parents=True)
    (staging / "junk.txt").write_text("x")
    keeper = project / "skills" / "424242111111 - keeper"
    keeper.mkdir()

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        return "wrap_up"

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    args = [
        "--project-path",
        str(project),
        "--project-id",
        "424242748186",
        "--task-id",
        "424242613304",
    ]
    assert main(args) == 0
    assert not staging.exists()
    assert keeper.is_dir()


def test_main_wires_dispatcher_with_rendered_configs(monkeypatch, tmp_path):
    _redirect_roots(monkeypatch, tmp_path)
    seen = {}

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        seen["ctx"] = ctx
        return "wrap_up"

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    assert main(ARGS) == 0
    dispatch = seen["ctx"].dispatch
    (run_dir,) = [d for d in (tmp_path / "repo" / "runs").iterdir() if d.is_dir()]
    assert dispatch._mcp_config_path == run_dir / "mcp_config.json"
    assert dispatch._settings_path == run_dir / "settings.json"
    assert dispatch._run_dir == run_dir
    assert dispatch._run_id == run_dir.name
    assert dispatch._traces_root == tmp_path / "repo" / "traces"
    # Both rendered files exist before any dispatch could run.
    assert (run_dir / "mcp_config.json").is_file()
    assert (run_dir / "settings.json").is_file()


def test_main_preflight_error_prints_instructive_error(monkeypatch, capsys, tmp_path):
    _redirect_roots(monkeypatch, tmp_path)

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        raise PreflightError(
            "preflight failed — the Kiln Harness Tuner gateway MCP lane is not working"
        )

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    assert main(ARGS) == 1
    assert "gateway MCP lane" in capsys.readouterr().err


def test_render_needs_no_committed_mcp_json(tmp_path):
    """Loop sessions never use the committed .mcp.json (it serves
    interactive sessions): a repo without one still renders."""
    target = TargetBlock(
        project_path="p", project_id="1", task_id="2", kiln_host="http://h"
    )
    mcp_path, _ = render_run_configs(tmp_path / "run", target, tmp_path / "empty-repo")
    assert (
        "kiln_harness_tuner_gateway" in json.loads(mcp_path.read_text())["mcpServers"]
    )


# ---- role overrides, run lock, unique ids ----


class TestRoleOverrideParsing:
    def test_valid_pairs_parse(self):
        from kiln_harness_tuner.cli import parse_role_overrides

        overrides = parse_role_overrides(
            ["describe_eval=haiku", "report_writer=sonnet"], "--agent-model"
        )
        assert overrides == {"describe_eval": "haiku", "report_writer": "sonnet"}

    def test_unknown_role_fails_at_startup(self):
        from kiln_harness_tuner.cli import ConfigRenderError, parse_role_overrides

        with pytest.raises(ConfigRenderError, match="unknown role 'planner'"):
            parse_role_overrides(["planner=opus"], "--agent-model")

    def test_malformed_pair_fails_at_startup(self):
        from kiln_harness_tuner.cli import ConfigRenderError, parse_role_overrides

        with pytest.raises(ConfigRenderError, match="ROLE=VALUE"):
            parse_role_overrides(["describe_eval"], "--agent-model")


def test_new_flags_have_safe_defaults():
    args = build_parser().parse_args(
        ["--project-path", "/p", "--project-id", "p1", "--task-id", "t1"]
    )
    assert args.budget_usd is None
    assert args.coverage_floor == pytest.approx(0.95)
    assert args.max_concurrent_evals == 4
    assert args.max_concurrent_agents == 4
    assert args.agent_model == []
    assert args.agent_effort == []
    assert args.max_turns is None  # uncapped by default — opt-in knob


def test_unique_run_id_suffixes_on_collision(tmp_path):
    from datetime import datetime

    from kiln_harness_tuner.cli import _unique_run_id

    now = datetime(2026, 8, 3, 17, 5)
    # Each call CLAIMS its id by creating the directory, so two
    # runs in the same minute cannot both pick the same id.
    assert _unique_run_id(tmp_path, now) == "260803-1705"
    assert (tmp_path / "260803-1705").is_dir()
    assert _unique_run_id(tmp_path, now) == "260803-1705-2"
    assert _unique_run_id(tmp_path, now) == "260803-1705-3"


class TestRunLock:
    def test_lock_acquire_and_release_shape(self, tmp_path):
        from kiln_harness_tuner.cli import _acquire_run_lock

        lock = _acquire_run_lock(tmp_path, "t1")
        record = json.loads(lock.path.read_text())
        assert record["pid"] == __import__("os").getpid()
        assert record["task_id"] == "t1" and "started" in record
        lock.release()
        # Released, not unlinked: the file is the lock's
        # identity; a fresh inode under the same name would split the lock.
        assert lock.path.exists()
        assert _acquire_run_lock(tmp_path, "t1").path == lock.path

    def test_lock_key_carries_the_project_id_when_known(self, tmp_path):
        from kiln_harness_tuner.cli import _acquire_run_lock

        # The same task id can exist in two projects; a lock keyed by task
        # alone would block or miss across them.
        lock = _acquire_run_lock(tmp_path, "t1", "p9")
        assert lock.path.name == ".lock-p9-t1"
        assert json.loads(lock.path.read_text())["project_id"] == "p9"

    def test_a_held_flock_refuses_a_second_run(self, tmp_path):
        import fcntl
        import os

        from kiln_harness_tuner.cli import RunLockError, _acquire_run_lock

        # The kernel lock is the claim, whatever the file says: here the
        # holder has the flock but has not written its record yet, and the
        # lock must not be reclaimed.
        path = tmp_path / ".lock-t1"
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(RunLockError, match="one run per task"):
                _acquire_run_lock(tmp_path, "t1")
        finally:
            os.close(fd)
        # Released by the holder's death (here: the close) — no reclaim step.
        assert _acquire_run_lock(tmp_path, "t1").path == path

    def test_live_holder_refuses_a_second_run(self, tmp_path):
        import os

        from kiln_harness_tuner.cli import RunLockError, _acquire_run_lock

        # An older kiln-harness-tuner wrote a bare live pid and holds no flock: still
        # honoured.
        (tmp_path / ".lock-t1").write_text(str(os.getppid()))
        with pytest.raises(RunLockError, match="one run per task"):
            _acquire_run_lock(tmp_path, "t1")

    def test_stale_lock_is_reclaimed(self, tmp_path):
        from kiln_harness_tuner.cli import _acquire_run_lock

        # A pid that cannot be alive (max pid is far below this on any host).
        (tmp_path / ".lock-t1").write_text("99999999")
        lock = _acquire_run_lock(tmp_path, "t1")
        assert lock.path.exists()
        assert json.loads(lock.path.read_text())["task_id"] == "t1"

    def test_garbage_lock_is_reclaimed(self, tmp_path):
        from kiln_harness_tuner.cli import _acquire_run_lock

        (tmp_path / ".lock-t1").write_text("not-a-pid")
        assert _acquire_run_lock(tmp_path, "t1").path.exists()


class TestSigterm:
    def test_sigterm_cancels_the_run_and_main_returns_143(self, monkeypatch):
        """`kill <pid>` must not terminate Python outright — that skips the
        finally blocks, leaves the lock behind and lets every `claude -p`
        child run to its own cap. The handler cancels the main task instead,
        so teardown runs."""
        import asyncio
        import os
        import signal
        import threading

        seen: list[str] = []

        async def fake_run(args):
            seen.append("started")
            try:
                os.kill(os.getpid(), signal.SIGTERM)
                await asyncio.sleep(5)
            finally:
                seen.append("finally")
            return "unreachable"

        monkeypatch.setattr(kiln_harness_tuner.cli, "_run", fake_run)
        rc: list[int] = []

        # Signal handlers install only on the main thread's loop; pytest's
        # sync test runs on the main thread, so `main` owns the loop here.
        def target():
            rc.append(
                main(["--project-path", "x", "--project-id", "p", "--task-id", "t"])
            )

        target()
        assert rc == [143]  # 128 + SIGTERM, the shell's own code
        assert seen == ["started", "finally"]
        assert threading.current_thread() is threading.main_thread()

    def test_sighup_stops_the_run_the_same_way(self, monkeypatch, capsys):
        """A closed terminal sends SIGHUP: teardown runs, exit 129."""
        import asyncio
        import os
        import signal

        seen: list[str] = []

        async def fake_run(args):
            try:
                os.kill(os.getpid(), signal.SIGHUP)
                await asyncio.sleep(5)
            finally:
                seen.append("finally")
            return "unreachable"

        monkeypatch.setattr(kiln_harness_tuner.cli, "_run", fake_run)
        rc = main(["--project-path", "x", "--project-id", "p", "--task-id", "t"])
        assert rc == 129 and seen == ["finally"]
        assert "interrupted (SIGHUP)" in capsys.readouterr().err
