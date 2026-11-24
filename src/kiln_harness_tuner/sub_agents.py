"""Sub-agents: other Kiln tasks that the subject task calls as tools.

A sub-agent is a Kiln task in the subject task's project. A tool server of
the type `kiln_task` pins that task and ONE of its run configs, and a run
config of the subject task attaches the server as the tool
`kiln_task::<tool_server_id>`. Each call of the tool is one new run of the
sub-agent's task: one turn, with no state from an earlier call.

This module is the one reader of that structure. The gateway and the loop's
own code both use it, so they agree on what a sub-agent is:

- `read_sub_agents`: the sub-agents a run config attaches, each with its
  task and the run config its tool pins. The model policy and the integrity
  checks read the sub-agent's model and its text from here.
- `sub_agent_calls`: the calls of sub-agent tools that a task run's trace
  shows. This is the evidence that the lever was applied.
- `with_sub_agent_usage`: a task run with the usage of its sub-agents'
  runs added.

**Kiln's usage numbers do not include a sub-agent's calls.** The `usage` of
a task run sums the model calls of that run only. Kiln saves the
sub-agent's run as a task run of the sub-agent's task, with its own
`usage`, and the tool message of the parent's trace names it
(`kiln_task_tool_data`: `<project>:::<tool id>:::<task id>:::<run id>`).
So code adds the two. Kiln saves the sub-agent's run only when it saves the
parent's run: the run endpoint does, an eval job does not. For a drive
through an eval job the sub-agent's usage is lost, and `SubAgentUsage.unread`
counts those calls.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kiln_harness_tuner.kiln import KilnError

Call = Callable[..., Awaitable[Any]]

KILN_TASK_TOOL_PREFIX = "kiln_task::"
TOOL_DATA_KEY = "kiln_task_tool_data"
TOOL_DATA_SEPARATOR = ":::"
# The prompts of a called task whose text uses no stored run: a saved
# prompt, a run config's frozen prompt, a fine-tune's prompt, and the
# generators that use only the task's own fields. Every other generator
# (few-shot, multi-shot, repairs) builds its text from the task's stored
# runs, which the loop does not split: one made while a held-out item was
# driven would show there. Such a prompt is named, never rendered.
RUN_FREE_PROMPT_PREFIXES = ("id::", "task_run_config::", "fine_tune_prompt::")
RUN_FREE_PROMPT_GENERATORS = frozenset(
    {"simple_prompt_builder", "simple_chain_of_thought_prompt_builder"}
)
# The run's registry of created records (`gateway_created.jsonl`, written
# by the gateway): the kinds of what `create_sub_agent` made. Each key
# carries its kind as a prefix, so no key is ever taken for the id of a
# train record. The row of a run config or a tool names its task.
CREATED_FILE = "gateway_created.jsonl"
SUB_AGENT_TASK_KIND = "sub_agent_task"
SUB_AGENT_RUN_CONFIG_KIND = "sub_agent_run_config"
SUB_AGENT_TOOL_KIND = "sub_agent_tool"
# A sub-agent can attach a sub-agent of its own. The usage of nested runs
# is added down to this depth.
MAX_USAGE_DEPTH = 3
_USAGE_SUM_KEYS = (
    "cost",
    "total_llm_latency_ms",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_tokens",
)


def tool_ids_of(props: Any) -> tuple[str, ...]:
    """Every tool id that run-config properties attach, in their order."""
    config = props.get("tools_config") if isinstance(props, Mapping) else None
    tools = config.get("tools") if isinstance(config, Mapping) else None
    return tuple(str(t) for t in tools or [])


def sub_agent_tool_ids(props: Any) -> tuple[str, ...]:
    """The `kiln_task::` tool ids that run-config properties attach."""
    return tuple(t for t in tool_ids_of(props) if t.startswith(KILN_TASK_TOOL_PREFIX))


@dataclass(frozen=True)
class SubAgent:
    """One sub-agent as a run config attaches it. `run_config`: the row of
    the run config that the tool pins, or None when the task has no such
    run config."""

    tool_id: str
    tool_name: str
    tool_description: str
    task_id: str
    run_config_id: str
    task: dict[str, Any]
    run_config: dict[str, Any] | None
    archived: bool = False

    @property
    def server_id(self) -> str:
        return self.tool_id.removeprefix(KILN_TASK_TOOL_PREFIX)

    @property
    def properties(self) -> dict[str, Any]:
        props = (self.run_config or {}).get("run_config_properties")
        return props if isinstance(props, dict) else {}

    @property
    def prompt_reads_runs(self) -> bool:
        """Is the prompt a generator that builds its text from the task's
        stored runs? A frozen prompt on the run config is never one."""
        if isinstance((self.run_config or {}).get("prompt"), dict):
            return False
        prompt_id = self.properties.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            return False
        return prompt_id not in RUN_FREE_PROMPT_GENERATORS and not prompt_id.startswith(
            RUN_FREE_PROMPT_PREFIXES
        )

    @property
    def pair(self) -> tuple[str | None, str | None]:
        """The (provider, model) the sub-agent runs on."""
        provider = self.properties.get("model_provider_name")
        model = self.properties.get("model_name")
        return (
            provider if isinstance(provider, str) else None,
            model if isinstance(model, str) else None,
        )


def _rows(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, dict) and isinstance(response.get("run_configs"), list):
        response = response["run_configs"]
    return [r for r in response or [] if isinstance(r, dict)]


async def read_sub_agent(call: Call, project_id: str, tool_id: str) -> SubAgent | None:
    """The sub-agent behind a `kiln_task::` tool id (or a bare tool server
    id), or None when the server is not a Kiln task tool. Reads the saved
    tool server, the task's own record and its run configs: never the
    task's runs or evals. Raises `KilnError` when Kiln does not answer."""
    server_id = tool_id.removeprefix(KILN_TASK_TOOL_PREFIX)
    project = f"/api/projects/{project_id}"
    # `/config` reads the saved record only. The plain route also asks an
    # MCP server for its tools.
    server = await call("GET", f"{project}/tool_servers/{server_id}/config")
    server = server if isinstance(server, dict) else {}
    props = server.get("properties")
    props = props if isinstance(props, dict) else {}
    if server.get("type") != "kiln_task" or not props.get("task_id"):
        return None
    task_id = str(props["task_id"])
    base = f"{project}/tasks/{task_id}"
    task = await call("GET", base)
    rc_id = str(props.get("run_config_id") or "")
    rows = _rows(await call("GET", f"{base}/run_configs"))
    return SubAgent(
        tool_id=f"{KILN_TASK_TOOL_PREFIX}{server_id}",
        tool_name=str(props.get("name") or server.get("name") or ""),
        tool_description=str(
            props.get("description") or server.get("description") or ""
        ),
        task_id=task_id,
        run_config_id=rc_id,
        task=task if isinstance(task, dict) else {},
        run_config=next((r for r in rows if str(r.get("id")) == rc_id), None),
        archived=props.get("is_archived") is True,
    )


async def read_sub_agents(
    call: Call, project_id: str, props: Any
) -> dict[str, SubAgent | None]:
    """tool id -> the sub-agent, for each `kiln_task::` tool that run-config
    properties attach. None: the id names no Kiln task tool."""
    return {
        tool_id: await read_sub_agent(call, project_id, tool_id)
        for tool_id in dict.fromkeys(sub_agent_tool_ids(props))
    }


# ---- what a run created ----


@dataclass(frozen=True)
class CreatedSubAgent:
    """One sub-agent task a run created, from the run's registry. A create
    that failed half-way leaves a task with no run config or no tool."""

    task_id: str
    run_config_id: str | None = None
    tool_id: str | None = None


def created_sub_agents(run_dir: Path) -> list[CreatedSubAgent]:
    """The sub-agents this run created, in the order of their tasks. Read
    from the run's registry: an unreadable file or line reads as nothing."""
    try:
        lines = (run_dir / CREATED_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    tasks: dict[str, dict[str, str]] = {}
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        kind, _, record_id = str(row.get("id") or "").partition(":")
        if kind != row.get("kind") or not record_id:
            continue
        if kind == SUB_AGENT_TASK_KIND:
            tasks.setdefault(record_id, {})
        elif kind in (SUB_AGENT_RUN_CONFIG_KIND, SUB_AGENT_TOOL_KIND):
            tasks.setdefault(str(row.get("eval_id") or ""), {})[kind] = record_id
    return [
        CreatedSubAgent(
            task_id=task_id,
            run_config_id=found.get(SUB_AGENT_RUN_CONFIG_KIND),
            tool_id=f"{KILN_TASK_TOOL_PREFIX}{found[SUB_AGENT_TOOL_KIND]}"
            if SUB_AGENT_TOOL_KIND in found
            else None,
        )
        for task_id, found in tasks.items()
        if task_id
    ]


# ---- the calls a task run made ----


@dataclass(frozen=True)
class SubAgentCall:
    """One call of a sub-agent tool in a trace. `run_id`: the sub-agent's
    task run, or None when Kiln did not save it."""

    tool_id: str
    task_id: str
    run_id: str | None


def sub_agent_calls(run: Mapping[str, Any]) -> list[SubAgentCall] | None:
    """The sub-agent calls in a task run's trace, in order. None when the
    run has no trace: then the calls are not known."""
    trace = run.get("trace")
    if not isinstance(trace, list):
        return None
    calls: list[SubAgentCall] = []
    for message in trace:
        data = message.get(TOOL_DATA_KEY) if isinstance(message, Mapping) else None
        if not isinstance(data, str):
            continue
        parts = data.split(TOOL_DATA_SEPARATOR)
        if len(parts) != 4:
            continue
        _project, tool_id, task_id, run_id = parts
        saved = run_id and run_id != "None"
        calls.append(SubAgentCall(tool_id, task_id, run_id if saved else None))
    return calls


def calls_by_tool(run: Mapping[str, Any]) -> dict[str, int] | None:
    """tool id -> how many times the run called that sub-agent. None when
    the run has no trace."""
    calls = sub_agent_calls(run)
    if calls is None:
        return None
    counts: dict[str, int] = {}
    for call in calls:
        counts[call.tool_id] = counts.get(call.tool_id, 0) + 1
    return counts


# ---- the usage of a run and its sub-agents ----


@dataclass(frozen=True)
class SubAgentUsage:
    """What `with_sub_agent_usage` added. `calls`: the sub-agent calls in
    the run and its nested runs. `unread`: the calls whose usage is not in
    the total (Kiln saved no run, or the run could not be read)."""

    calls: int = 0
    unread: int = 0
    cost: float = 0.0
    latency_ms: float = 0.0

    @property
    def note(self) -> str:
        """One line for a record, or "" when the run called no sub-agent."""
        if not self.calls:
            return ""
        if not self.unread:
            return f"includes {self.calls} sub-agent call(s)"
        return (
            f"{self.unread} of {self.calls} sub-agent call(s) have no usage"
            " record, so their cost and latency are not included"
        )


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


async def with_sub_agent_usage(
    call: Call, project_id: str, run: Mapping[str, Any], depth: int = MAX_USAGE_DEPTH
) -> tuple[dict[str, Any], SubAgentUsage]:
    """A copy of the task run whose `usage` also holds the usage of every
    sub-agent run it called, and what was added. The latencies are summed:
    when the model calls two sub-agents in one turn, Kiln runs them at the
    same time, and the sum is then above the wall-clock time."""
    out = dict(run)
    calls = sub_agent_calls(run) or []
    if not calls:
        return out, SubAgentUsage()
    usage = dict(run["usage"]) if isinstance(run.get("usage"), Mapping) else {}
    total = unread = len(calls)
    cost = latency = 0.0
    for sub_call in calls:
        if sub_call.run_id is None or depth <= 0:
            continue
        path = (
            f"/api/projects/{project_id}/tasks/{sub_call.task_id}"
            f"/runs/{sub_call.run_id}"
        )
        try:
            sub_run = await call("GET", path)
        except KilnError:
            continue
        if not isinstance(sub_run, Mapping):
            continue
        sub_run, nested = await with_sub_agent_usage(
            call, project_id, sub_run, depth - 1
        )
        sub_usage = sub_run.get("usage")
        if not isinstance(sub_usage, Mapping):
            continue
        unread += nested.unread - 1
        total += nested.calls
        cost += _number(sub_usage.get("cost"))
        latency += _number(sub_usage.get("total_llm_latency_ms"))
        for key in _USAGE_SUM_KEYS:
            if isinstance(sub_usage.get(key), (int, float)):
                base = usage.get(key)
                base = base if isinstance(base, (int, float)) else 0
                usage[key] = base + sub_usage[key]
    out["usage"] = usage
    return out, SubAgentUsage(total, unread, cost, latency)
