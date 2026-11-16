"""The run's catalogue snapshot, `model_catalog.json` in the run directory.

The CLI writes it once at the start of the run. The gateway process of each
agent session loads it, as it loads `model_policy.json`, so every agent of
the run sees the same data.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import ValidationError

from kiln_harness_tuner.model_catalog.schema import Catalog

CATALOG_FILE = "model_catalog.json"
NO_SNAPSHOT = "no snapshot"


def write_catalog(run_dir: Path, catalog: Catalog) -> None:
    """Write the snapshot atomically. A write that fails raises: a run that
    cannot write its run directory cannot start."""
    path = run_dir / CATALOG_FILE
    tmp = path.with_name(CATALOG_FILE + ".tmp")
    tmp.write_text(catalog.model_dump_json(), encoding="utf-8")
    os.replace(tmp, path)


def load_catalog(run_dir: Path) -> Catalog:
    """The snapshot, or `Catalog.unavailable("no snapshot")` when the file is
    missing, empty or not a valid catalogue. Never raises."""
    try:
        text = (run_dir / CATALOG_FILE).read_text(encoding="utf-8")
        if text.strip():
            return Catalog.model_validate_json(text)
    except (OSError, UnicodeDecodeError, ValidationError):
        pass
    return Catalog.unavailable(NO_SNAPSHOT)
