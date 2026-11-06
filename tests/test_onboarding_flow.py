"""`prepare_start` end to end on a fake Kiln: the Kiln check, the project and
task pickers, the rerun line and the readiness check, in that order."""

import pytest
from onboarding_fakes import (
    FOLDER,
    PROG,
    PROJECT_ID,
    TASK_ID,
    RouteKiln,
    ScriptedTerminal,
    kiln_routes,
    project,
    responses,
    runner_for,
    task,
    task_base,
)

from kiln_harness_tuner.cli import build_parser
from kiln_harness_tuner.onboarding.errors import OnboardingError, OnboardingStopped
from kiln_harness_tuner.onboarding.flow import prepare_start

SUPPORT = project("118230554411", "Support Triage", "/kiln-projects/Support Triage")
SUMMARISE = task("424242100001", "Summarise threads")
RERUN_HEADING = "To run this again without the questions:"
READINESS_PROMPT = "[r]etry  [o]pen the first link  [t]ask: pick another  e[x]it: "


def two_projects(**task_reads) -> RouteKiln:
    """Two projects; the first has two tasks. `task_reads` replaces the
    readiness reads of the demo's task, keyed by route suffix."""
    routes = kiln_routes(
        (project(), [task(), SUMMARISE]),
        (SUPPORT, [task("118230000001", "Triage tickets")]),
    )
    for suffix, body in task_reads.items():
        routes[("GET", f"{task_base(PROJECT_ID, TASK_ID)}/{suffix}")] = body
    return RouteKiln(routes)


def start(kiln: RouteKiln, terminal: ScriptedTerminal, argv: list[str]):
    parser = build_parser()
    return prepare_start(
        parser.parse_args(argv),
        argv,
        prog=PROG,
        parser_long_options=parser.long_options,
        terminal=terminal,
        runner=runner_for(kiln),
    )


def rerun_lines(terminal: ScriptedTerminal) -> list[str]:
    return [line for line in terminal.out if line.startswith(RERUN_HEADING)]


def test_no_flags_walks_the_kiln_check_the_pickers_and_readiness():
    kiln = two_projects()
    terminal = ScriptedTerminal(["1", "1"])
    args = start(kiln, terminal, ["--guidance", "Keep the output format."])
    assert (args.project_id, args.task_id, args.project_path) == (
        PROJECT_ID,
        TASK_ID,
        FOLDER,
    )
    assert args.guidance == "Keep the output format."
    assert terminal.out[-1] == (
        f"{RERUN_HEADING}\n"
        "  kiln-harness-tuner --project-path '/kiln-projects/Forum Moderation'"
        " --project-id 424242975950 --task-id 424242783061"
        " --guidance 'Keep the output format.'"
    )
    assert len(rerun_lines(terminal)) == 1
    assert terminal.prompts == [
        "Project [1-2, x to exit]: ",
        "Task [1-2, x to exit]: ",
    ]
    # The Kiln check, then the lists, then readiness last.
    assert kiln.paths()[:4] == [
        "/ping",
        "/openapi.json",
        "/api/projects",
        f"/api/projects/{PROJECT_ID}/tasks",
    ]
    assert sorted(kiln.paths()[4:]) == [
        f"{task_base(PROJECT_ID, TASK_ID)}/{read}"
        for read in ("evals", "run_configs", "specs")
    ]


def test_every_flag_given_prints_no_rerun_line_and_asks_nothing():
    terminal = ScriptedTerminal(interactive=False)
    argv = ["--project-id", PROJECT_ID, "--task-id", TASK_ID, "--project-path", FOLDER]
    args = start(two_projects(), terminal, argv)
    assert (args.project_id, args.task_id, args.project_path) == (
        PROJECT_ID,
        TASK_ID,
        FOLDER,
    )
    assert terminal.out == [] and terminal.prompts == []


def test_a_single_project_and_task_are_used_and_the_rerun_line_is_printed():
    kiln = RouteKiln(kiln_routes((project(), [task()])))
    terminal = ScriptedTerminal(interactive=False)
    args = start(kiln, terminal, [])
    assert (args.project_id, args.task_id) == (PROJECT_ID, TASK_ID)
    assert terminal.out == [
        "Using project Forum Moderation (424242975950), the only project in Kiln.",
        "Using task Moderate posts (424242783061), the only task in Forum Moderation.",
        f"{RERUN_HEADING}\n"
        "  kiln-harness-tuner --project-path '/kiln-projects/Forum Moderation'"
        " --project-id 424242975950 --task-id 424242783061",
    ]


def test_a_task_that_isnt_ready_retries_until_it_is():
    kiln = two_projects(run_configs=responses([], [{"id": "rc_1"}]))
    terminal = ScriptedTerminal(["r"])
    argv = ["--project-id", PROJECT_ID, "--task-id", TASK_ID]
    args = start(kiln, terminal, argv)
    assert args.task_id == TASK_ID
    (message,) = [line for line in terminal.out if "isn't ready" in line]
    assert message.startswith(
        "Task Moderate posts (424242783061) isn't ready to tune:\n"
        "  • It has no run configs."
    )
    # The task came from a flag, so there is no other task to pick.
    assert terminal.prompts == ["[r]etry  [o]pen the first link  e[x]it: "]
    # The rerun line (the path was filled in) comes before the check.
    assert terminal.out.index(rerun_lines(terminal)[0]) < terminal.out.index(message)


def test_t_goes_back_to_the_task_picker():
    kiln = two_projects(evals=[])
    terminal = ScriptedTerminal(["1", "1", "t", "2"])
    args = start(kiln, terminal, [])
    assert (args.project_id, args.task_id) == (PROJECT_ID, "424242100001")
    assert terminal.prompts == [
        "Project [1-2, x to exit]: ",
        "Task [1-2, x to exit]: ",
        READINESS_PROMPT,
        "Task [1-2, x to exit]: ",
    ]
    first, second = rerun_lines(terminal)
    assert "--task-id 424242783061" in first
    assert "--task-id 424242100001" in second
    assert "--project-id 424242975950" in second


def test_t_is_not_offered_for_a_task_that_wasnt_picked():
    kiln = RouteKiln(kiln_routes((project(), [task()])))
    kiln.routes[("GET", f"{task_base(PROJECT_ID, TASK_ID)}/evals")] = []
    terminal = ScriptedTerminal(["t", "x"])
    with pytest.raises(OnboardingStopped) as stopped:
        start(kiln, terminal, [])
    assert stopped.value.exit_code == 1
    assert terminal.prompts == ["[r]etry  [o]pen the first link  e[x]it: "] * 2


def test_open_opens_the_first_link():
    kiln = two_projects(run_configs=responses([], [{"id": "rc_1"}]))
    terminal = ScriptedTerminal(["o", "r"])
    start(kiln, terminal, ["--project-id", PROJECT_ID, "--task-id", TASK_ID])
    assert terminal.opened == [f"http://localhost:8757/optimize/{PROJECT_ID}/{TASK_ID}"]


def test_not_ready_and_not_interactive_fails_with_the_message():
    terminal = ScriptedTerminal(interactive=False)
    with pytest.raises(OnboardingError) as error:
        start(
            two_projects(evals=[]),
            terminal,
            [
                "--project-id",
                PROJECT_ID,
                "--task-id",
                TASK_ID,
                "--project-path",
                FOLDER,
            ],
        )
    assert str(error.value) == (
        "Task Moderate posts (424242783061) isn't ready to tune:\n"
        "  • It has no eval the loop can measure. Add one in Kiln:"
        f" http://localhost:8757/specs/{PROJECT_ID}/{TASK_ID}"
    )
    assert terminal.prompts == []


def test_the_readiness_warning_is_printed_each_time_the_check_runs():
    kiln = two_projects(
        evals=responses(
            {"evals": [], "load_error_count": 1},
            {"evals": [{"id": "e", "current_config_id": "ec"}], "load_error_count": 1},
        )
    )
    terminal = ScriptedTerminal(["r"])
    start(kiln, terminal, ["--project-id", PROJECT_ID, "--task-id", TASK_ID])
    assert (
        terminal.err
        == [
            "Kiln couldn't load 1 eval of task Moderate posts, so the run can't"
            " measure it."
        ]
        * 2
    )


def test_ctrl_c_at_the_picker_stops_with_130():
    with pytest.raises(OnboardingStopped) as stopped:
        start(two_projects(), ScriptedTerminal([KeyboardInterrupt()]), [])
    assert stopped.value.exit_code == 130
