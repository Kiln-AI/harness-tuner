"""Spend reconciliation: the ledger against the records.

The ledger books provider spend when a step reads its results back, so work
in flight when a run stops — an eval job, a drive, a screening chunk — never
reaches it (a run can book less Kiln spend than the records it
created cost). At every wave boundary and at every exit, code reads
the records THIS run created from the project on disk, prices them, and
books the difference as a `spend` event, source `reconciliation`.

Attribution — a record is this run's when it was created inside the run's
window (at or after the run started, the ledger's 120-second grace
included) AND:

- an EVAL RUN: its (eval, eval config, run config) is one this run started
  an eval job on (`Analyzer.started_jobs`), and it was created at or after
  the first such job (same grace);
- a TASK RUN: it carries a tag only loop code writes — the timing drives'
  (`latency.LATENCY_RUN_TAG`), the code probes' (`probes.PROBE_RUN_TAG`),
  the gateway's drives' (`gateway.ops.GATEWAY_RUN_TAG`), the model
  preflight's test calls (`model_preflight.MODEL_PREFLIGHT_RUN_TAG`) — or it
  is the scored task run of an attributed eval run.

A record created before the run (a stored row the run reused) is outside
the window. Another loop run on the same task cannot overlap (the run lock).
A timestamp that cannot be read is not attributed.

Pricing, once per record id: a task run's `usage.cost` (the task call) and
an eval run's `eval_usage.cost` (the judge). Eval runs on disk carry
`task_run_usage: null`, so an eval run's task cost is its scored task run's
`usage.cost`; the eval run's own `task_run_usage.cost` only when no scored
task run is on disk.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from kiln_harness_tuner.analysis import FRESH_RUN_GRACE
from kiln_harness_tuner.gateway.ops import GATEWAY_RUN_TAG
from kiln_harness_tuner.latency import LATENCY_RUN_TAG
from kiln_harness_tuner.model_preflight import MODEL_PREFLIGHT_RUN_TAG
from kiln_harness_tuner.probes import PROBE_RUN_TAG
from kiln_harness_tuner.quarantine import task_dirs

LOOP_TASK_RUN_TAGS = (
    LATENCY_RUN_TAG,
    PROBE_RUN_TAG,
    GATEWAY_RUN_TAG,
    MODEL_PREFLIGHT_RUN_TAG,
)
MIN_GAP_USD = 0.005  # below this, nothing is booked (rounding)
SOURCE = "reconciliation"


def _created(record: dict[str, Any]) -> datetime | None:
    raw = record.get("created_at")
    if not isinstance(raw, str):
        return None
    try:
        created = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if created.tzinfo is not None:
        created = created.astimezone().replace(tzinfo=None)
    return created


def _cost(usage: Any) -> float:
    value = usage.get("cost") if isinstance(usage, dict) else None
    return float(value) if isinstance(value, (int, float)) else 0.0


def _load(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _id_of(folder: Path, file_name: str) -> str:
    """The id a `.kiln` file names, else the directory name's id part."""
    data = _load(folder / file_name) or {}
    return str(data.get("id") or folder.name.split(" - ")[0])


def _loop_tagged(tags: Any) -> bool:
    return any(
        str(t) == tag or str(t).startswith(f"{tag}_")
        for t in tags or []
        for tag in LOOP_TASK_RUN_TAGS
    )


@dataclass
class Found:
    """What the records say this run spent."""

    task_runs: dict[str, float] = field(default_factory=dict)  # id -> usd
    eval_runs: dict[str, float] = field(default_factory=dict)  # id -> judge usd

    @property
    def total(self) -> float:
        return sum(self.task_runs.values()) + sum(self.eval_runs.values())


def records_spend(
    project_root: str | Path,
    task_id: str,
    started_at: datetime,
    started_jobs: dict[tuple[str, str, str], datetime],
) -> Found:
    """This run's provider spend, read from the records on disk (see the
    module doc for the attribution and the pricing)."""
    found = Found()
    floor = started_at - FRESH_RUN_GRACE
    # The records' files were written when they were created; a file last
    # written before the run cannot hold a record of it (a cheap filter
    # before parsing thousands of files).
    mtime_floor = (floor - timedelta(days=1)).timestamp()
    for task_dir in task_dirs(Path(project_root), task_id):
        task_runs: dict[str, dict[str, Any]] = {}
        for path in task_dir.glob("runs/*/task_run.kiln"):
            try:
                if os.stat(path).st_mtime < mtime_floor:
                    continue
            except OSError:
                continue
            data = _load(path)
            if data and data.get("id"):
                task_runs[str(data["id"])] = data
        scored: set[str] = set()
        for eval_dir in task_dir.glob("evals/*"):
            eval_id = _id_of(eval_dir, "eval.kiln")
            for config_dir in eval_dir.glob("configs/*"):
                config_id = _id_of(config_dir, "eval_config.kiln")
                for path in config_dir.glob("runs/*/eval_run.kiln"):
                    try:
                        if os.stat(path).st_mtime < mtime_floor:
                            continue
                    except OSError:
                        continue
                    run = _load(path)
                    if not run or not run.get("id"):
                        continue
                    key = (eval_id, config_id, str(run.get("task_run_config_id")))
                    first_job = started_jobs.get(key)
                    created = _created(run)
                    if first_job is None or created is None:
                        continue
                    if created < floor or created < first_job - FRESH_RUN_GRACE:
                        continue
                    found.eval_runs[str(run["id"])] = _cost(run.get("eval_usage"))
                    scored_id = run.get("scored_run_id")
                    if scored_id and str(scored_id) in task_runs:
                        scored.add(str(scored_id))
                    else:
                        # No scored task run on disk: the eval run's own
                        # task cost, when it carries one.
                        found.task_runs.setdefault(
                            f"eval_run:{run['id']}", _cost(run.get("task_run_usage"))
                        )
        for run_id, data in task_runs.items():
            created = _created(data)
            if created is None or created < floor:
                continue
            if run_id in scored or _loop_tagged(data.get("tags")):
                found.task_runs[run_id] = _cost(data.get("usage"))
    return found


@dataclass(frozen=True)
class Reconciliation:
    booked: float  # the provider lane before this reconciliation
    found: float
    gap: float  # what was booked now (0 when nothing was)

    @property
    def line(self) -> str:
        return (
            f"spend reconciled: booked ${self.booked:.2f}, found ${self.found:.2f}"
            f" in records (+${self.gap:.2f} in-flight work)"
        )


def reconcile(ctx: Any, *, when: str, quiet: bool = False) -> Reconciliation | None:
    """Price this run's records, book what the ledger lacks, and log one
    line (`spend_reconciled`). `quiet`: log the line only when something was
    booked (a wave boundary). Never raises: a reconciliation that cannot run
    says why in the log. Synchronous — it runs inside an interrupted exit."""
    analyzer = getattr(ctx, "analyzer", None)
    started_at = getattr(analyzer, "run_started_at", None)
    meter = getattr(ctx, "spend", None)
    if started_at is None or meter is None:
        return None
    try:
        found = records_spend(
            ctx.target.project_path,
            ctx.target.task_id,
            started_at,
            dict(getattr(analyzer, "started_jobs", {}) or {}),
        )
    except Exception as exc:  # the ledger stands as booked
        ctx.log.event("spend_reconcile_failed", when=when, error=repr(exc)[:300])
        return None
    booked = meter.provider_total
    gap = found.total - booked
    result = Reconciliation(
        booked=booked, found=found.total, gap=gap if gap >= MIN_GAP_USD else 0.0
    )
    if result.gap:
        ctx.log.event(
            "spend",
            source=SOURCE,
            lane="provider",
            cost_usd=result.gap,
            evidence_cost_usd=result.gap,
        )
    if result.gap or not quiet:
        ctx.log.event(
            "spend_reconciled",
            when=when,
            booked_usd=booked,
            found_usd=found.total,
            gap_usd=result.gap,
            task_runs=len(found.task_runs),
            eval_runs=len(found.eval_runs),
            line=result.line,
        )
    return result
