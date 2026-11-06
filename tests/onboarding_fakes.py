"""Fakes for the onboarding tests: a scripted terminal, a Kiln that answers
from a route table, and a runner that drives it."""

from __future__ import annotations

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
