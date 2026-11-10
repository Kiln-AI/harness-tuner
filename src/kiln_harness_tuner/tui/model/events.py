"""The item the run thread puts on the app's queue."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Stamped:
    """One loop event and the moment it was emitted. The sink stamps it on
    the loop thread, so `mono` is the loop's own clock (`time.monotonic`,
    the clock of `run_start.deadline`) and `wall` matches the wave log's
    `ts` (audit 5.2). The fields are the emitter's own, read-only."""

    kind: str
    fields: Mapping[str, Any]
    mono: float
    wall: datetime

    @classmethod
    def now(cls, kind: str, fields: Mapping[str, Any] | None = None) -> Stamped:
        """`kind` and `fields`, stamped with this moment."""
        return cls(kind, fields or {}, time.monotonic(), datetime.now())
