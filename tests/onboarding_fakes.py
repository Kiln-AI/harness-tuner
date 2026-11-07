"""Fakes for the onboarding tests: a scripted terminal, a Kiln that answers
from a route table, a runner that drives it, and builders for what Kiln
answers."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.cli import build_parser
from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.onboarding.errors import OnboardingStopped
from kiln_harness_tuner.onboarding.flow import KilnRunner
from kiln_harness_tuner.onboarding.terminal import (
    WHAT_NEXT,
    Action,
    Option,
    StepStatus,
)

T = TypeVar("T")

Answer = Any  # a value, a key, a text, a callable, or an exception to raise


@dataclass(frozen=True)
class Asked:
    """One prompt: its kind (`pick`, `act` or `text`), its question, what it
    offered (the options' values, the actions' keys, or a text's default),
    its default and its hint."""

    kind: str
    question: str
    offered: tuple[Any, ...] = ()
    default: Any = None
    hint: str | None = None


class ScriptedTerminal:
    """A terminal that answers prompts from `answers`, in order: `pick` with
    an option's value, `act` with an action's key, and `text` with the text.
    An answer that is an exception is raised instead. A callable answer is
    called first, so a test can change Kiln while the prompt waits: with no
    arguments for `pick` and `act`, and with the `validate` function for
    `text`. A text answered with a string must pass `validate`, since the
    real field refuses one that doesn't. When the answers run out, the
    prompt stops the way Ctrl-D does.

    Everything the start shows is recorded. A test fails at once on a
    prompt in a start that isn't guided, a key or value the prompt didn't
    offer, a prompt while a spinner runs, and a spinner inside a spinner."""

    def __init__(self, answers: Sequence[Answer] = (), *, interactive: bool = True):
        self.answers = list(answers)
        self.interactive = interactive
        self.welcomes: list[tuple[str, str, str]] = []
        self.steps: list[tuple[StepStatus, str, str]] = []
        self.blocks: list[tuple[str, tuple[str, ...]]] = []
        self.out: list[str] = []
        self.err: list[str] = []
        self.busy_texts: list[str] = []
        self.opened: list[str] = []
        self.asked: list[Asked] = []
        self.options: list[tuple[Option[Any], ...]] = []  # each pick's options
        # Everything above, in order, one short line each: `welcome …`,
        # `step Kiln: …`, `block <title>`, `say <first line>`, `warn …`,
        # `busy …`, `pick …`, `act …`, `text …`, `open …`.
        self.transcript: list[str] = []
        self._spinning = False

    def welcome(self, title: str, version: str, description: str) -> None:
        self.welcomes.append((title, version, description))
        self.transcript.append(f"welcome {title} v{version}")

    def step(self, status: StepStatus, label: str, text: str) -> None:
        self.steps.append((status, label, text))
        self.transcript.append(f"step {label}: {text}")

    def block(self, title: str, body: Sequence[str]) -> None:
        assert self.interactive, "a block in a start that isn't guided"
        self.blocks.append((title, tuple(body)))
        self.transcript.append(f"block {title}")

    def say(self, text: str) -> None:
        self.out.append(text)
        first = next((line for line in text.splitlines() if line.strip()), "")
        self.transcript.append(f"say {first}")

    def warn(self, text: str) -> None:
        self.err.append(text)
        self.transcript.append(f"warn {text}")

    @contextmanager
    def busy(self, text: str) -> Iterator[None]:
        assert not self._spinning, f"a spinner inside a spinner: {text}"
        self.busy_texts.append(text)
        self.transcript.append(f"busy {text}")
        self._spinning = True
        try:
            yield
        finally:
            self._spinning = False

    def _answer(self, asked: Asked) -> Answer:
        assert self.interactive, (
            f"asked {asked.question!r} in a start that isn't guided"
        )
        assert not self._spinning, f"asked {asked.question!r} while a spinner runs"
        self.asked.append(asked)
        self.transcript.append(f"{asked.kind} {asked.question}")
        if not self.answers:
            raise OnboardingStopped(1)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def pick(
        self,
        question: str,
        options: Sequence[Option[T]],
        *,
        hint: str | None = None,
        default: T | None = None,
    ) -> T:
        offered = tuple(o.value for o in options)
        self.options.append(tuple(options))
        answer = self._answer(Asked("pick", question, offered, default, hint))
        value: Any = answer() if callable(answer) else answer
        pickable = [o.value for o in options if o.disabled is None]
        assert value in pickable, f"{value!r} isn't a pickable option of {offered}"
        return value

    def act(self, question: str, actions: Sequence[Action]) -> Action:
        offered = tuple(a.key for a in actions)
        answer = self._answer(Asked("act", question, offered))
        key = answer() if callable(answer) else answer
        for action in actions:
            if action.key == key:
                return action
        raise AssertionError(f"no {key!r} in the menu {offered}")

    def text(
        self,
        question: str,
        *,
        default: str = "",
        hint: str | None = None,
        validate: Callable[[str], str | None] | None = None,
    ) -> str:
        answer = self._answer(Asked("text", question, (default,), default, hint))
        if callable(answer):
            typed: Any = answer(validate)
            return typed
        if validate is not None:
            refused = validate(answer)
            assert refused is None, f"{answer!r} is refused: {refused}"
        return answer

    def open_url(self, url: str) -> None:
        self.opened.append(url)
        self.transcript.append(f"open {url}")

    @property
    def menus(self) -> list[tuple[str, ...]]:
        """The keys of each action menu, in order."""
        return [asked.offered for asked in self.asked if asked.kind == "act"]

    @property
    def problem_menus(self) -> list[tuple[str, ...]]:
        """The keys of each failed check's menu, in order."""
        return [
            asked.offered
            for asked in self.asked
            if asked.kind == "act" and asked.question == WHAT_NEXT
        ]

    @property
    def questions(self) -> list[str]:
        return [asked.question for asked in self.asked]

    def asked_about(self, question: str) -> Asked:
        """The last prompt that asked `question`."""
        return next(a for a in reversed(self.asked) if a.question == question)

    def step_lines(self) -> list[str]:
        """The step lines as `label: text`, for short assertions."""
        return [f"{label}: {text}" for _status, label, text in self.steps]


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
    no_interaction: bool = False,
) -> argparse.Namespace:
    """The parsed flags, with the parser's defaults for every flag not
    named here; the project flags are `None` when not given."""
    args = build_parser().parse_args([])
    args.kiln_host = kiln_host
    args.project_id = project_id
    args.task_id = task_id
    args.project_path = project_path
    args.model_policy = model_policy
    args.demo_project = demo_project
    args.no_interaction = no_interaction
    return args


# Every core question answered by a flag with its default value.
CORE_ANSWERED = [
    "--objective",
    "quality",
    "--guidance",
    "",
    "--agent-cli",
    "claude -p",
    "--budget-usd",
    "50",
    "--timeout-mins",
    "720",
]
# With the advanced questions that have a flag for their default, so a
# guided start asks only the Advanced menu and whether to start the run: for
# tests about the checks, which answer those with `KEEP_AND_START`.
ANSWERED = [
    *CORE_ANSWERED,
    "--max-concurrent-evals",
    "4",
    "--max-concurrent-agents",
    "4",
]
ADVANCED_PROMPT = (
    "Advanced options: any model · 4 evals and 4 agents at once"
    " · no minimum effect · no noise check"
)
KEEP_AND_START = ["enter", "s"]
START_PROMPT = "Start the run?"
