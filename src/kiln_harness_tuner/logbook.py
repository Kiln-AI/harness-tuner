"""Per-run diagnostic log — `runs/<run_id>/kiln-harness-tuner.log`.

The wave log is the run's *evidence*: structured, folded into `RunStatus`, one
line per thing that HAPPENED. This is its opposite number — the run's
*diagnostics*: dispatch spawns and exits, retries, timeouts, stderr tails, and
any warning a library decides to emit. Until now that stream was terminal
output only, so a run's own account of why it got stuck died with the scroll
buffer.

Design notes:

- **Root logger, file handler.** Attaching at the root is what makes third-party
  warnings (httpx, asyncio, pydantic) land in the file too — the point is to
  catch what we did NOT think to log.
- **Nothing is added to stderr.** The live UI owns the terminal; a second
  handler writing there would tear through `rich.live`'s frame. The file is the
  only sink this module installs.
- **Idempotent.** `configure_run_logging` removes a handler it installed before
  re-installing, so a second call in one process (tests, a future in-process
  re-run) neither duplicates lines nor leaks file descriptors.
- **Wall-clock ISO timestamps**, not the run's monotonic clock: this file is
  read next to `wave_log.jsonl`, whose `ts` is wall-clock ISO, and the two have
  to line up by eye.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

LOG_FILE_NAME = "kiln-harness-tuner.log"
# `KILN_HARNESS_TUNER_LOG_LEVEL=DEBUG` (or any level name / number) turns the file up
# without touching the terminal. INFO is the default: the lifecycle moments below are
# INFO, so the ordinary file is a readable narrative, not a token dump.
LEVEL_ENV_VAR = "KILN_HARNESS_TUNER_LOG_LEVEL"
DEFAULT_LEVEL = logging.INFO
# `logger name` sits between level and message so a line says which subsystem
# spoke (`kiln_harness_tuner.dispatch.claude_cli`, `httpx`, …) — the file's only
# structure.
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"
# ISO-8601-ish, matching `wave_log`'s `datetime.isoformat()` to the second.
LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"
# Marks the handler this module owns, so re-configuring replaces exactly it and
# never a handler somebody else attached to the root.
_HANDLER_TAG = "kiln-harness-tuner-run-log"


def resolve_level(raw: str | None = None) -> int:
    """The configured level, defaulting to INFO. An unparseable value falls
    back rather than raising: a typo in an env var must not kill a run whose
    whole point is to produce a log about what went wrong."""
    value = (raw if raw is not None else os.environ.get(LEVEL_ENV_VAR, "")).strip()
    if not value:
        return DEFAULT_LEVEL
    if value.isdigit():
        return int(value)
    return logging.getLevelNamesMapping().get(value.upper(), DEFAULT_LEVEL)


def log_path(run_dir: Path) -> Path:
    return run_dir / LOG_FILE_NAME


def _remove_existing(root: logging.Logger) -> None:
    for handler in list(root.handlers):
        if getattr(handler, "name", None) == _HANDLER_TAG:
            root.removeHandler(handler)
            handler.close()


def run_log_configured() -> bool:
    """Whether `configure_run_logging` has put the run log on the root
    logger. Until then a log record with no handler goes to stderr."""
    return any(
        getattr(handler, "name", None) == _HANDLER_TAG
        for handler in logging.getLogger().handlers
    )


def configure_run_logging(run_dir: Path, *, level: int | str | None = None) -> Path:
    """Point the root logger at `runs/<run_id>/kiln-harness-tuner.log` and return the
    path.

    Appends rather than truncates: a run directory is reused by a re-run into
    the same id, and losing the first attempt's diagnostics is exactly the
    failure this file exists to prevent.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    path = log_path(run_dir)
    resolved = (
        resolve_level(level) if level is None or isinstance(level, str) else int(level)
    )
    handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    handler.name = _HANDLER_TAG
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    handler.setLevel(resolved)
    root = logging.getLogger()
    _remove_existing(root)
    root.addHandler(handler)
    # The root's own level gates before any handler sees a record, so it has to
    # come down too — but only downward: a caller who already set DEBUG
    # globally keeps it.
    if root.level == logging.NOTSET or root.level > resolved:
        root.setLevel(resolved)
    # httpx logs one INFO line per request, which would fill the file with
    # HTTP noise and hide the run's real warnings. Its warnings
    # still land; the request narrative is DEBUG-visible via
    # KILN_HARNESS_TUNER_LOG_LEVEL.
    logging.getLogger("httpx").setLevel(
        resolved if resolved <= logging.DEBUG else logging.WARNING
    )
    return path
