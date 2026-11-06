"""The rerun command: the command line as typed, with the chosen project,
task and folder in place of whatever the user gave for them."""

import shlex

import pytest
from onboarding_fakes import PROG

from kiln_harness_tuner.cli import build_parser
from kiln_harness_tuner.onboarding.flow import rerun_command

# The real parser's options, plus `--demo-project`, which phase 4 adds.
LONG_OPTIONS = [*build_parser().long_options, "--demo-project"]
CHOSEN = [
    "--project-path",
    "/Users/me/Kiln Projects/Forum Moderation",
    "--project-id",
    "424242975950",
    "--task-id",
    "424242783061",
]


def rerun(argv: list[str], long_options: list[str] = LONG_OPTIONS) -> list[str]:
    command = rerun_command(
        PROG,
        argv,
        parser_long_options=long_options,
        project_path="/Users/me/Kiln Projects/Forum Moderation",
        project_id="424242975950",
        task_id="424242783061",
    )
    return shlex.split(command)


def test_the_chosen_flags_come_first_after_prog():
    assert rerun([]) == [PROG, *CHOSEN]


@pytest.mark.parametrize(
    "given",
    [
        ["--project-id", "1", "--task-id", "2", "--project-path", "/old"],
        ["--project-id=1", "--task-id=2", "--project-path=/old"],
        ["--task-id", "2", "--project-id=1"],
        ["--project-path="],
    ],
    ids=["spaced", "equals", "mixed", "empty value"],
)
def test_given_project_flags_are_replaced(given):
    assert rerun([*given, "--no-ui"]) == [PROG, *CHOSEN, "--no-ui"]


@pytest.mark.parametrize(
    "given",
    [["--project-i", "1"], ["--task=2"], ["--project-p", "/old"], ["--demo"]],
)
def test_abbreviations_are_read_like_argparse(given):
    assert rerun([*given, "--no-ui"]) == [PROG, *CHOSEN, "--no-ui"]


def test_an_ambiguous_prefix_names_no_flag_and_is_kept():
    # argparse refuses `--project` (it starts both --project-id and
    # --project-path), so it never reaches here; if it did, it would stay.
    assert rerun(["--project", "--no-ui"]) == [PROG, *CHOSEN, "--project", "--no-ui"]


def test_demo_project_is_dropped_and_takes_no_value():
    assert rerun(["--demo-project", "--no-ui"]) == [PROG, *CHOSEN, "--no-ui"]


def test_other_flags_are_kept_in_order():
    argv = [
        "--guidance",
        "Keep the output format.",
        "--agent-model",
        "builder=haiku",
        "--task-id",
        "2",
        "--agent-model=planner=opus",
        "--budget-usd",
        "5",
    ]
    assert rerun(argv) == [
        PROG,
        *CHOSEN,
        "--guidance",
        "Keep the output format.",
        "--agent-model",
        "builder=haiku",
        "--agent-model=planner=opus",
        "--budget-usd",
        "5",
    ]


def test_a_value_that_looks_like_a_flag_but_has_a_space_is_kept():
    # argparse reads a token with a space as a value, never as an option.
    argv = ["--guidance", "--task-id is fine here"]
    assert rerun(argv) == [PROG, *CHOSEN, *argv]


def test_the_command_is_shell_quoted():
    command = rerun_command(
        PROG,
        ["--guidance", "Don't stop"],
        parser_long_options=LONG_OPTIONS,
        project_path="/Users/me/Kiln Projects/Forum Moderation",
        project_id="424242975950",
        task_id="424242783061",
    )
    assert command == (
        "kiln-harness-tuner --project-path '/Users/me/Kiln Projects/Forum Moderation'"
        " --project-id 424242975950 --task-id 424242783061"
        " --guidance 'Don'\"'\"'t stop'"
    )
    assert shlex.split(command)[-1] == "Don't stop"


def test_the_command_parses_back_to_the_same_run():
    argv = [
        "--task=2",
        "--guidance",
        "--project-id is a flag",
        "--budget-usd=5",
        "--project-p",
        "/old",
        "--agent-model",
        "builder=haiku",
    ]
    typed = vars(build_parser().parse_args(argv))
    again = vars(build_parser().parse_args(rerun(argv)[1:]))
    assert again == {
        **typed,
        "project_path": "/Users/me/Kiln Projects/Forum Moderation",
        "project_id": "424242975950",
        "task_id": "424242783061",
    }


def test_scanning_stops_at_a_double_dash():
    argv = ["--task-id", "2", "--", "--task-id", "3"]
    assert rerun(argv) == [PROG, *CHOSEN, "--", "--task-id", "3"]
