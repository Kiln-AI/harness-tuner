"""`prepare_start`: the start-up checks, run before the run itself.

The flow is synchronous on purpose. `asyncio.run` installs its own SIGINT
handler, and while the event loop's thread is blocked in `input()` that
handler only cancels the main task: the first Ctrl-C at a prompt would do
nothing visible. So prompts use plain `input()`, and each batch of Kiln reads
runs in its own short `asyncio.run` through a `KilnRunner`, with no prompt
inside it.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.kiln import KilnClient
from kiln_harness_tuner.onboarding.errors import OnboardingStopped
from kiln_harness_tuner.onboarding.kiln_check import check_kiln
from kiln_harness_tuner.onboarding.terminal import (
    Problem,
    StdTerminal,
    Terminal,
    resolve_problem,
)

T = TypeVar("T")


class KilnRunner(Protocol):
    """Runs one batch of Kiln reads to completion and returns its result.

    A Protocol rather than a `Callable` alias, so that `T` binds per call."""

    def __call__(self, fn: Callable[[KilnApi], Awaitable[T]], /) -> T: ...


def kiln_runner(host: str) -> KilnRunner:
    """Runs `fn` with a fresh KilnClient in its own `asyncio.run`, then
    closes the client."""

    def run(fn: Callable[[KilnApi], Awaitable[T]], /) -> T:
        async def with_client() -> T:
            async with KilnClient(host) as kiln:
                return await fn(kiln)

        return asyncio.run(with_client())

    return run


def prepare_start(
    args: argparse.Namespace,
    *,
    prog: str,
    terminal: Terminal | None = None,
    runner: KilnRunner | None = None,
) -> argparse.Namespace:
    """Check that Kiln answers and is new enough, asking the user to fix it
    when it isn't, then return `args` for the run.

    Raises `OnboardingError` for a problem that can't be asked about (not
    interactive), and `OnboardingStopped` when the user stops, Ctrl-C
    included."""
    if terminal is None:
        terminal = StdTerminal(prog)
    host = args.kiln_host.rstrip("/")
    run = runner if runner is not None else kiln_runner(host)
    try:
        resolve_problem(lambda: _run_kiln_check(run, terminal, host, prog), terminal)
    except KeyboardInterrupt:
        # A Ctrl-C during a Kiln read: since Python 3.11, `asyncio.run`
        # cancels the read and re-raises KeyboardInterrupt here.
        raise OnboardingStopped(130) from None
    return args


def _run_kiln_check(
    run: KilnRunner, terminal: Terminal, host: str, prog: str
) -> Problem | None:
    result = run(lambda kiln: check_kiln(kiln, host, prog=prog))
    if result.warning is not None:
        terminal.warn(result.warning)
    return result.problem
