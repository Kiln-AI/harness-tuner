"""WaveLog — the append-only JSONL event log (docs/architecture.md section 11): the raw
audit trail beside the structured `RunStatus`. One line per event:
`{"ts": <iso>, "kind": <kind>, **fields}`, flushed per line so a killed run
keeps everything it logged. Lives under `runs/<run_id>/` (run scratch).

`FanoutLog` is the wiring glue: one EventLog forwarding to several consumers
(wave log → status tracker → UI, in that order — the UI renders post-update
snapshots).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import TracebackType

from pydantic import BaseModel


def _jsonable(value: object) -> object:
    """Pydantic models dump to plain data; anything else non-JSON falls back
    to `repr` — an audit line must never fail to write."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


class WaveLog:
    """Append-only JSONL EventLog. Opens (and creates parents for) `path` on
    construction in append mode — restarts and re-wires keep prior lines."""

    def __init__(self, path: Path, *, now: Callable[[], datetime] = datetime.now):
        self.path = path
        self._now = now
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8")

    def event(self, kind: str, **fields: object) -> None:
        line = {"ts": self._now().isoformat(), "kind": kind}
        line.update({key: _jsonable(value) for key, value in fields.items()})
        self._file.write(json.dumps(line, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> WaveLog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class FanoutLog:
    """Forwards each event to every child log, in construction order."""

    def __init__(self, *logs: object):
        self._logs = logs

    def event(self, kind: str, **fields: object) -> None:
        for log in self._logs:
            log.event(kind, **fields)  # type: ignore[attr-defined]
