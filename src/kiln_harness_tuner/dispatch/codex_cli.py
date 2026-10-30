"""CodexCliDispatcher — the Codex CLI harness.

One `codex exec` subprocess per call. The prompt, the envelope, the repair
retry, the error routing and the step records are `ClaudeCliDispatcher`'s;
this module replaces the four harness-specific steps: the command, where it
runs, how its output reads, and where its transcript is.

**The fence.** Codex reads files through its shell, so a loop session keeps
its shell and Codex's own OS sandbox (Seatbelt on macOS, bubblewrap and
Landlock on Linux) is the fence. Every session runs under the permission
profile `kiln_harness_tuner`, passed per call with `-c`:

- the whole disk is denied (`":root" = "deny"`), reads and writes;
- the platform paths common tools need are readable (`":minimal"`);
- the session's own workspace — its scratch directory — is writable, or
  readable only for the val readers;
- the repository's skills directory is readable, and nothing else of it;
- the gateway's trace folder (`runs/<run_id>/scratch/traces/`) is
  readable to the roles that read traces (`TRACE_READER_ROLES`);
- the network is off for every command.

This is an allow list, not a deny list: a directory nobody thought to fence is
still fenced. MCP servers run outside the sandbox (Codex's own rule), so the
loop gateway and the skill installer work as they do under Claude Code.

What the profile does not govern — web search, apps and connectors, the
browser, Computer Use, plugins, sub-agents — is switched off by name. The
user's own `~/.codex/config.toml` is not loaded (`--ignore-user-config`), so
none of their MCP servers or settings reach a loop session, and
`--strict-config` makes an unknown key a failure instead of a silent no-op.

**Skills and instructions.** Codex finds skills in `.agents/skills` and
instructions in `AGENTS.md` under its working directory. Each workspace holds
`.agents/skills`, a link to the repository's `.agents/skills`, which the
profile makes readable, and a copy of the repository's installed `AGENTS.md`.

**What it does not have.** Codex reports tokens, not cost: every session's
tokens are booked as unmeasured, and `--budget-usd` caps the Kiln side only.
A session id cannot be chosen before the spawn, so a session killed at its
wall-clock cap is not resumed; the ladder re-runs it (docs/harness-seam.md section 5).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from kiln_harness_tuner.dispatch.claude_cli import (
    CLI_CHECK_ROLE,
    NO_TOOL_ROLES,
    READ_ONLY_ROLES,
    AsyncioSubprocessRunner,
    ClaudeCliDispatcher,
    CliRound,
)
from kiln_harness_tuner.gateway.hook import SKILL_INSTALLER_TOOLS
from kiln_harness_tuner.gateway.ops import GATEWAY_SERVER, ROLE_OPS, TRACE_READER_ROLES
from kiln_harness_tuner.gateway.trace_files import traces_dir
from kiln_harness_tuner.schemas import TargetBlock, Usage

logger = logging.getLogger(__name__)

CODEX_EFFORT = "high"
PROFILE = "kiln_harness_tuner"
DEFAULT_CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
SKILLS_LINK = Path(".agents") / "skills"
# The environment variables an MCP server receives from the run.
MCP_ENV_PREFIXES = ("UV_", "PYTHON", "VIRTUAL_ENV")

# Codex features the permission profile does not govern, each a way around
# the file fence or out of the session: connectors, plugins, the browser,
# Computer Use, sub-agents, installs. Off by name; `--strict-config` fails
# the session if a Codex release no longer knows one.
DISABLED_FEATURES = (
    "apps",
    "plugins",
    "remote_plugin",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "in_app_browser",
    "in_app_local_automation",
    "multi_agent",
    "skill_mcp_dependency_install",
    "tool_suggest",
    "image_generation",
)

# An MCP tool call can wait on a Kiln job (`run_train_eval`, `run_task`); the
# role's own wall-clock cap bounds the session, not a per-call timeout. The
# first spawn of a server may sync a uv environment.
MCP_TOOL_TIMEOUT_SECS = 30 * 60
MCP_STARTUP_TIMEOUT_SECS = 120


def toml_value(value: Any) -> str:
    """`value` as a TOML inline value, for `codex -c key=<value>`."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        # A JSON string is a valid TOML basic string.
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return (
            "{"
            + ", ".join(
                f"{json.dumps(str(k))} = {toml_value(v)}" for k, v in value.items()
            )
            + "}"
        )
    raise TypeError(f"no TOML form for {type(value).__name__}")


def _link_folders(path: Path) -> list[Path]:
    """Every folder that holds a link `path` resolves through, in order."""
    out: list[Path] = []
    todo = [Path(os.path.abspath(path))]
    while todo and len(out) < 40:  # a link loop ends here
        current = todo.pop()
        for prefix in [*reversed(current.parents[:-1]), current]:
            if prefix.is_symlink():
                out.append(prefix.parent)
                target = Path(os.readlink(prefix))
                if not target.is_absolute():
                    target = prefix.parent / target
                todo.append(Path(os.path.abspath(target / current.relative_to(prefix))))
                break
    return out


def codex_install_paths(codex_bin: str) -> list[Path]:
    """The folders Codex runs from, which the profile must keep readable.

    With codex-cli 0.162, Codex starts a helper by running its own
    binary again, under the session's profile, by the path it was started
    from. A standalone install links `~/.local/bin/codex` to
    `~/.codex/packages/standalone/current/bin/codex`, `current` is a link to
    `releases/<version>`, and all of it is inside the home directory the
    profile denies — so the helper could not start and every session
    failed. A profile entry is a folder (an entry for the link file alone
    did not let it start). These are each folder that holds a link on the
    way to the binary, and the release folder (the real binary's
    grandparent) — never the home directory or one of its ancestors, and
    never `~/.codex` itself, which holds `auth.json`. Empty when the binary
    is not on PATH."""
    found = shutil.which(codex_bin)
    if found is None:
        return []
    home = Path.home().resolve()
    real = Path(found).resolve()

    def allowed(folder: Path) -> bool:
        return not home.is_relative_to(folder) and folder != home / ".codex"

    paths: list[Path] = []
    for folder in _link_folders(Path(found)):
        if allowed(folder) and folder not in paths:
            paths.append(folder)
    for folder in (real.parent.parent, real.parent):
        if allowed(folder):
            if folder not in paths:
                paths.append(folder)
            break
    return paths


def permission_profile(
    skills_dir: Path,
    *,
    writable: bool,
    install_paths: Sequence[Path] = (),
    read_paths: Sequence[Path] = (),
) -> dict[str, Any]:
    """`read_paths`: more folders the session may read — the gateway's
    trace folder for a role that reads traces."""
    filesystem: dict[str, Any] = {
        ":root": "deny",
        ":minimal": "read",
        ":workspace_roots": {".": "write" if writable else "read"},
        str(skills_dir.resolve()): "read",
    }
    for path in [*install_paths, *read_paths]:
        filesystem[str(path)] = "read"
    return {
        "description": "Kiln Harness Tuner agent session: its workspace and the skills",
        "filesystem": filesystem,
        "network": {"enabled": False},
    }


def mcp_tool_names(role: str, server: str) -> list[str]:
    """The tools of `server` the role may call, by bare name."""
    if server == GATEWAY_SERVER:
        return list(ROLE_OPS.get(role, ()))
    prefix = f"mcp__{server}__"
    return [
        t.removeprefix(prefix)
        for t in SKILL_INSTALLER_TOOLS.get(role, ())
        if t.startswith(prefix)
    ]


def read_codex_output(stdout: str) -> CliRound | None:
    """`codex exec --json` prints one JSON event per line. The session id is
    `thread.started`'s, the final message the last `agent_message`, the usage
    `turn.completed`'s; a `turn.failed` is the session's own error. None when
    no event parses."""
    session_id: str | None = None
    final: str | None = None
    tokens = 0
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
        kind = event.get("type")
        if kind == "thread.started" and isinstance(event.get("thread_id"), str):
            session_id = event["thread_id"]
        elif kind == "item.completed":
            item = event.get("item")
            if (
                isinstance(item, dict)
                and item.get("type") == "agent_message"
                and isinstance(item.get("text"), str)
            ):
                final = item["text"]
        elif kind == "turn.completed":
            usage = event.get("usage")
            if isinstance(usage, dict):
                # Cached input is counted inside `input_tokens`.
                for key in ("input_tokens", "output_tokens"):
                    value = usage.get(key)
                    if isinstance(value, (int, float)):
                        tokens += int(value)
        elif kind == "turn.failed":
            err = event.get("error")
            message = err.get("message") if isinstance(err, dict) else None
            error = str(message or "turn failed")
    if not seen:
        return None
    return CliRound(
        session_id=session_id,
        result_text=final,
        # No cost in the output: the tokens are booked as unmeasured.
        usage=Usage(tokens=tokens, unmeasured_tokens=tokens),
        error=error,
    )


class CodexCliDispatcher(ClaudeCliDispatcher):
    """AgentDispatcher over `codex exec`."""

    CLI_NAME = "codex exec"
    SIGN_IN_HINT = "the Codex sign-in every agent session needs (`codex login`)"
    READS_ERROR_ON_NONZERO_EXIT = True
    # Each round reports its own cost (or none), so nothing is subtracted.
    RESUME_COST_IS_SESSION_TOTAL = False
    # No model option unless one is named: Codex's own default model.
    DEFAULT_MODEL = None
    MODEL_DEFAULTS: dict[str, str] = {}
    DEFAULT_EFFORT = CODEX_EFFORT
    HAS_MAX_TURNS = False
    REPORTS_COST = False

    def __init__(
        self,
        target: TargetBlock,
        repo_root: Path,
        *,
        codex_bin: str = "codex",
        codex_sessions_dir: Path = DEFAULT_CODEX_SESSIONS_DIR,
        skills_dir: Path | None = None,
        runner: Any = None,
        **kwargs: Any,
    ):
        super().__init__(
            target,
            repo_root,
            runner=runner or AsyncioSubprocessRunner(),
            **kwargs,
        )
        self._codex_bin = codex_bin
        self._codex_sessions_dir = codex_sessions_dir
        self._skills_dir = skills_dir or repo_root / ".agents" / "skills"
        self._instructions = repo_root / "AGENTS.md"
        self._install_paths = codex_install_paths(codex_bin)

    # ---- where it runs ----

    def _workspace(self, label: str | None) -> Path:
        """The session's workspace: its scratch directory, the only path the
        profile opens. Every session gets one — a val reader's is readable
        only — holding the link Codex finds the skills through, and the
        instructions file."""
        name = label or "session"
        root = (
            self._run_dir / "scratch"
            if self._run_dir is not None
            else Path(tempfile.gettempdir()) / "kiln-harness-tuner-codex"
        )
        workspace = root / name
        workspace.mkdir(parents=True, exist_ok=True)
        link = workspace / SKILLS_LINK
        if not link.is_symlink() and not link.exists():
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(self._skills_dir.resolve(), target_is_directory=True)
        if self._instructions.is_file():
            shutil.copyfile(self._instructions, workspace / "AGENTS.md")
        return workspace

    def _cwd(self, label: str | None) -> Path:
        return self._workspace(label)

    def _trace_reads(self, agent: str) -> list[Path]:
        """The trace folder, for a role that reads traces."""
        if self._run_dir is None or agent not in TRACE_READER_ROLES:
            return []
        folder = traces_dir(self._run_dir.resolve())
        folder.mkdir(parents=True, exist_ok=True)
        return [folder]

    # ---- the command ----

    @staticmethod
    def _mcp_env() -> dict[str, str]:
        """The run's uv and Python settings, for each MCP server. Codex starts
        a server with a small default environment, not the session's, and the
        gateway runs under `uv run`: without these, a `UV_FROZEN` or a
        `UV_PROJECT_ENVIRONMENT` the run was started with does not reach it."""
        return {
            name: value
            for name, value in os.environ.items()
            if name.startswith(MCP_ENV_PREFIXES)
        }

    def _mcp_servers(self, agent: str) -> dict[str, dict[str, Any]]:
        if (
            agent in NO_TOOL_ROLES
            or agent == CLI_CHECK_ROLE
            or self._mcp_config_path is None
        ):
            return {}
        path = self._mcp_config_path
        role_path = path.parent / f"mcp_config.{agent}.json"
        if role_path.exists():
            path = role_path
        servers = json.loads(path.read_text()).get("mcpServers", {})
        out: dict[str, dict[str, Any]] = {}
        for name, spec in servers.items():
            out[name] = {
                "command": spec["command"],
                "args": list(spec.get("args", [])),
                "env": self._mcp_env(),
                # Only the role's own tools, and no approval prompt: a
                # `codex exec` session has no one to ask.
                "enabled_tools": mcp_tool_names(agent, name),
                "default_tools_approval_mode": "approve",
                "startup_timeout_sec": MCP_STARTUP_TIMEOUT_SECS,
                "tool_timeout_sec": MCP_TOOL_TIMEOUT_SECS,
            }
        return out

    def _argv(
        self,
        agent: str,
        resume_session_id: str | None = None,
        session_id: str | None = None,
        label: str | None = None,
    ) -> list[str]:
        workspace = self._workspace(label)
        writable = agent not in READ_ONLY_ROLES
        config: list[tuple[str, Any]] = [
            (
                f"permissions.{PROFILE}",
                permission_profile(
                    self._skills_dir,
                    writable=writable,
                    install_paths=self._install_paths,
                    read_paths=self._trace_reads(agent),
                ),
            ),
            ("default_permissions", PROFILE),
            ("approval_policy", "never"),
            ("web_search", "disabled"),
            ("model_reasoning_effort", self._effort_for(agent)),
        ]
        config += [
            (f"mcp_servers.{name}", spec)
            for name, spec in self._mcp_servers(agent).items()
        ]
        options = [
            *self._cli_options,
            "--json",
            "--ignore-user-config",
            "--strict-config",
            "--skip-git-repo-check",
        ]
        for feature in DISABLED_FEATURES:
            options += ["--disable", feature]
        for key, value in config:
            options += ["-c", f"{key}={toml_value(value)}"]
        model = self._model_for(agent)
        if model:
            options += ["-m", model]
        if resume_session_id:
            # `resume` takes no `-C`: the runner's cwd is the workspace.
            return [
                self._codex_bin,
                "exec",
                "resume",
                *options,
                resume_session_id,
                "-",
            ]
        return [self._codex_bin, "exec", *options, "-C", str(workspace), "-"]

    # ---- what it printed ----

    def _parse_cli_output(self, stdout: str) -> CliRound | None:
        return read_codex_output(stdout)

    def _transcript_source(self, session_id: str) -> Path | None:
        matches = sorted(
            self._codex_sessions_dir.glob(f"*/*/*/rollout-*-{session_id}.jsonl")
        )
        return matches[-1] if matches else None

    def _recover_timed_out_usage(
        self, label: str, session_id: str, elapsed_secs: float
    ) -> Usage:
        # The id was not chosen before the spawn, so the killed session's
        # transcript cannot be named: nothing to recover, nothing to resume.
        return Usage(wall_secs=elapsed_secs)
