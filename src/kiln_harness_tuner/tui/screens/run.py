"""The run screen (functional spec sections 6 to 9, ui_design sections 6, 7,
9 and 10).

One view, top to bottom: the title bar, Goal beside Time and spend, the run
timeline, Results, then Experiments beside the Log (stacked below 150
columns), and the footer. The app hands `refresh_from` the run model; each
section takes its own view and updates only the widgets whose view changed
(research section 11: a repaint of every card costs 65% of a core). A
section that fails keeps its last text, and the rest go on.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.screen import Screen
from textual.widgets import Footer, Static

from kiln_harness_tuner.tui.model import links, words
from kiln_harness_tuner.tui.model.fold import RunModel
from kiln_harness_tuner.tui.model.state import (
    CardView,
    ClockView,
    ConcernView,
    DoneView,
    ExperimentsView,
    GaugeLine,
    GoalRow,
    GoalView,
    Line,
    LogLine,
    NextStep,
    NoteLine,
    ResultsView,
    StepLine,
    StripView,
    TimelineView,
    TitleView,
    joined,
)
from kiln_harness_tuner.tui.widgets import (
    KEY_GAP,
    SPIN_SECS,
    SPINS,
    TONE_STYLES,
    Card,
    CardGrid,
    Drawn,
    Gauge,
    Hypothesis,
    KeyLine,
    LineView,
    MetaSection,
    RunLog,
    Section,
    SectionHeader,
    Shown,
    StepRow,
    StepsView,
    StripCells,
    TierBars,
    Tiers,
    Timeline,
    TitleBar,
    bar,
    card_title,
    has_spinner,
    key_width,
    legend,
    line_content,
    link,
    muted,
    set_bar,
    tag,
)

if TYPE_CHECKING:
    from kiln_harness_tuner.tui.app import TunerApp

logger = logging.getLogger("kiln_harness_tuner.tui")

# Card widths (ui_design section 6).
RESULT_CARD_WIDTH = 34
EXPERIMENT_CARD_WIDTH = 44
# The cells between a section header's context and its meta.
META_GAP = 2

ExperimentLine4 = StripView | GaugeLine | NoteLine | None


# ---- the title bar ----


def title_context(view: TitleView) -> Content:
    """The task in bold, then the run, muted."""
    parts: list[str | tuple[str, str]] = [(view.task, "bold")]
    if view.run:
        parts.append((f" · {view.run}" if view.task else view.run, "$text-muted"))
    return Content.assemble(*parts)


# ---- Goal ----

ROLE_STYLES = {
    "optimize": "bold $text-success",
    "hold": "bold $text-warning",
    "watch": "$text-muted",
}
ROLE_WIDTH = max(len(role) for role in words.GOAL_ROLES.values())


# The data line's label, as wide as the role column and its gap, so the
# data text starts where the rows' texts do.
DATA_LABEL = f"  {words.GOAL_DATA_LABEL}".ljust(ROLE_WIDTH) + "  "


def goal_content(rows: Sequence[GoalRow]) -> Content:
    """7.1: the three aligned rows. The data line is a row of its own."""
    what_width = max((len(row.what) for row in rows), default=0)
    lines = [
        Content.assemble(
            (f"{words.GOAL_ROLES[row.role]:<{ROLE_WIDTH}}", ROLE_STYLES[row.role]),
            "  ",
            f"{row.what:<{what_width}}",
            "  ",
            (row.note, "$text-muted"),
        )
        for row in rows
    ]
    return Content("\n").join(lines)


class GoalSection(Section):
    def __init__(self) -> None:
        super().__init__(words.GOAL, id="goal")
        self.body = Shown(classes="goal-rows")
        # The split sizes: a long line wraps under its own text, not under
        # the label.
        self.data = LineView(classes="goal-data-text")

    def compose(self) -> ComposeResult:
        yield self.body
        with Horizontal(classes="goal-data"):
            yield Static(Content.styled(DATA_LABEL, "$text-muted"), classes="label")
            yield self.data

    def show(self, view: GoalView) -> None:
        self.body.show(view.rows, lambda: goal_content(view.rows))
        self.data.show(view.data)


# ---- Time and spend ----


def wave_meta(view: ClockView) -> tuple[Content, ...]:
    """7.2: "wave 4 of ~7", and the tag on the wrap-up wave only."""
    wave = line_content(view.wave)
    if not view.last_wave:
        return (wave,)
    return wave, tag(words.LAST_WAVE_TAG)


class ClockSection(MetaSection):
    def __init__(self) -> None:
        super().__init__(words.TIME_AND_SPEND, id="spend")
        self.time = Gauge(id="time-gauge")
        self.spend = Gauge(id="spend-gauge")
        self.lanes = LineView(classes="gauge-note")
        self.times = Shown(classes="gauge-note")

    def compose(self) -> ComposeResult:
        yield from super().compose()
        yield self.time
        yield self.spend
        yield self.lanes
        yield self.times

    def show(self, view: ClockView) -> None:
        self.show_meta((view.wave, view.last_wave), lambda: wave_meta(view))
        self.time.show(view.time)
        self.spend.show(view.spend)
        self.lanes.show(view.lanes)
        self.times.show(view.times, lambda: muted(view.times))


# ---- the run timeline ----


class TimelineSection(MetaSection):
    def __init__(self) -> None:
        super().__init__(words.TUNING_TIMELINE, id="timeline")
        self.timeline = Timeline()
        self.show_meta("legend", legend)

    def compose(self) -> ComposeResult:
        yield from super().compose()
        yield self.timeline

    def show(self, view: TimelineView | None) -> None:
        self.timeline.show(view)


# ---- Results ----


class ResultCard(Card):
    """7.4: one concern, its three tier bars, and in its subtitle the
    experiments in flight on it and the Best Unconfirmed config."""

    def __init__(self, view: ConcernView):
        self.bars = TierBars(self._tiers(view), view.start_note)
        super().__init__(
            self.bars,
            title=card_title(view.title, view.qualifier),
            subtitle=line_content(view.subtitle),
            classes="result-card",
        )
        self.view = view
        self.set_class(has_spinner(view.subtitle), SPINS)

    @staticmethod
    def _tiers(view: ConcernView) -> Tiers:
        return (view.start, view.confirmed, view.unconfirmed)

    def show(self, view: ConcernView) -> None:
        if view == self.view:
            return
        old, self.view = self.view, view
        if (view.title, view.qualifier) != (old.title, old.qualifier):
            self.border_title = card_title(view.title, view.qualifier)
        if view.subtitle != old.subtitle:
            self.border_subtitle = line_content(view.subtitle)
            self.set_class(has_spinner(view.subtitle), SPINS)
        self.bars.show(self._tiers(view), view.start_note)

    def spin(self) -> None:
        self.border_subtitle = line_content(self.view.subtitle)


class ResultsSection(Section):
    """The header line holds the key's first entries and the Compare link;
    the entries that don't fit beside the link wrap on the lines under it.
    The cards are made again only when the concerns change."""

    def __init__(self) -> None:
        super().__init__(words.RESULTS, id="results")
        self.header = SectionHeader()
        self.key = KeyLine(id="results-key")
        self.grid = CardGrid(min_width=RESULT_CARD_WIDTH, id="result-cards")
        self.cards: list[ResultCard] = []
        self._concerns: tuple[tuple[str, str], ...] | None = None
        self._entries: tuple[Line, ...] = ()

    def compose(self) -> ComposeResult:
        yield self.header
        yield self.key
        yield self.grid

    def show(self, view: ResultsView, compare: str) -> None:
        self._entries = view.key
        self.header.meta.show(compare, lambda: link(compare, words.COMPARE_LINK))
        self._place_key()
        concerns = tuple((card.title, card.qualifier) for card in view.cards)
        if concerns != self._concerns:
            self._concerns = concerns
            self.grid.remove_children()
            self.cards = [ResultCard(card) for card in view.cards]
            self.grid.mount(*self.cards)
            return
        for card, concern in zip(self.cards, view.cards, strict=True):
            card.show(concern)

    def on_resize(self) -> None:
        self._place_key()

    def _place_key(self) -> None:
        """As many entries as fit beside the link go on its line, so the
        link never has a line of its own once the width is known."""
        width = self.header.size.width
        room = width - META_GAP - link("", words.COMPARE_LINK).cell_length
        entries = self._entries
        fit = [n for n in range(1, len(entries) + 1) if key_width(entries[:n]) <= room]
        inline = max(fit, default=0) if width else 0
        # Empty rather than hidden: the context also pushes the link right.
        self.header.context.show(joined(entries[:inline], KEY_GAP))
        self.key.show([line_content(entry) for entry in entries[inline:]])
        self.key.display = inline < len(entries)


# ---- Experiments ----


class CardLineFour(Horizontal):
    """Line 4 of an experiment card: the judged-item strip (a gauge when the
    items don't fit), a gauge with its label, a note, or nothing."""

    def __init__(self, view: ExperimentLine4):
        self.strip = StripCells()
        self.bar = bar()
        self.label = Static(classes="line4-label")
        super().__init__(self.strip, self.bar, self.label, classes="line4")
        self.view: ExperimentLine4 = None
        self._apply(view)

    def show(self, view: ExperimentLine4) -> None:
        if view != self.view:
            self._apply(view)

    def _apply(self, view: ExperimentLine4) -> None:
        self.view = view
        self.set_class(isinstance(view, NoteLine), "-note")
        self.strip.show(view if isinstance(view, StripView) else None)
        if isinstance(view, StripView):
            self.label.update(muted(view.count))
            self._fit()
        elif isinstance(view, GaugeLine):
            self.strip.display = False
            self.bar.display = True
            set_bar(self.bar, view.done, view.total)
            self.label.update(muted(view.label))
        else:
            self.strip.display = self.bar.display = False
            note = (
                Content.styled(view.text, TONE_STYLES[view.tone])
                if isinstance(view, NoteLine)
                else Content("")
            )
            self.label.update(note)

    def _fit(self) -> None:
        """The strip when one cell per item fits beside the count, else a
        gauge. Before the first layout the width is not known: the strip."""
        view = self.view
        if not isinstance(view, StripView):
            return
        room = self.size.width - len(view.count) - 1
        fits = self.size.width == 0 or view.total <= room
        self.strip.display = fits
        self.bar.display = not fits
        if not fits:
            set_bar(self.bar, len(view.cells), view.total)

    def on_resize(self) -> None:
        self._fit()


def experiment_subtitle(spend: str) -> Content:
    """The card's bottom border: the details marker, then its spend. The
    marker takes the border's color, which lights up on hover."""
    if not spend:
        return Content(words.DETAILS_MARK)
    return Content.assemble(words.DETAILS_MARK, (f"  {spend}", "$text-muted"))


class ExperimentCard(Card):
    """7.5: one item, the same height in every state: the hypothesis (two
    lines), the steps, line 4 and line 5. The border is blue while live. A
    click opens the item's details panel (functional spec 7.9)."""

    def __init__(self, view: CardView):
        self.hypothesis = Hypothesis(view.text)
        self.steps = StepsView(view.steps, classes="card-line")
        self.line4 = CardLineFour(view.line4)
        self.line5 = LineView(view.line5, classes="card-line")
        super().__init__(
            self.hypothesis,
            self.steps,
            self.line4,
            self.line5,
            title=card_title(view.title, view.qualifier),
            subtitle=experiment_subtitle(view.subtitle),
            live=view.live,
            classes="experiment-card",
        )
        self.view = view

    def show(self, view: CardView) -> None:
        if view == self.view:
            return
        old, self.view = self.view, view
        if (view.title, view.qualifier) != (old.title, old.qualifier):
            self.border_title = card_title(view.title, view.qualifier)
        if view.subtitle != old.subtitle:
            self.border_subtitle = experiment_subtitle(view.subtitle)
        self.set_class(view.live, "-live")
        self.hypothesis.show(view.text)
        self.steps.show(view.steps)
        self.line4.show(view.line4)
        self.line5.show(view.line5)

    def on_click(self) -> None:
        cast("TunerApp", self.app).show_item(self.view.key)


def done_content(view: DoneView) -> Content:
    """The run summary (functional spec 7.6, Done mode). The report's path
    is a link: it opens as the `r` key does."""
    label_width = len(words.DONE_BEST) + 2
    totals: list[str | tuple[str, str]] = []
    for index, (count, word) in enumerate(view.totals):
        if index:
            totals.append((" · ", "$text-muted"))
        totals += [(count, "bold"), (f" {word}", "$text-muted")]
    report = link(view.report_path, view.report_path) if view.report_path else None
    lines = [
        Content.assemble(
            (view.ended, "bold"), (" · ", "$text-muted"), line_content(view.summary)
        ),
        Content.assemble(*totals),
        Content(""),
        line_content(view.best),
        Content.assemble(
            (f"{words.DONE_REPORT:<{label_width}}", "$text-muted"),
            report if report is not None else view.report,
        ),
    ]
    return Content("\n").join(lines)


def next_step_content(step: NextStep) -> Content:
    """A link with its note after it, a muted step still to come, or plain
    words."""
    if step.url is not None:
        shown = link(step.url, step.text)
        return Content.assemble(shown, f"  {step.note}") if step.note else shown
    return muted(step.text) if step.pending else Content(step.text)


class NextStepRow(Horizontal):
    """One next step after its bullet: a long one wraps under its own text."""

    def __init__(self, step: NextStep) -> None:
        super().__init__(
            Static(Content.styled(words.NEXT_STEP_BULLET, "$text-muted")),
            Static(next_step_content(step), classes="next-step-text"),
            classes="next-step",
        )


class RunCompleteCard(Card):
    """Done mode: one wide card in place of the grid, the run summary and
    then the next steps."""

    def __init__(self) -> None:
        self.body = Shown(classes="run-complete-body")
        self.next_steps = Vertical(classes="next-steps")
        super().__init__(
            self.body,
            Static(Content.styled(words.NEXT_STEPS, "bold"), classes="next-title"),
            self.next_steps,
            title=Content.styled(words.TUNING_COMPLETE, "bold"),
            id="run-complete",
        )
        self._subtitle = ""
        self._steps: tuple[NextStep, ...] | None = None

    def show(self, view: DoneView) -> None:
        if view.subtitle != self._subtitle:
            self._subtitle = view.subtitle
            self.border_subtitle = muted(view.subtitle)
        self.body.show(view, lambda: done_content(view))
        if view.next_steps != self._steps:
            self._steps = view.next_steps
            self.next_steps.remove_children()
            self.next_steps.mount(*(NextStepRow(step) for step in view.next_steps))


class ExperimentsSection(Section):
    """The mode, its step lines, the cards (kept by item key: a card keeps
    its place from the start of the wave to its verdict) and the note; in
    the Done mode the run summary in place of the cards."""

    def __init__(self) -> None:
        super().__init__(words.EXPERIMENTS, id="experiments")
        self.header = SectionHeader()
        self.steps = Vertical(classes="step-lines")
        self.subhead = Shown(classes="subhead")
        self.grid = CardGrid(min_width=EXPERIMENT_CARD_WIDTH, id="experiment-cards")
        self.note = Shown(classes="wave-note")
        self.done = RunCompleteCard()
        self.rows: list[StepRow] = []
        self.cards: dict[str, ExperimentCard] = {}
        self._bottom_height: int | None = None
        for part in (self.steps, self.subhead, self.note, self.done):
            part.display = False

    def compose(self) -> ComposeResult:
        yield self.header
        yield self.steps
        yield self.subhead
        yield self.grid
        yield self.note
        yield self.done

    def show(self, view: ExperimentsView, *, stopping: bool, live: int) -> None:
        self.header.context.show(
            words.stopping_header(live) if stopping else view.header
        )
        self.header.meta.show(view.elapsed, lambda: muted(view.elapsed))
        self._show_steps(view.steps)
        self._show_text(self.subhead, view.subhead)
        self._show_text(self.note, view.note)
        self._show_cards(view.cards)
        self.grid.display = view.done is None
        self.done.display = view.done is not None
        if view.done is not None:
            self.done.show(view.done)

    def _show_steps(self, steps: Sequence[StepLine]) -> None:
        self.steps.display = bool(steps)
        if len(steps) != len(self.rows):
            self.steps.remove_children()
            self.rows = [StepRow(step) for step in steps]
            self.steps.mount(*self.rows)
            return
        for row, step in zip(self.rows, steps, strict=True):
            row.show(step)

    @staticmethod
    def _show_text(part: Shown, text: str) -> None:
        part.display = bool(text)
        part.show(text, lambda: muted(text))

    def _show_cards(self, views: Sequence[CardView]) -> None:
        # By key: a wave's re-plan can use its probe phase's item ids.
        keys = [view.key for view in views]
        if keys != [card.view.key for card in self.cards.values()]:
            self.grid.remove_children()
            self.cards = {view.item_id: ExperimentCard(view) for view in views}
            self.grid.mount(*self.cards.values())
            return
        for view in views:
            self.cards[view.item_id].show(view)

    def on_mount(self) -> None:
        # Beside the Log this section is as tall as the bottom area, so a
        # change of its content resizes nothing: each layout is the moment
        # to measure it.
        self.screen.screen_layout_refresh_signal.subscribe(
            self, lambda _screen: self.fit_bottom()
        )

    def content_height(self) -> int:
        """The height the section needs: its parts and its frame. Beside
        the Log it is as tall as the bottom area, so its own height is no
        measure of what it holds."""
        shown = [part for part in self.children if part.display]
        if not shown:
            return self.styles.gutter.height
        last = max(shown, key=lambda part: part.region.bottom)
        used = last.region.bottom + last.styles.margin.bottom - self.content_region.y
        return max(used, 0) + self.styles.gutter.height

    def fit_bottom(self) -> None:
        """The bottom area is at least as tall as this section's content.
        So the whole screen scrolls and no card is cut off, and beside the
        Log the two grow together."""
        if not self.is_mounted:
            return
        height = self.content_height()
        if height != self._bottom_height:
            self._bottom_height = height
            self.screen.query_one("#bottom").styles.min_height = height


# ---- the Log ----


class LogSection(Section):
    def __init__(self) -> None:
        super().__init__(words.LOG, id="log")
        self.lines = RunLog(id="log-lines")

    def compose(self) -> ComposeResult:
        yield self.lines

    def show(self, lines: Sequence[LogLine]) -> None:
        self.lines.show(lines)


# ---- the screen ----


class RunScreen(Screen[None]):
    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (150, "-wide")]
    BINDINGS = [
        Binding("e", "experiments", words.EXPERIMENTS_KEY),
        Binding("c", "compare", words.COMPARE_LINK),
        Binding("g", "graph", words.GRAPH_KEY),
        Binding("r", "report", words.REPORT_LINK),
        Binding("q", "stop", words.STOP_KEY),
        Binding("q", "close", words.CLOSE_KEY),
        Binding("enter", "close", words.CLOSE_KEY, show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.title_bar = TitleBar()
        self.goal = GoalSection()
        self.clock = ClockSection()
        self.timeline = TimelineSection()
        self.results = ResultsSection()
        self.experiments = ExperimentsSection()
        self.log_section = LogSection()
        self._title: TitleView | None = None
        self._report = False
        self._items = False
        # The Compare link, built once per refresh from the Results view, for
        # the Results header, the Done card and the `c` key alike.
        self.compare: str | None = None
        # The sections whose last refresh failed: each failure is logged
        # with its traceback once, not once a second.
        self._failing: set[str] = set()

    @property
    def tuner(self) -> TunerApp:
        return cast("TunerApp", self.app)

    def compose(self) -> ComposeResult:
        yield self.title_bar
        with VerticalScroll(id="body"):
            with Horizontal(id="top"):
                yield self.goal
                yield self.clock
            yield self.timeline
            yield self.results
            with Container(id="bottom"):
                yield self.experiments
                yield self.log_section
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(SPIN_SECS, self.spin)

    def spin(self) -> None:
        """Turn every spinner on the screen: only live things move."""
        for widget in self.query(f".{SPINS}"):
            if isinstance(widget, (Drawn, ResultCard)):
                widget.spin()

    # ---- showing the run ----

    def refresh_from(self, model: RunModel, *, stopping: bool, now: float) -> None:
        info = model.info

        def show_results() -> None:
            view = model.results()
            self.compare = links.compare_url(
                info.kiln_host, info.project_id, info.task_id, view.compare_rc_ids
            )
            self.results.show(view, self.compare)

        def show_experiments() -> None:
            self.experiments.show(
                model.experiments(now), stopping=stopping, live=model.live_count()
            )

        parts: tuple[tuple[str, Callable[[], None]], ...] = (
            ("title", lambda: self._show_title(model.title())),
            ("goal", lambda: self.goal.show(model.goal())),
            ("clock", lambda: self.clock.show(model.clock(now))),
            ("timeline", lambda: self.timeline.show(model.timeline(now))),
            ("results", show_results),
            ("experiments", show_experiments),
            ("log", lambda: self.log_section.show(model.log)),
        )
        for name, show in parts:
            try:
                show()
            except Exception:
                self._failed(name)
            else:
                self._failing.discard(name)
        self.set_class(model.mode == "done", "-done")
        report = model.report_path is not None
        items = any(wave.items for wave in model.waves)
        if (report, items) != (self._report, self._items):
            self._report, self._items = report, items
            self.refresh_bindings()

    def compare_url(self, model: RunModel) -> str:
        """The Compare link the Results section shows; the model's own until
        the section has shown one."""
        return self.compare if self.compare is not None else model.compare_url()

    def _failed(self, section: str) -> None:
        """A section that fails keeps its last text. Its first failure in a
        row goes to the run log with the traceback; the ones after it, once
        a second, only at debug level."""
        if section in self._failing:
            logger.debug("the run screen still cannot show its %s", section)
            return
        self._failing.add(section)
        logger.exception("the run screen could not show its %s", section)

    def _show_title(self, view: TitleView) -> None:
        if view != self._title:
            self._title = view
            self.title_bar.show(title_context(view), view.meta)

    # ---- keys ----

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        done = self.tuner.phase == "done"
        if action == "stop":
            return not done
        if action == "close":
            return done
        if action == "report":
            return self._report_path() is not None
        if action == "experiments":
            return self._items
        return True

    def _report_path(self) -> str | None:
        model = self.tuner.model
        return model.report_path if model is not None else None

    def action_compare(self) -> None:
        if self.tuner.model is not None:
            self.tuner.action_open_link(self.compare_url(self.tuner.model))

    def action_graph(self) -> None:
        if self.tuner.model is not None:
            self.tuner.action_open_link(self.tuner.model.lineage_url())

    def action_report(self) -> None:
        path = self._report_path()
        if path:
            self.tuner.action_open_link(path)

    def action_experiments(self) -> None:
        self.tuner.show_picker()

    def action_stop(self) -> None:
        self.tuner.action_ask_stop()

    def action_close(self) -> None:
        self.tuner.close_app()
