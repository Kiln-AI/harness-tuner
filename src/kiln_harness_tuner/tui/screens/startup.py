"""The start-up screen (ui_design section 8): one transcript that grows down,
under the title bar.

The start-up thread's `AppTerminal` puts one request per `Terminal` call on
the app's queue, and the app's drain hands each one to `show`. A line or a
card stays. A prompt stays until it is answered; its step line, which the
flow writes next, then takes its place. The newest part is always in view.

The words are the flow's own. Two texts get a card of their own, by their
first line (`bridge.said_kind`): the summary, and the rerun command, which
goes in the summary's card.
"""

from __future__ import annotations

import logging
import re
import textwrap
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.content import Content
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Static

from kiln_harness_tuner.tui import bridge
from kiln_harness_tuner.tui.screens.prompts import (
    ActionMenu,
    Picker,
    PromptPart,
    TextAnswer,
)
from kiln_harness_tuner.tui.widgets import (
    BusyLine,
    Card,
    StepLineView,
    TitleBar,
    link,
    linked,
    step_content,
    warn_content,
)

logger = logging.getLogger("kiln_harness_tuner.tui")

# The step that names the chosen task (`onboarding/choose.py`), and its text:
# "<name> (<id>)", then " · the only task …" for a task nobody picked. The
# shortest name that leaves one of those two endings wins, so a name with
# its own brackets or dots still reads whole.
TASK_LABEL = "Task"
_CHOSEN = re.compile(r"(?P<name>.*?) \((?P<id>[^()\s]+)\)(?: · the only .*)?", re.S)


def task_name(text: str) -> str | None:
    """The task's name in its step line, or None for a text in another form."""
    match = _CHOSEN.fullmatch(text)
    return match.group("name") if match else None


def _heading_and_rest(text: str) -> tuple[str, str]:
    """A `say` text's first line, and the rest without its indent."""
    lines = text.strip("\n").split("\n")
    return lines[0], textwrap.dedent("\n".join(lines[1:]))


# A border title takes its own cells plus six: the corners, a rule each side
# and a space each side.
TITLE_FRAME = 6


class _TextCard(Card):
    """A start-up card whose lines are one text, which grows: lines added
    to a card still being mounted would land before its first ones. It is
    as wide as its text, its title included, up to the transcript's width,
    where the text wraps. A border title can't wrap, so a title wider than
    the card moves to the text's first line."""

    def __init__(self, text: Content, *, title: Content, classes: str) -> None:
        self.title = title
        self.text = text
        self.title_in_text = False
        self.body = Static(text, classes="body")
        super().__init__(self.body, title=title, classes=f"startup-card {classes}")
        # The stylesheet's `max-width: 100%` comes after this.
        self.styles.min_width = title.cell_length + TITLE_FRAME

    def add(self, more: Content) -> None:
        self.text = Content.assemble(self.text, "\n", more)
        self._show()

    def on_resize(self) -> None:
        inside = self.title.cell_length + TITLE_FRAME > self.outer_size.width
        if inside != self.title_in_text:
            self.title_in_text = inside
            self.border_title = "" if inside else self.title
            self._show()

    def _show(self) -> None:
        if self.title_in_text:
            self.body.update(Content.assemble(self.title, "\n", self.text))
        else:
            self.body.update(self.text)


class ProblemCard(_TextCard):
    """A failed check: a red border, the ✗ title, the body lines, then its
    warnings as `!` lines."""

    def __init__(self, title: str, body: tuple[str, ...]) -> None:
        super().__init__(
            linked("\n".join(body)),
            title=Content.styled(f"✗ {title}", "bold $text-error"),
            classes="-problem",
        )


class SummaryCard(_TextCard):
    """ "Ready to start": the summary lines, then the rerun command, muted."""

    def __init__(self, heading: str, lines: str) -> None:
        super().__init__(
            Content(lines), title=Content.styled(heading, "bold"), classes="-summary"
        )


class StartupScreen(Screen[None]):
    def __init__(self) -> None:
        super().__init__()
        # Requests that came before the screen was mounted.
        self._backlog: list[bridge.Request] = []
        self._ready = False
        # The newest part: a warning right after a problem goes in its card,
        # and a rerun command right after the summary in the summary's card.
        self._last: Widget | None = None
        self._busy: BusyLine | None = None

    def compose(self) -> ComposeResult:
        yield TitleBar()
        transcript = VerticalScroll(Vertical(id="column"), id="transcript")
        # The prompt in it keeps the focus: Tab never moves it away.
        transcript.can_focus = False
        yield transcript
        yield Footer()

    def on_mount(self) -> None:
        self._ready = True
        backlog, self._backlog = self._backlog, []
        for request in backlog:
            self.show(request)

    @property
    def transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    @property
    def column(self) -> Vertical:
        """What the transcript holds, in order."""
        return self.query_one("#column", Vertical)

    def show(self, request: bridge.Request) -> None:
        """Draw `request`, or keep it until the screen has mounted. A prompt
        the screen can't draw fails, so the start raises instead of waiting
        for an answer that can't come: also one that came before the mount,
        which `on_mount` draws."""
        if not self._ready:
            self._backlog.append(request)
            return
        try:
            self._draw(request)
        except Exception as error:
            logger.exception("the start-up screen could not show a request")
            if isinstance(request, bridge.Prompt):
                request.fail(error)

    def _draw(self, request: bridge.Request) -> None:
        match request:
            case bridge.Welcome():
                self._add(self._welcome(request))
            case bridge.Step(status=status, label=label, text=text):
                self._add(StepLineView(step_content(status, label, text)))
                if label == TASK_LABEL:
                    self._name_task(text)
            case bridge.Block(title=title, body=body):
                self._add(ProblemCard(title, body))
            case bridge.Warn(text=text):
                self._warn(text)
            case bridge.Say(text=text):
                self._say(text)
            case bridge.Busy(text=text):
                self._busy = BusyLine(text)
                self._add(self._busy)
            case bridge.BusyEnd():
                if self._busy is not None:
                    self._busy.end()
                    self._busy = None
            case bridge.Opened(url=url, opened=opened):
                said = bridge.opened_text(url, opened).removesuffix(url)
                self._add(StepLineView(Content.assemble(said, link(url))))
            case bridge.Pick():
                self._add(Picker(request))
            case bridge.Act():
                self._add(ActionMenu(request))
            case bridge.Text():
                self._add(TextAnswer(request))

    def _add(self, part: Widget) -> None:
        self.column.mount(part)
        self._last = part
        self._scroll()

    def _scroll(self) -> None:
        self.call_after_refresh(self.transcript.scroll_end, animate=False)

    def _welcome(self, request: bridge.Welcome) -> Card:
        return Card(
            Static(Content(request.description)),
            title=Content.styled(request.title, "bold $text-primary"),
            subtitle=Content.styled(f"v{request.version}", "$text-muted"),
            id="welcome",
        )

    def _name_task(self, text: str) -> None:
        name = task_name(text)
        if name is not None:
            self.query_one(TitleBar).show(name)

    def _warn(self, text: str) -> None:
        if isinstance(self._last, ProblemCard):
            self._last.add(warn_content(text))
            self._scroll()
        else:
            self._add(StepLineView(warn_content(text)))

    def _say(self, text: str) -> None:
        kind = bridge.said_kind(text)
        heading, rest = _heading_and_rest(text)
        if kind == "summary":
            self._add(SummaryCard(heading, rest))
        elif kind == "rerun":
            rerun = Content.styled(f"{heading}\n{rest}", "$text-muted")
            if isinstance(self._last, SummaryCard):
                self._last.add(Content.assemble("\n", rerun))
                self._scroll()
            else:
                self._add(Static(rerun, classes="said"))
        else:
            self._add(Static(linked(text.strip("\n")), classes="said"))

    # ---- answers ----

    def on_prompt_part_answered(self, message: PromptPart.Answered) -> None:
        message.stop()
        self._settle(message.part)
        message.part.request.resolve(message.value)

    def on_prompt_part_failed(self, message: PromptPart.Failed) -> None:
        message.stop()
        self._settle(message.part)
        message.part.request.fail(message.error)

    def _settle(self, part: PromptPart[Any]) -> None:
        part.remove()
        if self._last is part:
            self._last = None
