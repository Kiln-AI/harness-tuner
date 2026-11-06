"""Fakes for the onboarding tests: a scripted terminal, a Kiln that answers
from a route table, a runner that drives it, and builders for what Kiln
answers."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.onboarding.errors import OnboardingStopped
from kiln_harness_tuner.onboarding.flow import KilnRunner

T = TypeVar("T")


class ScriptedTerminal:
    """A terminal that answers prompts from `inputs`, in order. An input that
    is an exception is raised instead. When the inputs run out, `ask` stops
    the way end of input does."""

    def __init__(
        self, inputs: Sequence[str | BaseException] = (), *, interactive: bool = True
    ):
        self.inputs = list(inputs)
        self.interactive = interactive
        self.out: list[str] = []
        self.err: list[str] = []
        self.prompts: list[str] = []
        self.opened: list[str] = []

    def say(self, text: str) -> None:
        self.out.append(text)

    def warn(self, text: str) -> None:
        self.err.append(text)

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.inputs:
            raise OnboardingStopped(1)
        answer = self.inputs.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def open_url(self, url: str) -> None:
        self.opened.append(url)


@dataclass(frozen=True)
class KilnCall:
    method: str
    path: str
    query: dict[str, Any] | None
    body: Any
    timeout_secs: float | None
    retries: bool | None


class RouteKiln:
    """A Kiln that answers `(method, path)` from `routes`. A value is
    returned, an exception is raised, and a callable is called with
    `(query_params, body)` and its result used the same way. An unrouted call
    is a 404, as Kiln answers one. Every call is recorded in `calls`."""

    def __init__(self, routes: dict[tuple[str, str], Any]):
        self.routes = dict(routes)
        self.calls: list[KilnCall] = []

    async def call(
        self,
        method: str,
        url_path: str,
        query_params: dict[str, Any] | None = None,
        body: Any | None = None,
        *,
        timeout_secs: float | None = None,
        retries: bool | None = None,
    ) -> Any:
        self.calls.append(
            KilnCall(method, url_path, query_params, body, timeout_secs, retries)
        )
        key = (method, url_path)
        if key not in self.routes:
            raise KilnAPIError(method, url_path, 404, {"detail": "Not Found"})
        answer = self.routes[key]
        if callable(answer):
            answer = answer(query_params, body)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def paths(self) -> list[str]:
        return [call.path for call in self.calls]


def responses(*answers: Any) -> Callable[[Any, Any], Any]:
    """A route that gives each answer in turn, then keeps giving the last."""
    queue = list(answers)

    def next_answer(_query: Any, _body: Any) -> Any:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return next_answer


def runner_for(kiln: RouteKiln) -> KilnRunner:
    def run(fn: Callable[[KilnApi], Awaitable[T]], /) -> T:
        async def on_the_fake() -> T:
            return await fn(kiln)

        return asyncio.run(on_the_fake())

    return run


HOST = "http://localhost:8757"
PROG = "kiln-harness-tuner"

# The demo's IDs, which are inside the invented `424242……` band.
PROJECT_ID = "424242975950"
TASK_ID = "424242783061"
FOLDER = "/kiln-projects/Forum Moderation"

Routes = dict[tuple[str, str], Any]


def openapi(
    *,
    version: object = "1.0.3",
    memories: bool = True,
    eval_jobs: bool = True,
    provenance: bool = True,
) -> dict:
    """An OpenAPI document shaped like Kiln's, with each needed API present
    unless turned off."""
    paths = {"/ping": {}}
    if memories:
        paths["/api/projects/{project_id}/memories"] = {}
    if eval_jobs:
        paths["/api/jobs/evals/run"] = {}
    properties = {"name": {}, "run_config_properties": {}}
    if provenance:
        properties["provenance"] = {}
    return {
        "info": {"title": "Kiln", "version": version},
        "paths": paths,
        "components": {
            "schemas": {"CreateTaskRunConfigRequest": {"properties": properties}}
        },
    }


def project(
    project_id: str = PROJECT_ID,
    name: str = "Forum Moderation",
    folder: str | None = FOLDER,
) -> dict[str, Any]:
    """One `GET /api/projects` row. `path` names the project's
    `project.kiln`, as Kiln's list sets it."""
    row: dict[str, Any] = {"v": 1, "id": project_id, "name": name}
    if folder is not None:
        row["path"] = f"{folder}/project.kiln"
    return row


def task(task_id: str = TASK_ID, name: str = "Moderate posts") -> dict[str, Any]:
    """One `GET /api/projects/{id}/tasks` row."""
    return {"v": 1, "id": task_id, "name": name, "turn_mode": "single_turn"}


def task_base(project_id: str, task_id: str) -> str:
    return f"/api/projects/{project_id}/tasks/{task_id}"


def ready_task_routes(project_id: str, task_id: str) -> Routes:
    """The readiness reads of a task that is ready: one run config, and one
    eval with a default eval config and no spec."""
    base = task_base(project_id, task_id)
    return {
        ("GET", f"{base}/run_configs"): [{"id": "rc_1", "name": "Baseline"}],
        ("GET", f"{base}/evals"): [
            {"id": "eval_1", "name": "Tone", "current_config_id": "ec_1"}
        ],
        ("GET", f"{base}/specs"): [],
    }


def kiln_routes(*projects: tuple[dict[str, Any], list[dict[str, Any]]]) -> Routes:
    """A new Kiln that answers the start-up reads for `projects`, each a
    project row with its task rows. Every task is ready."""
    routes: Routes = {
        ("GET", "/ping"): "pong",
        ("GET", "/openapi.json"): openapi(),
        ("GET", "/api/projects"): [row for row, _tasks in projects],
    }
    for row, tasks in projects:
        routes[("GET", f"/api/projects/{row['id']}/tasks")] = tasks
        for task_row in tasks:
            routes.update(ready_task_routes(row["id"], task_row["id"]))
    return routes


def one_project_routes() -> Routes:
    """A Kiln with the demo's project and its one task."""
    return kiln_routes((project(), [task()]))


def flags(
    project_id: str | None = None,
    task_id: str | None = None,
    project_path: str | None = None,
    *,
    kiln_host: str = HOST,
) -> argparse.Namespace:
    """The flags the start-up reads, each `None` when not given."""
    return argparse.Namespace(
        kiln_host=kiln_host,
        project_id=project_id,
        task_id=task_id,
        project_path=project_path,
    )
