"""The start-up's three prompts (ui_design section 8): the action menu, the
picker and the text answer.

They keep the rules of functional spec section 5 ("Interaction behaviors to
keep"), which the questionary prompts kept before them. Each one answers
once: it posts `Answered` with the value, or `Failed` with an error for the
start-up thread to raise. The start-up screen then removes it.

Ctrl+C and Ctrl+D are the app's priority bindings, so they work in every
prompt, a focused `Input` included. A prompt asked with `back` also takes
Esc, which fails it with `GoBack`: the question before it is asked again. In
a picker with filter text, Esc first clears the filter.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Generic, TypeVar

from rich.cells import cell_len
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.content import Content
from textual.events import Key
from textual.message import Message
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option as Row

from kiln_harness_tuner.onboarding.errors import GoBack
from kiln_harness_tuner.onboarding.terminal import (
    COLUMN_GAP,
    ENTER,
    PICK_HINT,
    Action,
    aligned_rows,
)
from kiln_harness_tuner.tui.bridge import Act, Pick, Prompt, Text

P = TypeVar("P", bound=Prompt[Any])


# The app's stop keys. A prompt's input has none of its own on them, so the
# footer shows them in the app's order: `^c Stop`, then `^d Exit`.
_APP_KEYS = frozenset({"ctrl+c", "ctrl+d"})


def _without_app_keys(bindings: list[Any]) -> list[BindingType]:
    kept: list[BindingType] = []
    for binding in bindings:
        keys = [key for key in binding.key.split(",") if key not in _APP_KEYS]
        if keys:
            kept.append(dataclasses.replace(binding, key=",".join(keys)))
    return kept


class PromptInput(Input, inherit_bindings=False):
    """An `Input` without its own Ctrl+C (copy) and Ctrl+D (delete): the
    app's priority bindings take those keys anyway. Its base classes' keys
    only scroll, which a one-line input does not."""

    BINDINGS = _without_app_keys(Input.BINDINGS)


def key_name(action: Action) -> str:
    """How a menu row names its key: `Enter)` for the Enter action."""
    return "Enter" if action.key == ENTER else action.key


def _question(text: str) -> Static:
    return Static(Content.styled(text, "bold"), classes="question")


def _hint(text: str) -> Static:
    return Static(Content.styled(text, "$text-muted"), classes="hint")


class PromptPart(Vertical, Generic[P]):
    """One question in the transcript, answered once."""

    BINDINGS = [Binding("escape", "back", "Back")]

    class Answered(Message):
        def __init__(self, part: PromptPart[Any], value: Any) -> None:
            super().__init__()
            self.part = part
            self.value = value

    class Failed(Message):
        def __init__(self, part: PromptPart[Any], error: BaseException) -> None:
            super().__init__()
            self.part = part
            self.error = error

    def __init__(self, request: P) -> None:
        super().__init__(classes="prompt")
        self.request = request
        self.done = False

    def answer(self, value: Any) -> None:
        if not self.done:
            self.done = True
            self.post_message(self.Answered(self, value))

    def fail(self, error: BaseException) -> None:
        if not self.done:
            self.done = True
            self.post_message(self.Failed(self, error))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Esc, and its footer key, only where the question has one before it.
        if action == "back":
            return self.request.back
        return True

    def action_back(self) -> None:
        self.fail(GoBack())


class ActionMenu(PromptPart[Act]):
    """The question, then one row per action, "key) label". A letter that
    one action has acts at once, with no Enter. The arrows and Enter work
    too, from the first row. Any other key is ignored."""

    def __init__(self, request: Act) -> None:
        super().__init__(request)
        self.keys = {
            action.key: action for action in request.actions if action.key != ENTER
        }

    def compose(self) -> ComposeResult:
        yield _question(self.request.question)
        yield OptionList(
            *(
                Row(Content(f" {key_name(action)}) {action.label} "), id=str(i))
                for i, action in enumerate(self.request.actions)
            ),
            classes="menu",
        )

    def on_mount(self) -> None:
        menu = self.query_one(OptionList)
        menu.highlighted = 0
        menu.focus()

    def on_key(self, event: Key) -> None:
        action = self.keys.get(event.character or "")
        if action is not None:
            event.stop()
            event.prevent_default()
            self.answer(action)

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.answer(self.request.actions[int(event.option.id or 0)])


class Picker(PromptPart[Pick]):
    """The question, a filter, the rows with aligned columns, the
    highlighted row's description and the hint.

    Typing filters the rows on their whole text, ids included. A disabled
    row is muted with its reason: it can't be highlighted, so the arrows
    skip it and Enter never picks it. The cursor starts on the default row
    when it can be picked, and goes to the first row that can be picked
    each time the filter changes. While the filter matches nothing, Enter
    does nothing."""

    BINDINGS = [
        Binding("up", "move(-1)", show=False),
        Binding("down", "move(1)", show=False),
        # One Esc key, two actions: `check_action` enables one at a time, so
        # the footer names what Esc does now.
        Binding("escape", "clear_filter", "Clear filter"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self, request: Pick) -> None:
        super().__init__(request)
        self.rows = aligned_rows(request.options)
        width = max(cell_len(row) for row in self.rows)
        # A disabled row's reason sits in one column after the widest row.
        self.prompts = [
            Content(
                f" {row}{' ' * (width - cell_len(row))}{COLUMN_GAP}{option.disabled} "
                if option.disabled is not None
                else f" {row} "
            )
            for row, option in zip(self.rows, request.options, strict=True)
        ]
        self.filter_text = ""

    def compose(self) -> ComposeResult:
        yield _question(self.request.question)
        yield PromptInput(placeholder="type to filter", classes="filter")
        menu = OptionList(classes="menu")
        # The filter keeps the focus, so typing always filters.
        menu.can_focus = False
        yield menu
        yield Static(classes="description")
        yield _hint(self.request.hint or PICK_HINT)

    def on_mount(self) -> None:
        self._show("", start=self._default_index())
        self.query_one(Input).focus()

    def _default_index(self) -> int | None:
        default = self.request.default
        if default is None:
            return None
        return next(
            (
                i
                for i, option in enumerate(self.request.options)
                if option.value == default and option.disabled is None
            ),
            None,
        )

    def _show(self, query: str, *, start: int | None = None) -> None:
        """The rows the filter keeps, with the cursor on `start` when it is
        kept, else on the first row that can be picked."""
        menu = self.query_one(OptionList)
        menu.clear_options()
        wanted = query.casefold()
        options = self.request.options
        for i, (row, option) in enumerate(zip(self.rows, options, strict=True)):
            if wanted in row.casefold():
                disabled = option.disabled is not None
                menu.add_option(Row(self.prompts[i], id=str(i), disabled=disabled))
        shown = [int(row.id or 0) for row in menu.options]
        enabled = [p for p, row in enumerate(menu.options) if not row.disabled]
        if start is not None and start in shown:
            menu.highlighted = shown.index(start)
        else:
            menu.highlighted = enabled[0] if enabled else None
        self._describe()

    def _describe(self) -> None:
        row = self.query_one(OptionList).highlighted_option
        option = self.request.options[int(row.id or 0)] if row is not None else None
        text = option.description if option is not None else None
        self.query_one(".description", Static).update(Content(text or ""))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Esc clears the filter first, also where it can't go back.
        if action == "clear_filter":
            return bool(self.filter_text)
        if action == "back":
            return not self.filter_text and self.request.back
        return True

    def action_clear_filter(self) -> None:
        self.query_one(Input).value = ""

    def action_move(self, step: int) -> None:
        menu = self.query_one(OptionList)
        if step > 0:
            menu.action_cursor_down()
        else:
            menu.action_cursor_up()

    @on(Input.Changed)
    def _filter(self, event: Input.Changed) -> None:
        event.stop()
        if event.value != self.filter_text:
            self.filter_text = event.value
            self._show(event.value)
            self.refresh_bindings()

    @on(OptionList.OptionHighlighted)
    def _highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        self._describe()

    @on(Input.Submitted)
    def _submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._pick(self.query_one(OptionList).highlighted_option)

    @on(OptionList.OptionSelected)
    def _clicked(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self._pick(event.option)

    def _pick(self, row: Row | None) -> None:
        if row is None or row.disabled:
            return
        self.answer(self.request.options[int(row.id or 0)].value)


class TextAnswer(PromptPart[Text]):
    """The question, an input with the default in it, the validator's
    message in red under it, and the hint. Enter accepts the default. An
    answer the validator refuses shows its message and keeps the text, until
    the next Enter; an edit clears the message."""

    def compose(self) -> ComposeResult:
        yield _question(self.request.question)
        # Not selected on focus: typing goes after the default, and
        # Backspace deletes from its end, as in the terminal before.
        yield PromptInput(self.request.default, select_on_focus=False, classes="answer")
        yield Static(classes="error")
        if self.request.hint:
            yield _hint(self.request.hint)

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def _refuse(self, message: str) -> None:
        self.query_one(".error", Static).update(Content.styled(message, "$text-error"))
        self.query_one(Input).set_class(bool(message), "-invalid")

    @on(Input.Changed)
    def _edited(self, event: Input.Changed) -> None:
        event.stop()
        self._refuse("")

    @on(Input.Submitted)
    def _submitted(self, event: Input.Submitted) -> None:
        event.stop()
        validate = self.request.validate
        try:
            refusal = validate(event.value) if validate is not None else None
        except Exception as error:
            # The validator's own fault: the start-up thread raises it, as
            # the flow would on the main thread.
            self.fail(error)
            return
        if refusal:
            self._refuse(refusal)
        else:
            self.answer(event.value)
