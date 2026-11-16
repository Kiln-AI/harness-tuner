"""The OpenEnv check: every OpenEnv server the task uses answers, through Kiln.

Kiln saves only an OpenEnv server's URL. It never starts the server: the
user starts it. A server that is down makes each eval item that resets into
it fail as a measurement error, and the run spends money on a baseline that
cannot be measured. So the start asks Kiln for each server's tools first.

The servers come from two sources only:

- the baseline run config's tool ids, `kiln_tool::openenv::<server id>::<tool>`;
- the `openenv_reset.openenv_server_id` of the task's TRAIN eval inputs.

The train inputs are the loop's own train set (`gateway.trainset.compute`),
which leaves out every item a val or test split holds or a held-out tag
marks. The check looks at no other item: the loop reads train items only.
The check talks only to Kiln (`GET …/openenv_servers/{id}/tools`), never to
an OpenEnv server. That call starts no episode.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.gateway.trainset import TrainSetError, compute
from kiln_harness_tuner.job_drives import OPENENV_LIST_TIMEOUT_SECS, openenv_tool_parts
from kiln_harness_tuner.kiln import KilnAPIError, KilnError, kiln_error_text
from kiln_harness_tuner.onboarding.terminal import Problem, english_list, plural
from kiln_harness_tuner.quarantine import QuarantineError, _children, task_dirs

WHY = (
    "Kiln runs the task's OpenEnv tools and its OpenEnv eval inputs on these",
    "servers. Kiln doesn't start an OpenEnv server: you start each one yourself.",
)
RETRY_LABEL = "Check again (the environment is running now)"


@dataclass(frozen=True)
class OpenEnvCheck:
    """The check's outcome. `servers` counts the servers that answered; it
    is 0 when the task uses no OpenEnv server, or Kiln has none."""

    problem: Problem | None = None
    servers: int = 0

    @property
    def step_text(self) -> str:
        verb = "answers" if self.servers == 1 else "answer"
        return f"{plural(self.servers, 'server')} {verb} through Kiln"


@dataclass
class ServerUse:
    """What the task uses of one server: the tool names the baseline run
    config lists, and how many train inputs reset into it."""

    tools: set[str] = field(default_factory=set)
    train_inputs: int = 0


def baseline_servers(tool_ids: Iterable[str]) -> dict[str, set[str]]:
    """server id -> the tool names the run config lists on that server."""
    servers: dict[str, set[str]] = {}
    for tool_id in tool_ids:
        parts = openenv_tool_parts(tool_id)
        if parts is not None:
            servers.setdefault(parts[0], set()).add(parts[1])
    return servers


def train_input_servers(project_path: Path, task_id: str) -> dict[str, int]:
    """server id -> how many of the task's TRAIN eval inputs reset into it,
    read from disk. The train inputs are the gateway's train set, so an
    item that is val or test anywhere, or has a held-out tag, is left out;
    only a train input's `openenv_reset` is looked at. A project whose train
    set can't be decided gives none: the run's start-up refuses it later,
    with its own message."""
    try:
        train = compute(project_path, task_id).eval_inputs
    except (TrainSetError, QuarantineError):
        return {}
    counts: dict[str, int] = {}
    for task_dir in task_dirs(project_path, task_id):
        for _path, doc in _children(task_dir / "eval_inputs", "eval_input.kiln"):
            if str(doc.get("id")) not in train:
                continue
            reset = doc.get("openenv_reset")
            server_id = (
                reset.get("openenv_server_id") if isinstance(reset, dict) else None
            )
            if isinstance(server_id, str) and server_id:
                counts[server_id] = counts.get(server_id, 0) + 1
    return counts


def used_servers(
    tool_ids: Iterable[str], project_path: Path, task_id: str
) -> dict[str, ServerUse]:
    """Every OpenEnv server the task uses, by id, from both sources."""
    uses: dict[str, ServerUse] = {}
    for server_id, tools in baseline_servers(tool_ids).items():
        uses.setdefault(server_id, ServerUse()).tools |= tools
    for server_id, count in train_input_servers(project_path, task_id).items():
        uses.setdefault(server_id, ServerUse()).train_inputs += count
    return uses


def _used_by(use: ServerUse) -> str:
    parts = []
    if use.tools:
        parts.append("the baseline run config's tools")
    if use.train_inputs:
        parts.append(plural(use.train_inputs, "train eval input"))
    return english_list(parts)


def _quoted(names: Iterable[str]) -> str:
    return english_list([f'"{name}"' for name in sorted(names)])


def _url(row: Mapping[str, Any]) -> str | None:
    url = row.get("url")
    return url if isinstance(url, str) and url else None


@dataclass(frozen=True)
class _Failure:
    """Why Kiln couldn't list a server's tools. `unreachable` when the
    server didn't answer Kiln (Kiln's 502 or another 5xx) or Kiln didn't
    answer the tuner; otherwise Kiln refused the request (a 4xx)."""

    reason: str
    unreachable: bool


async def _tool_names(
    kiln: KilnApi, project_id: str, server_id: str
) -> list[str] | _Failure:
    """The tool names the server serves, or why Kiln couldn't list them."""
    path = f"/api/projects/{project_id}/openenv_servers/{server_id}/tools"
    try:
        body = await kiln.call(
            "GET", path, timeout_secs=OPENENV_LIST_TIMEOUT_SECS, retries=False
        )
    except KilnAPIError as exc:
        return _Failure(
            f"Kiln answered HTTP {exc.status_code}: {kiln_error_text(exc.body)}",
            unreachable=exc.status_code >= 500,
        )
    except KilnError:
        # An httpx timeout is a transport error too.
        return _Failure(
            f"Kiln gave no answer in {OPENENV_LIST_TIMEOUT_SECS:g} s,"
            " or the connection to Kiln failed",
            unreachable=True,
        )
    rows = body if isinstance(body, list) else []
    return [
        str(row["name"]) for row in rows if isinstance(row, dict) and row.get("name")
    ]


def _server_lines(
    server_id: str,
    row: Mapping[str, Any] | None,
    use: ServerUse,
    served: list[str] | _Failure,
) -> list[str]:
    """The lines for one server that isn't ready, or none when it is."""
    if row is None:
        return [
            f"• OpenEnv server {server_id}, used by {_used_by(use)}: Kiln has no"
            " OpenEnv server with this ID in this project.",
            "  Add the server in Kiln, or change what uses it.",
        ]
    name = str(row.get("name") or server_id)
    url = _url(row)
    if url is None:
        return [
            f'• "{name}", used by {_used_by(use)}: it has no URL in Kiln.',
            "  Start the environment, then set the server's URL in Kiln.",
        ]
    if isinstance(served, _Failure) and served.unreachable:
        return [
            f'• "{name}" at {url}, used by {_used_by(use)}, doesn\'t answer.',
            f"  {served.reason}.",
            f"  Start the environment at {url}, or correct the server's URL in Kiln.",
        ]
    if isinstance(served, _Failure):
        return [
            f'• "{name}" at {url}, used by {_used_by(use)}: Kiln couldn\'t list'
            " its tools.",
            f"  {served.reason}.",
            "  Check the server in Kiln.",
        ]
    missing = use.tools - set(served)
    if missing:
        serves = _quoted(served) if served else "no tools"
        tool = "a tool" if len(missing) == 1 else "tools"
        return [
            f'• "{name}" at {url} doesn\'t serve {tool} the baseline run config'
            f" lists: {_quoted(missing)}.",
            f"  It serves {serves}.",
            "  Start the right environment at this URL, then check again.",
            "  To change the run config's tools instead, exit and start the tuner"
            " again.",
        ]
    return []


async def check_openenv(
    kiln: KilnApi, *, project_id: str, uses: Mapping[str, ServerUse]
) -> OpenEnvCheck:
    """Ask Kiln for the tools of each server in `uses`. A Kiln with no
    OpenEnv servers (404 or 405), or no server to check, passes silently."""
    if not uses:
        return OpenEnvCheck()
    try:
        listing = await kiln.call("GET", f"/api/projects/{project_id}/openenv_servers")
    except KilnAPIError as exc:
        if exc.status_code in (404, 405):  # a Kiln with no OpenEnv servers
            return OpenEnvCheck()
        raise
    rows = {
        str(row.get("id")): row
        for row in (listing if isinstance(listing, list) else [])
        if isinstance(row, dict) and row.get("id")
    }
    ids = sorted(uses)

    async def served(server_id: str) -> list[str] | _Failure:
        row = rows.get(server_id)
        if row is None or _url(row) is None:
            return []  # nothing to ask: the lines say why
        return await _tool_names(kiln, project_id, server_id)

    # The servers are asked at once: a dead one waits for its connect
    # failure, and the others need not wait behind it.
    answers = await asyncio.gather(*(served(server_id) for server_id in ids))
    groups = [
        lines
        for server_id, answer in zip(ids, answers, strict=True)
        if (
            lines := _server_lines(
                server_id, rows.get(server_id), uses[server_id], answer
            )
        )
    ]
    if not groups:
        return OpenEnvCheck(servers=len(ids))
    bad = len(groups)
    title = (
        "An OpenEnv server the task uses isn't ready"
        if bad == 1
        else f"{bad} OpenEnv servers the task uses aren't ready"
    )
    problem = Problem(
        title,
        (*WHY, *(f"  {line}" for lines in groups for line in lines)),
        retry_label=RETRY_LABEL,
    )
    return OpenEnvCheck(problem=problem)
