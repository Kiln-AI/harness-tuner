"""Choosing the project and the task: from the flags, from the only choice,
or from a picker in a guided start. Then the project's folder, from
`--project-path` or from Kiln."""

from __future__ import annotations

import argparse
import asyncio
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.onboarding import kiln_urls
from kiln_harness_tuner.onboarding.errors import OnboardingError
from kiln_harness_tuner.onboarding.terminal import (
    Action,
    ActionKind,
    Option,
    Problem,
    Terminal,
    resolve_problem,
    tilde,
)
from kiln_harness_tuner.setup import _require_rows

if TYPE_CHECKING:  # flow.py imports this module; a runtime import would cycle.
    from kiln_harness_tuner.onboarding.flow import KilnRunner

PROJECTS_PATH = "/api/projects"
PROJECT_QUESTION = "Which project do you want to tune?"
READING_PROJECTS = "Reading projects…"
USE_DEMO = ActionKind.USE_DEMO
PICK_PROJECT = ActionKind.PICK_PROJECT
PICK_ANOTHER_PROJECT = Action("p", "Pick a different project", PICK_PROJECT)


@dataclass(frozen=True)
class ProjectRow:
    id: str
    name: str
    path: str | None  # Kiln's path to the project's `project.kiln`


@dataclass(frozen=True)
class TaskRow:
    id: str
    name: str
    description: str | None = None


Row = TypeVar("Row", ProjectRow, TaskRow)


@dataclass(frozen=True)
class Selection:
    project: ProjectRow
    task: TaskRow
    project_path: Path
    filled: bool  # any of the three flags was filled in here
    project_picked: bool  # the user chose the project from two or more
    task_picked: bool  # the user chose the task from two or more


@dataclass(frozen=True)
class _ProjectChoice:
    project: ProjectRow
    tasks: list[TaskRow] | None  # when finding the project read them
    picked: bool  # the user chose it from two or more


def _tasks_path(project_id: str) -> str:
    return f"{PROJECTS_PATH}/{project_id}/tasks"


def _text(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    return value.strip() if isinstance(value, str) else ""


def _path(row: dict[str, Any]) -> str | None:
    """Kiln's `path`, as given: a folder name may begin or end with a space."""
    value = row.get("path")
    return value if isinstance(value, str) and value.strip() else None


def _identified(url_path: str, body: Any) -> list[tuple[str, dict[str, Any]]]:
    """The rows of a list route that are dicts with an id, each with its id.
    `None` is no rows; a body that isn't a list raises
    `SetupPreconditionError`, worded as setup's own shape errors."""
    rows = [row for row in _require_rows(url_path, body) if isinstance(row, dict)]
    identified = [(str(row.get("id") or "").strip(), row) for row in rows]
    return [(row_id, row) for row_id, row in identified if row_id]


async def fetch_projects(kiln: KilnApi) -> list[ProjectRow]:
    """The projects in Kiln, in Kiln's order. The list sets each project's
    `path`; the single-project GET may not, so only the list is read."""
    body = await kiln.call("GET", PROJECTS_PATH)
    return [
        ProjectRow(row_id, _text(row, "name") or row_id, _path(row))
        for row_id, row in _identified(PROJECTS_PATH, body)
    ]


async def fetch_tasks(kiln: KilnApi, project_id: str) -> list[TaskRow]:
    """The project's tasks, in Kiln's order."""
    path = _tasks_path(project_id)
    body = await kiln.call("GET", path)
    return [
        TaskRow(row_id, _text(row, "name") or row_id, _text(row, "description") or None)
        for row_id, row in _identified(path, body)
    ]


async def _fetch_tasks_of_each(
    kiln: KilnApi, projects: Sequence[ProjectRow]
) -> list[list[TaskRow]]:
    return await asyncio.gather(*(fetch_tasks(kiln, p.id) for p in projects))


def _listing(heading: str, rows: Sequence[ProjectRow | TaskRow]) -> tuple[str, ...]:
    return (f"{heading}:", *(f"  {row.id}  {row.name}" for row in rows))


def _kiln_folder(project: ProjectRow) -> Path | None:
    """The project's folder: the parent of the `project.kiln` Kiln names."""
    return Path(project.path).expanduser().parent if project.path else None


def _option(row: ProjectRow | TaskRow) -> Option[str]:
    """A picker row: a project with its ID and folder, a task with its ID
    and, under the highlighted row, its description."""
    if isinstance(row, ProjectRow):
        folder = _kiln_folder(row)
        return Option(row.id, row.name, (row.id, tilde(folder) if folder else ""))
    return Option(row.id, row.name, (row.id,), description=row.description)


def choose_one(
    rows: Sequence[Row],
    *,
    label: str,
    question: str,
    heading: str,
    flag: str,
    where: str,
    terminal: Terminal,
) -> tuple[Row, bool]:
    """One of `rows`, which must not be empty, and whether the user picked
    it from two or more. Its step line is printed under `label`.

    One row is used without a question. With more, a guided start picks;
    otherwise it is an error that lists them, since `flag` is the only way
    to choose."""
    if len(rows) == 1:
        (row,) = rows
        _step(terminal, label, row, note=f"the only {label.lower()} {where}")
        return row, False
    if not terminal.interactive:
        raise OnboardingError(
            f"{flag} is needed when the start isn't guided", _listing(heading, rows)
        )
    chosen = terminal.pick(question, [_option(row) for row in rows])
    row = next(row for row in rows if row.id == chosen)
    _step(terminal, label, row)
    return row, True


def _step(
    terminal: Terminal, label: str, row: ProjectRow | TaskRow, *, note: str = ""
) -> None:
    """The step line of a chosen project or task: picked, the only one, or
    named by a flag."""
    text = f"{row.name} ({row.id})"
    terminal.step("ok", label, f"{text} · {note}" if note else text)


def _no_projects_lines(host: str, prog: str) -> tuple[str, ...]:
    """What to do about a Kiln with no projects, for an error message."""
    return (
        "Kiln has no projects yet. Create one in Kiln:"
        f" {kiln_urls.create_project(host)}",
        f"Or start with the demo: {prog} --demo-project",
    )


def _no_projects(host: str, prog: str, *, guided: bool) -> Problem:
    demo = (
        "the demo: a small moderation task with its evals and data already set up."
        if guided
        else f"the demo: {prog} --demo-project"
    )
    return Problem(
        "Kiln has no projects yet",
        (
            "The tuner works on a task in a Kiln project. Create one in Kiln, or"
            " start with",
            demo,
        ),
        actions=(
            Action("m", "Use the demo project", USE_DEMO),
            Action(
                "o",
                "Open Kiln to create a project",
                ActionKind.OPEN,
                url=kiln_urls.create_project(host),
            ),
        ),
        retry_first=False,
    )


def _no_tasks(host: str, project: ProjectRow) -> Problem:
    return Problem(
        f"{project.name} has no tasks yet",
        (
            "The tuner works on one task of a Kiln project, and this project has none.",
            "Create one in Kiln.",
        ),
        actions=(
            Action(
                "o",
                "Open Kiln to create a task",
                ActionKind.OPEN,
                url=kiln_urls.create_task(host, project.id),
            ),
        ),
        retry_first=False,
    )


def _projects_in_kiln(
    projects: Sequence[ProjectRow], host: str, prog: str
) -> tuple[str, ...]:
    """The projects Kiln has, or how to make one."""
    if not projects:
        return _no_projects_lines(host, prog)
    return _listing("Projects in Kiln", projects)


def _folder(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _read_projects(terminal: Terminal, run: KilnRunner) -> list[ProjectRow]:
    with terminal.busy(READING_PROJECTS):
        return run(fetch_projects)


def _choose_project(
    args: argparse.Namespace,
    *,
    terminal: Terminal,
    run: KilnRunner,
    host: str,
    prog: str,
) -> _ProjectChoice | Literal[ActionKind.USE_DEMO]:
    """The project, or USE_DEMO when the user chose the demo because Kiln
    has no projects."""
    if args.project_id is not None:
        projects = _read_projects(terminal, run)
        for project in projects:
            if project.id == args.project_id:
                _step(terminal, "Project", project)
                return _ProjectChoice(project, None, picked=False)
        raise OnboardingError(
            f"Kiln has no project {args.project_id}",
            _projects_in_kiln(projects, host, prog),
        )
    if args.project_path is not None:
        projects = _read_projects(terminal, run)
        wanted = _folder(args.project_path)
        for project in projects:
            kiln_folder = _kiln_folder(project)
            if kiln_folder is not None and kiln_folder.resolve() == wanted:
                _step(terminal, "Project", project)
                return _ProjectChoice(project, None, picked=False)
        raise OnboardingError(
            f"--project-path {wanted} isn't a project in Kiln",
            (*_projects_in_kiln(projects, host, prog), "Import it into Kiln first."),
        )
    if args.task_id is not None:
        projects = _read_projects(terminal, run)
        return _choose_project_by_task(
            args.task_id, projects, terminal, run, host, prog
        )
    return _pick_project(terminal=terminal, run=run, host=host, prog=prog)


def _pick_project(
    *, terminal: Terminal, run: KilnRunner, host: str, prog: str
) -> _ProjectChoice | Literal[ActionKind.USE_DEMO]:
    """The project when no flag names one. A Kiln with no projects is a
    problem to fix in Kiln, or a reason to use the demo."""
    projects: list[ProjectRow] = []

    def check() -> Problem | None:
        nonlocal projects
        projects = run(fetch_projects)
        if projects:
            return None
        return _no_projects(host, prog, guided=terminal.interactive)

    if resolve_problem(check, terminal, busy=READING_PROJECTS) is not None:
        return USE_DEMO  # the problem's one action besides Open
    project, picked = choose_one(
        projects,
        label="Project",
        question=PROJECT_QUESTION,
        heading="Projects in Kiln",
        flag="--project-id",
        where="in Kiln",
        terminal=terminal,
    )
    return _ProjectChoice(project, None, picked)


def _choose_project_by_task(
    task_id: str,
    projects: list[ProjectRow],
    terminal: Terminal,
    run: KilnRunner,
    host: str,
    prog: str,
) -> _ProjectChoice:
    """The project that has task `task_id`, chosen among them when several
    do: copies of one project share their task IDs."""
    with terminal.busy("Reading every project's tasks…"):
        tasks_of_each = run(lambda kiln: _fetch_tasks_of_each(kiln, projects))
    holders = [
        (project, tasks)
        for project, tasks in zip(projects, tasks_of_each, strict=True)
        if any(task.id == task_id for task in tasks)
    ]
    if not holders:
        raise OnboardingError(
            f"No project in Kiln has task {task_id}",
            _projects_in_kiln(projects, host, prog),
        )
    project, picked = choose_one(
        [project for project, _tasks in holders],
        label="Project",
        question=f"Which project with task {task_id}?",
        heading=f"Projects with task {task_id}",
        flag="--project-id",
        where=f"with task {task_id}",
        terminal=terminal,
    )
    tasks = next(tasks for holder, tasks in holders if holder is project)
    return _ProjectChoice(project, tasks, picked)


def _choose_task(
    task_id: str | None,
    found: _ProjectChoice,
    *,
    terminal: Terminal,
    run: KilnRunner,
    host: str,
) -> tuple[TaskRow, bool] | Literal[ActionKind.PICK_PROJECT]:
    """The task and whether the user picked it, or PICK_PROJECT when the
    project has no tasks and the user asked to pick another project."""
    if task_id is not None:
        return _given_task(task_id, found, terminal=terminal, run=run, host=host), False
    project = found.project
    tasks: list[TaskRow] = []

    def check() -> Problem | None:
        nonlocal tasks
        tasks = run(lambda kiln: fetch_tasks(kiln, project.id))
        return None if tasks else _no_tasks(host, project)

    # Another project is offered only when this one was picked from a list.
    extra = (PICK_ANOTHER_PROJECT,) if found.picked else ()
    busy = _reading_tasks(project)
    if resolve_problem(check, terminal, busy=busy, extra=extra) is not None:
        return PICK_PROJECT  # the only action the problem returns
    return choose_one(
        tasks,
        label="Task",
        question=f"Which task in {project.name}?",
        heading=f"Tasks in {project.name}",
        flag="--task-id",
        where="in this project",
        terminal=terminal,
    )


def _reading_tasks(project: ProjectRow) -> str:
    return f"Reading {project.name}'s tasks…"


def _given_task(
    task_id: str,
    found: _ProjectChoice,
    *,
    terminal: Terminal,
    run: KilnRunner,
    host: str,
) -> TaskRow:
    """The task `--task-id` names, which must be one of the project's."""
    project = found.project
    tasks = found.tasks
    if tasks is None:
        with terminal.busy(_reading_tasks(project)):
            tasks = run(lambda kiln: fetch_tasks(kiln, project.id))
    for task in tasks:
        if task.id == task_id:
            _step(terminal, "Task", task)
            return task
    if tasks:
        listing = _listing(f"Tasks in {project.name}", tasks)
    else:
        listing = (
            f"It has no tasks yet. Create one in Kiln:"
            f" {kiln_urls.create_task(host, project.id)}",
        )
    raise OnboardingError(f"{project.name} has no task {task_id}", listing)


def _project_path(project_path: str | None, project: ProjectRow) -> Path:
    """The folder the run uses. A given `--project-path` must be the folder
    Kiln has for the project: the read fence denies the folder it is given,
    so a wrong one would leave the real project, val and test data
    included, open to the agents."""
    kiln_folder = _kiln_folder(project)
    if project_path is None:
        if kiln_folder is None:
            raise OnboardingError(
                f"Kiln has no folder for project {project.name} ({project.id})",
                ("Pass --project-path with the project's folder.",),
            )
        return kiln_folder
    if kiln_folder is not None and _folder(project_path) != kiln_folder.resolve():
        theirs = kiln_folder.resolve()
        raise OnboardingError(
            f"--project-path isn't Kiln's folder for project {project.name}",
            (
                f"--project-path is {_folder(project_path)},",
                f"but Kiln has project {project.id} at {theirs}.",
                f"Drop --project-path, or pass {shlex.quote(str(theirs))}.",
            ),
        )
    # Expanded here: a quoted `~` reaches us unexpanded, and the readers of
    # the project's files downstream take the path as it is.
    return Path(project_path).expanduser()


def select(
    args: argparse.Namespace,
    *,
    terminal: Terminal,
    run: KilnRunner,
    host: str,
    prog: str,
    same_project: Selection | None = None,
) -> Selection | Literal[ActionKind.USE_DEMO]:
    """The project, the task and the project's folder, from
    `args.project_id`, `args.task_id` and `args.project_path` (each `None`
    when not given), asking for what is missing in a guided start.

    `same_project` keeps the project of an earlier selection, for picking
    another task in it: the project isn't read or announced again, and it
    still counts as picked from a list if it was.

    Returns USE_DEMO when Kiln has no projects and the user chose the demo;
    the caller makes it and selects again. Raises `OnboardingError` for a
    choice that can't be made: an unknown ID, nothing to choose from when
    not guided, a missing ID when not guided, or a path that isn't Kiln's."""
    found = (
        _ProjectChoice(same_project.project, None, same_project.project_picked)
        if same_project is not None
        else None
    )
    while True:
        if found is None:
            chosen_project = _choose_project(
                args, terminal=terminal, run=run, host=host, prog=prog
            )
            if chosen_project is USE_DEMO:
                return USE_DEMO
            found = chosen_project
        # Before the task: a project with no folder is an error whichever
        # task the user would pick.
        project_path = _project_path(args.project_path, found.project)
        chosen = _choose_task(
            args.task_id, found, terminal=terminal, run=run, host=host
        )
        if chosen is PICK_PROJECT:
            found = None
            continue
        task, task_picked = chosen
        return Selection(
            project=found.project,
            task=task,
            project_path=project_path,
            filled=None in (args.project_id, args.task_id, args.project_path),
            project_picked=found.picked,
            task_picked=task_picked,
        )
