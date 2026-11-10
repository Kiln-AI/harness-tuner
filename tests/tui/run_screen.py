"""The run screen's pilot harness: the app over a `Run` of `model_events`.

The app's model is the run's own, and the app's clock is the run's clock,
so the views a test reads are the ones the screen shows. The drain timer is
off: `shown` drains and lets the screen settle. Links open into `opened`,
unless the test asks for the app's own opener.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import AsyncIterator
from typing import Any

from app_fakes import SIZE, FakeRun, make_app, make_session, until
from model_events import Run, fix, probe, row, table, val_row
from textual.pilot import Pilot
from textual.widget import Widget

from kiln_harness_tuner.tui.app import NamesLookup, TunerApp
from kiln_harness_tuner.tui.screens.run import RunScreen
from kiln_harness_tuner.tui.theme import DARK
from kiln_harness_tuner.tui.widgets import SPINNER

_SPIN = re.compile(f"[{SPINNER}]")


def unspun(text: str) -> str:
    """The text with each spinner frame as `*`: the frame turns with time."""
    return _SPIN.sub("*", text)


def text(widget: Widget) -> str:
    """What a widget draws now, as plain text, spinners as `*`."""
    return unspun(str(widget.render()))


def title_of(widget: Widget) -> str:
    content = widget._border_title  # the Content Textual keeps
    return unspun(content.plain if content is not None else "")


def subtitle_of(widget: Widget) -> str:
    content = widget._border_subtitle
    return unspun(content.plain if content is not None else "")


class Harness:
    def __init__(
        self,
        run: Run | None = None,
        *,
        lookup: NamesLookup | None = None,
        theme: str = DARK,
        catch_links: bool = True,
    ):
        self.run = run if run is not None else Run()
        self.fake = FakeRun()
        self.session = make_session(
            run=self.fake, model=lambda: self.run.model, lookup_names=lookup
        )
        self.app: TunerApp = make_app(
            self.session, clock=lambda: self.run.t, drain_interval=60, theme=theme
        )
        self.opened: list[str] = []

        def open_link(url: str) -> None:
            self.opened.append(url)

        # A click or a key opens a link through the app's action. Without
        # `catch_links` the app's own action runs, and its opener.
        if catch_links:
            self.app.action_open_link = open_link  # type: ignore[method-assign]

    @property
    def screen(self) -> RunScreen:
        screen = self.app.run_screen
        assert screen is not None
        return screen

    async def shown(self, pilot: Pilot[Any]) -> RunScreen:
        """Drain the queue (the run's moves land in the model at once), and
        let the screen mount what it made."""
        self.app.drain()
        await pilot.pause()
        await pilot.pause()
        return self.screen


@contextlib.asynccontextmanager
async def on_screen(
    run: Run | None = None, *, size: tuple[int, int] = SIZE, **kwargs: Any
) -> AsyncIterator[tuple[Harness, Pilot[Any]]]:
    """The app on its run screen, showing `run`. The fake run ends when the
    test does."""
    harness = Harness(run, **kwargs)
    app = harness.app
    try:
        async with app.run_test(size=size) as pilot:
            await until(pilot, lambda: harness.session.start.outcome is not None)
            app.drain()
            await until(pilot, lambda: app.run_screen is not None)
            await until(pilot, lambda: harness.screen.is_mounted)
            await harness.shown(pilot)
            yield harness, pilot
    finally:
        harness.fake.release.set()
        harness.session.run.join(5)


# ---- runs at each mode ----


def wave_one(*items: Any, **start: Any) -> Run:
    """A started run (two pass-rate evals), wave 1 planned with `items`."""
    run = Run().standard_start(**start)
    run.model.agent_cli_checked(run.t - 2)
    run.at(10).plan(1).at(12).planned(1)
    return run.wave_start(1, *items)


def running() -> Run:
    """Wave 1 with one card in each state, 20 minutes in."""
    run = wave_one(
        fix("w1-1", hypothesis="Return only the hold fields from get_container"),
        fix("w1-2", "e2", technique="skill"),
        fix("w1-3"),
        probe("w1-4"),
        fix("w1-5"),
    )
    run.at(13).state("w1-1", "building_rc")
    run.state("w1-2", "building_rc").state("w1-2", "measuring")
    run.chunk("w1-2", "rc_b", [(False, True), (True, True), (False, False)])
    run.state("w1-4", "probing")
    run.success("w1-3", "rc_c", row("e1", 0.6, 0.7))
    return run.at(20)


def gated() -> Run:
    """Wave 1's success at the gate, then promoted."""
    run = wave_one(fix("w1-1"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7))
    run.at(30).gate(1, table("rc_a", val_row("e1", 0.6, 0.7)), winner="rc_a", end=False)
    return run


def done() -> Run:
    run = wave_one(fix("w1-1"), fix("w1-2"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7))
    run.state("w1-2", "measuring")
    run.outcome("w1-2", "not_landed", rc_id="rc_b", stop_kind="futility")
    run.emit("spend", source="x", lane="provider", cost_usd=12.5)
    run.at(30).gate(1, table("rc_a", val_row("e1", 0.6, 0.7)), winner="rc_a")
    run.model.names.learn([{"id": "rc_a", "name": "Hold fields", "description": "x"}])
    run.at(170).emit("exit", reason="wrap_up")
    run.emit("report_written", path="/state/reports/report.md", fallback=False)
    run.emit("run_ended")
    return run
