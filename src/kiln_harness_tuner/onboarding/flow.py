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
import shlex
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Protocol, TypeVar

from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.kiln import KilnClient
from kiln_harness_tuner.model_policy import ModelPolicy
from kiln_harness_tuner.onboarding.choose import Selection, select
from kiln_harness_tuner.onboarding.demo import make_demo
from kiln_harness_tuner.onboarding.errors import OnboardingStopped
from kiln_harness_tuner.onboarding.keys import key_problem, needed_providers
from kiln_harness_tuner.onboarding.kiln_check import check_kiln
from kiln_harness_tuner.onboarding.readiness import readiness_problem
from kiln_harness_tuner.onboarding.terminal import (
    Problem,
    Resolution,
    StdTerminal,
    Terminal,
    resolve_problem,
)

T = TypeVar("T")

# The flags the rerun command sets itself, so any the user typed are dropped.
# Only `--demo-project` takes no value.
FILLED_FLAGS = frozenset({"--project-id", "--task-id", "--project-path"})
DROPPED_FLAGS = FILLED_FLAGS | {"--demo-project"}


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


def _long_option(token: str, long_options: Sequence[str]) -> str | None:
    """The long option a `--x` or `--x=v` token names, the way argparse's
    `allow_abbrev` reads it: the option it equals, else the one option it
    is a prefix of. `None` when it names none, or several."""
    name = token.split("=", 1)[0]
    if name in long_options:
        return name
    matches = {option for option in long_options if option.startswith(name)}
    return matches.pop() if len(matches) == 1 else None


def rerun_command(
    prog: str,
    argv: Sequence[str],
    *,
    parser_long_options: Sequence[str],
    project_path: str,
    project_id: str,
    task_id: str,
) -> str:
    """The command that re-runs this start with no questions: `argv` with
    the project flags and `--demo-project` dropped, and the chosen
    project, task and folder passed instead. Shell-quoted."""
    rest: list[str] = []
    tokens = iter(argv)
    for token in tokens:
        if token == "--":
            rest += [token, *tokens]
            break
        option = (
            _long_option(token, parser_long_options) if token.startswith("--") else None
        )
        if option not in DROPPED_FLAGS:
            rest.append(token)
        elif option in FILLED_FLAGS and "=" not in token:
            next(tokens, None)  # the flag's value
    return shlex.join(
        [
            prog,
            "--project-path",
            project_path,
            "--project-id",
            project_id,
            "--task-id",
            task_id,
            *rest,
        ]
    )


def prepare_start(
    args: argparse.Namespace,
    argv: Sequence[str],
    *,
    prog: str,
    parser_long_options: Sequence[str],
    policy_loader: Callable[[str | None], ModelPolicy | None],
    terminal: Terminal | None = None,
    runner: KilnRunner | None = None,
    home: Path | None = None,
) -> argparse.Namespace:
    """Check that Kiln answers and is new enough, make and import a copy of
    the demo when `args.demo_project` asks for one, choose the project and
    the task, check that the task is ready to tune, and check that Kiln has
    a key for every provider the run calls, asking the user to fix each
    problem when they can. Returns `args` with `project_id`, `task_id` and
    `project_path` filled in.

    `argv` is the command line as typed, and `parser_long_options` the
    parser's long option strings: they rebuild the command printed when
    anything was filled in. `policy_loader` loads `--model-policy`, whose
    providers the key check covers, each time that check runs; it is cli's,
    so a bad file reads the same here as in the run. The demo's copies go in
    `home`'s `Kiln Projects/` folder; `home` is `Path.home()` by default.

    Raises `OnboardingError` for a problem that can't be asked about (not
    interactive, or an unknown ID), and `OnboardingStopped` when the user
    stops, Ctrl-C included."""
    if terminal is None:
        terminal = StdTerminal(prog)
    host = args.kiln_host.rstrip("/")
    run = runner if runner is not None else kiln_runner(host)
    try:
        resolve_problem(lambda: _run_kiln_check(run, terminal, host, prog), terminal)
        if args.demo_project:
            demo = make_demo(
                terminal=terminal,
                run=run,
                home=home if home is not None else Path.home(),
            )
            args.project_id = demo.project_id
            args.task_id = demo.task_id
            args.project_path = str(demo.folder)
        selection = _choose_a_ready_task(
            args,
            argv,
            terminal=terminal,
            run=run,
            host=host,
            prog=prog,
            parser_long_options=parser_long_options,
            demo=args.demo_project,
        )
        _wait_for_keys(
            run,
            terminal,
            host,
            selection,
            policy_path=args.model_policy,
            policy_loader=policy_loader,
        )
        return args
    except KeyboardInterrupt:
        # A Ctrl-C during a Kiln read: since Python 3.11, `asyncio.run`
        # cancels the read and re-raises KeyboardInterrupt here.
        raise OnboardingStopped(130) from None


def _choose_a_ready_task(
    args: argparse.Namespace,
    argv: Sequence[str],
    *,
    terminal: Terminal,
    run: KilnRunner,
    host: str,
    prog: str,
    parser_long_options: Sequence[str],
    demo: bool,
) -> Selection:
    """Choose the project and the task, writing them into `args`, until the
    task is ready to tune. `[t]` at the readiness prompt goes back to the
    task picker. `demo`: the demo filled in every flag before the choice, so
    the rerun line is printed although nothing is left to fill in here."""
    while True:
        selection = select(args, terminal=terminal, run=run, host=host, prog=prog)
        args.project_id = selection.project.id
        args.task_id = selection.task.id
        args.project_path = str(selection.project_path)
        # A `[t]` pass has no task ID, so it is always filled in, and the
        # line is printed again with the new task.
        if selection.filled or demo:
            command = rerun_command(
                prog,
                argv,
                parser_long_options=parser_long_options,
                project_path=args.project_path,
                project_id=args.project_id,
                task_id=args.task_id,
            )
            terminal.say(f"To run this again without the questions:\n  {command}")
        if _wait_until_ready(run, terminal, host, selection) is Resolution.OK:
            return selection
        # `[t]`: back to the task picker. The project and its folder
        # stay chosen.
        args.task_id = None


def _run_kiln_check(
    run: KilnRunner, terminal: Terminal, host: str, prog: str
) -> Problem | None:
    result = run(lambda kiln: check_kiln(kiln, host, prog=prog))
    if result.warning is not None:
        terminal.warn(result.warning)
    return result.problem


def _wait_until_ready(
    run: KilnRunner, terminal: Terminal, host: str, selection: Selection
) -> Resolution:
    """The readiness check, through the retry prompt. `[t]` is offered only
    for a task the user picked from a list."""

    def check() -> Problem | None:
        problem, warnings = run(
            lambda kiln: readiness_problem(
                kiln,
                host=host,
                project_id=selection.project.id,
                task_id=selection.task.id,
                task_name=selection.task.name,
            )
        )
        for warning in warnings:
            terminal.warn(warning)
        return problem

    return resolve_problem(check, terminal, offer_pick_task=selection.task_picked)


def _wait_for_keys(
    run: KilnRunner,
    terminal: Terminal,
    host: str,
    selection: Selection,
    *,
    policy_path: str | None,
    policy_loader: Callable[[str | None], ModelPolicy | None],
) -> None:
    """The API-key check, through the retry prompt. Each run of the check,
    the first and every retry, loads the policy and works out again what the
    run needs, so a fix made in Kiln or in the policy file counts on `r`."""

    def check() -> Problem | None:
        policy = policy_loader(policy_path)

        async def problem(kiln: KilnApi) -> Problem | None:
            needs = await needed_providers(
                kiln,
                project_id=selection.project.id,
                task_id=selection.task.id,
                project_path=selection.project_path,
                policy=policy,
            )
            return await key_problem(kiln, host=host, needs=needs)

        return run(problem)

    resolve_problem(check, terminal)
