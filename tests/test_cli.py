"""CLI tests: the flags parse, wire a TargetBlock, and main runs the
orchestrator (monkeypatched — no live Kiln)."""

import argparse
import io
import json
import sys
from pathlib import Path

import pytest
from rich.console import Console

import kiln_harness_tuner.cli
from kiln_harness_tuner.analysis import AnalysisError
from kiln_harness_tuner.cli import (
    ERROR_TITLES,
    GATEWAY_MODULE,
    INSTALLER_MODULE,
    PROG,
    ConfigRenderError,
    RunLockError,
    build_parser,
    load_model_policy,
    main,
    render_run_configs,
    target_from_args,
    title_for,
)
from kiln_harness_tuner.contract import ContractPreflightError
from kiln_harness_tuner.detectability import UnderpoweredEvalsError
from kiln_harness_tuner.dispatch.claude_cli import PreflightError
from kiln_harness_tuner.gateway.trainset import TrainSetError
from kiln_harness_tuner.install_agents import package_version
from kiln_harness_tuner.kiln import KilnAPIError, KilnError, KilnTransportError
from kiln_harness_tuner.memory import BaselineRecordMissingError, MemoryWriteRefused
from kiln_harness_tuner.memory_schema import RecordKindError
from kiln_harness_tuner.model_preflight import ModelPreflightError
from kiln_harness_tuner.onboarding import OnboardingError, OnboardingStopped
from kiln_harness_tuner.onboarding.demo import demo_fence_root
from kiln_harness_tuner.onboarding.summary import OptionInfo
from kiln_harness_tuner.paths import StatePaths
from kiln_harness_tuner.quarantine import QuarantineError
from kiln_harness_tuner.report import ReportWriter
from kiln_harness_tuner.schemas import TargetBlock
from kiln_harness_tuner.setup import (
    EvalSplitAuditError,
    SetupDescribeError,
    SetupError,
    SetupPreconditionError,
    no_run_configs_message,
)
from kiln_harness_tuner.ui import PlainUi
from kiln_harness_tuner.wave_log import FanoutLog

ARGS = [
    "--project-path",
    "../../Kestrel Depot",
    "--project-id",
    "424242748186",
    "--task-id",
    "424242613304",
]
REPO = Path(__file__).resolve().parents[1]


def _state(tmp_path) -> StatePaths:
    return StatePaths(tmp_path / "state")


def _agents_dir(state: StatePaths, cli: str = "claude") -> Path:
    """Where `install_for_run` puts a run's agent files."""
    return state.agents / cli / package_version()


def _run_dir(state_dir: Path) -> Path:
    """The one run folder a `main` call made in the per-test state folder."""
    (run_dir,) = [d for d in (state_dir / "runs").iterdir() if d.is_dir()]
    return run_dir


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
    assert args.timeout_mins == 720  # default timeout: 12 hours
    assert args.no_progress is False
    assert args.guidance == ""


def test_the_parser_is_named_after_the_command():
    # `uvx harness-tuner` runs the command named `harness-tuner`, so usage
    # and errors name it too.
    assert build_parser().prog == "harness-tuner"


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
            "--no-progress",
        ]
    )
    assert args.kiln_host == "http://localhost:9999"
    assert args.timeout_mins == 30
    assert args.no_progress is True


def test_no_ui_was_renamed_no_progress(capsys):
    with pytest.raises(SystemExit) as exited:
        build_parser().parse_args(["--no-ui"])
    assert exited.value.code == 2
    assert capsys.readouterr().err.splitlines()[-1] == (
        "harness-tuner: error: --no-ui was renamed --no-progress:"
        " plain log lines instead of the live progress display"
    )


def test_the_budget_defaults_to_50_dollars():
    assert build_parser().parse_args([]).budget_usd == 50.0


@pytest.mark.parametrize(
    ("given", "budget"),
    [("none", None), ("NONE", None), ("$12.50", 12.5), ("7", 7.0)],
)
def test_the_budget_takes_none_and_a_dollar_sign(given, budget):
    assert build_parser().parse_args(["--budget-usd", given]).budget_usd == budget


@pytest.mark.parametrize("given", ["0", "-5", "lots", "$", "nan"])
def test_an_invalid_budget_exits_2(given, capsys):
    with pytest.raises(SystemExit) as exited:
        build_parser().parse_args(["--budget-usd", given])
    assert exited.value.code == 2
    assert capsys.readouterr().err.splitlines()[-1] == (
        f"harness-tuner: error: argument --budget-usd: {given!r}:"
        " Enter an amount in USD, like 50, or none"
    )


def test_an_invalid_min_effect_names_the_valid_values(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--min-effect", "2"])
    assert capsys.readouterr().err.splitlines()[-1] == (
        "harness-tuner: error: argument --min-effect: '2':"
        " Enter a fraction above 0 and at most 1, like 0.1 for 10%"
    )


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
    example = REPO / "examples" / "model-policy.json"
    assert load_model_policy(str(example)) is not None
    bad = tmp_path / "bad.json"
    bad.write_text('{"allow": []}')
    with pytest.raises(ConfigRenderError, match="fix: "):
        load_model_policy(str(bad))

    policy = load_model_policy(str(example))
    target = target_from_args(build_parser().parse_args(ARGS))
    state = _state(tmp_path)
    run_dir = state.runs / "r"
    ctx = build_ctx(
        target,
        KilnClient("http://localhost:1"),
        state=state,
        agents_dir=_agents_dir(state),
        run_id="r",
        run_dir=run_dir,
        mcp_config_path=run_dir / "mcp_config.json",
        settings_path=run_dir / "settings.json",
        objective="speed",
        model_policy=policy,
    )
    assert ctx.objective == "speed" and ctx.model_policy is policy
    # The quarantine lands in a tree the read fence denies.
    assert ctx.quarantine_root == state.runs / ".quarantine" / (
        "424242748186-424242613304"
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
    state = _state(tmp_path)
    run_dir = state.runs / "r"
    ctx = build_ctx(
        target_from_args(args),
        KilnClient("http://localhost:1"),
        state=state,
        agents_dir=_agents_dir(state),
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
    state = _state(tmp_path)
    run_dir = state.runs / "r"

    def ctx_with(**kwargs):
        return build_ctx(
            target_from_args(build_parser().parse_args(ARGS)),
            KilnClient("http://localhost:1"),
            state=state,
            agents_dir=_agents_dir(state),
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


def test_no_interaction_parses_and_defaults_off():
    assert build_parser().parse_args([]).no_interaction is False
    assert build_parser().parse_args(["--no-interaction"]).no_interaction is True


def test_demo_project_parses_and_defaults_off():
    assert build_parser().parse_args([]).demo_project is False
    assert build_parser().parse_args(["--demo-project"]).demo_project is True


@pytest.mark.parametrize("flag", ["--project-id", "--task-id", "--project-path"])
def test_demo_project_with_a_project_flag_exits_2(monkeypatch, capsys, flag):
    started: list = []
    monkeypatch.setattr(
        kiln_harness_tuner.cli,
        "prepare_start",
        lambda args, _argv, **_kwargs: started.append(args),
    )
    with pytest.raises(SystemExit) as exited:
        main(["--demo-project", flag, "424242613304"])
    assert exited.value.code == 2
    assert capsys.readouterr().err.splitlines()[-1] == (
        "harness-tuner: error: --demo-project makes its own project:"
        " drop --project-id, --task-id and --project-path"
    )
    assert started == []


def test_the_parser_records_every_long_option():
    parser = build_parser()
    every_long_option = {
        option: action
        for action in parser._actions
        for option in action.option_strings
        if option.startswith("--")
    }
    assert sorted(parser.option_dests) == sorted(every_long_option)
    for option, action in every_long_option.items():
        assert parser.option_dests[option] == OptionInfo(
            action.dest, takes_value=action.nargs != 0, default=action.default
        )
    assert parser.option_dests["--budget-usd"] == OptionInfo("budget_usd", True, 50.0)
    assert parser.option_dests["--no-progress"] == OptionInfo(
        "no_progress", False, False
    )
    assert parser.option_dests["--agent-model"] == OptionInfo("agent_model", True, [])


def test_the_parser_is_named_by_prog():
    assert PROG == "harness-tuner"
    assert build_parser().prog == PROG


def test_main_runs_the_start_up_checks_before_the_run(monkeypatch, capsys):
    seen = {}

    def fake_prepare_start(args, argv, *, prog, option_dests, policy_loader):
        seen["prog"] = prog
        seen["argv"] = argv
        seen["option_dests"] = option_dests
        seen["policy_loader"] = policy_loader
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
    assert seen["option_dests"] == build_parser().option_dests
    # The start-up's key check loads `--model-policy` the way the run does.
    assert seen["policy_loader"] is load_model_policy
    assert seen["run"] is seen["returned"] and seen["run"] is not seen["given"]
    assert seen["run"].guidance == "filled in by the checks"
    assert "harness-tuner: run finished — report: wrap_up" in capsys.readouterr().out


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


CLOSING = "harness-tuner stopped with an error (exit 1)\n"
NOTHING_SPENT = "  Nothing was spent and no run folder was created.\n"


def test_main_prints_an_onboarding_error_and_exits_1(monkeypatch, capsys):
    def fails(args, _argv, **_kwargs):
        raise OnboardingError("Kiln isn't running at http://localhost:8757.")

    ran: list = []
    monkeypatch.setattr(kiln_harness_tuner.cli, "prepare_start", fails)
    monkeypatch.setattr(kiln_harness_tuner.cli, "_run", _never_run(ran))
    assert main(ARGS) == 1
    assert capsys.readouterr().err == (
        "harness-tuner: error: Kiln isn't running at http://localhost:8757.\n"
        + NOTHING_SPENT
        + CLOSING
    )
    assert ran == []


def test_main_prints_only_the_closing_line_for_an_error_the_guided_start_showed(
    monkeypatch, capsys
):
    def shown(args, _argv, **_kwargs):
        error = OnboardingError("Kiln has no project 999", ["Projects in Kiln:"])
        error.shown = True
        raise error

    ran: list = []
    monkeypatch.setattr(kiln_harness_tuner.cli, "prepare_start", shown)
    monkeypatch.setattr(kiln_harness_tuner.cli, "_run", _never_run(ran))
    assert main(ARGS) == 1
    assert capsys.readouterr().err == CLOSING
    assert ran == []


def test_main_prints_a_titled_error_with_its_body(monkeypatch, capsys):
    def fails(args, _argv, **_kwargs):
        raise OnboardingError("Kiln has no project 999", ["Projects in Kiln:"])

    monkeypatch.setattr(kiln_harness_tuner.cli, "prepare_start", fails)
    assert main(ARGS) == 1
    assert capsys.readouterr().err == (
        "harness-tuner: error: Kiln has no project 999\n"
        "  Projects in Kiln:\n" + NOTHING_SPENT + CLOSING
    )


CONFIG_PATH = "/api/projects/424242748186/tasks/424242613304/evals/e1/eval_config/c1"
JINJA_ERROR = KilnAPIError(
    "GET",
    CONFIG_PATH,
    500,
    {"error_messages": [": Value error, Invalid Jinja2 template: no 'x' filter"]},
)
JINJA_BODY = (
    f"  GET {CONFIG_PATH}\n  HTTP 500: Invalid Jinja2 template: no 'x' filter\n"
)


def test_main_shows_a_kiln_error_before_the_run_with_nothing_spent(monkeypatch, capsys):
    async def fails(args):
        raise JINJA_ERROR

    monkeypatch.setattr(kiln_harness_tuner.cli, "_run", fails)
    assert main(ARGS) == 1
    assert capsys.readouterr().err == (
        "harness-tuner: error: Kiln returned an error\n"
        + JINJA_BODY
        + NOTHING_SPENT
        + CLOSING
    )


def test_main_after_the_agent_cli_check_names_its_one_test_prompt(
    monkeypatch, capsys, tmp_path
):

    def locked(*_args):
        raise RunLockError("another run (pid 7) is live against task 424242613304.")

    monkeypatch.setattr(kiln_harness_tuner.cli, "_acquire_run_lock", locked)
    assert main(ARGS) == 1
    assert capsys.readouterr().err == (
        "harness-tuner: error: Another run is already using this task\n"
        "  another run (pid 7) is live against task 424242613304.\n"
        "  The only spend was the agent CLI's one test prompt."
        " No run folder was created.\n" + CLOSING
    )


@pytest.mark.parametrize("report", [True, False], ids=["report", "no report"])
def test_main_after_the_run_folder_names_spend_folder_and_report(
    monkeypatch, capsys, tmp_path, report
):
    seen = {}

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        ctx.log.event("spend", source="preflight", cost_usd=1.25)
        ctx.log.event("spend", source="describe_eval", cost_usd=1.95)
        seen["run_dir"] = ctx.run_dir
        if report:
            seen["report"] = await ctx.write_fallback(ctx, "fatal")
        raise JINJA_ERROR

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    assert main(ARGS) == 1
    report_line = f"  Report: {seen['report']}\n" if report else ""
    assert capsys.readouterr().err == (
        "harness-tuner: error: Kiln returned an error\n"
        + JINJA_BODY
        + "  The run stopped. Spent $3.20 so far"
        " (agents and the task's model calls).\n"
        f"  Run folder: {seen['run_dir']}\n" + report_line + CLOSING
    )


def test_each_run_starts_with_nothing_spent(monkeypatch, capsys, tmp_path):
    """How far one `main` got is not the next one's ending."""

    async def fails_in_the_run(ctx, guidance, timeout_mins):
        raise JINJA_ERROR

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fails_in_the_run)
    assert main(ARGS) == 1
    assert "Run folder:" in capsys.readouterr().err

    async def fails_at_once(args):
        raise JINJA_ERROR

    monkeypatch.setattr(kiln_harness_tuner.cli, "_run", fails_at_once)
    assert main(ARGS) == 1
    assert NOTHING_SPENT in capsys.readouterr().err


SETUP_ERRORS = [
    (EvalSplitAuditError("x"), "Some evals have no train or val split"),
    (
        SetupDescribeError("e1", "Tone", "rate_limit: 429", attempts=2),
        "The tuner couldn't describe an eval",
    ),
    (UnderpoweredEvalsError("x"), "No eval can show a win on val"),
    (ModelPreflightError("x"), "The task's model failed its test call"),
    (
        SetupPreconditionError("x"),
        "The task isn't set up the way the tuner needs",
    ),
    (SetupError("x"), "Setup stopped the run"),
]


TITLES = [
    (KilnTransportError("x"), "Lost the connection to Kiln"),
    (KilnAPIError("GET", "/x", 422, None), "Kiln returned an error"),
    (KilnError("x"), "The run stopped"),
    (RunLockError("x"), "Another run is already using this task"),
    (PreflightError("x"), "The tuner's agents couldn't start"),
    (ConfigRenderError("x"), "A start-up option needs fixing"),
    *SETUP_ERRORS,
    (
        BaselineRecordMissingError("x"),
        "The task has no baseline the tuner can use",
    ),
    (AnalysisError("x"), "Measuring a candidate failed"),
    (ContractPreflightError("x"), "Kiln's API isn't what the tuner expects"),
    (MemoryWriteRefused("x"), "The tuner refused an unsafe memory write"),
    (RecordKindError("x"), "An agent named the wrong kind of record"),
    (QuarantineError("x"), "The tuner couldn't tell the splits apart"),
    (TrainSetError("x"), "The train split isn't clear"),
    (OnboardingError("Kiln isn't running"), "Kiln isn't running"),
    (RuntimeError("x"), "The run stopped"),
]


@pytest.mark.parametrize(
    "error,title", TITLES, ids=[type(error).__name__ for error, _title in TITLES]
)
def test_each_error_has_its_title(error, title):
    assert title_for(error) == title


def test_the_titles_are_most_specific_first():
    # A class listed after one of its bases would never be reached.
    kinds = [kind for kind, _title in ERROR_TITLES]
    for i, kind in enumerate(kinds):
        assert not any(issubclass(kind, base) for base in kinds[:i]), kind


@pytest.mark.parametrize("exit_code", [1, 130])
def test_main_returns_the_stop_code(monkeypatch, capsys, exit_code):
    def stops(args, _argv, **_kwargs):
        raise OnboardingStopped(exit_code)

    ran: list = []
    monkeypatch.setattr(kiln_harness_tuner.cli, "prepare_start", stops)
    monkeypatch.setattr(kiln_harness_tuner.cli, "_run", _never_run(ran))
    assert main(ARGS) == exit_code
    assert capsys.readouterr().err == "harness-tuner: stopped.\n"
    assert ran == []


def test_main_runs_orchestrate_with_wired_args(capsys, monkeypatch, state_dir):
    seen = {}

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        seen["ctx"] = ctx
        seen["guidance"] = guidance
        seen["timeout_mins"] = timeout_mins
        return "wrap_up"

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    code = main(ARGS + ["--guidance", "one wave", "--timeout-mins", "30"])
    assert code == 0
    out = capsys.readouterr().out
    assert "harness-tuner: run finished — report: wrap_up" in out
    assert seen["guidance"] == "one wave"
    assert seen["timeout_mins"] == 30
    ctx = seen["ctx"]
    assert ctx.target.task_id == "424242613304"
    # Wiring: fanout log (wave log → tracker → UI) + the report hook.
    assert isinstance(ctx.log, FanoutLog)
    assert isinstance(ctx.write_report, ReportWriter)
    assert ctx.write_report.report_path.parent == state_dir / "reports"
    # The wave log landed under <state>/runs/<run_id>/, and the start line,
    # printed before the live display, named the folder.
    run_dir = _run_dir(state_dir)
    assert (run_dir / "wave_log.jsonl").exists()
    assert out.startswith(f"harness-tuner: run {run_dir.name} — files in {run_dir}/\n")
    # …and beside it the run's diagnostics file, already carrying
    # the run's opening line — the bootstrap runs before anything can fail.
    log_text = (run_dir / "kiln-harness-tuner.log").read_text()
    assert f"run {run_dir.name} starting" in log_text
    assert "project 424242748186 task 424242613304" in log_text


@pytest.mark.parametrize(
    ("flags", "display"), [([], "live"), (["--no-progress"], "plain")]
)
def test_no_progress_gives_the_plain_display_on_a_terminal(
    monkeypatch, tmp_path, flags, display
):
    built = []

    def recorded(name):
        # Each records which display the run chose, and builds the plain
        # one, so the test starts no live display thread.
        def build(tracker, console):
            built.append(name)
            return PlainUi(tracker, console=console)

        return build

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        return "wrap_up"

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    monkeypatch.setattr(kiln_harness_tuner.cli, "LiveUi", recorded("live"))
    monkeypatch.setattr(kiln_harness_tuner.cli, "PlainUi", recorded("plain"))
    monkeypatch.setattr(
        kiln_harness_tuner.cli,
        "Console",
        lambda: Console(file=io.StringIO(), force_terminal=True),
    )
    assert main(ARGS + flags) == 0
    assert built == [display]


def test_main_missing_baseline_prints_instructive_error(monkeypatch, capsys, tmp_path):

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        raise BaselineRecordMissingError(
            "no baseline record for task 424242613304 — run baseline setup"
        )

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    code = main(ARGS)
    assert code == 1
    err = capsys.readouterr().err
    assert err.startswith("harness-tuner: no baseline record")
    assert "run baseline setup" in err


def test_main_setup_precondition_error_prints_instructive_error(
    monkeypatch, capsys, tmp_path
):

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

    def target(self, tmp_path) -> TargetBlock:
        return TargetBlock(
            project_path=str(_fixture_project(tmp_path, self.TASK_ID)),
            project_id="424242748186",
            task_id=self.TASK_ID,
            kiln_host="http://localhost:9999",
        )

    def make(self, tmp_path, **kwargs):
        state = _state(tmp_path)
        run_dir = state.runs / "260731-0900"
        rendered = render_run_configs(
            run_dir,
            self.target(tmp_path),
            state=state,
            agents_dir=_agents_dir(state),
            **kwargs,
        )
        return rendered, run_dir

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
            # The tuner's own interpreter, with no `uv` and no checkout.
            assert gateway["command"] == sys.executable
            args = gateway["args"]
            assert args[:3] == ["-P", "-m", GATEWAY_MODULE]
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
        (mcp_path, _), _ = self.make(tmp_path, max_concurrent_evals=3)
        args = json.loads(mcp_path.read_text())["mcpServers"][
            "kiln_harness_tuner_gateway"
        ]["args"]
        assert args[args.index("--max-concurrent-evals") + 1] == "3"

    def test_skill_installer_server_rendered(self, tmp_path):
        # Run-scoped: the tuner's interpreter plus the run's resolved
        # project + scratch roots — in the builder's and the probe's configs.
        (_, _s), run_dir = self.make(tmp_path)
        server = json.loads(
            (run_dir / "mcp_config.create_experiment_rc.json").read_text()
        )["mcpServers"]["skill_installer"]
        assert server["type"] == "stdio"
        assert server["command"] == sys.executable
        project_root = _fixture_project(tmp_path, self.TASK_ID).resolve()
        assert server["args"] == [
            "-P",
            "-m",
            INSTALLER_MODULE,
            "--project-path",
            str(project_root),
            "--scratch-root",
            str((run_dir / "scratch").resolve()),
        ]

    def test_settings_deny_project_tree(self, tmp_path):
        # The Write-tool leak closure: the Kiln
        # project tree is deny-ruled for ALL file tools; deny beats both the
        # scratch allow and the implicit cwd grant. Scratch lives under
        # <state>/runs/, disjoint, so the allow is untouched.
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
        # hatch; the state folder (the cwd's, the agents folder's, every
        # run's) denied, scratch re-opened.
        sandbox = settings["sandbox"]
        assert sandbox["enabled"] is True
        assert sandbox["failIfUnavailable"] is True
        assert sandbox["allowUnsandboxedCommands"] is False
        assert sandbox["filesystem"]["denyWrite"] == [str(_state(tmp_path).root)]
        assert sandbox["filesystem"]["allowWrite"] == [str(scratch)]

    def test_no_role_may_edit_the_agents_folder(self, tmp_path):
        """The agents folder is the Claude sessions' working folder, which
        Claude Code lets the file tools write by default, and every later
        session reads its skills."""
        from kiln_harness_tuner.gateway.ops import ROLE_OPS

        (_, settings_path), run_dir = self.make(tmp_path)
        agents = _agents_dir(_state(tmp_path))
        rule = f"Edit(//{str(agents).lstrip('/')}/**)"
        for name in ["settings.json", "settings.readonly.json"] + [
            f"settings.{r}.json" for r in ROLE_OPS
        ]:
            settings = json.loads((run_dir / name).read_text())
            assert rule in settings["permissions"]["deny"], name

    def test_the_val_readers_may_edit_nothing_in_the_state_folder(self, tmp_path):
        (_, _s), run_dir = self.make(tmp_path)
        state = _state(tmp_path)
        for name in ("settings.readonly.json", "settings.promote_best.json"):
            deny = json.loads((run_dir / name).read_text())["permissions"]["deny"]
            assert deny[0] == f"Edit(//{str(state.root).lstrip('/')}/**)"

    def test_an_unknown_interpreter_is_refused(self, tmp_path, monkeypatch):
        from kiln_harness_tuner.cli import ConfigRenderError

        for value in ("", "python3"):
            monkeypatch.setattr(sys, "executable", value)
            with pytest.raises(ConfigRenderError) as exc:
                self.make(tmp_path)
            assert repr(value) in str(exc.value)
            assert "fix: run harness-tuner from its installed command." in str(
                exc.value
            )

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
            # The session's working folder stays off the hook's `sys.path`.
            assert hook["command"].endswith(
                f" -P -m kiln_harness_tuner.gateway.hook --role {role}"
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
        # An agent reads the agents folder's skills and CLAUDE.md, and its
        # own run scratch — never its Kiln project, other runs, traces,
        # reports or other Claude sessions' transcripts; no internet, no web
        # tools.
        (_, settings_path), run_dir = self.make(tmp_path)
        settings = json.loads(settings_path.read_text())
        fs = settings["sandbox"]["filesystem"]
        state = _state(tmp_path)
        project = _fixture_project(tmp_path, self.TASK_ID).resolve()
        # The run's own project is denied WHOLE — it holds the
        # eval inputs and results of every split.
        assert fs["denyRead"] == [
            str(project),
            str(state.runs),
            str(state.traces),
            str(state.reports),
            str(demo_fence_root().resolve()),
            str(Path.home() / ".claude" / "projects"),
        ]
        # Only the scratch of this run — not its wave log, steps or
        # snapshots, which carry val tables.
        agents = _agents_dir(state)
        assert fs["allowRead"] == [
            str((run_dir / "scratch").resolve()),
            str(agents / ".claude" / "skills"),
            str(agents / "CLAUDE.md"),
        ]
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


def test_read_fence_denies_the_runs_own_project_whole(tmp_path):
    from kiln_harness_tuner.cli import read_fence_for

    own = tmp_path / "Forum Moderation"
    (own / "tasks" / "42 - t").mkdir(parents=True)
    (own / "project.kiln").write_text("v1: 1\n")
    state = _state(tmp_path)
    agents = _agents_dir(state)
    run_dir = state.runs / "260731-0900"
    fence = read_fence_for(
        state=state,
        project_root=own,
        run_dir=run_dir,
        agents_dir=agents,
        home=tmp_path / "home",
    )
    assert fence.deny == (
        own,
        state.runs,
        state.traces,
        state.reports,
        tmp_path / "home" / ".claude" / "projects",
    )
    assert fence.allow == (
        run_dir / "scratch",
        agents / ".claude" / "skills",
        agents / "CLAUDE.md",
    )
    # The run's own project, its own task included, is denied.
    assert not any(own == a or own in a.parents for a in fence.allow)
    assert fence.rule_paths()[0] == own


def test_read_fence_denies_the_packaged_demo(tmp_path):
    """The packaged demo holds every split's labels. Once the package is
    installed it sits outside the repository, where the project scan never
    looks, so the fence names it by path."""
    from kiln_harness_tuner.cli import read_fence_for

    fence = read_fence_for(
        repo_root=tmp_path / "repo",
        project_root=tmp_path / "Kiln Projects" / "Forum Moderation",
        run_dir=tmp_path / "repo" / "runs" / "260731-0900",
        task_id="424242783061",
        home=tmp_path / "home",
    )
    assert demo_fence_root().resolve() in fence.deny
    assert demo_fence_root().resolve() in fence.rule_paths()


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


def test_main_wires_dispatcher_with_rendered_configs(monkeypatch, state_dir):
    seen = {}

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        seen["ctx"] = ctx
        return "wrap_up"

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    assert main(ARGS) == 0
    dispatch = seen["ctx"].dispatch
    run_dir = _run_dir(state_dir)
    assert dispatch._mcp_config_path == run_dir / "mcp_config.json"
    assert dispatch._settings_path == run_dir / "settings.json"
    assert dispatch._run_dir == run_dir
    assert dispatch._run_id == run_dir.name
    assert dispatch._traces_root == state_dir / "traces"
    # Both rendered files exist before any dispatch could run.
    assert (run_dir / "mcp_config.json").is_file()
    assert (run_dir / "settings.json").is_file()
    # Claude sessions run in the agents folder the run installed.
    agents = _agents_dir(StatePaths(state_dir))
    assert dispatch._cwd("w1-1") == agents
    assert (agents / "CLAUDE.md").is_file()
    assert (agents / ".claude" / "skills" / "memory" / "SKILL.md").is_file()
    assert seen["ctx"].quarantine_root == state_dir / "runs" / ".quarantine" / (
        "424242748186-424242613304"
    )


def test_a_codex_run_installs_the_codex_layout(monkeypatch, state_dir):
    seen = {}

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        seen["ctx"] = ctx
        return "wrap_up"

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    assert main([*ARGS, "--agent-cli", "codex exec"]) == 0
    agents = _agents_dir(StatePaths(state_dir), "codex")
    assert (agents / "AGENTS.md").is_file()
    assert (agents / ".agents" / "skills" / "memory" / "SKILL.md").is_file()
    assert not (state_dir / "agents" / "claude").exists()
    workspace = seen["ctx"].dispatch._cwd("w1-1")
    assert (workspace / ".agents" / "skills").resolve() == agents / ".agents" / (
        "skills"
    )


def test_main_preflight_error_prints_instructive_error(monkeypatch, capsys, tmp_path):
    async def fake_orchestrate(ctx, guidance, timeout_mins):
        raise PreflightError(
            "preflight failed — the Kiln Harness Tuner gateway MCP lane is not working"
        )

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    assert main(ARGS) == 1
    assert "gateway MCP lane" in capsys.readouterr().err


# ---- what a run prints, and where it writes ----


def test_a_handled_error_names_the_run_folder_and_the_report(
    monkeypatch, capsys, state_dir
):
    """A fatal exit says where its log is, and where the fallback report it
    wrote is."""

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        ctx.write_report.report_path.parent.mkdir(parents=True, exist_ok=True)
        ctx.write_report.report_path.write_text("# fallback\n")
        raise KilnError("Kiln answered 500 on the baseline run config")

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    assert main(ARGS) == 1
    run_dir = _run_dir(state_dir)
    report = state_dir / "reports" / f"kiln-harness-tuner-{run_dir.name}.md"
    assert capsys.readouterr().err.splitlines() == [
        "harness-tuner: Kiln answered 500 on the baseline run config",
        f"harness-tuner: run folder: {run_dir}",
        f"harness-tuner: report: {report}",
    ]


def test_a_handled_error_with_no_report_names_the_run_folder_only(
    monkeypatch, capsys, state_dir
):
    async def fake_orchestrate(ctx, guidance, timeout_mins):
        raise KilnError("no answer")

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    assert main(ARGS) == 1
    err = capsys.readouterr().err
    assert f"harness-tuner: run folder: {_run_dir(state_dir)}" in err
    assert "harness-tuner: report:" not in err


def test_an_interrupted_run_names_the_run_folder(monkeypatch, capsys, state_dir):
    import asyncio
    import os
    import signal

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(5)

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    assert main(ARGS) == 143
    err = capsys.readouterr().err
    assert "harness-tuner: interrupted (SIGTERM)" in err
    assert f"harness-tuner: run folder: {_run_dir(state_dir)}" in err


def test_a_ctrl_c_names_the_run_folder(monkeypatch, capsys, state_dir):
    """Ctrl-C reaches `main` as KeyboardInterrupt, not as the cancellation
    SIGTERM and SIGHUP give, and goes on as before after the line."""
    import asyncio
    import os
    import signal

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        os.kill(os.getpid(), signal.SIGINT)
        await asyncio.sleep(5)

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    with pytest.raises(KeyboardInterrupt):
        main(ARGS)
    err = capsys.readouterr().err
    assert f"harness-tuner: run folder: {_run_dir(state_dir)}" in err


def test_a_state_folder_that_cannot_be_used_stops_the_start(
    monkeypatch, capsys, tmp_path
):
    blocked = tmp_path / "state-file"
    blocked.write_text("a file, not a folder")
    monkeypatch.setenv("KILN_HARNESS_TUNER_HOME", str(blocked))
    started = []

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        started.append(ctx)

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    assert main(ARGS) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"harness-tuner: the state folder {blocked} cannot be used")
    assert "fix: set KILN_HARNESS_TUNER_HOME to a writable folder." in err
    # Before any run folder: there is none to name.
    assert "run folder" not in err
    assert not started


def test_a_state_folder_inside_the_project_stops_the_start(
    monkeypatch, capsys, tmp_path
):
    project = tmp_path / "Forum Moderation"
    project.mkdir()
    monkeypatch.setenv("KILN_HARNESS_TUNER_HOME", str(project / "state"))
    args = ["--project-path", str(project), "--project-id", "1", "--task-id", "2"]
    assert main(args) == 1
    assert "overlap" in capsys.readouterr().err
    # Refused before the state folder is made: nothing lands in the project.
    assert list(project.iterdir()) == []


def test_a_run_writes_nothing_where_it_is_started(monkeypatch, tmp_path, state_dir):
    """The goal of the state folder: a run started from any folder writes
    there nothing, and nothing in the home folder either."""
    cwd = tmp_path / "anywhere"
    home = tmp_path / "home"
    cwd.mkdir()
    home.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("HOME", str(home))

    async def fake_orchestrate(ctx, guidance, timeout_mins):
        return "wrap_up"

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fake_orchestrate)
    assert main(ARGS) == 0
    assert list(cwd.iterdir()) == []
    assert list(home.iterdir()) == []
    assert (_run_dir(state_dir) / "settings.json").is_file()


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
    assert args.budget_usd == 50.0
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
            with pytest.raises(
                RunLockError, match="another harness-tuner run .* one run per task"
            ):
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
        assert "harness-tuner: interrupted (SIGHUP)" in capsys.readouterr().err
