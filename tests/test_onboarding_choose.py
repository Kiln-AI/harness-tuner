"""Choosing the project and the task: the lists from Kiln, the picker, the
selection rules for each combination of flags, and the project folder."""

from pathlib import Path

import pytest
from onboarding_fakes import (
    FOLDER,
    HOST,
    PROG,
    PROJECT_ID,
    TASK_ID,
    RouteKiln,
    ScriptedTerminal,
    flags,
    kiln_routes,
    project,
    runner_for,
    task,
)

from kiln_harness_tuner.onboarding.choose import (
    ProjectRow,
    Selection,
    TaskRow,
    choose_one,
    fetch_projects,
    fetch_tasks,
    select,
)
from kiln_harness_tuner.onboarding.errors import OnboardingError, OnboardingStopped
from kiln_harness_tuner.setup import SetupPreconditionError

FORUM = project()
SUPPORT = project("118230554411", "Support Triage", "/kiln-projects/Support Triage")
COPY = project("731954460212", "Forum Moderation-1", f"{FOLDER}-1")
MODERATE = task()
SUMMARISE = task("424242100001", "Summarise threads")
TRIAGE = task("118230000001", "Triage tickets")

FORUM_ROW = ProjectRow(PROJECT_ID, "Forum Moderation", f"{FOLDER}/project.kiln")
MODERATE_ROW = TaskRow(TASK_ID, "Moderate posts")
SUMMARISE_ROW = TaskRow("424242100001", "Summarise threads")

PROJECTS_LISTING = (
    "Projects in Kiln:\n"
    "  424242975950  Forum Moderation\n"
    "  118230554411  Support Triage"
)
TASKS_LISTING = (
    "Tasks in Forum Moderation:\n"
    "  424242783061  Moderate posts\n"
    "  424242100001  Summarise threads"
)
NO_PROJECTS = (
    "Kiln has no projects. Create one in the Kiln app"
    " (http://localhost:8757/settings/create_project), or try the demo:"
    " kiln-harness-tuner --demo-project"
)
NO_TASKS = (
    "Project Forum Moderation has no tasks. Create one in Kiln:"
    " http://localhost:8757/settings/create_task/424242975950"
)


def choose(kiln: RouteKiln, terminal: ScriptedTerminal, **given) -> Selection:
    return select(
        flags(**given), terminal=terminal, run=runner_for(kiln), host=HOST, prog=PROG
    )


def two_projects() -> RouteKiln:
    return RouteKiln(kiln_routes((FORUM, [MODERATE, SUMMARISE]), (SUPPORT, [TRIAGE])))


# ---- fetch_projects and fetch_tasks ----


async def test_fetch_projects_reads_kilns_list_in_order():
    rows = [
        project(),
        {"id": "118230554411", "name": "  ", "path": ""},
        "not a row",
        {"name": "no id", "path": "/x/project.kiln"},
        {"id": "", "name": "empty id"},
        {"id": 5, "name": None},
    ]
    kiln = RouteKiln({("GET", "/api/projects"): rows})
    assert await fetch_projects(kiln) == [
        FORUM_ROW,
        ProjectRow("118230554411", "118230554411", None),
        ProjectRow("5", "5", None),
    ]


async def test_fetch_tasks_reads_the_projects_tasks():
    rows = [task(), {"id": "424242100001"}, ["odd"], {"name": "no id"}]
    kiln = RouteKiln({("GET", f"/api/projects/{PROJECT_ID}/tasks"): rows})
    assert await fetch_tasks(kiln, PROJECT_ID) == [
        MODERATE_ROW,
        TaskRow("424242100001", "424242100001"),
    ]


async def test_an_empty_body_is_no_rows():
    kiln = RouteKiln(
        {
            ("GET", "/api/projects"): None,
            ("GET", f"/api/projects/{PROJECT_ID}/tasks"): None,
        }
    )
    assert await fetch_projects(kiln) == []
    assert await fetch_tasks(kiln, PROJECT_ID) == []


async def test_a_drifted_list_raises_setup_precondition_error():
    kiln = RouteKiln(
        {
            ("GET", "/api/projects"): {"projects": []},
            ("GET", f"/api/projects/{PROJECT_ID}/tasks"): "drift",
        }
    )
    with pytest.raises(SetupPreconditionError, match="GET /api/projects did not"):
        await fetch_projects(kiln)
    with pytest.raises(SetupPreconditionError, match="/tasks did not"):
        await fetch_tasks(kiln, PROJECT_ID)


# ---- choose_one ----

ROWS = [
    TaskRow("t1", "First"),
    TaskRow("t2", "Second"),
    TaskRow("t3", "Third"),
]


def pick(rows, terminal):
    return choose_one(
        rows,
        noun="task",
        heading="Tasks in Forum Moderation",
        flag="--task-id",
        where="in Forum Moderation",
        terminal=terminal,
    )


def test_one_row_is_used_without_a_question():
    terminal = ScriptedTerminal(interactive=False)
    assert pick(ROWS[:1], terminal) == (ROWS[0], False)
    assert terminal.out == ["Using task First (t1), the only task in Forum Moderation."]
    assert terminal.prompts == []


def test_several_rows_are_numbered_and_picked_by_number():
    terminal = ScriptedTerminal(["2"])
    assert pick(ROWS, terminal) == (ROWS[1], True)
    assert terminal.out == [
        "Tasks in Forum Moderation:\n"
        "  1. First  (t1)\n"
        "  2. Second  (t2)\n"
        "  3. Third  (t3)"
    ]
    assert terminal.prompts == ["Task [1-3, x to exit]: "]


def test_an_answer_out_of_range_or_not_a_number_asks_again():
    terminal = ScriptedTerminal(["0", "4", "abc", "", "-1", "²", " 3 "])
    assert pick(ROWS, terminal) == (ROWS[2], True)
    assert len(terminal.prompts) == 7
    assert len(terminal.out) == 1  # the list is shown once


@pytest.mark.parametrize("answers", [["x"], [" X "], ["9"], []])
def test_x_or_end_of_input_stops_with_1(answers):
    with pytest.raises(OnboardingStopped) as stopped:
        pick(ROWS, ScriptedTerminal(answers))
    assert stopped.value.exit_code == 1


def test_ctrl_c_at_the_picker_stops_with_130():
    with pytest.raises(OnboardingStopped) as stopped:
        pick(ROWS, ScriptedTerminal([OnboardingStopped(130)]))
    assert stopped.value.exit_code == 130


def test_several_rows_when_not_interactive_is_an_error_that_lists_them():
    terminal = ScriptedTerminal(["1"], interactive=False)
    with pytest.raises(OnboardingError) as error:
        pick(ROWS, terminal)
    assert str(error.value) == (
        "--task-id is needed when not run interactively. Tasks in Forum Moderation:\n"
        "  t1  First\n"
        "  t2  Second\n"
        "  t3  Third"
    )
    assert terminal.prompts == [] and terminal.out == []


# ---- select: the project ----


@pytest.mark.parametrize("interactive", [True, False])
def test_no_projects_is_an_error_in_both_modes(interactive):
    kiln = RouteKiln(kiln_routes())
    with pytest.raises(OnboardingError) as error:
        choose(kiln, ScriptedTerminal(interactive=interactive))
    assert str(error.value) == NO_PROJECTS


@pytest.mark.parametrize("interactive", [True, False])
def test_the_only_project_and_task_are_used_in_both_modes(interactive):
    kiln = RouteKiln(kiln_routes((FORUM, [MODERATE])))
    terminal = ScriptedTerminal(interactive=interactive)
    selection = choose(kiln, terminal)
    assert selection == Selection(
        project=FORUM_ROW,
        task=MODERATE_ROW,
        project_path=Path(FOLDER),
        filled=True,
        task_picked=False,
    )
    assert terminal.out == [
        "Using project Forum Moderation (424242975950), the only project in Kiln.",
        "Using task Moderate posts (424242783061), the only task in Forum Moderation.",
    ]
    assert terminal.prompts == []


def test_several_projects_are_picked_then_their_tasks():
    terminal = ScriptedTerminal(["1", "2"])
    selection = choose(two_projects(), terminal)
    assert (selection.project, selection.task) == (FORUM_ROW, SUMMARISE_ROW)
    assert selection.task_picked is True
    assert terminal.out == [
        "Projects in Kiln:\n"
        "  1. Forum Moderation  (424242975950)\n"
        "  2. Support Triage  (118230554411)",
        "Tasks in Forum Moderation:\n"
        "  1. Moderate posts  (424242783061)\n"
        "  2. Summarise threads  (424242100001)",
    ]
    assert terminal.prompts == [
        "Project [1-2, x to exit]: ",
        "Task [1-2, x to exit]: ",
    ]


def test_a_picked_project_with_one_task_uses_it():
    terminal = ScriptedTerminal(["2"])
    selection = choose(two_projects(), terminal)
    assert selection.task == TaskRow("118230000001", "Triage tickets")
    assert selection.task_picked is False
    assert terminal.out[-1] == (
        "Using task Triage tickets (118230000001), the only task in Support Triage."
    )


def test_several_projects_when_not_interactive_lists_them():
    with pytest.raises(OnboardingError) as error:
        choose(two_projects(), ScriptedTerminal(interactive=False))
    assert str(error.value) == (
        f"--project-id is needed when not run interactively. {PROJECTS_LISTING}"
    )


@pytest.mark.parametrize("interactive", [True, False])
def test_an_unknown_project_id_lists_the_projects(interactive):
    terminal = ScriptedTerminal(["1"], interactive=interactive)
    with pytest.raises(OnboardingError) as error:
        choose(two_projects(), terminal, project_id="999")
    assert str(error.value) == f"Kiln has no project 999. {PROJECTS_LISTING}"
    assert terminal.prompts == []


def test_an_unknown_project_id_when_kiln_has_none():
    with pytest.raises(OnboardingError) as error:
        choose(RouteKiln(kiln_routes()), ScriptedTerminal(), project_id="999")
    assert str(error.value) == f"Kiln has no project 999. {NO_PROJECTS}"


def test_a_given_project_id_asks_only_for_the_task():
    terminal = ScriptedTerminal(["1"])
    selection = choose(two_projects(), terminal, project_id=PROJECT_ID)
    assert (selection.project, selection.task) == (FORUM_ROW, MODERATE_ROW)
    assert terminal.prompts == ["Task [1-2, x to exit]: "]


# ---- select: the task ----


@pytest.mark.parametrize("interactive", [True, False])
def test_no_tasks_is_an_error_in_both_modes(interactive):
    kiln = RouteKiln(kiln_routes((FORUM, [])))
    with pytest.raises(OnboardingError) as error:
        choose(kiln, ScriptedTerminal(interactive=interactive))
    assert str(error.value) == NO_TASKS


def test_several_tasks_when_not_interactive_lists_them():
    with pytest.raises(OnboardingError) as error:
        choose(
            two_projects(), ScriptedTerminal(interactive=False), project_id=PROJECT_ID
        )
    assert str(error.value) == (
        f"--task-id is needed when not run interactively. {TASKS_LISTING}"
    )


@pytest.mark.parametrize("interactive", [True, False])
def test_an_unknown_task_id_lists_the_projects_tasks(interactive):
    terminal = ScriptedTerminal(["1"], interactive=interactive)
    with pytest.raises(OnboardingError) as error:
        choose(two_projects(), terminal, project_id=PROJECT_ID, task_id="999")
    assert str(error.value) == (
        f"Project Forum Moderation has no task 999. {TASKS_LISTING}"
    )
    assert terminal.prompts == []


def test_an_unknown_task_id_in_a_project_with_no_tasks():
    kiln = RouteKiln(kiln_routes((FORUM, [])))
    with pytest.raises(OnboardingError) as error:
        choose(kiln, ScriptedTerminal(), project_id=PROJECT_ID, task_id="999")
    assert str(error.value) == f"Project Forum Moderation has no task 999. {NO_TASKS}"


# ---- select: a task ID alone finds its project ----


def test_a_task_id_in_one_project_finds_it():
    kiln = two_projects()
    terminal = ScriptedTerminal(interactive=False)
    selection = choose(kiln, terminal, task_id="424242100001")
    assert (selection.project, selection.task) == (FORUM_ROW, SUMMARISE_ROW)
    assert terminal.out == [
        "Using project Forum Moderation (424242975950), the only project with"
        " task 424242100001."
    ]
    # Each project's tasks are read once: the found project's are reused.
    assert sorted(kiln.paths()) == [
        "/api/projects",
        "/api/projects/118230554411/tasks",
        "/api/projects/424242975950/tasks",
    ]


def copies_of_the_demo() -> RouteKiln:
    return RouteKiln(
        kiln_routes(
            (FORUM, [MODERATE]), (SUPPORT, [TRIAGE]), (COPY, [MODERATE, SUMMARISE])
        )
    )


def test_a_task_id_in_two_projects_is_picked_among_them():
    terminal = ScriptedTerminal(["2"])
    selection = choose(copies_of_the_demo(), terminal, task_id=TASK_ID)
    assert selection.project.id == "731954460212"
    assert selection.task == MODERATE_ROW
    assert selection.task_picked is False
    assert terminal.out == [
        "Projects with task 424242783061:\n"
        "  1. Forum Moderation  (424242975950)\n"
        "  2. Forum Moderation-1  (731954460212)"
    ]


def test_a_task_id_in_two_projects_when_not_interactive_lists_those_two():
    with pytest.raises(OnboardingError) as error:
        choose(
            copies_of_the_demo(), ScriptedTerminal(interactive=False), task_id=TASK_ID
        )
    assert str(error.value) == (
        "--project-id is needed when not run interactively."
        " Projects with task 424242783061:\n"
        "  424242975950  Forum Moderation\n"
        "  731954460212  Forum Moderation-1"
    )


def test_a_task_id_in_no_project_is_an_error():
    with pytest.raises(OnboardingError) as error:
        choose(two_projects(), ScriptedTerminal(), task_id="999")
    assert str(error.value) == f"No project in Kiln has task 999. {PROJECTS_LISTING}"


# ---- select: --project-path alone finds its project ----


def test_a_project_path_finds_its_project():
    terminal = ScriptedTerminal(["2"])
    selection = choose(two_projects(), terminal, project_path=f"{FOLDER}/")
    assert selection.project == FORUM_ROW
    assert selection.task == SUMMARISE_ROW
    assert selection.project_path == Path(FOLDER)
    assert selection.filled is True


def test_a_project_path_kiln_doesnt_have_is_an_error():
    with pytest.raises(OnboardingError) as error:
        choose(two_projects(), ScriptedTerminal(), project_path="/elsewhere/Proj")
    assert str(error.value) == (
        f"--project-path /elsewhere/Proj isn't a project in Kiln. {PROJECTS_LISTING}\n"
        "Import it into Kiln first."
    )


# ---- select: the project folder ----


def test_kilns_folder_is_used_when_the_flag_is_missing():
    selection = choose(
        two_projects(), ScriptedTerminal(), project_id=PROJECT_ID, task_id=TASK_ID
    )
    assert selection.project_path == Path(FOLDER)
    assert selection.filled is True


def test_a_project_path_that_isnt_kilns_is_an_error():
    with pytest.raises(OnboardingError) as error:
        choose(
            two_projects(),
            ScriptedTerminal(),
            project_id=PROJECT_ID,
            task_id=TASK_ID,
            project_path="/a/b",
        )
    assert str(error.value) == (
        "--project-path is /a/b, but Kiln has project 424242975950 at"
        " /kiln-projects/Forum Moderation.\n"
        "fix: drop --project-path, or pass '/kiln-projects/Forum Moderation'."
    )


@pytest.mark.parametrize(
    "typed,kept",
    [
        ("Kiln Projects/Forum Moderation", "Kiln Projects/Forum Moderation"),
        # A quoted `~` reaches the CLI unexpanded; the run's disk readers
        # don't expand it, so the selection does.
        ("~/Kiln Projects/Forum Moderation", "{home}/Kiln Projects/Forum Moderation"),
    ],
    ids=["relative", "tilde"],
)
def test_a_matching_project_path_is_kept_with_a_tilde_expanded(
    typed, kept, tmp_path, monkeypatch
):
    folder = tmp_path / "Kiln Projects" / "Forum Moderation"
    folder.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    kiln = RouteKiln(kiln_routes((project(folder=str(folder)), [MODERATE])))
    selection = choose(
        kiln,
        ScriptedTerminal(),
        project_id=PROJECT_ID,
        task_id=TASK_ID,
        project_path=typed,
    )
    assert selection.project_path == Path(kept.format(home=tmp_path))
    assert selection.filled is False
    assert selection.task_picked is False


def test_a_project_kiln_has_no_folder_for_expands_a_tilde_in_the_flag(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    kiln = RouteKiln(kiln_routes((project(folder=None), [MODERATE])))
    selection = choose(
        kiln,
        ScriptedTerminal(),
        project_id=PROJECT_ID,
        task_id=TASK_ID,
        project_path="~/proj",
    )
    assert selection.project_path == tmp_path / "proj"


def test_a_project_kiln_has_no_folder_for_uses_the_flag():
    kiln = RouteKiln(kiln_routes((project(folder=None), [MODERATE])))
    selection = choose(
        kiln,
        ScriptedTerminal(),
        project_id=PROJECT_ID,
        task_id=TASK_ID,
        project_path="/anywhere",
    )
    assert selection.project_path == Path("/anywhere")


def test_a_project_kiln_has_no_folder_for_needs_the_flag():
    kiln = RouteKiln(kiln_routes((project(folder=None), [MODERATE, SUMMARISE])))
    terminal = ScriptedTerminal(["1"])
    with pytest.raises(OnboardingError) as error:
        choose(kiln, terminal)
    assert str(error.value) == (
        "Kiln has no folder for project Forum Moderation (424242975950).\n"
        "fix: pass --project-path with the project's folder."
    )
    # Found before the task is asked for.
    assert terminal.prompts == []


def test_every_flag_given_fills_nothing_and_picks_nothing():
    terminal = ScriptedTerminal(interactive=False)
    selection = choose(
        two_projects(),
        terminal,
        project_id=PROJECT_ID,
        task_id="424242100001",
        project_path=FOLDER,
    )
    assert selection == Selection(
        project=FORUM_ROW,
        task=SUMMARISE_ROW,
        project_path=Path(FOLDER),
        filled=False,
        task_picked=False,
    )
    assert terminal.out == [] and terminal.prompts == []
