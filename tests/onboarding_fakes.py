"""Fakes for the onboarding tests: a scripted terminal, a Kiln that answers
from a route table, a runner that drives it, and builders for what Kiln
answers."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.onboarding.errors import OnboardingStopped
from kiln_harness_tuner.onboarding.flow import KilnRunner

T = TypeVar("T")


class ScriptedTerminal:
    """A terminal that answers prompts from `inputs`, in order. An input that
    is an exception is raised instead, and one that is a callable is called
    for the answer, so a test can change things while the prompt waits. When
    the inputs run out, `ask` stops the way end of input does."""

    def __init__(
        self,
        inputs: Sequence[str | BaseException | Callable[[], str]] = (),
        *,
        interactive: bool = True,
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
        return answer() if callable(answer) else answer

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


def memories_path(project_id: str) -> str:
    return f"/api/projects/{project_id}/memories"


def run_config(
    rc_id: str = "rc_1",
    name: str = "Baseline",
    provider: str | None = "fireworks_ai",
    **fields: Any,
) -> dict[str, Any]:
    """One `GET …/run_configs` row. `provider` is its model's provider;
    `None` leaves the model out, as an MCP run config does."""
    row: dict[str, Any] = {"id": rc_id, "name": name, **fields}
    if provider is not None:
        row["run_config_properties"] = {
            "type": "kiln_agent",
            "model_provider_name": provider,
            "model_name": "llama-v3p1-8b-instruct",
        }
    return row


def eval_config(
    properties: dict[str, Any] | None = None,
    *,
    model_provider: str | None = None,
    config_id: str = "ec_1",
) -> dict[str, Any]:
    """One `GET …/evals/{id}/eval_config/{config_id}` body: a code eval,
    which calls no model, unless `properties` say otherwise.
    `model_provider` is the root field a legacy judge sets."""
    return {
        "id": config_id,
        "name": "scorer",
        "model_name": None,
        "model_provider": model_provider,
        "config_type": "g_eval" if model_provider else "v2",
        "properties": properties or {"type": "code_eval"},
    }


def memory_listing(*rows: dict[str, Any]) -> dict[str, Any]:
    """One `GET /api/projects/{id}/memories` page holding all of `rows`."""
    return {"listings": list(rows), "matched": len(rows), "remaining": 0}


def available_models(*provider_ids: str) -> list[dict[str, Any]]:
    """A `GET /api/available_models` body: the providers Kiln has keys for,
    each with one model."""
    return [
        {
            "provider_id": provider_id,
            "provider_name": provider_id,
            "models": [{"id": "some_model", "name": "Some Model"}],
        }
        for provider_id in provider_ids
    ]


def ready_task_routes(project_id: str, task_id: str) -> Routes:
    """The start-up reads of a task that is ready, whose provider Kiln has a
    key for: one run config on `fireworks_ai`, one eval with a code-eval
    default config and no spec, a single-turn task, and no baseline record
    yet. `/api/available_models` is Kiln-wide, so `kiln_routes` answers it."""
    base = task_base(project_id, task_id)
    return {
        ("GET", base): {"v": 1, "id": task_id, "turn_mode": "single_turn"},
        ("GET", f"{base}/run_configs"): [run_config()],
        ("GET", f"{base}/evals"): [
            {"id": "eval_1", "name": "Tone", "current_config_id": "ec_1"}
        ],
        ("GET", f"{base}/evals/eval_1/eval_config/ec_1"): eval_config(),
        ("GET", f"{base}/specs"): [],
        ("GET", memories_path(project_id)): memory_listing(),
    }


def kiln_routes(*projects: tuple[dict[str, Any], list[dict[str, Any]]]) -> Routes:
    """A new Kiln that answers the start-up reads for `projects`, each a
    project row with its task rows. Every task is ready, and Kiln has the
    key each one needs."""
    routes: Routes = {
        ("GET", "/ping"): "pong",
        ("GET", "/openapi.json"): openapi(),
        ("GET", "/api/projects"): [row for row, _tasks in projects],
        ("GET", "/api/available_models"): available_models("fireworks_ai"),
    }
    for row, tasks in projects:
        routes[("GET", f"/api/projects/{row['id']}/tasks")] = tasks
        for task_row in tasks:
            routes.update(ready_task_routes(row["id"], task_row["id"]))
    return routes


def import_project_route(kiln: RouteKiln) -> Callable[[Any, Any], Any]:
    """A `POST /api/import_project` route that imports like Kiln: it loads
    the `project.kiln` the query names from disk, lists the project in
    `GET /api/projects` with that path, and answers the start-up reads of
    each of its tasks as `ready_task_routes` does. It returns the project."""

    def import_project(query: dict[str, Any], _body: Any) -> dict[str, Any]:
        project_file = Path(query["project_path"])
        row = json.loads(project_file.read_text(encoding="utf-8"))
        projects = kiln.routes.get(("GET", "/api/projects"), [])
        listed = {**row, "path": str(project_file)}
        kiln.routes[("GET", "/api/projects")] = [*projects, listed]
        tasks = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(project_file.parent.glob("tasks/*/task.kiln"))
        ]
        kiln.routes[("GET", f"/api/projects/{row['id']}/tasks")] = tasks
        for task_row in tasks:
            kiln.routes.update(ready_task_routes(row["id"], task_row["id"]))
        return row

    return import_project


def one_project_routes() -> Routes:
    """A Kiln with the demo's project and its one task."""
    return kiln_routes((project(), [task()]))


def flags(
    project_id: str | None = None,
    task_id: str | None = None,
    project_path: str | None = None,
    *,
    kiln_host: str = HOST,
    model_policy: str | None = None,
    demo_project: bool = False,
) -> argparse.Namespace:
    """The flags the start-up reads, each `None` when not given."""
    return argparse.Namespace(
        kiln_host=kiln_host,
        project_id=project_id,
        task_id=task_id,
        project_path=project_path,
        model_policy=model_policy,
        demo_project=demo_project,
    )
