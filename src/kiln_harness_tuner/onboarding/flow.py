"""`prepare_start`: the start-up checks, run before the run itself.

The flow is synchronous on purpose. `asyncio.run` installs its own SIGINT
handler, and while the event loop's thread waits at a prompt that handler
only cancels the main task: the first Ctrl-C at a prompt would do nothing
visible. So each prompt runs its own prompt_toolkit loop with no asyncio
loop running, and each batch of Kiln reads runs in its own short
`asyncio.run` through a `KilnRunner`, with no prompt inside it.
"""

from __future__ import annotations

import argparse
import asyncio
import shlex
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Protocol, TypeVar

from kiln_harness_tuner import __version__
from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.kiln import KilnClient
from kiln_harness_tuner.model_policy import ModelPolicy
from kiln_harness_tuner.onboarding.choose import Selection, select
from kiln_harness_tuner.onboarding.console import default_terminal
from kiln_harness_tuner.onboarding.demo import make_demo
from kiln_harness_tuner.onboarding.errors import OnboardingError, OnboardingStopped
from kiln_harness_tuner.onboarding.keys import (
    Need,
    key_problem,
    keys_passed_text,
    needed_providers,
)
from kiln_harness_tuner.onboarding.kiln_check import (
    KilnCheck,
    check_kiln,
    kiln_passed_text,
)
from kiln_harness_tuner.onboarding.readiness import Readiness, readiness_problem
from kiln_harness_tuner.onboarding.terminal import (
    Action,
    ActionKind,
    Problem,
    Terminal,
    resolve_problem,
)

T = TypeVar("T")

# The flags the rerun command sets itself, so any the user typed are dropped.
# Only `--demo-project` takes no value.
FILLED_FLAGS = frozenset({"--project-id", "--task-id", "--project-path"})
DROPPED_FLAGS = FILLED_FLAGS | {"--demo-project"}

PICK_ANOTHER_TASK = Action("t", "Pick a different task", ActionKind.PICK_TASK)


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

    The start is guided when stdin and stdout are terminals and
    `args.no_interaction` is false; `terminal` overrides that choice.

    `argv` is the command line as typed, and `parser_long_options` the
    parser's long option strings: they rebuild the command printed when
    anything was filled in. `policy_loader` loads `--model-policy`, whose
    providers the key check covers, each time that check runs; it is cli's,
    so a bad file reads the same here as in the run. The demo's copies go in
    `home`'s `Kiln Projects/` folder; `home` is `Path.home()` by default.

    Raises `OnboardingError` for a problem that can't be asked about (not
    guided, or an unknown ID); a guided start has shown it already, and
    marks it `shown`. Raises `OnboardingStopped` when the user stops,
    Ctrl-C included."""
    if terminal is None:
        terminal = default_terminal(prog, no_interaction=args.no_interaction)
    host = args.kiln_host.rstrip("/")
    run = runner if runner is not None else kiln_runner(host)
    home = home if home is not None else Path.home()
    try:
        terminal.header(f"Kiln Harness Tuner {__version__}")
        _check_kiln(run, terminal, host)
        if args.demo_project:
            _use_demo(args, terminal=terminal, run=run, home=home)
        selection = _choose_a_ready_task(
            args,
            argv,
            terminal=terminal,
            run=run,
            host=host,
            prog=prog,
            parser_long_options=parser_long_options,
            home=home,
        )
        _check_keys(
            run,
            terminal,
            host,
            selection,
            policy_path=args.model_policy,
            policy_loader=policy_loader,
        )
        return args
    except OnboardingError as error:
        if terminal.interactive:
            terminal.block(error.title, error.body)
            error.shown = True
        raise
    except KeyboardInterrupt:
        # A Ctrl-C during a Kiln read: since Python 3.11, `asyncio.run`
        # cancels the read and re-raises KeyboardInterrupt here.
        raise OnboardingStopped(130) from None


def _use_demo(
    args: argparse.Namespace, *, terminal: Terminal, run: KilnRunner, home: Path
) -> None:
    """Make and import a copy of the demo, and point `args` at it: what
    `--demo-project` does, and what `m` does when Kiln has no projects."""
    demo = make_demo(terminal=terminal, run=run, home=home)
    args.project_id = demo.project_id
    args.task_id = demo.task_id
    args.project_path = str(demo.folder)
    args.demo_project = True


def _choose_a_ready_task(
    args: argparse.Namespace,
    argv: Sequence[str],
    *,
    terminal: Terminal,
    run: KilnRunner,
    host: str,
    prog: str,
    parser_long_options: Sequence[str],
    home: Path,
) -> Selection:
    """Choose the project and the task until the task is ready to tune,
    then write them into `args`. Until then `args` holds only what was
    given, so a choice made again asks for the same things. `t` at the
    readiness menu goes back to the task picker in the same project. A demo
    fills in every flag before the choice, so the rerun line is printed for
    it although nothing is left to fill in here."""
    same_project: Selection | None = None
    while True:
        selection = select(
            args,
            terminal=terminal,
            run=run,
            host=host,
            prog=prog,
            same_project=same_project,
        )
        if selection is ActionKind.USE_DEMO:
            _use_demo(args, terminal=terminal, run=run, home=home)
            same_project = None
            continue
        # A `t` pass has no task ID, so it is always filled in, and the
        # line is printed again with the new task.
        if selection.filled or args.demo_project:
            command = rerun_command(
                prog,
                argv,
                parser_long_options=parser_long_options,
                project_path=str(selection.project_path),
                project_id=selection.project.id,
                task_id=selection.task.id,
            )
            terminal.say(f"To run this again without the questions:\n  {command}")
        if _check_ready(run, terminal, host, selection) is None:
            args.project_id = selection.project.id
            args.task_id = selection.task.id
            args.project_path = str(selection.project_path)
            return selection
        same_project = selection  # `t`


def _check_kiln(run: KilnRunner, terminal: Terminal, host: str) -> None:
    result = KilnCheck()

    def check() -> Problem | None:
        nonlocal result
        result = run(lambda kiln: check_kiln(kiln, host))
        return result.problem

    resolve_problem(check, terminal, busy=f"Checking Kiln at {host}…")
    terminal.step("ok", "Kiln", kiln_passed_text(result.version, host))
    if result.warning is not None:
        terminal.warn(result.warning)


def _check_ready(
    run: KilnRunner, terminal: Terminal, host: str, selection: Selection
) -> Action | None:
    """The readiness check, through the action menu. `None` once the task
    is ready, or the `t` action: another task is offered only for a task
    the user picked from a list."""
    readiness: Readiness | None = None

    def check() -> Problem | None:
        nonlocal readiness
        readiness = run(
            lambda kiln: readiness_problem(
                kiln,
                host=host,
                project_id=selection.project.id,
                task_id=selection.task.id,
                task_name=selection.task.name,
            )
        )
        return readiness.problem

    extra = (PICK_ANOTHER_TASK,) if selection.task_picked else ()
    action = resolve_problem(
        check, terminal, busy="Checking the task is ready…", extra=extra
    )
    if action is None:
        assert readiness is not None
        terminal.step("ok", "Ready", readiness.step_text)
        for warning in readiness.warnings:
            terminal.warn(warning)
    return action


def _check_keys(
    run: KilnRunner,
    terminal: Terminal,
    host: str,
    selection: Selection,
    *,
    policy_path: str | None,
    policy_loader: Callable[[str | None], ModelPolicy | None],
) -> None:
    """The API-key check, through the action menu. Each run of the check,
    the first and every retry, loads the policy and works out again what the
    run needs, so a fix made in Kiln or in the policy file counts on `r`."""
    needs: list[Need] = []

    def check() -> Problem | None:
        policy = policy_loader(policy_path)

        async def problem(kiln: KilnApi) -> Problem | None:
            nonlocal needs
            needs = await needed_providers(
                kiln,
                project_id=selection.project.id,
                task_id=selection.task.id,
                project_path=selection.project_path,
                policy=policy,
            )
            return await key_problem(kiln, host=host, needs=needs)

        return run(problem)

    resolve_problem(check, terminal, busy="Checking API keys…")
    terminal.step("ok", "API keys", keys_passed_text(needs))
