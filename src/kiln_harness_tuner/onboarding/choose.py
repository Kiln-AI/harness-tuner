"""Choosing the project and the task: from the flags, from the only choice,
or from a numbered list in the terminal. Then the project's folder, from
`--project-path` or from Kiln."""

from __future__ import annotations

import argparse
import asyncio
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.onboarding import kiln_urls
from kiln_harness_tuner.onboarding.errors import OnboardingError, OnboardingStopped
from kiln_harness_tuner.onboarding.terminal import Terminal
from kiln_harness_tuner.setup import _require_rows

if TYPE_CHECKING:  # flow.py imports this module; a runtime import would cycle.
    from kiln_harness_tuner.onboarding.flow import KilnRunner

PROJECTS_PATH = "/api/projects"


@dataclass(frozen=True)
class ProjectRow:
    id: str
    name: str
    path: str | None  # Kiln's path to the project's `project.kiln`


@dataclass(frozen=True)
class TaskRow:
    id: str
    name: str


Row = TypeVar("Row", ProjectRow, TaskRow)


@dataclass(frozen=True)
class Selection:
    project: ProjectRow
    task: TaskRow
    project_path: Path
    filled: bool  # any of the three flags was filled in here
    task_picked: bool  # the user chose the task from two or more


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
        TaskRow(row_id, _text(row, "name") or row_id)
        for row_id, row in _identified(path, body)
    ]


async def _fetch_tasks_of_each(
    kiln: KilnApi, projects: Sequence[ProjectRow]
) -> list[list[TaskRow]]:
    return await asyncio.gather(*(fetch_tasks(kiln, p.id) for p in projects))


def _listing(heading: str, rows: Sequence[ProjectRow | TaskRow]) -> str:
    return f"{heading}:\n" + "\n".join(f"  {row.id}  {row.name}" for row in rows)


def choose_one(
    rows: Sequence[Row],
    *,
    noun: str,
    heading: str,
    flag: str,
    where: str,
    terminal: Terminal,
) -> tuple[Row, bool]:
    """One of `rows`, which must not be empty, and whether the user picked
    it from two or more.

    One row is used without a question. With more, an interactive user
    picks by number; otherwise it is an error that lists them, since `flag`
    is the only way to choose."""
    if len(rows) == 1:
        (row,) = rows
        terminal.say(f"Using {noun} {row.name} ({row.id}), the only {noun} {where}.")
        return row, False
    if not terminal.interactive:
        raise OnboardingError(
            f"{flag} is needed when not run interactively. {_listing(heading, rows)}"
        )
    numbered = "\n".join(
        f"  {n}. {row.name}  ({row.id})" for n, row in enumerate(rows, start=1)
    )
    terminal.say(f"{heading}:\n{numbered}")
    prompt = f"{noun.capitalize()} [1-{len(rows)}, x to exit]: "
    while True:
        answer = terminal.ask(prompt).strip().lower()
        if answer == "x":
            raise OnboardingStopped(1)
        if answer.isdecimal() and 1 <= int(answer) <= len(rows):
            return rows[int(answer) - 1], True


def _no_projects(host: str, prog: str) -> str:
    return (
        "Kiln has no projects. Create one in the Kiln app"
        f" ({kiln_urls.create_project(host)}), or try the demo:"
        f" {prog} --demo-project"
    )


def _no_tasks(host: str, project: ProjectRow) -> str:
    return (
        f"Project {project.name} has no tasks. Create one in Kiln:"
        f" {kiln_urls.create_task(host, project.id)}"
    )


def _with_projects(
    headline: str, projects: Sequence[ProjectRow], host: str, prog: str
) -> str:
    """`headline`, then the projects Kiln has, or how to make one."""
    if not projects:
        return f"{headline} {_no_projects(host, prog)}"
    return f"{headline} {_listing('Projects in Kiln', projects)}"


def _folder(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _kiln_folder(project: ProjectRow) -> Path | None:
    """The project's folder: the parent of the `project.kiln` Kiln names."""
    return Path(project.path).expanduser().parent if project.path else None


def _choose_project(
    args: argparse.Namespace,
    *,
    terminal: Terminal,
    run: KilnRunner,
    host: str,
    prog: str,
) -> tuple[ProjectRow, list[TaskRow] | None]:
    """The project, and its tasks when finding the project read them."""
    projects = run(fetch_projects)
    if args.project_id is not None:
        for project in projects:
            if project.id == args.project_id:
                return project, None
        raise OnboardingError(
            _with_projects(
                f"Kiln has no project {args.project_id}.", projects, host, prog
            )
        )
    if args.project_path is not None:
        wanted = _folder(args.project_path)
        for project in projects:
            kiln_folder = _kiln_folder(project)
            if kiln_folder is not None and kiln_folder.resolve() == wanted:
                return project, None
        headline = f"--project-path {wanted} isn't a project in Kiln."
        raise OnboardingError(
            _with_projects(headline, projects, host, prog)
            + "\nImport it into Kiln first."
        )
    if args.task_id is not None:
        return _choose_project_by_task(
            args.task_id, projects, terminal, run, host, prog
        )
    if not projects:
        raise OnboardingError(_no_projects(host, prog))
    project, _picked = choose_one(
        projects,
        noun="project",
        heading="Projects in Kiln",
        flag="--project-id",
        where="in Kiln",
        terminal=terminal,
    )
    return project, None


def _choose_project_by_task(
    task_id: str,
    projects: list[ProjectRow],
    terminal: Terminal,
    run: KilnRunner,
    host: str,
    prog: str,
) -> tuple[ProjectRow, list[TaskRow]]:
    """The project that has task `task_id`, chosen among them when several
    do: copies of one project share their task IDs."""
    tasks_of_each = run(lambda kiln: _fetch_tasks_of_each(kiln, projects))
    holders = [
        (project, tasks)
        for project, tasks in zip(projects, tasks_of_each, strict=True)
        if any(task.id == task_id for task in tasks)
    ]
    if not holders:
        raise OnboardingError(
            _with_projects(
                f"No project in Kiln has task {task_id}.", projects, host, prog
            )
        )
    project, _picked = choose_one(
        [project for project, _tasks in holders],
        noun="project",
        heading=f"Projects with task {task_id}",
        flag="--project-id",
        where=f"with task {task_id}",
        terminal=terminal,
    )
    tasks = next(tasks for holder, tasks in holders if holder is project)
    return project, tasks


def _choose_task(
    task_id: str | None,
    project: ProjectRow,
    tasks: list[TaskRow],
    terminal: Terminal,
    host: str,
) -> tuple[TaskRow, bool]:
    if task_id is not None:
        for task in tasks:
            if task.id == task_id:
                return task, False
        headline = f"Project {project.name} has no task {task_id}."
        if not tasks:
            raise OnboardingError(f"{headline} {_no_tasks(host, project)}")
        raise OnboardingError(
            f"{headline} {_listing(f'Tasks in {project.name}', tasks)}"
        )
    if not tasks:
        raise OnboardingError(_no_tasks(host, project))
    return choose_one(
        tasks,
        noun="task",
        heading=f"Tasks in {project.name}",
        flag="--task-id",
        where=f"in {project.name}",
        terminal=terminal,
    )


def _project_path(project_path: str | None, project: ProjectRow) -> Path:
    """The folder the run uses. A given `--project-path` must be the folder
    Kiln has for the project: the read fence denies the folder it is given,
    so a wrong one would leave the real project, val and test data
    included, open to the agents."""
    kiln_folder = _kiln_folder(project)
    if project_path is None:
        if kiln_folder is None:
            raise OnboardingError(
                f"Kiln has no folder for project {project.name} ({project.id})."
                "\nfix: pass --project-path with the project's folder."
            )
        return kiln_folder
    if kiln_folder is not None and _folder(project_path) != kiln_folder.resolve():
        theirs = kiln_folder.resolve()
        raise OnboardingError(
            f"--project-path is {_folder(project_path)}, but Kiln has project"
            f" {project.id} at {theirs}.\n"
            f"fix: drop --project-path, or pass {shlex.quote(str(theirs))}."
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
) -> Selection:
    """The project, the task and the project's folder, from
    `args.project_id`, `args.task_id` and `args.project_path` (each `None`
    when not given), asking for what is missing. Raises `OnboardingError`
    for a choice that can't be made: an unknown ID, nothing to choose from,
    a missing ID when not interactive, or a path that isn't Kiln's."""
    project, tasks = _choose_project(
        args, terminal=terminal, run=run, host=host, prog=prog
    )
    # Before the task: a project with no folder is an error whichever task
    # the user would pick.
    project_path = _project_path(args.project_path, project)
    if tasks is None:
        tasks = run(lambda kiln: fetch_tasks(kiln, project.id))
    task, task_picked = _choose_task(args.task_id, project, tasks, terminal, host)
    return Selection(
        project=project,
        task=task,
        project_path=project_path,
        filled=None in (args.project_id, args.task_id, args.project_path),
        task_picked=task_picked,
    )
