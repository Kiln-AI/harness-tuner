"""The terminal the onboarding talks through, and the shared retry prompt.

Plain `print` and `input`, no `rich`: the prompts are a few lines, and plain
I/O is what a scripted test terminal can replay.
"""

from __future__ import annotations

import sys
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TextIO

from kiln_harness_tuner.onboarding.errors import OnboardingError, OnboardingStopped


class Terminal(Protocol):
    interactive: bool

    def say(self, text: str) -> None:
        """One message to stdout."""
        ...

    def warn(self, text: str) -> None:
        """One message to stderr, prefixed `<prog>: `."""
        ...

    def ask(self, prompt: str) -> str:
        """One line of input. End of input raises `OnboardingStopped(1)`, and
        Ctrl-C raises `OnboardingStopped(130)`."""
        ...

    def open_url(self, url: str) -> None:
        """Open `url` in the browser, and print it too: a remote shell has
        no browser."""
        ...


def _is_tty(stream: TextIO | None) -> bool:
    return stream is not None and stream.isatty()


class StdTerminal:
    """The real terminal: stdout, stderr, `input()` and the browser."""

    def __init__(self, prog: str):
        self.prog = prog
        self.interactive = _is_tty(sys.stdin) and _is_tty(sys.stdout)

    def say(self, text: str) -> None:
        print(text)

    def warn(self, text: str) -> None:
        print(f"{self.prog}: {text}", file=sys.stderr)

    def ask(self, prompt: str) -> str:
        # The newline before each stop puts the next line, ours or the
        # shell's, at the start of a line instead of after the prompt.
        try:
            return input(prompt)
        except EOFError:
            print()
            raise OnboardingStopped(1) from None
        except KeyboardInterrupt:
            print()
            raise OnboardingStopped(130) from None

    def open_url(self, url: str) -> None:
        webbrowser.open(url)
        self.say(url)


@dataclass(frozen=True)
class Problem:
    """A failed check: what is wrong and how to fix it, with no prompt line.
    `open_url` is the page that fixes it, offered as `[o]pen <open_label>`."""

    message: str
    open_url: str | None = None
    open_label: str | None = None


class Resolution(Enum):
    OK = "ok"
    PICK_TASK = "pick_task"


def resolve_problem(
    check: Callable[[], Problem | None],
    terminal: Terminal,
    *,
    offer_pick_task: bool = False,
) -> Resolution:
    """Run `check` until it finds no problem.

    Not interactive, the first problem raises `OnboardingError`. Interactive,
    each problem is printed, then the prompt asks what to do: retry, open the
    fix page, pick another task (only when `offer_pick_task`), or exit. A
    retry runs `check` again; nothing polls."""
    problem = check()
    while problem is not None:
        if not terminal.interactive:
            raise OnboardingError(problem.message)
        terminal.say(problem.message)
        if _ask_what_next(problem, terminal, offer_pick_task) is Resolution.PICK_TASK:
            return Resolution.PICK_TASK
        problem = check()
    return Resolution.OK


def _ask_what_next(
    problem: Problem, terminal: Terminal, offer_pick_task: bool
) -> Resolution | None:
    """Ask until the answer is a retry (None) or a task pick. `o` opens the
    fix page and asks again without re-checking; `x` stops."""
    prompt = _prompt(problem, offer_pick_task)
    while True:
        answer = terminal.ask(prompt).strip().lower()
        if answer in ("", "r"):
            return None
        if answer == "o" and problem.open_url is not None:
            terminal.open_url(problem.open_url)
        elif answer == "t" and offer_pick_task:
            return Resolution.PICK_TASK
        elif answer == "x":
            raise OnboardingStopped(1)


def _prompt(problem: Problem, offer_pick_task: bool) -> str:
    options = ["[r]etry"]
    if problem.open_url is not None:
        options.append(f"[o]pen {problem.open_label or problem.open_url}")
    if offer_pick_task:
        options.append("[t]ask: pick another")
    options.append("e[x]it")
    return "  ".join(options) + ": "
