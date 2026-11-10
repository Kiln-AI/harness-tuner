"""A preview of the new TUI's design, with hardcoded data.

Design material for ui_design.md, not product code: it is not shipped and
nothing imports it. Run it from the repository root:

    uv run --with textual==8.2.8 python specs/projects/new-tui/ui_preview/preview_app.py

Keys 1–5 show the run screen's modes, 6–0 the start-up parts, g the card
gallery, q the stop dialog and t switches light and dark. Ctrl+Q quits.
"""

from __future__ import annotations

import sys

import sample_data as data
from rich.cells import cell_len
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.markup import escape
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widget import Widget
from textual.widgets import Button, Footer, Input
from widgets import (
    ActionMenu,
    Card,
    CardGrid,
    ExperimentCard,
    Gauge,
    KeyLine,
    Markup,
    Picker,
    ResultCard,
    RunLog,
    SectionHeader,
    StepLineView,
    TextAnswer,
    Timeline,
    TitleBar,
    link,
    tag,
    timeline_legend,
)

# ---------------------------------------------------------------------------
# Themes (ui_design.md section 2)
# ---------------------------------------------------------------------------

KILN_DARK = Theme(
    name="kiln-dark",
    primary="#415CF5",
    secondary="#415CF5",
    accent="#7B90FF",
    warning="#F4B544",
    error="#E74D31",
    success="#4CC38A",
    foreground="#E6E8F0",
    background="#0F1117",
    surface="#151822",
    panel="#1C2030",
    dark=True,
    variables={
        "text-primary": "#7B90FF",
        "text-warning": "#F4B544",
        "text-error": "#E74D31",
        "text-success": "#4CC38A",
        "text-muted": "#8C92A6",
        "text-disabled": "#5C6276",
        "border-muted": "#363C4F",
        "faint": "#4C5266",
        "held": "#2C7D57",
        "failing": "#9C3D2C",
        "tag-text": "#0F1117",
        "block-cursor-foreground": "#FFFFFF",
        "footer-key-foreground": "#7B90FF",
        "footer-background": "#1C2030",
        "input-selection-background": "#415CF5 40%",
    },
)

KILN_LIGHT = Theme(
    name="kiln-light",
    primary="#415CF5",
    secondary="#415CF5",
    accent="#415CF5",
    warning="#F4B544",
    error="#D1401F",
    success="#1E8A57",
    foreground="#1B1E27",
    background="#F4F5F9",
    surface="#FFFFFF",
    panel="#E3E6F0",
    dark=False,
    variables={
        "text-primary": "#415CF5",
        "text-warning": "#9A6A00",
        "text-error": "#D1401F",
        "text-success": "#1E8A57",
        "text-muted": "#636A7E",
        "text-disabled": "#9CA1B0",
        "border-muted": "#C4C9D6",
        "faint": "#B3B8C6",
        "held": "#86C9A6",
        "failing": "#EBA595",
        "tag-text": "#1B1E27",
        "block-cursor-foreground": "#FFFFFF",
        "footer-key-foreground": "#415CF5",
        "footer-background": "#E3E6F0",
        "input-selection-background": "#415CF5 30%",
    },
)

# The variables the themes add to Textual's own.
CUSTOM_VARIABLES = ("border-muted", "faint", "held", "failing", "tag-text")

RUN_MODES = ("starting", "planning", "running", "gate", "done")
STARTUP_PARTS = ("welcome", "picker", "problem", "text", "summary")
SLOTS = (
    f"{data.AGENT_CLI} · {data.AGENT_SLOTS} agent slots · {data.EVAL_SLOTS} eval slots"
)


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


# ---------------------------------------------------------------------------
# The run screen (ui_design.md sections 6 and 7)
# ---------------------------------------------------------------------------


def item_state(item: data.Item) -> str:
    """The card state an item is in, for the Experiments header's counts."""
    current = [name for name, state in item.steps if state == "current"]
    if current:
        return {
            "build": "building",
            "measure": "measuring",
            "analyze": "analyzing",
            "gate": "at the gate",
            "answering": "asking",
        }[current[0]]
    if all(state == "later" for _, state in item.steps):
        return "queued"
    return "landed"


def wave_counts(items: tuple[data.Item, ...]) -> str:
    order = ("queued", "building", "measuring", "asking", "analyzing")
    order += ("at the gate", "landed")
    states = [item_state(item) for item in items]
    parts = []
    for state in order:
        count = states.count(state)
        if not count:
            continue
        live = state not in ("queued", "landed")
        text = f"{count} {state}"
        parts.append(f"[$text-primary]{text}[/]" if live else text)
    return " [$text-muted]·[/] ".join(parts)


class ExperimentsSection(Vertical):
    """The Experiments section. When it sits beside the Log, the bottom area
    must be at least as tall as it, so the whole screen scrolls instead of
    the cards being cut off."""

    def on_resize(self) -> None:
        bottom = self.screen.query_one("#bottom")
        bottom.styles.min_height = self.outer_size.height


class ResultsSection(Vertical):
    """The Results section. The key sits on the header line when it fits
    there, else on its own line under it."""

    def on_resize(self) -> None:
        header = self.query_one(SectionHeader)
        key = self.query_one(KeyLine)
        title = cell_len("RESULTS") + 3  # the context's margin
        meta = cell_len("Compare in Kiln UI ↗") + 2
        needed = title + Content.from_markup(key.markup).cell_length + meta
        inline = needed <= self.size.width
        # Empty rather than hidden: the context also pushes the link right.
        header.set_context(key.markup if inline else "")
        key.display = not inline


class RunScreen(Screen):
    BINDINGS = [
        Binding("c", "compare", "Compare in Kiln UI"),
        Binding("l", "lineage", "Lineage in Kiln UI"),
        Binding("r", "report", "Report"),
        Binding("q", "stop", "Stop"),
        Binding("ctrl+c", "stop", "Stop", show=False, priority=True),
        Binding("q,enter", "close", "Close"),
        Binding("w", "last_wave", "Last wave", show=False),
    ]

    def __init__(self, mode: str, last_wave: bool = False) -> None:
        super().__init__(classes=f"-{mode}")
        self.state = data.RUN_STATES[mode]
        self.last_wave = last_wave
        self.stopping = False

    # -- composition ------------------------------------------------------

    def compose(self) -> ComposeResult:
        s = self.state
        yield TitleBar(f"[b]{data.TASK}[/] [$text-muted]· run {data.RUN_ID}[/]", SLOTS)
        with VerticalScroll(id="body"):
            with Horizontal(id="top"):
                with Vertical(id="goal", classes="section"):
                    yield SectionHeader("GOAL")
                    yield Markup(self.goal_markup())
                with Vertical(id="spend", classes="section"):
                    yield SectionHeader("TIME & SPEND", meta=self.wave_markup())
                    yield Gauge(
                        "Time", s.elapsed, s.now_min, data.TIME_LIMIT_MIN, "3h00m"
                    )
                    yield Gauge(
                        "Spend", f"${s.spend:.2f}", s.spend, data.BUDGET, "$50.00"
                    )
                    yield Markup(self.lanes_markup(), classes="gauge-note")
            with Vertical(id="timeline", classes="section"):
                yield SectionHeader("RUN TIMELINE", meta=timeline_legend())
                yield Timeline(
                    data.TIMELINE,
                    s.now_min,
                    s.clock,
                    () if self.last_wave else s.projected,
                    data.TIME_LIMIT_MIN,
                    ended=s.mode == "done",
                )
            with ResultsSection(id="results", classes="section"):
                key = self.key_items()
                yield SectionHeader(
                    "RESULTS",
                    context=KeyLine.GAP.join(key),
                    meta=link("Compare in Kiln UI", "screen.compare"),
                )
                yield KeyLine(key, id="results-key")
                yield CardGrid(*self.result_cards(), min_width=34)
            with Container(id="bottom"):
                with ExperimentsSection(id="experiments", classes="section"):
                    yield SectionHeader(
                        "EXPERIMENTS",
                        context=self.experiments_context(),
                        meta=f"[$text-muted]{s.mode_elapsed}[/]",
                        id="experiments-header",
                    )
                    yield from self.experiments_body()
                with Vertical(id="log", classes="section"):
                    yield SectionHeader("LOG")
                    yield RunLog([e for e in data.LOG if e[0] <= s.clock])
        yield Footer()

    def goal_markup(self) -> str:
        rows = (
            (
                "[b $text-success]▲ optimize[/]",
                "Quality",
                "5 evals, weighted by priority",
            ),
            ("[b $text-warning]═ hold    [/]", "Every eval", "none may drop on val"),
            ("[$text-muted]○ watch   [/]", "Cost, latency", "shown, never decide"),
        )
        lines = [f"{a}  {b:<13}  [$text-muted]{c}[/]" for a, b, c in rows]
        lines.append(
            "[$text-muted]  data    [/]  [$text-muted]train[/] 312 [$text-muted]·"
            " val[/] 104 [$text-muted]· test[/] 104  [$text-muted]sealed, never"
            " read[/]"
        )
        return "\n".join(lines)

    def wave_markup(self) -> str:
        s = self.state
        if s.mode == "done":
            return "[$text-muted]7 waves, done[/]"
        if s.wave is None:
            return "[$text-muted]before wave 1[/]"
        if self.last_wave:
            return (
                f"[$text-muted]wave[/] [b]{s.wave}[/] [$text-muted]of {s.wave}[/]  "
                + tag("LAST WAVE")
            )
        return f"[$text-muted]wave[/] [b]{s.wave}[/] [$text-muted]of ~7[/]"

    def lanes_markup(self) -> str:
        s = self.state
        burn = f" · ${s.burn:.2f}/h" if s.burn else ""
        return (
            f"Kiln [$foreground]${s.kiln:.2f}[/] · Claude Code "
            f"[$foreground]${s.agent:.2f}[/]{burn}\n"
            f"started {data.STARTED} · ends {data.ENDS}"
        )

    def key_items(self) -> tuple[str, str, str]:
        s = self.state
        if s.confirmed is None:
            confirmed = "[$text-muted]· no promotion yet[/]"
        else:
            confirmed = (
                f"[$text-muted]· val ·[/] {s.confirmed_rc} "
                f"[$text-muted]{s.confirmed_name}[/]"
            )
        return (
            f"[$text-muted]■[/] Start [$text-muted]· {s.start_split} · starting"
            " point[/]",
            f"[$foreground]■[/] Best Confirmed {confirmed}",
            "[$warning]■[/] Best Unconfirmed [$text-muted]· train estimate[/]",
        )

    def result_cards(self) -> list[ResultCard]:
        s = self.state
        live = [item for item in s.items if item.live]
        cards = []
        for index, concern in enumerate(data.CONCERNS):
            in_flight = sum(concern.name in item.targets for item in live)
            cards.append(
                ResultCard(
                    concern,
                    s.start[index],
                    s.confirmed[index] if s.confirmed else None,
                    s.unconfirmed.get(index),
                    in_flight,
                )
            )
        return cards

    def experiments_context(self) -> str:
        s = self.state
        if self.stopping:
            live = sum(item.live for item in s.items)
            return (
                "[$text-primary]{spin}[/] [b $text-error]Stopping…[/] "
                f"[$text-muted]· cancelling {plural(live, 'experiment')}[/]"
            )
        if s.mode == "starting":
            return (
                "[$text-primary]{spin}[/] [b]Starting[/] [$text-muted]· wave 0,"
                " recon ·[/] 2 of 3 probes answered"
            )
        if s.mode == "planning":
            return (
                "[$text-primary]{spin}[/] [b]Planning[/] [$text-muted]· wave 5"
                " from[/] w4-2"
            )
        if s.mode == "running":
            return (
                "[b]Running[/] [$text-muted]· wave 4 from[/] w3-4 [$text-muted]·[/] "
                + wave_counts(s.items)
            )
        if s.mode == "gate":
            return (
                "[b]Promotion gate[/] [$text-muted]· wave 4 · every eval on val ·[/] "
                + wave_counts(s.items)
            )
        return "[b]Done[/] [$text-muted]· 7 waves[/]"

    def experiments_body(self) -> ComposeResult:
        s = self.state
        if s.steps:
            with Vertical(classes="step-lines"):
                for step in s.steps:
                    yield StepLineView(step)
        if s.mode == "starting":
            yield Markup("WAVE 0 · recon probes", classes="subhead")
        elif s.mode == "planning":
            yield Markup("WAVE 4 · landed", classes="subhead")
        if s.mode == "done":
            yield self.run_complete()
        else:
            yield CardGrid(*(ExperimentCard(item) for item in s.items), min_width=44)
        if s.mode == "starting":
            yield Markup(
                "Wave 1 is planned when the recon probes finish.", classes="wave-note"
            )

    def run_complete(self) -> Card:
        s = self.state
        totals = " [$text-muted]·[/] ".join(
            f"[b]{count}[/] [$text-muted]{word}[/]"
            for count, word in data.RUN_COMPLETE["totals"]
        )
        links = "   ".join(
            (
                link("Compare in Kiln UI", "screen.compare"),
                link("Lineage in Kiln UI", "screen.lineage"),
                link("Report", "screen.report"),
            )
        )
        lines = (
            f"[b]{data.RUN_COMPLETE['ended']}[/] [$text-muted]·[/] {s.elapsed} "
            f"[$text-muted]·[/] ${s.spend:.2f} [$text-muted]of $50[/]",
            totals,
            "",
            f"[$text-muted]Best Confirmed[/]  [b]{s.confirmed_rc}[/] "
            f"[$text-muted]·[/] {s.confirmed_name} [$text-muted]· measured on val[/]",
            f"[$text-muted]Report        [/]  {data.REPORT_PATH}",
            "",
            links,
            "",
            f"Next: run your test split on [b]{s.confirmed_rc}[/]. The tuner never"
            " read it.",
        )
        return Card(
            Markup("\n".join(lines)),
            title="[b]RUN COMPLETE[/]",
            subtitle=f"[$text-muted]run {data.RUN_ID}[/]",
            id="run-complete",
        )

    # -- actions ----------------------------------------------------------

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        done = self.state.mode == "done"
        if action in ("report", "close"):
            return done
        if action in ("stop", "last_wave"):
            return not done
        return True

    def action_compare(self) -> None:
        self.app.action_open_link(data.COMPARE_URL)

    def action_lineage(self) -> None:
        self.app.action_open_link(data.LINEAGE_URL)

    def action_report(self) -> None:
        self.app.action_open_link(data.REPORT_PATH)

    def action_close(self) -> None:
        self.notify("The app closes and prints the closing block.", title="Close")

    def action_last_wave(self) -> None:
        screen = RunScreen(self.state.mode, last_wave=not self.last_wave)
        self.app.switch_screen(screen)

    def action_stop(self) -> None:
        live = sum(item.live for item in self.state.items)
        if self.state.mode == "planning":
            message = (
                "The planner is at work on wave 5. Its work so far is lost, and no"
                " report is written."
            )
        else:
            are = "is" if live == 1 else "are"
            message = (
                f"{plural(live, 'experiment')} {are} in flight. Their work so far"
                " is lost, and no report is written."
            )
        self.app.push_screen(StopDialog(message), self.stopped)

    def stopped(self, stop: bool | None) -> None:
        if stop:
            self.stopping = True
            header = self.query_one("#experiments-header", SectionHeader)
            header.set_context(self.experiments_context())


class StopDialog(ModalScreen[bool]):
    """4.11: a card centered over the dimmed screen. Keep running has the
    focus; Esc keeps running."""

    BINDINGS = [
        Binding("escape", "keep", "Keep running"),
        Binding("left", "app.focus_previous", "Move", show=False),
        Binding("right", "app.focus_next", "Move", show=False),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Card(title="[b]Stop the run?[/]", id="dialog"):
            yield Markup(self.message)
            with Horizontal(id="dialog-buttons"):
                yield Button("Keep running", id="keep", compact=True)
                yield Button("Stop the run", id="stop", compact=True, variant="error")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#keep", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "stop")

    def action_keep(self) -> None:
        self.dismiss(False)


# ---------------------------------------------------------------------------
# The start-up screen (ui_design.md section 8)
# ---------------------------------------------------------------------------

HISTORY_UP_TO = {"welcome": 0, "picker": 1, "problem": 3, "text": 8, "summary": 12}


class StartupScreen(Screen):
    """One transcript that grows down. Each preview part is the transcript
    at one moment: the step history so far, then the part being asked."""

    BINDINGS = [
        Binding("ctrl+c", "stop(130)", "Stop", priority=True),
        Binding("ctrl+d", "stop(1)", "Exit", priority=True),
        # Preview only: a focused Input takes every printable key, priority
        # bindings included, so Esc leaves it to give the preview keys back.
        Binding("escape", "leave_input", "Leave the input", show=False),
    ]

    def __init__(self, part: str) -> None:
        super().__init__()
        self.part = part

    def compose(self) -> ComposeResult:
        history = data.HISTORY[: HISTORY_UP_TO[self.part]]
        task_known = len(history) >= 3
        yield TitleBar(
            f"[b]{data.TASK}[/]" if task_known else "",
            "",
        )
        with VerticalScroll(id="transcript"):
            yield Card(
                Markup(data.WELCOME_DESCRIPTION),
                title=f"[b $text-primary]{data.WELCOME_TITLE}[/]",
                subtitle=f"[$text-muted]{data.VERSION}[/]",
                id="welcome",
            )
            for step in history:
                yield StepLineView(step)
            yield from self.part_widgets()
        yield Footer()

    def part_widgets(self) -> ComposeResult:
        if self.part == "welcome":
            yield StepLineView(
                data.StepLine("busy", "", f"Checking Kiln at {data.KILN_HOST}…")
            )
        elif self.part == "picker":
            yield Picker(
                "Which project do you want to tune?", data.PROJECT_PICK, data.PICK_HINT
            )
        elif self.part == "problem":
            problem = data.PROBLEM
            lines: list[Widget] = [Markup(escape("\n".join(problem.body)))]
            for warning in problem.warnings:
                lines.append(StepLineView(data.StepLine("warn", "", warning)))
            yield Card(
                *lines,
                title=f"[b $text-error]✗ {escape(problem.title)}[/]",
                classes="startup-card -problem",
            )
            yield ActionMenu("What next?", problem.actions)
        elif self.part == "text":
            question = data.MODEL_QUESTION
            yield TextAnswer(
                question.question, question.typed, question.hint, question.error
            )
        else:
            width = 12
            summary = "\n".join(
                f"[$text-muted]{label:<{width}}[/] {escape(text)}"
                for label, text in data.SUMMARY_LINES
            )
            rerun = escape("\n".join((data.RERUN_HEADING, *data.RERUN_COMMAND)))
            yield Card(
                Markup(summary),
                Markup(f"\n[$text-muted]{rerun}[/]"),
                title="[b]Ready to start[/]",
                classes="startup-card",
            )
            yield ActionMenu("Start the run?", data.START_ACTIONS)

    def on_mount(self) -> None:
        self.query_one("#transcript").scroll_end(animate=False)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "leave_input":
            return isinstance(self.focused, Input)
        return True

    def action_leave_input(self) -> None:
        self.set_focus(None)

    def action_stop(self, code: int) -> None:
        text = "harness-tuner: stopped." if code == 130 else "Stopped."
        self.notify(f"{text} (exit {code})", title="The flow would stop")


# ---------------------------------------------------------------------------
# The card gallery
# ---------------------------------------------------------------------------


class GalleryScreen(Screen):
    """Every experiment card variant of ui_design.md section 7.5."""

    def compose(self) -> ComposeResult:
        yield TitleBar("[b]Card gallery[/]", "every experiment card variant")
        with VerticalScroll(id="gallery"):
            yield SectionHeader(
                "EXPERIMENT CARDS",
                context="[$text-muted]ui_design.md section 7.5 · one per item type"
                " and state[/]",
            )
            cells = [
                Vertical(
                    Markup(caption, classes="caption"),
                    ExperimentCard(item),
                    classes="gallery-cell",
                )
                for caption, item in data.GALLERY
            ]
            yield CardGrid(*cells, min_width=44)
        yield Footer()


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------

RUN_GROUP = Binding.Group("Run", compact=True)
STARTUP_GROUP = Binding.Group("Start-up", compact=True)


class PreviewApp(App[None]):
    """The preview. `animate=False` fixes the spinner frame and the cursor,
    so screenshots come out the same every time."""

    CSS_PATH = "preview.tcss"
    TITLE = "Kiln Harness Tuner · UI preview"
    ENABLE_COMMAND_PALETTE = False
    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (150, "-wide")]

    BINDINGS = [
        *(
            Binding(
                str(i + 1),
                f"run('{mode}')",
                mode.title(),
                group=RUN_GROUP,
                priority=True,
                show=False,
            )
            for i, mode in enumerate(RUN_MODES)
        ),
        *(
            Binding(
                str((i + 6) % 10),
                f"startup('{part}')",
                part.title(),
                group=STARTUP_GROUP,
                priority=True,
                show=False,
            )
            for i, part in enumerate(STARTUP_PARTS)
        ),
        Binding("g", "gallery", "Cards", show=False),
        Binding("t", "toggle_theme", "Theme", show=False),
    ]

    def __init__(
        self, *, start: str = "running", theme: str = "kiln-dark", animate: bool = True
    ) -> None:
        super().__init__()
        self.register_theme(KILN_DARK)
        self.register_theme(KILN_LIGHT)
        self.theme = theme
        self.first_screen = start
        self.animate = animate
        self.frame = 3  # the spinner frame: ⠸

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """The variables the themes add, so the stylesheet always parses."""
        return {name: KILN_DARK.variables[name] for name in CUSTOM_VARIABLES}

    def on_mount(self) -> None:
        self.push_screen(self.make_screen(self.first_screen))
        if self.animate:
            self.set_interval(0.1, self.tick)

    def tick(self) -> None:
        self.frame += 1
        for screen in self.screen_stack:
            for widget in screen.query(".-spins"):
                widget.on_spin()  # type: ignore[attr-defined]

    def make_screen(self, name: str) -> Screen:
        if name in RUN_MODES:
            return RunScreen(name)
        if name in STARTUP_PARTS:
            return StartupScreen(name)
        return GalleryScreen()

    def show(self, name: str) -> None:
        while isinstance(self.screen, ModalScreen):
            self.pop_screen()
        self.switch_screen(self.make_screen(name))

    def action_run(self, mode: str) -> None:
        self.show(mode)

    def action_startup(self, part: str) -> None:
        self.show(part)

    def action_gallery(self) -> None:
        self.show("gallery")

    def action_toggle_theme(self) -> None:
        self.theme = "kiln-light" if self.theme == "kiln-dark" else "kiln-dark"

    def action_open_link(self, url: str) -> None:
        self.notify(url, title="Opens in the browser")


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else "running"
    PreviewApp(start=start).run()
