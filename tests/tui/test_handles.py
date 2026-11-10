"""The two threads beside the app, and what stops them (architecture
sections 1 and 6)."""

import argparse
import asyncio
import signal
import threading
from queue import SimpleQueue

import pytest

from kiln_harness_tuner import cli
from kiln_harness_tuner.onboarding import OnboardingStopped
from kiln_harness_tuner.tui.handles import (
    RUN_ENDED,
    START_ENDED,
    Outcome,
    RunHandle,
    StartHandle,
    Worker,
)


def ended_kinds(queue: SimpleQueue) -> list[str]:
    kinds = []
    while not queue.empty():
        kinds.append(queue.get_nowait().kind)
    return kinds


def test_a_thread_keeps_what_its_target_returned_and_says_it_ended():
    queue = SimpleQueue()
    worker = Worker(lambda: 42, queue, name="t", daemon=True, ended="t_ended")
    assert worker.join() is True  # not begun: nothing to wait for
    worker.begin()
    assert worker.join(5)
    assert worker.outcome == Outcome(value=42)
    assert ended_kinds(queue) == ["t_ended"]


@pytest.mark.parametrize(
    "error",
    [
        ValueError("a bug"),
        # On a thread, `threading` drops these with no word.
        SystemExit("--agent-cli: 'x' is not a supported agent CLI"),
        KeyboardInterrupt(),
        asyncio.CancelledError(),
    ],
)
def test_a_thread_keeps_any_exception_for_the_main_thread(error):
    def target():
        raise error

    queue = SimpleQueue()
    worker = Worker(target, queue, name="t", daemon=True, ended="t_ended")
    worker.begin()
    assert worker.join(5)
    with pytest.raises(type(error)):
        worker.outcome.unwrap()
    assert ended_kinds(queue) == ["t_ended"]


def test_a_stop_during_the_start_wins_over_how_the_start_ends():
    release = threading.Event()
    queue = SimpleQueue()
    start = StartHandle(lambda: release.wait(5) and argparse.Namespace(), queue)
    with pytest.raises(RuntimeError):
        start.result()
    start.begin()
    start.stop(130)
    start.stop(1)  # the first stop is kept
    release.set()
    assert start.join(5)
    assert start.outcome.error is None
    with pytest.raises(OnboardingStopped) as stopped:
        start.result()
    assert stopped.value.exit_code == 130
    assert ended_kinds(queue) == [START_ENDED]


@pytest.fixture
def slow_run(monkeypatch):
    """`cli._run` as a run that waits to be cancelled; `running` is set
    once it waits."""
    running = threading.Event()

    async def fake_run(_args, *, display=None):
        running.set()
        await asyncio.sleep(30)
        return "report.md"

    monkeypatch.setattr(cli, "_run", fake_run)
    cli._stopped_by.clear()
    yield running
    cli._stopped_by.clear()


def run_handle(queue: SimpleQueue) -> RunHandle:
    """A run thread as `run_app` makes it: the real `_run_with_signals`,
    which clears `cli._stopped_by` and then calls `on_started`."""

    def target() -> str:
        return asyncio.run(
            cli._run_with_signals(argparse.Namespace(), on_started=handle.started)
        )

    handle = RunHandle(target, queue, cli._stopped_by)
    return handle


def test_a_stop_before_the_run_starts_cancels_it_as_it_starts(slow_run):
    queue = SimpleQueue()
    handle = run_handle(queue)
    assert handle.stop(signal.SIGTERM) is True
    assert handle.stopping
    handle.begin()
    assert handle.join(5)
    assert isinstance(handle.outcome.error, asyncio.CancelledError)
    # Recorded after `_run_with_signals` cleared the list, so `main` reads it.
    assert cli._stopped_by == [signal.SIGTERM]
    assert ended_kinds(queue) == [RUN_ENDED]


def test_a_stop_cancels_the_running_run_once(slow_run):
    queue = SimpleQueue()
    handle = run_handle(queue)
    handle.begin()
    assert slow_run.wait(5)
    assert handle.stop(signal.SIGHUP) is True
    assert handle.stop(signal.SIGTERM) is False
    assert handle.join(5)
    with pytest.raises(asyncio.CancelledError):
        handle.result()
    assert cli._stopped_by == [signal.SIGHUP]


def test_a_stop_after_the_run_ended_does_nothing(monkeypatch):
    async def quick_run(_args, *, display=None):
        return "report.md"

    monkeypatch.setattr(cli, "_run", quick_run)
    handle = run_handle(SimpleQueue())
    handle.begin()
    assert handle.join(5)
    assert handle.stop(signal.SIGTERM) is True  # the loop has closed: no error
    assert handle.result() == "report.md"
    cli._stopped_by.clear()
