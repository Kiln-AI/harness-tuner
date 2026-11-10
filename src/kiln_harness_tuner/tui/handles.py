"""The two threads beside the app, and what the app holds to stop them.

Each is a plain `threading.Thread`, not a Textual worker: a thread worker
keeps `App.run()` from returning until it ends (research section 3). The
thread keeps how its target ended, any `BaseException` included, for the
main thread to re-raise after the app has closed: on a thread, `threading`
drops a `SystemExit` with no word. Then it puts its ended event on the
app's queue.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from queue import SimpleQueue
from typing import Any, Generic, TypeVar

from kiln_harness_tuner.onboarding import OnboardingStopped
from kiln_harness_tuner.tui.model.events import Stamped

T = TypeVar("T")

START_ENDED = "start_ended"
RUN_ENDED = "run_ended"


@dataclass(frozen=True)
class Outcome(Generic[T]):
    """What a thread's target returned, or what it raised."""

    value: T | None = None
    error: BaseException | None = None

    def unwrap(self) -> T:
        if self.error is not None:
            raise self.error
        return self.value  # type: ignore[return-value]


class Worker(Generic[T]):
    def __init__(
        self,
        target: Callable[[], T],
        queue: SimpleQueue[Stamped],
        *,
        name: str,
        daemon: bool,
        ended: str,
    ):
        self._target = target
        self._queue = queue
        self._ended = ended
        self._thread = threading.Thread(target=self._main, name=name, daemon=daemon)
        self.outcome: Outcome[T] | None = None

    def _main(self) -> None:
        try:
            outcome: Outcome[T] = Outcome(value=self._target())
        except BaseException as error:  # the main thread re-raises it
            outcome = Outcome(error=error)
        self.outcome = outcome
        self._queue.put(Stamped.now(self._ended))

    def begin(self) -> None:
        self._thread.start()

    @property
    def began(self) -> bool:
        return self._thread.ident is not None

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def join(self, timeout: float | None = None) -> bool:
        """True when the thread has ended. Waits in short steps, so the main
        thread's signal handlers run while it waits."""
        if not self.began:
            return True
        step = 0.2 if timeout is None else min(timeout, 0.2)
        waited = 0.0
        while self._thread.is_alive():
            self._thread.join(step)
            waited += step
            if timeout is not None and waited >= timeout:
                break
        return not self._thread.is_alive()


class StartHandle(Worker[argparse.Namespace]):
    """The start-up thread: `prepare_start` behind the app. A daemon: if
    the app stops while the start waits on Kiln, the process does not wait
    for it."""

    def __init__(
        self, target: Callable[[], argparse.Namespace], queue: SimpleQueue[Stamped]
    ):
        super().__init__(target, queue, name="startup", daemon=True, ended=START_ENDED)
        self.stopped: OnboardingStopped | None = None

    def stop(self, code: int) -> None:
        """Ctrl+C (130), Ctrl+D (1) or a signal (128 + its number) during
        the start. The first stop wins over how the thread itself ends."""
        if self.stopped is None:
            self.stopped = OnboardingStopped(code)

    def result(self) -> argparse.Namespace:
        if self.stopped is not None:
            raise self.stopped
        if self.outcome is None:
            raise RuntimeError("the start has not ended")
        return self.outcome.unwrap()


class RunHandle(Worker[str]):
    """The run thread: `asyncio.run(_run_with_signals(...))`, which returns
    the report path. Not a daemon: the run's `finally` blocks (the agents
    killed, the lock released) must run before the process exits.

    `stopped_by` is `cli._stopped_by`, read by `main`'s `CancelledError` arm
    for the exit code and the interrupted line."""

    def __init__(
        self,
        target: Callable[[], str],
        queue: SimpleQueue[Stamped],
        stopped_by: list[int],
    ):
        super().__init__(target, queue, name="run", daemon=False, ended=RUN_ENDED)
        self.stopped_by = stopped_by
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[Any] | None = None
        self._stop: int | None = None

    def started(self, loop: asyncio.AbstractEventLoop, task: asyncio.Task[Any]) -> None:
        """`_run_with_signals`'s `on_started`, on the run thread, after it has
        cleared `stopped_by`. A stop asked for before now is done now."""
        with self._lock:
            self._loop, self._task = loop, task
            if self._stop is not None:
                self.stopped_by.append(self._stop)
                task.cancel()

    def stop(self, signum: int) -> bool:
        """Stop the run as a stop signal does: record the signal and cancel
        the run's main task, so its teardown runs. Once only: False when the
        run is already stopping."""
        with self._lock:
            if self._stop is not None:
                return False
            self._stop = signum
            if self._loop is not None and self._task is not None:
                self.stopped_by.append(signum)
                with contextlib.suppress(RuntimeError):  # the loop has closed
                    self._loop.call_soon_threadsafe(self._task.cancel)
        return True

    @property
    def stopping(self) -> bool:
        return self._stop is not None

    def result(self) -> str:
        if self.outcome is None:
            raise RuntimeError("the run has not ended")
        return self.outcome.unwrap()
