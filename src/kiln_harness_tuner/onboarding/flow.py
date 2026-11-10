"""`prepare_start`: the start-up checks, then the questions and the summary,
all before the run itself.

The flow is synchronous on purpose: no asyncio loop runs while it waits at a
prompt, and each batch of Kiln reads runs in its own short `asyncio.run`
through a `KilnRunner`, with no prompt inside it. A plain start runs it on
the main thread. A guided start runs it on a thread of its own behind the
terminal app, whose terminal waits for each answer and whose runner the app
can cancel (`kiln_harness_tuner.tui.bridge`).
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from kiln_harness_tuner import __version__
from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.kiln import KilnClient
from kiln_harness_tuner.model_policy import ModelPolicy
from kiln_harness_tuner.onboarding.choose import Selection, select
from kiln_harness_tuner.onboarding.console import PlainTerminal
from kiln_harness_tuner.onboarding.demo import make_demo
from kiln_harness_tuner.onboarding.errors import OnboardingError, OnboardingStopped
from kiln_harness_tuner.onboarding.keys import (
    Baseline,
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
from kiln_harness_tuner.onboarding.questions import (
    Answers,
    QuestionContext,
    ask_all,
    ask_one,
    changeable,
)
from kiln_harness_tuner.onboarding.readiness import Readiness, readiness_problem
from kiln_harness_tuner.onboarding.summary import (
    RERUN_HEADING,
    OptionInfo,
    given_dests,
    parser_defaults,
    rerun_command,
    summary_lines,
    summary_text,
)
from kiln_harness_tuner.onboarding.terminal import (
    EXIT_ACTION,
    Action,
    ActionKind,
    Option,
    Problem,
    Terminal,
    resolve_problem,
)

T = TypeVar("T")

PICK_ANOTHER_TASK = Action("t", "Pick a different task", ActionKind.PICK_TASK)
START = Action("s", "Start tuning", ActionKind.START)
CHANGE = Action("e", "Change an answer", ActionKind.CHANGE)
START_QUESTION = "Start tuning?"
CHANGE_QUESTION = "Which answer do you want to change?"
WELCOME_TITLE = "Kiln Harness Tuner"
WELCOME_DESCRIPTION = (
    "Automatically improves an AI agent by tuning its harness: the prompt,"
    " model, tools, skills and settings around it."
)


@dataclass(frozen=True)
class StartReport:
    """What a start chose and last said, for a display that keeps it after
    the start's own lines are gone: the names the run is about, its
    baseline run config, and the summary and the rerun command."""

    project_name: str
    task_name: str
    baseline_rc_id: str | None  # None when the key check found no baseline
    baseline_name: str | None
    summary: str
    rerun: str | None  # None when the start isn't guided: it says none


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
    argv: Sequence[str],
    *,
    prog: str,
    option_dests: Mapping[str, OptionInfo],
    policy_loader: Callable[[str | None], ModelPolicy | None],
    terminal: Terminal | None = None,
    runner: KilnRunner | None = None,
    home: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
    report: Callable[[StartReport], None] | None = None,
) -> argparse.Namespace:
    """Check that Kiln answers and is new enough, make and import a copy of
    the demo when `args.demo_project` asks for one, choose the project and
    the task, check that the task is ready to tune, and check that Kiln has
    a key for every provider the run calls, asking the user to fix each
    problem when they can. A guided start then asks every question no flag
    answered, and shows the summary and the command that runs the start
    again, until the user starts the run; a start that isn't guided shows
    the summary only. Returns `args` with the project, the task, the folder
    and every answer filled in.

    The start is guided when its `terminal` is interactive: the app passes
    its own. With no `terminal` it is a plain start (`PlainTerminal`).

    `argv` is the command line as typed, and `option_dests` the parser's
    long options: they say which flags were given, whose questions are
    skipped, and rebuild the rerun command. `policy_loader` loads the model
    policy, whose providers the key check covers, each time that check
    runs; it is cli's, so a bad file reads the same here as in the run. The
    demo's copies go in `home`'s `Kiln Projects/` folder; `home` is
    `Path.home()` by default. `which` finds the agent CLIs on PATH.
    `report`, when given, gets the `StartReport` once, just before the
    start returns, and never when it stops or fails.

    Raises `OnboardingError` for a problem that can't be asked about (not
    guided, or an unknown ID); a guided start also shows it as a block.
    Raises `OnboardingStopped` when the user stops, Ctrl-C included."""
    if terminal is None:
        terminal = PlainTerminal(prog)
    host = args.kiln_host.rstrip("/")
    run = runner if runner is not None else kiln_runner(host)
    home = home if home is not None else Path.home()
    given = given_dests(argv, option_dests)
    defaults = parser_defaults(option_dests)
    try:
        terminal.welcome(WELCOME_TITLE, __version__, WELCOME_DESCRIPTION)
        _check_kiln(run, terminal, host)
        if args.demo_project:
            _use_demo(args, terminal=terminal, run=run, home=home)
        selection = _choose_a_ready_task(
            args, terminal=terminal, run=run, host=host, prog=prog, home=home
        )
        keys = _KeyCheck(run, terminal, host, selection, policy_loader)
        keys.check(args.model_policy)
        answers = Answers.from_args(args)

        def summary() -> str:
            lines = summary_lines(selection, answers, keys.baseline, defaults)
            return "\n" + summary_text(lines)

        def rerun() -> str:
            command = rerun_command(
                prog,
                argv,
                option_dests=option_dests,
                selection=selection,
                answers=answers,
                indent="  ",
            )
            return f"\n{RERUN_HEADING}\n{command}\n"

        if terminal.interactive:
            context = QuestionContext(
                terminal, defaults, policy_loader, demo=args.demo_project, which=which
            )
            _ask_until_started(
                answers, context, given=given, keys=keys, summary=summary, rerun=rerun
            )
        else:
            terminal.say(summary())
        answers.apply(args)
        if report is not None:
            # Nothing they read changes after the last say, so the closures
            # give the texts as the terminal last showed them.
            baseline = keys.baseline
            report(
                StartReport(
                    project_name=selection.project.name,
                    task_name=selection.task.name,
                    baseline_rc_id=None if baseline is None else baseline.rc_id,
                    baseline_name=None if baseline is None else baseline.name,
                    summary=summary(),
                    rerun=rerun() if terminal.interactive else None,
                )
            )
        return args
    except OnboardingError as error:
        if terminal.interactive:
            terminal.block(error.title, error.body)
        raise
    except KeyboardInterrupt:
        # A Ctrl-C during a Kiln read: since Python 3.11, `asyncio.run`
        # cancels the read and re-raises KeyboardInterrupt here.
        raise OnboardingStopped(130) from None


def _ask_until_started(
    answers: Answers,
    context: QuestionContext,
    *,
    given: frozenset[str],
    keys: _KeyCheck,
    summary: Callable[[], str],
    rerun: Callable[[], str],
) -> None:
    """Ask every question no flag answered, then show the summary and the
    rerun command, each as of the answers so far, until the user starts the
    run. Change an answer asks one question again; an answer that sets or
    changes the model policy runs the key check again, so its providers are
    covered."""
    terminal = context.terminal
    ask_all(answers, context, given=given)
    keys.check_if_changed(answers.model_policy)
    questions = changeable(given)
    menu = (START, *((CHANGE,) if questions else ()), EXIT_ACTION)
    while True:
        terminal.say(summary())
        terminal.say(rerun())
        action = terminal.act(START_QUESTION, menu)
        if action.kind is ActionKind.START:
            return
        if action.kind is ActionKind.EXIT:
            raise OnboardingStopped(1)
        name = terminal.pick(
            CHANGE_QUESTION,
            [Option(q.name, q.title, (q.show(answers),)) for q in questions],
        )
        ask_one(name, answers, context)
        keys.check_if_changed(answers.model_policy)


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
    *,
    terminal: Terminal,
    run: KilnRunner,
    host: str,
    prog: str,
    home: Path,
) -> Selection:
    """Choose the project and the task until the task is ready to tune,
    then write them into `args`. Until then `args` holds only what was
    given, so a choice made again asks for the same things. `t` at the
    readiness menu goes back to the task picker in the same project."""
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


@dataclass
class _KeyCheck:
    """The API-key check, through the action menu, with the policy it last
    covered and the baseline run config it found, for the summary."""

    run: KilnRunner
    terminal: Terminal
    host: str
    selection: Selection
    policy_loader: Callable[[str | None], ModelPolicy | None]
    policy: str | None = None
    baseline: Baseline | None = None

    def check(self, policy_path: str | None) -> None:
        """Each run of the check, the first and every retry, loads the policy
        and works out again what the run needs, so a fix made in Kiln or in
        the policy file counts on `r`."""
        needs: list[Need] = []
        selection = self.selection

        def check() -> Problem | None:
            policy = self.policy_loader(policy_path)

            async def problem(kiln: KilnApi) -> Problem | None:
                nonlocal needs
                needs, self.baseline = await needed_providers(
                    kiln,
                    project_id=selection.project.id,
                    task_id=selection.task.id,
                    project_path=selection.project_path,
                    policy=policy,
                )
                return await key_problem(kiln, host=self.host, needs=needs)

            return self.run(problem)

        resolve_problem(check, self.terminal, busy="Checking API keys…")
        self.terminal.step("ok", "API keys", keys_passed_text(needs))
        self.policy = policy_path

    def check_if_changed(self, policy_path: str | None) -> None:
        """The check again, when the policy isn't the one it last covered."""
        if policy_path != self.policy:
            self.check(policy_path)
