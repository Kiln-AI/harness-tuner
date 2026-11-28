"""What the detectors that read traces share.

Each such detector reads `DetectorInputs.trace_stats`: one
`gateway.trace_files.trace_stats` dict for each stored train run of the
baseline. A detector states where the largest case is, so that the baseline
reviewer can open that trace.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# Kiln and the providers limit a tool name and a skill name to 64
# characters. Kiln's ids have 12 digits. Each cut keeps a fact inside the
# 300-character limit of a detection for any other source.
NAME_CHARS = 64
ID_CHARS = 24


def place(stats: Mapping[str, Any]) -> str:
    """Where one run is: its train item and its eval."""
    item = str(stats.get("item_id") or "?")[:ID_CHARS]
    eval_id = str(stats.get("eval_id") or "?")[:ID_CHARS]
    return f"train item `{item}`, eval `{eval_id}`"


def item_count(runs: Iterable[Mapping[str, Any]]) -> int:
    """The distinct train items of the runs. One item can have a stored run
    on each eval; a run with no item id counts as its own item."""
    ids: set[str] = set()
    unknown = 0
    for run in runs:
        item = run.get("item_id")
        if item:
            ids.add(str(item))
        else:
            unknown += 1
    return len(ids) + unknown


def listed(names: Iterable[str], budget: int) -> str:
    """The names in code marks, joined by commas, in at most `budget`
    characters. The names that do not fit are counted: "and 3 more"."""
    names = [name[:NAME_CHARS] for name in names]
    shown: list[str] = []
    for index, name in enumerate(names):
        rest = len(names) - index - 1
        tail = f" and {rest:,} more" if rest else ""
        text = ", ".join([*shown, f"`{name}`"])
        if shown and len(text) + len(tail) > budget:
            return f"{', '.join(shown)} and {len(names) - index:,} more"
        shown.append(f"`{name}`")
    return ", ".join(shown)
