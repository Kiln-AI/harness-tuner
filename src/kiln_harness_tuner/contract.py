"""The $0 API-contract preflight.

kiln-harness-tuner reads the Kiln API through a hand-maintained fake in its tests, so a
server-side shape change is found only by a live run: a response shape
can move silently, and one such move can measure an empty set and call it
a clean read. Every check here is
a cheap read or a pure computation against the LIVE server, run once before
wave 1, and it fails with the field names — so a drift costs seconds at
start, never a wave.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from kiln_harness_tuner.analysis import AnalysisError, flatten_result_row
from kiln_harness_tuner.setup import eval_rows_from_response

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

logger = logging.getLogger(__name__)


class ContractPreflightError(AnalysisError):
    """A live response no longer has the shape kiln-harness-tuner reads. The message
    names the route, the missing keys and the keys received."""


# Tiny fixtures — the statistics routes are pure, so these cost nothing.
_MCNEMAR_FIXTURE = {
    "operation": "mcnemar_paired",
    "outcomes_a": [1, 0, 1, 0, 1, 0],
    "outcomes_b": [1, 1, 1, 0, 0, 1],
}
_COMPARE_FIXTURE = {
    "operation": "compare_paired",
    "values_a": [3.0, 2.0, 4.0, 3.0, 2.0, 5.0],
    "values_b": [4.0, 2.0, 4.5, 3.5, 3.0, 5.0],
}
_MCNEMAR_KEYS = ("p_exact", "ci_low", "ci_high", "significant")
_COMPARE_KEYS = ("wilcoxon_p", "ci_low", "ci_high", "n_pairs_used", "significant")


def _require_keys(route: str, response: Any, keys: tuple[str, ...]) -> None:
    if not isinstance(response, dict):
        raise ContractPreflightError(
            f"{route}: expected a JSON object, got {type(response).__name__}"
        )
    missing = [k for k in keys if k not in response]
    if missing:
        raise ContractPreflightError(
            f"{route}: response lacks {missing} (got keys {sorted(response)}) — "
            "the server's shape moved past what kiln-harness-tuner reads; "
            "fix the reader before spending a wave"
        )


async def contract_preflight(ctx: Ctx, baseline_rc_id: str) -> dict[str, Any]:
    """Run every contract check; returns a small summary for the wave log."""
    base = f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}"
    checked: dict[str, Any] = {}

    # 1. The evals listing envelope — `_active_evals` iterated a dict's KEYS
    #    once and handed every wave an empty active set.
    route = f"GET {base}/evals"
    raw = await ctx.kiln.call("GET", f"{base}/evals")
    evals, load_errors = eval_rows_from_response(f"{base}/evals", raw)
    if not isinstance(evals, list):
        raise ContractPreflightError(f"{route}: eval rows are not a list")
    checked["evals"] = len(evals)
    checked["eval_load_errors"] = load_errors

    # 2. Both statistics operations, on fixtures.
    for body, keys in (
        (_MCNEMAR_FIXTURE, _MCNEMAR_KEYS),
        (_COMPARE_FIXTURE, _COMPARE_KEYS),
    ):
        response = await ctx.kiln.call(
            "POST", "/api/statistics", body=body, retries=True
        )
        _require_keys(f"POST /api/statistics ({body['operation']})", response, keys)
    checked["statistics"] = "ok"

    # 3. One stored-results read: every row must flatten to something that
    #    carries an item id, or pairing silently measures nothing.
    probed = 0
    for eval_def in evals:
        config_id = (
            eval_def.get("current_config_id") if isinstance(eval_def, dict) else None
        )
        # An eval Kiln can't load, active or not, answers its results read
        # the same way, and it is not in the run.
        if not config_id or str(eval_def["id"]) in ctx.unloadable_eval_ids:
            continue
        route = f"GET {base}/evals/{eval_def['id']}/…/results?split=train"
        rows = await ctx.analyzer.stored_results(
            eval_def["id"], config_id, baseline_rc_id, "train"
        )
        flats = [flatten_result_row(row) for row in rows]
        with_id = [
            f
            for f in flats
            if isinstance(f, dict) and (f.get("dataset_id") or f.get("eval_input_id"))
        ]
        if flats and not with_id:
            # A stray item-less record is data; EVERY row item-less is the
            # wrapper shape the reader no longer understands.
            first = flats[0]
            shape = sorted(first) if isinstance(first, dict) else type(first).__name__
            raise ContractPreflightError(
                f"{route}: no result row carries a dataset_id/eval_input_id "
                f"after flattening (first row: {shape}) — pairing would "
                "silently drop every row"
            )
        probed += 1
        if rows or probed >= 3:
            break
    checked["results_probed"] = probed
    logger.info("contract preflight ok: %s", checked)
    return checked
