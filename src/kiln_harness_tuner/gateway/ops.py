"""The gateway's operations.

Loop agents never get a generic Kiln API tool. They get these operations,
each a typed request, a typed answer, and loop code in between that knows
the splits:

- a READ builds its answer only from records whose item is in the run's
  train set (`trainset`), or that this run created on a train item; every
  other record is dropped, and a direct read of one is refused;
- every answer is re-built into the gateway's own models (`models.project`),
  field by field;
- a WRITE is validated before Kiln sees it: run configs against the model
  policy, drives and eval jobs on train items only, memory in the two
  scopes, new evals with splits the gateway assigns.

`ROLE_OPS` names the operations each agent role gets; a role's session is
served those and no others. There is no operation for settings, providers,
deletes of evals or baselines, split edits, or held-out items.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from kiln_harness_tuner.gateway import models as m
from kiln_harness_tuner.gateway.trace_files import TraceWriter, summary_line, traces_dir
from kiln_harness_tuner.gateway.trainset import TrainSet, held_out_by_name
from kiln_harness_tuner.job_drives import EvalJobDriver
from kiln_harness_tuner.model_policy import ModelPolicy, allowed_models

Call = Callable[..., Awaitable[Any]]

PROJECT_SCOPE = "project"
VAL_MEMORY_TAG = "val"
GATEWAY_RUN_TAG = "kiln_harness_tuner_gateway"
CREATED_FILE = "gateway_created.jsonl"
# One line per memory write an agent makes, for the loop to forward into
# the wave log (`kiln_harness_tuner.memory_feed`).
MEMORY_FILE = "gateway_memory.jsonl"
VAL_SUMMARY_FILE = "val_summary.json"
VAL_READERS = frozenset({"promote_best", "report_writer"})
RC_PROP_KEYS = (
    "type",
    "model_name",
    "model_provider_name",
    "thinking_level",
    "prompt_id",
    "structured_output_mode",
    "temperature",
    "top_p",
    "tools_config",
    "skills_config",
    "input_transform",
)


class GatewayError(RuntimeError):
    """A refused or failed operation; the message is the agent's answer."""


NOT_TRAIN = (
    "not available: this record is not provably a train record. The gateway"
    " shows only train items and what this run created on them."
)
# The largest expected output `get_train_expected` returns whole, as JSON
# text. A larger one comes back clipped and says so; the whole of it is also
# written to a file (`reference_path`).
# 12,000 and not 20,000: a clipped reference is a JSON string inside JSON,
# and its escapes can double its length. Two of them then still fit in one
# answer under ANSWER_MAX_CHARS.
EXPECTED_MAX_CHARS = 12_000
# The largest answer any operation gives, as JSON text.
# Claude Code refuses an MCP answer over about 25,000 tokens: it saves it to
# a file under ~/.claude/projects, which the read fence denies, so the agent
# sees nothing. JSON text runs at 3 to 4 characters a token, but dense or
# escaped JSON can fall to about 2. At 40,000 characters an answer stays
# under 20,000 tokens even then. An answer over the cap comes back as its
# first part and a continuation (`AnswerPart`, `get_answer_part`).
ANSWER_MAX_CHARS = 40_000
# How much of a train result's output an answer shows; the trace file has
# all of it.
OUTPUT_PREVIEW_CHARS = 300
# How many over-cap answers a gateway process keeps for `get_answer_part`.
KEPT_ANSWERS = 16


@dataclass
class State:
    project_id: str
    task_id: str
    role: str
    train: TrainSet
    run_dir: Path | None = None
    project_path: Path | None = None
    policy: ModelPolicy | None = None
    # The eval jobs this process runs at once (`--max-concurrent-evals`);
    # None = no cap.
    max_concurrent_evals: int | None = None
    # records this run created: id -> (kind, split, eval id)
    created: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    # Where trace files go (`--traces-dir`); None = the run's
    # `scratch/traces/` (`trace_files.traces_dir`).
    traces_dir: Path | None = None

    @property
    def trace_root(self) -> Path | None:
        if self.traces_dir is not None:
            return self.traces_dir
        return traces_dir(self.run_dir) if self.run_dir is not None else None

    @property
    def base(self) -> str:
        return f"/api/projects/{self.project_id}/tasks/{self.task_id}"

    @property
    def project(self) -> str:
        return f"/api/projects/{self.project_id}"

    @property
    def val_reader(self) -> bool:
        return self.role in VAL_READERS

    # ---- the created-record registry (shared by every session of a run)

    def _registry(self) -> Path | None:
        return self.run_dir / CREATED_FILE if self.run_dir is not None else None

    def load_created(self) -> None:
        path = self._registry()
        if path is None or not path.exists():
            return
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("id"):
                self.created[str(row["id"])] = (
                    str(row.get("kind")),
                    str(row.get("split")),
                    str(row.get("eval_id") or ""),
                )

    def record(self, record_id: str, kind: str, split: str, eval_id: str = "") -> None:
        self.created[record_id] = (kind, split, eval_id)
        path = self._registry()
        if path is None:
            return
        line = json.dumps(
            {"id": record_id, "kind": kind, "split": split, "eval_id": eval_id}
        )
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (line + "\n").encode())
        finally:
            os.close(fd)

    def record_memory(self, op: str, view: m.MemoryView) -> None:
        """Append one line for a memory write this session made: the
        record's id, scope, tags and overview, the op and this role. Not the
        content. One `os.write` on an `O_APPEND` file, as `record` does, so
        the lines of concurrent sessions do not mix. The record is written
        by then: a failed append loses only the line, never the write."""
        if self.run_dir is None:
            return
        line = json.dumps(
            {
                "ts": datetime.now().isoformat(),
                "op": op,
                "id": view.id,
                "role": self.role,
                "scope": view.scope,
                "tags": list(view.tags),
                "overview": view.overview,
            },
            ensure_ascii=False,
        )
        try:
            fd = os.open(
                self.run_dir / MEMORY_FILE,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                0o600,
            )
            try:
                os.write(fd, (line + "\n").encode())
            finally:
                os.close(fd)
        except OSError:
            pass

    def created_train(
        self, kind: str | None = None, eval_id: str | None = None
    ) -> set[str]:
        return {
            i
            for i, (k, split, e) in self.created.items()
            if split == "train"
            and (kind is None or k == kind)
            and (eval_id is None or e == eval_id)
        }

    def train_items(self, eval_id: str | None = None) -> set[str]:
        """Train item ids: of one eval (its start-up train items, or the
        train items this run created for a new eval), or of all evals."""
        if eval_id is None:
            return set(self.train.ids) | self.created_train("eval_item")
        return set(self.train.per_eval.get(eval_id, frozenset())) | self.created_train(
            "eval_item", eval_id
        )


def _fit(
    page: BaseModel, items: list[Any], offset: int, total: int
) -> tuple[list[Any], int | None]:
    """The first of `items` that fit in one answer with `page` under
    ANSWER_MAX_CHARS, at least one, and the offset of the next page (None
    when this page reaches `total`). A single item over the cap still goes
    alone; the answer cap in `dispatch` then splits it."""
    used = len(json.dumps(page.model_dump(mode="json"))) + 200
    kept: list[Any] = []
    for item in items:
        size = len(json.dumps(item.model_dump(mode="json"))) + 2
        if kept and used + size > ANSWER_MAX_CHARS:
            break
        kept.append(item)
        used += size
    end = offset + len(kept)
    return kept, end if end < total else None


def _item_of(row: dict[str, Any]) -> str | None:
    value = row.get("dataset_id") or row.get("eval_input_id")
    return str(value) if value else None


def _text(value: Any, limit: int = 2000) -> str | None:
    return None if value is None else str(value)[:limit]


def _flatten(row: Any) -> dict[str, Any]:
    if isinstance(row, dict) and isinstance(row.get("eval_run"), dict):
        flat = dict(row["eval_run"])
        flat.update({k: v for k, v in row.items() if k != "eval_run"})
        return flat
    return row if isinstance(row, dict) else {}


def _rows(response: Any, key: str | None = None) -> list[dict[str, Any]]:
    if isinstance(response, dict) and key is not None:
        response = response.get(key)
    return [r for r in response or [] if isinstance(r, dict)]


def _tags_ok(tags: list[str], state: State) -> list[str]:
    for tag in tags:
        if held_out_by_name(tag) or tag in state.train.heldout_tags:
            raise GatewayError(f"tag {tag!r} names a held-out split; use another")
    return tags


def _scope_ok(scope: str, state: State) -> None:
    if scope not in (PROJECT_SCOPE, f"task::{state.task_id}"):
        raise GatewayError(
            f"scope must be {PROJECT_SCOPE!r} or 'task::{state.task_id}', not {scope!r}"
        )


def _memory_hidden(row: dict[str, Any], state: State) -> bool:
    return not state.val_reader and VAL_MEMORY_TAG in (row.get("tags") or [])


def _usage(src: Any) -> m.Usage | None:
    return m.project(m.Usage, src) if isinstance(src, dict) else None


def _output_text(run: dict[str, Any]) -> str | None:
    out = run.get("output")
    out = out.get("output") if isinstance(out, dict) else out
    return out if isinstance(out, str) else None


def _rc_view(row: dict[str, Any]) -> m.RunConfigView:
    props = row.get("run_config_properties") or {}
    props = props if isinstance(props, dict) else {}
    tools = (
        (props.get("tools_config") or {}).get("tools")
        if isinstance(props.get("tools_config"), dict)
        else None
    )
    prompt = row.get("prompt")
    return m.project(
        m.RunConfigView,
        {**props, **row},
        tools=[str(t) for t in tools or []],
        run_config_properties={k: props[k] for k in RC_PROP_KEYS if k in props},
        prompt=m.project(m.RunConfigPrompt, prompt)
        if isinstance(prompt, dict)
        else None,
    )


class Gateway:
    def __init__(self, call: Call, state: State):
        self.call = call
        self.s = state
        self._job_driver: EvalJobDriver | None = None
        # Over-cap answers this process gave, for `get_answer_part`.
        self._answers: OrderedDict[str, str] = OrderedDict()

    def _writer(self) -> TraceWriter | None:
        root = self.s.trace_root
        return TraceWriter(root) if root is not None else None

    # ------------------------------------------------------------ the answer cap

    def capped(self, data: Any) -> Any:
        """`data`, or — when its JSON text is over ANSWER_MAX_CHARS — its
        first part and a continuation. The whole text is kept in this
        process, so a continuation never runs the operation again (a write
        is never repeated to read the rest of its answer)."""
        text = json.dumps(data)
        if len(text) <= ANSWER_MAX_CHARS:
            return data
        token = secrets.token_hex(6)
        self._answers[token] = text
        while len(self._answers) > KEPT_ANSWERS:
            self._answers.popitem(last=False)
        return self._part(token, text, 0).model_dump(mode="json")

    @staticmethod
    def _part(token: str, text: str, offset: int) -> m.AnswerPart:
        size = min(len(text) - offset, ANSWER_MAX_CHARS - 1000)
        while True:
            end = offset + size
            part = m.AnswerPart(
                continuation=token,
                answer_chars=len(text),
                offset=offset,
                next_offset=end if end < len(text) else None,
                part=text[offset:end],
                note=(
                    "The answer was over the gateway's answer cap. `part` is a"
                    " piece of its JSON text. Call get_answer_part with this"
                    " continuation and offset=next_offset for the next piece."
                    " For a large result, prefer a narrower request (a page,"
                    " fewer ids)."
                ),
            )
            length = len(json.dumps(part.model_dump(mode="json")))
            if length <= ANSWER_MAX_CHARS or size <= 1:
                return part
            # Escapes made the piece longer than its text: cut it.
            size = max(1, size * (ANSWER_MAX_CHARS - 1000) // length)

    async def get_answer_part(self, req: m.AnswerPartRequest) -> m.AnswerPart:
        text = self._answers.get(req.continuation)
        if text is None:
            raise GatewayError(
                f"no kept answer {req.continuation}: this session's gateway keeps"
                f" the last {KEPT_ANSWERS} over-cap answers only."
            )
        if req.offset >= len(text):
            raise GatewayError(
                f"offset {req.offset} is past the end of the answer"
                f" ({len(text)} characters)"
            )
        return self._part(req.continuation, text, req.offset)

    def _driver(self) -> EvalJobDriver:
        """The drives through eval jobs (`kiln_harness_tuner.job_drives`), made once
        per process. Its jobs share one cap, `--max-concurrent-evals`."""
        if self._job_driver is None:
            cap = self.s.max_concurrent_evals
            semaphore = asyncio.Semaphore(cap) if cap else None
            self._job_driver = EvalJobDriver(
                self.call,
                self.s.project_id,
                self.s.task_id,
                slot=(lambda: semaphore) if semaphore is not None else None,  # type: ignore[arg-type,return-value]
            )
        return self._job_driver

    def _train_eval_of(self, item_id: str) -> str | None:
        """The first eval (by id) whose train split holds the item: a
        start-up train item, or an eval item this run created."""
        evals = sorted(e for e, ids in self.s.train.per_eval.items() if item_id in ids)
        if not evals:
            evals = sorted(
                e
                for i, (kind, split, e) in self.s.created.items()
                if i == item_id and kind == "eval_item" and split == "train" and e
            )
        return evals[0] if evals else None

    # ------------------------------------------------------------ reads

    async def ping(self, req: m.NoArgs) -> m.Pong:
        await self.call("GET", "/ping")
        return m.Pong(ok=True)

    async def get_task(self, req: m.NoArgs) -> m.TaskView:
        task = await self.call("GET", self.s.base) or {}
        reqs = [m.project(m.Requirement, r) for r in _rows(task.get("requirements"))]
        return m.project(m.TaskView, task, requirements=reqs)

    async def _run_configs(self) -> list[dict[str, Any]]:
        return _rows(await self.call("GET", f"{self.s.base}/run_configs"))

    async def list_run_configs(self, req: m.NoArgs) -> list[m.RunConfigView]:
        return [_rc_view(r) for r in await self._run_configs()]

    async def get_run_config(self, req: m.ById) -> m.RunConfigView:
        for row in await self._run_configs():
            if str(row.get("id")) == req.id:
                return _rc_view(row)
        raise GatewayError(f"no run config {req.id}")

    async def list_prompts(self, req: m.NoArgs) -> list[m.PromptView]:
        response = await self.call("GET", f"{self.s.base}/prompts")
        return [m.project(m.PromptView, p) for p in _rows(response, "prompts")]

    async def get_prompt_text(self, req: m.ById) -> m.PromptText:
        return m.project(
            m.PromptText, await self.call("GET", f"{self.s.base}/gen_prompt/{req.id}")
        )

    async def list_available_models(self, req: m.NoArgs) -> list[m.ModelView]:
        available = await self.call("GET", "/api/available_models")
        available = available if isinstance(available, list) else []
        rows = allowed_models(self.s.policy, available)
        modes = {
            (p.get("provider_id"), d.get("id")): d.get("structured_output_mode")
            for p in _rows(available)
            for d in _rows(p.get("models"))
        }
        return [
            m.ModelView(
                provider=str(r.get("provider")),
                model=str(r.get("model")),
                name=r.get("name"),
                thinking_levels=[str(x) for x in r.get("thinking_levels") or []],
                structured_output_mode=mode
                if isinstance(
                    mode := modes.get((r.get("provider"), r.get("model"))), str
                )
                else None,
            )
            for r in rows
        ]

    def _eval_view(self, row: dict[str, Any]) -> m.EvalView:
        scores = [m.project(m.ScoreView, s) for s in _rows(row.get("output_scores"))]
        eval_id = str(row.get("id"))
        return m.project(
            m.EvalView,
            row,
            output_scores=scores,
            train_items=len(self.s.train_items(eval_id)),
        )

    async def list_evals(self, req: m.NoArgs) -> list[m.EvalView]:
        return [
            self._eval_view(r)
            # The live route wraps the list: {"evals": [...], "load_error_count": N};
            # older servers return the bare list. `_rows` reads both.
            for r in _rows(await self.call("GET", f"{self.s.base}/evals"), "evals")
        ]

    async def get_eval(self, req: m.ById) -> m.EvalView:
        return self._eval_view(
            await self.call("GET", f"{self.s.base}/evals/{req.id}") or {}
        )

    async def get_eval_config(self, req: m.EvalConfigRef) -> m.EvalConfigView:
        row = await self.call(
            "GET", f"{self.s.base}/evals/{req.eval_id}/eval_config/{req.eval_config_id}"
        )
        row = row if isinstance(row, dict) else {}
        props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
        return m.project(m.EvalConfigView, row, properties=dict(props))

    async def list_specs(self, req: m.NoArgs) -> list[m.SpecView]:
        return [
            m.project(m.SpecView, s)
            for s in _rows(await self.call("GET", f"{self.s.base}/specs"))
        ]

    async def get_spec(self, req: m.ById) -> m.SpecView:
        return m.project(
            m.SpecView, await self.call("GET", f"{self.s.base}/specs/{req.id}")
        )

    async def get_train_results(self, req: m.TrainResultsRequest) -> m.TrainResults:
        """A run config's stored TRAIN results on one eval. Each train row's
        trace goes to a file under the run's `scratch/traces/`
        (`trace_files`), and the run config's `_stats` files are rebuilt. The
        answer carries the paths and a one-line summary per row, never a
        trace. A row that is not provably train is dropped before anything
        is written: no val or test record reaches a file. Rows come in pages
        (`offset`, `limit`), and a page also ends at the answer cap."""
        eval_def = await self.call("GET", f"{self.s.base}/evals/{req.eval_id}") or {}
        config_id = eval_def.get("current_config_id")
        if not config_id:
            raise GatewayError(f"eval {req.eval_id} has no current eval config")
        response = await self.call(
            "GET",
            f"{self.s.base}/evals/{req.eval_id}/eval_config/{config_id}"
            f"/run_config/{req.run_config_id}/results",
            query_params={"split": "train"},
        )
        train = self.s.train_items(req.eval_id)
        wanted = set(req.item_ids) if req.item_ids else None
        writer = self._writer()
        rows: list[m.TrainResultRow] = []
        dropped = 0
        for raw in (
            _rows(response, "results")
            if isinstance(response, dict)
            else _rows(response)
        ):
            row = _flatten(raw)
            item = _item_of(row)
            if item is None or item not in train:
                dropped += 1
                continue
            if wanted is not None and item not in wanted:
                continue
            rows.append(self._result_row(req, str(config_id), item, row, writer))
        stats_path = None
        if writer is not None and rows:
            stats_path = str(writer.write_stats(req.run_config_id)[1])
        page = m.TrainResults(
            eval_id=req.eval_id,
            eval_config_id=str(config_id),
            run_config_id=req.run_config_id,
            dropped=dropped,
            total=len(rows),
            offset=req.offset,
            stats_path=stats_path,
            traces_dir=str(writer.root) if writer is not None else None,
        )
        page.rows, page.next_offset = _fit(
            page, rows[req.offset : req.offset + req.limit], req.offset, len(rows)
        )
        return page

    def _result_row(
        self,
        req: m.TrainResultsRequest,
        config_id: str,
        item: str,
        row: dict[str, Any],
        writer: TraceWriter | None,
    ) -> m.TrainResultRow:
        usage = (
            row.get("task_run_usage")
            if isinstance(row.get("task_run_usage"), dict)
            else {}
        )
        raw_scores = row.get("scores") if isinstance(row.get("scores"), dict) else {}
        scores = {
            str(k): (float(v) if isinstance(v, (int, float)) else None)
            for k, v in raw_scores.items()
        }
        trace_path = summary = None
        if writer is not None:
            path, stats = writer.write_result(
                run_config_id=req.run_config_id,
                eval_id=req.eval_id,
                eval_config_id=config_id,
                item_id=item,
                row=row,
                scores=scores,
            )
            trace_path, summary = str(path), summary_line(stats)
        output = row.get("output")
        return m.project(
            m.TrainResultRow,
            row,
            item_id=item,
            eval_run_id=str(row["id"]) if row.get("id") else None,
            scores=scores,
            output_preview=output[:OUTPUT_PREVIEW_CHARS]
            if isinstance(output, str)
            else None,
            task_cost_usd=usage.get("cost"),
            latency_ms=usage.get("total_llm_latency_ms"),
            skipped_reason=_text(row.get("skipped_reason")),
            skipped_detail=_text(row.get("skipped_detail")),
            trace_path=trace_path,
            trace_summary=summary,
        )

    def _run_train_item(self, run: dict[str, Any]) -> str | None:
        """The train item a task run ran on, or None when not provable."""
        run_id = str(run.get("id"))
        source = run.get("eval_source")
        if (
            isinstance(source, dict)
            and str(source.get("source_id")) in self.s.train_items()
        ):
            return str(source["source_id"])
        created = self.s.created.get(run_id)
        if created and created[1] == "train":
            return created[2] or run_id
        if run_id in self.s.train.task_runs:
            return run_id
        return None

    async def get_task_run(self, req: m.ById) -> m.TaskRunView:
        self.s.load_created()
        # Decide from what is known BEFORE fetching when possible: an id
        # this run never saw and that is no dataset item still has to prove
        # itself by its link to a train input, read from the record.
        run = await self.call("GET", f"{self.s.base}/runs/{req.id}")
        run = run if isinstance(run, dict) else {}
        item = self._run_train_item({**run, "id": req.id})
        if item is None or str(run.get("id", req.id)) != req.id:
            raise GatewayError(NOT_TRAIN)
        return self._run_view(run, item)

    def _run_view(self, run: dict[str, Any], item: str | None) -> m.TaskRunView:
        out = run.get("output") if isinstance(run.get("output"), dict) else {}
        props = (
            (out.get("source") or {}).get("properties")
            if isinstance(out.get("source"), dict)
            else {}
        )
        props = props if isinstance(props, dict) else {}
        # The trace goes to a file; the run is provably train
        # here (`get_task_run` checked it, `run_task` drove a train item).
        trace_path = summary = None
        writer = self._writer()
        if writer is not None and run.get("id"):
            path, stats = writer.write_task_run(run=run, item_id=item)
            trace_path, summary = str(path), summary_line(stats)
        return m.project(
            m.TaskRunView,
            run,
            item_id=item,
            input=run.get("input") if isinstance(run.get("input"), str) else None,
            output=_output_text(run),
            trace_path=trace_path,
            trace_summary=summary,
            usage=_usage(run.get("usage")),
            tags=[str(t) for t in run.get("tags") or []],
            model_name=props.get("model_name"),
            model_provider=props.get("model_provider"),
        )

    def _inputs_on_disk(self) -> dict[str, Any]:
        """Train eval inputs from the project on disk: id -> TrainInput."""
        from kiln_harness_tuner.probes import input_from_doc
        from kiln_harness_tuner.quarantine import _children, task_dirs

        if self.s.project_path is None:
            return {}
        found: dict[str, Any] = {}
        train = self.s.train_items()
        for task_dir in task_dirs(self.s.project_path, self.s.task_id):
            for _p, doc in _children(task_dir / "eval_inputs", "eval_input.kiln"):
                item_id = str(doc.get("id"))
                if item_id in train and (inp := input_from_doc(doc)) is not None:
                    found[item_id] = inp
        return found

    def _train_docs_on_disk(self) -> dict[str, dict[str, Any]]:
        """Train eval-input records from the project on disk: id -> record.
        Every input shape (single-turn and multi-turn), train ids only."""
        from kiln_harness_tuner.quarantine import _children, task_dirs

        if self.s.project_path is None:
            return {}
        train = self.s.train_items()
        found: dict[str, dict[str, Any]] = {}
        for task_dir in task_dirs(self.s.project_path, self.s.task_id):
            for _p, doc in _children(task_dir / "eval_inputs", "eval_input.kiln"):
                item_id = str(doc.get("id"))
                if item_id in train:
                    found[item_id] = doc
        return found

    async def get_train_expected(
        self, req: m.TrainExpectedRequest
    ) -> m.TrainExpectedPage:
        """The expected outputs of TRAIN items. Train labels are train
        data: the val gate and the untouched test split are what
        keep the loop honest, not hiding them. An item that is not provably
        train is refused by name, never skipped in silence.

        One page per call, `offset` and `limit` over the train items (or over
        `item_ids`), and a page also ends at the answer cap.
        A reference over EXPECTED_MAX_CHARS is clipped in the answer and
        written whole to `traces/_expected/<item>.json`."""
        docs = self._train_docs_on_disk()
        train = self.s.train_items(req.eval_id) & set(docs)
        if req.item_ids is not None:
            refused = [i for i in req.item_ids if i not in train]
            if refused:
                raise GatewayError(f"{NOT_TRAIN} Refused: {', '.join(refused[:10])}.")
            every = list(dict.fromkeys(req.item_ids))
        else:
            every = sorted(train)
        writer = self._writer()
        out: list[m.TrainExpected] = []
        for i in every[req.offset : req.offset + req.limit]:
            doc = docs[i]
            reference = doc.get("reference")
            text = json.dumps(reference, ensure_ascii=False, default=str)
            clipped = len(text) > EXPECTED_MAX_CHARS
            path = (
                writer.write_expected(i, reference)
                if clipped and writer is not None
                else None
            )
            out.append(
                m.TrainExpected(
                    id=i,
                    eval_ids=sorted(
                        e for e, v in self.s.train.per_eval.items() if i in v
                    ),
                    reference=text[:EXPECTED_MAX_CHARS] if clipped else reference,
                    tags=[
                        str(t)
                        for t in doc.get("tags") or []
                        if not held_out_by_name(t)
                        and str(t) not in self.s.train.heldout_tags
                    ],
                    clipped=clipped,
                    reference_path=str(path) if path is not None else None,
                )
            )
        page = m.TrainExpectedPage(total=len(every), offset=req.offset)
        page.items, page.next_offset = _fit(page, out, req.offset, len(every))
        return page

    async def list_train_items(self, req: m.TrainItemsRequest) -> list[m.TrainItem]:
        inputs = self._inputs_on_disk()
        ids = sorted(self.s.train_items(req.eval_id) & set(inputs))
        by_eval = {
            i: sorted(e for e, v in self.s.train.per_eval.items() if i in v)
            for i in ids
        }
        return [
            m.TrainItem(
                id=i,
                eval_ids=by_eval[i],
                plaintext_input=inputs[i].plaintext
                if inputs[i].structured is None
                else None,
                structured_input=inputs[i].structured,
                multi_turn=inputs[i].multi_turn,
            )
            for i in ids[req.offset : req.offset + req.limit]
        ]

    # ------------------------------------------------------------ memory

    async def memory_list(self, req: m.MemoryListRequest) -> m.MemoryPage:
        _scope_ok(req.scope, self.s)
        if VAL_MEMORY_TAG in req.tags and not self.s.val_reader:
            raise GatewayError("the `val` memory record is not available to this role")
        query: dict[str, Any] = {
            "scope": req.scope,
            "limit": req.limit,
            "offset": req.offset,
        }
        if req.tags:
            query["tags"] = list(req.tags)
        if req.content_match:
            query["content_match"] = req.content_match
        page = (
            await self.call("GET", f"{self.s.project}/memories", query_params=query)
            or {}
        )
        rows = [r for r in _rows(page, "listings") if not _memory_hidden(r, self.s)]
        return m.MemoryPage(
            listings=[m.project(m.MemoryView, r) for r in rows],
            matched=page.get("matched"),
            remaining=page.get("remaining"),
        )

    async def memory_get(self, req: m.MemoryIdsRequest) -> list[m.MemoryView]:
        rows = await self.call(
            "GET",
            f"{self.s.project}/memories/by_ids",
            query_params={"ids": list(req.ids)},
        )
        scopes = (PROJECT_SCOPE, f"task::{self.s.task_id}")
        return [
            m.project(m.MemoryView, r)
            for r in _rows(rows)
            if r.get("scope") in scopes and not _memory_hidden(r, self.s)
        ]

    async def memory_summary(self, req: m.MemorySummaryRequest) -> m.MemorySummary:
        scope = req.scope or f"task::{self.s.task_id}"
        _scope_ok(scope, self.s)
        return m.project(
            m.MemorySummary,
            await self.call(
                "GET",
                f"{self.s.project}/memories/summary",
                query_params={"scope": scope},
            ),
        )

    async def memory_create(self, req: m.MemoryCreate) -> m.MemoryView:
        _scope_ok(req.scope, self.s)
        if VAL_MEMORY_TAG in req.tags:
            raise GatewayError("the `val` tag is written by the loop's code only")
        row = await self.call(
            "POST", f"{self.s.project}/memories", body=req.model_dump(exclude_none=True)
        )
        view = m.project(m.MemoryView, row)
        self.s.record_memory("create", view)
        return view

    async def memory_update(self, req: m.MemoryUpdate) -> m.MemoryView:
        (current,) = await self.memory_get(m.MemoryIdsRequest(ids=[req.id])) or [None]
        if current is None:
            raise GatewayError(f"no memory {req.id} in this run's scopes")
        if req.tags is not None and VAL_MEMORY_TAG in req.tags:
            raise GatewayError("the `val` tag is written by the loop's code only")
        body = req.model_dump(exclude_none=True, exclude={"id"})
        view = m.project(
            m.MemoryView,
            await self.call("PATCH", f"{self.s.project}/memories/{req.id}", body=body),
        )
        self.s.record_memory("update", view)
        return view

    # ------------------------------------------------------------ artifacts

    async def list_code_tools(self, req: m.NoArgs) -> list[m.CodeToolView]:
        return [
            m.project(m.CodeToolView, r)
            for r in _rows(await self.call("GET", f"{self.s.project}/code_tools"))
        ]

    async def get_code_tool(self, req: m.ById) -> m.CodeToolView:
        return m.project(
            m.CodeToolView,
            await self.call("GET", f"{self.s.project}/code_tools/{req.id}"),
        )

    async def list_skills(self, req: m.NoArgs) -> list[m.SkillView]:
        return [
            m.project(m.SkillView, r)
            for r in _rows(await self.call("GET", f"{self.s.project}/skills"))
        ]

    async def get_skill(self, req: m.ById) -> m.SkillView:
        return m.project(
            m.SkillView, await self.call("GET", f"{self.s.project}/skills/{req.id}")
        )

    async def get_skill_content(self, req: m.ById) -> m.SkillContent:
        return m.project(
            m.SkillContent,
            await self.call("GET", f"{self.s.project}/skills/{req.id}/content"),
        )

    async def list_available_tools(self, req: m.NoArgs) -> list[m.ToolSetView]:
        response = await self.call(
            "GET",
            f"{self.s.project}/available_tools",
            query_params={"task_id": self.s.task_id},
        )
        return [
            m.project(
                m.ToolSetView,
                s,
                tools=[m.project(m.ToolView, t) for t in _rows(s.get("tools"))],
            )
            for s in _rows(response)
        ]

    async def get_tool_definition(self, req: m.ById) -> m.ToolDefinition:
        row = await self.call("GET", f"{self.s.base}/tools/{req.id}/definition")
        return m.ToolDefinition(definition=row if isinstance(row, dict) else {})

    # ------------------------------------------------------------ writes

    def _policy_check(self, props: dict[str, Any]) -> None:
        if props.get("type", "kiln_agent") != "kiln_agent":
            raise GatewayError("only kiln_agent run configs can be created here")
        if self.s.policy is not None:
            refusal = self.s.policy.refusal(
                props.get("model_provider_name"), props.get("model_name")
            )
            if refusal is not None:
                raise GatewayError(refusal)

    async def create_run_config(self, req: m.RunConfigCreate) -> m.RunConfigView:
        self._policy_check(req.run_config_properties)
        row = await self.call(
            "POST", f"{self.s.base}/run_configs", body=req.model_dump(exclude_none=True)
        )
        return _rc_view(row if isinstance(row, dict) else {})

    async def rename_run_config(self, req: m.RunConfigRename) -> m.RunConfigView:
        body = req.model_dump(exclude_none=True, exclude={"id"})
        row = await self.call("PATCH", f"{self.s.base}/run_configs/{req.id}", body=body)
        return _rc_view(row if isinstance(row, dict) else {})

    async def create_prompt(self, req: m.PromptCreate) -> m.PromptView:
        row = await self.call(
            "POST", f"{self.s.base}/prompts", body=req.model_dump(exclude_none=True)
        )
        return m.project(m.PromptView, row)

    async def run_task(self, req: m.RunTaskRequest) -> m.TaskRunView:
        """Drive a saved run config on ONE train item. The input comes from
        the item itself, never from the caller.

        A multi-turn task, a run config with a world tool, or an item with a
        `world_reset` is driven as a one-item TRAIN eval job
        (`kiln_harness_tuner.job_drives`): the run endpoint starts no world episode
        and no simulated user. That task run is the job's trace, so it
        carries no caller tags."""
        _tags_ok(req.tags, self.s)
        self.s.load_created()
        inputs = self._inputs_on_disk()
        if req.item_id not in inputs:
            raise GatewayError(
                f"{req.item_id} is not a train item this gateway can drive"
            )
        rc = next(
            (
                r
                for r in await self._run_configs()
                if str(r.get("id")) == req.run_config_id
            ),
            None,
        )
        if rc is None:
            raise GatewayError(f"no run config {req.run_config_id}")
        props = rc.get("run_config_properties") or {}
        self._policy_check(props)
        inp = inputs[req.item_id]
        driver = self._driver()
        if await driver.needed([props], [inp]):
            return await self._run_task_by_job(req, driver)
        body: dict[str, Any] = {
            "run_config_properties": props,
            "task_run_config_id": req.run_config_id,
            "tags": [GATEWAY_RUN_TAG, *req.tags],
        }
        if inp.structured is not None:
            body["structured_input"] = inp.structured
        else:
            body["plaintext_input"] = inp.plaintext or ""
        run = await self.call("POST", f"{self.s.base}/run", body=body)
        run = run if isinstance(run, dict) else {}
        if run.get("id"):
            self.s.record(str(run["id"]), "task_run", "train", req.item_id)
        return self._run_view(run, req.item_id)

    async def _run_task_by_job(
        self, req: m.RunTaskRequest, driver: EvalJobDriver
    ) -> m.TaskRunView:
        eval_id = self._train_eval_of(req.item_id)
        if eval_id is None:
            raise GatewayError(f"{req.item_id} is in no eval's train split")
        outcome = await driver.drive(req.run_config_id, req.item_id, eval_id, "train")
        if outcome.run is None:
            raise GatewayError(f"the drive of {req.item_id} failed: {outcome.error}")
        run = outcome.run
        if run.get("id"):
            self.s.record(str(run["id"]), "task_run", "train", req.item_id)
        return self._run_view(run, req.item_id)

    async def create_code_tool(self, req: m.CodeToolCreate) -> m.CodeToolView:
        row = await self.call(
            "POST",
            f"{self.s.project}/code_tools",
            body=req.model_dump(exclude_none=True),
        )
        return m.project(m.CodeToolView, row)

    async def archive_code_tool(self, req: m.Archive) -> m.CodeToolView:
        row = await self.call(
            "POST",
            f"{self.s.project}/code_tools/{req.id}/archive",
            body={"archived": req.archived},
        )
        return m.project(m.CodeToolView, row)

    async def test_code_tool(self, req: m.CodeToolTestRequest) -> m.CodeToolTest:
        row = await self.call(
            "POST",
            f"{self.s.project}/test_code_tool",
            body=req.model_dump(exclude_none=True),
        )
        row = row if isinstance(row, dict) else {}
        entries: list[m.ToolCallEntry] = []
        for entry in row.get("tool_call_log") or []:
            # Documented as a list of {value: [entry, …]}; seen flat too.
            inner = entry.get("value") if isinstance(entry, dict) else None
            for e in inner if isinstance(inner, list) else [entry]:
                if isinstance(e, dict):
                    entries.append(
                        m.project(
                            m.ToolCallEntry,
                            e,
                            arguments=e.get("arguments")
                            if isinstance(e.get("arguments"), dict)
                            else {},
                        )
                    )
        text = {
            k: None if row.get(k) is None else str(row.get(k))
            for k in ("result", "error", "traceback", "stdout", "stderr")
        }
        return m.CodeToolTest(
            **text,
            not_trusted=row.get("not_trusted") is True,
            tool_call_log=entries,
            duration_ms=row.get("duration_ms")
            if isinstance(row.get("duration_ms"), int)
            else None,
        )

    async def trust_code_tools(self, req: m.NoArgs) -> m.Pong:
        await self.call("POST", f"{self.s.project}/add_code_trust")
        return m.Pong(ok=True)

    async def archive_skill(self, req: m.Archive) -> m.SkillView:
        row = await self.call(
            "PATCH",
            f"{self.s.project}/skills/{req.id}",
            body={"is_archived": req.archived},
        )
        return m.project(m.SkillView, row)

    # ------------------------------------------------------------ eval build

    async def create_spec(self, req: m.SpecCreate) -> m.SpecView:
        row = await self.call(
            "POST", f"{self.s.base}/specs", body=req.model_dump(exclude_none=True)
        )
        return m.project(m.SpecView, row)

    def _own_eval(self, eval_id: str, what: str) -> str:
        """The split token of an eval THIS run's gateway created. Every other
        eval — the evals the loop measures with — is frozen: no items, no
        judge config, no change of its current config."""
        self.s.load_created()
        token = self.s.created.get(f"eval:{eval_id}")
        if token is None:
            raise GatewayError(
                f"eval {eval_id} was not created through this gateway in this"
                f" run; {what} only on an eval you created with create_eval"
            )
        return token[2]

    def _split_tag(self, eval_id: str, split: str) -> str:
        return f"kl_{self._own_eval(eval_id, 'items can be added')}_{split}"

    async def create_eval(self, req: m.EvalCreate) -> m.EvalView:
        """A new eval whose splits the GATEWAY assigns: `kl_<token>_test` as
        its eval set, `kl_<token>_train` as its train set. Its items are
        added with `add_eval_items`; only the train ones are ever readable."""
        token = secrets.token_hex(4)
        body = req.model_dump(exclude_none=True)
        body["eval_set_filter_id"] = f"tag::kl_{token}_test"
        row = await self.call("POST", f"{self.s.base}/create_evaluator", body=body)
        row = row if isinstance(row, dict) else {}
        eval_id = str(row.get("id") or "")
        if not eval_id:
            raise GatewayError("the eval was not created (no id returned)")
        await self.call(
            "PATCH",
            f"{self.s.base}/evals/{eval_id}",
            body={"train_set_filter_id": f"tag::kl_{token}_train"},
        )
        self.s.record(f"eval:{eval_id}", "eval", "meta", token)
        return self._eval_view(row)

    async def add_eval_items(self, req: m.EvalItemsCreate) -> list[m.Created]:
        self.s.load_created()
        tag = self._split_tag(req.eval_id, req.split)
        made: list[m.Created] = []
        for item in req.items:
            row = await self.call(
                "POST",
                f"{self.s.base}/runs",
                body={
                    "input": item.input,
                    "output": item.output,
                    "tags": [tag],
                    "model_name": req.model_name,
                    "model_provider": req.model_provider,
                    "adapter_name": "kiln_harness_tuner_gateway",
                },
            )
            item_id = str((row or {}).get("id") or "")
            if item_id:
                # val and test items are recorded so they are NEVER readable;
                # train items so they are.
                self.s.record(item_id, "eval_item", req.split, req.eval_id)
                made.append(m.Created(id=item_id, kind=f"{req.split} item"))
        return made

    async def create_eval_config(self, req: m.EvalConfigCreate) -> m.EvalConfigView:
        self._own_eval(req.eval_id, "a judge config can be created")
        body = req.model_dump(exclude_none=True, exclude={"eval_id"})
        row = await self.call(
            "POST", f"{self.s.base}/evals/{req.eval_id}/create_eval_config", body=body
        )
        props = row.get("properties") if isinstance(row, dict) else None
        return m.project(
            m.EvalConfigView, row, properties=props if isinstance(props, dict) else {}
        )

    async def set_current_eval_config(self, req: m.SetCurrentConfig) -> m.Pong:
        self._own_eval(req.eval_id, "the current config can be set")
        await self.call(
            "POST",
            f"{self.s.base}/evals/{req.eval_id}/set_current_eval_config/{req.eval_config_id}",
        )
        return m.Pong(ok=True)

    async def test_judge(self, req: m.JudgeTestRequest) -> m.JudgeTest:
        row = await self.call(
            "POST",
            f"{self.s.base}/evals/{req.eval_id}/test_v2_eval",
            body={
                "properties": req.properties,
                "eval_input": {
                    "final_message": req.final_message,
                    "task_input": req.task_input,
                },
            },
        )
        row = row if isinstance(row, dict) else {}
        scores = row.get("scores") if isinstance(row.get("scores"), dict) else {}
        return m.JudgeTest(
            scores={
                str(k): (float(v) if isinstance(v, (int, float)) else None)
                for k, v in (scores or {}).items()
            },
            skipped_reason=_text(row.get("skipped_reason")),
            skipped_detail=_text(row.get("skipped_detail")),
            score_range_errors=[str(e) for e in row.get("score_range_errors") or []],
        )

    async def run_train_eval(self, req: m.TrainEvalRequest) -> m.JobView:
        """An eval job on TRAIN items only: split=train, item_ids limited to
        the eval's train items (all of them when none are named)."""
        self.s.load_created()
        train = self.s.train_items(req.eval_id)
        items = list(req.item_ids) if req.item_ids else sorted(train)
        outside = sorted(set(items) - train)
        if outside:
            raise GatewayError(f"not train items of eval {req.eval_id}: {outside[:5]}")
        if not items:
            raise GatewayError(f"eval {req.eval_id} has no train item to run")
        eval_def = await self.call("GET", f"{self.s.base}/evals/{req.eval_id}") or {}
        config_id = eval_def.get("current_config_id")
        if not config_id:
            raise GatewayError(f"eval {req.eval_id} has no current eval config")
        job = await self.call(
            "POST",
            "/api/jobs/evals/run",
            body={
                "project_id": self.s.project_id,
                "task_id": self.s.task_id,
                "eval_id": req.eval_id,
                "eval_config_id": config_id,
                "run_config_id": req.run_config_id,
                "split": "train",
                "item_ids": items,
            },
        )
        job_id = str((job or {}).get("job_id") or (job or {}).get("id") or "")
        records = await self.call(
            "POST", "/api/jobs/wait", body={"ids": [job_id], "timeout": 600}
        )
        record = records[0] if isinstance(records, list) and records else {}
        progress = record.get("progress") or {}
        return m.JobView(
            job_id=job_id,
            status=record.get("status"),
            success=progress.get("success"),
            error=progress.get("error"),
            total=progress.get("total"),
            error_message=_text((record.get("error") or {}).get("error"))
            if isinstance(record.get("error"), dict)
            else None,
        )

    # ------------------------------------------------------------ val readers

    async def get_val_summary(self, req: m.NoArgs) -> m.ValSummary:
        path = self.s.run_dir / VAL_SUMMARY_FILE if self.s.run_dir else None
        # The run folder is prepared with an empty file (the read fence must
        # deny it from the first session on), so empty means no gate yet.
        text = path.read_text() if path is not None and path.exists() else ""
        if not text.strip():
            return m.ValSummary(note="no promotion gate has run yet")
        data = json.loads(text)
        from kiln_harness_tuner.schemas import CandidateValTable

        tables = [
            CandidateValTable.model_validate(t).model_dump(mode="json")
            for t in data.get("candidates", [])
        ]
        return m.ValSummary(candidates=tables, note=str(data.get("note") or ""))

    async def compute_statistics(self, req: m.StatisticsRequest) -> m.Statistics:
        row = await self.call(
            "POST", "/api/statistics", body=req.model_dump(exclude_none=True)
        )
        row = row if isinstance(row, dict) else {}

        def number(v: Any) -> bool:
            return v is None or isinstance(v, (int, float, bool, str))

        return m.Statistics(
            values={
                str(k): v
                for k, v in row.items()
                if number(v) or (isinstance(v, list) and all(number(x) for x in v))
            }
        )


# ---- which operations, for which role -------------------------------------


@dataclass(frozen=True)
class Op:
    name: str
    request: type[BaseModel]
    summary: str


def _ops(*specs: tuple[str, type[BaseModel], str]) -> dict[str, Op]:
    return {n: Op(n, r, s) for n, r, s in specs}


OPS = _ops(
    ("ping", m.NoArgs, "Check the Kiln server answers."),
    ("get_task", m.NoArgs, "The task: instruction, requirements, schemas."),
    ("list_run_configs", m.NoArgs, "Every run config of the task."),
    ("get_run_config", m.ById, "One run config."),
    ("list_prompts", m.NoArgs, "The task's saved prompts."),
    ("get_prompt_text", m.ById, "The prompt text a prompt id renders."),
    ("list_available_models", m.NoArgs, "The models the run's policy allows."),
    ("list_evals", m.NoArgs, "The task's evals, with their train item counts."),
    ("get_eval", m.ById, "One eval."),
    ("get_eval_config", m.EvalConfigRef, "One eval config (the judge)."),
    ("list_specs", m.NoArgs, "The task's specs."),
    ("get_spec", m.ById, "One spec."),
    (
        "get_train_results",
        m.TrainResultsRequest,
        "A run config's stored TRAIN results on one eval: per row the scores,"
        " cost and latency, and the path of the trace file (Grep and Read it).",
    ),
    (
        "get_task_run",
        m.ById,
        "One task run, when it is provably train; its trace is in a file.",
    ),
    ("list_train_items", m.TrainItemsRequest, "Train items, with their inputs."),
    (
        "get_train_expected",
        m.TrainExpectedRequest,
        "Train items' expected outputs (the eval input's reference) and tags,"
        " one page per call.",
    ),
    ("memory_list", m.MemoryListRequest, "List memory records in the run's scopes."),
    ("memory_get", m.MemoryIdsRequest, "Memory records by id."),
    ("memory_summary", m.MemorySummaryRequest, "Counts per scope."),
    ("memory_create", m.MemoryCreate, "Write a memory record."),
    ("memory_update", m.MemoryUpdate, "Update a memory record."),
    ("list_code_tools", m.NoArgs, "The project's code tools."),
    ("get_code_tool", m.ById, "One code tool, with its code."),
    ("list_skills", m.NoArgs, "The project's skills."),
    ("get_skill", m.ById, "One skill."),
    ("get_skill_content", m.ById, "One skill's SKILL.md."),
    ("list_available_tools", m.NoArgs, "The tools a run config can attach."),
    ("get_tool_definition", m.ById, "One tool's definition."),
    (
        "create_run_config",
        m.RunConfigCreate,
        "A new run config (model policy checked).",
    ),
    ("rename_run_config", m.RunConfigRename, "Rename a run config."),
    ("create_prompt", m.PromptCreate, "A new saved prompt."),
    ("run_task", m.RunTaskRequest, "Drive a run config on one train item."),
    ("create_code_tool", m.CodeToolCreate, "A new code tool."),
    ("archive_code_tool", m.Archive, "Archive a code tool."),
    ("test_code_tool", m.CodeToolTestRequest, "Dry-run code tool code."),
    ("trust_code_tools", m.NoArgs, "Trust the project's code tools to run."),
    ("archive_skill", m.Archive, "Archive a skill."),
    ("create_spec", m.SpecCreate, "A new spec."),
    ("create_eval", m.EvalCreate, "A new eval, splits assigned by the gateway."),
    (
        "add_eval_items",
        m.EvalItemsCreate,
        "Items for a new eval's train, val or test split.",
    ),
    ("create_eval_config", m.EvalConfigCreate, "A judge config for an eval."),
    ("set_current_eval_config", m.SetCurrentConfig, "Make a config the eval's judge."),
    ("test_judge", m.JudgeTestRequest, "Score one given output with a judge config."),
    ("run_train_eval", m.TrainEvalRequest, "An eval job on train items only."),
    ("get_val_summary", m.NoArgs, "The gate's code-assembled val tables."),
    (
        "compute_statistics",
        m.StatisticsRequest,
        "Run a statistics operation on numbers.",
    ),
    (
        "get_answer_part",
        m.AnswerPartRequest,
        "The next piece of an answer that was over the gateway's answer cap.",
    ),
)

_TASK = (
    "get_task",
    "list_run_configs",
    "get_run_config",
    "list_prompts",
    "get_prompt_text",
)
_EVALS = ("list_evals", "get_eval", "get_eval_config", "list_specs", "get_spec")
_MEM_READ = ("memory_list", "memory_get", "memory_summary")
_MEM_WRITE = ("memory_create", "memory_update")
_ARTIFACTS = (
    "list_code_tools",
    "get_code_tool",
    "list_skills",
    "get_skill",
    "get_skill_content",
    "list_available_tools",
    "get_tool_definition",
)
_CODE_TOOLS = ("create_code_tool", "test_code_tool", "trust_code_tools")

ROLE_OPS: dict[str, tuple[str, ...]] = {
    "preflight": ("ping",),
    "describe_eval": ("get_task", *_EVALS),
    "batch_plan": (
        *_TASK,
        "list_available_models",
        *_EVALS,
        "get_train_results",
        "get_train_expected",
        *_ARTIFACTS,
        *_MEM_READ,
        *_MEM_WRITE,
    ),
    "create_experiment_rc": (
        *_TASK,
        "list_available_models",
        *_EVALS,
        "get_train_results",
        "get_task_run",
        "list_train_items",
        "get_train_expected",
        *_ARTIFACTS,
        *_MEM_READ,
        *_MEM_WRITE,
        "create_run_config",
        "rename_run_config",
        "create_prompt",
        "run_task",
        *_CODE_TOOLS,
        "archive_code_tool",
        "archive_skill",
    ),
    "probe": (
        *_TASK,
        "list_available_models",
        "get_task_run",
        "list_train_items",
        "get_train_expected",
        "list_code_tools",
        "list_skills",
        *_MEM_READ,
        "memory_create",
        "create_run_config",
        "run_task",
        *_CODE_TOOLS,
    ),
    "analyze_experiment_results": (
        *_TASK,
        *_EVALS,
        "get_train_results",
        "get_train_expected",
        "get_task_run",
        "get_code_tool",
        "get_skill",
        "get_skill_content",
        *_MEM_READ,
        *_MEM_WRITE,
    ),
    "promote_best": (
        "get_task",
        "list_run_configs",
        "list_evals",
        "get_eval",
        *_MEM_READ,
        "get_val_summary",
        "compute_statistics",
    ),
    "report_writer": (
        "get_task",
        "list_run_configs",
        "list_evals",
        *_MEM_READ,
        "get_val_summary",
    ),
    "eval_build": (
        "get_task",
        "list_run_configs",
        *_EVALS,
        "get_train_results",
        *_MEM_READ,
        *_MEM_WRITE,
        "create_spec",
        "create_eval",
        "add_eval_items",
        "create_eval_config",
        "set_current_eval_config",
        "test_judge",
        "run_train_eval",
        *_CODE_TOOLS,
    ),
}
# Every role with operations can read the rest of an over-cap answer.
# The preflight's one `ping` answer is tiny.
for _role, _role_ops in ROLE_OPS.items():
    if _role != "preflight":
        ROLE_OPS[_role] = (*_role_ops, "get_answer_part")

# The operations that write trace files, and the roles that call them: the
# dispatcher opens the trace folder to these roles only.
TRACE_OPS = frozenset(
    {"get_train_results", "get_task_run", "run_task", "get_train_expected"}
)
TRACE_READER_ROLES = frozenset(
    role for role, ops in ROLE_OPS.items() if TRACE_OPS & set(ops)
)

GATEWAY_SERVER = "kiln_harness_tuner_gateway"


def tool_name(op: str) -> str:
    """The name a Claude Code session sees for a gateway operation."""
    return f"mcp__{GATEWAY_SERVER}__{op}"


def role_tools(role: str) -> list[str]:
    return [tool_name(op) for op in ROLE_OPS.get(role, ())]


async def dispatch(
    gateway: Gateway, role: str, op: str, arguments: dict[str, Any]
) -> Any:
    """Run one operation for a role: unknown to the role = refused."""
    if op not in ROLE_OPS.get(role, ()):
        raise GatewayError(f"operation {op!r} is not available to the {role} role")
    request = OPS[op].request.model_validate(arguments)
    result = await getattr(gateway, op)(request)
    if isinstance(result, list):
        data: Any = [r.model_dump(mode="json") for r in result]
    else:
        data = result.model_dump(mode="json")
    # No answer is over the cap: Claude Code would put it in a
    # file the agent cannot read.
    return gateway.capped(data)
