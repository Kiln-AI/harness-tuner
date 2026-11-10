"""The run screen (functional spec sections 6 and 7, ui_design section 7).

For now it shows the run as plain text: the title, the Experiments header
and the newest log lines, from the views of `tui/model`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Static

from kiln_harness_tuner.tui.model import words
from kiln_harness_tuner.tui.model.fold import RunModel
from kiln_harness_tuner.tui.model.state import LogLine, plain
from kiln_harness_tuner.tui.widgets import TITLE_MARKUP

if TYPE_CHECKING:
    from kiln_harness_tuner.tui.app import TunerApp

LOG_LINES = 40


def log_text(line: LogLine) -> str:
    if line.divider:
        return f"── {line.text} ──"
    return f"{line.time} {line.glyph} {line.text}"


class RunScreen(Screen[None]):
    BINDINGS = [
        Binding("q", "stop", "Stop"),
        Binding("q", "close", "Close"),
        Binding("enter", "close", "Close", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        # The text each part shows now. `Static.update` always repaints, so
        # an unchanged part is not updated (research section 11).
        self._shown: dict[str, str] = {}

    @property
    def tuner(self) -> TunerApp:
        return cast("TunerApp", self.app)

    def compose(self) -> ComposeResult:
        yield Static(TITLE_MARKUP, classes="title-bar")
        yield Static(id="run-title", markup=False)
        yield Static(id="run-mode", markup=False)
        yield Static(id="run-log", markup=False)
        yield Footer()

    def refresh_from(self, model: RunModel, *, stopping: bool, now: float) -> None:
        title = model.title()
        header = words.STOPPING if stopping else plain(model.experiments(now).header)
        texts = {
            "run-title": " · ".join(
                part for part in (title.task, title.run, title.meta) if part
            ),
            "run-mode": header,
            "run-log": "\n".join(log_text(line) for line in model.log[-LOG_LINES:]),
        }
        for part, text in texts.items():
            if self._shown.get(part) != text:
                self._shown[part] = text
                self.query_one(f"#{part}", Static).update(text)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        done = self.tuner.phase == "done"
        if action == "stop":
            return not done
        if action == "close":
            return done
        return True

    def action_stop(self) -> None:
        self.tuner.action_ask_stop()

    def action_close(self) -> None:
        self.tuner.close_app()
