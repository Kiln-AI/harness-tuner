"""Fakes for the app's tests: the threads' targets, a session, a headless
takeover, and a pilot helper that waits for a condition."""

from __future__ import annotations

import argparse
import asyncio
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from queue import SimpleQueue
from typing import Any

from textual.pilot import Pilot

from kiln_harness_tuner.onboarding.error_screen import RunProgress
from kiln_harness_tuner.tui.app import AppMode, Session, TunerApp
from kiln_harness_tuner.tui.handles import RunHandle, StartHandle
from kiln_harness_tuner.tui.model.events import Stamped
from kiln_harness_tuner.tui.model.fold import RunModel
from kiln_harness_tuner.tui.model.state import RunInfo
from kiln_harness_tuner.tui.theme import DARK

SIZE = (210, 62)
INFO = RunInfo(
    project_id="424242975950",
    task_id="424242783061",
    kiln_host="http://localhost:8757",
    task_name="Moderate posts",
)


async def until(pilot: Pilot[Any], condition: Callable[[], bool], timeout=5.0) -> None:
    """Let the app run until `condition` holds."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("the app did not get there in time")
        await pilot.pause(0.02)


class FakeStart:
    """A start target. It waits until `release` is set (at once with
    `wait=False`), then returns its namespace or raises `error`."""

    def __init__(self, *, wait: bool = False, error: BaseException | None = None):
        self.release = threading.Event()
        if not wait:
            self.release.set()
        self.error = error
        self.args = argparse.Namespace()

    def __call__(self) -> argparse.Namespace:
        self.release.wait(10)
        if self.error is not None:
            raise self.error
        return self.args


class FakeRun:
    """A run target: an asyncio run that hands its loop and task to the
    handle, puts `events` on the queue as the loop's display would, then
    waits for `release` (at once with `wait=False`) or its cancel."""

    def __init__(
        self,
        events: Sequence[tuple[str, dict[str, Any]]] = (),
        *,
        wait: bool = True,
        error: BaseException | None = None,
    ):
        self.events = list(events)
        self.release = threading.Event()
        if not wait:
            self.release.set()
        self.error = error
        self.cancelled = False
        self.handle: RunHandle | None = None
        self.queue: SimpleQueue[Stamped] | None = None

    def __call__(self) -> str:
        return asyncio.run(self._main())

    async def _main(self) -> str:
        assert self.handle is not None and self.queue is not None
        task = asyncio.current_task()
        assert task is not None
        self.handle.started(asyncio.get_running_loop(), task)
        for kind, fields in self.events:
            self.queue.put(Stamped.now(kind, fields))
        # Bounded: a test that fails before it stops the run must not leave
        # this non-daemon thread to hold the process open.
        deadline = time.monotonic() + 10
        try:
            while not self.release.is_set() and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.error is not None:
            raise self.error
        return "/reports/report.md"


def make_session(
    *,
    mode: AppMode = "app",
    start: FakeStart | None = None,
    run: FakeRun | None = None,
    stopped_by: list[int] | None = None,
    progress: RunProgress | None = None,
) -> Session:
    queue: SimpleQueue[Stamped] = SimpleQueue()
    run = run if run is not None else FakeRun()
    run_handle = RunHandle(run, queue, stopped_by if stopped_by is not None else [])
    run.handle, run.queue = run_handle, queue
    run_progress = progress if progress is not None else RunProgress()
    return Session(
        mode=mode,
        queue=queue,
        start=StartHandle(start if start is not None else FakeStart(), queue),
        run=run_handle,
        make_model=lambda: RunModel(INFO, started=time.monotonic()),
        progress=lambda: run_progress,
    )


def make_app(session: Session, **kwargs: Any) -> TunerApp:
    kwargs.setdefault("signals", None)
    kwargs.setdefault("drain_interval", 0.02)
    return TunerApp(session, **kwargs)


class HeadlessTakeover:
    """`run_app`'s terminal, for tests: the app runs headless at 210×62,
    driven by `script` (a pilot coroutine), with no signal handlers. The
    prints the app captures are kept in `prints`."""

    signals = None

    def __init__(
        self,
        script: Callable[[Pilot[Any]], Awaitable[None]] | None = None,
        *,
        stray: Path | None = None,
    ):
        self.script = script
        self.prints: list[str] = []
        self.stray = stray
        self.settled: list[Path | None] = []
        self.app: TunerApp | None = None

    def theme(self) -> str:
        return DARK

    def print_sink(self, text: str) -> None:
        self.prints.append(text)

    def run(self, app: TunerApp) -> None:
        self.app = app
        # Bounded: an app that never closes fails the test instead of hanging it.
        fired = threading.Event()

        def stop() -> None:
            fired.set()
            app.call_from_thread(app.exit)

        watchdog = threading.Timer(20, stop)
        watchdog.daemon = True
        watchdog.start()
        try:
            app.run(headless=True, size=SIZE, auto_pilot=self.script)
        finally:
            watchdog.cancel()
        assert not fired.is_set(), "the app did not close by itself"

    def settle(self, run_log: Path | None) -> Path | None:
        self.settled.append(run_log)
        return self.stray
