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
    Asked,
    RouteKiln,
    ScriptedTerminal,
    flags,
    kiln_routes,
    project,
    responses,
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
from kiln_harness_tuner.onboarding.terminal import ActionKind, Option
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
    "Projects in Kiln:",
    "  424242975950  Forum Moderation",
    "  118230554411  Support Triage",
)
TASKS_LISTING = (
    "Tasks in Forum Moderation:",
    "  424242783061  Moderate posts",
    "  424242100001  Summarise threads",
)
NO_PROJECTS_LINES = (
    "Kiln has no projects yet. Create one in Kiln:"
    " http://localhost:8757/settings/create_project",
    "Or start with the demo: kiln-harness-tuner --demo-project",
)
CREATE_TASK = f"http://localhost:8757/settings/create_task/{PROJECT_ID}"
READING_FORUM_TASKS = "Reading Forum Moderation's tasks…"


def choose(kiln: RouteKiln, terminal: ScriptedTerminal, **given):
    return select(
        flags(**given), terminal=terminal, run=runner_for(kiln), host=HOST, prog=PROG
    )


def two_projects() -> RouteKiln:
    return RouteKiln(kiln_routes((FORUM, [MODERATE, SUMMARISE]), (SUPPORT, [TRIAGE])))


def raised(call) -> OnboardingError:
    with pytest.raises(OnboardingError) as error:
        call()
    return error.value


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


async def test_fetch_tasks_reads_the_projects_tasks_and_descriptions():
    rows = [
        {**task(), "description": " Keep or remove one post. "},
        {"id": "424242100001", "description": ""},
        ["odd"],
        {"name": "no id"},
    ]
    kiln = RouteKiln({("GET", f"/api/projects/{PROJECT_ID}/tasks"): rows})
    assert await fetch_tasks(kiln, PROJECT_ID) == [
        TaskRow(TASK_ID, "Moderate posts", "Keep or remove one post."),
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
    TaskRow("t1", "First", "The first task."),
    TaskRow("t2", "Second"),
    TaskRow("t3", "Third"),
]


def pick(rows, terminal):
    return choose_one(
        rows,
        label="Task",
        question="Which task in Forum Moderation?",
        heading="Tasks in Forum Moderation",
        flag="--task-id",
        where="in this project",
        terminal=terminal,
    )


@pytest.mark.parametrize("interactive", [True, False])
def test_one_row_is_used_without_a_question(interactive):
    terminal = ScriptedTerminal(interactive=interactive)
    assert pick(ROWS[:1], terminal) == (ROWS[0], False)
    assert terminal.steps == [
        ("ok", "Task", "First (t1) · the only task in this project")
    ]
    assert terminal.asked == []


def test_several_rows_are_picked_and_the_step_line_follows():
    terminal = ScriptedTerminal(["t2"])
    assert pick(ROWS, terminal) == (ROWS[1], True)
    assert terminal.asked == [
        Asked("pick", "Which task in Forum Moderation?", ("t1", "t2", "t3"))
    ]
    assert terminal.steps == [("ok", "Task", "Second (t2)")]


def test_a_task_option_shows_its_id_and_its_description():
    terminal = ScriptedTerminal(["t1"])
    pick(ROWS, terminal)
    assert terminal.options[0][:2] == (
        Option("t1", "First", ("t1",), description="The first task."),
        Option("t2", "Second", ("t2",)),
    )


@pytest.mark.parametrize("answers", [[], [OnboardingStopped(130)]])
def test_a_stop_at_the_picker_propagates(answers):
    with pytest.raises(OnboardingStopped):
        pick(ROWS, ScriptedTerminal(answers))


def test_several_rows_when_not_guided_is_an_error_that_lists_them():
    error = raised(lambda: pick(ROWS, ScriptedTerminal(interactive=False)))
    assert str(error) == (
        "--task-id is needed when the start isn't guided\n"
        "  Tasks in Forum Moderation:\n"
        "    t1  First\n"
        "    t2  Second\n"
        "    t3  Third"
    )


# ---- select: the project ----


def test_no_projects_offers_the_demo_first():
    kiln = RouteKiln(kiln_routes())
    terminal = ScriptedTerminal(["m"])
    assert choose(kiln, terminal) is ActionKind.USE_DEMO
    ((title, body),) = terminal.blocks
    assert title == "Kiln has no projects yet"
    assert body == (
        "The tuner works on a task in a Kiln project. Create one in Kiln, or start"
        " with",
        "the demo: a small moderation task with its evals and data already set up.",
    )
    assert terminal.menus == [("m", "o", "r", "x")]
    assert terminal.busy_texts == ["Reading projects…"]


def test_no_projects_o_opens_kiln_and_r_reads_the_list_again():
    kiln = RouteKiln(kiln_routes((FORUM, [MODERATE])))
    kiln.routes[("GET", "/api/projects")] = responses([], [FORUM])
    terminal = ScriptedTerminal(["o", "r"])
    selection = choose(kiln, terminal)
    assert selection.project == FORUM_ROW
    assert terminal.opened == ["http://localhost:8757/settings/create_project"]
    assert terminal.busy_texts[:2] == ["Reading projects…"] * 2
    assert kiln.paths().count("/api/projects") == 2


def test_no_projects_when_not_guided_names_the_demo_command_and_the_link():
    error = raised(
        lambda: choose(RouteKiln(kiln_routes()), ScriptedTerminal(interactive=False))
    )
    assert str(error) == (
        "Kiln has no projects yet\n"
        "  The tuner works on a task in a Kiln project. Create one in Kiln, or"
        " start with\n"
        "  the demo: kiln-harness-tuner --demo-project\n"
        "  Open Kiln to create a project:"
        " http://localhost:8757/settings/create_project"
    )


@pytest.mark.parametrize("interactive", [True, False])
def test_the_only_project_and_task_are_used_in_both_kinds_of_start(interactive):
    kiln = RouteKiln(kiln_routes((FORUM, [MODERATE])))
    terminal = ScriptedTerminal(interactive=interactive)
    selection = choose(kiln, terminal)
    assert selection == Selection(
        project=FORUM_ROW,
        task=MODERATE_ROW,
        project_path=Path(FOLDER),
        filled=True,
        project_picked=False,
        task_picked=False,
    )
    assert terminal.step_lines() == [
        "Project: Forum Moderation (424242975950) · the only project in Kiln",
        "Task: Moderate posts (424242783061) · the only task in this project",
    ]
    assert terminal.asked == []
    assert terminal.busy_texts == ["Reading projects…", READING_FORUM_TASKS]


def test_several_projects_are_picked_then_their_tasks():
    terminal = ScriptedTerminal([PROJECT_ID, "424242100001"])
    selection = choose(two_projects(), terminal)
    assert (selection.project, selection.task) == (FORUM_ROW, SUMMARISE_ROW)
    assert (selection.project_picked, selection.task_picked) == (True, True)
    assert terminal.asked == [
        Asked(
            "pick", "Which project do you want to tune?", (PROJECT_ID, "118230554411")
        ),
        Asked("pick", "Which task in Forum Moderation?", (TASK_ID, "424242100001")),
    ]
    assert terminal.step_lines() == [
        "Project: Forum Moderation (424242975950)",
        "Task: Summarise threads (424242100001)",
    ]


def test_a_project_option_shows_its_id_and_kilns_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    mine = project("118230554411", "Mine", str(tmp_path / "Kiln Projects" / "Mine"))
    odd = project("1", "Odd", None)
    kiln = RouteKiln(kiln_routes((FORUM, [MODERATE]), (mine, [TRIAGE]), (odd, [])))
    terminal = ScriptedTerminal([PROJECT_ID])
    choose(kiln, terminal)
    assert terminal.options[0] == (
        Option(PROJECT_ID, "Forum Moderation", (PROJECT_ID, FOLDER)),
        Option("118230554411", "Mine", ("118230554411", "~/Kiln Projects/Mine")),
        Option("1", "Odd", ("1", "")),
    )


def test_a_picked_project_with_one_task_uses_it():
    terminal = ScriptedTerminal(["118230554411"])
    selection = choose(two_projects(), terminal)
    assert selection.task == TaskRow("118230000001", "Triage tickets")
    assert selection.task_picked is False
    assert terminal.step_lines()[-1] == (
        "Task: Triage tickets (118230000001) · the only task in this project"
    )


def test_several_projects_when_not_guided_lists_them():
    error = raised(lambda: choose(two_projects(), ScriptedTerminal(interactive=False)))
    assert (error.title, error.body) == (
        "--project-id is needed when the start isn't guided",
        PROJECTS_LISTING,
    )


@pytest.mark.parametrize("interactive", [True, False])
def test_an_unknown_project_id_lists_the_projects(interactive):
    terminal = ScriptedTerminal(interactive=interactive)
    error = raised(lambda: choose(two_projects(), terminal, project_id="999"))
    assert (error.title, error.body) == ("Kiln has no project 999", PROJECTS_LISTING)
    assert terminal.asked == []


def test_an_unknown_project_id_when_kiln_has_none():
    error = raised(
        lambda: choose(RouteKiln(kiln_routes()), ScriptedTerminal(), project_id="999")
    )
    assert (error.title, error.body) == ("Kiln has no project 999", NO_PROJECTS_LINES)


def test_a_given_project_id_asks_only_for_the_task():
    terminal = ScriptedTerminal([TASK_ID])
    selection = choose(two_projects(), terminal, project_id=PROJECT_ID)
    assert (selection.project, selection.task) == (FORUM_ROW, MODERATE_ROW)
    assert terminal.questions == ["Which task in Forum Moderation?"]
    assert terminal.step_lines() == [
        "Project: Forum Moderation (424242975950)",
        "Task: Moderate posts (424242783061)",
    ]


def test_the_same_project_is_kept_without_reading_or_announcing_it_again():
    kiln = two_projects()
    first = choose(kiln, ScriptedTerminal([PROJECT_ID, TASK_ID]))
    terminal = ScriptedTerminal(["424242100001"])
    selection = select(
        flags(),
        terminal=terminal,
        run=runner_for(kiln),
        host=HOST,
        prog=PROG,
        same_project=first,
    )
    assert (selection.project, selection.task) == (FORUM_ROW, SUMMARISE_ROW)
    assert (selection.project_picked, selection.task_picked) == (True, True)
    assert terminal.questions == ["Which task in Forum Moderation?"]
    assert terminal.step_lines() == ["Task: Summarise threads (424242100001)"]
    assert kiln.paths().count("/api/projects") == 1  # the first selection's


def test_the_same_project_with_no_tasks_left_still_offers_another_project():
    kiln = two_projects()
    first = choose(kiln, ScriptedTerminal([PROJECT_ID, TASK_ID]))
    kiln.routes[("GET", f"/api/projects/{PROJECT_ID}/tasks")] = []
    terminal = ScriptedTerminal(["p", "118230554411"])
    selection = select(
        flags(),
        terminal=terminal,
        run=runner_for(kiln),
        host=HOST,
        prog=PROG,
        same_project=first,
    )
    assert selection.project.id == "118230554411"
    assert terminal.menus == [("o", "r", "p", "x")]
    assert terminal.questions == ["What next?", "Which project do you want to tune?"]


# ---- select: the task ----


def test_no_tasks_offers_to_create_one_and_checks_again():
    kiln = RouteKiln(kiln_routes((FORUM, [MODERATE])))
    kiln.routes[("GET", f"/api/projects/{PROJECT_ID}/tasks")] = responses([], [task()])
    terminal = ScriptedTerminal(["o", "r"])
    selection = choose(kiln, terminal)
    assert selection.task == MODERATE_ROW
    ((title, body),) = terminal.blocks
    assert title == "Forum Moderation has no tasks yet"
    assert body == (
        "The tuner works on one task of a Kiln project, and this project has none.",
        "Create one in Kiln.",
    )
    # The only project, so there is no other project to pick.
    assert terminal.menus == [("o", "r", "x")] * 2
    assert terminal.opened == [CREATE_TASK]
    assert terminal.busy_texts.count(READING_FORUM_TASKS) == 2


def test_no_tasks_in_a_picked_project_offers_another_project():
    kiln = RouteKiln(kiln_routes((FORUM, []), (SUPPORT, [TRIAGE])))
    terminal = ScriptedTerminal([PROJECT_ID, "p", "118230554411"])
    selection = choose(kiln, terminal)
    assert selection.project.id == "118230554411"
    assert terminal.menus == [("o", "r", "p", "x")]
    assert terminal.questions == [
        "Which project do you want to tune?",
        "What next?",
        "Which project do you want to tune?",
    ]
    assert terminal.step_lines() == [
        "Project: Forum Moderation (424242975950)",
        "Project: Support Triage (118230554411)",
        "Task: Triage tickets (118230000001) · the only task in this project",
    ]


def test_no_tasks_when_not_guided_fails_with_the_link():
    kiln = RouteKiln(kiln_routes((FORUM, [])))
    error = raised(lambda: choose(kiln, ScriptedTerminal(interactive=False)))
    assert error.title == "Forum Moderation has no tasks yet"
    assert error.body[-1] == f"Open Kiln to create a task: {CREATE_TASK}"


def test_several_tasks_when_not_guided_lists_them():
    error = raised(
        lambda: choose(
            two_projects(), ScriptedTerminal(interactive=False), project_id=PROJECT_ID
        )
    )
    assert (error.title, error.body) == (
        "--task-id is needed when the start isn't guided",
        TASKS_LISTING,
    )


@pytest.mark.parametrize("interactive", [True, False])
def test_an_unknown_task_id_lists_the_projects_tasks(interactive):
    terminal = ScriptedTerminal(interactive=interactive)
    error = raised(
        lambda: choose(two_projects(), terminal, project_id=PROJECT_ID, task_id="999")
    )
    assert (error.title, error.body) == (
        "Forum Moderation has no task 999",
        TASKS_LISTING,
    )
    assert terminal.asked == []


def test_an_unknown_task_id_in_a_project_with_no_tasks():
    kiln = RouteKiln(kiln_routes((FORUM, [])))
    error = raised(
        lambda: choose(kiln, ScriptedTerminal(), project_id=PROJECT_ID, task_id="999")
    )
    assert (error.title, error.body) == (
        "Forum Moderation has no task 999",
        (f"It has no tasks yet. Create one in Kiln: {CREATE_TASK}",),
    )


# ---- select: a task ID alone finds its project ----


def test_a_task_id_in_one_project_finds_it():
    kiln = two_projects()
    terminal = ScriptedTerminal(interactive=False)
    selection = choose(kiln, terminal, task_id="424242100001")
    assert (selection.project, selection.task) == (FORUM_ROW, SUMMARISE_ROW)
    assert terminal.step_lines() == [
        "Project: Forum Moderation (424242975950) · the only project with"
        " task 424242100001",
        "Task: Summarise threads (424242100001)",
    ]
    assert terminal.busy_texts == [
        "Reading projects…",
        "Reading every project's tasks…",
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
    terminal = ScriptedTerminal(["731954460212"])
    selection = choose(copies_of_the_demo(), terminal, task_id=TASK_ID)
    assert selection.project.id == "731954460212"
    assert selection.task == MODERATE_ROW
    assert selection.task_picked is False
    assert terminal.asked == [
        Asked(
            "pick",
            "Which project with task 424242783061?",
            (PROJECT_ID, "731954460212"),
        )
    ]


def test_a_task_id_in_two_projects_when_not_guided_lists_those_two():
    error = raised(
        lambda: choose(
            copies_of_the_demo(), ScriptedTerminal(interactive=False), task_id=TASK_ID
        )
    )
    assert (error.title, error.body) == (
        "--project-id is needed when the start isn't guided",
        (
            "Projects with task 424242783061:",
            "  424242975950  Forum Moderation",
            "  731954460212  Forum Moderation-1",
        ),
    )


def test_a_task_id_in_no_project_is_an_error():
    error = raised(lambda: choose(two_projects(), ScriptedTerminal(), task_id="999"))
    assert (error.title, error.body) == (
        "No project in Kiln has task 999",
        PROJECTS_LISTING,
    )


# ---- select: --project-path alone finds its project ----


def test_a_project_path_finds_its_project():
    terminal = ScriptedTerminal(["424242100001"])
    selection = choose(two_projects(), terminal, project_path=f"{FOLDER}/")
    assert selection.project == FORUM_ROW
    assert selection.task == SUMMARISE_ROW
    assert selection.project_path == Path(FOLDER)
    assert selection.filled is True
    assert terminal.step_lines()[0] == "Project: Forum Moderation (424242975950)"


def test_a_project_path_kiln_doesnt_have_is_an_error():
    error = raised(
        lambda: choose(two_projects(), ScriptedTerminal(), project_path="/elsewhere/P")
    )
    assert (error.title, error.body) == (
        "--project-path /elsewhere/P isn't a project in Kiln",
        (*PROJECTS_LISTING, "Import it into Kiln first."),
    )


# ---- select: the project folder ----


def test_kilns_folder_is_used_when_the_flag_is_missing():
    selection = choose(
        two_projects(), ScriptedTerminal(), project_id=PROJECT_ID, task_id=TASK_ID
    )
    assert selection.project_path == Path(FOLDER)
    assert selection.filled is True


def test_a_project_path_that_isnt_kilns_is_an_error():
    error = raised(
        lambda: choose(
            two_projects(),
            ScriptedTerminal(),
            project_id=PROJECT_ID,
            task_id=TASK_ID,
            project_path="/a/b",
        )
    )
    assert str(error) == (
        "--project-path isn't Kiln's folder for project Forum Moderation\n"
        "  --project-path is /a/b,\n"
        "  but Kiln has project 424242975950 at /kiln-projects/Forum Moderation.\n"
        "  Drop --project-path, or pass '/kiln-projects/Forum Moderation'."
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
    terminal = ScriptedTerminal()
    error = raised(lambda: choose(kiln, terminal))
    assert (error.title, error.body) == (
        "Kiln has no folder for project Forum Moderation (424242975950)",
        ("Pass --project-path with the project's folder.",),
    )
    # Found before the task is asked for.
    assert terminal.asked == []


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
        project_picked=False,
        task_picked=False,
    )
    assert terminal.step_lines() == [
        "Project: Forum Moderation (424242975950)",
        "Task: Summarise threads (424242100001)",
    ]
    assert terminal.asked == []
