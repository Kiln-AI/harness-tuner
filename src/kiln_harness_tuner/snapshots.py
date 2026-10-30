"""Memory snapshots around every agent dispatch.

The viewer's step-through shows each decision against the memory store as
it stood when the agent started, and what the step wrote. Records have no
edit history in Kiln, so the only way to know what a planner, builder or
analyzer could see is to copy the store at that moment. `snapshot_memory`
writes `runs/<id>/snapshots/NNN-<label>.json` and logs a `memory_snapshot`
event; it never fails the loop.

Lives outside `orchestrator` so `items` (which the orchestrator imports)
can call it too.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

logger = logging.getLogger(__name__)


async def snapshot_memory(ctx: Ctx, label: str) -> None:
    if ctx.run_dir is None:
        return
    try:
        snapshot = await ctx.memory.snapshot()
        folder = ctx.run_dir / "snapshots"
        folder.mkdir(parents=True, exist_ok=True)
        seq = len(list(folder.glob("*.json"))) + 1
        path = folder / f"{seq:03d}-{label}.json"
        path.write_text(
            json.dumps(snapshot, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        ctx.log.event(
            "memory_snapshot",
            label=label,
            file=path.name,
            records=len(snapshot["records"]),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory snapshot %s failed: %r", label, exc)
        ctx.log.event("memory_snapshot_failed", label=label, error=repr(exc))
