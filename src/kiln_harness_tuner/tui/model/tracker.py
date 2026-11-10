"""The run screen's own `RunStatusTracker`.

The loop's tracker reads the clock when an event is emitted. This one folds
events later, from the app's queue, so a clock read at fold time would move
every timing by the queue's delay. `UiTracker` reads the stamp of the event
it folds instead, and the live clock at other times (audit 3.3). The loop's
tracker, which the report reads, is not changed.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from kiln_harness_tuner.schemas import RunStatus
from kiln_harness_tuner.status import RunStatusTracker
from kiln_harness_tuner.tui.model.events import Stamped


class UiTracker(RunStatusTracker):
    def __init__(
        self,
        *,
        project_id: str,
        task_id: str,
        agent_cli: str,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._live_clock = clock
        self._stamp: float | None = None
        super().__init__(
            project_id=project_id,
            task_id=task_id,
            guidance="",
            clock=self._now,
            agent_cli=agent_cli,
        )

    def _now(self) -> float:
        return self._live_clock() if self._stamp is None else self._stamp

    def fold(self, stamped: Stamped) -> None:
        self._stamp = stamped.mono
        try:
            self.event(stamped.kind, **stamped.fields)
        finally:
            self._stamp = None

    @property
    def status(self) -> RunStatus:
        """The folded status itself, not a copy. The app folds and reads on
        its own thread, so it needs no `snapshot()` (a deep copy of the
        whole run's history). Read-only."""
        return self._status
