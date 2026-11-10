"""The start-up flow in the app (architecture section 4).

`prepare_start` runs on the start-up thread, as it runs on the main thread
of a plain start. `AppTerminal` is its terminal there: each call becomes a
request on the app's queue, and a prompt waits on a future that the app
answers. `CancellableRunner` is its Kiln runner, which the app can stop:
on a thread no SIGINT reaches a Kiln read, so a Ctrl+C during a busy check
cancels the read instead.

Nothing here imports Textual: the start-up screen draws the requests.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Awaitable, Callable, Iterator, Sequence
from concurrent.futures import Future, InvalidStateError
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from queue import SimpleQueue
from typing import Any, Generic, Literal, TypeVar

from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.kiln import KilnClient
from kiln_harness_tuner.onboarding import demo
from kiln_harness_tuner.onboarding.errors import OnboardingStopped
from kiln_harness_tuner.onboarding.summary import RERUN_HEADING, SUMMARY_HEADING
from kiln_harness_tuner.onboarding.terminal import ENTER, Action, Option, StepStatus
from kiln_harness_tuner.tui.model.events import Stamped
from kiln_harness_tuner.tui.opener import open_link

T = TypeVar("T")

UI_REQUEST = "ui_request"
NOT_GUIDED = "not a guided start"


# ---- the requests ----


class Request:
    """One `Terminal` call, for the start-up screen to show."""


@dataclass(frozen=True)
class Welcome(Request):
    title: str
    version: str
    description: str


@dataclass(frozen=True)
class Step(Request):
    status: StepStatus
    label: str
    text: str


@dataclass(frozen=True)
class Block(Request):
    title: str
    body: tuple[str, ...]


@dataclass(frozen=True)
class Say(Request):
    text: str


@dataclass(frozen=True)
class Warn(Request):
    text: str


@dataclass(frozen=True)
class Busy(Request):
    text: str


@dataclass(frozen=True)
class BusyEnd(Request):
    pass


@dataclass(frozen=True)
class Opened(Request):
    """A page the start opened, or could not open here."""

    url: str
    opened: bool


@dataclass(frozen=True, eq=False)
class Prompt(Request, Generic[T]):
    """A question. The start-up thread waits on `answer`."""

    answer: Future[T] = field(default_factory=Future, kw_only=True)

    def resolve(self, value: T) -> None:
        """The person's answer. A prompt the app abandoned stays failed."""
        with contextlib.suppress(InvalidStateError):
            self.answer.set_result(value)

    def fail(self, error: BaseException) -> None:
        with contextlib.suppress(InvalidStateError):
            self.answer.set_exception(error)


@dataclass(frozen=True, eq=False)
class Pick(Prompt[Any]):
    question: str
    options: tuple[Option[Any], ...]
    hint: str | None
    default: Any


@dataclass(frozen=True, eq=False)
class Act(Prompt[Action]):
    question: str
    actions: tuple[Action, ...]


@dataclass(frozen=True, eq=False)
class Text(Prompt[str]):
    question: str
    default: str
    hint: str | None
    validate: Callable[[str], str | None] | None


SaidKind = Literal["summary", "rerun"]
_SAID_KINDS: dict[str, SaidKind] = {SUMMARY_HEADING: "summary", RERUN_HEADING: "rerun"}


def said_kind(text: str) -> SaidKind | None:
    """Which of the start's two lasting texts a `say` text is, by its first
    line: the summary or the rerun command. `say` carries nothing else in a
    start (`prepare_start`), but any other text is None."""
    return _SAID_KINDS.get(text.strip("\n").split("\n", 1)[0])


def opened_text(url: str, opened: bool) -> str:
    """What `open_url` says: the guided start's two texts from before the
    app, kept as they were."""
    return f"Opening {url}" if opened else f"Open this link in your browser: {url}"


# ---- the terminal ----


class AppTerminal:
    """The `Terminal` of a start in the app. The calls come from the
    start-up thread. Lines are put on the queue and return at once; a prompt
    waits for the app's answer. With `interactive=False` it never asks, as
    `PlainTerminal` does not."""

    def __init__(
        self,
        queue: SimpleQueue[Stamped],
        *,
        interactive: bool,
        opener: Callable[[str], bool] = open_link,
        on_say: Callable[[str], None] | None = None,
    ):
        self.interactive = interactive
        self._queue = queue
        self._opener = opener
        self._on_say = on_say
        self._lock = threading.Lock()
        self._open: Prompt[Any] | None = None
        self._stop: OnboardingStopped | None = None
        # The text of the busy check that runs now, or None.
        self.busy_text: str | None = None

    @property
    def copying_demo(self) -> bool:
        """The start copies the demo project: a file copy, which a stop
        can't cut short. The copy is short, and it is removed on the way
        out, so the app waits for it."""
        return self.busy_text == demo.COPYING

    def _put(self, request: Request) -> None:
        self._queue.put(Stamped.now(UI_REQUEST, {"request": request}))

    def _guided(self) -> None:
        if not self.interactive:
            raise RuntimeError(NOT_GUIDED)

    def _ask(self, prompt: Prompt[T]) -> T:
        with self._lock:
            if self._stop is not None:
                raise self._stop
            self._open = prompt
        self._put(prompt)
        try:
            return prompt.answer.result()
        finally:
            with self._lock:
                self._open = None

    def abandon(self, stop: OnboardingStopped) -> None:
        """The person stopped the start (Ctrl+C, Ctrl+D or a signal), or the
        app closed. The open prompt and every later one raise the first
        stop, so the start unwinds as it does from a prompt today."""
        with self._lock:
            if self._stop is None:
                self._stop = stop
            first, prompt = self._stop, self._open
        if prompt is not None:
            prompt.fail(first)

    def welcome(self, title: str, version: str, description: str) -> None:
        self._put(Welcome(title, version, description))

    def step(self, status: StepStatus, label: str, text: str) -> None:
        self._put(Step(status, label, text))

    def block(self, title: str, body: Sequence[str]) -> None:
        self._guided()
        self._put(Block(title, tuple(body)))

    def say(self, text: str) -> None:
        """A text, and `on_say` keeps it: the summary and the rerun command
        go to the scrollback also when the start stops after them."""
        if self._on_say is not None:
            self._on_say(text)
        self._put(Say(text))

    def warn(self, text: str) -> None:
        self._put(Warn(text))

    @contextlib.contextmanager
    def busy(self, text: str) -> Iterator[None]:
        self.busy_text = text
        self._put(Busy(text))
        try:
            yield
        finally:
            self.busy_text = None
            self._put(BusyEnd())

    def pick(
        self,
        question: str,
        options: Sequence[Option[T]],
        *,
        hint: str | None = None,
        default: T | None = None,
    ) -> T:
        self._guided()
        value: T = self._ask(Pick(question, tuple(options), hint, default))
        return value

    def act(self, question: str, actions: Sequence[Action]) -> Action:
        self._guided()
        assert all(action.key != ENTER for action in actions[1:]), (
            "Enter picks the first action"
        )
        return self._ask(Act(question, tuple(actions)))

    def text(
        self,
        question: str,
        *,
        default: str = "",
        hint: str | None = None,
        validate: Callable[[str], str | None] | None = None,
    ) -> str:
        self._guided()
        return self._ask(Text(question, default, hint, validate))

    def open_url(self, url: str) -> None:
        self._guided()
        self._put(Opened(url, self._opener(url)))


# ---- the Kiln runner ----


class CancellableRunner:
    """A `KilnRunner` the app can stop. Each call runs `fn` in its own
    `asyncio.run` with a fresh client from `connect`, as `kiln_runner` does.

    After `cancel()`, the call in flight and every later call raise
    `KeyboardInterrupt`, which `prepare_start` turns into
    `OnboardingStopped(130)`, as it does for a Ctrl+C during a Kiln read on
    the main thread. The flow's own cleanup (`except BaseException`) runs on
    the way out."""

    def __init__(self, connect: Callable[[], AbstractAsyncContextManager[KilnApi]]):
        self._connect = connect
        self._lock = threading.Lock()
        self._cancelled = False
        self._call: tuple[asyncio.AbstractEventLoop, asyncio.Task[Any]] | None = None

    def __call__(self, fn: Callable[[KilnApi], Awaitable[T]], /) -> T:
        async def with_client() -> T:
            task = asyncio.current_task()
            assert task is not None
            with self._lock:
                if self._cancelled:
                    raise asyncio.CancelledError
                self._call = (asyncio.get_running_loop(), task)
            try:
                async with self._connect() as kiln:
                    return await fn(kiln)
            finally:
                with self._lock:
                    self._call = None

        try:
            return asyncio.run(with_client())
        except asyncio.CancelledError:
            if self._cancelled:
                raise KeyboardInterrupt from None
            raise

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            call = self._call
        if call is not None:
            loop, task = call
            with contextlib.suppress(RuntimeError):  # the call's loop has closed
                loop.call_soon_threadsafe(task.cancel)


def cancellable_runner(host: str) -> CancellableRunner:
    """`kiln_runner(host)`, cancellable."""
    return CancellableRunner(lambda: KilnClient(host))
