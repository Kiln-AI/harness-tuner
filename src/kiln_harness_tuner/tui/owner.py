"""The questions to the owner, between the run and the app
(`kiln_harness_tuner.owner_questions`).

The run thread asks through `AppOwnerAsker`, which is the run's
`Ctx.ask_owner`. It puts one request on the app's queue and waits for the
app's answer, for a limited time. This is the one place where the run waits
for the app: only the owner can answer. The wait ends by itself when the
time is over, when the run is stopped, and when the app closes.

Nothing here imports Textual: the dialog draws the request
(`screens/owner.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Sequence
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass, field
from queue import SimpleQueue

from kiln_harness_tuner.owner_questions import (
    DISPLAY_CLOSED,
    LEFT_FOR_LATER,
    NO_ANSWER_IN_TIME,
    OwnerReplies,
    OwnerReply,
)
from kiln_harness_tuner.schemas import OwnerQuestion
from kiln_harness_tuner.tui.model.events import Stamped

OWNER_ASK = "owner_ask"
OWNER_ASK_CLOSED = "owner_ask_closed"

# The dialog's words.
OTHER_LABEL = "Other"
OTHER_HELP = "type your own answer"
OTHER_PLACEHOLDER = "Type your answer, then press Enter"
DEFAULT_MARK = "default"
LATER = "Decide later"


def dialog_title(number: int, total: int) -> str:
    return f"A question for you ({number} of {total})"


def dialog_hint(wait_secs: float) -> str:
    """What the keys do, and what happens with no answer."""
    minutes = max(round(wait_secs / 60), 1)
    return (
        "Enter chooses. Esc leaves this question and the next ones for later:"
        " the run then takes the default of each, and a later run asks again."
        f" With no answer in {minutes} min, the run goes on with the defaults."
    )


@dataclass(frozen=True, eq=False)
class OwnerAsk:
    """The open questions of a run, for the app to show. The run thread
    waits on `answer`: one reply for each question, in order, and None for a
    question that the owner left for later."""

    questions: tuple[OwnerQuestion, ...]
    wait_secs: float
    answer: Future[OwnerReplies] = field(default_factory=Future)

    def resolve(self, replies: Sequence[OwnerReply | None]) -> None:
        """The owner's replies. An ask that has ended keeps how it ended."""
        missing = any(reply is None for reply in replies)
        self._set(OwnerReplies(tuple(replies), LEFT_FOR_LATER if missing else ""))

    def abandon(self, reason: str) -> None:
        """Nobody answers this ask any more."""
        self._set(OwnerReplies.none(len(self.questions), reason))

    def _set(self, replies: OwnerReplies) -> None:
        # The run can have stopped waiting: the future is then cancelled.
        with contextlib.suppress(InvalidStateError):
            self.answer.set_result(replies)


class AppOwnerAsker:
    """`Ctx.ask_owner` of a run in the app. The call comes from the run
    thread's loop. After `close()`, the open ask and every later one get no
    answer, so a run that goes on with no app takes the defaults."""

    def __init__(self, queue: SimpleQueue[Stamped]):
        self._queue = queue
        self._lock = threading.Lock()
        self._closed = False
        self._open: OwnerAsk | None = None

    async def __call__(
        self, questions: Sequence[OwnerQuestion], wait_secs: float
    ) -> OwnerReplies:
        ask = OwnerAsk(tuple(questions), wait_secs)
        with self._lock:
            if self._closed:
                return OwnerReplies.none(len(ask.questions), DISPLAY_CLOSED)
            self._open = ask
        self._queue.put(Stamped.now(OWNER_ASK, {"request": ask}))
        try:
            return await asyncio.wait_for(asyncio.wrap_future(ask.answer), wait_secs)
        except TimeoutError:
            return OwnerReplies.none(len(ask.questions), NO_ANSWER_IN_TIME)
        finally:
            # Also when the run is stopped while it waits: the app takes
            # the dialog away.
            with self._lock:
                self._open = None
            self._queue.put(Stamped.now(OWNER_ASK_CLOSED, {"request": ask}))

    def close(self) -> None:
        """The app has closed. Called from the main thread."""
        with self._lock:
            self._closed = True
            ask = self._open
        if ask is not None:
            ask.abandon(DISPLAY_CLOSED)
