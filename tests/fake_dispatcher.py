"""FakeDispatcher — scripted AgentResult sequences for the loop integration
tier. Each agent method pops from its own FIFO queue; an entry may
be an AgentResult or a callable taking the request (dynamic responses). An
empty queue fails the test loudly — every dispatch must be scripted.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from pydantic import BaseModel

from kiln_harness_tuner.schemas import (
    AgentResult,
    ErrorInfo,
    EvalDescription,
    GeneralityReviewResult,
    IntegrityReviewResult,
    Usage,
)

# The stand-in every unscripted describe_eval returns (see FakeDispatcher.
# describe_eval): the setup phase runs in EVERY orchestrate test, so making
# each of them script one description per eval would be pure noise.
DEFAULT_DESCRIPTION = EvalDescription(
    overview="the behaviour this eval measures regresses",
    description="A stand-in description written by the fake dispatcher.",
)


def ok(data, cost_usd: float = 0.0) -> AgentResult:
    return AgentResult(status="ok", data=data, usage=Usage(cost_usd=cost_usd))


def expected(detail: str, data=None) -> AgentResult:
    return AgentResult(status="failed_expected", data=data, detail=detail)


def infra(error_class: str = "provider_error", detail: str = "boom") -> AgentResult:
    return AgentResult(
        status="failed_infra",
        data=None,
        error=ErrorInfo(error_class=error_class, detail=detail),
    )


class FakeDispatcher:
    def __init__(self) -> None:
        self.queues: dict[str, list] = defaultdict(list)
        self.calls: list[tuple[str, BaseModel]] = []
        self.preflight_calls = 0
        # None → preflight succeeds silently (the common case; every
        # orchestrate test would otherwise have to script it). Set to an
        # exception to make preflight raise it.
        self.preflight_error: Exception | None = None

    async def preflight(self) -> Usage:
        self.preflight_calls += 1
        if self.preflight_error is not None:
            raise self.preflight_error
        return Usage()

    def queue(self, method: str, *results) -> None:
        self.queues[method].extend(results)

    def calls_to(self, method: str) -> list[BaseModel]:
        return [req for name, req in self.calls if name == method]

    async def _next(self, method: str, req: BaseModel) -> AgentResult:
        self.calls.append((method, req))
        queue = self.queues[method]
        if not queue:
            raise AssertionError(f"unscripted dispatch: {method}({req!r})")
        entry = queue.pop(0)
        if isinstance(entry, Callable):
            entry = entry(req)
        return entry

    async def batch_plan(self, req):
        return await self._next("batch_plan", req)

    async def describe_eval(self, req):
        # Defaults instead of failing when NEVER scripted — the setup phase
        # dispatches one of these per active eval on every
        # orchestrate test, and scripting them everywhere would be noise.
        # Keyed on "no queue was ever created", not "the queue is empty":
        # otherwise a test scripting one failure for a two-eval fan-out would
        # get a silent success for the second, hiding what it meant to pin.
        # `.get` on purpose — `self.queues[...]` would create the key.
        if self.queues.get("describe_eval") is None:
            self.calls.append(("describe_eval", req))
            return ok(DEFAULT_DESCRIPTION)
        return await self._next("describe_eval", req)

    async def eval_build(self, req):
        return await self._next("eval_build", req)

    async def create_experiment_rc(self, req):
        return await self._next("create_experiment_rc", req)

    async def analyze_experiment_results(self, req):
        return await self._next("analyze_experiment_results", req)

    async def promote_best(self, req):
        return await self._next("promote_best", req)

    async def probe(self, req):
        return await self._next("probe", req)

    async def report_writer(self, req):
        return await self._next("report_writer", req)

    async def integrity_review(self, req):
        # Like describe_eval: a default "no flags" answer when never
        # scripted, so gate tests with a changed prompt need not script it.
        if self.queues.get("integrity_review") is None:
            self.calls.append(("integrity_review", req))
            return ok(IntegrityReviewResult())
        return await self._next("integrity_review", req)

    async def generality_review(self, req):
        # Like integrity_review: a default "no leak" answer when never
        # scripted, so every test whose build changes the prompt need not.
        if self.queues.get("generality_review") is None:
            self.calls.append(("generality_review", req))
            return ok(GeneralityReviewResult())
        return await self._next("generality_review", req)
