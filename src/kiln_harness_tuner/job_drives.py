"""Drives through eval jobs.

The plain run endpoint (`POST …/tasks/{t}/run`) runs one input once. It
starts no OpenEnv episode and no simulated user. So it cannot drive:

- a multi-turn task (the task's `turn_mode` is `multiturn`);
- a run config that lists an OpenEnv tool (`kiln_tool::openenv::…`);
- an item whose input carries an `openenv_reset`.

There, the OpenEnv tools fail and the conversation does not start. An eval
job drives such an item correctly: it starts an episode for the item, runs
the synthetic user, and saves the trace as a task run with its `usage`.

`EvalJobDriver.drive` drives one (run config, item) as a one-item eval job
on the item's own eval and split:

- **Judge-free** (`judge: false`) when the server has it — Kiln detects it
  from the server's OpenAPI schema. The job drives the item again, scored
  or not, so the drive is of this window. Its result names the task run
  (`driven_runs`), and code reads that run with `GET …/runs/{id}`.
- **Judged**, on an older server, with one log line. The job stores a
  score, and code reads the usage, output and trace from the item's stored
  row. That row can come from an EARLIER drive: the server reuses a stored
  trace, and it skips an item that has a score. Two such rows are not from
  one window, so a timing comparison made from them is weaker.

The metric is the run's own usage: `usage.total_llm_latency_ms` and
`usage.cost` (for a multi-turn item, the whole conversation). The
synthetic user's spend and the judge's spend are provider spend too, so
they are returned as `overhead_cost`, never as the metric.

A one-item job keeps the paired design of the timing drives: per item the
arms still run back to back, in the order the caller rotates.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kiln_harness_tuner.kiln import KilnAPIError, KilnError

logger = logging.getLogger(__name__)

# A tool an OpenEnv server serves: `kiln_tool::openenv::<server id>::<tool>`.
# Kiln resolves it only while an episode of that server is running.
OPENENV_TOOL_PREFIX = "kiln_tool::openenv::"
MULTI_TURN_MODE = "multiturn"
# The longest one drive may take, end to end: a multi-turn conversation in a
# OpenEnv episode runs several model calls and several tool calls. The server's
# `POST /api/jobs/wait` caps one wait at an hour.
JOB_DRIVE_TIMEOUT_SECS = 1800.0
JOB_DRIVE_WAIT_MARGIN_SECS = 30.0
# The splits a drive may name. Never test.
DRIVE_SPLITS = ("train", "val")

Call = Callable[..., Awaitable[Any]]
Slot = Callable[[], contextlib.AbstractAsyncContextManager[None]]


def openenv_tool_parts(tool_id: str) -> tuple[str, str] | None:
    """(server id, tool name) of an OpenEnv tool id, or None for any other
    tool id. Exactly four `::` parts, as Kiln requires
    (`openenv_and_tool_name_from_id`)."""
    parts = tool_id.split("::")
    if not tool_id.startswith(OPENENV_TOOL_PREFIX) or len(parts) != 4:
        return None
    server_id, name = parts[2], parts[3]
    return (server_id, name) if server_id and name else None


def lists_openenv_tool(props: Mapping[str, Any] | None) -> bool:
    """Does this run config's tool list name an OpenEnv tool?"""
    tools_config = props.get("tools_config") if isinstance(props, Mapping) else None
    tools = tools_config.get("tools") if isinstance(tools_config, Mapping) else None
    return any(
        isinstance(t, str) and t.startswith(OPENENV_TOOL_PREFIX) for t in tools or []
    )


def doc_needs_eval_job(doc: Mapping[str, Any]) -> bool:
    """Does this eval input record need an eval job to drive it: a
    multi-turn input, or one with an `openenv_reset`?"""
    if doc.get("openenv_reset"):
        return True
    data = doc.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return False
    return isinstance(data, Mapping) and data.get("type") == "multi_turn_synthetic"


def eval_of_item(
    project_root: Path, task_id: str, item_id: str, split: str
) -> str | None:
    """The id of an eval whose `split` holds this eval input, or None. The
    first such eval by id, so the same item always goes to the same eval."""
    from kiln_harness_tuner.quarantine import membership, task_dirs

    found: list[str] = []
    for task_dir in task_dirs(project_root, task_id):
        member, _loaded = membership(task_dir)
        for eval_id, splits in member.by_eval.items():
            if ("eval_input", item_id) in splits.get(split, set()):
                found.append(eval_id)
    return min(found) if found else None


def run_from_result_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """A stored eval-run row (flat) in the shape `POST …/run` returns a
    task run: `usage`, `output.output`, `trace`."""
    trace = row.get("task_run_trace") or row.get("trace") or []
    if isinstance(trace, str):
        try:
            trace = json.loads(trace)
        except ValueError:
            trace = []
    output = row.get("output")
    return {
        "id": row.get("scored_run_id"),
        "usage": row.get("task_run_usage"),
        "output": output if isinstance(output, Mapping) else {"output": output},
        "trace": trace if isinstance(trace, list) else [],
        "intermediate_outputs": row.get("intermediate_outputs"),
    }


def _cost_in(usage: Any) -> float:
    value = usage.get("cost") if isinstance(usage, Mapping) else None
    return float(value) if isinstance(value, (int, float)) else 0.0


def error_text(exc: Exception) -> str:
    """One line for a failed Kiln call: the status and the server's
    message."""
    body = getattr(exc, "body", None)
    status = getattr(exc, "status_code", None)
    if isinstance(body, Mapping):
        text = str(body.get("message") or body.get("detail") or body)
    else:
        text = str(body if body is not None else exc)
    return f"HTTP {status}: {text}" if status else text


@dataclass
class EvalJobDrive:
    """What one eval-job drive returned.

    `run`: the task run, in the shape `POST …/run` returns, or None when
    the drive failed. `error`: why it failed — the job's own error text,
    which carries the provider's message. `judged`: a judged job drove it
    (an older server), so the run can be from an earlier window.
    `overhead_cost`: provider spend that is not the task call's — the
    synthetic user's and, on a judged job, the judge's."""

    run: dict[str, Any] | None
    error: str | None = None
    judged: bool = False
    overhead_cost: float = 0.0
    job_id: str | None = None


class EvalJobDriver:
    """Drives (run config, item) pairs as one-item eval jobs.

    One per Kiln client: the loop's code has one (`driver_for`), and each
    gateway process has one. `slot` is the eval-job concurrency slot
    (`--max-concurrent-evals`); every job holds it while it runs. The
    server checks (task turn mode, judge-free support, each eval's config)
    are read once and kept."""

    def __init__(
        self,
        call: Call,
        project_id: str,
        task_id: str,
        *,
        slot: Slot | None = None,
        log: Any = None,
        wait_timeout_secs: float = JOB_DRIVE_TIMEOUT_SECS,
    ):
        self._call = call
        self.project_id = project_id
        self.task_id = task_id
        self._slot = slot
        self._log = log
        self.wait_timeout_secs = wait_timeout_secs
        self._multi_turn: bool | None = None
        self._judge_free: bool | None = None
        self._config_ids: dict[str, str] = {}
        # Evals whose default config the server refused for a judge-free job
        # (a legacy judge): their drives use judged jobs.
        self._judged_evals: set[str] = set()

    @property
    def _base(self) -> str:
        return f"/api/projects/{self.project_id}/tasks/{self.task_id}"

    async def multi_turn(self) -> bool:
        """Is the task multi-turn? Read once from the task record."""
        if self._multi_turn is None:
            try:
                task = await self._call("GET", self._base)
            except KilnError:
                return False
            mode = task.get("turn_mode") if isinstance(task, Mapping) else None
            self._multi_turn = mode == MULTI_TURN_MODE
        return self._multi_turn

    async def needed(
        self, props: Iterable[Mapping[str, Any] | None], inputs: Iterable[Any]
    ) -> bool:
        """Must these drives go through eval jobs? Yes when the task is
        multi-turn, when one run config lists an OpenEnv tool, or when one
        input needs an eval job (`TrainInput.needs_eval_job`)."""
        if any(lists_openenv_tool(p) for p in props):
            return True
        if any(getattr(i, "needs_eval_job", False) for i in inputs):
            return True
        return await self.multi_turn()

    async def judge_free(self) -> bool:
        """Does the server run judge-free eval jobs? Its OpenAPI schema
        shows a `judge` field on `EvalJobParams` when it does. Read once; a
        server without it gets one log line."""
        if self._judge_free is None:
            try:
                schema = await self._call("GET", "/openapi.json")
            except KilnError:
                schema = None
            params = (
                ((schema or {}).get("components") or {}).get("schemas") or {}
            ).get("EvalJobParams") or {}
            self._judge_free = "judge" in (params.get("properties") or {})
            if not self._judge_free:
                self._event(
                    "eval_job_drives_judged",
                    note=(
                        "this Kiln server has no judge-free eval jobs; drives use"
                        " judged eval jobs. They pay for the judge, and they reuse"
                        " a stored trace when one exists, so a drive can be from an"
                        " earlier window."
                    ),
                )
                logger.warning(
                    "Kiln has no judge-free eval jobs: drives use judged jobs,"
                    " which can reuse an earlier drive's trace"
                )
        return self._judge_free

    def _event(self, kind: str, **fields: Any) -> None:
        if self._log is not None:
            self._log.event(kind, **fields)

    async def _config_id(self, eval_id: str) -> str:
        if eval_id not in self._config_ids:
            eval_def = await self._call("GET", f"{self._base}/evals/{eval_id}")
            config_id = (
                eval_def.get("current_config_id")
                if isinstance(eval_def, Mapping)
                else None
            )
            if not config_id:
                raise KilnError(f"eval {eval_id} has no default eval config")
            self._config_ids[eval_id] = str(config_id)
        return self._config_ids[eval_id]

    @contextlib.asynccontextmanager
    async def _held_slot(self):  # type: ignore[no-untyped-def]
        if self._slot is None:
            yield
            return
        async with self._slot():
            yield

    async def drive(
        self, rc_id: str, item_id: str, eval_id: str, split: str = "train"
    ) -> EvalJobDrive:
        """Drive `item_id` of `eval_id`'s `split` once with `rc_id`. Never
        raises for a failed drive: the result says why."""
        if split not in DRIVE_SPLITS:
            raise ValueError(f"a drive never names the {split!r} split")
        try:
            config_id = await self._config_id(eval_id)
            judge_free = await self.judge_free() and eval_id not in self._judged_evals
            async with self._held_slot():
                job_id, judge_free = await self._start(
                    eval_id, config_id, rc_id, item_id, split, judge_free
                )
                record = await self._wait(job_id)
        except KilnError as exc:
            return EvalJobDrive(run=None, error=error_text(exc), judged=False)
        if judge_free:
            outcome = await self._driven_run(record, item_id)
        else:
            outcome = await self._judged_run(eval_id, config_id, rc_id, split, item_id)
        outcome.job_id = job_id
        if outcome.run is None and outcome.error is None:
            outcome.error = await self._job_error(job_id, record, item_id)
        return outcome

    async def _start(
        self,
        eval_id: str,
        config_id: str,
        rc_id: str,
        item_id: str,
        split: str,
        judge_free: bool,
    ) -> tuple[str, bool]:
        body: dict[str, Any] = {
            "project_id": self.project_id,
            "task_id": self.task_id,
            "eval_id": eval_id,
            "eval_config_id": config_id,
            "run_config_id": rc_id,
            "split": split,
            "item_ids": [item_id],
        }
        if judge_free:
            try:
                # retries=False: a judge-free job is never deduplicated, so a
                # replayed request would drive the item a second time.
                created = await self._call(
                    "POST",
                    "/api/jobs/evals/run",
                    body={**body, "judge": False},
                    retries=False,
                )
                return str(created["job_id"]), True
            except KilnAPIError as exc:
                if exc.status_code != 400 or "V2 judge" not in error_text(exc):
                    raise
                # The eval's default judge is a legacy one: the server drives
                # only through a V2 judge. Judged jobs for this eval from now.
                self._judged_evals.add(eval_id)
                self._event(
                    "eval_job_drives_judged",
                    eval_id=eval_id,
                    note="the eval's default config is not a V2 judge, so its"
                    " drives use judged eval jobs",
                )
        created = await self._call(
            "POST", "/api/jobs/evals/run", body=body, retries=True
        )
        return str(created["job_id"]), False

    async def _wait(self, job_id: str) -> dict[str, Any]:
        records = await self._call(
            "POST",
            "/api/jobs/wait",
            body={"ids": [job_id], "timeout": self.wait_timeout_secs},
            timeout_secs=self.wait_timeout_secs + JOB_DRIVE_WAIT_MARGIN_SECS,
            retries=False,
        )
        record = records[0] if isinstance(records, list) and records else {}
        return record if isinstance(record, dict) else {}

    async def _driven_run(
        self, record: Mapping[str, Any], item_id: str
    ) -> EvalJobDrive:
        result = record.get("result")
        driven = result.get("driven_runs") if isinstance(result, Mapping) else None
        run_id = next(
            (
                str(d.get("task_run_id"))
                for d in driven or []
                if isinstance(d, Mapping)
                and str(d.get("item_id")) == item_id
                and d.get("task_run_id")
            ),
            None,
        )
        if run_id is None:
            return EvalJobDrive(run=None)
        try:
            run = await self._call("GET", f"{self._base}/runs/{run_id}")
        except KilnError as exc:
            return EvalJobDrive(run=None, error=error_text(exc))
        run = run if isinstance(run, dict) else {}
        return EvalJobDrive(
            run=run, overhead_cost=_cost_in(run.get("synthetic_user_usage"))
        )

    async def _judged_run(
        self, eval_id: str, config_id: str, rc_id: str, split: str, item_id: str
    ) -> EvalJobDrive:
        from kiln_harness_tuner.analysis import flatten_result_row

        try:
            response = await self._call(
                "GET",
                f"{self._base}/evals/{eval_id}/eval_config/{config_id}"
                f"/run_config/{rc_id}/results",
                query_params={"split": split},
            )
        except KilnError as exc:
            return EvalJobDrive(run=None, error=error_text(exc), judged=True)
        rows = response.get("results") if isinstance(response, dict) else response
        for raw in rows or []:
            row = flatten_result_row(raw)
            if not isinstance(row, dict):
                continue
            if str(row.get("eval_input_id") or row.get("dataset_id")) != item_id:
                continue
            return EvalJobDrive(
                run=run_from_result_row(row),
                judged=True,
                overhead_cost=_cost_in(row.get("eval_usage")),
            )
        return EvalJobDrive(run=None, judged=True)

    async def _job_error(
        self, job_id: str, record: Mapping[str, Any], item_id: str
    ) -> str:
        """The job's own error text for the item: it carries the provider's
        message, where `POST …/run` answers a generic 500."""
        messages: list[str] = []
        try:
            rows = await self._call("GET", f"/api/jobs/{job_id}/errors")
        except KilnError:
            rows = []
        for row in rows or []:
            if isinstance(row, Mapping) and row.get("error_message"):
                messages.append(str(row["error_message"]))
        job_error = record.get("error") if isinstance(record, Mapping) else None
        if isinstance(job_error, Mapping) and job_error.get("error"):
            messages.append(str(job_error["error"]))
        if messages:
            return "; ".join(messages)[:1000]
        status = record.get("status") if isinstance(record, Mapping) else None
        return f"eval job {job_id} ({status}) returned no drive for {item_id}"


def driver_for(ctx: Any) -> EvalJobDriver:
    """The loop's driver, made once per run and kept on the context. Its
    jobs take the analyzer's eval-job slot, so `--max-concurrent-evals`
    caps them with every other eval job of the run."""
    driver = getattr(ctx, "eval_job_driver", None)
    if driver is None:
        driver = EvalJobDriver(
            ctx.kiln.call,
            ctx.target.project_id,
            ctx.target.task_id,
            slot=ctx.analyzer.job_slot,
            log=ctx.log,
        )
        ctx.eval_job_driver = driver
    return driver


async def eval_of_input(ctx: Any, inp: Any, split: str) -> str | None:
    """The eval to drive `inp` through: the one its loader named, else the
    first eval whose `split` holds it on disk."""
    if getattr(inp, "eval_id", None):
        return str(inp.eval_id)
    return eval_of_item(
        Path(ctx.target.project_path), ctx.target.task_id, inp.id, split
    )
