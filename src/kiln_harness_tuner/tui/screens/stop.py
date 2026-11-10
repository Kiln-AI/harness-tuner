"""The stop dialog (functional spec section 9, ui_design sections 4.11, 9)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, Footer, Static

from kiln_harness_tuner.tui.model import words
from kiln_harness_tuner.tui.screens.modal import CardModal
from kiln_harness_tuner.tui.widgets import Card


class StopDialog(CardModal[bool]):
    """ "Stop tuning?" over the dimmed run screen, with what is lost.
    "Keep tuning", the safe choice, has the focus. Esc, or a click outside
    the dialog, keeps tuning. The result is True for "Stop tuning"."""

    BINDINGS = [
        Binding("escape", "keep", words.KEEP_TUNING),
        Binding("left", "app.focus_previous", "Move", show=False),
        Binding("right", "app.focus_next", "Move", show=False),
    ]

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Card(title=f"[b]{words.STOP_TITLE}[/]", id="dialog"):
            yield Static(self.message, markup=False, id="dialog-message")
            with Horizontal(id="dialog-buttons"):
                yield Button(words.KEEP_TUNING, id="keep", compact=True)
                yield Button(
                    words.STOP_TUNING, id="stop", compact=True, variant="error"
                )
        # A modal screen has its own bindings, so it needs its own footer.
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#keep", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "stop")

    def action_keep(self) -> None:
        self.dismiss(False)
