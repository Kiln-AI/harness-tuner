"""The app shell (architecture sections 6 and 7.3): the phases, the keys, the
stop dialog, the signals and the drain. Pilot tests at 210×62, on fake
threads."""

import signal
from pathlib import Path

import pytest
from app_fakes import SIZE, FakeRun, FakeStart, make_app, make_session, until
from textual.widgets import Button, Static

from kiln_harness_tuner.onboarding import OnboardingError
from kiln_harness_tuner.onboarding.error_screen import RunProgress
from kiln_harness_tuner.tui.model import words
from kiln_harness_tuner.tui.model.events import Stamped
from kiln_harness_tuner.tui.screens.run import RunScreen
from kiln_harness_tuner.tui.screens.startup import StartupScreen
from kiln_harness_tuner.tui.screens.stop import StopDialog
from kiln_harness_tuner.tui.theme import DARK, LIGHT

RUN_START = (
    "run_start",
    {"timeout_mins": 60, "deadline": 1e9, "budget_usd": 20.0, "objective": "quality"},
)


def footer(app) -> dict[str, str]:
    """The keys the footer shows now, with their words."""
    return {
        key: active.binding.description
        for key, active in app.screen.active_bindings.items()
        if active.binding.show and active.enabled
    }


def closed(app) -> bool:
    return app.return_code is not None


def text(app, part: str) -> str:
    return str(app.screen.query_one(f"#{part}", Static).render())


# ---- the start-up ----


@pytest.mark.parametrize("key,code", [("ctrl+c", 130), ("ctrl+d", 1), ("ctrl+q", 130)])
async def test_a_key_during_the_start_stops_it_at_once(key, code):
    start = FakeStart(wait=True)
    session = make_session(start=start)
    app = make_app(session)
    async with app.run_test(size=SIZE) as pilot:
        assert isinstance(app.screen, StartupScreen)
        assert footer(app) == {"ctrl+c": "Stop", "ctrl+d": "Exit"}
        await pilot.press(key)
        await until(pilot, lambda: closed(app))
    assert session.start.stopped.exit_code == code
    assert app.closed_on_purpose
    assert not session.run.began


async def test_a_signal_during_the_start_stops_it_with_its_code():
    start = FakeStart(wait=True)
    session = make_session(start=start)
    app = make_app(session)
    async with app.run_test(size=SIZE) as pilot:
        app.on_signal(signal.SIGHUP)
        await until(pilot, lambda: closed(app))
    assert session.start.stopped.exit_code == 129


async def test_the_hand_off_shows_the_run_and_starts_it():
    session = make_session(run=FakeRun([RUN_START]))
    app = make_app(session)
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.phase == "run")
        assert isinstance(app.screen, RunScreen)
        assert session.run.began
        await until(pilot, lambda: "Run started" in text(app, "run-log"))
        assert "Moderate posts" in text(app, "run-title")
        assert footer(app) == {"q": "Stop"}
        session.run.stop(signal.SIGTERM)
        await until(pilot, lambda: closed(app))


async def test_with_no_progress_the_app_closes_when_the_start_ends():
    session = make_session(mode="app_startup")
    app = make_app(session)
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: closed(app))
    assert app.closed_on_purpose
    assert not session.run.began
    assert app.model is None


async def test_a_start_error_closes_the_app():
    session = make_session(start=FakeStart(error=OnboardingError("No Kiln")))
    app = make_app(session)
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: closed(app))
    assert not session.run.began


# ---- the run ----


async def running(pilot, session) -> None:
    await until(pilot, lambda: pilot.app.phase == "run")
    await until(pilot, lambda: pilot.app.model.version > 0)


async def test_q_opens_the_stop_dialog_once_and_esc_keeps_running():
    session = make_session(run=FakeRun([RUN_START]))
    app = make_app(session)
    async with app.run_test(size=SIZE) as pilot:
        await running(pilot, session)
        await pilot.press("q")
        await until(pilot, lambda: isinstance(app.screen, StopDialog))
        dialog = app.screen
        message = str(dialog.query_one("#dialog-message", Static).render())
        assert message == "The run's work so far is lost, and no report is written."
        assert app.focused.id == "keep"
        await pilot.press("ctrl+c")
        await pilot.press("q")
        assert app.screen is dialog
        assert len(app.screen_stack) == 3  # the default screen, the run, the dialog
        await pilot.press("escape")
        await until(pilot, lambda: isinstance(app.screen, RunScreen))
        assert not session.run.stopping
        # Enter on the focused "Keep running" keeps running too.
        await pilot.press("ctrl+c")
        await until(pilot, lambda: isinstance(app.screen, StopDialog))
        await pilot.press("enter")
        await until(pilot, lambda: isinstance(app.screen, RunScreen))
        assert not session.run.stopping
        session.run.stop(signal.SIGTERM)
        await until(pilot, lambda: closed(app))


async def test_stop_the_run_stops_it_as_ctrl_c_does_a_plain_run():
    stopped_by: list[int] = []
    run = FakeRun([RUN_START])
    session = make_session(run=run, stopped_by=stopped_by)
    app = make_app(session)
    async with app.run_test(size=SIZE) as pilot:
        await running(pilot, session)
        await pilot.press("ctrl+c")
        await until(pilot, lambda: isinstance(app.screen, StopDialog))
        assert app.screen.query_one("#stop", Button).label.plain == "Stop the run"
        await pilot.press("tab", "enter")
        await until(pilot, lambda: session.run.stopping)
        await until(pilot, lambda: closed(app))
    assert stopped_by == [signal.SIGINT]
    assert run.cancelled
    assert app.closed_on_purpose


async def test_the_run_screen_says_stopping_until_the_run_has_stopped():
    run = FakeRun([RUN_START])
    session = make_session(run=run)
    # A slow drain, so the stopped run's end is not drained at once.
    app = make_app(session, drain_interval=60)
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: session.start.outcome is not None)
        app.drain()
        await pilot.pause()
        app.drain()
        await until(pilot, lambda: session.run.began and run.handle._task is not None)
        app.on_signal(signal.SIGTERM)
        assert text(app, "run-mode") == words.STOPPING
        await until(pilot, lambda: session.run.outcome is not None)
        app.drain()
        await until(pilot, lambda: closed(app))


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP, signal.SIGINT])
async def test_a_signal_during_the_run_stops_it(signum):
    stopped_by: list[int] = []
    run = FakeRun([RUN_START])
    session = make_session(run=run, stopped_by=stopped_by)
    app = make_app(session)
    async with app.run_test(size=SIZE) as pilot:
        await running(pilot, session)
        await pilot.press("q")
        await until(pilot, lambda: isinstance(app.screen, StopDialog))
        # The signal does not wait for the dialog: it closes it.
        app.on_signal(signum)
        assert isinstance(app.screen, RunScreen)
        await until(pilot, lambda: closed(app))
    assert stopped_by == [signum]
    assert run.cancelled


async def test_a_second_stop_closes_the_app_at_once():
    run = FakeRun([RUN_START])
    session = make_session(run=run)
    app = make_app(session, drain_interval=60)
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: session.start.outcome is not None)
        app.drain()
        await until(pilot, lambda: run.handle._task is not None)
        app.on_signal(signal.SIGTERM)
        assert not closed(app)
        app.on_signal(signal.SIGTERM)
        await until(pilot, lambda: closed(app))
    assert app.closed_on_purpose
    assert session.run.join(5)


async def test_q_while_stopping_closes_the_app_at_once():
    run = FakeRun([RUN_START])
    session = make_session(run=run)
    app = make_app(session, drain_interval=60)
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: session.start.outcome is not None)
        app.drain()
        await until(pilot, lambda: run.handle._task is not None)
        app.on_signal(signal.SIGTERM)
        await pilot.press("q")
        await until(pilot, lambda: closed(app))
    assert session.run.join(5)


@pytest.mark.parametrize("key", ["q", "enter", "ctrl+c"])
async def test_done_stays_open_until_the_person_closes_it(key):
    session = make_session(run=FakeRun([RUN_START], wait=False))
    app = make_app(session)
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.phase == "done")
        assert footer(app) == {"q": "Close"}
        await pilot.pause(0.1)
        assert not closed(app)
        await pilot.press(key)
        await until(pilot, lambda: closed(app))
    assert app.closed_on_purpose
    assert app.model.run_ended


async def test_the_end_of_the_run_closes_an_open_stop_dialog():
    run = FakeRun([RUN_START])
    session = make_session(run=run)
    app = make_app(session)
    async with app.run_test(size=SIZE) as pilot:
        await running(pilot, session)
        await pilot.press("q")
        await until(pilot, lambda: isinstance(app.screen, StopDialog))
        run.release.set()
        await until(pilot, lambda: app.phase == "done")
        assert isinstance(app.screen, RunScreen)
        assert not session.run.stopping
        assert footer(app) == {"q": "Close"}
        await pilot.press("q")
        await until(pilot, lambda: closed(app))


async def test_stop_the_run_after_the_end_does_not_stop_a_finished_run():
    stopped_by: list[int] = []
    session = make_session(run=FakeRun([RUN_START], wait=False), stopped_by=stopped_by)
    app = make_app(session)
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.phase == "done")
        # An answer that arrives after the end, as from a dialog the end
        # closed.
        app._stop_answered(True)
        assert not session.run.stopping
        assert stopped_by == []
        await pilot.press("q")
        await until(pilot, lambda: closed(app))


async def test_a_signal_on_done_closes_the_app():
    session = make_session(run=FakeRun(wait=False))
    app = make_app(session)
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.phase == "done")
        app.on_signal(signal.SIGTERM)
        await until(pilot, lambda: closed(app))


async def test_a_run_error_closes_the_app_at_once():
    session = make_session(run=FakeRun(wait=False, error=RuntimeError("Kiln is gone")))
    app = make_app(session)
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: closed(app))
    assert app.phase == "run"
    assert app.closed_on_purpose


# ---- the drain ----


async def test_an_event_the_model_cannot_fold_is_skipped_and_noted():
    session = make_session(run=FakeRun([RUN_START]))
    app = make_app(session)
    async with app.run_test(size=SIZE) as pilot:
        await running(pilot, session)
        model = app.model
        real_apply = model.apply

        def apply(stamped):
            if stamped.kind == "bad":
                raise KeyError("items")
            real_apply(stamped)

        model.apply = apply
        session.queue.put(Stamped.now("bad", {}))
        session.queue.put(Stamped.now("wave_start", {"wave": 1, "items": []}))
        app.drain()
        assert model.ui_errors == 1
        assert words.UI_ERROR in text(app, "run-log")
        assert "Wave 1" in text(app, "run-log")
        assert not closed(app)
        session.run.stop(signal.SIGTERM)
        await until(pilot, lambda: closed(app))


async def test_an_unchanged_model_is_not_shown_again():
    session = make_session(run=FakeRun([RUN_START]))
    app = make_app(session, drain_interval=60)
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: session.start.outcome is not None)
        app.drain()
        await until(pilot, lambda: session.queue.qsize() > 0)
        await pilot.pause()
        app.drain()
        screen = app.run_screen
        updates: list[str] = []
        for part in ("run-title", "run-mode", "run-log"):
            widget = screen.query_one(f"#{part}", Static)
            widget.update = lambda text, part=part: updates.append(part)
        app.drain()
        screen.refresh_from(app.model, stopping=False, now=0.0)
        assert updates == []
        session.queue.put(Stamped.now("wave_start", {"wave": 1, "items": []}))
        app.drain()
        assert updates == ["run-mode", "run-log"]
        session.run.stop(signal.SIGTERM)
        app.drain()
        await until(pilot, lambda: session.run.outcome is not None)
        app.drain()
        await until(pilot, lambda: closed(app))


async def test_the_drain_reads_the_run_id_and_the_agent_cli_check():
    progress = RunProgress()
    session = make_session(run=FakeRun([RUN_START]), progress=progress)
    app = make_app(session)
    async with app.run_test(size=SIZE) as pilot:
        await running(pilot, session)
        assert app.model.info.run_id is None
        progress.cli_checked = True
        progress.run_dir = Path("/state/runs/261110-0912")
        await until(pilot, lambda: app.model.info.run_id == "261110-0912")
        assert app.model.cli_checked is not None
        assert "run 261110-0912" in text(app, "run-title")
        session.run.stop(signal.SIGTERM)
        await until(pilot, lambda: closed(app))


# ---- the rest of the shell ----


async def test_the_app_installs_its_signal_handlers_and_removes_them():
    calls: list[str] = []

    def hooks(_loop, handler):
        calls.append(f"installed {handler.__name__}")
        return lambda: calls.append("removed")

    session = make_session(mode="app_startup")
    app = make_app(session, signals=hooks)
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: closed(app))
    assert calls == ["installed on_signal", "removed"]


async def test_prints_go_to_the_print_sink():
    printed: list[str] = []
    start = FakeStart(wait=True)
    app = make_app(make_session(start=start), print_sink=printed.append)
    async with app.run_test(size=SIZE) as pilot:
        print("a stray warning")
        await until(pilot, lambda: "a stray warning" in "".join(printed))
        await pilot.press("ctrl+c")


@pytest.mark.parametrize("name", [DARK, LIGHT])
async def test_the_app_uses_the_theme_it_is_given(name):
    start = FakeStart(wait=True)
    app = make_app(make_session(start=start), theme=name)
    async with app.run_test(size=SIZE) as pilot:
        assert app.theme == name
        assert {DARK, LIGHT} <= set(app.available_themes)
        await pilot.press("ctrl+c")


PLANNING = (
    "phase_start",
    {"name": "planning", "wave": 1, "label": "Planning wave 1… (a session)"},
)


async def stop_message(pilot) -> str:
    """Open the stop dialog, read its message, and keep running."""
    app = pilot.app
    await pilot.press("q")
    await until(pilot, lambda: isinstance(app.screen, StopDialog))
    message = str(app.screen.query_one("#dialog-message", Static).render())
    await pilot.press("escape")
    await until(pilot, lambda: isinstance(app.screen, RunScreen))
    return message


async def test_the_stop_dialog_names_the_planner_only_while_it_plans():
    session = make_session(run=FakeRun([RUN_START, PLANNING]))
    app = make_app(session)
    async with app.run_test(size=SIZE) as pilot:
        await until(
            pilot, lambda: app.model is not None and app.model.mode == "planning"
        )
        assert (await stop_message(pilot)).startswith(
            "The planner is at work on wave 1."
        )
        session.queue.put(
            Stamped.now(
                "phase_end", {"name": "planning", "wave": 1, "elapsed_secs": 60.0}
            )
        )
        session.queue.put(Stamped.now("wave_start", {"wave": 1, "items": []}))
        await until(pilot, lambda: app.model.mode == "running")
        assert await stop_message(pilot) == (
            "The run's work so far is lost, and no report is written."
        )
        session.run.stop(signal.SIGTERM)
        await until(pilot, lambda: closed(app))
