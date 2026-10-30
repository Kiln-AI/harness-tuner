"""OpenCodeCliDispatcher — the OpenCode CLI harness.

One `opencode run` subprocess per call. As for Codex, the prompt, the
envelope, the repair retry, the error routing and the step records are
`ClaudeCliDispatcher`'s; this module replaces the command, where it runs, how
its output reads and where its transcript is.

**The fence.** OpenCode has no OS sandbox. The fence is its permission rules,
which apply to its own tools, and every session runs under a config file the
dispatcher writes for it (`OPENCODE_CONFIG`):

- every tool is denied (`"*": "deny"`), so the shell, web fetch, web search,
  grep, glob, sub-agents and the rest are not in the session at all;
- `read` is allowed in the session's workspace and in the repository's
  skills directory, and — for the roles that read traces — in the
  gateway's trace folder, and nowhere else;
- `edit` is allowed in the workspace, and denied for the val readers;
- `skill` is allowed, so the skills load as they do under Claude Code;
- the role's MCP tools are allowed by name.

This is a tool-level allow list, not an OS one: it is as strong as
OpenCode's own permission checks. OpenCode matches a path rule against the
path relative to its worktree (the git root above the workspace), so each
rule is written in that form and in the absolute form.

**Skills and instructions.** OpenCode finds `.agents/skills` and
`AGENTS.md` under the worktree root, so the repository's skills and its
installed instructions load with no link.

**What it does not have.** A session id cannot be chosen before the spawn,
so a session killed at its wall-clock cap is not resumed; the ladder re-runs
it. A provider plan that prices nothing reports a cost of 0: such a
session's tokens are booked as unmeasured.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from kiln_harness_tuner.dispatch.claude_cli import (
    CLI_CHECK_ROLE,
    NO_TOOL_ROLES,
    READ_ONLY_ROLES,
    ClaudeCliDispatcher,
    CliRound,
    child_env,
)
from kiln_harness_tuner.dispatch.codex_cli import mcp_tool_names
from kiln_harness_tuner.gateway.ops import GATEWAY_SERVER, TRACE_READER_ROLES
from kiln_harness_tuner.gateway.trace_files import traces_dir
from kiln_harness_tuner.schemas import TargetBlock, Usage

logger = logging.getLogger(__name__)

OPENCODE_EFFORT = "high"
# An MCP request can wait on a Kiln job (`run_train_eval`, `run_task`), and
# OpenCode's default is 5 seconds. The role's own wall-clock cap bounds the
# session. The first spawn of a server may sync a uv environment.
MCP_TIMEOUT_MS = 30 * 60 * 1000
EXPORT_TIMEOUT_SECS = 60


def _worktree(path: Path) -> Path | None:
    """The git root OpenCode treats as the worktree, or None."""
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            return parent
    return None


def path_patterns(path: Path, worktree: Path | None) -> list[str]:
    """The rule patterns for everything under `path`: relative to the
    worktree (the form OpenCode matches; with no worktree, relative to `/`)
    and absolute."""
    path = path.resolve()
    if worktree is not None and path.is_relative_to(worktree):
        relative = path.relative_to(worktree).as_posix()
    else:
        relative = path.as_posix().lstrip("/")
    return [f"{relative}/**", f"{path.as_posix()}/**"]


def opencode_config(
    *,
    workspace: Path,
    skills_dir: Path,
    writable: bool,
    tools: bool,
    mcp_servers: dict[str, dict[str, Any]],
    mcp_tools: list[str],
    max_turns: int | None,
    read_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    """One session's OpenCode config. `tools=False`: every tool denied.
    `read_dirs`: more folders `read` may open — the gateway's trace folder
    for a role that reads traces."""
    permission: dict[str, Any] = {"*": "deny"}
    if tools:
        worktree = _worktree(workspace.resolve())
        ws = path_patterns(workspace, worktree)
        skills = path_patterns(skills_dir, worktree)
        extra = [p for d in read_dirs or [] for p in path_patterns(d, worktree)]
        permission["read"] = {
            "*": "deny",
            **dict.fromkeys(ws + skills + extra, "allow"),
        }
        permission["edit"] = (
            {"*": "deny", **dict.fromkeys(ws, "allow")} if writable else "deny"
        )
        permission["external_directory"] = {
            "*": "deny",
            **dict.fromkeys(ws + skills + extra, "allow"),
        }
        permission["skill"] = "allow"
        permission.update(dict.fromkeys(mcp_tools, "allow"))
    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "permission": permission,
        "mcp": mcp_servers if tools else {},
        "share": "disabled",
        "autoupdate": False,
    }
    if max_turns is not None:
        config["agent"] = {"build": {"steps": max_turns}}
    return config


def read_opencode_output(stdout: str) -> CliRound | None:
    """`opencode run --format json` prints one JSON event per line. Every
    event carries the session id; the final message is the text of the last
    step that has text; the usage is the sum of `step_finish`; an `error`
    event is the session's own error. None when no event parses."""
    session_id: str | None = None
    final: str | None = None
    step_text: list[str] = []
    tokens = 0
    cost = 0.0
    error: str | None = None
    seen = False
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        seen = True
        if isinstance(event.get("sessionID"), str):
            session_id = event["sessionID"]
        kind = event.get("type")
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        if kind == "step_start":
            step_text = []
        elif kind == "text" and isinstance(part.get("text"), str):
            step_text.append(part["text"])
            final = "".join(step_text)
        elif kind == "step_finish":
            counts = part.get("tokens")
            if isinstance(counts, dict):
                total = counts.get("total")
                if isinstance(total, (int, float)):
                    tokens += int(total)
                else:
                    tokens += sum(
                        int(v)
                        for k in ("input", "output", "reasoning")
                        if isinstance(v := counts.get(k), (int, float))
                    )
            if isinstance(part.get("cost"), (int, float)):
                cost += float(part["cost"])
        elif kind == "error":
            err = event.get("error")
            message = None
            if isinstance(err, dict):
                data = err.get("data")
                message = (
                    data.get("message") if isinstance(data, dict) else None
                ) or err.get("name")
            error = str(message or "session failed")
    if not seen:
        return None
    usage = (
        Usage(cost_usd=cost, tokens=tokens)
        if cost > 0
        # A plan that prices nothing: the tokens are booked as unmeasured.
        else Usage(tokens=tokens, unmeasured_tokens=tokens)
    )
    return CliRound(session_id=session_id, result_text=final, usage=usage, error=error)


class OpenCodeCliDispatcher(ClaudeCliDispatcher):
    """AgentDispatcher over `opencode run`."""

    CLI_NAME = "opencode run"
    SIGN_IN_HINT = (
        "the OpenCode sign-in every agent session needs (`opencode auth login`)"
    )
    READS_ERROR_ON_NONZERO_EXIT = True
    # Each round reports its own cost (or none), so nothing is subtracted.
    RESUME_COST_IS_SESSION_TOTAL = False
    # No model option unless one is named: OpenCode's own default model.
    DEFAULT_MODEL = None
    MODEL_DEFAULTS: dict[str, str] = {}
    DEFAULT_EFFORT = OPENCODE_EFFORT

    def __init__(
        self,
        target: TargetBlock,
        repo_root: Path,
        *,
        opencode_bin: str = "opencode",
        skills_dir: Path | None = None,
        **kwargs: Any,
    ):
        super().__init__(target, repo_root, **kwargs)
        self._opencode_bin = opencode_bin
        self._skills_dir = skills_dir or repo_root / ".agents" / "skills"

    def _session_root(self) -> Path:
        return (
            self._run_dir
            if self._run_dir is not None
            else Path(tempfile.gettempdir()) / "kiln-harness-tuner-opencode"
        )

    # ---- where it runs ----

    def _workspace(self, label: str | None) -> Path:
        """The session's scratch directory, the only path it may write."""
        workspace = self._session_root() / "scratch" / (label or "session")
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def _cwd(self, label: str | None) -> Path:
        return self._workspace(label)

    # ---- the command ----

    def _mcp_servers(self, agent: str) -> dict[str, dict[str, Any]]:
        if self._mcp_config_path is None:
            return {}
        path = self._mcp_config_path
        role_path = path.parent / f"mcp_config.{agent}.json"
        if role_path.exists():
            path = role_path
        servers = json.loads(path.read_text()).get("mcpServers", {})
        return {
            name: {
                "type": "local",
                "command": [spec["command"], *spec.get("args", [])],
                "enabled": True,
                "timeout": MCP_TIMEOUT_MS,
            }
            for name, spec in servers.items()
        }

    def _config_path(self, agent: str, label: str | None) -> Path:
        """Write the session's config outside its workspace — under the run
        directory, which the read rules deny — and return its path."""
        no_tools = agent in NO_TOOL_ROLES or agent == CLI_CHECK_ROLE
        servers = {} if no_tools else self._mcp_servers(agent)
        mcp_tools = [
            # OpenCode names an MCP tool `<server>_<tool>`. The gateway
            # serves only the role's operations, so its tools are allowed by
            # wildcard; the installer's are named one by one.
            f"{name}_{tool}"
            for name in servers
            for tool in (
                ["*"] if name == GATEWAY_SERVER else mcp_tool_names(agent, name)
            )
        ]
        config = opencode_config(
            workspace=self._workspace(label),
            skills_dir=self._skills_dir,
            writable=agent not in READ_ONLY_ROLES,
            tools=not no_tools,
            mcp_servers=servers,
            mcp_tools=mcp_tools,
            max_turns=self._max_turns,
            read_dirs=[traces_dir(self._run_dir.resolve())]
            if self._run_dir is not None and agent in TRACE_READER_ROLES
            else None,
        )
        path = self._session_root() / "opencode" / f"{label or agent}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
        return path

    def _argv(
        self,
        agent: str,
        resume_session_id: str | None = None,
        session_id: str | None = None,
        label: str | None = None,
    ) -> list[str]:
        argv = [
            "env",
            f"OPENCODE_CONFIG={self._config_path(agent, label)}",
            self._opencode_bin,
            "run",
            *self._cli_options,
            "--format",
            "json",
            # No external plugins; approve what the rules do not deny, so a
            # session never waits on a question nobody can answer.
            "--pure",
            "--auto",
            "--variant",
            self._effort_for(agent),
        ]
        model = self._model_for(agent)
        if model:
            argv += ["-m", model]
        if resume_session_id:
            argv += ["--session", resume_session_id]
        return argv

    # ---- what it printed ----

    def _parse_cli_output(self, stdout: str) -> CliRound | None:
        return read_opencode_output(stdout)

    def _transcript_source(self, session_id: str) -> Path | None:
        """`opencode export <id>` into the run directory; None on failure."""
        dest = self._session_root() / "opencode" / f"export-{session_id}.json"
        try:
            proc = subprocess.run(
                [self._opencode_bin, "export", session_id],
                capture_output=True,
                text=True,
                timeout=EXPORT_TIMEOUT_SECS,
                env=child_env()[0],
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("opencode export %s failed: %s", session_id, exc)
            return None
        if proc.returncode != 0 or not proc.stdout.strip():
            logger.warning("opencode export %s exited %d", session_id, proc.returncode)
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(proc.stdout)
        return dest

    def _recover_timed_out_usage(
        self, label: str, session_id: str, elapsed_secs: float
    ) -> Usage:
        # The id was not chosen before the spawn: nothing to recover.
        return Usage(wall_secs=elapsed_secs)
