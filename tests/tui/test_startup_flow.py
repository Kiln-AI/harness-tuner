"""The real `prepare_start` in the app (architecture 15, the bridge): on the
start-up thread, behind `AppTerminal` and `CancellableRunner`, on a fake
Kiln, answered with keys. `tests/test_onboarding_*.py` pin the flow's own
words and order with a scripted terminal; these check that the app carries
them."""

import asyncio
import threading
from contextlib import asynccontextmanager

from app_fakes import SIZE, Asking, prompted, until
from onboarding_fakes import (
    ADVANCED_PROMPT,
    ANSWERED,
    FOLDER,
    PROG,
    PROJECT_ID,
    TASK_ID,
    RouteKiln,
    claude_models_only,
    import_project_route,
    kiln_routes,
    one_project_routes,
    responses,
    run_config,
    task_base,
)
from textual.widgets import Static

from kiln_harness_tuner.cli import build_parser, load_model_policy
from kiln_harness_tuner.onboarding import demo
from kiln_harness_tuner.onboarding.demo import IMPORT_PATH
from kiln_harness_tuner.onboarding.errors import OnboardingStopped
from kiln_harness_tuner.onboarding.flow import START_QUESTION, prepare_start
from kiln_harness_tuner.tui.bridge import CancellableRunner
from kiln_harness_tuner.tui.screens.prompts import ActionMenu
from kiln_harness_tuner.tui.widgets import BusyLine, TitleBar

EVERY_FLAG = ["--project-id", PROJECT_ID, "--task-id", TASK_ID]
EVERY_FLAG += ["--project-path", FOLDER]
PASSED = [
    "✓ Kiln      1.0.3 at http://localhost:8757",
    "✓ Project   Forum Moderation (424242975950)",
    "✓ Task      Moderate posts (424242783061)",
    "✓ Ready     1 run config · 1 eval the tuner can measure",
    "✓ API keys  Fireworks AI",
]
NOT_READY = "Moderate posts isn't ready to tune yet"


def connect_to(kiln):
    @asynccontextmanager
    async def connect():
        yield kiln

    return connect


class Start:
    """`prepare_start` with `argv` on `kiln`, in the app."""

    def __init__(self, kiln, argv, *, interactive=True, home=None):
        self.reports = []
        # Set to keep the app open after the start, for a look at it.
        self.hold = threading.Event()
        self.runner = CancellableRunner(connect_to(kiln))
        parser = build_parser()
        args = parser.parse_args(argv)

        def start(terminal):
            started = prepare_start(
                args,
                argv,
                prog=PROG,
                option_dests=parser.option_dests,
                policy_loader=load_model_policy,
                terminal=terminal,
                runner=self.runner,
                which=lambda name: f"/usr/local/bin/{name}",
                list_models=claude_models_only,
                home=home,
                report=self.reports.append,
            )
            self.hold.wait(10)
            return started

        self.asking = Asking(start, interactive=interactive, runner=self.runner)
        self.app = self.asking.app
        self.hold.set()

    @property
    def outcome(self):
        return self.asking.start.outcome


async def answer(pilot, question: str, key: str) -> None:
    """Wait for the menu that asks `question`, then press `key`."""

    def asking() -> bool:
        menus = pilot.app.screen.query(ActionMenu)
        return bool(menus) and menus.last().request.question == question

    await until(pilot, asking)
    await prompted(pilot, ActionMenu)
    await pilot.press(key)
    await until(pilot, lambda: not asking())


def lines(app) -> list[str]:
    column = app.screen.query_one("#column")
    return [str(part.render()) for part in column.children if isinstance(part, Static)]


async def test_a_guided_start_runs_in_the_app_to_the_start_of_the_run():
    start = Start(RouteKiln(one_project_routes()), [*EVERY_FLAG, *ANSWERED])
    async with start.app.run_test(size=SIZE) as pilot:
        await answer(pilot, ADVANCED_PROMPT, "enter")
        await until(pilot, lambda: pilot.app.screen.query(".-summary"))
        await until(pilot, lambda: not pilot.app.screen.query(BusyLine))
        shown = lines(pilot.app)
        summary = pilot.app.screen.query_one(".-summary .body")
        context = pilot.app.screen.query_one(TitleBar).query_one(".title-context")
        await answer(pilot, START_QUESTION, "s")
        await until(pilot, lambda: start.outcome is not None)
    assert start.outcome.error is None
    assert start.outcome.value.task_id == TASK_ID
    assert shown[: len(PASSED)] == PASSED
    assert "Ready to start" not in shown
    (report,) = start.reports
    body = str(summary.render())
    assert body.startswith("Task         Moderate posts (424242783061)")
    assert "To run this again with the same answers and no questions:" in body
    assert report.task_name == "Moderate posts"
    assert str(context.render()) == "Moderate posts"


async def test_exit_at_start_the_run_stops_with_1():
    start = Start(RouteKiln(one_project_routes()), [*EVERY_FLAG, *ANSWERED])
    async with start.app.run_test(size=SIZE) as pilot:
        await answer(pilot, ADVANCED_PROMPT, "enter")
        await answer(pilot, START_QUESTION, "x")
        await until(pilot, lambda: start.outcome is not None)
    error = start.outcome.error
    assert isinstance(error, OnboardingStopped) and error.exit_code == 1
    assert start.reports == []


async def test_a_failed_check_shows_its_card_and_r_checks_again():
    routes = one_project_routes()
    base = task_base(PROJECT_ID, TASK_ID)
    routes[("GET", f"{base}/run_configs")] = responses([], [run_config()])
    start = Start(RouteKiln(routes), [*EVERY_FLAG, *ANSWERED])
    async with start.app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: pilot.app.screen.query(".-problem"))
        card = pilot.app.screen.query_one(".-problem")
        assert NOT_READY in card.border_title
        await answer(pilot, "What next?", "r")
        await answer(pilot, ADVANCED_PROMPT, "enter")
        await answer(pilot, START_QUESTION, "s")
        await until(pilot, lambda: start.outcome is not None)
    assert start.outcome.error is None


async def test_with_no_interaction_the_start_shows_its_steps_and_asks_nothing():
    start = Start(
        RouteKiln(one_project_routes()), [*EVERY_FLAG, *ANSWERED], interactive=False
    )
    start.hold.clear()
    async with start.app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: start.reports)
        await until(pilot, lambda: pilot.app.screen.query(".-summary"))
        # A spinner whose check was quick can take a turn to go.
        await until(pilot, lambda: not pilot.app.screen.query(BusyLine))
        assert lines(pilot.app) == PASSED
        assert pilot.app.screen.query_one(".-summary").border_title
        assert not pilot.app.screen.query(ActionMenu)
        start.hold.set()
        await until(pilot, lambda: start.outcome is not None)
    assert start.outcome.error is None
    (report,) = start.reports
    assert report.rerun is None


class StuckPing(RouteKiln):
    """A Kiln that never answers its ping."""

    def __init__(self):
        super().__init__(one_project_routes())
        self.cancelled = False

    async def call(self, method, url_path, *args, **kwargs):
        if url_path == "/ping":
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return await super().call(method, url_path, *args, **kwargs)


async def test_ctrl_c_during_a_kiln_read_stops_the_start_with_130():
    kiln = StuckPing()
    start = Start(kiln, [*EVERY_FLAG, *ANSWERED])
    async with start.app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: pilot.app.screen.query(".busy-line"))
        await pilot.press("ctrl+c")
        await until(pilot, lambda: start.outcome is not None, timeout=5)
    error = start.outcome.error
    assert isinstance(error, OnboardingStopped) and error.exit_code == 130
    assert kiln.cancelled


async def test_ctrl_c_during_the_demo_copy_waits_for_it_then_removes_it(
    tmp_path, monkeypatch
):
    copying = threading.Event()
    release = threading.Event()
    real_copy = demo.write_copy

    def slow_copy(*args, **kwargs):
        copying.set()
        release.wait(10)
        return real_copy(*args, **kwargs)

    monkeypatch.setattr(demo, "write_copy", slow_copy)
    kiln = RouteKiln(kiln_routes())
    kiln.routes[("POST", IMPORT_PATH)] = import_project_route(kiln)
    start = Start(kiln, ["--demo-project", *ANSWERED], home=tmp_path)
    async with start.app.run_test(size=SIZE) as pilot:
        await until(pilot, copying.is_set)
        await pilot.press("ctrl+c")
        await pilot.pause(0.3)
        assert start.outcome is None
        release.set()
        await until(pilot, lambda: start.outcome is not None)
    error = start.outcome.error
    assert isinstance(error, OnboardingStopped) and error.exit_code == 130
    # The whole copy was made, then removed; it was never imported.
    assert list((tmp_path / "Kiln Projects").iterdir()) == []
    assert IMPORT_PATH not in kiln.paths()
