"""The building blocks of ui_design.md section 4, as Textual widgets.

Each class here is one convention of the design. The screens in
`preview_app.py` only arrange them. Colors are theme variables in markup
(`$text-muted`, `$warning`, ...), so a theme switch recolors everything; the
one exception is the log, which Rich renders, so it is rebuilt on a switch.
"""

from __future__ import annotations

import textwrap
from collections.abc import Iterable, Sequence

from rich.cells import cell_len
from rich.rule import Rule
from rich.style import Style
from rich.table import Table
from rich.text import Text
from sample_data import (
    Concern,
    Item,
    Note,
    PickRow,
    Progress,
    SoFar,
    StepLine,
    Strip,
    Verdict,
)
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.content import Content
from textual.events import Key, Resize
from textual.geometry import Size
from textual.markup import escape
from textual.validation import Function
from textual.widget import Widget
from textual.widgets import Input, Label, OptionList, ProgressBar, RichLog
from textual.widgets.option_list import Option

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPIN = "{spin}"  # in markup, stands for the current spinner frame
LABEL_WIDTH = 9  # the step line's label column (onboarding/console.py)
COLUMN_GAP = "   "  # between a picker row's columns (onboarding/console.py)


def spin_glyph(widget: Widget) -> str:
    """The spinner frame the app is on. The screenshot script fixes it."""
    return SPINNER[getattr(widget.app, "frame", 3) % len(SPINNER)]


def link(text: str, action: str) -> str:
    """Markup for a link: clickable, styled by the widget's link-* rules."""
    return f"[@click={action}]{escape(text)} ↗[/]"


def tag(text: str) -> str:
    """Markup for a tag: a short label in reverse video."""
    return f"[b $tag-text on $warning] {escape(text)} [/]"


class Markup(Widget):
    """A run of markup that may hold a spinner. `{spin}` in the markup is
    replaced by the current frame each time the app ticks."""

    DEFAULT_CSS = "Markup { height: auto; }"

    def __init__(self, markup: str = "", **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        # As in Static: with `shrink`, an auto-width parent that is still 0
        # wide on the first layout pass would cache a width of 0.
        self.shrink = False
        self.markup = markup
        self.set_class(SPIN in markup, "-spins")

    def set_markup(self, markup: str) -> None:
        self.markup = markup
        self.set_class(SPIN in markup, "-spins")
        self.refresh(layout=True)

    def render(self) -> Content:
        return Content.from_markup(self.markup.replace(SPIN, spin_glyph(self)))

    def on_spin(self) -> None:
        self.refresh()


# ---------------------------------------------------------------------------
# 4.1 Title bar, 4.2 Section header
# ---------------------------------------------------------------------------


class TitleBar(Horizontal):
    """One line, docked at the top: the mark, the task and the run on the
    left; the agent CLI and the slot counts on the right, muted."""

    def __init__(self, context: str = "", meta: str = "") -> None:
        super().__init__()
        self.context = context
        self.meta = meta

    def compose(self) -> ComposeResult:
        mark = "[$error]◆[/] [b]KILN HARNESS TUNER[/]"
        yield Markup(f"{mark}   {self.context}", classes="title-left")
        yield Markup(f"[$text-muted]{self.meta}[/]", classes="title-right")


class SectionHeader(Horizontal):
    """A section's name in bold capitals, muted context, and right-aligned
    meta. No border: a blank line separates sections."""

    def __init__(
        self, title: str, context: str = "", meta: str = "", **kwargs: object
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.title = title
        self.context = context
        self.meta = meta

    def compose(self) -> ComposeResult:
        yield Markup(f"[b]{self.title}[/]", classes="header-title")
        yield Markup(self.context, classes="header-context")
        yield Markup(self.meta, classes="header-meta")

    def set_context(self, context: str) -> None:
        self.context = context
        self.query_one(".header-context", Markup).set_markup(context)


class KeyLine(Widget):
    """A key of swatches on its own line. When it is too long, it wraps
    between items, never inside one."""

    DEFAULT_CSS = "KeyLine { height: auto; }"
    GAP = "   "

    def __init__(self, items: Sequence[str], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.items = items

    @property
    def markup(self) -> str:
        return self.GAP.join(self.items)

    def _lines(self, width: int) -> list[str]:
        lines: list[list[str]] = [[]]
        used = 0
        for item in self.items:
            size = Content.from_markup(item).cell_length
            if lines[-1] and used + len(self.GAP) + size > width:
                lines.append([])
                used = 0
            used += (len(self.GAP) if lines[-1] else 0) + size
            lines[-1].append(item)
        return [self.GAP.join(line) for line in lines]

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return len(self._lines(width))

    def render(self) -> Content:
        return Content.from_markup("\n".join(self._lines(self.size.width)))


# ---------------------------------------------------------------------------
# 4.3 Card, and the grid cards sit in
# ---------------------------------------------------------------------------


class Card(Vertical):
    """A rounded container. The border title says what the card is; the
    border subtitle carries muted meta. The border is blue while live."""

    def __init__(
        self,
        *children: Widget,
        title: str = "",
        subtitle: str = "",
        live: bool = False,
        **kwargs: object,
    ) -> None:
        super().__init__(*children, **kwargs)  # type: ignore[arg-type]
        self._title = title
        self._subtitle = subtitle
        self.set_class(live, "-live")
        self.set_class(SPIN in title + subtitle, "-spins")

    def on_mount(self) -> None:
        self.on_spin()

    def on_spin(self) -> None:
        glyph = spin_glyph(self)
        self.border_title = self._title.replace(SPIN, glyph)
        self.border_subtitle = self._subtitle.replace(SPIN, glyph)


class CardGrid(Grid):
    """A grid of cards whose column count follows the width: as many columns
    as fit at `min_width` each, with one cell between them."""

    def __init__(self, *children: Widget, min_width: int, **kwargs: object) -> None:
        super().__init__(*children, **kwargs)  # type: ignore[arg-type]
        self.min_width = min_width

    def on_resize(self, event: Resize) -> None:
        gutter = self.styles.grid_gutter_horizontal
        columns = max(1, (event.size.width + gutter) // (self.min_width + gutter))
        if self.styles.grid_size_columns != columns:
            self.styles.grid_size_columns = columns


# ---------------------------------------------------------------------------
# 4.4 Bar: the Results tiers
# ---------------------------------------------------------------------------

EIGHTHS = "▏▎▍▌▋▊▉"
VALUE_WIDTH = 6
CHANGE_WIDTH = 5
MINUS = "−"


def block_bar(fraction: float, width: int) -> str:
    """A bar of block characters with 1/8-cell precision."""
    eighths = round(max(0.0, min(1.0, fraction)) * width * 8)
    full, part = divmod(eighths, 8)
    return "█" * full + (EIGHTHS[part - 1] if part else "")


def format_value(kind: str, value: float) -> str:
    if kind == "pass":
        return f"{value:.1f}%"
    if kind == "rating":
        return f"{value:.2f}"
    if kind == "cost":
        return f"${value:.3f}"
    return f"{value:.1f}s"


def bar_fraction(kind: str, value: float, start: float) -> float:
    """Pass rates on 0–100%, ratings on 1–5; cost and latency against the
    start, which draws at 80% so a cut shows as a shorter bar."""
    if kind == "pass":
        return value / 100
    if kind == "rating":
        return (value - 1) / 4
    return value / start * 0.8


def change_markup(kind: str, value: float, reference: float) -> str:
    """The change against the reference, colored by meaning and padded to
    the change column: points for pass rates, raw for ratings, relative for
    cost and latency (lower is better)."""
    if kind in ("pass", "rating"):
        delta = value - reference
        better = delta
        quiet = abs(delta) < (0.5 if kind == "pass" else 0.02)
        text = f"{abs(delta):.1f}" if kind == "pass" else f"{abs(delta):.2f}"
    else:
        delta = (value - reference) / reference * 100
        better = -delta
        quiet = abs(delta) < 2
        text = f"{abs(delta):.0f}%"
    text = ("+" if delta >= 0 else MINUS) + text
    color = "$text-muted" if quiet else "$text-success" if better > 0 else "$text-error"
    return " " * (CHANGE_WIDTH - cell_len(text)) + f"[{color}]{text}[/]"


class TierBars(Widget):
    """Three bars, top to bottom: Start, Best Confirmed, Best Unconfirmed.
    A tier with no value leaves its row empty, so every card is as tall."""

    DEFAULT_CSS = "TierBars { height: 3; }"

    def __init__(
        self,
        concern: Concern,
        start: float,
        confirmed: float | None,
        unconfirmed: float | None,
    ) -> None:
        super().__init__()
        self.concern = concern
        self.start = start
        self.confirmed = confirmed
        self.unconfirmed = unconfirmed

    def render(self) -> Content:
        kind = self.concern.kind
        bar_width = max(4, self.size.width - VALUE_WIDTH - CHANGE_WIDTH - 2)
        parent = self.start if self.confirmed is None else self.confirmed
        tiers = (
            (self.start, None, "$text-muted", "$text-muted"),
            (self.confirmed, self.start, "$foreground", "$foreground"),
            (self.unconfirmed, parent, "$warning", "$text-warning"),
        )
        rows = []
        for value, reference, bar_color, value_color in tiers:
            if value is None:
                rows.append("")
                continue
            bar = block_bar(bar_fraction(kind, value, self.start), bar_width)
            shown = format_value(kind, value)
            change = "" if reference is None else change_markup(kind, value, reference)
            rows.append(
                f"[{bar_color}]{bar}[/]{' ' * (bar_width - cell_len(bar))} "
                f"[{value_color}]{shown:>{VALUE_WIDTH}}[/] {change}"
            )
        return Content.from_markup("\n".join(rows))


class ResultCard(Card):
    """One concern: its name and priority, three tier bars, and in its
    subtitle the experiments in flight on it and the Best Unconfirmed
    config."""

    def __init__(
        self,
        concern: Concern,
        start: float,
        confirmed: float | None,
        unconfirmed: tuple[float, str] | None,
        in_flight: int,
    ) -> None:
        title = f"[b]{escape(concern.name)}[/]"
        if concern.qualifier:
            title += f" [$text-muted]· {escape(concern.qualifier)}[/]"
        meta = []
        if in_flight:
            meta.append(
                f"[$text-primary]{SPIN}[/] [$text-muted]{in_flight} in flight[/]"
            )
        if unconfirmed:
            meta.append(f"[$text-warning]{unconfirmed[1]}[/]")
        super().__init__(
            TierBars(
                concern, start, confirmed, unconfirmed[0] if unconfirmed else None
            ),
            title=title,
            subtitle=" [$text-muted]·[/] ".join(meta),
            classes="result-card",
        )


# ---------------------------------------------------------------------------
# 4.5 Gauge
# ---------------------------------------------------------------------------


class Gauge(Horizontal):
    """Textual's ProgressBar with its percentage and ETA hidden, a label and
    value on the left, and the limit on the right."""

    def __init__(
        self, label: str, value: str, progress: float, total: float, limit: str
    ) -> None:
        super().__init__(classes="gauge")
        self.label = label
        self.value = value
        self.progress = progress
        self.total = total
        self.limit = limit

    def compose(self) -> ComposeResult:
        yield Label(self.label, classes="gauge-label")
        yield Label(self.value, classes="gauge-value")
        yield ProgressBar(total=self.total, show_percentage=False, show_eta=False)
        yield Label(self.limit, classes="gauge-limit")

    def on_mount(self) -> None:
        self.query_one(ProgressBar).update(progress=self.progress)


class LabelledBar(Horizontal):
    """A card's line 4 as a gauge: a ProgressBar and a muted label."""

    def __init__(self, done: int, total: int, label: str) -> None:
        super().__init__(classes="labelled-bar")
        self.done = done
        self.total = total
        self.label = label

    def compose(self) -> ComposeResult:
        yield ProgressBar(total=self.total, show_percentage=False, show_eta=False)
        yield Label(self.label, classes="bar-label")

    def on_mount(self) -> None:
        self.query_one(ProgressBar).update(progress=self.done)


# ---------------------------------------------------------------------------
# 4.6 Steps, and the judged-item strip
# ---------------------------------------------------------------------------


def steps_markup(steps: Iterable[tuple[str, str]]) -> str:
    """Done stages muted with a green ✓; the current one bold blue with a
    spinner; later ones muted with `·`; a stop shows a red ✗."""
    parts = []
    for name, state in steps:
        if state == "done":
            parts.append(f"[$text-success]✓[/] [$text-muted]{name}[/]")
        elif state == "current":
            parts.append(f"[$text-primary]{SPIN} [b]{name}[/b][/]")
        elif state == "stopped":
            parts.append(f"[$text-error]✗[/] [$text-muted]{name}[/]")
        else:
            parts.append(f"[$text-muted]· {name}[/]")
    return "  ".join(parts)


STRIP_CELLS = {
    "F": ("█", "$success"),  # fixed
    "H": ("▓", "$held"),  # held
    "S": ("░", "$failing"),  # still failing
    "B": ("✗", "$text-error"),  # broke
    "Q": ("·", "$text-muted"),  # queued
}


def runs_markup(cells: Sequence[tuple[str, str]]) -> str:
    """Join (character, style) cells into markup, one tag per run."""
    out: list[str] = []
    run_style, run_text = "", ""
    for char, style in cells:
        if style != run_style:
            if run_text:
                out.append(f"[{run_style}]{run_text}[/]" if run_style else run_text)
            run_style, run_text = style, ""
        run_text += escape(char) if char == "[" else char
    if run_text:
        out.append(f"[{run_style}]{run_text}[/]" if run_style else run_text)
    return "".join(out)


class JudgedItems(Horizontal):
    """Line 4 while an item is measured: one cell per item, then the count.
    When the items don't fit, a gauge replaces the strip."""

    def __init__(self, strip: Strip) -> None:
        super().__init__(classes="judged")
        self.strip = strip

    def compose(self) -> ComposeResult:
        codes = self.strip.codes.ljust(self.strip.total, "Q")
        cells = [STRIP_CELLS[code] for code in codes]
        yield Markup(runs_markup(cells), classes="strip")
        yield ProgressBar(total=self.strip.total, show_percentage=False, show_eta=False)
        yield Label(f"{len(self.strip.codes)}/{self.strip.total}", classes="count")

    def on_mount(self) -> None:
        self.query_one(ProgressBar).update(progress=len(self.strip.codes))

    def on_resize(self, event: Resize) -> None:
        count = self.query_one(".count", Label)
        room = event.size.width - cell_len(str(count.content)) - 1
        fits = self.strip.total <= room
        self.query_one(".strip").display = fits
        self.query_one(ProgressBar).display = not fits


# ---------------------------------------------------------------------------
# The experiment card (ui_design.md 7.5)
# ---------------------------------------------------------------------------


class Hypothesis(Widget):
    """Lines 1–2 of an experiment card: the hypothesis, wrapped, cut with an
    ellipsis after two lines."""

    DEFAULT_CSS = "Hypothesis { height: 2; }"

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    def render(self) -> Content:
        width = max(8, self.size.width)
        lines = textwrap.wrap(self.text, width) or [""]
        if len(lines) > 2:
            rest = " ".join(lines[1:])
            lines = [lines[0], rest[: width - 1].rstrip() + "…"]
        return Content("\n".join(lines))


VERDICT_STYLE = {
    "success": ("●", "$text-success"),
    "nowin": ("○", "$text-muted"),
    "stopped": ("✗", "$text-error"),
    "rejected": ("✗", "$text-error"),
    "promoted": ("◆", "b $text-success"),
    "done": ("✓", "$text-success"),
}


def fixed_broke(fixed: int, broke: int) -> str:
    broke_color = "$text-error" if broke else "$text-muted"
    return (
        f"[$text-success]{fixed} fixed[/] [$text-muted]·[/] "
        f"[{broke_color}]{broke} broke[/]"
    )


def line5_markup(line5: SoFar | Verdict | None) -> str:
    if isinstance(line5, SoFar):
        so_far = " [$text-muted]so far[/]" if line5.so_far else ""
        return fixed_broke(line5.fixed, line5.broke) + so_far
    if isinstance(line5, Verdict):
        glyph, style = VERDICT_STYLE[line5.tone]
        return f"[{style}]{glyph} {escape(line5.text)}[/]"
    return ""


class ExperimentCard(Card):
    """One item, the same height in every state: hypothesis (2 lines),
    steps, line 4 (strip, gauge or note) and line 5 (so far or verdict)."""

    def __init__(self, item: Item) -> None:
        title = f"[b]{escape(item.target)}[/] [$text-muted]· {escape(item.kind)}[/]"
        subtitle = " · ".join(part for part in (item.id, item.spend) if part)
        super().__init__(
            title=title,
            subtitle=f"[$text-muted]{escape(subtitle)}[/]",
            live=item.live,
            classes="experiment-card",
        )
        self.item = item

    def compose(self) -> ComposeResult:
        item = self.item
        yield Hypothesis(item.text)
        yield Markup(steps_markup(item.steps), classes="line")
        line4 = item.line4
        if isinstance(line4, Strip):
            yield JudgedItems(line4)
        elif isinstance(line4, Progress):
            yield LabelledBar(line4.done, line4.total, line4.label)
        elif isinstance(line4, Note):
            color = "$text-warning" if line4.tone == "warning" else "$text-muted"
            yield Markup(f"[{color}]{escape(line4.text)}[/]", classes="line")
        else:
            yield Markup("", classes="line")
        yield Markup(line5_markup(item.line5), classes="line")


# ---------------------------------------------------------------------------
# 4.7 Step line
# ---------------------------------------------------------------------------

STEP_GLYPH = {
    "ok": "[$text-success]✓[/]",
    "fail": "[$text-error]✗[/]",
    "warn": "[$text-warning]![/]",
    "busy": f"[$text-primary]{SPIN}[/]",
}


def linkify(text: str) -> str:
    """Escape the text and turn each http address in it into a link."""
    words = []
    for word in text.split(" "):
        if word.startswith("http") and "/" in word[8:]:
            words.append(link(word, f"app.open_link('{word}')"))
        else:
            words.append(escape(word))
    return " ".join(words)


class StepLineView(Markup):
    """A glyph, a 9-character label and the text. A busy step has a blue
    spinner in place of the glyph."""

    def __init__(self, step: StepLine) -> None:
        label = f"{step.label:<{LABEL_WIDTH}} " if step.label else ""
        text = "\n".join(linkify(line) for line in step.text.split("\n"))
        super().__init__(f"{STEP_GLYPH[step.status]} {escape(label)}{text}")
        self.add_class("step-line")


# ---------------------------------------------------------------------------
# 4.10 Log line, in a RichLog
# ---------------------------------------------------------------------------

LOG_GLYPH = {
    "run": ("·", "text-muted"),
    "wave": ("▸", "foreground"),
    "gate": ("·", "text-muted"),
    "success": ("●", "text-success"),
    "yes": ("●", "text-success"),
    "nowin": ("○", "text-muted"),
    "stop": ("✗", "text-error"),
    "reject": ("✗", "text-error"),
    "promo": ("◆", "text-success"),
    "warn": ("!", "text-warning"),
    "assumed": ("?", "text-warning"),
    "lesson": ("≈", "text-muted"),
    "built": ("+", "text-muted"),
}


class RunLog(RichLog):
    """Textual's RichLog: newest at the bottom, a muted time, a glyph colored
    by meaning, plain text, promotions bold, a divider per wave. Rich draws
    the lines, so they are rebuilt when the theme changes."""

    def __init__(self, entries: Sequence[tuple[str, int, str, str]]) -> None:
        super().__init__(wrap=True, auto_scroll=True, min_width=20, id="log-lines")
        self.entries = entries

    def on_mount(self) -> None:
        self.app.theme_changed_signal.subscribe(self, lambda _theme: self.fill())
        self.fill()

    def on_resize(self) -> None:
        # Rich wraps each line at the width it has when it is written, so the
        # lines are written again at the new width.
        self.call_after_refresh(self.fill)

    def fill(self) -> None:
        colors = self.app.theme_variables

        def style(name: str, bold: bool = False) -> Style:
            return Style(color=colors[name], bold=bold)

        self.clear()
        wave = None
        for time, entry_wave, kind, text in self.entries:
            if entry_wave != wave:
                wave = entry_wave
                name = "start" if wave == 0 else f"wave {wave}"
                divider = Table.grid(expand=True)
                divider.add_column(no_wrap=True)
                divider.add_column(ratio=1)
                divider.add_row(
                    Text.assemble(("── ", style("faint")), (name, style("text-muted"))),
                    Rule(style=style("faint")),
                )
                self.write(divider, expand=True)
            glyph, color = LOG_GLYPH[kind]
            row = Table.grid(expand=True, padding=(0, 1, 0, 0))
            row.add_column(width=5, no_wrap=True)
            row.add_column(width=1, no_wrap=True)
            row.add_column(ratio=1)
            row.add_row(
                Text(time, style("text-muted")),
                Text(glyph, style(color, bold=kind == "promo")),
                Text(text, style("foreground", bold=kind == "promo")),
            )
            self.write(row, expand=True)


# ---------------------------------------------------------------------------
# The run timeline (custom character art, ui_design.md 7.3)
# ---------------------------------------------------------------------------

SEGMENT_CELL = {
    "setup": ("░", "$text-muted"),
    "plan": ("░", "$text-muted"),
    "report": ("░", "$text-muted"),
    "screen": ("▒", "$warning"),
    "items": ("▓", "$primary"),
    "recon": ("▓", "$primary"),
}
TICKS = ((0, "0"), (30, "30m"), (60, "1h"), (90, "1h30"), (120, "2h"), (150, "2h30"))


def timeline_legend() -> str:
    keys = (
        ("░", "$text-muted", "plan"),
        ("▒", "$warning", "screen"),
        ("▓", "$primary", "experiments"),
        ("◆", "b $text-success", "promotion"),
        ("·", "$text-muted", "gate, no winner"),
        ("┄", "$faint", "projected"),
    )
    return "  ".join(
        f"[{style}]{glyph}[/] [$text-muted]{name}[/]" for glyph, style, name in keys
    )


class Timeline(Widget):
    """Four rows under the section header: wave labels, the strip, the time
    axis, and the tick labels with the now marker."""

    DEFAULT_CSS = "Timeline { height: 4; }"

    def __init__(
        self,
        segments: Sequence[tuple[int, int, str, int]],
        now: int,
        clock: str,
        projected: Sequence[tuple[int, int]],
        limit: int,
        ended: bool,
    ) -> None:
        super().__init__()
        self.segments = segments
        self.now = now
        self.clock = clock
        self.projected = projected
        self.limit = limit
        self.ended = ended

    def render(self) -> Content:
        width = max(10, self.size.width)

        def col(minute: float) -> int:
            return min(width - 1, int(minute * width / self.limit))

        labels: list[tuple[str, str]] = [(" ", "")] * width
        after = (" ", "") if self.ended else ("┄", "$faint")
        strip: list[tuple[str, str]] = [after] * width

        def put(cells: list[tuple[str, str]], start: int, text: str, style: str):
            start = min(start, width - len(text))
            if all(cells[i][0] == " " for i in range(start, start + len(text))):
                for i, char in enumerate(text):
                    cells[start + i] = (char, style)

        labelled: set[int] = set()
        for start, end, kind, wave in self.segments:
            if start >= self.now:
                continue
            first, last = col(start), max(col(start) + 1, col(min(end, self.now)))
            if kind in ("gate", "promo"):
                for x in range(first, last):
                    strip[x] = ("·", "$text-muted")
                if kind == "promo" and end <= self.now:
                    strip[last - 1] = ("◆", "b $text-success")
            else:
                for x in range(first, last):
                    strip[x] = SEGMENT_CELL[kind]
            if wave not in labelled and kind in ("setup", "recon", "plan", "report"):
                labelled.add(wave)
                name = {"setup": "setup", "report": "report"}.get(kind, f"w{wave}")
                put(labels, first, name, "$text-muted")
        for start, wave in self.projected:
            put(labels, col(start), f"w{wave}", "$faint")

        axis = [("─", "$faint")] * width
        for minute, _ in TICKS:
            axis[col(minute)] = ("├" if minute == 0 else "┼", "$faint")
        axis[width - 1] = ("┤", "$faint")

        # The now marker first; a tick label that would touch it is left out.
        marker = f"ended {self.clock}" if self.ended else f"now {self.clock}"
        style = "$text-muted" if self.ended else "$text-primary"
        at = col(self.now)
        text = f"▲ {marker}"
        if at + len(text) > width:
            text = f"{marker} ▲"
            at = at - len(text) + 1
        ticks: list[tuple[str, str]] = [(" ", "")] * width
        for i, char in enumerate(text):
            ticks[at + i] = (char, f"b {style}" if char == "▲" else style)
        taken = range(at - 2, at + len(text) + 2)
        for minute, name in (*TICKS, (self.limit, "3h")):
            start = min(col(minute), width - len(name))
            if not any(i in taken for i in range(start, start + len(name))):
                put(ticks, start, name, "$text-muted")

        return Content.from_markup(
            "\n".join(runs_markup(row) for row in (labels, strip, axis, ticks))
        )


# ---------------------------------------------------------------------------
# Start-up parts: action menu, picker, text answer (ui_design.md 8)
# ---------------------------------------------------------------------------


class ActionMenu(Vertical):
    """The question, then one row per action. One key acts at once; arrows
    and Enter also work. A key that no action has is ignored."""

    def __init__(self, question: str, actions: Sequence[tuple[str, str]]) -> None:
        super().__init__(classes="prompt")
        self.question = question
        self.actions = actions

    def compose(self) -> ComposeResult:
        yield Markup(f"[b]{escape(self.question)}[/]", classes="question")
        yield OptionList(
            *(Option(f" {key}) {label} ", id=key) for key, label in self.actions),
            classes="menu",
        )

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_key(self, event: Key) -> None:
        keys = [key for key, _ in self.actions]
        if event.character in keys:
            event.stop()
            menu = self.query_one(OptionList)
            menu.highlighted = keys.index(event.character)
            self._chose(event.character)

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self._chose(str(event.option.id))

    def _chose(self, key: str) -> None:
        label = dict(self.actions)[key]
        self.notify(f"{key}) {label}", title="The flow would act on")


class Picker(Vertical):
    """The question, a filter input, the rows with aligned columns, the
    highlighted row's description, and the hint. Disabled rows are muted
    with their reason, and the cursor skips them."""

    BINDINGS = [
        Binding("up", "move(-1)", show=False),
        Binding("down", "move(1)", show=False),
    ]

    def __init__(self, question: str, rows: Sequence[PickRow], hint: str) -> None:
        super().__init__(classes="prompt")
        self.question = question
        self.rows = rows
        self.hint = hint
        cells = [(row.label, *row.columns) for row in rows]
        widths = [max(cell_len(row[i]) for row in cells) for i in range(len(cells[0]))]
        self.texts = [
            COLUMN_GAP.join(
                cell + " " * (widths[i] - cell_len(cell)) for i, cell in enumerate(row)
            )
            for row in cells
        ]

    def compose(self) -> ComposeResult:
        yield Markup(f"[b]{escape(self.question)}[/]", classes="question")
        yield Input(placeholder="type to filter", classes="filter")
        yield OptionList(classes="menu")
        yield Markup("", classes="description")
        yield Markup(f"[$text-muted]{escape(self.hint)}[/]", classes="hint")

    def on_mount(self) -> None:
        self._show("")
        self.query_one(Input).focus()

    def _show(self, query: str) -> None:
        menu = self.query_one(OptionList)
        menu.clear_options()
        for index, (row, text) in enumerate(zip(self.rows, self.texts, strict=True)):
            if query.casefold() not in text.casefold():
                continue
            prompt = (
                f" {text}{COLUMN_GAP}{row.disabled} " if row.disabled else f" {text} "
            )
            menu.add_option(Option(prompt, id=str(index), disabled=bool(row.disabled)))
        enabled = [i for i, option in enumerate(menu.options) if not option.disabled]
        menu.highlighted = enabled[0] if enabled else None
        self._describe()

    def _describe(self) -> None:
        menu = self.query_one(OptionList)
        option = menu.highlighted_option
        row = self.rows[int(option.id)] if option and option.id else None
        text = row.description if row and row.description else ""
        self.query_one(".description", Markup).set_markup(escape(text))

    def action_move(self, step: int) -> None:
        menu = self.query_one(OptionList)
        menu.action_cursor_down() if step > 0 else menu.action_cursor_up()

    @on(Input.Changed)
    def _filter(self, event: Input.Changed) -> None:
        self._show(event.value)

    @on(OptionList.OptionHighlighted)
    def _highlighted(self) -> None:
        self._describe()

    @on(Input.Submitted)
    def _picked(self) -> None:
        option = self.query_one(OptionList).highlighted_option
        if option is not None and option.id is not None:
            self.notify(self.rows[int(option.id)].label, title="Picked")


class TextAnswer(Vertical):
    """The question, an input with the default pre-filled, the validator's
    message in red under it, and the hint."""

    def __init__(self, question: str, value: str, hint: str, error: str) -> None:
        super().__init__(classes="prompt")
        self.question = question
        self.value = value
        self.hint = hint
        self.error = error

    def compose(self) -> ComposeResult:
        yield Markup(f"[b]{escape(self.question)}[/]", classes="question")
        yield Input(
            self.value,
            select_on_focus=False,
            validators=[Function(lambda text: " " not in text.strip(), self.error)],
            validate_on=["changed", "submitted"],
            classes="answer",
        )
        yield Markup("", classes="error")
        yield Markup(f"[$text-muted]{escape(self.hint)}[/]", classes="hint")

    def on_mount(self) -> None:
        answer = self.query_one(Input)
        answer.focus()
        self._show(answer.validate(answer.value))

    @on(Input.Changed)
    @on(Input.Submitted)
    def _validated(self, event: Input.Changed | Input.Submitted) -> None:
        self._show(event.validation_result)
        result = event.validation_result
        if isinstance(event, Input.Submitted) and result and result.is_valid:
            self.notify(event.value, title="Answer")

    def _show(self, result: object) -> None:
        messages = getattr(result, "failure_descriptions", [])
        text = f"[$text-error]{escape(messages[0])}[/]" if messages else ""
        self.query_one(".error", Markup).set_markup(text)
