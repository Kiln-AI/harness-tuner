"""Failure notes: what a reader saw in a failing train item's trace.

A failure card (`gateway.failure_cards`) has parts that code makes: the
input, the output, the expected output and a matching success. Two parts
need a reader: the step that lost the item, and the kind of the failure. A
reader writes them as `FailureNote`s:

- the analyzer, in `ExperimentResult.failure_notes`, for the failing items
  it read on the candidate run config;
- the planner, in `BatchPlan.failure_notes`, for the failing items it read
  on the baseline.

Code stores each note as one JSON line in the run folder,
`runs/<run_id>/failure_notes.jsonl`. The file is in the run folder, which no
agent reads: the notes reach an agent only on the cards of the gateway's
`get_failure_cards`. The gateway joins a note to a card by eval and item,
and it builds a card only for a train item, so a note that names any other
item reaches no one.

The notes of a run last for that run. They are not memory records.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from kiln_harness_tuner.schemas import FailureNote

FILE_NAME = "failure_notes.jsonl"
# A note is one short line on a card: a longer value is cut when stored.
LOST_AT_MAX_CHARS = 240
PATTERN_MAX_CHARS = 80
KINDS = frozenset({"config", "eval_data", "judge", "environment", "noise"})


@dataclass(frozen=True)
class StoredNote:
    """One line of the notes file."""

    eval_id: str
    item_id: str
    run_config_id: str  # the run config whose trace the reader read
    kind: str
    lost_at: str
    pattern: str | None
    by: str  # the role that read the trace
    source: str  # the experiment item ("w2-1") or the plan ("w3-plan")
    ts: str = ""


def _one_line(text: str, limit: int) -> str:
    line = " ".join(str(text).split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def append_notes(
    run_dir: Path | None,
    notes: Iterable[FailureNote],
    *,
    run_config_id: str,
    by: str,
    source: str,
) -> int:
    """Append `notes` to the run's notes file, and return how many lines
    were written. A note's own `run_config_id` wins over `run_config_id`:
    the caller checks it first (`items.store_failure_notes`). Each line is
    one `os.write` on an `O_APPEND` file, so the lines of concurrent items
    do not mix. A note with no item, no eval or an empty `lost_at` is not
    written. No run folder: nothing is written."""
    if run_dir is None:
        return 0
    written = 0
    for note in notes:
        lost_at = _one_line(note.lost_at, LOST_AT_MAX_CHARS)
        read_on = note.run_config_id or run_config_id
        if not (note.eval_id and note.item_id and lost_at):
            continue
        line = json.dumps(
            {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "eval_id": note.eval_id,
                "item_id": note.item_id,
                "run_config_id": read_on,
                "kind": note.kind,
                "lost_at": lost_at,
                "pattern": _one_line(note.pattern, PATTERN_MAX_CHARS)
                if note.pattern
                else None,
                "by": by,
                "source": source,
            },
            ensure_ascii=False,
        )
        fd = os.open(run_dir / FILE_NAME, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (line + "\n").encode())
        finally:
            os.close(fd)
        written += 1
    return written


def _stored(row: Any) -> StoredNote | None:
    if not isinstance(row, dict):
        return None
    eval_id, item_id = row.get("eval_id"), row.get("item_id")
    kind, lost_at = row.get("kind"), row.get("lost_at")
    if not (eval_id and item_id and lost_at) or kind not in KINDS:
        return None
    pattern = row.get("pattern")
    return StoredNote(
        eval_id=str(eval_id),
        item_id=str(item_id),
        run_config_id=str(row.get("run_config_id") or ""),
        kind=str(kind),
        lost_at=_one_line(lost_at, LOST_AT_MAX_CHARS),
        pattern=_one_line(pattern, PATTERN_MAX_CHARS) if pattern else None,
        by=str(row.get("by") or ""),
        source=str(row.get("source") or ""),
        ts=str(row.get("ts") or ""),
    )


def load_notes(run_dir: Path | None) -> list[StoredNote]:
    """Every readable note of the run, oldest first. A line that is not a
    note is skipped. No file: no notes. It never raises."""
    if run_dir is None:
        return []
    try:
        text = (run_dir / FILE_NAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    notes: list[StoredNote] = []
    for line in text.splitlines():
        try:
            note = _stored(json.loads(line))
        except ValueError:
            continue
        if note is not None:
            notes.append(note)
    return notes


def note_for(
    notes: Iterable[StoredNote], eval_id: str, item_id: str, run_config_id: str
) -> StoredNote | None:
    """The note a card shows for one item of one eval: the newest note read
    on `run_config_id`, else the newest note read on any other run config.
    A note from another run config is still useful: an item that an arm did
    not fix often fails on the arm as it fails on its parent, and an
    eval-data or judge failure belongs to the item, not to the config. The
    card names the run config of the note, so the reader can tell."""
    same = other = None
    for note in notes:
        if note.eval_id != eval_id or note.item_id != item_id:
            continue
        if note.run_config_id == run_config_id:
            same = note
        else:
            other = note
    return same or other
