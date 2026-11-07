"""`harness-tuner …` — the documented interface (a CLI,
invoked manually from a terminal).

Wires `Ctx` (dispatcher, KilnClient, Analyzer, MemoryWriter) plus the
observability: run id, wave log (`<state>/runs/<run_id>/`, run scratch),
`RunStatus` tracker + UI (rich live display, or plain lines under `--no-ui` /
non-TTY), and the report hook (`<state>/reports/kiln-harness-tuner-<run_id>.md`)
— then runs `orchestrate`, which returns the report path. `<state>` is the
state folder (`paths.py`); the agents' skills and instructions come from the
package and are installed into it (`install_agents.py`).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import json
import logging
import os
import shutil
import signal
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from kiln_harness_tuner.analysis import AnalysisError, Analyzer
from kiln_harness_tuner.dispatch.agent_cli import (
    DEFAULT_AGENT_CLI,
    AgentCli,
    parse_agent_cli,
)
from kiln_harness_tuner.dispatch.claude_cli import (
    AGENT_MODEL,
    AGENT_MODEL_DEFAULTS,
    AGENT_TIMEOUTS_SECS,
    READ_ONLY_ROLES,
    ClaudeCliDispatcher,
    PreflightError,
)
from kiln_harness_tuner.dispatch.codex_cli import CodexCliDispatcher
from kiln_harness_tuner.dispatch.opencode_cli import OpenCodeCliDispatcher
from kiln_harness_tuner.gateway.hook import MCP_MATCHER, SKILL_INSTALLER_TOOLS
from kiln_harness_tuner.gateway.hook import hook_command as gateway_hook_command
from kiln_harness_tuner.gateway.ops import GATEWAY_SERVER, ROLE_OPS, TRACE_READER_ROLES
from kiln_harness_tuner.gateway.trace_files import traces_dir
from kiln_harness_tuner.gateway.trainset import TrainSetError
from kiln_harness_tuner.install_agents import (
    InstallError,
    install_for_run,
    package_version,
)
from kiln_harness_tuner.kiln import DEFAULT_KILN_HOST, KilnClient, KilnError
from kiln_harness_tuner.logbook import configure_run_logging
from kiln_harness_tuner.memory import (
    BaselineRecordMissingError,
    MemoryWriter,
    MemoryWriteRefused,
)
from kiln_harness_tuner.memory_schema import RecordKindError
from kiln_harness_tuner.model_policy import ModelPolicy, ModelPolicyError
from kiln_harness_tuner.orchestrator import (
    DEFAULT_COVERAGE_FLOOR,
    Ctx,
    SpendMeter,
    orchestrate,
)
from kiln_harness_tuner.paths import (
    StateHomeError,
    StatePaths,
    check_no_overlap,
    prepare_state,
    state_home,
)
from kiln_harness_tuner.quarantine import QuarantineError
from kiln_harness_tuner.reference_check import REFERENCE_CHECK_MODES
from kiln_harness_tuner.report import ReportWriter
from kiln_harness_tuner.schemas import TargetBlock
from kiln_harness_tuner.setup import SetupError
from kiln_harness_tuner.skill_installer import sweep_staging
from kiln_harness_tuner.status import RunStatusTracker, make_run_id
from kiln_harness_tuner.ui import LiveUi, PlainUi
from kiln_harness_tuner.wave_log import FanoutLog, WaveLog

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MINS = 180
# Concurrency defaults: bounded by default — the
# unbounded gathers were what manufactured provider 429 storms ("Kiln Harness Tuner's
# own parallelism creates the limit that stalls it"). 4 concurrent eval jobs × ~a few
# conversations each sits comfortably under every provider quota the runs have hit;
# raise per run when the provider allows.
DEFAULT_MAX_CONCURRENT_EVALS = 4
DEFAULT_MAX_CONCURRENT_AGENTS = 4

# The adapter for each agent CLI `--agent-cli` can name, and the keyword
# its executable is passed under.
DISPATCHERS: dict[str, tuple[type[ClaudeCliDispatcher], str]] = {
    "claude": (ClaudeCliDispatcher, "claude_bin"),
    "codex": (CodexCliDispatcher, "codex_bin"),
    "opencode": (OpenCodeCliDispatcher, "opencode_bin"),
}


def make_dispatcher(
    agent_cli: AgentCli, target: TargetBlock, agents_dir: Path, **kwargs: Any
) -> ClaudeCliDispatcher:
    """The dispatcher `--agent-cli` names, with its options, model and
    effort. `agents_dir` is the folder `install_for_run` installed that CLI's
    skills and instructions into."""
    cls, bin_kw = DISPATCHERS[agent_cli.kind]
    return cls(
        target,
        agents_dir,
        **{bin_kw: agent_cli.bin},
        cli_options=agent_cli.options,
        default_model=agent_cli.model,
        default_effort=agent_cli.effort,
        **kwargs,
    )


async def check_agent_cli(
    agent_cli: AgentCli,
    target: TargetBlock,
    agents_dir: Path,
    models: dict[str, str] | None = None,
    efforts: dict[str, str] | None = None,
) -> None:
    """Refuse the start when the agent CLI does not answer one short
    prompt — a clear message now, not an `agent_crash` on every session
    later. Raises PreflightError."""
    if shutil.which(agent_cli.bin) is None:
        raise PreflightError(
            f"--agent-cli {agent_cli.text!r}: `{agent_cli.bin}` is not on PATH."
            "\nfix: install the CLI, or name it by its full path."
        )
    dispatcher = make_dispatcher(
        agent_cli, target, agents_dir, models=models, efforts=efforts
    )
    await dispatcher.check_cli(agent_cli.text)


# Removed flags, each with the one line that an old command line
# gets in place of argparse's "unrecognized arguments".
REMOVED_FLAGS = {
    "--max-request-cost": (
        "--max-request-cost was removed: --model-policy keeps"
        " costly models out, and the cost of each arm per request is still"
        " reported"
    ),
    "--max-request-latency": (
        "--max-request-latency was removed: use --objective"
        " speed when latency is the goal; the latency of each arm per request"
        " is still reported"
    ),
}


class _Parser(argparse.ArgumentParser):
    """An ArgumentParser that names a removed flag and gives the reason."""

    def parse_known_args(  # type: ignore[override]
        self, args: Sequence[str] | None = None, namespace: Any = None
    ) -> tuple[argparse.Namespace, list[str]]:
        given = sys.argv[1:] if args is None else list(args)
        for arg in given:
            if arg == "--":
                break
            message = REMOVED_FLAGS.get(arg.split("=", 1)[0])
            if message is not None:
                self.error(message)
        return super().parse_known_args(args, namespace)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="harness-tuner",
        description=(
            "Kiln Harness Tuner\n"
            "\n"
            "Automatically improves an AI agent by tuning its harness: the prompt,\n"
            "model, tools, skills and settings around it. Agents propose and build\n"
            "each change, and your evals measure it. A change becomes the new\n"
            "baseline only when held-out data confirms it, and the test set is\n"
            "never read, so your test score stays a true measure."
        ),
        # Keep the title on its own line and the description as wrapped above.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project-path",
        required=True,
        help="on-disk Kiln project dir (for agents exploring .kiln files)",
    )
    parser.add_argument("--project-id", required=True, help="Kiln project id")
    parser.add_argument("--task-id", required=True, help="Kiln task id")
    parser.add_argument(
        "--kiln-host",
        default=DEFAULT_KILN_HOST,
        help=f"running Kiln app (default {DEFAULT_KILN_HOST})",
    )
    parser.add_argument(
        "--guidance",
        default="",
        help="kickoff human guidance, passed verbatim to every wave's planner",
    )
    parser.add_argument(
        "--timeout-mins",
        type=int,
        default=DEFAULT_TIMEOUT_MINS,
        help=f"time budget, wave-boundary checked (default {DEFAULT_TIMEOUT_MINS})",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="plain log lines instead of the live display (V1)",
    )
    parser.add_argument(
        "--budget-usd",
        type=float,
        default=None,
        help="cost ceiling for the run (both lanes); at the ceiling the run"
        " exits, one projected wave short of it the next wave is the wrap-up"
        " (default: no ceiling)",
    )
    parser.add_argument(
        "--coverage-floor",
        type=float,
        default=DEFAULT_COVERAGE_FLOOR,
        help="promotion-gate val coverage floor: a candidate whose fills sit"
        f" below this is ineligible this wave (default {DEFAULT_COVERAGE_FLOOR})",
    )
    parser.add_argument(
        "--max-concurrent-evals",
        type=int,
        default=DEFAULT_MAX_CONCURRENT_EVALS,
        help="max eval jobs in flight at once, run-wide — the rate-limit"
        f" knob (default {DEFAULT_MAX_CONCURRENT_EVALS}; 0 = unlimited)",
    )
    parser.add_argument(
        "--agent-cli",
        default=DEFAULT_AGENT_CLI,
        metavar="COMMAND",
        help="the agent CLI every loop session runs, as the whole command you"
        f" would type, not just its name: {DEFAULT_AGENT_CLI!r} (the default),"
        " 'codex exec --model MODEL' or 'opencode run --model PROVIDER/MODEL"
        " --variant high'. Its model and effort are the run-wide defaults under"
        " --agent-model/--agent-effort."
        " It must already be signed in; one short test prompt runs before the"
        " run starts. docs/agent-cli.md says what each one fences",
    )
    parser.add_argument(
        "--max-concurrent-agents",
        type=int,
        default=DEFAULT_MAX_CONCURRENT_AGENTS,
        help="max `claude -p` sessions in flight at once"
        f" (default {DEFAULT_MAX_CONCURRENT_AGENTS}; 0 = unlimited)",
    )
    parser.add_argument(
        "--agent-model",
        action="append",
        default=[],
        metavar="ROLE=MODEL",
        help="per-role model override, e.g. --agent-model describe_eval=haiku"
        f" (repeatable; default {AGENT_MODEL}"
        + "".join(f", {r} {m}" for r, m in sorted(AGENT_MODEL_DEFAULTS.items()))
        + "; roles: "
        + ", ".join(sorted(AGENT_TIMEOUTS_SECS))
        + ")",
    )
    parser.add_argument(
        "--agent-effort",
        action="append",
        default=[],
        metavar="ROLE=EFFORT",
        help="per-role reasoning-effort override, e.g."
        " --agent-effort report_writer=medium (repeatable)",
    )
    parser.add_argument(
        "--no-baseline-audit",
        action="store_true",
        help="skip the audit of the baseline's own prompt, skills and code"
        " tools for train-specific text (an exact-match search, then one"
        " review session; once per baseline, and a leak becomes a scrub"
        " issue)",
    )
    parser.add_argument(
        "--reference-check",
        choices=REFERENCE_CHECK_MODES,
        default="warn",
        help="what to do with a build whose prompt or skills name a skill or a"
        " tool it does not attach: warn ="
        " record the findings and measure (default); refuse = one rebuild,"
        " then a dead end, like a generality leak; off = do not check",
    )
    parser.add_argument(
        "--no-recon",
        action="store_true",
        help="skip the setup reconnaissance battery (the code probes on the"
        " baseline — tool call, skill read, alternate output mode, thinking,"
        " json validity, output on the longest inputs — run as wave 0 before"
        " the first plan)",
    )
    parser.add_argument(
        "--gate-agent",
        choices=("auto", "always"),
        default="auto",
        help="who decides a one-candidate promotion gate: auto = the code rule"
        " (the promote agent is dispatched only to rank two or more"
        " candidates), always = dispatch the agent for every gate",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="opt-in hard turn cap per agent session (default: uncapped —"
        " the per-role wall-clock timeouts bound runaways; long sessions in"
        " the trace corpus were productive, not loops)",
    )
    parser.add_argument(
        "--objective",
        choices=OBJECTIVES,
        default="quality",
        help="what the loop optimises: quality = the eval scores (default);"
        " speed = per-request wall-clock latency, promoted only when clearly"
        " faster on val (paired, median and p90) with no eval regressed"
        " significantly",
    )
    parser.add_argument(
        "--min-effect",
        type=_fraction,
        default=None,
        metavar="FRACTION",
        help="the smallest improvement you would act on, as a share of an"
        " eval's items (0.1 = 10%%). The pre-run check then says per eval"
        " whether the best case can show it, and refuses the run when no"
        " eval that can drive a promotion can show it on val",
    )
    parser.add_argument(
        "--allow-underpowered",
        action="store_true",
        help="run even when the pre-run detectability check refuses (no eval"
        " can show a significant val win); the refusal still prints and lands"
        " in the report",
    )
    parser.add_argument(
        "--noise-check",
        type=_positive_int,
        default=0,
        metavar="N",
        help="before wave 1, re-drive and re-judge the baseline on N train"
        " items per eval and report how often the same config scores"
        " differently (paid, estimated before it spends; default off)",
    )
    parser.add_argument(
        "--model-policy",
        default=None,
        metavar="PATH",
        help="JSON file of allowed/denied (provider, model) pairs for the task's"
        " run configs, enforced on every run config an agent creates and"
        " before any rc is measured or promoted (default: no policy — every"
        " model allowed). See examples/model-policy.json",
    )
    parser.add_argument(
        "--skip-model-preflight",
        action="store_true",
        help="skip the model preflight: before the baseline measurement, one"
        " short test call per model the run may use (the baseline's, and the"
        " allowed models the planner can pick), plus a context-window check."
        " A model that fails is flagged and not screened this run; a failing"
        " baseline model stops the start",
    )
    return parser


OBJECTIVES = ("quality", "speed", "cost")


def _fraction(text: str) -> float:
    """`--min-effect`: a share of items, above 0 and at most 1."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not 0.0 < value <= 1.0:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a fraction above 0 and at most 1 (0.1 = 10%)"
        )
    return value


def _positive_int(text: str) -> int:
    """`--noise-check`: at least one item."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"{text!r} is below 1")
    return value


def parse_role_overrides(pairs: Sequence[str], flag: str) -> dict[str, str]:
    """`ROLE=VALUE` pairs → dict, validated against the known agent roles so
    a typo fails at startup instead of silently keeping the default."""
    overrides: dict[str, str] = {}
    for pair in pairs:
        role, sep, value = pair.partition("=")
        if not sep or not value:
            raise ConfigRenderError(
                f"{flag} expects ROLE=VALUE, got {pair!r}.\n"
                f"fix: pass one role and one value joined by '=', e.g."
                f" {flag} {sorted(AGENT_TIMEOUTS_SECS)[0]}=<value>."
            )
        if role not in AGENT_TIMEOUTS_SECS:
            raise ConfigRenderError(
                f"{flag}: unknown role {role!r}.\n"
                f"fix: name one of the agent roles — "
                f"{', '.join(sorted(AGENT_TIMEOUTS_SECS))}."
            )
        overrides[role] = value
    return overrides


def load_model_policy(path: str | None) -> ModelPolicy | None:
    """`--model-policy`, loaded and validated at start-up — a bad file is a
    start-up error with the fix, never a policy silently not applied."""
    if path is None:
        return None
    try:
        return ModelPolicy.load(Path(path).expanduser())
    except ModelPolicyError as exc:
        raise ConfigRenderError(
            f"--model-policy: {exc}.\n"
            "fix: correct the file (examples/model-policy.json shows the shape),"
            " or drop --model-policy to allow every model."
        ) from exc


def target_from_args(args: argparse.Namespace) -> TargetBlock:
    return TargetBlock(
        project_path=args.project_path,
        project_id=args.project_id,
        task_id=args.task_id,
        kiln_host=args.kiln_host,
    )


class ConfigRenderError(RuntimeError):
    """Per-run config rendering failed at startup — instructive, not a
    traceback (same treatment as the other startup errors)."""


@dataclass(frozen=True)
class ReadFence:
    """What the sandbox and the Read tool may not open (`deny`) and what is
    re-opened inside a denied tree (`allow`)."""

    deny: tuple[Path, ...]
    allow: tuple[Path, ...]

    def rule_paths(self) -> tuple[Path, ...]:
        """The paths for path-scoped `Read(...)` deny rules. Permission
        rules have no allow-over-deny (a `Read(...)` deny beats every
        allow, and Bash's `cat`/`head`/`python … open()` of a file is
        checked against the same rules), so a denied tree that contains an
        allowed path is replaced by its entries minus the allowed ones —
        recursively, down the path to each allowed one, files included
        (with the run's scratch re-opened inside the run directory, the wave
        log BESIDE it is a file, and one level of directories would leave it
        readable)."""
        out: list[Path] = []

        def expand(tree: Path) -> None:
            if tree in self.allow:
                return
            inside = [a for a in self.allow if tree in a.parents]
            if not inside:
                out.append(tree)
                return
            for child in sorted(tree.iterdir()) if tree.is_dir() else []:
                expand(child)

        for tree in self.deny:
            expand(tree)
        return tuple(out)


def read_fence_for(
    *,
    state: StatePaths,
    project_root: Path,
    run_dir: Path,
    agents_dir: Path,
    home: Path,
) -> ReadFence:
    """The read fence for one run.

    Denied: the run's OWN Kiln project, whole — it holds the eval
    inputs of every split, every task run and every eval run, so a file read
    there is a read of val and test; the agents reach the project through their
    gateway operations, which answer from train records only, and through
    code-assembled inputs. Every run's directory in the state folder — this
    run's too, except its scratch: the wave log, the step records and the
    memory snapshots carry val tables. The traces and the reports. Every other
    Claude session's transcripts.

    Readable: this run's scratch, and the skills and `CLAUDE.md` that
    `install_for_run` installs for Claude Code in the agents folder. A project
    skill's files reach a builder through the skill installer's
    `export_skill_bundle`, which copies them into scratch.

    The fence is a deny list, so what nobody names is readable: other Kiln
    projects, and any clone of this repository with its `examples/`
    (docs/limitations.md)."""
    deny = [
        project_root,
        state.runs,
        state.traces,
        state.reports,
        home / ".claude" / "projects",
    ]
    allow = [
        run_dir / "scratch",
        agents_dir / ".claude" / "skills",
        agents_dir / "CLAUDE.md",
    ]
    return ReadFence(deny=tuple(deny), allow=tuple(allow))


# Everything the orchestrator writes into a run's directory, created before the
# read fence is computed: a `Read(...)` deny over the run directory
# has to name its entries one by one (the scratch inside it is re-opened, and a
# deny rule has no allow override). An entry not listed here is still denied
# from the next session on: the dispatcher re-reads the directory before every
# session (`dispatch.run_fence`). Directories are listed with a trailing slash.
RUN_DIR_ENTRIES = (
    "steps/",
    "snapshots/",
    "wave_log.jsonl",
    "kiln-harness-tuner.log",
    "mcp_config.json",
    "settings.json",
    "settings.readonly.json",
    "train_set.json",
    "gateway_created.jsonl",
    "val_summary.json",
    "model_policy.json",
)


def prepare_run_dir(run_dir: Path) -> None:
    """Create every `RUN_DIR_ENTRIES` entry that does not exist yet, so the
    read fence denies it from the first dispatch on."""
    (run_dir / "scratch").mkdir(parents=True, exist_ok=True)
    for entry in RUN_DIR_ENTRIES:
        path = run_dir / entry.rstrip("/")
        if entry.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
        elif not path.exists():
            path.write_text("")


GATEWAY_MODULE = "kiln_harness_tuner.gateway.server"
INSTALLER_MODULE = "kiln_harness_tuner.skill_installer.mcp_server"


def python_command() -> str:
    """The interpreter running the tuner, which starts the run's MCP servers:
    the same code as their console scripts, with no `uv` and no checkout
    needed."""
    python = sys.executable
    if not python or not Path(python).is_absolute():
        raise ConfigRenderError(
            "cannot start the run's MCP servers: the Python interpreter running"
            f" harness-tuner is unknown (sys.executable is {python!r}).\n"
            "fix: run harness-tuner from its installed command."
        )
    return python


def python_module_command(module: str) -> list[str]:
    """`<python> -P -m <module>`. `-m` puts the current folder first on
    `sys.path`, and Codex and OpenCode start an MCP server in the session's
    workspace, which the agent writes: a planted `json.py` there would run
    inside the server, outside every fence. `-P` leaves the current folder
    out."""
    return [python_command(), "-P", "-m", module]


def render_run_configs(
    run_dir: Path,
    target: TargetBlock,
    *,
    state: StatePaths,
    agents_dir: Path,
    model_policy: Path | None = None,
    max_concurrent_evals: int | None = None,
) -> tuple[Path, Path]:
    """Render the per-run MCP configs and settings the dispatcher passes to
    every `claude -p`. `state` is the state folder the run lives in, and
    `agents_dir` the folder its sessions read skills and instructions from
    (for Claude Code, also their working folder).

    MCP: loop agents never get a generic Kiln API tool. Each role gets
    `mcp_config.<role>.json` holding ONE Kiln server, the loop gateway
    (`kiln_harness_tuner.gateway`), started with `--role <role>` so it serves that
    role's operations only — plus, for the builder and the probe, the skill
    installer. `mcp_config.json` is the preflight's (the gateway's `ping`).
    The installed `.mcp.json` and its `kiln_api_mcp` server are for
    interactive sessions and are never rendered into a loop session.

    Settings: the write sandbox (file tools scoped to the run's scratch tree
    via permission rules; Bash OS-sandboxed the same way), one file per role
    (`settings.<role>.json`) whose hook admits only that role's MCP tools,
    and `settings.readonly.json` for the val readers (no write grant).
    `model_policy` = the copy of `--model-policy` inside the run directory,
    which the gateway reads. `max_concurrent_evals` caps the eval jobs each
    gateway process runs at once (its drives through eval jobs); None or 0
    = no cap."""
    run_dir.mkdir(parents=True, exist_ok=True)
    prepare_run_dir(run_dir)

    scratch_root = (run_dir / "scratch").resolve()
    # The gateway writes the train traces here, and the roles that read them
    # get an explicit read allow (`gateway.trace_files`).
    traces_root = traces_dir(run_dir.resolve())
    traces_root.mkdir(parents=True, exist_ok=True)
    project_root = Path(target.project_path).expanduser().resolve()
    # Resolved like the run directory, so the read fence's trees and the
    # scratch it re-opens inside them compare as paths.
    state = StatePaths(state.root.resolve())
    agents_dir = agents_dir.resolve()
    read_fence = read_fence_for(
        state=state,
        project_root=project_root,
        run_dir=run_dir.resolve(),
        agents_dir=agents_dir,
        home=Path.home(),
    )
    gateway_command = python_module_command(GATEWAY_MODULE)

    def gateway_server(role: str) -> dict[str, Any]:
        # Runs OUTSIDE the agent sandbox, like the installer: it is the
        # loop's own code, the one lane to Kiln, and it reads the run's
        # train set and model policy from the run directory.
        return {
            "type": "stdio",
            "command": gateway_command[0],
            "args": [
                *gateway_command[1:],
                "--role",
                role,
                "--run-dir",
                str(run_dir.resolve()),
                "--kiln-host",
                target.kiln_host,
                "--project-id",
                target.project_id,
                "--task-id",
                target.task_id,
                "--project-path",
                str(project_root),
                "--traces-dir",
                str(traces_root),
                *(
                    ["--max-concurrent-evals", str(max_concurrent_evals)]
                    if max_concurrent_evals
                    else []
                ),
            ],
        }

    # The skill-bundle installer is run-scoped: its args (project + scratch
    # roots) only exist per run. It runs OUTSIDE the agent sandbox — agents
    # author a bundle in scratch and this trusted lane installs it
    # atomically (see kiln_harness_tuner.skill_installer). Only the builder and the
    # probe get it.
    installer_command = python_module_command(INSTALLER_MODULE)
    installer = {
        "type": "stdio",
        "command": installer_command[0],
        "args": [
            *installer_command[1:],
            "--project-path",
            str(project_root),
            "--scratch-root",
            str(scratch_root),
        ],
    }

    def write_mcp(path: Path, role: str) -> None:
        servers: dict[str, Any] = {GATEWAY_SERVER: gateway_server(role)}
        if role in SKILL_INSTALLER_TOOLS:
            servers["skill_installer"] = installer
        path.write_text(
            json.dumps({"mcpServers": servers}, indent=2, ensure_ascii=False) + "\n"
        )

    mcp_config_path = run_dir / "mcp_config.json"
    write_mcp(mcp_config_path, "preflight")
    for role in ROLE_OPS:
        write_mcp(run_dir / f"mcp_config.{role}.json", role)

    read_deny = [
        rule
        for p in read_fence.rule_paths()
        for rule in (
            (f"Read(//{str(p).lstrip('/')}/**)",)
            if p.is_dir() or not p.exists()
            else (f"Read(//{str(p).lstrip('/')})",)
        )
    ]

    def hook(role: str) -> dict:
        # Only the role's own MCP tools are callable (`gateway.hook`). The
        # shared settings files carry role "none": a session without its
        # role's file can call no MCP tool at all.
        return {
            "PreToolUse": [
                {
                    "matcher": MCP_MATCHER,
                    "hooks": [
                        {
                            "type": "command",
                            "command": gateway_hook_command(role),
                            "timeout": 10,
                        }
                    ],
                }
            ]
        }

    settings = {
        # File tools use the permission system, not the OS sandbox: one
        # path-scoped Edit allow rule is the ONLY write grant — Edit rules
        # cover ALL file-editing tools including Write/new-file creation,
        # while path-scoped Write(...) rules are never consulted (docs
        # v2.1.210+). Rule syntax: `//` replaces the leading `/` of an
        # absolute path (`Edit(//home/…)` → `/home/…`). In `claude -p` any
        # non-allowed write is denied, no prompt.
        "permissions": {
            "allow": [
                f"Edit(//{str(scratch_root).lstrip('/')}/**)",
            ],
            # Close the Write-tool leak: the scratch allow doesn't DENY
            # elsewhere, and the Write tool was getting through into the Kiln
            # project tree (agents hand-built skill references there, stray
            # undeletable files included). Deny beats the allow above; scratch
            # lives in the state folder, disjoint from the project tree
            # (`paths.check_no_overlap`), so the allow is unaffected. The only
            # remaining lane into skills/ is the skill_installer MCP server
            # (unsandboxed trusted code, atomic installs).
            "deny": [
                f"Edit(//{str(project_root).lstrip('/')}/**)",
                # The trace folder is the gateway's: agents read
                # it, and only the gateway writes it.
                f"Edit(//{str(traces_root).lstrip('/')}/**)",
                # The agents folder is the session's working folder, which
                # Claude Code lets the file tools write by default. Its skills
                # and instructions are read by every later session and run.
                f"Edit(//{str(agents_dir).lstrip('/')}/**)",
                # No internet, and the file tools see the same
                # fence as Bash below.
                "WebFetch",
                "WebSearch",
                # No generic Kiln API tool in a loop session, by name too.
                "mcp__kiln_api_mcp",
                *read_deny,
            ],
        },
        # Bash and its children ride the OS sandbox. The default sandbox
        # writable area is the cwd (= the agents folder, inside the state
        # folder), so the whole state folder is denied and only the scratch
        # tree re-opened. Behavioral assertion (deny outside / allow inside)
        # is checked by a test.
        "sandbox": {
            "enabled": True,
            "autoAllowBashIfSandboxed": True,
            # Loud failure over silent unsandboxed fallback: the hazards
            # (memory-store tampering, uncontrolled writes) must not
            # come back just because a dependency is missing.
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "filesystem": {
                "denyWrite": [str(state.root)],
                "allowWrite": [str(scratch_root)],
                # Read fence: an agent reads the skills it loads and its own
                # run scratch — never its Kiln project, other runs' logs and
                # traces, reports, or another Claude session's transcripts.
                # An agent can read a file under ~/.claude/projects, which is
                # why that one is listed.
                "denyRead": [str(p) for p in read_fence.deny],
                "allowRead": [str(p) for p in read_fence.allow],
            },
            # Outbound network: none. Bash inside the sandbox cannot curl the
            # internet, and cannot curl the Kiln API either — the gateway
            # operations are the only lane to Kiln.
            "network": {
                "strictAllowlist": True,
                "allowedDomains": [],
            },
        },
        # The gateway hook with no role: it refuses every MCP
        # tool. Each role's own settings arm it for that role, so a session
        # can call its own gateway operations and nothing else. Exit code 2
        # blocks the call and hands the message to the model.
        "hooks": hook("none"),
    }
    settings_path = run_dir / "settings.json"
    settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")

    # The val readers: the same read fence and sandbox, no write
    # grant at all, and the hook in read-only mode. What they hold is val
    # data; nothing they could write may reach a later agent.
    readonly = json.loads(json.dumps(settings))
    readonly["permissions"]["allow"] = []
    readonly["permissions"]["deny"].insert(
        0, f"Edit(//{str(scratch_root).lstrip('/')}/**)"
    )
    readonly["permissions"]["deny"].insert(
        0, f"Edit(//{str(state.root).lstrip('/')}/**)"
    )
    readonly["sandbox"]["filesystem"]["allowWrite"] = []
    readonly["hooks"] = hook("none")
    readonly_path = run_dir / "settings.readonly.json"
    readonly_path.write_text(json.dumps(readonly, indent=2, ensure_ascii=False) + "\n")
    for role in ROLE_OPS:
        per_role = json.loads(
            json.dumps(readonly if role in READ_ONLY_ROLES else settings)
        )
        per_role["hooks"] = hook(role)
        if role in TRACE_READER_ROLES:
            # An explicit allow for the trace folder, so neither
            # the read fence nor auto mode refuses a Read of a trace file.
            # A Read rule covers Grep and Glob too: Claude Code applies Read
            # rules to every built-in tool that reads files, and a Grep(…)
            # or Glob(…) path rule never matches.
            per_role["permissions"]["allow"].append(
                f"Read(//{str(traces_root).lstrip('/')}/**)"
            )
        (run_dir / f"settings.{role}.json").write_text(
            json.dumps(per_role, indent=2, ensure_ascii=False) + "\n"
        )
    return mcp_config_path, settings_path


def build_ctx(
    target: TargetBlock,
    kiln: KilnClient,
    *,
    state: StatePaths,
    agents_dir: Path,
    run_id: str,
    run_dir: Path,
    mcp_config_path: Path,
    settings_path: Path,
    models: dict[str, str] | None = None,
    efforts: dict[str, str] | None = None,
    max_concurrent_agents: int | None = DEFAULT_MAX_CONCURRENT_AGENTS,
    max_concurrent_evals: int | None = DEFAULT_MAX_CONCURRENT_EVALS,
    coverage_floor: float = DEFAULT_COVERAGE_FLOOR,
    budget_usd: float | None = None,
    max_turns: int | None = None,
    run_started_at: datetime | None = None,
    gate_agent: str = "auto",
    recon: bool = True,
    baseline_audit: bool = True,
    reference_check: str = "warn",
    objective: str = "quality",
    model_policy: ModelPolicy | None = None,
    agent_cli: AgentCli | None = None,
    min_effect: float | None = None,
    allow_underpowered: bool = False,
    noise_check_items: int = 0,
    model_preflight: bool = True,
) -> Ctx:
    return Ctx(
        run_dir=run_dir,
        max_concurrent_agents=max_concurrent_agents or None,
        dispatch=make_dispatcher(
            agent_cli or parse_agent_cli(DEFAULT_AGENT_CLI),
            target,
            agents_dir,
            run_id=run_id,
            run_dir=run_dir,
            mcp_config_path=mcp_config_path,
            settings_path=settings_path,
            traces_root=state.traces,
            models=models,
            efforts=efforts,
            max_concurrent=max_concurrent_agents or None,
            max_turns=max_turns,
            readonly_settings_path=settings_path.parent / "settings.readonly.json",
        ),
        kiln=kiln,
        memory=MemoryWriter(kiln, project_id=target.project_id, task_id=target.task_id),
        analyzer=Analyzer(
            kiln,
            project_id=target.project_id,
            task_id=target.task_id,
            max_concurrent_jobs=max_concurrent_evals or None,
            run_started_at=run_started_at,
        ),
        target=target,
        coverage_floor=coverage_floor,
        budget_usd=budget_usd,
        spend=SpendMeter(),
        gate_agent=gate_agent,
        recon=recon,
        baseline_audit=baseline_audit,
        reference_check=reference_check,
        measure_baseline_first=True,
        detectability_check=True,
        min_effect=min_effect,
        allow_underpowered=allow_underpowered,
        noise_check_items=noise_check_items,
        model_preflight=model_preflight,
        opening_screen=True,
        objective=objective,
        model_policy=model_policy,
        # A tree the read fence denies: `runs/` is denied except this run's
        # scratch, so `runs/.quarantine/` is unreadable to every agent.
        quarantine_root=state.quarantine(target.project_id, target.task_id),
        train_set_path=run_dir / "train_set.json",
    )


class RunLockError(RuntimeError):
    """Another harness-tuner run is live against the same task (two concurrent
    runs race the baseline record and the memory store)."""


def _unique_run_id(runs_root: Path, now: datetime) -> str:
    """The run id, made collision-proof: the id is minute-granular, and
    two runs started in the same minute would APPEND into one
    wave_log.jsonl, with two run_starts and different budgets. A taken id
    gets a `-2`/`-3` suffix instead — and the id is CLAIMED by creating its
    directory here: an exists-check alone lets two runs on different tasks
    (the lock is per task) pick the same id in the same minute and share one
    wave log."""
    base = make_run_id(now)
    run_id = base
    n = 1
    while True:
        try:
            (runs_root / run_id).mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            n += 1
            run_id = f"{base}-{n}"
            continue
        return run_id


def _lock_holder_pid(lock_path: Path) -> int | None:
    """The pid a lock file names: the JSON record this code writes, or the
    bare pid an older lock carried. Garbage reads as no holder."""
    try:
        text = lock_path.read_text().strip()
    except OSError:
        return None
    try:
        record = json.loads(text)
        if isinstance(record, dict) and isinstance(record.get("pid"), int):
            return record["pid"]
    except ValueError:
        pass
    try:
        return int(text)
    except ValueError:
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


@dataclass
class RunLock:
    """The held one-run-per-task lock: the file's path and the descriptor
    that holds the `flock`. `release` drops the flock and closes the fd; the
    file itself is never unlinked (see `_acquire_run_lock`)."""

    path: Path
    fd: int

    def release(self) -> None:
        if self.fd < 0:
            return
        with contextlib.suppress(OSError):
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(self.fd)
        self.fd = -1


def _acquire_run_lock(
    runs_root: Path, task_id: str, project_id: str | None = None
) -> RunLock:
    """One live run per task (enforced). The lock is a kernel
    `flock` on `.lock-<key>`: the kernel releases it when
    the holder dies, so there is no stale-lock reclaim step — and no reclaim
    race. The O_EXCL + unlink scheme before it let two racers each unlink a
    "stale" file and both proceed, and a reader that saw the file EMPTY (the
    holder had created but not yet written it) reclaimed a live lock.

    The file is created once and never unlinked: unlinking a locked file
    lets a third run create a fresh inode under the same name and lock that
    one while the second still holds the old inode. The record (pid, start
    time, ids) is written for humans and for an older kiln-harness-tuner that only reads
    a pid; a live pid in the record is refused even without a flock, so an older holder
    is still honoured."""
    runs_root.mkdir(parents=True, exist_ok=True)
    key = f"{project_id}-{task_id}" if project_id else task_id
    lock_path = runs_root / f".lock-{key}"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        holder = _lock_holder_pid(lock_path)
        os.close(fd)
        raise RunLockError(
            f"another harness-tuner run (pid {holder or '?'}) is live against task"
            f" {task_id} — one run per task.\n"
            "fix: wait for that run to finish, or stop it — the kernel"
            f" releases the lock when its process ends. The lock is"
            f" {lock_path}."
        ) from None
    holder = _lock_holder_pid(lock_path)
    if holder is not None and holder != os.getpid() and _pid_alive(holder):
        os.close(fd)  # closing releases the flock
        raise RunLockError(
            f"another harness-tuner run (pid {holder}) is live against task"
            f" {task_id} — one run per task.\n"
            f"fix: wait for that run to finish. If pid {holder} is not a"
            f" harness-tuner run, delete {lock_path} and retry."
        )
    record = json.dumps(
        {
            "pid": os.getpid(),
            "started": datetime.now().isoformat(timespec="seconds"),
            "project_id": project_id,
            "task_id": task_id,
        }
    )
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, record.encode())
    return RunLock(lock_path, fd)


@dataclass
class _RunOutput:
    """Where this run's files are, for the lines `main` prints at the exit:
    set as soon as each exists, cleared at the top of `main`."""

    run_dir: Path | None = None
    report_path: Path | None = None

    def clear(self) -> None:
        self.run_dir = None
        self.report_path = None


_RUN_OUTPUT = _RunOutput()


async def _run(args: argparse.Namespace) -> str:
    try:
        agent_cli = parse_agent_cli(args.agent_cli)
    except ValueError as exc:
        raise SystemExit(f"--agent-cli: {exc}") from exc
    # The state folder first: one that cannot be used fails before anything
    # is written (`paths.py`). The overlap check runs before the folder is
    # made, so a refused one is not created inside the Kiln project.
    project_root = Path(args.project_path).expanduser().resolve()
    root = state_home()
    check_no_overlap(StatePaths(root), project_root)
    state = prepare_state(root)
    # The skills and the instructions, where this agent CLI reads them
    # (`install_agents.py`). Before the CLI check, which is a session.
    try:
        agents_dir = install_for_run(agent_cli.kind, state.agents, package_version())
    except InstallError as exc:
        raise SystemExit(f"harness-tuner: {exc}") from exc
    target = target_from_args(args)
    models = parse_role_overrides(args.agent_model, "--agent-model")
    efforts = parse_role_overrides(args.agent_effort, "--agent-effort")
    # Before the run lock and the run directory: a CLI that cannot answer
    # leaves nothing behind.
    await check_agent_cli(agent_cli, target, agents_dir, models, efforts)
    policy = load_model_policy(args.model_policy)
    lock = _acquire_run_lock(state.runs, target.task_id, target.project_id)
    try:
        # The budget's clock zero: EvalRuns created before this
        # are evidence the run reads, not money the run spends.
        run_started_at = datetime.now()
        run_id = _unique_run_id(state.runs, run_started_at)
        run_dir = state.runs / run_id
        _RUN_OUTPUT.run_dir = run_dir
        # Before the live display, which owns the terminal from then on.
        print(f"harness-tuner: run {run_id} — files in {run_dir}/", flush=True)
        # Diagnostics to `runs/<run_id>/kiln-harness-tuner.log` from the first moment
        # the run has a directory — before `render_run_configs`, so a startup failure
        # there is in the file too. File only: the live UI owns the
        # terminal.
        configure_run_logging(run_dir)
        logger.info(
            "run %s starting — project %s task %s kiln %s budget %s min%s",
            run_id,
            target.project_id,
            target.task_id,
            target.kiln_host,
            args.timeout_mins,
            f" / ${args.budget_usd:.2f}" if args.budget_usd is not None else "",
        )
        adapter = DISPATCHERS[agent_cli.kind][0]
        logger.info("agent CLI: %s", agent_cli.text)
        if not adapter.HAS_MAX_TURNS and args.max_turns is not None:
            logger.warning(
                "--max-turns has no %s equivalent and is ignored", adapter.CLI_NAME
            )
        if not adapter.REPORTS_COST and args.budget_usd is not None:
            # The CLI reports tokens, not cost: agent sessions book $0 and
            # their tokens as unmeasured, so the cap sees the Kiln side only.
            logger.warning(
                "--budget-usd with %s caps Kiln-side spend only: its sessions"
                " report tokens, not cost",
                adapter.CLI_NAME,
            )
        policy_copy: Path | None = None
        if policy is not None:
            # The hook reads the policy from inside the run directory, which
            # no agent can write; the original may sit anywhere.
            policy_copy = (run_dir / "model_policy.json").resolve()
            policy_copy.write_text(
                Path(args.model_policy).expanduser().read_text(encoding="utf-8")
            )
        mcp_config_path, settings_path = render_run_configs(
            run_dir,
            target,
            state=state,
            agents_dir=agents_dir,
            model_policy=policy_copy,
            max_concurrent_evals=args.max_concurrent_evals,
        )
        # Crash debris from a dead run's skill install (the janitor reasoning:
        # the run lock guarantees no live install is racing this).
        swept = sweep_staging(project_root)
        if swept:
            logger.info("swept %d stale skill staging dir(s)", swept)
        async with KilnClient(target.kiln_host) as kiln:
            ctx = build_ctx(
                target,
                kiln,
                state=state,
                agents_dir=agents_dir,
                run_id=run_id,
                run_dir=run_dir,
                mcp_config_path=mcp_config_path,
                settings_path=settings_path,
                models=models,
                efforts=efforts,
                max_concurrent_agents=args.max_concurrent_agents,
                max_concurrent_evals=args.max_concurrent_evals,
                coverage_floor=args.coverage_floor,
                budget_usd=args.budget_usd,
                max_turns=args.max_turns,
                run_started_at=run_started_at,
                gate_agent=args.gate_agent,
                recon=not args.no_recon,
                baseline_audit=not args.no_baseline_audit,
                reference_check=args.reference_check,
                objective=args.objective,
                model_policy=policy,
                agent_cli=agent_cli,
                min_effect=args.min_effect,
                allow_underpowered=args.allow_underpowered,
                noise_check_items=args.noise_check,
                model_preflight=not args.skip_model_preflight,
            )
            # The tracker MUST share Ctx.clock's domain — run_start's monotonic
            # `deadline` is compared against the tracker's clock.
            tracker = RunStatusTracker(
                project_id=target.project_id,
                task_id=target.task_id,
                guidance=args.guidance,
                clock=ctx.clock,
                agent_cli=agent_cli.kind,
            )
            console = Console()
            # --no-ui (and CI / non-TTY) falls back to plain log lines.
            ui = (
                LiveUi(tracker, console=console)
                if not args.no_ui and console.is_terminal
                else PlainUi(tracker, console=console)
            )
            with WaveLog(run_dir / "wave_log.jsonl") as wave_log, ui:
                # Tracker before UI: renders always see post-update snapshots.
                # The spend meter rides the same fan-out (budget enforcement).
                ctx.log = FanoutLog(wave_log, tracker, ui, ctx.spend)
                # The analyzer logs too (`eval_job_errors`)
                # — re-point it at the fan-out now that it exists.
                ctx.analyzer.log = ctx.log
                report_writer = ReportWriter(tracker, run_id, state.reports)
                _RUN_OUTPUT.report_path = report_writer.report_path
                ctx.write_report = report_writer
                # Agent-free report for fatal/circuit-break exits: a
                # report always ships, whatever the exit.
                ctx.write_fallback = report_writer.write_fallback
                return await orchestrate(ctx, args.guidance, args.timeout_mins)
    finally:
        lock.release()


# The signals that stop a run cleanly: SIGTERM (`kill`, a
# service stop, `timeout(1)`) and SIGHUP (the terminal that started it
# closed). SIGINT is Ctrl-C in the foreground — Python raises
# KeyboardInterrupt, which cancels the main task the same way — but a shell
# starts a BACKGROUND job (`harness-tuner … &`) with SIGINT ignored, so `kill -INT`
# does nothing to it: stop a backgrounded run with `kill <pid>` (SIGTERM).
STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP)
_stopped_by: list[int] = []


async def _run_with_signals(args: argparse.Namespace) -> str:
    """`_run`, with SIGTERM and SIGHUP handled like Ctrl-C: cancel the main
    task so every `finally` runs — children killed by group, server jobs
    cancelled, the lock released, the `exit` event and fallback report
    written. Without this a `kill`, `timeout(1)`, service stop or closed
    terminal terminated Python outright and left every `claude -p` child
    running to its own 30-minute cap."""
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    installed: list[int] = []
    _stopped_by.clear()

    def on_stop(signum: int) -> None:
        # One shot: a second signal during teardown must
        # not re-cancel the main task mid-`finally` (lock release, child
        # reaping). Once we are cancelling, the default disposition returns.
        _stopped_by.append(signum)
        for sig in STOP_SIGNALS:
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(sig)
        if task is not None:
            task.cancel()

    if task is not None:
        for sig in STOP_SIGNALS:
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.add_signal_handler(sig, on_stop, sig)
                installed.append(sig)
    try:
        return await _run(args)
    finally:
        for sig in installed:
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(sig)


def _print_run_folder() -> None:
    """The run folder, after a run that did not finish: its
    `kiln-harness-tuner.log` holds the details."""
    if _RUN_OUTPUT.run_dir is not None:
        print(f"harness-tuner: run folder: {_RUN_OUTPUT.run_dir}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _RUN_OUTPUT.clear()
    try:
        result = asyncio.run(_run_with_signals(args))
    except asyncio.CancelledError:
        signum = _stopped_by[0] if _stopped_by else signal.SIGTERM
        print(
            f"harness-tuner: interrupted ({signal.Signals(signum).name})"
            " — children killed, lock released",
            file=sys.stderr,
        )
        _print_run_folder()
        return 128 + signum  # 143 for SIGTERM, 129 for SIGHUP: the shell's codes
    except KeyboardInterrupt:
        # Ctrl-C cancels the main task like the signals above, but
        # `asyncio.run` then raises KeyboardInterrupt, not CancelledError.
        # It goes on as before; the run folder is named first.
        _print_run_folder()
        raise
    except (
        StateHomeError,  # the state folder cannot be used
        BaselineRecordMissingError,
        SetupError,  # setup precondition / split audit / description
        AnalysisError,
        KilnError,
        PreflightError,
        ConfigRenderError,
        RunLockError,
        MemoryWriteRefused,  # a refused lifecycle write that reached the top
        RecordKindError,  # an agent-supplied id of the wrong record kind
        QuarantineError,  # the splits could not be decided
        TrainSetError,  # the train boundary is unclear: no agent may start
    ) as exc:
        # The hard errors carry instructive messages — land them instead
        # of a traceback. Anything else is a genuine bug and may raise raw.
        print(f"harness-tuner: {exc}", file=sys.stderr)
        _print_run_folder()
        # The fallback report a fatal exit writes, when there is one.
        report = _RUN_OUTPUT.report_path
        if report is not None and report.exists():
            print(f"harness-tuner: report: {report}", file=sys.stderr)
        return 1
    print(f"harness-tuner: run finished — report: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
