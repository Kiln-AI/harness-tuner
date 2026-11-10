"""The run config names the run screen shows (architecture section 8).

Events carry run config ids only. The app looks the names up in Kiln, the
task's run config list, and keeps them here. An id the dictionary does not
know makes it stale; the app runs one lookup at a time, at most once every
`LOOKUP_INTERVAL_SECS`. A failed lookup keeps the ids, and the next unknown
id asks again. The lookup itself is the app's (an async worker).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

LOOKUP_INTERVAL_SECS = 10.0


class RcNames:
    def __init__(self, seed: Mapping[str, str] | None = None):
        self._names: dict[str, str] = dict(seed or {})
        self._descriptions: dict[str, str] = {}
        # Ids already asked for: a lookup that did not find one does not make
        # the dictionary stale again for it.
        self._asked: set[str] = set(self._names)
        self._pending: set[str] = set()
        self._running = False
        self._last_started: float | None = None

    @property
    def stale(self) -> bool:
        return bool(self._pending)

    def see(self, rc_id: str | None) -> None:
        if rc_id and rc_id not in self._names and rc_id not in self._asked:
            self._pending.add(rc_id)

    def lookup_due(self, now: float) -> bool:
        if not self._pending or self._running:
            return False
        return (
            self._last_started is None
            or now - self._last_started >= LOOKUP_INTERVAL_SECS
        )

    def lookup_started(self, now: float) -> None:
        self._running = True
        self._last_started = now
        self._asked |= self._pending
        self._pending = set()

    def learn(self, rows: Iterable[Mapping[str, Any]]) -> bool:
        """The rows of `GET /api/projects/{p}/tasks/{t}/run_configs`. True
        when a name or a description changed."""
        self._running = False
        changed = False
        for row in rows:
            rc_id, name = row.get("id"), row.get("name")
            if not rc_id or not isinstance(name, str) or not name:
                continue
            rc_id = str(rc_id)
            description = row.get("description")
            if self._names.get(rc_id) != name:
                self._names[rc_id] = name
                changed = True
            if isinstance(description, str) and description:
                if self._descriptions.get(rc_id) != description:
                    self._descriptions[rc_id] = description
                    changed = True
        self._pending -= set(self._names)
        return changed

    def lookup_failed(self) -> None:
        self._running = False

    def name(self, rc_id: str | None) -> str | None:
        return self._names.get(rc_id) if rc_id else None

    def description(self, rc_id: str | None) -> str | None:
        return self._descriptions.get(rc_id) if rc_id else None

    def label(self, rc_id: str, item_id: str | None = None) -> str:
        """The name, with the item that built it beside it ("Hold fields
        wrapper · w3-4"). Before the name is known, the item id or the rc
        id."""
        name = self._names.get(rc_id)
        if name is None:
            return item_id or rc_id
        return f"{name} · {item_id}" if item_id else name
