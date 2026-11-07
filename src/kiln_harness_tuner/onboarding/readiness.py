"""The readiness check: the task has a run config to start from, and an eval
the loop can measure.

Both rules are the loop's own: a run config is a row with an ID, as setup
seeds the baseline from, and an eval is measurable by `measurable_evals`,
which the loop measures with. Splits are not checked here; setup's split
audit keeps its own message.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.onboarding import kiln_urls
from kiln_harness_tuner.onboarding.terminal import (
    Action,
    ActionKind,
    Problem,
    english_list,
    plural,
)
from kiln_harness_tuner.setup import (
    _require_rows,
    _usable_run_configs,
    active_eval_rows,
    eval_rows_from_response,
    measurable_evals,
)

WHY = (
    "The tuner starts from one of the task's run configs and measures every change",
    "with its evals. This task is missing:",
)


@dataclass(frozen=True)
class Readiness:
    """The check's outcome: the problem, if any, and the warnings to print
    whatever the outcome; a problem carries them too, for after its block.
    The counts are for the step line."""

    problem: Problem | None
    run_configs: int
    measurable: int
    warnings: list[str] = field(default_factory=list)

    @property
    def step_text(self) -> str:
        return (
            f"{plural(self.run_configs, 'run config')}"
            f" · {plural(self.measurable, 'eval')} the tuner can measure"
        )


def _load_error_warning(count: int) -> str:
    evals = plural(count, "eval")
    them = "it" if count == 1 else "them"
    return f"Kiln couldn't load {evals} of this task, so the run can't measure {them}."


def _no_judge_line(names: list[str]) -> str:
    has = "has" if len(names) == 1 else "have"
    quoted = english_list([f'"{name}"' for name in names])
    return f"• A default judge on its active evals: {quoted} {has} none set."


async def readiness_problem(
    kiln: KilnApi, *, host: str, project_id: str, task_id: str, task_name: str
) -> Readiness:
    """Every reason the task can't be tuned, as one problem with one action
    per page that fixes a reason."""
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
    measurable = measurable_evals(evals, specs)
    warnings = [_load_error_warning(load_errors)] if load_errors else []

    lines: list[str] = []
    actions: list[Action] = []
    if not run_configs:
        lines.append("• A run config: the model and prompt the tuner starts from.")
        actions.append(
            Action(
                "c",
                "Open Run configs in Kiln",
                ActionKind.OPEN,
                url=kiln_urls.run_configs(host, project_id, task_id),
            )
        )
    if not measurable:
        active = active_eval_rows(evals, specs)
        if active:
            names = [str(row.get("name") or row["id"]) for row in active]
            lines.append(_no_judge_line(names))
        else:
            lines.append(
                "• An eval it can measure: an active eval with a default judge set."
            )
        actions.append(
            Action(
                "e",
                "Open Evals in Kiln",
                ActionKind.OPEN,
                url=kiln_urls.evals(host, project_id, task_id),
            )
        )
    problem = (
        Problem(
            f"{task_name} isn't ready to tune yet",
            (*WHY, *(f"  {line}" for line in lines)),
            actions=tuple(actions),
            retry_label="Check again (I've fixed it in Kiln)",
            warnings=tuple(warnings),
        )
        if lines
        else None
    )
    return Readiness(problem, len(run_configs), len(measurable), warnings)
