"""The experiment details panel and the experiment picker (functional spec
7.9, ui_design 4.13).

Details that do not fit the overview open in a modal panel over the run
screen. The app shows the run model on every open panel as it does on the
run screen (`TunerApp._refresh`), so a panel updates live and keeps its
scroll position. Esc closes the panel, or goes back to the picker it was
opened from; Esc closes the picker. A click outside a modal does what its
Esc does (`CardModal`). `q` and Ctrl+C open the stop dialog on top, as on
the run screen.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from rich.cells import cell_len
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.content import Content
from textual.widget import Widget
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from kiln_harness_tuner.tui.model import words
from kiln_harness_tuner.tui.model.fold import RunModel
from kiln_harness_tuner.tui.model.state import (
    DetailLink,
    DetailsView,
    ItemKey,
    Line,
    PickerGroup,
    PickerRow,
)
from kiln_harness_tuner.tui.screens.modal import CardModal
from kiln_harness_tuner.tui.widgets import (
    SPIN_SECS,
    SPINS,
    Card,
    Drawn,
    LineView,
    card_title,
    has_spinner,
    line_content,
    link,
)

if TYPE_CHECKING:
    from kiln_harness_tuner.tui.app import TunerApp

logger = logging.getLogger("kiln_harness_tuner.tui")

# The gap between the panel's links.
LINK_GAP = "    "
# The picker's title column, at most: the item id and its card title words.
PICKER_TITLE_WIDTH = 44


# `q` opens the stop dialog while the run goes on, as on the run screen. On
# Done it does nothing in a modal, so it never closes the app from one.
STOP_BINDING = Binding("q", "stop", words.STOP_KEY)


class RunModal(CardModal[None]):
    """A modal screen over the run screen that shows the run model: first
    once its widgets are mounted, then each time the app shows the run
    (`show`)."""

    def __init__(self) -> None:
        super().__init__()
        self.ready = False

    @property
    def tuner(self) -> TunerApp:
        return cast("TunerApp", self.app)

    def compose_body(self) -> ComposeResult:
        raise NotImplementedError

    def compose(self) -> ComposeResult:
        yield from self.compose_body()
        # A modal screen has its own bindings, so it needs its own footer.
        yield Footer()

    def on_mount(self) -> None:
        # Textual calls each class's `on_mount`: a subclass's does not call
        # this one. The screen mounts before its widgets have.
        self.set_interval(SPIN_SECS, self.spin)
        self.call_after_refresh(self._first_show)

    def _first_show(self) -> None:
        self.ready = True
        model = self.tuner.model
        if model is None:
            return
        # As `TunerApp._refresh` does: a view that fails is logged, and the
        # app stays open.
        try:
            self.show(model, now=self.tuner.now())
        except Exception:
            logger.exception("a details panel could not show the run")

    def show(self, model: RunModel, *, now: float) -> None:
        if self.ready:
            self.refresh_from(model, now=now)

    def refresh_from(self, model: RunModel, *, now: float) -> None:
        raise NotImplementedError

    def spin(self) -> None:
        for widget in self.query(f".{SPINS}"):
            if isinstance(widget, Drawn):
                widget.spin()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "stop":
            return self.tuner.phase != "done"
        return True

    def action_stop(self) -> None:
        self.tuner.action_ask_stop()


# ---- the details panel ----


class FieldRow(Horizontal):
    """A labelled field: a value of several lines stays under its own text."""

    def __init__(self, label: str, lines: Sequence[Line]) -> None:
        self.value = Static(classes="detail-value")
        super().__init__(
            Static(Content.styled(label, "$text-muted"), classes="detail-label"),
            self.value,
            classes="detail-field",
        )
        self.lines: tuple[Line, ...] | None = None
        self.show(lines)

    def show(self, lines: Sequence[Line]) -> None:
        if tuple(lines) == self.lines:
            return
        self.lines = tuple(lines)
        self.value.update(Content("\n").join(line_content(line) for line in lines))


class BulletRow(Horizontal):
    """A list line: it wraps under its own text, not under the bullet."""

    def __init__(self, line: Line) -> None:
        self.text = LineView(line, classes="detail-bullet-text")
        super().__init__(
            Static(Content.styled(words.LIST_BULLET, "$text-muted")),
            self.text,
            classes="detail-bullet",
        )

    def show(self, line: Line) -> None:
        self.text.show(line)


# The body's rows, as the panel builds them: what each row is and what it
# says. The same shape as before updates the rows in place.
Row = tuple[str, str, object]


def body_rows(view: DetailsView) -> list[Row]:
    rows: list[Row] = [("field", field.label, field.lines) for field in view.fields]
    for section in view.sections:
        rows.append(("title", section.title, section.title))
        for line in section.lines:
            rows.append(("bullet" if line.bullet else "line", "", line.line))
    return rows


def row_widget(kind: str, label: str, value: object) -> Widget:
    if kind == "field":
        return FieldRow(label, cast("Sequence[Line]", value))
    if kind == "title":
        return Static(Content.styled(label, "bold"), classes="detail-title")
    if kind == "bullet":
        return BulletRow(cast("Line", value))
    return LineView(cast("Line", value), classes="detail-line")


def show_row(widget: Widget, value: object) -> None:
    if isinstance(widget, (FieldRow, BulletRow, LineView)):
        widget.show(value)


def links_content(found: Sequence[DetailLink]) -> Content:
    return Content(LINK_GAP).join(link(item.url, item.text) for item in found)


class DetailsBody(VerticalScroll, can_focus=False):
    """The panel's middle, which scrolls: the fields, then the sections. The
    panel's keys scroll it, so the footer shows them in the panel's order."""


class DetailsPanel(RunModal):
    """One item in full (ui_design 4.13): the status line on top and the
    links at the bottom stay in place; the middle scrolls, so long text
    works."""

    BINDINGS = [
        Binding("escape", "close", words.CLOSE_KEY),
        Binding("escape", "back", words.BACK_KEY),
        Binding("up", "scroll_body(-1)", words.SCROLL_KEY, key_display="↑↓"),
        Binding("down", "scroll_body(1)", show=False),
        Binding("pageup", "page_body(-1)", show=False),
        Binding("pagedown", "page_body(1)", show=False),
        STOP_BINDING,
    ]

    def __init__(self, key: ItemKey, *, from_picker: bool = False) -> None:
        super().__init__()
        self.key = key
        self.from_picker = from_picker
        self.status = LineView(classes="detail-status")
        self.body = DetailsBody(classes="detail-body")
        self.links = Static(classes="detail-links")
        self.view: DetailsView | None = None
        self._shape: list[tuple[str, str]] = []

    def compose_body(self) -> ComposeResult:
        with Card(id="details"):
            yield self.status
            yield self.body
            yield self.links

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "close":
            return not self.from_picker
        if action == "back":
            return self.from_picker
        return super().check_action(action, parameters)

    def action_close(self) -> None:
        self.dismiss()

    def action_back(self) -> None:
        self.dismiss()

    def action_scroll_body(self, lines: int) -> None:
        self.body.scroll_relative(y=lines, animate=False)

    def action_page_body(self, pages: int) -> None:
        self.body.scroll_relative(
            y=pages * self.body.scrollable_content_region.height, animate=False
        )

    def refresh_from(self, model: RunModel, *, now: float) -> None:
        view = model.details(self.key, now)
        if view is None or view == self.view:
            return
        old, self.view = self.view, view
        panel = self.query_one("#details", Card)
        if old is None or (view.title, view.qualifier) != (old.title, old.qualifier):
            panel.border_title = card_title(view.title, view.qualifier)
        self.status.show(view.status)
        self._show_body(view)
        if old is None or view.links != old.links:
            self.links.update(links_content(view.links))
            self.links.display = bool(view.links)

    def _show_body(self, view: DetailsView) -> None:
        """The rows in place while their shape holds. A new shape mounts them
        again, at the same scroll position."""
        rows = body_rows(view)
        shape = [(kind, label) for kind, label, _value in rows]
        shown = list(self.body.children)
        if shape == self._shape and len(shown) == len(rows):
            for widget, (_kind, _label, value) in zip(shown, rows, strict=True):
                show_row(widget, value)
            return
        self._shape = shape
        scrolled = self.body.scroll_y
        self.body.remove_children()
        self.body.mount(*(row_widget(*row) for row in rows))
        if scrolled:
            self.body.call_after_refresh(self.body.scroll_to, y=scrolled, animate=False)


# ---- the picker ----


def option_id(key: ItemKey) -> str:
    return f"{key[0]}/{key[1]}"


class PickerList(OptionList):
    """Textual's `OptionList`, with its keys in the footer."""

    BINDINGS = [
        Binding("up", "cursor_up", words.MOVE_KEY, key_display="↑↓"),
        Binding("down", "cursor_down", show=False),
        Binding("enter", "select", words.OPEN_KEY),
    ]


def row_content(row: PickerRow, width: int) -> Content:
    """The item id in bold and its card title words, in a column, then its
    status."""
    name = Content.assemble((row.title, "bold"), (f" {row.qualifier}", "$text-muted"))
    if name.cell_length > width:
        name = name.truncate(width, ellipsis=True)
    return Content.assemble(
        name.pad_right(width - name.cell_length), "  ", line_content(row.status)
    )


class ExperimentPicker(RunModal):
    """Every item of the run, under its wave's header, newest wave first and
    recon last. ↑↓ move, Enter opens the item's panel, Esc closes."""

    BINDINGS = [Binding("escape", "close", words.CLOSE_KEY), STOP_BINDING]

    def __init__(self) -> None:
        super().__init__()
        self.options = PickerList(id="picker-list")
        self.groups: tuple[PickerGroup, ...] | None = None
        self.rows: dict[str, PickerRow] = {}
        self._width = 0

    def compose_body(self) -> ComposeResult:
        with Card(title=Content.styled(words.PICKER_TITLE, "bold"), id="picker"):
            yield self.options

    def on_mount(self) -> None:
        self.options.focus()

    def action_close(self) -> None:
        self.dismiss()

    def refresh_from(self, model: RunModel, *, now: float) -> None:
        groups = model.picker()
        if groups == self.groups:
            return
        old, self.groups = self.groups, groups
        rows = [row for group in groups for row in group.rows]
        # In terminal cells: a qualifier names an eval of the person's project.
        self._width = min(
            max((cell_len(f"{row.title} {row.qualifier}") for row in rows), default=0),
            PICKER_TITLE_WIDTH,
        )
        shape = [
            (group.header, tuple(option_id(row.key) for row in group.rows))
            for group in groups
        ]
        old_shape = [
            (group.header, tuple(option_id(row.key) for row in group.rows))
            for group in old or ()
        ]
        self.rows = {option_id(row.key): row for row in rows}
        if shape == old_shape:
            for row in rows:
                self._draw(row)
            return
        highlighted = self.options.highlighted_option
        keep = highlighted.id if highlighted is not None else None
        options: list[Option] = []
        for index, group in enumerate(groups):
            if index:
                options.append(Option("", disabled=True))
            header = Content.styled(group.header, "bold $text-muted")
            options.append(Option(header, disabled=True))
            options += [
                Option(row_content(row, self._width), id=option_id(row.key))
                for row in group.rows
            ]
        self.options.set_options(options)
        if keep in self.rows:
            self.options.highlighted = self.options.get_option_index(keep)
        else:
            self.options.action_first()

    def _draw(self, row: PickerRow) -> None:
        self.options.replace_option_prompt(
            option_id(row.key), row_content(row, self._width)
        )

    def spin(self) -> None:
        for row in self.rows.values():
            if has_spinner(row.status):
                self._draw(row)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        row = self.rows.get(event.option.id or "")
        if row is not None:
            self.tuner.show_item(row.key, from_picker=True)
