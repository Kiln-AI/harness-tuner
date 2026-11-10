"""The summary and the rerun command: which flags the command line gave, the
`Ready to start` rows, and the command that runs the same start again with
no questions."""

import dataclasses
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from onboarding_fakes import PROG, PROJECT_ID, TASK_ID

from kiln_harness_tuner.cli import build_parser
from kiln_harness_tuner.onboarding.choose import ProjectRow, Selection, TaskRow
from kiln_harness_tuner.onboarding.demo import DEMO_GUIDANCE
from kiln_harness_tuner.onboarding.keys import Baseline
from kiln_harness_tuner.onboarding.questions import Answers
from kiln_harness_tuner.onboarding.summary import (
    SET_BY_START,
    given_dests,
    parser_defaults,
    rerun_command,
    summary_lines,
    summary_text,
)

OPTIONS = build_parser().option_dests
DEFAULTS = parser_defaults(OPTIONS)
FOLDER = Path("/Users/you/Kiln Projects/Forum Moderation")
SELECTION = Selection(
    project=ProjectRow(PROJECT_ID, "Forum Moderation", f"{FOLDER}/project.kiln"),
    task=TaskRow(TASK_ID, "Forum Moderation"),
    project_path=FOLDER,
    filled=True,
    project_picked=True,
    task_picked=True,
)
BASELINE = Baseline("Baseline", "deepseek_4_1_flash_1017", "Fireworks AI", "rc_1")


def answers(**changes) -> Answers:
    return dataclasses.replace(DEFAULTS, **changes)


def rerun(argv: list[str], **changes) -> str:
    return rerun_command(
        PROG,
        argv,
        option_dests=OPTIONS,
        selection=SELECTION,
        answers=answers(**changes),
    )


def tokens(command: str) -> list[str]:
    """The command's tokens, read as a POSIX shell joins its lines."""
    return shlex.split(command.replace("\\\n", " "))


# ---- which flags were given ----


@pytest.mark.parametrize(
    ("argv", "dests"),
    [
        ([], set()),
        (["--project-id", "1", "--budget-usd=5"], {"project_id", "budget_usd"}),
        (["--budget-usd", "50"], {"budget_usd"}),  # given, at its default
        (["--no-progress", "--demo-project"], {"no_progress", "demo_project"}),
        (["--task", "2", "--obj=speed"], {"task_id", "objective"}),  # abbreviated
        (["--guidance", "--task-id is a flag"], {"guidance"}),
        (["--guidance", "--task-id"], {"guidance"}),  # the value is the guidance
        (["--no-progress", "--", "--task-id", "3"], {"no_progress"}),
        (["--no"], set()),  # names several flags, so none
    ],
)
def test_given_dests_reads_the_command_line_as_argparse_does(argv, dests):
    assert given_dests(argv, OPTIONS) == dests


def test_the_defaults_are_the_parsers():
    assert parser_defaults(OPTIONS) == Answers.from_args(build_parser().parse_args([]))
    assert (DEFAULTS.budget_usd, DEFAULTS.timeout_mins) == (50.0, 720)


def test_set_by_start_names_only_real_dests():
    assert SET_BY_START <= {info.dest for info in OPTIONS.values()}


# ---- the rerun command ----


def test_the_rerun_command_has_every_answer_one_group_per_line():
    command = rerun([], guidance=DEMO_GUIDANCE)
    assert command == (
        "kiln-harness-tuner \\\n"
        "  --project-id 424242975950 --task-id 424242783061 \\\n"
        "  --project-path '/Users/you/Kiln Projects/Forum Moderation' \\\n"
        "  --objective quality \\\n"
        "  --guidance 'Improve the moderation decisions. Keep the output format.' \\\n"
        "  --agent-cli 'claude -p' \\\n"
        "  --budget-usd 50 --timeout-mins 720 \\\n"
        "  --no-interaction"
    )


def test_the_flags_the_start_sets_are_replaced_by_the_answers():
    argv = [
        "--demo-project",
        "--project-id=9",
        "--objective",
        "cost",
        "--guidance",
        "old",
        "--budget",  # abbreviated
        "5",
        "--no-interaction",
        "--model-policy",
        "old.json",
    ]
    command = rerun(argv, objective="speed", guidance="new", budget_usd=7.5)
    assert tokens(command) == [
        PROG,
        "--project-id",
        PROJECT_ID,
        "--task-id",
        TASK_ID,
        "--project-path",
        str(FOLDER),
        "--objective",
        "speed",
        "--guidance",
        "new",
        "--agent-cli",
        "claude -p",
        "--budget-usd",
        "7.5",
        "--timeout-mins",
        "720",
        "--no-interaction",
    ]


def test_the_flags_the_start_never_sets_are_kept_as_typed_on_their_own_line():
    argv = [
        "--kiln-host",
        "http://localhost:9000",
        "--agent-model",
        "builder=haiku",
        "--task-id",
        "2",
        "--no-progress",
        "--agent-model=planner=opus",
    ]
    lines = rerun(argv).splitlines()
    assert lines[-2:] == [
        "  --kiln-host http://localhost:9000 --agent-model builder=haiku"
        " --no-progress --agent-model=planner=opus \\",
        "  --no-interaction",
    ]


def test_the_rerun_command_keeps_experimental_eval_building():
    command = rerun(["--experimental-eval-building"])
    parsed = build_parser().parse_args(tokens(command)[1:])
    assert parsed.experimental_eval_building is True
    parsed = build_parser().parse_args(tokens(rerun([]))[1:])
    assert parsed.experimental_eval_building is False


def test_no_cap_and_no_guidance_are_written_out():
    lines = rerun([], budget_usd=None, guidance="").splitlines()
    assert "  --guidance '' \\" in lines
    assert "  --budget-usd none --timeout-mins 720 \\" in lines


def test_the_advanced_flags_are_passed_only_when_they_arent_the_defaults():
    command = rerun(
        [],
        model_policy="~/my policy.json",
        min_effect=0.1,
        max_concurrent_evals=0,
        noise_check=3,
    )
    assert command.splitlines()[-2] == (
        "  --model-policy '~/my policy.json' --min-effect 0.1"
        " --max-concurrent-evals 0 --noise-check 3 \\"
    )
    assert "--max-concurrent-agents" not in command


def test_the_rerun_command_parses_back_to_the_same_answers():
    changed = answers(
        objective="cost",
        guidance="Don't touch the 'format'.",
        agent_cli="codex exec -m gpt-6.1-sol",
        budget_usd=None,
        timeout_mins=95,
        model_policy="p.json",
        min_effect=0.25,
        max_concurrent_evals=2,
        max_concurrent_agents=0,
        noise_check=4,
    )
    argv = ["--kiln-host", "http://k:1", "--guidance", "old", "--no-recon"]
    command = rerun_command(
        PROG, argv, option_dests=OPTIONS, selection=SELECTION, answers=changed
    )
    parsed = build_parser().parse_args(tokens(command)[1:])
    assert Answers.from_args(parsed) == changed
    assert (parsed.project_id, parsed.task_id, parsed.project_path) == (
        PROJECT_ID,
        TASK_ID,
        str(FOLDER),
    )
    assert (parsed.kiln_host, parsed.no_recon, parsed.no_interaction) == (
        "http://k:1",
        True,
        True,
    )


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
@pytest.mark.parametrize("indent", ["", "  "])
def test_a_posix_shell_reads_the_command_as_its_tokens(indent):
    guidance = 'It\'s "quoted" $HOME,\n  on two lines'
    command = rerun_command(
        PROG,
        ["--kiln-host", "http://k:1"],
        option_dests=OPTIONS,
        selection=SELECTION,
        answers=answers(guidance=guidance),
        indent=indent,
    )
    assert command.startswith(f"{indent}{PROG} \\\n{indent}  --project-id ")
    script = (
        "printargs() { for a; do printf '%s\\0' \"$a\"; done; }\n"
        + command.replace(PROG, "printargs", 1)
    )
    printed = subprocess.run(
        ["sh", "-c", script], capture_output=True, check=True
    ).stdout.decode()
    args = printed.split("\0")[:-1]
    assert args[args.index("--guidance") + 1] == guidance
    assert args == tokens(rerun(["--kiln-host", "http://k:1"], guidance=guidance))[1:]


# ---- the summary ----


def test_the_summary_names_the_task_the_baseline_and_every_answer():
    lines = summary_lines(
        SELECTION, answers(guidance=DEMO_GUIDANCE), BASELINE, DEFAULTS
    )
    assert summary_text(lines) == (
        "Ready to start\n"
        "  Task         Forum Moderation (424242783061) in project Forum Moderation"
        " (424242975950)\n"
        '  Starts from  run config "Baseline": deepseek_4_1_flash_1017 on Fireworks'
        " AI\n"
        "  Improve      Quality\n"
        '  Guidance     "Improve the moderation decisions. Keep the output format."\n'
        "  Agents       Claude Code (claude -p) · opus\n"
        "  Limits       $50.00 on the task's model calls · 12 hours\n"
        "  Advanced     defaults"
    )


def test_with_no_baseline_the_starts_from_line_is_left_out():
    labels = [
        label for label, _text in summary_lines(SELECTION, DEFAULTS, None, DEFAULTS)
    ]
    assert labels == ["Task", "Improve", "Guidance", "Agents", "Limits", "Advanced"]


@pytest.mark.parametrize(
    ("baseline", "text"),
    [
        (Baseline("MCP", None, None, "rc_2"), 'run config "MCP"'),
        (Baseline("B", "m", None, "rc_3"), 'run config "B": m'),
    ],
)
def test_a_baseline_with_no_model_or_provider_says_what_it_has(baseline, text):
    lines = dict(summary_lines(SELECTION, DEFAULTS, baseline, DEFAULTS))
    assert lines["Starts from"] == text


def test_the_summary_lists_only_the_advanced_values_that_changed():
    changed = answers(
        model_policy="./policy.json",
        max_concurrent_evals=2,
        budget_usd=None,
        timeout_mins=90,
        objective="speed",
        agent_cli="codex exec",
    )
    lines = dict(summary_lines(SELECTION, changed, BASELINE, DEFAULTS))
    assert lines["Advanced"] == "model policy ./policy.json · 2 evals at once"
    assert lines["Limits"] == "no cap · 90 minutes"
    assert lines["Improve"] == "Speed"
    assert lines["Agents"] == "Codex (codex exec) · default model"


@pytest.mark.parametrize("agent_cli", ["claude -p", "codex exec", "opencode run"])
def test_the_limits_line_says_the_budget_is_the_task_model_calls(agent_cli):
    changed = answers(agent_cli=agent_cli, budget_usd=50.0, timeout_mins=720)
    lines = dict(summary_lines(SELECTION, changed, BASELINE, DEFAULTS))
    assert lines["Limits"] == "$50.00 on the task's model calls · 12 hours"
