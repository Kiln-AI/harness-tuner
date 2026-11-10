"""The two real terminals: `RichTerminal` for a guided start, and
`PlainTerminal` for a start with no one to ask.

The guided terminal writes with rich and asks with questionary. Each
questionary prompt runs its own prompt_toolkit loop while no asyncio loop is
running, which is why the start-up flow is synchronous.
"""

from __future__ import annotations

import string
import sys
import webbrowser
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import Any, TextIO, TypeVar, cast

import questionary
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent, merge_key_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import Output
from questionary.prompts.common import InquirerControl
from rich.box import ROUNDED
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from kiln_harness_tuner.onboarding.errors import OnboardingStopped
from kiln_harness_tuner.onboarding.terminal import (
    ENTER,
    PICK_HINT,
    Action,
    Option,
    StepStatus,
    Terminal,
    aligned_rows,
)
from kiln_harness_tuner.tui.opener import graphical_browser_expected

T = TypeVar("T")

# The `primary` colour of Kiln's web UI (its tailwind config).
KILN_BLUE = "#415CF5"
WELCOME_MAX_WIDTH = 68
LABEL_WIDTH = 9
SYMBOLS: dict[StepStatus, tuple[str, str]] = {
    "ok": ("✓", "green"),
    "fail": ("✗", "red"),
    "warn": ("!", "yellow"),
}


def _key_name(action: Action) -> str:
    return "Enter" if action.key == ENTER else action.key


def _exit_with(result: int) -> Callable[[KeyPressEvent], None]:
    def handler(event: KeyPressEvent) -> None:
        event.app.exit(result=result)

    return handler


def _stop_on_ctrl_d(event: KeyPressEvent) -> None:
    event.app.exit(exception=EOFError())


def _inquirer_control(prompt: questionary.Question) -> InquirerControl:
    """The list a questionary select draws, which holds its cursor and its
    filter."""
    for window in prompt.application.layout.find_all_windows():
        if isinstance(window.content, InquirerControl):
            return window.content
    raise RuntimeError("questionary's select has no list")


def _pick_bindings(prompt: questionary.Question) -> KeyBindings:
    """Enter, the arrow keys and the filter keys for a picker, over
    questionary's own. With the search filter on, questionary 2.1 lets Enter
    pick a disabled row, picks the first row when the filter matches
    nothing, skips disabled rows by their place in the unfiltered list, and
    puts the cursor on the filtered list's first row even when that row is
    disabled."""
    control = _inquirer_control(prompt)
    keys = KeyBindings()

    def filter_by(event: KeyPressEvent) -> None:
        # Typed as `Keys`, but questionary's own filter passes the typed
        # character too, as this does.
        control.add_search_character(cast(Keys, event.key_sequence[0].key))
        rows = control.filtered_choices
        enabled = (i for i, row in enumerate(rows) if not row.disabled)
        control.pointed_at = next(enabled, 0)

    # The keys questionary's own filter reads.
    for key in (*string.printable, Keys.Backspace):
        keys.add(key, eager=True)(filter_by)

    @keys.add(Keys.ControlM, eager=True)
    def pick_the_pointed_row(event: KeyPressEvent) -> None:
        rows = control.filtered_choices  # also updates `found_in_search`
        if control.search_filter and not control.found_in_search:
            return  # nothing matches; the list shown is the unfiltered one
        row = rows[control.pointed_at]
        if row.disabled:
            return
        control.is_answered = True
        event.app.exit(result=row.value)

    def move(step: int) -> Callable[[KeyPressEvent], None]:
        def to_the_next_enabled_row(event: KeyPressEvent) -> None:
            rows = control.filtered_choices
            for _ in rows:
                control.pointed_at = (control.pointed_at + step) % len(rows)
                if not rows[control.pointed_at].disabled:
                    return

        return to_the_next_enabled_row

    for key in (Keys.Down, Keys.ControlN):
        keys.add(key, eager=True)(move(1))
    for key in (Keys.Up, Keys.ControlP):
        keys.add(key, eager=True)(move(-1))
    return keys


class RichTerminal:
    """The guided start's terminal. `console`, `pt_input` and `pt_output`
    are for tests; by default rich and prompt_toolkit use the real stdout
    and stdin, and turn colour off by their own rules (`NO_COLOR`, or a
    stream that isn't a terminal)."""

    interactive = True

    def __init__(
        self,
        prog: str,
        *,
        console: Console | None = None,
        pt_input: Input | None = None,
        pt_output: Output | None = None,
    ):
        self.console = console if console is not None else Console(highlight=False)
        self._io: dict[str, Any] = {"erase_when_done": True}
        if pt_input is not None:
            self._io["input"] = pt_input
        if pt_output is not None:
            self._io["output"] = pt_output

    def _print(self, text: str | Text = "") -> None:
        # soft_wrap: the terminal wraps long lines itself, so a printed
        # command copies out whole, with no newline rich would insert.
        # No markup and no emoji codes: names, paths and guidance print as typed.
        self.console.print(
            text, soft_wrap=True, markup=False, highlight=False, emoji=False
        )

    def welcome(self, title: str, version: str, description: str) -> None:
        title_row = Table.grid(expand=True)
        title_row.add_column()
        title_row.add_column(justify="right")
        title_row.add_row(
            Text(title, style=f"bold {KILN_BLUE}"), Text(f"v{version}", style="dim")
        )
        self.console.print(
            Panel(
                Group(title_row, Text(), Text(description)),
                box=ROUNDED,
                padding=(0, 1),
                width=min(self.console.width, WELCOME_MAX_WIDTH),
                border_style=KILN_BLUE,
            )
        )
        self._print()

    def step(self, status: StepStatus, label: str, text: str) -> None:
        symbol, colour = SYMBOLS[status]
        self._print(Text.assemble((symbol, colour), f" {label:<{LABEL_WIDTH}} {text}"))

    def block(self, title: str, body: Sequence[str]) -> None:
        self._print()
        self._print(Text(f"✗ {title}", style="bold red"))
        self._print()
        for line in body:
            self._print(f"  {line}")
        self._print()

    def say(self, text: str) -> None:
        self._print(text)

    def warn(self, text: str) -> None:
        # To stdout, beside the step lines: the guided start is all on
        # stdout. A console on stderr would also write past the spinner's
        # redirect of stdout and stderr, and break the spinner's line.
        symbol, colour = SYMBOLS["warn"]
        self._print(Text.assemble((symbol, colour), f" {text}"))

    def busy(self, text: str) -> AbstractContextManager[object]:
        # A str would be read as rich markup, and the text holds Kiln's names.
        return self.console.status(Text(text), spinner="dots")

    def _ask(self, question: questionary.Question, *bindings: KeyBindings) -> Any:
        """The answer to one prompt. Ctrl-C stops with 130 and Ctrl-D with 1.
        questionary's lists swallow Ctrl-D, and its text field deletes a
        character with it, so an eager binding makes it stop everywhere."""
        ctrl_d = KeyBindings()
        ctrl_d.add(Keys.ControlD, eager=True)(_stop_on_ctrl_d)
        app = question.application
        own = [app.key_bindings] if app.key_bindings is not None else []
        app.key_bindings = merge_key_bindings([*own, ctrl_d, *bindings])
        try:
            return question.unsafe_ask()
        except KeyboardInterrupt:
            raise OnboardingStopped(130) from None
        except EOFError:
            raise OnboardingStopped(1) from None

    def pick(
        self,
        question: str,
        options: Sequence[Option[T]],
        *,
        hint: str | None = None,
        default: T | None = None,
    ) -> T:
        # The filter matches the whole row text, so typing an ID works.
        choices = [
            questionary.Choice(
                title=row,
                value=i,
                disabled=option.disabled,
                description=option.description,
                shortcut_key=False,
            )
            for i, (row, option) in enumerate(
                zip(aligned_rows(options), options, strict=True)
            )
        ]
        # questionary refuses a default it can't point at.
        start = next(
            (
                choice
                for choice, option in zip(choices, options, strict=True)
                if default is not None
                and option.value == default
                and option.disabled is None
            ),
            None,
        )
        prompt = questionary.select(
            question,
            choices=choices,
            default=start,
            use_search_filter=True,
            use_jk_keys=False,
            instruction=hint or PICK_HINT,
            **self._io,
        )
        return options[self._ask(prompt, _pick_bindings(prompt))].value

    def act(self, question: str, actions: Sequence[Action]) -> Action:
        # The keys are drawn here, not by questionary's shortcuts, which must
        # be single characters and only move the cursor: `Enter)` is a key
        # name, and each letter acts at once.
        assert all(action.key != ENTER for action in actions[1:]), (
            "Enter picks the first action"
        )
        choices = [
            questionary.Choice(title=f"{_key_name(action)}) {action.label}", value=i)
            for i, action in enumerate(actions)
        ]
        prompt = questionary.select(
            question,
            choices=choices,
            use_jk_keys=False,
            # An empty instruction gets questionary's own "(Use arrow keys)";
            # the keys in front of each action say it.
            instruction=" ",
            **self._io,
        )
        # Eager bindings, merged over questionary's own: a letter acts at
        # once with no Enter. Enter picks the action the cursor is on, which
        # starts on the first.
        keys = KeyBindings()
        for i, action in enumerate(actions):
            if action.key != ENTER:
                keys.add(action.key, eager=True)(_exit_with(i))
        return actions[self._ask(prompt, keys)]

    def text(
        self,
        question: str,
        *,
        default: str = "",
        hint: str | None = None,
        validate: Callable[[str], str | None] | None = None,
    ) -> str:
        def verdict(answer: str) -> bool | str:
            error = validate(answer) if validate is not None else None
            return True if error is None else error

        prompt = questionary.text(
            question, default=default, instruction=hint, validate=verdict, **self._io
        )
        return self._ask(prompt)

    def open_url(self, url: str) -> None:
        if graphical_browser_expected():
            self._print(f"Opening {url}")
            webbrowser.open(url)
        else:
            self._print(f"Open this link in your browser: {url}")


class PlainTerminal:
    """A start with no one to ask: plain lines on stdout, warnings on
    stderr, no colour, no spinners, and never a prompt."""

    interactive = False

    def __init__(self, prog: str):
        self.prog = prog

    def welcome(self, title: str, version: str, description: str) -> None:
        pass

    def step(self, status: StepStatus, label: str, text: str) -> None:
        print(f"{label:<{LABEL_WIDTH}} {text}")

    def say(self, text: str) -> None:
        print(text)

    def warn(self, text: str) -> None:
        print(f"{self.prog}: {text}", file=sys.stderr)

    def busy(self, text: str) -> AbstractContextManager[object]:
        return nullcontext()

    # A start that isn't guided fails each problem with its message
    # (`resolve_problem`), so nothing ever asks or shows a menu here.

    def block(self, title: str, body: Sequence[str]) -> None:
        raise RuntimeError("not a guided start")

    def pick(
        self,
        question: str,
        options: Sequence[Option[T]],
        *,
        hint: str | None = None,
        default: T | None = None,
    ) -> T:
        raise RuntimeError("not a guided start")

    def act(self, question: str, actions: Sequence[Action]) -> Action:
        raise RuntimeError("not a guided start")

    def text(
        self,
        question: str,
        *,
        default: str = "",
        hint: str | None = None,
        validate: Callable[[str], str | None] | None = None,
    ) -> str:
        raise RuntimeError("not a guided start")

    def open_url(self, url: str) -> None:
        raise RuntimeError("not a guided start")


def _is_tty(stream: TextIO | None) -> bool:
    return stream is not None and stream.isatty()


def default_terminal(prog: str, *, no_interaction: bool) -> Terminal:
    """The guided terminal when someone is there to answer: stdin and stdout
    are both terminals, and `--no-interaction` wasn't given."""
    if _is_tty(sys.stdin) and _is_tty(sys.stdout) and not no_interaction:
        return RichTerminal(prog)
    return PlainTerminal(prog)
