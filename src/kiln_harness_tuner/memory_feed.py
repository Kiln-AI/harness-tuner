"""The memory records agents write, forwarded into the run's event log.

Agents write memory through the loop gateway. The gateway appends one line
per write to the run folder's `gateway_memory.jsonl`
(`gateway.ops.State.record_memory`): the record's id, scope, tags and
overview, the op and the writer's role. The gateway runs in its own process
per session, so the loop reads the file. It reads the new lines when an
agent session ends (`ClaudeCliDispatcher.on_session_end`) and once more
before the run's first `exit` (a cancelled session never reaches its end
hook). Each line becomes one `memory_written` event:

    memory_written {id, op, role, scope, tags, overview,
                    record_kind,   # memory_schema.record_kind(tags, overview)
                    written_at,    # the gateway's ts
                    forwarded_by}  # the session label whose end read it, or "exit"

The field is `record_kind`, not `kind`: `kind` is the event's own name
(`EventLog.event(kind, **fields)`, and the wave log's line key), so a field
of that name cannot be sent.

`forwarded_by` is not the writer: concurrent sessions append to one file,
and a line does not say which session wrote it. `role` does.

Records that loop code writes (probe lessons, screen results, verdicts,
open questions) do not go through the gateway and are not in this file.
They have events of their own.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kiln_harness_tuner.memory_schema import record_kind

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import EventLog

logger = logging.getLogger(__name__)


class MemoryFeed:
    """Reads the gateway's memory lines from where the last read stopped.
    Only complete lines are read: a gateway can be in the middle of an
    append. Every call runs on the loop's thread."""

    def __init__(self, path: Path):
        self.path = path
        self._offset = 0

    def forward(self, log: EventLog, forwarded_by: str) -> int:
        """Emit one `memory_written` event per new line; return how many.
        A missing or unreadable file, or a line that is not a JSON object,
        forwards nothing and raises nothing: a display feed must not stop
        the run."""
        try:
            with self.path.open("rb") as f:
                f.seek(self._offset)
                data = f.read()
        except OSError:
            return 0
        end = data.rfind(b"\n") + 1
        self._offset += end
        forwarded = 0
        for raw in data[:end].splitlines():
            fields = _event_fields(raw, forwarded_by)
            if fields is None:
                logger.warning("memory feed: skipped a line that is not a record")
                continue
            log.event("memory_written", **fields)
            forwarded += 1
        return forwarded


def _event_fields(raw: bytes, forwarded_by: str) -> dict[str, Any] | None:
    try:
        row = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(row, dict):
        return None
    raw_tags = row.get("tags")
    tags = (
        [t for t in raw_tags if isinstance(t, str)]
        if isinstance(raw_tags, list)
        else []
    )
    overview = str(row.get("overview") or "")
    return {
        "id": str(row.get("id") or ""),
        "op": str(row.get("op") or ""),
        "role": str(row.get("role") or ""),
        "scope": str(row.get("scope") or ""),
        "tags": tags,
        "overview": overview,
        "record_kind": record_kind(tags, overview).value,
        "written_at": str(row.get("ts") or ""),
        "forwarded_by": forwarded_by,
    }
