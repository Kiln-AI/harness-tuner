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
- `usage_includes_sub_agents`: whether a run's `usage` on this Kiln holds
  its sub-agents' usage. The one switch of the cost guard.
- `read_sub_agent_usage`: the usage of the sub-agents' own runs for one
  task run, for information.

**Kiln's usage numbers do not include a sub-agent's calls.** The `usage` of
a task run sums the model calls of that run only. Kiln saves the
sub-agent's run as a task run of the sub-agent's task, with its own
`usage`, and the tool message of the parent's trace names it
(`kiln_task_tool_data`: `<project>:::<tool id>:::<task id>:::<run id>`).
Kiln saves the sub-agent's run only when it saves the parent's run: the
run endpoint does, an eval job does not, and the run id is then `None`.

The fix is in Kiln, by the owner's decision: a run's usage will hold its
sub-agents' usage. So the tuner reads cost and latency from the same place
as before, and it never adds the two itself. Until it can tell that the
server has the fix, a drive that calls a sub-agent has no cost and no
latency for a comparison (`NOT_MEASURED`), and the usage of the
sub-agents' own runs is shown beside it, for information.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
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
    run has no trace: then the calls are not known. A stored eval-run row
    is read too (`task_run_trace`, a list or its JSON text). The proof of a
    call is the parent's tool message: in an eval job no run of the
    sub-agent is saved."""
    trace = run.get("trace") or run.get("task_run_trace")
    if isinstance(trace, str):
        try:
            trace = json.loads(trace)
        except ValueError:
            return None
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


# ---- the usage of a sub-agent's calls ----

# Does the `usage` of a task run on this Kiln server include the usage of
# the sub-agents that the run called? Today it does not, and no route or
# schema field of the server says which case holds, so the answer is fixed.
# The owner decided that the fix is in Kiln: a run's usage will hold its
# sub-agents' usage, in a run through the run endpoint and in an eval job.
# When Kiln names how a client can tell, `usage_includes_sub_agents` reads
# that, and every guard below switches off with it. The tuner never adds a
# sub-agent's usage to a run's usage itself: after the Kiln fix that would
# count it twice.
KILN_USAGE_INCLUDES_SUB_AGENTS = False

NOT_MEASURED = (
    "not measured: a drive of this run config calls a sub-agent, and Kiln's"
    " usage of a run leaves the sub-agent's calls out"
)


def not_measured_note(who: str) -> str:
    """The line for a comparison that has no verdict: "cost and latency
    not measured: the drives of the candidate call a sub-agent, …"."""
    return (
        f"cost and latency not measured: the drives of {who} call a"
        " sub-agent, and Kiln's usage of a run leaves the sub-agent's calls out"
    )


def usage_includes_sub_agents() -> bool:
    """Is a run's `usage` on this Kiln server the cost and latency of the
    whole request, sub-agents included? The one switch for every guard on a
    sub-agent's cost. False: a drive that calls a sub-agent has no latency
    and no cost for a comparison, so a candidate cannot win on cost or
    speed from numbers that leave its sub-agent out."""
    return KILN_USAGE_INCLUDES_SUB_AGENTS


@dataclass(frozen=True)
class SubAgentUsage:
    """The usage of the sub-agents' own runs for ONE task run, read for
    information. It is never added to the run's usage. `calls`: the
    sub-agent calls in the run and its nested runs. `unread`: the calls
    with no usage record (Kiln saved no run, or the run could not be
    read). `usage`: the sum over the runs that were read."""

    calls: int = 0
    unread: int = 0
    cost: float = 0.0
    latency_ms: float = 0.0
    usage: dict[str, Any] | None = None

    @property
    def note(self) -> str:
        """One line for an agent, or "" when the run called no sub-agent."""
        if not self.calls:
            return ""
        if self.unread == self.calls:
            return (
                f"{self.calls} sub-agent call(s) have no usage record: an eval"
                " job saves no run for a sub-agent"
            )
        text = (
            f"sub_agent_usage is the usage of the sub-agents' own runs"
            f" ({self.calls} call(s))"
        )
        if self.unread:
            text += f"; {self.unread} of them have no usage record"
        if not usage_includes_sub_agents():
            text += "; it is not in `usage`"
        return text


def calls_a_sub_agent(run: Mapping[str, Any]) -> bool:
    """Does the run's trace (or the eval-run row's) show a sub-agent call?"""
    return bool(sub_agent_calls(run))


def usage_line(infos: Sequence[SubAgentUsage], drives: int) -> str:
    """One line for a candidate's record on what its timing drives show of
    its sub-agents: how many drives call one, whether the cost and latency
    are measured, and the usage of the sub-agents' own runs. "" when no
    drive calls a sub-agent."""
    if not infos:
        return ""
    head = f"{len(infos)} of {max(drives, len(infos))} drives call a sub-agent"
    read = [i for i in infos if i.unread < i.calls]
    own = (
        f"the sub-agents' own runs: median ${median(i.cost for i in read):.4f}"
        f" and {median(i.latency_ms for i in read):.0f} ms per request"
        if read
        else "the sub-agents' runs have no usage record"
    )
    if usage_includes_sub_agents():
        return f"{head} ({own}, inside the numbers of the request)"
    return (
        f"cost and latency not measured: {head}, and Kiln's usage of a run"
        f" leaves the sub-agent's calls out ({own}, in no number here)"
    )


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


async def read_sub_agent_usage(
    call: Call, project_id: str, run: Mapping[str, Any], depth: int = MAX_USAGE_DEPTH
) -> SubAgentUsage:
    """The usage of every sub-agent run that a task run called, from the
    sub-agents' own task runs (the tool messages of the trace name them).
    Only the `usage` and the trace of a sub-agent's run are read. The
    latencies are summed: when the model calls two sub-agents in one turn,
    Kiln runs them at the same time, and the sum is then above the
    wall-clock time."""
    calls = sub_agent_calls(run) or []
    if not calls:
        return SubAgentUsage()
    total = unread = len(calls)
    summed: dict[str, Any] = {}
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
        sub_usage = sub_run.get("usage") if isinstance(sub_run, Mapping) else None
        if not isinstance(sub_usage, Mapping):
            continue
        unread -= 1
        parts = [sub_usage]
        if not usage_includes_sub_agents():
            # The sub-agent's own usage leaves ITS sub-agents out too.
            nested = await read_sub_agent_usage(call, project_id, sub_run, depth - 1)
            total += nested.calls
            unread += nested.unread
            parts.append(nested.usage or {})
        for part in parts:
            for key in _USAGE_SUM_KEYS:
                if isinstance(part.get(key), (int, float)):
                    summed[key] = summed.get(key, 0) + part[key]
    return SubAgentUsage(
        calls=total,
        unread=unread,
        cost=_number(summed.get("cost")),
        latency_ms=_number(summed.get("total_llm_latency_ms")),
        usage=summed or None,
    )
