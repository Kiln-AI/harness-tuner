"""The readiness check: the task has a run config to start from, and an eval
the loop can measure.

Both rules are the loop's own: a run config is a row with an ID, as setup
seeds the baseline from, and an eval is measurable by `measurable_evals`,
which the loop measures with, when Kiln can load its default config
(`read_eval_config`). Splits are not checked here; setup's split audit keeps
its own message.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

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
    read_eval_configs,
    unloadable_eval_lines,
)

WHY = (
    "The tuner starts from one of the task's run configs and measures every change",
    "with its evals. This task is missing:",
)


@dataclass(frozen=True)
class Readiness:
    """The check's outcome: the problem, if any, and the warnings to print
    whatever the outcome; a problem carries them too, for after its block.
    The counts are for the step line: `left_out` counts the evals Kiln
    can't load, which `measurable` leaves out."""

    problem: Problem | None
    run_configs: int
    measurable: int
    warnings: list[str] = field(default_factory=list)
    left_out: int = 0

    @property
    def step_text(self) -> str:
        text = (
            f"{plural(self.run_configs, 'run config')}"
            f" · {plural(self.measurable, 'eval')} the tuner can measure"
        )
        return f"{text} ({self.left_out} left out)" if self.left_out else text


def _load_error_warning(count: int) -> str:
    evals = plural(count, "eval")
    them = "it" if count == 1 else "them"
    return f"Kiln couldn't load {evals} of this task, so the run can't measure {them}."


def _eval_link(
    host: str, project_id: str, task_id: str, row: dict[str, Any], specs: list[Any]
) -> str:
    """The eval's own page when its spec is known, else the evals list."""
    spec_id = next(
        (
            str(spec["id"])
            for spec in specs
            if isinstance(spec, dict)
            and spec.get("id")
            and str(spec.get("eval_id")) == str(row["id"])
        ),
        None,
    )
    if spec_id is None:
        return kiln_urls.evals(host, project_id, task_id)
    return kiln_urls.eval_page(host, project_id, task_id, spec_id, str(row["id"]))


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
    pairs = measurable_evals(evals, specs)
    measurable: list[tuple[dict[str, Any], str]] = []
    unloadable: list[tuple[dict[str, Any], str]] = []  # (eval row, Kiln's reason)
    reads = await read_eval_configs(kiln, base, pairs)
    for pair, read in zip(pairs, reads, strict=True):
        if read.unloadable is None:
            measurable.append(pair)
        else:
            unloadable.append((pair[0], read.unloadable))
    warnings = [_load_error_warning(load_errors)] if load_errors else []
    warnings += [
        "\n".join(
            unloadable_eval_lines(
                str(row.get("name") or row["id"]),
                reason,
                _eval_link(host, project_id, task_id, row, specs),
            )
        )
        for row, reason in unloadable
    ]

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
        if unloadable:
            lines.append(
                "• An eval it can measure: Kiln can't load any of this task's"
                " evals (see the warnings)."
            )
        elif active := active_eval_rows(evals, specs):
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
    return Readiness(
        problem, len(run_configs), len(measurable), warnings, left_out=len(unloadable)
    )
