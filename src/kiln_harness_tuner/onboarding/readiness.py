"""The readiness check: the task has a run config to start from, and an eval
the loop can measure.

Both rules are the loop's own: a run config is a row with an ID, as setup
seeds the baseline from, and an eval is measurable by `measurable_evals`,
which the loop measures with. Splits are not checked here; setup's split
audit keeps its own message.
"""

from __future__ import annotations

import asyncio

from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.onboarding import kiln_urls
from kiln_harness_tuner.onboarding.terminal import Problem
from kiln_harness_tuner.setup import (
    _require_rows,
    _usable_run_configs,
    active_eval_rows,
    eval_rows_from_response,
    measurable_evals,
)


def _load_error_warning(count: int, task_name: str) -> str:
    evals, them = ("eval", "it") if count == 1 else ("evals", "them")
    return (
        f"Kiln couldn't load {count} {evals} of task {task_name}, so the run"
        f" can't measure {them}."
    )


async def readiness_problem(
    kiln: KilnApi, *, host: str, project_id: str, task_id: str, task_name: str
) -> tuple[Problem | None, list[str]]:
    """Every reason the task can't be tuned, as one problem (or `None`),
    and the warnings to print whatever the outcome."""
    base = f"/api/projects/{project_id}/tasks/{task_id}"
    run_config_body, eval_body, spec_body = await asyncio.gather(
        kiln.call("GET", f"{base}/run_configs"),
        kiln.call("GET", f"{base}/evals"),
        kiln.call("GET", f"{base}/specs"),
    )
    run_configs = _usable_run_configs(
        _require_rows(f"{base}/run_configs", run_config_body)
    )
    evals, load_errors = eval_rows_from_response(f"{base}/evals", eval_body)
    specs = _require_rows(f"{base}/specs", spec_body)
    warnings = [_load_error_warning(load_errors, task_name)] if load_errors else []

    lines: list[tuple[str, str]] = []
    if not run_configs:
        link = kiln_urls.run_configs(host, project_id, task_id)
        lines.append((f"It has no run configs. Create one in Kiln: {link}", link))
    if not measurable_evals(evals, specs):
        link = kiln_urls.evals(host, project_id, task_id)
        active = active_eval_rows(evals, specs)
        if active:
            names = ", ".join(f'"{row.get("name") or row["id"]}"' for row in active)
            lines.append(
                (
                    f"Its active evals have no default eval config: {names}."
                    f" Set one on each eval's page in Kiln: {link}",
                    link,
                )
            )
        else:
            lines.append(
                (f"It has no eval the loop can measure. Add one in Kiln: {link}", link)
            )
    if not lines:
        return None, warnings
    message = f"Task {task_name} ({task_id}) isn't ready to tune:\n" + "\n".join(
        f"  • {text}" for text, _link in lines
    )
    return Problem(message, open_url=lines[0][1], open_label="the first link"), warnings
