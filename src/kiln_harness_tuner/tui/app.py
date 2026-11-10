"""The terminal app of an interactive run (architecture sections 1, 6, 7.3).

The app runs on the main thread. The start-up and the run run on threads of
their own (`handles.py`) and put stamped events on one queue. A timer drains
the queue: the threads' ended events move the app on, and every other event
is folded into the run model, which the run screen shows. Nothing on the
threads waits for the app. The app reaches the run only by cancelling it.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Any, Literal

from textual import events
from textual.app import App
from textual.binding import Binding

from kiln_harness_tuner.onboarding.error_screen import RunProgress
from kiln_harness_tuner.tui import opener
from kiln_harness_tuner.tui.bridge import UI_REQUEST, opened_text
from kiln_harness_tuner.tui.handles import (
    RUN_ENDED,
    START_ENDED,
    RunHandle,
    StartHandle,
)
from kiln_harness_tuner.tui.model import words
from kiln_harness_tuner.tui.model.events import Stamped
from kiln_harness_tuner.tui.model.fold import RunModel
from kiln_harness_tuner.tui.model.state import ItemKey, RunInfo
from kiln_harness_tuner.tui.screens.details import (
    DetailsPanel,
    ExperimentPicker,
    RunModal,
)
from kiln_harness_tuner.tui.screens.run import RunScreen
from kiln_harness_tuner.tui.screens.startup import StartupScreen
from kiln_harness_tuner.tui.screens.stop import StopDialog
from kiln_harness_tuner.tui.terminal_io import SignalHooks, loop_signals
from kiln_harness_tuner.tui.theme import CUSTOM_VARIABLES, DARK, KILN_DARK, THEMES

logger = logging.getLogger("kiln_harness_tuner.tui")

# The app's two modes (architecture section 2): the start only, then plain
# lines for the run; or the start and the run.
AppMode = Literal["app_startup", "app"]
Phase = Literal["startup", "run", "done"]
DRAIN_INTERVAL_SECS = 0.25
# How long a stopped start may take to unwind before the app closes anyway.
# A prompt or a Kiln read unwinds at once. The demo copy, which can't be cut
# short, is waited for in full (`Session.uninterruptible`).
START_STOP_GRACE_SECS = 2.0


def _never() -> bool:
    return False


# The task's run configs, as Kiln lists them: the rows the run screen takes
# its run config names from (architecture section 8).
NamesLookup = Callable[[RunInfo], Awaitable[Iterable[Mapping[str, Any]]]]


@dataclass(frozen=True)
class Session:
    """What the app runs over: the queue both threads put on, the two
    threads, the run model's maker (called at the hand-off, once the start
    has filled in `args`), and how far `_run` got (`cli._RUN_PROGRESS`)."""

    mode: AppMode
    queue: SimpleQueue[Stamped]
    start: StartHandle
    run: RunHandle
    make_model: Callable[[], RunModel]
    progress: Callable[[], RunProgress]
    # Whether the start is in a step a stop can't cut short (the demo copy).
    # A first stop then waits for the start to end, with no grace period.
    uninterruptible: Callable[[], bool] = _never
    # Where the run config names come from; None looks nothing up.
    lookup_names: NamesLookup | None = None


class TunerApp(App[None]):
    CSS_PATH = Path(__file__).with_name("tuner.tcss")
    ENABLE_COMMAND_PALETTE = False
    # Priority bindings: a focused input takes Ctrl+C and Ctrl+D otherwise.
    # Ctrl+Q does what Ctrl+C does, so it never quits without the dialog.
    # `check_action` turns on the pair of the current phase.
    BINDINGS = [
        Binding("ctrl+c", "stop_start(130)", "Stop", priority=True),
        Binding("ctrl+d", "stop_start(1)", "Exit", priority=True),
        Binding("ctrl+q", "stop_start(130)", priority=True, show=False),
        Binding("ctrl+c,ctrl+q", "ask_stop", priority=True, show=False),
    ]

    def __init__(
        self,
        session: Session,
        *,
        theme: str = DARK,
        signals: SignalHooks | None = loop_signals,
        print_sink: Callable[[str], None] | None = None,
        drain_interval: float = DRAIN_INTERVAL_SECS,
        stop_grace: float = START_STOP_GRACE_SECS,
        clock: Callable[[], float] = time.monotonic,
    ):
        super().__init__()
        # Before the first screen mounts (ui_design section 13).
        for kiln_theme in THEMES:
            self.register_theme(kiln_theme)
        self.theme = theme
        self.session = session
        self.phase: Phase = "startup"
        self.model: RunModel | None = None
        self.startup_screen = StartupScreen()
        self.run_screen: RunScreen | None = None
        # The app closed itself: a stop, the end, or the person. False after
        # a crash.
        self.closed_on_purpose = False
        self._signals = signals
        self._remove_signals: Callable[[], None] | None = None
        self._print_sink = print_sink
        self._drain_interval = drain_interval
        self._stop_grace = stop_grace
        # The clock the run screen's views read: the run's own monotonic
        # clock, which stamps the events.
        self._clock = clock
        self._shown: tuple[int, bool, int] | None = None

    def get_theme_variable_defaults(self) -> dict[str, str]:
        return {name: KILN_DARK.variables[name] for name in CUSTOM_VARIABLES}

    @property
    def crash(self) -> BaseException | None:
        """The first error that escaped a handler, a timer or a render. It
        closed the app: Textual has no error boundary (research section 5)."""
        # `App._exception` is private API: `textual<9` pins it, and the entry
        # crash tests cover it.
        return self._exception

    # ---- life ----

    def on_mount(self) -> None:
        if self._signals is not None:
            self._remove_signals = self._signals(
                asyncio.get_running_loop(), self.on_signal
            )
        if self._print_sink is not None:
            # Textual drops Python-level writes unless a target captures them.
            self.begin_capture_print(self)
        self.push_screen(self.startup_screen)
        self.set_interval(self._drain_interval, self.drain)
        self.session.start.begin()

    def on_unmount(self) -> None:
        if self._remove_signals is not None:
            self._remove_signals()
            self._remove_signals = None

    def on_print(self, event: events.Print) -> None:
        if self._print_sink is not None:
            self._print_sink(event.text)

    def close_app(self) -> None:
        if not self.closed_on_purpose:
            self.closed_on_purpose = True
            self.exit()

    def action_open_link(self, url: str) -> None:
        """A click on a link, or a link's key (ui_design 4.9). Not
        `App.open_url`, which waits for the browser and lets it draw on the
        screen. With no graphical browser, or over SSH, nothing opens: after
        the start, the run screen's log then gives the whole address, in the
        start-up's words (functional spec section 5)."""
        if opener.open_link(url):
            return
        model = self.model
        if model is not None and self.phase != "startup":
            model.note_link(opened_text(url, opened=False))
            self._refresh()

    def _set_phase(self, phase: Phase) -> None:
        self.phase = phase
        self.refresh_bindings()

    def now(self) -> float:
        """The run's clock, which the run screen's views read."""
        return self._clock()

    # ---- the details panels (functional spec 7.9) ----

    def show_item(self, key: ItemKey, *, from_picker: bool = False) -> None:
        """A click on an experiment card, or Enter in the picker."""
        model = self.model
        if model is not None and model.item_at(key) is not None:
            self.push_screen(DetailsPanel(key, from_picker=from_picker))

    def show_picker(self) -> None:
        """The `e` key: every item of the run, to open one."""
        if self.model is not None:
            self.push_screen(ExperimentPicker())

    # ---- the drain ----

    def drain(self) -> None:
        """Everything on the queue now. A thread's ended event moves the app
        on; any other event is folded into the model, and an event the model
        can't fold is logged and skipped. Then the run screen shows the
        model, if it changed."""
        queue = self.session.queue
        for _ in range(queue.qsize()):
            try:
                stamped = queue.get_nowait()
            except Empty:
                break
            if stamped.kind == UI_REQUEST:
                self._show_request(stamped)
                continue
            if stamped.kind == START_ENDED:
                self._start_ended()
                continue
            if stamped.kind == RUN_ENDED:
                self._run_ended()
            self._fold(stamped)
        self._read_progress()
        self._look_up_names()
        self._refresh()

    def _show_request(self, stamped: Stamped) -> None:
        """A `Terminal` call of the start, on the start-up screen, which
        fails a prompt it can't show (`StartupScreen.show`)."""
        self.startup_screen.show(stamped.fields["request"])

    def _fold(self, stamped: Stamped) -> None:
        model = self.model
        if model is None:
            return
        try:
            model.apply(stamped)
        except Exception:
            logger.exception("the app could not show a %s event", stamped.kind)
            model.note_ui_error()

    def _read_progress(self) -> None:
        """What `_run` records but no event carries: the agent CLI's test
        prompt passed, and the run id (the run folder's name)."""
        model = self.model
        if model is None:
            return
        progress = self.session.progress()
        if model.cli_checked is None and progress.cli_checked:
            model.agent_cli_checked(self._clock())
        if model.info.run_id is None and progress.run_dir is not None:
            model.update_info(run_id=progress.run_dir.name)

    def _refresh(self) -> None:
        """The run screen shows the model again when it changed, when the
        stop began, or once a second: the elapsed times and the gauges move
        with no event. Each widget still updates only when its text did."""
        model, screen = self.model, self.run_screen
        # Not yet mounted in the drain that switched to it: the next one shows.
        if model is None or screen is None or not screen.is_mounted:
            return
        stopping = self.session.run.stopping
        now = self._clock()
        shown = (model.version, stopping, int(now))
        if self._shown == shown:
            return
        self._shown = shown
        try:
            screen.refresh_from(model, stopping=stopping, now=now)
        except Exception:
            logger.exception("the run screen could not show the run")
        # An open panel shows the run live too, under a stop dialog as well.
        for modal in self.screen_stack:
            if isinstance(modal, RunModal):
                try:
                    modal.show(model, now=now)
                except Exception:
                    logger.exception("a details panel could not show the run")

    # ---- the run config names ----

    def _look_up_names(self) -> None:
        """One lookup at a time, at most once every 10 s, when the model has
        seen a run config id it has no name for (architecture section 8). It
        runs on the app's own loop: an async worker, not a thread."""
        model, lookup = self.model, self.session.lookup_names
        if model is None or lookup is None:
            return
        now = self._clock()
        if not model.names.lookup_due(now):
            return
        model.names.lookup_started(now)
        self.run_worker(
            self._names_from(model, lookup),
            group="run-config-names",
            exit_on_error=False,
        )

    async def _names_from(self, model: RunModel, lookup: NamesLookup) -> None:
        try:
            model.learn_names(await lookup(model.info))
        except Exception:
            # The ids stay on screen; the next unknown id asks again.
            logger.warning("the run config names could not be read", exc_info=True)
            model.names.lookup_failed()

    # ---- the hand-off and the end ----

    def _start_ended(self) -> None:
        if self.phase != "startup":
            return
        start = self.session.start
        outcome = start.outcome
        if (
            start.stopped is not None
            or outcome is None
            or outcome.error is not None
            or self.session.mode == "app_startup"
        ):
            self.close_app()
            return
        self.model = self.session.make_model()
        self.run_screen = RunScreen()
        self.switch_screen(self.run_screen)
        self._set_phase("run")
        self.session.run.begin()

    def _run_ended(self) -> None:
        run = self.session.run
        outcome = run.outcome
        if run.stopping or outcome is None or outcome.error is not None:
            # The error, or the interrupted line, prints once the app is
            # gone; there is nothing more to show here.
            self.close_app()
            return
        # Nothing is in flight any more: a stop dialog still open would ask
        # about a finished run. `pop_screen` does not answer it.
        if isinstance(self.screen, StopDialog):
            self.pop_screen()
        self._set_phase("done")

    # ---- stopping ----

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "stop_start":
            return self.phase == "startup"
        if action == "ask_stop":
            return self.phase != "startup"
        return True

    def action_stop_start(self, code: int) -> None:
        """Ctrl+C (130) or Ctrl+D (1) during the start: nothing is in flight,
        so there is no dialog (functional spec section 5). The stop fails the
        open prompt and cancels the Kiln read in flight, and the app closes
        once the start has unwound (`_start_ended`), or after the grace
        period; during the demo copy, only once the start has unwound. A
        second stop closes it at once."""
        start = self.session.start
        again = start.stopped is not None
        start.stop(code)
        if again or not start.alive:
            self.close_app()
        elif not self.session.uninterruptible():
            self.set_timer(self._stop_grace, self.close_app)

    def action_ask_stop(self) -> None:
        """`q`, Ctrl+C or Ctrl+Q after the start. On Done it closes. While
        the run is already stopping, it closes the app at once, as a second
        signal does. Else it opens the stop dialog."""
        if self.phase == "done" or self.session.run.stopping:
            self.close_app()
            return
        if isinstance(self.screen, StopDialog):
            return
        model = self.model
        # `planning_wave` stays set after the planner ends: name the planner
        # only while it plans.
        planning = (
            model.planning_wave
            if model is not None and model.mode == "planning"
            else None
        )
        message = words.stop_question(
            model.live_count() if model is not None else 0, planning
        )
        self.push_screen(StopDialog(message), self._stop_answered)

    def _stop_answered(self, stop: bool | None) -> None:
        if stop and self.phase == "run":
            # What a Ctrl+C does to a plain run: `main` then prints
            # "interrupted (SIGINT)" and exits 130.
            self._stop_run(signal.SIGINT)

    def on_signal(self, signum: int) -> None:
        """SIGTERM, SIGHUP or SIGINT, on the app's loop. During the start it
        stops the start with 128 + the signal. During the run the first one
        stops the run, and a second one closes the app at once; the run's
        teardown then goes on with plain lines. On Done it closes."""
        if self.phase == "startup":
            self.action_stop_start(128 + signum)
        elif self.phase == "run":
            self._stop_run(signum)
        else:
            self.close_app()

    def _stop_run(self, signum: int) -> None:
        if isinstance(self.screen, StopDialog):
            self.pop_screen()
        if self.session.run.stop(signum):
            self._refresh()
        else:
            self.close_app()
