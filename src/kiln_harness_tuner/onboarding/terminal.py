"""What the start-up checks say and ask, independent of how it is shown.

The `Terminal` protocol is the one surface the checks talk through: the
terminal app has the guided terminal (`tui/bridge.py`), `console.py` the
plain one, and the tests a scripted one. A failed check is a `Problem`, and
`resolve_problem` is the one loop that shows it, offers what to do, and runs
the check again. The rules a guided terminal draws a picker by
(`aligned_rows`, `PICK_HINT`) are here too, outside the app, so the plain
path never loads Textual through them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Generic, Literal, Protocol, TypeVar

from rich.cells import cell_len

from kiln_harness_tuner.onboarding.errors import OnboardingError, OnboardingStopped

T = TypeVar("T")

StepStatus = Literal["ok", "fail", "warn"]

WHAT_NEXT = "What next?"
# What a picker says under its question when the question gives no hint.
PICK_HINT = "↑↓ move · type to filter · Enter pick"
COLUMN_GAP = "   "


@dataclass(frozen=True)
class Option(Generic[T]):
    """One row of a picker. `columns` are aligned across the rows."""

    value: T
    label: str
    columns: tuple[str, ...] = ()
    description: str | None = None  # shown under the highlighted row
    disabled: str | None = None  # why it can't be picked, shown dimmed


def aligned_rows(options: Sequence[Option[Any]]) -> list[str]:
    """Each option's label and columns, every column padded to its widest
    cell, so the rows line up."""
    cells = [(option.label, *option.columns) for option in options]
    widths = [0] * max(len(row) for row in cells)
    for row in cells:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], cell_len(cell))
    return [
        COLUMN_GAP.join(
            cell + " " * (widths[i] - cell_len(cell)) for i, cell in enumerate(row)
        ).rstrip()
        for row in cells
    ]


class ActionKind(Enum):
    RETRY = "retry"
    OPEN = "open"
    PICK_TASK = "pick_task"
    PICK_PROJECT = "pick_project"
    USE_DEMO = "use_demo"
    EXIT = "exit"
    START = "start"
    CHANGE = "change"
    KEEP = "keep"
    CUSTOMIZE = "customize"


# The key of an action that Enter picks, shown as `Enter)`. Enter picks the
# action the cursor is on, which starts on the first, so it leads its menu.
ENTER = "enter"


@dataclass(frozen=True)
class Action:
    """One entry of an action menu, chosen by its single `key`."""

    key: str  # one lower-case letter, unique in its menu, or ENTER
    label: str
    kind: ActionKind
    url: str | None = None  # the page an OPEN action opens


class Terminal(Protocol):
    interactive: bool  # True for a guided start

    def welcome(self, title: str, version: str, description: str) -> None:
        """What a guided start opens with: the title, the version and what
        the tool does."""
        ...

    def step(self, status: StepStatus, label: str, text: str) -> None:
        """One finished step: a symbol, the label in a fixed width, the text."""
        ...

    def block(self, title: str, body: Sequence[str]) -> None:
        """A failed check: the ✗ title and the lines that explain it."""
        ...

    def say(self, text: str) -> None: ...

    def warn(self, text: str) -> None: ...

    def busy(self, text: str) -> AbstractContextManager[object]:
        """A spinner saying `text` while Kiln is called. No prompt runs
        inside it."""
        ...

    def pick(
        self,
        question: str,
        options: Sequence[Option[T]],
        *,
        hint: str | None = None,
        default: T | None = None,
    ) -> T:
        """One option's value. The rows are `aligned_rows`, and `hint`
        replaces `PICK_HINT`. The cursor starts on `default`'s option, else
        on the first one that can be picked. Ctrl-C raises
        `OnboardingStopped(130)`, and Ctrl-D `OnboardingStopped(1)`, as at
        every prompt."""
        ...

    def act(self, question: str, actions: Sequence[Action]) -> Action: ...

    def text(
        self,
        question: str,
        *,
        default: str = "",
        hint: str | None = None,
        validate: Callable[[str], str | None] | None = None,
    ) -> str:
        """A typed answer, asked again while `validate` returns an error."""
        ...

    def open_url(self, url: str) -> None: ...


def _retry(label: str) -> Action:
    return Action("r", label, ActionKind.RETRY)


EXIT_ACTION = Action("x", "Exit", ActionKind.EXIT)


@dataclass(frozen=True)
class Problem:
    """A failed check: what is wrong and how to fix it, and the actions
    that help, besides Check again and Exit.

    `retry_first` puts Check again at the top of the menu. A problem whose
    fix is one of its own actions (make a project, use the demo) leads with
    those instead. `warnings` are printed after the block, as a check that
    passes prints its own after its step line."""

    title: str
    body: tuple[str, ...]
    actions: tuple[Action, ...] = ()
    retry_label: str = "Check again"
    retry_first: bool = True
    warnings: tuple[str, ...] = ()

    def menu(self, extra: Sequence[Action] = ()) -> tuple[Action, ...]:
        """The action menu, with `extra` (the caller's own actions) last
        before Exit."""
        retry = _retry(self.retry_label)
        if self.retry_first:
            return (retry, *self.actions, *extra, EXIT_ACTION)
        return (*self.actions, retry, *extra, EXIT_ACTION)

    def error(self) -> OnboardingError:
        """The problem for a start with no one to ask. With no menu to open
        a page from, each page an Open action would open is named, unless
        the body already gives its address."""
        links = tuple(
            f"{action.label}: {action.url}"
            for action in self.actions
            if action.kind is ActionKind.OPEN
            and action.url is not None
            and not any(action.url in line for line in self.body)
        )
        return OnboardingError(self.title, (*self.body, *links))


def resolve_problem(
    check: Callable[[], Problem | None],
    terminal: Terminal,
    *,
    busy: str,
    extra: Sequence[Action] = (),
) -> Action | None:
    """Run `check`, with a spinner saying `busy`, until it finds no problem,
    and return `None`.

    A start that isn't guided prints the first problem's warnings, then
    raises the problem as an `OnboardingError`. A guided start shows each
    problem as a block, with its warnings, then asks what next: Check again
    runs `check` again (nothing polls), an Open action opens its page and
    asks again, and Exit stops with exit 1. Any other action, the problem's
    own or one of `extra`, is returned for the caller to act on."""
    while True:
        with terminal.busy(busy):
            problem = check()
        if problem is None:
            return None
        if not terminal.interactive:
            for warning in problem.warnings:
                terminal.warn(warning)
            raise problem.error()
        terminal.block(problem.title, problem.body)
        for warning in problem.warnings:
            terminal.warn(warning)
        action = _ask_what_next(terminal, problem.menu(extra))
        if action.kind is not ActionKind.RETRY:
            return action


def _ask_what_next(terminal: Terminal, menu: Sequence[Action]) -> Action:
    """The chosen action, after opening any pages asked for on the way."""
    while True:
        action = terminal.act(WHAT_NEXT, menu)
        if action.kind is ActionKind.EXIT:
            raise OnboardingStopped(1)
        if action.kind is not ActionKind.OPEN:
            return action
        assert action.url is not None
        terminal.open_url(action.url)


def tilde(path: str | Path, home: Path | None = None) -> str:
    """`path` with the home folder shown as `~`."""
    home = Path.home() if home is None else home
    try:
        rest = Path(path).relative_to(home)
    except ValueError:
        return str(path)
    return "~" if rest == Path() else f"~/{rest}"


def english_list(items: Sequence[str]) -> str:
    """`a`, `a and b`, `a, b and c`."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def plural(count: int, noun: str, nouns: str | None = None) -> str:
    """`1 eval`, `2 evals`."""
    return f"{count} {noun if count == 1 else nouns or noun + 's'}"
