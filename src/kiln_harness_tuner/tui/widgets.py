"""The building blocks of ui_design section 4, as Textual widgets.

`specs/projects/new-tui/ui_preview/widgets.py` is the design's version of
each. The run screen's widgets read the views of `tui/model` and only draw
them: every word and number is the model's, and each `Tone` maps to one
theme style (`TONE_STYLES`).

Text that comes from Kiln, the flow or the person (names, paths, guidance)
is plain text in a `Content`, never markup, so it shows as typed. `Content`
compares by its text alone, so a widget compares the view it was given, not
the `Content` it drew, to know whether it changed.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable, Hashable, Sequence
from typing import Any

from rich.console import RenderableType
from rich.rule import Rule
from rich.style import Style as RichStyle
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical
from textual.content import Content
from textual.events import Resize
from textual.geometry import Size
from textual.style import Style
from textual.theme import Theme
from textual.widget import Widget
from textual.widgets import ProgressBar, RichLog, Static

from kiln_harness_tuner.onboarding.console import LABEL_WIDTH
from kiln_harness_tuner.tui.model import words

# The run model's step statuses, which hold the start-up's three (ok, fail,
# warn): one step line serves both screens.
from kiln_harness_tuner.tui.model.state import (
    Cell,
    CheckStatus,
    GaugeView,
    LogLine,
    Span,
    StepLine,
    StepState,
    StripView,
    TierView,
    TimelineView,
    Tone,
)

TITLE_MARKUP = f"[$error]◆[/] [b]{words.TITLE_MARK}[/]"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPIN_SECS = 0.1
LINK_MARK = "↗"
# The class of a widget that holds a spinner: the run screen turns each one
# on its own timer (`Drawn.spin`).
SPINS = "-spins"

# A web address with a path, in a line of text. The last character is not
# punctuation that ends the sentence around it. A bare host (Kiln's address
# in a step line) stays text.
_URL = re.compile(r"https?://[^\s/'\"<>]+/(?:[^\s'\"<>]*[^\s'\"<>.,;:)])?")

# One theme style per tone (ui_design section 3).
TONE_STYLES: dict[Tone, str] = {
    "fg": "",
    "bold": "bold",
    "muted": "$text-muted",
    "faint": "$faint",
    "primary": "$text-primary",
    "success": "$text-success",
    "error": "$text-error",
    "warning": "$text-warning",
    "promoted": "bold $text-success",
    "spinner": "$text-primary",
}


def spinner_frame(now: float | None = None) -> str:
    """The spinner glyph for this moment."""
    moment = time.monotonic() if now is None else now
    return SPINNER[int(moment / SPIN_SECS) % len(SPINNER)]


def has_spinner(line: Sequence[Span]) -> bool:
    return any(span.tone == "spinner" for span in line)


def line_content(line: Sequence[Span], frame: str | None = None) -> Content:
    """A view's spans as `Content`: each tone its theme style, the spinner
    span the current frame."""
    glyph = spinner_frame() if frame is None else frame
    return Content.assemble(
        *(
            (glyph if span.tone == "spinner" else span.text, TONE_STYLES[span.tone])
            for span in line
        )
    )


def runs(cells: Sequence[tuple[str, str]]) -> Content:
    """(character, style) cells as one `Content`, one span per run."""
    parts: list[tuple[str, str]] = []
    for char, style in cells:
        if parts and parts[-1][1] == style:
            parts[-1] = (parts[-1][0] + char, style)
        else:
            parts.append((char, style))
    return Content.assemble(*parts)


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

    def show(self, context: str | Content = "", meta: str = "") -> None:
        """A text context is the task's name, in bold."""
        shown = Content.styled(context, "bold") if isinstance(context, str) else context
        self.query_one(".title-context", Static).update(shown)
        self.query_one(".title-meta", Static).update(
            Content.styled(meta, "$text-muted")
        )


# ---- 4.9 Link ----


def link(url: str, label: str | None = None) -> Content:
    """4.9: the label (else the address), then "↗". A click opens the
    address (`app.open_link`); the stylesheet's link rules color and
    underline it."""
    style = Style.from_meta({"@click": f"app.open_link({url!r})"})
    return Content.assemble((f"{label or url} {LINK_MARK}", style))


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


# ---- text drawn from a view ----


class Drawn(Static):
    """Text drawn from a source value (a `Line`, a step line, a card's
    steps). It draws again only when its source changed, and on each turn
    of the spinner while it holds one."""

    def __init__(
        self, source: Any = None, *, id: str | None = None, classes: str | None = None
    ):
        self.source = source
        super().__init__(self.draw(spinner_frame()), id=id, classes=classes)
        self.set_class(self.spinning(), SPINS)

    def show(self, source: Any) -> None:
        if source == self.source:
            return
        self.source = source
        self.set_class(self.spinning(), SPINS)
        self.update(self.draw(spinner_frame()))

    def spin(self) -> None:
        self.update(self.draw(spinner_frame()), layout=False)

    def draw(self, frame: str) -> Content:
        raise NotImplementedError

    def spinning(self) -> bool:
        return False


class LineView(Drawn):
    """One `Line` of a view."""

    def __init__(
        self,
        line: Sequence[Span] = (),
        *,
        id: str | None = None,
        classes: str | None = None,
    ):
        super().__init__(tuple(line), id=id, classes=classes)

    def show(self, source: Any) -> None:
        super().show(tuple(source))

    def draw(self, frame: str) -> Content:
        return line_content(self.source, frame)

    def spinning(self) -> bool:
        return has_spinner(self.source)


class Shown(Static):
    """A `Content` drawn from a key: it draws again only for a new key."""

    def __init__(self, *, id: str | None = None, classes: str | None = None):
        super().__init__(id=id, classes=classes)
        self.key: Hashable = None

    def show(self, key: Hashable, draw: Callable[[], Content]) -> None:
        if key == self.key:
            return
        self.key = key
        self.update(draw())


# ---- 4.2 Section, its header line, and the keys ----

# The gap between the Results key's entries.
KEY_GAP = "   "
# The gap between a section's name and its meta on the top border, and
# between the meta's entries.
TITLE_GAP = "   "
ENTRY_GAP = "  "
# The cells of a top border that a title can't use: a corner, a line cell
# and a space at each end (Textual's `_styles_cache` and
# `render_border_label`).
BORDER_LABEL_CELLS = 6


def section_title(name: str, meta: Sequence[Content] = ()) -> Content:
    """A section's border title: its name in bold capitals, then its meta."""
    title = Content.styled(name, "bold")
    if not meta:
        return title
    return Content.assemble(title, TITLE_GAP, Content(ENTRY_GAP).join(meta))


class KeyLine(Widget):
    """Entries on one line, such as a key of swatches. When they are too
    long, they wrap between entries, never inside one."""

    DEFAULT_CSS = "KeyLine { height: auto; }"

    def __init__(
        self, *, gap: str = KEY_GAP, id: str | None = None, classes: str | None = None
    ):
        super().__init__(id=id, classes=classes)
        # Sized by its container: an auto width would cache 0 (ui_design 13).
        self.shrink = False
        self.gap = gap
        self.entries: tuple[Content, ...] = ()

    def show(self, entries: Sequence[Content]) -> None:
        # By markup: `Content` compares by its text alone.
        shown = tuple(entries)
        if [e.markup for e in shown] != [e.markup for e in self.entries]:
            self.entries = shown
            self.refresh(layout=True)

    def _lines(self, width: int) -> list[list[Content]]:
        lines: list[list[Content]] = [[]]
        used = 0
        for entry in self.entries:
            size = entry.cell_length
            if lines[-1] and used + len(self.gap) + size > width:
                lines.append([])
                used = 0
            used += (len(self.gap) if lines[-1] else 0) + size
            lines[-1].append(entry)
        return lines

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return len(self._lines(width))

    def render(self) -> Content:
        return Content("\n").join(
            Content(self.gap).join(line) for line in self._lines(self.size.width)
        )


class SectionHeader(Horizontal):
    """4.2: the header line inside a section that has context: the muted
    context, then the right-aligned meta. The name is on the border."""

    def __init__(self, *, id: str | None = None):
        super().__init__(id=id)
        self.context = LineView(classes="header-context")
        self.meta = Shown(classes="header-meta")

    def compose(self) -> ComposeResult:
        yield self.context
        yield self.meta


class Section(Vertical):
    """4.2: a square box, the section's name in bold capitals on its top
    border."""

    def __init__(self, name: str, *, id: str):
        super().__init__(id=id, classes="section")
        self.section_name = name
        self.border_title = section_title(name)


class MetaSection(Section):
    """A section with no context line. Its meta goes on the top border after
    the name, when it fits there. Else it goes on the first line inside,
    and wraps between entries. Nothing is cut off."""

    def __init__(self, name: str, *, id: str):
        super().__init__(name, id=id)
        self.meta = KeyLine(gap=ENTRY_GAP, classes="section-meta")
        self.meta.display = False
        self._key: Hashable = None
        self._entries: tuple[Content, ...] = ()

    def compose(self) -> ComposeResult:
        yield self.meta

    def show_meta(self, key: Hashable, draw: Callable[[], Sequence[Content]]) -> None:
        """Draw the meta again only for a new key."""
        if key == self._key:
            return
        self._key = key
        self._entries = tuple(draw())
        self._place_meta()

    def on_resize(self) -> None:
        self._place_meta()

    def _place_meta(self) -> None:
        title = section_title(self.section_name, self._entries)
        width = self.outer_size.width
        on_border = width == 0 or title.cell_length + BORDER_LABEL_CELLS <= width
        self.border_title = title if on_border else section_title(self.section_name)
        self.meta.show(() if on_border else self._entries)
        self.meta.display = not on_border and bool(self._entries)


def key_width(entries: Sequence[Sequence[Span]]) -> int:
    """The cells the key takes on one line."""
    widths = [line_content(entry).cell_length for entry in entries]
    return sum(widths) + len(KEY_GAP) * max(len(widths) - 1, 0)


# ---- 4.3 Card grid ----


class CardGrid(Grid):
    """A grid of cards whose column count follows the width: as many columns
    as fit at `min_width` each, with one cell between them. So cards in one
    grid always share one width."""

    def __init__(self, *children: Widget, min_width: int, id: str | None = None):
        super().__init__(*children, id=id)
        self.min_width = min_width

    def on_resize(self, event: Resize) -> None:
        gutter = self.styles.grid_gutter_horizontal
        columns = max(1, (event.size.width + gutter) // (self.min_width + gutter))
        if self.styles.grid_size_columns != columns:
            self.styles.grid_size_columns = columns


# ---- 4.5 Gauge ----


def set_bar(bar: ProgressBar, done: float, total: float | None) -> None:
    """A bar with no total is hidden, not left to pulse: a pulsing bar says
    "working", which a run with no budget is not."""
    if total is None or total <= 0:
        bar.visible = False
        bar.update(total=1, progress=0)
        return
    bar.visible = True
    bar.update(total=total, progress=min(max(done, 0), total))


def bar() -> ProgressBar:
    return ProgressBar(total=1, show_percentage=False, show_eta=False)


class Gauge(Horizontal):
    """4.5: Textual's `ProgressBar` with its percentage and ETA hidden, the
    label and the value on the left, and the limit on the right."""

    def __init__(self, *, id: str | None = None):
        super().__init__(id=id, classes="gauge")
        self.label = Static(classes="gauge-label")
        self.value = Static(classes="gauge-value")
        self.bar = bar()
        self.limit = Static(classes="gauge-limit")
        self.view: GaugeView | None = None

    def compose(self) -> ComposeResult:
        yield self.label
        yield self.value
        yield self.bar
        yield self.limit

    def show(self, view: GaugeView) -> None:
        if view == self.view:
            return
        self.view = view
        self.label.update(Content(view.label))
        self.value.update(Content(view.value))
        self.limit.update(Content(view.limit))
        set_bar(self.bar, view.done, view.total)


# ---- 4.4 Bar: the Results tiers ----

EIGHTHS = "▏▎▍▌▋▊▉"
# Start muted, Best Confirmed in the foreground, Best Unconfirmed yellow:
# (bar, value) styles per tier (ui_design 7.4).
TIER_STYLES = (
    ("$text-muted", "$text-muted"),
    ("$foreground", "$foreground"),
    ("$warning", "$text-warning"),
)


# The three tiers of a Results card, top to bottom; None for an empty one.
Tiers = tuple[TierView | None, TierView | None, TierView | None]


def block_bar(fraction: float, width: int) -> str:
    """A bar of block characters with 1/8-cell precision."""
    eighths = round(max(0.0, min(1.0, fraction)) * width * 8)
    full, part = divmod(eighths, 8)
    return "█" * full + (EIGHTHS[part - 1] if part else "")


class TierBars(Widget):
    """Three bars, top to bottom: Start, Best Confirmed, Best Unconfirmed,
    each with its value and its change. A tier with no value leaves its row
    empty, so every card is as tall; Start with no value says why."""

    DEFAULT_CSS = "TierBars { height: 3; }"

    def __init__(self, tiers: Tiers, start_note: str):
        super().__init__()
        self.shrink = False
        self.tiers = tiers
        self.start_note = start_note

    def show(self, tiers: Tiers, start_note: str) -> None:
        if (tiers, start_note) != (self.tiers, self.start_note):
            self.tiers, self.start_note = tiers, start_note
            self.refresh()

    def render(self) -> Content:
        shown = [tier for tier in self.tiers if tier is not None]
        value_width = max((len(t.value) for t in shown), default=0)
        notes = [t.change for t in shown]
        if self.tiers[0] is not None:
            notes.append(self.start_note)
        change_width = max((len(n) for n in notes), default=0)
        bar_width = max(4, self.size.width - value_width - change_width - 2)
        rows: list[Content] = []
        for index, (tier, (bar_style, value_style)) in enumerate(
            zip(self.tiers, TIER_STYLES, strict=True)
        ):
            if tier is None:
                note = self.start_note if index == 0 else ""
                rows.append(Content.styled(note, "$text-muted"))
                continue
            drawn = block_bar(tier.fraction, bar_width)
            change, change_style = tier.change, TONE_STYLES[tier.change_tone]
            if index == 0:
                change, change_style = self.start_note, "$text-muted"
            rows.append(
                Content.assemble(
                    (drawn, bar_style),
                    " " * (bar_width - len(drawn) + 1),
                    (f"{tier.value:>{value_width}}", value_style),
                    " ",
                    (f"{change:>{change_width}}", change_style),
                )
            )
        return Content("\n").join(rows)


# ---- 4.6 Steps, and the judged-item strip ----


def steps_content(steps: Sequence[tuple[str, StepState]], frame: str) -> Content:
    """4.6: done stages muted with a green ✓; the current one bold blue with
    a spinner; later ones muted with `·`; a stop shows a red ✗, and a stage
    that ended with a warning a yellow ○."""
    parts: list[str | tuple[str, str]] = []
    for index, (name, state) in enumerate(steps):
        if index:
            parts.append("  ")
        if state == "done":
            parts += [("✓", "$text-success"), (f" {name}", "$text-muted")]
        elif state == "current":
            parts += [(frame, "$text-primary"), (f" {name}", "bold $text-primary")]
        elif state == "stopped":
            parts += [("✗", "$text-error"), (f" {name}", "$text-muted")]
        elif state == "warned":
            parts += [
                (words.NOT_SELECTED_GLYPH, "$text-warning"),
                (f" {name}", "$text-muted"),
            ]
        else:
            parts.append((f"· {name}", "$text-muted"))
    return Content.assemble(*parts)


class StepsView(Drawn):
    def draw(self, frame: str) -> Content:
        return steps_content(self.source or (), frame)

    def spinning(self) -> bool:
        return any(state == "current" for _name, state in self.source or ())


STRIP_CELLS: dict[Cell, tuple[str, str]] = {
    "fixed": ("█", "$success"),
    "held": ("▓", "$held"),
    "failing": ("░", "$failing"),
    "broke": ("✗", "$text-error"),
    # A judged item whose parent's result is not known: neither held nor
    # fixed, so it gets no meaning color.
    "unknown": ("▒", "$text-muted"),
}
QUEUED_CELL = ("·", "$text-muted")


def strip_content(strip: StripView) -> Content:
    cells = [STRIP_CELLS[cell] for cell in strip.cells]
    cells += [QUEUED_CELL] * max(strip.total - len(strip.cells), 0)
    return runs(cells)


class StripCells(Widget):
    """The judged-item cells, sized by its container."""

    DEFAULT_CSS = "StripCells { height: 1; width: 1fr; }"

    def __init__(self) -> None:
        super().__init__()
        self.shrink = False
        self.strip: StripView | None = None

    def show(self, strip: StripView | None) -> None:
        if strip != self.strip:
            self.strip = strip
            self.refresh()

    def render(self) -> Content:
        return strip_content(self.strip) if self.strip else Content("")


# ---- 4.7 Step line ----

GLYPHS: dict[CheckStatus, tuple[str, str]] = {
    "ok": ("✓", "$text-success"),
    "fail": ("✗", "$text-error"),
    "warn": ("!", "$text-warning"),
    "skip": ("·", "$text-muted"),
}


def step_content(
    status: CheckStatus, label: str, text: str, frame: str | None = None
) -> Content:
    """A glyph, the label in 9 cells, and the text: the start-up's step line,
    as `PlainTerminal` aligns it, also used by the run screen's checklists.
    A busy step has the spinner in place of the glyph."""
    if status == "busy":
        glyph, style = (spinner_frame() if frame is None else frame), "$text-primary"
    else:
        glyph, style = GLYPHS[status]
    return Content.assemble((glyph, style), f" {label:<{LABEL_WIDTH}} ", linked(text))


def warn_content(text: str) -> Content:
    """A warning: the `!` glyph and the text, with no label."""
    glyph, style = GLYPHS["warn"]
    return Content.assemble((glyph, style), " ", linked(text))


class StepLineView(Static):
    def __init__(self, content: Content, *, classes: str = "") -> None:
        super().__init__(content, classes=f"step-line {classes}".strip())


class StepRow(Drawn):
    """A step line of the run screen's checklists (a `StepLine` view)."""

    def __init__(self, step: StepLine):
        super().__init__(step, classes="step-line")

    def draw(self, frame: str) -> Content:
        step: StepLine = self.source
        return step_content(step.status, step.label, step.text, frame)

    def spinning(self) -> bool:
        return self.source.status == "busy"


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


# ---- 4.8 Tag ----


def tag(text: str) -> Content:
    """4.8: a short label in reverse video, for rare states that must stand
    out."""
    return Content.styled(f" {text} ", "bold $tag-text on $warning")


# ---- the experiment card's text (ui_design 7.5) ----


def two_lines(text: Content, width: int) -> Content:
    """The text wrapped at `width`, with its styles, cut with "…" after two
    lines. Textual has no line clamp (ui_design 13)."""
    width = max(width, 8)
    lines = text.wrap(width) or [Content("")]
    if len(lines) > 2:
        cut = Content(" ").join(lines[1:])[: width - 1].rstrip()
        style = cut.spans[-1].style if cut.spans else ""
        lines = [lines[0], Content.assemble(cut, ("…", style))]
    return Content("\n").join(lines)


class Hypothesis(Widget):
    """Lines 1–2 of an experiment card: a label, then the planner's words."""

    DEFAULT_CSS = "Hypothesis { height: 2; }"

    def __init__(self, text: Sequence[Span]) -> None:
        super().__init__()
        self.shrink = False
        self.text = tuple(text)

    def show(self, text: Sequence[Span]) -> None:
        if tuple(text) != self.text:
            self.text = tuple(text)
            self.refresh()

    def render(self) -> Content:
        return two_lines(line_content(self.text), self.size.width)


# ---- 7.3 The run timeline (custom character art) ----

PLAIN_CELL = ("░", "$text-muted")
SEGMENT_CELLS = {
    "setup": PLAIN_CELL,
    "plan": PLAIN_CELL,
    "backoff": PLAIN_CELL,
    "report": PLAIN_CELL,
    "screen": ("▒", "$warning"),
    "items": ("▓", "$primary"),
    "recon": ("▓", "$primary"),
}
GATE_CELL = ("·", "$text-muted")
PROMOTION_CELL = ("◆", "bold $text-success")
PROJECTED_CELL = ("┄", "$faint")
BLANK = (" ", "")
NOW_MARK = "▲"


def legend() -> tuple[Content, ...]:
    """The timeline's legend, one entry per glyph."""
    keys = (
        (PLAIN_CELL, words.LEGEND_PLAN),
        (SEGMENT_CELLS["screen"], words.LEGEND_SCREEN),
        (SEGMENT_CELLS["items"], words.LEGEND_EXPERIMENTS),
        (PROMOTION_CELL, words.LEGEND_PROMOTION),
        (GATE_CELL, words.LEGEND_NO_WINNER),
        (PROJECTED_CELL, words.LEGEND_PROJECTED),
    )
    return tuple(
        Content.assemble((glyph, style), (f" {name}", "$text-muted"))
        for (glyph, style), name in keys
    )


def timeline_rows(view: TimelineView, width: int) -> list[list[tuple[str, str]]]:
    """Four rows of (character, style) cells: the wave labels, the strip,
    the time axis, and the tick labels with the now marker."""

    def col(secs: float) -> int:
        return max(0, min(width - 1, int(secs * width / view.limit)))

    def put(cells: list[tuple[str, str]], start: int, text: str, style: str) -> bool:
        """`text` at `start` (moved left to fit), only where nothing is yet."""
        if len(text) > width:
            return False
        start = max(0, min(start, width - len(text)))
        if any(cells[i] != BLANK for i in range(start, start + len(text))):
            return False
        for i, char in enumerate(text):
            cells[start + i] = (char, style)
        return True

    labels = [BLANK] * width
    strip = [BLANK if view.ended else PROJECTED_CELL] * width
    labelled: set[tuple[str, int]] = set()
    for seg in view.segments:
        end = seg.end if seg.end is not None else view.now
        first = col(seg.start)
        last = max(first + 1, col(min(end, view.now)))
        if seg.kind == "gate":
            for x in range(first, last):
                strip[x] = GATE_CELL
            if seg.mark == "promotion":
                strip[last - 1] = PROMOTION_CELL
        else:
            for x in range(first, last):
                strip[x] = SEGMENT_CELLS.get(seg.kind, PLAIN_CELL)
        name = {
            "setup": words.SETUP_LABEL,
            "plan": words.wave_mark(seg.wave),
            "report": words.REPORT_LABEL,
        }.get(seg.kind)
        if name is not None and (seg.kind, seg.wave) not in labelled:
            labelled.add((seg.kind, seg.wave))
            put(labels, first, name, "$text-muted")
    for start, wave in view.projected:
        put(labels, col(start), words.wave_mark(wave), "$faint")

    axis = [("─", "$faint")] * width
    for secs, _label in view.ticks:
        axis[col(secs)] = ("├" if secs == 0 else "┼", "$faint")
    axis[width - 1] = ("┤", "$faint")

    # The now marker first; a tick label that would touch it is left out.
    style = "$text-muted" if view.ended else "$text-primary"
    at = col(view.now)
    text = f"{NOW_MARK} {view.now_label}"
    if at + len(text) > width:
        text = f"{view.now_label} {NOW_MARK}"
        at = at - len(text) + 1
    at = max(0, at)
    ticks = [BLANK] * width
    for i, char in enumerate(text[: width - at]):
        ticks[at + i] = (char, f"bold {style}" if char == NOW_MARK else style)
    taken = range(at - 2, at + len(text) + 2)
    for secs, label in view.ticks:
        start = max(0, min(col(secs), width - len(label)))
        if not any(i in taken for i in range(start, start + len(label))):
            put(ticks, start, label, "$text-muted")
    return [labels, strip, axis, ticks]


class Timeline(Widget):
    """7.3: four rows in the section's box. Blank until the run has
    started (no `TimelineView`)."""

    DEFAULT_CSS = "Timeline { height: 4; }"

    def __init__(self) -> None:
        super().__init__()
        self.shrink = False
        self.view: TimelineView | None = None

    def show(self, view: TimelineView | None) -> None:
        if view != self.view:
            self.view = view
            self.refresh()

    def render(self) -> Content:
        width = self.size.width
        view = self.view
        if view is None or view.limit <= 0 or width < 10:
            return Content("")
        return Content("\n").join(runs(row) for row in timeline_rows(view, width))


# ---- 4.10 Log line, in a RichLog ----

MAX_LOG_LINES = 2000
TIME_WIDTH = 5
# The theme variable of each tone, for Rich, which draws the log.
TONE_VARIABLES: dict[Tone, str] = {
    "fg": "foreground",
    "bold": "foreground",
    "muted": "text-muted",
    "faint": "faint",
    "primary": "text-primary",
    "success": "text-success",
    "error": "text-error",
    "warning": "text-warning",
    "promoted": "text-success",
    "spinner": "text-primary",
}


def log_renderable(line: LogLine, colors: dict[str, str]) -> RenderableType:
    """One log line as Rich draws it: a grid, so a long line wraps under its
    text, never under the time. A divider is a faint rule with the wave's
    name."""

    def style(tone: Tone, bold: bool = False) -> RichStyle:
        return RichStyle(color=colors.get(TONE_VARIABLES[tone]), bold=bold)

    if line.divider:
        divider = Table.grid(expand=True)
        divider.add_column(no_wrap=True)
        divider.add_column(ratio=1)
        divider.add_row(
            Text.assemble(("── ", style("faint")), (f"{line.text} ", style("muted"))),
            Rule(style=style("faint")),
        )
        return divider
    bold = line.bold or line.tone == "promoted"
    row = Table.grid(expand=True, padding=(0, 1, 0, 0))
    row.add_column(width=TIME_WIDTH, no_wrap=True)
    row.add_column(width=1, no_wrap=True)
    # A word wider than the column (a long address) folds onto the next
    # line: a log line is never cut short (ui_design 4.10).
    row.add_column(ratio=1, overflow="fold")
    row.add_row(
        Text(line.time, style("muted")),
        Text(line.glyph, style(line.tone, bold=bold)),
        Text(line.text, style("fg", bold=line.bold)),
    )
    return row


class RunLog(RichLog):
    """4.10 and 7.6: Textual's `RichLog`, newest at the bottom. It writes
    only the lines it has not written. It follows the newest line until the
    reader scrolls up, so they stay where they are, and again once they
    scroll back to the end.
    Rich renders each line at the width it has when it is written, with the
    theme's colors then, so on a resize or a theme change it writes all its
    lines again (ui_design 13)."""

    def __init__(self, *, id: str | None = None):
        super().__init__(
            max_lines=MAX_LOG_LINES, wrap=True, auto_scroll=True, min_width=20, id=id
        )
        self.kept: deque[LogLine] = deque(maxlen=MAX_LOG_LINES)
        # How many of the model's lines it has taken (the model only adds).
        self.taken = 0
        self._width = 0
        # Whether it follows the newest line. Only a scroll changes it: the
        # reader's, up or back to the end, or its own to the end. A height
        # change moves the end without a scroll, and a scroll to the end
        # waits for the next refresh, so "at the end now" is no test of it.
        self._following = True

    def on_mount(self) -> None:
        self.app.theme_changed_signal.subscribe(self, self._on_theme)

    def _on_theme(self, _theme: Theme) -> None:
        self.rewrite()

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        self._following = new_value >= self.max_scroll_y

    def show(self, lines: Sequence[LogLine]) -> None:
        if len(lines) < self.taken:
            self.taken = 0
            self.kept.clear()
            self.clear()
        new = lines[self.taken :]
        if not new:
            return
        self.taken = len(lines)
        self.kept.extend(new)
        # Before its first width the log writes nothing: `RichLog` would
        # hold the lines back and draw them later. The rewrite that the
        # first width starts writes them all.
        if self._width:
            self._write(new)

    def _write(self, lines: Sequence[LogLine]) -> None:
        colors = self.app.theme_variables
        for line in lines:
            self.write(log_renderable(line, colors), expand=True, scroll_end=False)
        if self._following:
            self.scroll_end(animate=False, x_axis=False)

    def on_resize(self, event: Resize) -> None:
        if event.size.width != self._width:
            self._width = event.size.width
            self.call_after_refresh(self.rewrite)
        elif self._following:
            self.scroll_end(animate=False, x_axis=False)

    def rewrite(self) -> None:
        if not self._width:
            return  # the first width rewrites
        following = self._following
        self.clear()
        # Textual 8.2.8's clear keeps the scroll position; should a later
        # one scroll to the top, that scroll is no reader's.
        self._following = following
        self._write(list(self.kept))
