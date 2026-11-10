"""Save an SVG screenshot of the real app in each state of the UI preview.

The run screen shows `sample_run.py`, the preview's story as loop events.
The start-up screen runs the real `prepare_start` on a fake Kiln, as
`tests/tui/test_startup_flow.py` does. The app runs headless with
`App.run_test`, with the spinner frame fixed, at the preview's sizes, so a
file here and the file of the same name in `../ui_preview/screenshots/`
show the same moment.

Run it from the repository root. It reads the test suite's fakes:

    PYTHONPATH=tests:tests/tui:specs/projects/new-tui/app_screenshots \\
        uv run python specs/projects/new-tui/app_screenshots/make_screenshots.py
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import sample_run
from app_fakes import Asking, prompted, until
from onboarding_fakes import (
    ADVANCED_PROMPT,
    ANSWERED,
    FOLDER,
    PROG,
    PROJECT_ID,
    TASK_ID,
    RouteKiln,
    one_project_routes,
    responses,
    run_config,
    task_base,
)
from run_screen import on_screen
from textual.pilot import Pilot
from textual.widgets import Input

from kiln_harness_tuner.cli import build_parser, load_model_policy
from kiln_harness_tuner.onboarding import OnboardingStopped
from kiln_harness_tuner.onboarding.flow import START_QUESTION, prepare_start
from kiln_harness_tuner.tui import widgets
from kiln_harness_tuner.tui.app import TunerApp
from kiln_harness_tuner.tui.bridge import CancellableRunner
from kiln_harness_tuner.tui.screens.prompts import ActionMenu, Picker, TextAnswer
from kiln_harness_tuner.tui.theme import DARK, LIGHT

OUT = Path(__file__).parent
WIDE = (210, 62)
NARROW = (120, 40)

# One spinner frame, so a file changes only when the screen does.
widgets.spinner_frame = lambda now=None: widgets.SPINNER[3]


async def settle(pilot: Pilot[Any]) -> None:
    """Let the layout settle: the grids pick their column counts on the
    first resize, and the log renders once its width is known."""
    for _ in range(4):
        await pilot.pause()


def save(app: TunerApp, name: str, title: str) -> Path:
    for answer in app.screen.query(Input):
        answer.cursor_blink = False
    path = OUT / f"{name}.svg"
    path.write_text(app.export_screenshot(title=title), encoding="utf-8")
    return path


# ---- the run screen ----


async def press(app: TunerApp, pilot: Pilot[Any], keys: tuple[str, ...]) -> None:
    """Press `keys`. The key "end" scrolls the run screen to its bottom."""
    for key in keys:
        if key == "end":
            app.screen.query_one("#body").scroll_end(animate=False)
        else:
            await pilot.press(key)
        await pilot.pause()


async def run_shot(
    name: str,
    title: str,
    run: sample_run.Run,
    *,
    size: tuple[int, int] = WIDE,
    theme: str = DARK,
    keys: tuple[str, ...] = (),
    ended: bool = False,
) -> Path:
    async with on_screen(run, size=size, theme=theme) as (harness, pilot):
        app = harness.app
        if ended:
            harness.fake.release.set()
            # The harness drains by hand: the run's end is on the queue.
            await until(pilot, lambda: (app.drain(), app.phase == "done")[1])
            await harness.shown(pilot)
        await settle(pilot)
        await press(app, pilot, keys)
        await settle(pilot)
        return save(app, name, title)


# ---- the start-up screen ----


def connect_to(kiln: RouteKiln):
    @asynccontextmanager
    async def connect():
        yield kiln

    return connect


@asynccontextmanager
async def start_up(routes, argv: list[str], *, theme=DARK, kiln=None):
    """`prepare_start` with `argv` on a fake Kiln (`routes`), in the app: the
    screen as the start shows it."""
    runner = CancellableRunner(connect_to(kiln or RouteKiln(routes)))
    parser = build_parser()
    args = parser.parse_args(argv)

    def start(terminal):
        return prepare_start(
            args,
            argv,
            prog=PROG,
            option_dests=parser.option_dests,
            policy_loader=load_model_policy,
            terminal=terminal,
            runner=runner,
            which=lambda name: f"/usr/local/bin/{name}",
        )

    asking = Asking(start, runner=runner, theme=theme)
    async with asking.app.run_test(size=WIDE) as pilot:
        yield asking.app, pilot
        # Unwind the start, as a stop does.
        asking.terminal.abandon(OnboardingStopped(130))
        runner.cancel()


async def menu(pilot: Pilot[Any], question: str) -> None:
    def asking() -> bool:
        menus = pilot.app.screen.query(ActionMenu)
        return bool(menus) and menus.last().request.question == question

    await until(pilot, asking)
    await prompted(pilot, ActionMenu)


EVERY_FLAG = ["--project-id", PROJECT_ID, "--task-id", TASK_ID]
EVERY_FLAG += ["--project-path", FOLDER]


class SlowPing(RouteKiln):
    """A Kiln whose ping does not answer yet: the Kiln check's spinner."""

    async def call(self, method, url_path, *args, **kwargs):
        if url_path == "/ping":
            await asyncio.sleep(60)
        return await super().call(method, url_path, *args, **kwargs)


async def startup_shots() -> list[Path]:
    paths = []
    # The welcome card, and the first check with its spinner.
    routes = one_project_routes()
    async with start_up(routes, EVERY_FLAG, kiln=SlowPing(routes)) as (app, pilot):
        await until(pilot, lambda: bool(app.screen.query(".busy-line")))
        await settle(pilot)
        paths.append(save(app, "startup-welcome-dark-210x62", "Start-up · Busy"))
    # The first question, with the welcome card and the checks above it.
    for theme, name in ((DARK, "dark"), (LIGHT, "light")):
        async with start_up(one_project_routes(), EVERY_FLAG, theme=theme) as (
            app,
            pilot,
        ):
            await prompted(pilot, Picker)
            await settle(pilot)
            paths.append(
                save(app, f"startup-picker-{name}-210x62", "Start-up · Picker")
            )
    # A failed check: the task has no run config.
    routes = one_project_routes()
    base = task_base(PROJECT_ID, TASK_ID)
    routes[("GET", f"{base}/run_configs")] = responses([], [run_config()])
    async with start_up(routes, [*EVERY_FLAG, *ANSWERED]) as (app, pilot):
        await menu(pilot, "What next?")
        await settle(pilot)
        paths.append(save(app, "startup-problem-dark-210x62", "Start-up · Problem"))
    # A text answer the validator refuses.
    budget = list(ANSWERED)
    at = budget.index("--budget-usd")
    del budget[at : at + 2]
    async with start_up(one_project_routes(), [*EVERY_FLAG, *budget]) as (app, pilot):
        await prompted(pilot, TextAnswer)
        await pilot.press("ctrl+u", *"lots", "enter")
        await settle(pilot)
        paths.append(
            save(app, "startup-text-answer-dark-210x62", "Start-up · Text answer")
        )
    # The guidance question, with its hint of one example per line.
    guidance = list(ANSWERED)
    at = guidance.index("--guidance")
    del guidance[at : at + 2]
    async with start_up(one_project_routes(), [*EVERY_FLAG, *guidance]) as (
        app,
        pilot,
    ):
        await prompted(pilot, TextAnswer)
        await settle(pilot)
        paths.append(save(app, "startup-guidance-dark-210x62", "Start-up · Guidance"))
    # The summary and "Start tuning?".
    async with start_up(one_project_routes(), [*EVERY_FLAG, *ANSWERED]) as (
        app,
        pilot,
    ):
        await menu(pilot, ADVANCED_PROMPT)
        await pilot.press("enter")
        await menu(pilot, START_QUESTION)
        await settle(pilot)
        paths.append(save(app, "startup-summary-dark-210x62", "Start-up · Summary"))
    return paths


async def main() -> None:
    shots = [
        ("run-starting-dark-210x62", "Run · Starting", sample_run.starting(), {}),
        ("run-planning-dark-210x62", "Run · Planning", sample_run.planning(), {}),
        ("run-running-dark-210x62", "Run · Running", sample_run.running(), {}),
        ("run-gate-dark-210x62", "Run · Promotion gate", sample_run.gate_open(), {}),
        ("run-done-dark-210x62", "Run · Done", sample_run.done(), {"ended": True}),
        (
            "run-running-last-wave-dark-210x62",
            "Run · Running, last wave",
            sample_run.running(wrap_up=True),
            {},
        ),
        (
            "run-stop-dialog-dark-210x62",
            "Run · Stop dialog",
            sample_run.running(),
            {"keys": ("q",)},
        ),
        (
            "run-running-light-210x62",
            "Run · Running",
            sample_run.running(),
            {"theme": LIGHT},
        ),
        (
            "run-running-dark-120x40",
            "Run · Running, stacked",
            sample_run.running(),
            {"size": NARROW},
        ),
        (
            "run-running-bottom-dark-120x40",
            "Run · Running, stacked, scrolled to the bottom",
            sample_run.running(),
            {"size": NARROW, "keys": ("end",)},
        ),
    ]
    for name, title, run, options in shots:
        print(await run_shot(name, title, run, **options))
    for path in await startup_shots():
        print(path)


if __name__ == "__main__":
    asyncio.run(main())
