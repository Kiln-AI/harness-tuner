"""Fan-out helpers for the loop's three concurrent stretches — a wave's items,
the promotion gate's val fills, and one item's per-target measurements.

`asyncio.gather(return_exceptions=True)` or a plain `asyncio.wait` would
collect exceptions and re-raise the first only AFTER every sibling had
finished. A fatal — the circuit breaker on a 402, a 422 missing split, a
statistics contract mismatch — is a run-ending answer, so the siblings'
remaining work (an Opus session up to 30 min, more eval jobs that will hit
the same wall) would be money spent on a run that has already decided to
stop. `first_error_or_done` stops them at the first raise.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable


async def first_error_or_done(
    tasks: Iterable[asyncio.Task],
    *,
    timeout: float | None = None,
) -> tuple[BaseException | None, set[asyncio.Task]]:
    """Wait for the tasks until one RAISES, all finish, or `timeout` (seconds
    from now) expires.

    Returns `(error, still_pending)`:
    - a task raised → every pending sibling is cancelled and awaited (their
      cancel paths kill subprocesses and cancel server jobs) and `error` is
      the first exception; `still_pending` is empty;
    - the timeout expired → `error` is None and `still_pending` names the
      unfinished tasks, NOT cancelled — the caller decides (the wave runner
      cancels them as deadline casualties);
    - all finished → `(None, set())`.
    A cancelled task never counts as an error."""
    remaining = set(tasks)
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        while remaining:
            budget = None if deadline is None else max(deadline - time.monotonic(), 0.0)
            done, pending = await asyncio.wait(
                remaining, timeout=budget, return_when=asyncio.FIRST_EXCEPTION
            )
            raised = [
                t for t in done if not t.cancelled() and t.exception() is not None
            ]
            if raised:
                await _cancel_all(pending)
                return raised[0].exception(), set()
            if not done:
                return None, pending  # the timeout expired with work in flight
            remaining = pending
    except asyncio.CancelledError:
        # The CALLER was cancelled (deadline, sibling fatal, SIGTERM).
        # `asyncio.gather` cancelled its children in this case; `asyncio.wait`
        # does not — so without this the per-target measurements and the val
        # fills keep running, and their cancel paths (which kill subprocesses
        # and cancel server jobs) never run.
        await _cancel_all(remaining)
        raise
    return None, set()


async def _cancel_all(tasks: Iterable[asyncio.Task]) -> None:
    pending = [t for t in tasks if not t.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
