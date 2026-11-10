"""The run's display in the app (architecture section 5.2)."""

from __future__ import annotations

import logging
from queue import SimpleQueue
from types import TracebackType

from kiln_harness_tuner.orchestrator import EventLog
from kiln_harness_tuner.status import RunStatusTracker
from kiln_harness_tuner.tui.closing import ClosingBlock
from kiln_harness_tuner.tui.model.events import Stamped
from kiln_harness_tuner.ui import plain_exit_line

logger = logging.getLogger("kiln_harness_tuner.tui")


class TuiSink:
    """Last in the run's fan-out, on the loop thread, after the loop's own
    tracker. It stamps each event and puts it on the app's queue, so the
    loop never waits for the app.

    It never raises into the loop: the fan-out has no `try`, and a raise
    here would skip the spend meter after it.

    It builds the closing block's run part when `PlainUi` would print it:
    the totals keep moving after `exit`."""

    def __init__(
        self,
        tracker: RunStatusTracker,
        queue: SimpleQueue[Stamped],
        closing: ClosingBlock,
    ):
        self.tracker = tracker
        self._queue = queue
        self._closing = closing
        self._plain: EventLog | None = None

    def event(self, kind: str, **fields: object) -> None:
        try:
            if kind == "exit":
                snapshot = self.tracker.snapshot()
                self._closing.run_exit_line = plain_exit_line(fields, snapshot)
            elif kind == "report_written":
                path = fields.get("path")
                self._closing.report_path = str(path) if path else None
            plain = self._plain
            if plain is not None:
                plain.event(kind, **fields)
            else:
                self._queue.put(Stamped.now(kind, fields))
        except Exception:
            logger.exception("the app's display failed on a %s event", kind)

    def forward_to(self, plain: EventLog) -> None:
        """From now on, show the events on `plain` (a `PlainUi`), not in the
        app: the app has closed while the run goes on (architecture 11)."""
        self._plain = plain

    @property
    def forwarding(self) -> bool:
        return self._plain is not None

    def __enter__(self) -> TuiSink:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None
