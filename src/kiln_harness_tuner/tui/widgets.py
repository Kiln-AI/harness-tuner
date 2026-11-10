"""The building blocks of ui_design section 4, as Textual widgets.

`specs/projects/new-tui/ui_preview/widgets.py` is the design's version of
each. This module grows with the screens that use them.

Text that comes from Kiln, the flow or the person (names, paths, guidance)
is plain text in a `Content`, never markup, so it shows as typed.
"""

from __future__ import annotations

import re
import time

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.style import Style
from textual.widget import Widget
from textual.widgets import Static

from kiln_harness_tuner.onboarding.console import LABEL_WIDTH
from kiln_harness_tuner.tui.model import words

# The run model's statuses, which hold the start-up's three (ok, fail, warn).
from kiln_harness_tuner.tui.model.state import StepStatus

TITLE_MARKUP = f"[$error]◆[/] [b]{words.TITLE_MARK}[/]"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPIN_SECS = 0.1
LINK_MARK = "↗"

# A web address with a path, in a line of text. The last character is not
# punctuation that ends the sentence around it. A bare host (Kiln's address
# in a step line) stays text.
_URL = re.compile(r"https?://[^\s/'\"<>]+/(?:[^\s'\"<>]*[^\s'\"<>.,;:)])?")


class Card(Vertical):
    """4.3: a rounded container. The border title says what the card is; the
    border subtitle carries muted meta. The border is blue while live."""

    def __init__(
        self,
        *children: Widget,
        title: str | Content = "",
        subtitle: str | Content = "",
        live: bool = False,
        id: str | None = None,
        classes: str | None = None,
    ):
        super().__init__(*children, id=id, classes=classes)
        self.border_title = title
        self.border_subtitle = subtitle
        self.set_class(live, "-live")


class TitleBar(Horizontal):
    """4.1: one line, docked at the top: the mark and the context (the task,
    then the run) on the left, muted meta on the right."""

    def compose(self) -> ComposeResult:
        yield Static(TITLE_MARKUP, classes="title-mark")
        yield Static(classes="title-context")
        yield Static(classes="title-meta")

    def show(self, context: str = "", meta: str = "") -> None:
        self.query_one(".title-context", Static).update(Content.styled(context, "bold"))
        self.query_one(".title-meta", Static).update(
            Content.styled(meta, "$text-muted")
        )


# ---- 4.9 Link ----


def link(url: str) -> Content:
    """4.9: the address, then "↗". A click opens it (`app.open_link`); the
    stylesheet's link rules color and underline it."""
    style = Style.from_meta({"@click": f"app.open_link({url!r})"})
    return Content.assemble((f"{url} {LINK_MARK}", style))


def linked(text: str) -> Content:
    """`text` as plain text, with each web address in it a link."""
    parts: list[str | Content] = []
    end = 0
    for match in _URL.finditer(text):
        parts.append(text[end : match.start()])
        parts.append(link(match.group()))
        end = match.end()
    parts.append(text[end:])
    return Content.assemble(*parts)


# ---- 4.7 Step line ----

GLYPHS: dict[StepStatus, tuple[str, str]] = {
    "ok": ("✓", "$text-success"),
    "fail": ("✗", "$text-error"),
    "warn": ("!", "$text-warning"),
}


def step_content(status: StepStatus, label: str, text: str) -> Content:
    """A glyph, the label in 9 cells, and the text: the start-up's step line,
    as `PlainTerminal` aligns it, also used by the run screen's checklists."""
    glyph, style = GLYPHS[status]
    return Content.assemble((glyph, style), f" {label:<{LABEL_WIDTH}} ", linked(text))


def warn_content(text: str) -> Content:
    """A warning: the `!` glyph and the text, with no label."""
    glyph, style = GLYPHS["warn"]
    return Content.assemble((glyph, style), " ", linked(text))


class StepLineView(Static):
    def __init__(self, content: Content, *, classes: str = "") -> None:
        super().__init__(content, classes=f"step-line {classes}".strip())


def spinner_frame(now: float | None = None) -> str:
    """The spinner glyph for this moment."""
    moment = time.monotonic() if now is None else now
    return SPINNER[int(moment / SPIN_SECS) % len(SPINNER)]


class BusyLine(Static):
    """A step line with a blue spinner in place of the glyph, while a check
    runs. It turns on its own timer."""

    def __init__(self, text: str) -> None:
        super().__init__(classes="step-line busy-line")
        self.text = text
        self.ended = False

    def end(self) -> None:
        """The check is over. A quick check can end while its line is still
        mounting, when a remove does not take: the line then removes itself
        once it has mounted, or at its next turn."""
        self.ended = True
        if self.is_mounted:
            self.remove()

    def on_mount(self) -> None:
        self._spin()
        self.set_interval(SPIN_SECS, self._spin)

    def _spin(self) -> None:
        if self.ended:
            self.remove()
            return
        self.update(
            Content.assemble((spinner_frame(), "$text-primary"), " ", linked(self.text))
        )
