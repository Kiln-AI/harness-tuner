"""The run directory is denied by default.

The read fence denies the run's own directory except its scratch. A
permission rule has no allow-over-deny, so the rendered `Read(...)` deny
rules name the run directory's entries one by one, and an entry that did
not exist at render time was not denied: a new writer that forgot
`cli.RUN_DIR_ENTRIES` left its file readable to the `Read` tool (Bash's
sandbox denies the whole tree either way).

`refresh_run_dir_denials` closes that: before EVERY session the dispatcher
re-reads the run directory and adds a deny rule for each top-level entry
that has none, in every settings file it passes. Only `scratch/` is ever
left open. So an entry is denied from the first session that starts after
it exists, whoever created it and whether or not it is listed. A session
already running when the entry appears keeps the rules it started with.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from pathlib import Path, PurePath, PureWindowsPath

logger = logging.getLogger(__name__)

SCRATCH = "scratch"


def rule_path(path: PurePath) -> str:
    """An absolute path as a Claude Code permission rule names it: `//`, then
    the path in POSIX form. Claude Code matches a Windows path in that form,
    with the drive as a lower-case first segment: `C:\\Users\\alice` is
    `//c/Users/alice`."""
    if isinstance(path, PureWindowsPath):
        drive = path.drive.rstrip(":").lower()
        return "//" + "/".join([drive, *path.parts[1:]])
    return "//" + str(path).lstrip("/")


def read_rule(path: Path) -> str:
    """The `Read(...)` deny rule for one path (a tree for a directory)."""
    body = rule_path(path)
    return f"Read({body}/**)" if path.is_dir() else f"Read({body})"


def refresh_run_dir_denials(run_dir: Path, settings_paths: Sequence[Path]) -> list[str]:
    """Add a deny rule for every top-level entry of `run_dir` except
    `scratch/` that a settings file does not deny yet. Returns the rules it
    added (empty when nothing was new). Each file is replaced atomically, so
    a session starting at the same moment reads the old file or the new
    one, never half of either."""
    if not run_dir.is_dir():
        return []
    root = run_dir.resolve()
    wanted = [
        read_rule(child) for child in sorted(root.iterdir()) if child.name != SCRATCH
    ]
    added: set[str] = set()
    for settings_path in settings_paths:
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # a missing file is a session without a fence file
        deny = settings.setdefault("permissions", {}).setdefault("deny", [])
        new = [rule for rule in wanted if rule not in deny]
        if not new:
            continue
        deny.extend(new)
        added.update(new)
        tmp = settings_path.with_name(settings_path.name + ".tmp")
        tmp.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        os.replace(tmp, settings_path)
    if added:
        logger.warning(
            "run directory: %d entr(ies) had no read deny rule and now have one: %s",
            len(added),
            ", ".join(sorted(added)),
        )
    return sorted(added)
