"""ClaudeCliDispatcher — the reference AgentDispatcher (docs/agent-cli.md).

One `claude -p` subprocess per call, cwd = the repository root so
`.claude/skills/` loads exactly as it does for interactive sessions. MCP
servers come from the per-run rendered config (`--mcp-config` +
`--strict-mcp-config`, host-injected), NOT the installed
`.mcp.json`; write tools are sandboxed by the per-run rendered `--settings`.
Shared plumbing lives in two private helpers (`_run` → `_dispatch`) that each
public method calls with its concrete types.

Subprocess execution sits behind `SubprocessRunner` so the unit tier runs on
canned `claude -p --output-format json` transcripts with no live calls.

Envelope contract (rendered into every prompt): the agent's FINAL message is
exactly one fenced ```json block of
`{"status": "ok"|"failed_expected", "data": <output schema>|null,
"detail": <string|null>}`. The agent's own status passes through
(`failed_expected` = measured negative, a normal verdict); transport/parse/
crash failures become `failed_infra` with an `error_class`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from kiln_harness_tuner.dispatch.run_fence import refresh_run_dir_denials
from kiln_harness_tuner.gateway.ops import ROLE_OPS, role_tools
from kiln_harness_tuner.path_order import path_key
from kiln_harness_tuner.provider_errors import (
    ProviderQuotaError,
    classify_provider_message,
)
from kiln_harness_tuner.schemas import (
    AgentResult,
    AnalysisInput,
    BatchPlan,
    BatchPlanInput,
    DescribeEvalInput,
    ErrorInfo,
    EvalBuildInput,
    EvalBuildReport,
    EvalDescription,
    ExperimentResult,
    GeneralityReviewInput,
    GeneralityReviewResult,
    IntegrityReviewInput,
    IntegrityReviewResult,
    ProbeInput,
    ProbeResult,
    PromotionDecision,
    PromotionInput,
    RcBuildInput,
    RcBuildResult,
    ReportInput,
    ReportMarkdown,
    TargetBlock,
    Usage,
)

M = TypeVar("M", bound=BaseModel)

logger = logging.getLogger(__name__)

# How much of a stderr/stdout tail reaches the run log. Long enough for an MCP
# spawn traceback, short enough that one wedged session cannot bury the file.
_TAIL_CHARS = 2000


def _tail(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _TAIL_CHARS else "…" + text[-_TAIL_CHARS:]


# Model/effort pinned in code: without the pin every
# dispatch inherits the invoking machine's default, so runs differ across
# machines. Latest-Opus alias by design — pin a dated version only if the
# alias misbehaves. Effort high (not xhigh) as the default for every role.
# Per-role overrides (agent sessions are most of a run's spend, so a pin is a
# cost lever) ride the constructor's `models`/`efforts` dicts, fed by
# --agent-model / --agent-effort in cli.py.
AGENT_MODEL = "opus"
AGENT_EFFORT = "high"
# Per-role model defaults that sit under the --agent-model overrides. The
# generality review reads two texts and a few inputs with no tools, so it
# runs on Sonnet.
AGENT_MODEL_DEFAULTS: dict[str, str] = {"generality_review": "sonnet"}

# Per-dispatch turn cap: OPT-IN, uncapped by default. The long
# sessions in the trace corpus (225-turn eval_build, 130-turn rc builds) were
# expensive but PRODUCTIVE — large legitimate jobs, not loops — and a cap
# sitting near the observed maximum would truncate the next big-but-healthy
# session at 90% done and then re-pay it from scratch through the retry
# ladder. The wall-clock timeouts already bound pathological sessions; the
# real cost levers are per-role model/effort and input-contract fattening.
# `--max-turns` remains available as an operator knob for runs that want a
# hard ceiling anyway.

# Per-call wall-clock timeouts (rough defaults):
# without them a wedged `claude -p` hangs the run forever, evading both the
# budget (checked at wave boundaries only) and the tripwire.
AGENT_TIMEOUTS_SECS: dict[str, float] = {
    "batch_plan": 15 * 60,
    "promote_best": 15 * 60,
    "report_writer": 15 * 60,
    "eval_build": 30 * 60,
    "create_experiment_rc": 30 * 60,
    "analyze_experiment_results": 30 * 60,
    # A probe: fork one config, a handful of drives, read the
    # traces, answer. Short by design — it is the cheap check.
    "probe": 15 * 60,
    # Setup's describe_eval: two reads and a paragraph — the shortest session
    # the loop dispatches after the preflight, and one of many running at
    # once on a cold project.
    "describe_eval": 10 * 60,
    # The integrity reviewer: reads its input, writes flags.
    "integrity_review": 10 * 60,
    # The generality reviewer: the same shape — read the input, answer.
    "generality_review": 10 * 60,
    "preflight": 3 * 60,
}

# Loop agents never get a generic Kiln API tool. Each role gets
# its own gateway operations, by full name (`gateway.ops.role_tools`), the
# file tools (fenced by the rendered settings and the OS sandbox), and — the
# builder and the probe — the skill installer. `--allowedTools` accepts no
# `mcp__<server>__*` wildcard, so every name is listed.
_FILE_TOOLS = ["Read", "Grep", "Glob", "Bash"]
_SKILL_INSTALLER_TOOL = "mcp__skill_installer__install_skill_bundle"
# The project tree is read-denied, so a builder reads a skill it clones
# through the installer's export (a copy into its own scratch).
_SKILL_EXPORT_TOOL = "mcp__skill_installer__export_skill_bundle"

DEFAULT_ALLOWED_TOOLS: dict[str, list[str]] = {
    role: [*role_tools(role), *(_FILE_TOOLS if role != "preflight" else [])]
    for role in ROLE_OPS
}
# The integrity reviewer needs no tool and no Kiln access at
# all: everything it reads is in its input (`NO_TOOL_ROLES`). It has no
# gateway operations.
DEFAULT_ALLOWED_TOOLS["integrity_review"] = []
DEFAULT_ALLOWED_TOOLS["generality_review"] = []
DEFAULT_ALLOWED_TOOLS["create_experiment_rc"] += [
    _SKILL_INSTALLER_TOOL,
    _SKILL_EXPORT_TOOL,
]
# A probe may stage a throwaway skill to ask "does the model read one".
DEFAULT_ALLOWED_TOOLS["probe"] += [_SKILL_INSTALLER_TOOL]

# Roles that run with no MCP server and every built-in tool disallowed: the
# session can read its input and answer, nothing else. It has no Kiln lane
# and no gateway operations.
NO_TOOL_ROLES = frozenset({"integrity_review", "generality_review"})

# The start-up check of the agent CLI: one short prompt with no
# tools, before the run directory exists. It runs under the no-tool argv and
# the run-wide model. Not a role: it has no template, input or record.
CLI_CHECK_ROLE = "cli_check"
CLI_CHECK_WORD = "KILN-HARNESS-TUNER-READY"
CLI_CHECK_PROMPT = f"Reply with exactly {CLI_CHECK_WORD} and nothing else."
CLI_CHECK_TIMEOUT_SECS = 3 * 60
_BUILT_IN_TOOLS = (
    "Bash",
    "Read",
    "Grep",
    "Glob",
    "Edit",
    "Write",
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
    "Task",
    "TodoWrite",
    "Skill",
)

DEFAULT_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "agents"

# Roles whose session creates Kiln artifacts as it goes — the ones a fresh
# retry duplicates.
RESUME_ON_TIMEOUT_ROLES = frozenset({"create_experiment_rc", "eval_build", "probe"})
RESUME_TIMEOUT_SECS = 10 * 60
FINISH_UP_PROMPT = (
    "Your session hit its wall-clock cap and was resumed. Do NOT start over: "
    "every artifact you already created (prompts, run configs, code tools, "
    "skills) exists on the server — reuse it. Finish only the remaining steps "
    "as briefly as possible and reply with the final fenced ```json block. If "
    "the run config (or the eval) already exists, return its id now."
)

DEFAULT_CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


class DispatchTimeoutError(Exception):
    """The subprocess exceeded its per-call wall-clock timeout."""


class PreflightError(RuntimeError):
    """The once-per-run MCP-lane preflight failed —
    fatal before any paid work; the message is the operator's fix list."""


@dataclass
class SubprocessResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass
class CliRound:
    """What one finished CLI session printed, read into the loop's terms:
    the CLI-specific part of a dispatch round. Every
    dispatcher reads its CLI's output into this; the retry, repair and
    error-routing logic around it is shared."""

    session_id: str | None
    result_text: str | None  # the agent's final message
    usage: Usage
    error: str | None = None  # the CLI's own error report, None when none


class SubprocessRunner(Protocol):
    """The test seam: the real runner spawns `claude -p`; tests inject a
    fake returning canned transcripts."""

    async def run(
        self,
        argv: list[str],
        *,
        stdin_text: str,
        timeout_secs: float,
        cwd: str,
    ) -> SubprocessResult: ...


# Environment names that must not reach `claude -p` or the MCP servers it
# spawns: agents hold Bash and can `env`, and transcripts are
# committed. `ANTHROPIC_API_KEY` is the one that also changes BILLING — with
# it set, the CLI bills the API instead of the operator's login, and
# `total_cost_usd` silently turns from a shadow price into real money.
_SECRET_ENV_RE = re.compile(
    r"(?i)(token|secret|passw|private_key|api_key|apikey|access_key|credential"
    r"|(?:^|_)pat(?:_|$)|(?:^|_)auth(?:_|$)|_key(?:_|$))"
)
# A value that carries credentials whatever its name — `DATABASE_URL=
# postgres://user:password@host`.
_SECRET_ENV_VALUE_RE = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
# The CLI's own headless login credential stays: it is how `claude -p`
# authenticates on a box with no interactive login.
CHILD_ENV_KEEP_DEFAULT = frozenset({"CLAUDE_CODE_OAUTH_TOKEN"})
CHILD_ENV_KEEP_VAR = (
    "KILN_HARNESS_TUNER_CHILD_ENV_KEEP"  # comma-separated extra names to pass
)


def child_env(
    environ: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """The environment a `claude -p` child gets: the parent's, minus every
    secret-looking name that is not explicitly kept. Returns (env, withheld
    names) so the caller can say what it withheld — names only, never
    values."""
    source = os.environ if environ is None else environ
    keep = set(CHILD_ENV_KEEP_DEFAULT)
    keep.update(n for n in source.get(CHILD_ENV_KEEP_VAR, "").split(",") if n)
    env: dict[str, str] = {}
    withheld: list[str] = []
    for name, value in source.items():
        if name not in keep and (
            _SECRET_ENV_RE.search(name) or _SECRET_ENV_VALUE_RE.search(value)
        ):
            withheld.append(name)
            continue
        env[name] = value
    return env, sorted(withheld)


# How long `taskkill` may take to end a Windows process tree.
_TASKKILL_TIMEOUT_SECS = 10


def _kill_tree(pid: int) -> None:
    """Kill the process tree under `pid`, without waiting for it to exit.

    macOS and Linux: SIGKILL to the child's process group. Windows has no
    process groups to signal, so `taskkill /T` ends the tree by parent pid —
    there the child is often `cmd.exe` running npm's `claude.cmd` shim, and
    killing that pid alone leaves the real CLI running."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=_TASKKILL_TIMEOUT_SECS,
            check=False,
        )
    else:
        os.killpg(pid, signal.SIGKILL)


def spawn_argv(argv: list[str]) -> list[str]:
    """`argv` with its executable found on PATH the way a shell finds it.
    Only Windows needs this: there `CreateProcess` finds only an `.exe`, and
    npm installs the agent CLIs as `.cmd` shims (`claude.cmd`). macOS and
    Linux search PATH at exec time."""
    if sys.platform != "win32" or not argv:
        return argv
    found = shutil.which(argv[0])
    return [found, *argv[1:]] if found else argv


async def _kill_and_reap(proc: asyncio.subprocess.Process) -> None:
    """Kill the child's whole process tree and reap it, tolerating every
    race on the way out.

    The TREE, not just `claude`: the child is spawned as a
    session leader (`start_new_session=True`), so its MCP servers
    (the loop gateway, the skill installer under `uv run`) and anything the
    agent launched from Bash share its pgid — signalling the pid alone
    re-parented all of them to init, still running and, for a Bash-launched
    driver, still spending. Windows has no process group, and `_kill_tree`
    walks the tree there. `proc.kill()` stays as the fallback for a child
    that is not a group leader (a test seam).

    **The kill runs before the first suspension point on purpose**: awaiting
    this coroutine always executes it, so the signal — which is what stops the
    spend — lands even when the reap does not. The reap itself suppresses
    `BaseException`, not `Exception`: a second `CancelledError` (say
    `asyncio.run`'s teardown cancelling a task `describe_all` already
    cancelled) or a `GeneratorExit` arriving here must not replace the caller's
    original exception, and the worst case it leaves behind is a zombie of an
    already-dead child that goes away with the process. `ProcessLookupError`
    means it exited on its own between the two — the outcome we wanted."""
    if proc.returncode is None:  # a reaped pid may already be someone else's
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            _kill_tree(proc.pid)
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    with contextlib.suppress(BaseException):
        await proc.wait()


class AsyncioSubprocessRunner:
    def __init__(self) -> None:
        self._env, withheld = child_env()
        if withheld:
            level = logging.WARNING if "ANTHROPIC_API_KEY" in withheld else logging.INFO
            logger.log(
                level,
                "claude -p env: withholding %s (secret-looking names never reach "
                "agent sessions; %s=<name,…> passes one through)%s",
                ", ".join(withheld),
                CHILD_ENV_KEEP_VAR,
                (
                    " — ANTHROPIC_API_KEY withheld, so sessions bill the CLI login, "
                    "not the API"
                    if "ANTHROPIC_API_KEY" in withheld
                    else ""
                ),
            )

    async def run(
        self,
        argv: list[str],
        *,
        stdin_text: str,
        timeout_secs: float,
        cwd: str,
    ) -> SubprocessResult:
        proc = await asyncio.create_subprocess_exec(
            *spawn_argv(argv),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=self._env,
            # Its own session ⇒ its own process group, so a kill reaches the
            # MCP servers and Bash children too (`_kill_and_reap`). Windows
            # ignores it; `_kill_tree` walks the tree there.
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(stdin_text.encode()), timeout_secs
            )
        except TimeoutError as exc:
            await _kill_and_reap(proc)
            raise DispatchTimeoutError(
                f"claude -p exceeded {timeout_secs:.0f}s wall-clock timeout"
            ) from exc
        except BaseException:
            # Cancellation, notably: `wait_for` cancels `communicate()` and
            # re-raises, but the CHILD is never signalled — a cancelled
            # dispatch would otherwise leave a `claude -p` session running
            # (and spending, and writing its trace) for the rest of its
            # timeout, outliving the run that paid for it. Setup's describe
            # fan-out cancels its siblings on a fatal (`describe_all`), so
            # this is a live path; reaping belongs here, where it covers every
            # present and future cancellation site.
            await _kill_and_reap(proc)
            raise
        return SubprocessResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
        )


class _AgentEnvelope(BaseModel):
    status: Literal["ok", "failed_expected"]
    data: Any | None = None
    detail: str | None = None


class PreflightData(BaseModel):
    """The preflight envelope's data: the gateway `ping` operation's reply,
    `{"ok": true}`, relayed by the session."""

    model_config = ConfigDict(extra="allow")

    ping: str | dict[str, Any]
    """Whatever the tool handed back, unread: the JSON text, or the object
    the model parsed it into. `reply_text` does the reading."""

    def reply_text(self) -> str | None:
        """The reply as JSON text, or None when it cannot be read. Only an
        object whose `ok` is exactly true passes the check; an `error`
        answer from the gateway does not."""
        value: Any = self.ping
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return None
        if not isinstance(value, dict) or value.get("ok") is not True:
            return None
        return json.dumps({"ok": True})


def _dispatch_label(agent: str, req: BaseModel) -> str:
    """`<agent>-<item_id|loop>` — the per-dispatch discriminator used for the
    scratch dir and the trace-copy filename. Item dispatches carry their item
    (rc build / analysis), the generality review its `item_id`; eval_build
    carries the issue id; describe_eval
    carries its eval id — setup fans out SETUP_DESCRIBE_CONCURRENCY of them at
    once, and `_make_scratch_dir` keys the per-session write sandbox by this
    label, so concurrent sessions must not collapse onto one tree. The
    loop-level singles (plan / promote / report / preflight) are `loop`."""
    item = getattr(req, "item", None)
    discriminator = (
        getattr(item, "id", None)
        or getattr(req, "item_id", None)
        or getattr(req, "issue_memory_id", None)
        or getattr(req, "eval_id", None)
        or "loop"
    )
    return f"{agent}-{discriminator}"


def _project_slug(path: str) -> str:
    """The Claude projects-dir slug of a cwd: every non-alphanumeric becomes
    `-` (e.g. /home/u/repo → -home-u-repo) — verified against a live
    ~/.claude/projects layout."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


# Transcripts land in `traces/`, which a person may share, and an agent's tool results
# can carry whatever it read — a remote-MCP config with its Authorization
# token, a settings file, an `env` dump. Known credential shapes are blanked
# on copy; the shapes are the ones seen in practice
# (Anthropic/OpenRouter/OpenAI-style keys, GitHub and Slack tokens, Parse
# session tokens as a hosted backend issues them, bearer headers, and JSON
# fields named like a secret — with the `\"` escaping a JSONL transcript
# uses inside tool-result strings).
_REDACTED = "[REDACTED]"
_SECRET_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-or-v1-[A-Fa-f0-9]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\br:[A-Za-z0-9]{24,}"),
)
_SECRET_FIELD_NAMES = (
    r"(?:authorization|api[_-]?key|x-api-key|token|secret|password|session[_-]?token)"
)
_SECRET_PREFIXED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}"),
    re.compile(
        r'(?i)((?:\\?")' + _SECRET_FIELD_NAMES + r'(?:\\?")\s*:\s*(?:\\?"))([^"\\]{4,})'
    ),
    # The same field names unquoted — YAML / INI / `KEY=value` lines an agent
    # reads with `cat`.
    re.compile(r"(?i)(\b" + _SECRET_FIELD_NAMES + r"\s*[:=]\s*)([^\s\"',;\\]{8,})"),
    # Credentials inside a URL: keep the user, blank the password.
    re.compile(r"(://[^/\s:@\"]+:)[^/\s@\"]+(?=@)"),
)


def redact_secrets(text: str) -> str:
    """Blank known credential shapes in `text`; everything else is byte-
    identical, so a transcript without secrets copies unchanged."""
    for pattern in _SECRET_TEXT_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    for pattern in _SECRET_PREFIXED_PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + _REDACTED, text)
    return text


def _copy_redacted(src: Path, dest: Path) -> None:
    # utf-8 on both sides: the locale encoding turned a transcript into
    # U+FFFD on a C-locale box and then refused to write it back.
    dest.write_text(
        redact_secrets(src.read_text(encoding="utf-8", errors="replace")),
        encoding="utf-8",
    )


_USAGE_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def transcript_usage_tokens(path: Path) -> int:
    """Every token the CLI transcript at `path` reports across its assistant
    turns (`message.usage`). The cost is NOT derivable here — pricing is the
    provider's, and a local price table is exactly what the accounting
    doctrine forbids — so this is the "how much is missing" number for a
    session that never printed its `total_cost_usd`."""
    total = 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            message = event.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            if not isinstance(usage, dict):
                continue
            for key in _USAGE_TOKEN_KEYS:
                value = usage.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    total += int(value)
    return total


# The prompt mandates a ```json fence, so the opener requires the tag — a bare
# ``` line (e.g. the closing fence of a ```python block) can never open a
# candidate, which is what prevents fence mis-pairing across mixed-language
# messages. Both fences are line-anchored.
_FENCE_RE = re.compile(r"^```json[ \t]*\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)
# Opener probe: a ```json opener line with no complete block after the last
# complete one means the FINAL block is broken (truncated mid-JSON by a
# max-token cutoff, or fenced with indentation) — never fall back to a stale
# complete block in that case. Line-anchored with optional indentation only:
# truncated openers always sit at line start and indented fences carry only
# leading whitespace, while mid-sentence prose mentions of ```json and other
# fence tags (```jsonc/```json5) must not trip it — each false positive would
# burn a full repair round on output that was complete and valid.
_OPENER_RE = re.compile(r"^[ \t]*```json[ \t]*$", re.MULTILINE)


def _extract_envelope(
    result_text: str, output_model: type[M]
) -> tuple[tuple[str, M | None, str | None] | None, str | None]:
    """Validate the agent's FINAL fenced ```json block (the contract — never an
    earlier draft: a superseded but schema-valid draft must not silently win
    over a broken final answer; that failure belongs to the repair retry).

    Returns ((status, data, detail), None) on success or (None, error_detail)
    for the repair retry / parse_failure path.
    """
    matches = list(_FENCE_RE.finditer(result_text))
    if matches:
        last = matches[-1]
        if _OPENER_RE.search(result_text, last.end()):
            # The agent opened another ```json block after the last complete
            # one and never closed it — the final block is broken, and an
            # earlier (stale) block must not win. Repair retry's job.
            return None, (
                "the final ```json fence opens but no complete block follows"
                " — the block is incomplete (truncated or mis-fenced); reply"
                " again with one complete, unindented fenced ```json block"
            )
        candidate = last.group(1)
    else:
        if _OPENER_RE.search(result_text):
            return None, (
                "a ```json fence opens but no complete fenced block was "
                "found — the block is incomplete (truncated or mis-fenced)"
            )
        # Lenient fallback only when nothing is fenced: try the whole message.
        candidate = result_text
    try:
        raw = json.loads(candidate.strip())
    except ValueError as exc:
        return None, (
            f"invalid JSON: {exc}"
            if matches
            else f"no fenced ```json block found, and the message itself is not "
            f"valid JSON ({exc})"
        )
    try:
        envelope = _AgentEnvelope.model_validate(raw)
    except ValidationError as exc:
        return None, f"envelope invalid: {exc}"
    if envelope.status == "ok" and envelope.data is None:
        return None, 'envelope has status "ok" but data is null'
    data: M | None = None
    if envelope.data is not None:
        try:
            data = output_model.model_validate(envelope.data)
        except ValidationError as exc:
            return None, f"data does not match {output_model.__name__}: {exc}"
    return (envelope.status, data, envelope.detail), None


def _usage_from_payload(payload: dict[str, Any]) -> Usage:
    raw_usage = payload.get("usage") or {}
    tokens = 0
    # Cache tokens included: for skill-loading agents the cache reads typically
    # dominate, so omitting them would materially undercount Usage.tokens.
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        value = raw_usage.get(key)
        if isinstance(value, (int, float)):
            tokens += int(value)
    duration_ms = payload.get("duration_ms")
    cost = payload.get("total_cost_usd")
    return Usage(
        cost_usd=float(cost) if isinstance(cost, (int, float)) else 0.0,
        tokens=tokens,
        wall_secs=(
            float(duration_ms) / 1000.0
            if isinstance(duration_ms, (int, float))
            else 0.0
        ),
    )


def _add_usage(a: Usage, b: Usage) -> Usage:
    return Usage(
        cost_usd=a.cost_usd + b.cost_usd,
        tokens=a.tokens + b.tokens,
        wall_secs=a.wall_secs + b.wall_secs,
        unmeasured_tokens=a.unmeasured_tokens + b.unmeasured_tokens,
    )


# A bare "429" substring would false-positive on ids/durations that happen to
# contain the digits; require the rate-limit phrasing or a clearly HTTP-status-
# shaped 429: a standalone word (boundary on BOTH sides, so "_429" id suffixes
# never match) introduced by http/status/code — "error" is deliberately not an
# introducer: it bridges onto ids like "error: mem_429".
_RATE_LIMIT_RE = re.compile(
    r"rate[\s_-]?limit|too many requests"
    r"|\b(?:http|status|code)\D{0,10}\b429\b|^429\b"
)


# The CLI's own out-of-quota wording on a subscription login ("Claude AI usage
# limit reached|<epoch>") — the provider regexes know the API's 402/401
# phrasings, not this one.
_CLI_USAGE_LIMIT_RE = re.compile(r"usage limit|hit your limit|claude ai usage")


def _classify_cli_error(detail: str) -> str:
    """An `is_error` CLI result's class. Quota and auth are
    recognized here too (the eval lane's classifier, plus the CLI's own
    usage-limit wording) — read as `provider_error`, they would walk
    the full retry ladder, four full sessions that could not succeed."""
    lowered = detail.lower()
    provider_class = classify_provider_message(detail, status_codes=False)
    if provider_class in ("quota_exhausted", "auth"):
        return provider_class
    # Rate limits before the CLI's usage-limit wording: "API rate limit
    # reached, retry later" is throttling, not exhaustion.
    if _RATE_LIMIT_RE.search(lowered):
        return "rate_limit"
    if _CLI_USAGE_LIMIT_RE.search(lowered):
        return "quota_exhausted"
    return "provider_error"


# The roles that run read-only: no write grant and no scratch tree. Two hold
# val data in their input: the promotion agent reads the val
# tables, the report writer the whole run's history. Read-only, nothing they
# hold can reach a record a later agent reads. The two reviewers (integrity,
# generality) hold no val data; they have no tools, so they have nothing to
# write.
READ_ONLY_ROLES = frozenset(
    {"promote_best", "report_writer", "integrity_review", "generality_review"}
)


# TargetBlock fields never rendered into a prompt: the host (agents reach
# Kiln through their gateway operations) and the project path (the tree is read-denied).
_PROMPT_EXCLUDED = {"kiln_host", "project_path"}


class ClaudeCliDispatcher:
    """Prototype AgentDispatcher: one `claude -p` subprocess per call.

    The CLI-specific steps are methods a second CLI
    dispatcher overrides — `_argv` (the command), `_cwd` (where it runs),
    `_parse_cli_output` (what it printed) and `_transcript_source` (where its
    session transcript is). Everything else is shared."""

    # How logs and errors name the CLI; the preflight's sign-in hint.
    CLI_NAME = "claude -p"
    SIGN_IN_HINT = "the Claude Code sign-in every agent session needs"
    # A non-zero exit is `agent_crash`, its text unread (docs/agent-cli.md
    # section 5). A CLI that ends a failed session with a STRUCTURED error event and
    # a non-zero exit sets this, so that event is classified like an
    # `is_error` result — quota and auth then stop the run.
    READS_ERROR_ON_NONZERO_EXIT = False
    # `claude -p --resume` reports `total_cost_usd` for the whole session so
    # far, not for the resumed round, so a resumed round books only the rise.
    RESUME_COST_IS_SESSION_TOTAL = True
    # The model and effort a role gets when neither `--agent-model` nor the
    # model in `--agent-cli` names one. A CLI whose own default
    # model is the right default sets DEFAULT_MODEL to None and passes no
    # model option. MODEL_DEFAULTS are per-role, and apply only when the
    # `--agent-cli` text names no model: a run-wide model replaces them.
    DEFAULT_MODEL: str | None = AGENT_MODEL
    MODEL_DEFAULTS: dict[str, str] = AGENT_MODEL_DEFAULTS
    DEFAULT_EFFORT = AGENT_EFFORT
    # False: `--max-turns` has no equivalent and is ignored (cli.py warns).
    HAS_MAX_TURNS = True
    # False: the CLI reports tokens, not cost — sessions book their tokens
    # as unmeasured and `--budget-usd` caps the Kiln side only.
    REPORTS_COST = True

    def __init__(
        self,
        target: TargetBlock,
        repo_root: Path,
        *,
        runner: SubprocessRunner | None = None,
        claude_bin: str = "claude",
        cli_options: tuple[str, ...] = (),
        default_model: str | None = None,
        default_effort: str | None = None,
        templates_dir: Path = DEFAULT_TEMPLATES_DIR,
        allowed_tools: dict[str, list[str]] | None = None,
        timeouts_secs: dict[str, float] | None = None,
        run_id: str | None = None,
        run_dir: Path | None = None,
        mcp_config_path: Path | None = None,
        settings_path: Path | None = None,
        traces_root: Path | None = None,
        claude_projects_dir: Path = DEFAULT_CLAUDE_PROJECTS_DIR,
        models: dict[str, str] | None = None,
        efforts: dict[str, str] | None = None,
        max_concurrent: int | None = None,
        max_turns: int | None = None,
        readonly_settings_path: Path | None = None,
    ):
        self._target = target
        self._repo_root = repo_root
        self._runner = runner or AsyncioSubprocessRunner()
        self._claude_bin = claude_bin
        # The operator's own options from `--agent-cli`, on every session.
        self._cli_options = tuple(cli_options)
        self._templates_dir = templates_dir
        self._allowed_tools = allowed_tools or DEFAULT_ALLOWED_TOOLS
        self._timeouts_secs = timeouts_secs or AGENT_TIMEOUTS_SECS
        # Per-role model/effort overrides; missing roles keep
        # the run-wide model and effort — `--agent-cli`'s, else the pinned
        # defaults.
        if default_model is not None:
            self._models = dict(models or {})
            self._default_model: str | None = default_model
        else:
            self._models = {**self.MODEL_DEFAULTS, **(models or {})}
            self._default_model = self.DEFAULT_MODEL
        self._efforts = efforts or {}
        self._default_effort = default_effort or self.DEFAULT_EFFORT
        self._max_turns = max_turns
        # Dispatch concurrency cap: one
        # semaphore over every `claude -p` subprocess — wave items and
        # setup's describe fan-out share it. None = unlimited.
        self._semaphore = asyncio.Semaphore(max_concurrent) if max_concurrent else None
        # Run wiring — cli.py always passes
        # the full set; None (minimal/test wiring) omits the matching argv
        # flags and skips scratch dirs / trace copies.
        self._run_id = run_id
        # Sessions this dispatcher has seen finish — a timed-out session's
        # unnamed transcript is found by elimination among the NEW files
        # (`_recover_timed_out_usage`), so siblings' known ids are excluded.
        # label → the session id `_recover_timed_out_usage` found for a
        # timed-out dispatch, consumed by the resume-on-timeout path.
        self._timed_out_sessions: dict[str, str] = {}
        # Session id → the session cost already booked, for resumed rounds.
        self._session_cost: dict[str, float] = {}
        self._run_dir = run_dir
        self._step_seq = 0  # steps/<seq>-<label>.json, in dispatch order
        self._mcp_config_path = mcp_config_path
        self._settings_path = settings_path
        # The val readers run under their own settings — no write
        # grant, a read-only hook (`READ_ONLY_ROLES`). None = every role gets
        # `settings_path` (minimal/test wiring).
        self._readonly_settings_path = readonly_settings_path
        self._traces_root = traces_root
        self._claude_projects_dir = claude_projects_dir

    # ---- the protocol surface: one line each into the shared plumbing ----

    async def batch_plan(self, req: BatchPlanInput) -> AgentResult[BatchPlan]:
        return await self._run("batch_plan", req, BatchPlan)

    async def eval_build(self, req: EvalBuildInput) -> AgentResult[EvalBuildReport]:
        return await self._run("eval_build", req, EvalBuildReport)

    async def describe_eval(
        self, req: DescribeEvalInput
    ) -> AgentResult[EvalDescription]:
        return await self._run("describe_eval", req, EvalDescription)

    async def create_experiment_rc(
        self, req: RcBuildInput
    ) -> AgentResult[RcBuildResult]:
        return await self._run("create_experiment_rc", req, RcBuildResult)

    async def analyze_experiment_results(
        self, req: AnalysisInput
    ) -> AgentResult[ExperimentResult]:
        return await self._run("analyze_experiment_results", req, ExperimentResult)

    async def promote_best(self, req: PromotionInput) -> AgentResult[PromotionDecision]:
        return await self._run("promote_best", req, PromotionDecision)

    async def integrity_review(
        self, req: IntegrityReviewInput
    ) -> AgentResult[IntegrityReviewResult]:
        return await self._run("integrity_review", req, IntegrityReviewResult)

    async def generality_review(
        self, req: GeneralityReviewInput
    ) -> AgentResult[GeneralityReviewResult]:
        return await self._run("generality_review", req, GeneralityReviewResult)

    async def probe(self, req: ProbeInput) -> AgentResult[ProbeResult]:
        return await self._run("probe", req, ProbeResult)

    async def report_writer(self, req: ReportInput) -> AgentResult[ReportMarkdown]:
        # Markdown back; report.py writes reports/kiln-harness-tuner-<run_id>.md.
        return await self._run("report_writer", req, ReportMarkdown)

    # ---- shared plumbing ----

    def _make_scratch_dir(self, label: str) -> Path | None:
        """Per-session scratch: the ONLY place the
        rendered sandbox settings let write tools touch. Keyed by label, so
        CONCURRENT sessions (distinct items) never share a tree; sequential
        same-label dispatches (e.g. each wave's `batch_plan-loop`) reuse
        theirs — scratch is throwaway run-scoped state, not an isolation
        boundary between waves."""
        if self._run_dir is None:
            return None
        scratch = self._run_dir / "scratch" / label
        scratch.mkdir(parents=True, exist_ok=True)
        return scratch

    def _render_prompt(
        self,
        agent: str,
        req: BaseModel,
        output_model: type[M],
        scratch_dir: Path | None = None,
    ) -> str:
        template = (self._templates_dir / f"{agent}.md").read_text(encoding="utf-8")
        # Scratch-dir + tool guidance ride the shared render,
        # not each template — one place, every role.
        scratch_block = (
            f"scratch_dir: {scratch_dir} — you can only write files here.\n"
            if scratch_dir is not None
            else ""
        )
        # The standard target block: cwd alone identifies nothing — the repo
        # holds one project with multiple tasks; no agent ever guesses ids.
        # `kiln_host` is excluded: it reached agents ONLY through
        # this block, and an agent given it would curl Kiln directly —
        # agents reach Kiln through their gateway operations, which need no host. The
        # field stays on TargetBlock for the code that does need it (the per-run
        # MCP host injection, KilnClient, the preflight wave-log event and the
        # preflight failure message). `project_path` is excluded too: the
        # project tree is read-denied to every agent (it holds
        # the eval inputs and results of every split), so the path is no
        # affordance — only an invitation to a read the fence refuses.
        return (
            f"{template.rstrip()}\n\n"
            "## Target\n"
            "The Kiln project/task this run operates on — use these ids for every"
            " gateway operation; never guess:\n"
            "```json\n"
            f"{self._target.model_dump_json(indent=2, exclude=_PROMPT_EXCLUDED)}\n"
            "```\n"
            f"{scratch_block}"
            "Reach the project through your gateway operations only: its files"
            " are not readable in this session, and every answer holds train"
            " records only. Avoid writing files unless there's a good reason —"
            " prefer the gateway operations directly.\n\n"
            "## Input\n"
            f"```json\n{req.model_dump_json(indent=2)}\n```\n\n"
            "## Output requirements\n"
            "Your FINAL message must be exactly one fenced ```json code block"
            " containing an object of the form:\n"
            '`{"status": "ok" | "failed_expected", "data": <output object> | null,'
            ' "detail": <string | null>}`\n'
            'Use "failed_expected" only for a measured negative / expected'
            " failure; infrastructure problems should be surfaced as errors,"
            " not encoded here.\n"
            f'When status is "ok", "data" must match this JSON schema'
            f' ({output_model.__name__}); if "data" is not null it must'
            " match the schema regardless of status:\n"
            "```json\n"
            + json.dumps(output_model.model_json_schema(), indent=2, ensure_ascii=False)
            + "\n```\n"
        )

    def _argv(
        self,
        agent: str,
        resume_session_id: str | None = None,
        session_id: str | None = None,
        label: str | None = None,
    ) -> list[str]:
        argv = [
            self._claude_bin,
            "-p",
            *self._cli_options,
            "--output-format",
            "json",
        ]
        model = self._model_for(agent)
        if model:
            argv += ["--model", model]
        argv += ["--effort", self._effort_for(agent)]
        if self._max_turns is not None:
            argv += ["--max-turns", str(self._max_turns)]
        if agent in NO_TOOL_ROLES or agent == CLI_CHECK_ROLE:
            # No MCP server at all (--strict-mcp-config with no config) and
            # every built-in tool refused: the input is all it reads.
            argv += [
                "--strict-mcp-config",
                "--disallowedTools",
                ",".join(_BUILT_IN_TOOLS),
            ]
        elif self._mcp_config_path is not None:
            # --strict-mcp-config: ONLY the rendered per-run config — never
            # the installed .mcp.json and its generic Kiln API server.
            # Each role has its own config holding its gateway (and the
            # installer where the role has it).
            mcp_path = self._mcp_config_path
            role_mcp = mcp_path.parent / f"mcp_config.{agent}.json"
            if role_mcp.exists():
                mcp_path = role_mcp
            argv += ["--mcp-config", str(mcp_path), "--strict-mcp-config"]
        # The run directory is denied by default — every entry
        # that exists now, listed or not, gets a read deny rule before the
        # session starts (`run_fence`).
        if self._run_dir is not None:
            refresh_run_dir_denials(
                self._run_dir,
                [
                    p
                    for p in (self._settings_path, self._readonly_settings_path)
                    if p is not None
                ]
                + sorted(self._run_dir.glob("settings.*.json"), key=path_key),
            )
        settings_path = self._settings_path
        if agent in READ_ONLY_ROLES and self._readonly_settings_path is not None:
            settings_path = self._readonly_settings_path
        # The role's own settings: its hook knows the role.
        if self._settings_path is not None:
            role_settings = self._settings_path.parent / f"settings.{agent}.json"
            if role_settings.exists():
                settings_path = role_settings
        if settings_path is not None:
            argv += ["--settings", str(settings_path)]
        tools = self._allowed_tools.get(agent)
        if tools:
            argv += ["--allowedTools", ",".join(tools)]
        if resume_session_id:
            argv += ["--resume", resume_session_id]
        elif session_id:
            # The session id is CHOSEN here, so the
            # transcript's path is known before the spawn — a session killed
            # at its cap never prints it, and finding the file by elimination
            # could pick a running sibling's (and then RESUME that sibling).
            argv += ["--session-id", session_id]
        return argv

    def _model_for(self, agent: str) -> str | None:
        """The role's model: its override, else the run-wide model. None =
        no model option, so the CLI's own default."""
        return self._models.get(agent, self._default_model)

    def _effort_for(self, agent: str) -> str:
        return self._efforts.get(agent, self._default_effort)

    async def check_cli(self, command: str) -> Usage:
        """Send one short prompt through the agent CLI before the run starts. The loop
        does not manage the CLI's sign-in, so this is where a CLI that is not installed,
        not signed in or given a model it does not know is found — once, with what it
        printed, instead of as an `agent_crash` on every session. `command` is the
        `--agent-cli` text, for the message. Raises PreflightError."""
        label = "cli-check"
        argv = self._argv(CLI_CHECK_ROLE, label=label)
        problem: str | None = None
        output = ""
        usage = Usage()
        try:
            proc = await self._runner.run(
                argv,
                stdin_text=CLI_CHECK_PROMPT,
                timeout_secs=CLI_CHECK_TIMEOUT_SECS,
                cwd=str(self._cwd(label)),
            )
        except DispatchTimeoutError:
            problem = f"no answer in {CLI_CHECK_TIMEOUT_SECS // 60} minutes"
        except OSError as exc:
            problem = f"it could not be started: {exc}"
        else:
            cli = self._parse_cli_output(proc.stdout)
            # The CLI's own error is the reason; its event stream adds noise.
            output = proc.stderr.strip()
            if cli is None or cli.error is None:
                output = (output + "\n" + proc.stdout.strip()).strip()
            if cli is not None:
                usage = cli.usage
            if cli is not None and cli.error is not None:
                problem = f"it reported an error: {cli.error}"
            elif proc.returncode != 0:
                problem = f"it exited {proc.returncode}"
            elif cli is None:
                problem = f"its output is not what {self.CLI_NAME} prints"
            elif CLI_CHECK_WORD not in (cli.result_text or ""):
                problem = f"it answered {(cli.result_text or '')[:200]!r}"
        if problem is None:
            logger.info("agent CLI check: `%s` answered", command)
            return usage
        tail = redact_secrets(_tail(output)) if output else "(nothing)"
        raise PreflightError(
            f"--agent-cli {command!r} failed its start-up check ({problem}).\n"
            f"  sent: {CLI_CHECK_PROMPT!r} on stdin, with no tools\n"
            f"  output (tail):\n{tail}\n"
            f"fix: run `{command}` by hand with a short prompt and make it"
            " answer — the CLI is installed and on PATH, it is signed in"
            " (the loop uses the CLI's own sign-in), and its model name"
            " exists. A credential the CLI reads from an environment"
            " variable is withheld from loop sessions unless"
            f" {CHILD_ENV_KEEP_VAR} names it."
        )

    async def preflight(self) -> Usage:
        """Once-per-run MCP-lane proof: one tiny inline
        dispatch that calls the gateway's `ping` operation. Any
        failure — tool missing, call failed, parse failed — is fatal
        (PreflightError) with the operator's fix list. Costs pennies."""
        prompt = (
            "Call the `ping` tool (the Kiln Harness Tuner gateway's connectivity check"
            ' — it returns {"ok": true} when the Kiln server answers).\n\n'
            "## Output requirements\n"
            "Your FINAL message must be exactly one fenced ```json code block"
            " containing an object of the form:\n"
            '`{"status": "ok" | "failed_expected", "data":'
            ' {"ping": <the tool\'s exact response>} | null,'
            ' "detail": <string | null>}`\n'
            "If the ping tool is unavailable or the call fails, use"
            ' status "failed_expected", data null, and put what happened in'
            ' "detail". Do nothing else.\n'
        )
        result = await self._dispatch(
            "preflight", "preflight-loop", prompt, PreflightData
        )
        problem: str | None = None
        if result.status == "failed_infra":
            problem = (
                f"{result.error.error_class}: {result.error.detail}"
                if result.error
                else "infrastructure failure"
            )
        elif result.status != "ok" or result.data is None:
            problem = result.detail or "agent reported the tool call failed"
        else:
            reply = result.data.reply_text()
            if reply is None:
                problem = f"unexpected ping response: {result.data.ping!r}"
        if problem is not None:
            raise PreflightError(
                "preflight failed — the Kiln Harness Tuner gateway MCP lane is not"
                f" working ({problem}).\n"
                f"  MCP config: {self._mcp_config_path}\n"
                f"  Kiln host:  {self._target.kiln_host}\n"
                "Likely causes: the Kiln studio server is not running at that"
                " host; the MCP server is not installed; or the MCP server"
                " failed to spawn/handshake.\n"
                "fix: start the Kiln studio server at the host above, then"
                " install the tools with `uv tool install .` from this"
                " repository's root. If both are already true, read the"
                " config file named above and this run's stderr copy under"
                " traces/. docs/install.md checks each prerequisite in turn,"
                f" including {self.SIGN_IN_HINT} — this message names the"
                " two commonest causes, not every one."
            )
        return result.usage

    async def _run(
        self, agent: str, req: BaseModel, output_model: type[M]
    ) -> AgentResult[M]:
        label = _dispatch_label(agent, req)
        # A read-only role gets no scratch: it may write nothing.
        scratch_dir = (
            None
            if agent in READ_ONLY_ROLES and self._readonly_settings_path is not None
            else self._make_scratch_dir(label)
        )
        prompt = self._render_prompt(agent, req, output_model, scratch_dir)
        started = datetime.now()
        result = await self._dispatch(agent, label, prompt, output_model)
        self._persist_step(agent, label, req, result, started)
        return result

    def _persist_step(
        self, agent: str, label: str, req: BaseModel, result: Any, started: datetime
    ) -> None:
        """One JSON per dispatch under `runs/<id>/steps/`: the
        request the agent was given and the result it returned, in dispatch
        order. With the memory snapshots the orchestrator takes around the
        planner and the gate, this is what the viewer's step-through view
        replays — "given this state and this input, was the decision good?".
        Log-and-continue: persisting must never fail a dispatch."""
        if self._run_dir is None:
            return
        try:
            self._step_seq += 1
            ended = datetime.now()
            data = getattr(result, "data", None)
            error = getattr(result, "error", None)
            usage = getattr(result, "usage", None)
            record = {
                "seq": self._step_seq,
                "agent": agent,
                "label": label,
                "started": started.isoformat(timespec="seconds"),
                "ended": ended.isoformat(timespec="seconds"),
                "elapsed_secs": (ended - started).total_seconds(),
                "request": req.model_dump(mode="json"),
                "result": {
                    "status": getattr(result, "status", None),
                    "detail": getattr(result, "detail", None),
                    "data": data.model_dump(mode="json") if data is not None else None,
                    "error": error.model_dump(mode="json")
                    if error is not None
                    else None,
                    "usage": usage.model_dump(mode="json")
                    if usage is not None
                    else None,
                },
            }
            steps_dir = self._run_dir / "steps"
            steps_dir.mkdir(parents=True, exist_ok=True)
            path = steps_dir / f"{self._step_seq:03d}-{label}.json"
            path.write_text(
                json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001 — a bookkeeping miss is not a dispatch failure
            logger.warning(
                "dispatch %s: could not persist the step record", label, exc_info=True
            )

    async def _dispatch(
        self, agent: str, label: str, prompt: str, output_model: type[M]
    ) -> AgentResult[M]:
        usage = Usage()
        # An entry a previous attempt of this label left behind (a repair
        # retry that timed out) must never be resumed by this one.
        self._timed_out_sessions.pop(label, None)

        outcome, usage = await self._invoke(agent, label, prompt, output_model, usage)
        timed_out_session = self._timed_out_sessions.pop(label, None)
        if (
            isinstance(outcome, ErrorInfo)
            and outcome.error_class == "timeout"
            and agent in RESUME_ON_TIMEOUT_ROLES
            and timed_out_session is not None
        ):
            # A build session killed at its cap has already
            # POSTed prompts, run configs, tools, maybe a skill bundle. The
            # item ladder's fresh retry created a SECOND set (and the skill
            # installer rejected the bundle name it had already taken).
            # Resume the same session once, with a short cap, and tell it to
            # finish with what exists.
            session_id = timed_out_session
            logger.warning(
                "dispatch %s: resuming timed-out session %s to finish up",
                label,
                session_id,
            )
            outcome, usage = await self._invoke(
                agent,
                label,
                FINISH_UP_PROMPT,
                output_model,
                usage,
                resume=session_id,
                timeout_secs=RESUME_TIMEOUT_SECS,
            )
        if isinstance(outcome, ErrorInfo):
            return self._failed_infra(output_model, outcome, usage)

        parsed, parse_error, session_id = outcome
        if parsed is None:
            # One in-session repair retry with the validation errors,
            # then failed_infra(parse_failure).
            if session_id:
                repair_prompt = (
                    "Your final message did not satisfy the output requirements:\n"
                    f"{parse_error}\n\n"
                    "Reply with ONLY one fenced ```json code block containing "
                    '{"status": "ok" | "failed_expected", "data": <output object>'
                    ' | null, "detail": <string | null>}, with "data" matching '
                    "the schema you were given."
                )
                outcome, usage = await self._invoke(
                    agent, label, repair_prompt, output_model, usage, resume=session_id
                )
                if isinstance(outcome, ErrorInfo):
                    return self._failed_infra(output_model, outcome, usage)
                parsed, parse_error, _ = outcome
            if parsed is None:
                return self._failed_infra(
                    output_model,
                    ErrorInfo(
                        error_class="parse_failure",
                        detail=parse_error or "unparseable agent output",
                    ),
                    usage,
                )

        status, data, detail = parsed
        return AgentResult[output_model](
            status=status, data=data, detail=detail, usage=usage
        )

    async def _invoke(
        self,
        agent: str,
        label: str,
        prompt: str,
        output_model: type[M],
        usage: Usage,
        resume: str | None = None,
        timeout_secs: float | None = None,
    ) -> tuple[
        ErrorInfo
        | tuple[tuple[str, M | None, str | None] | None, str | None, str | None],
        Usage,
    ]:
        """One subprocess round: returns either an infra ErrorInfo or
        (parsed_envelope, parse_error, session_id), plus accumulated usage.

        Every exit path from this method logs: a dispatch is the
        loop's longest silence — up to 30 minutes of one subprocess — and
        before `runs/<run_id>/kiln-harness-tuner.log` existed the only trace of a wedged
        or crashing session was terminal scrollback."""
        if timeout_secs is None:
            timeout_secs = self._timeouts_secs[agent]
        logger.info(
            "dispatch %s: %s (timeout %.0fs%s)",
            label,
            "resuming" if resume else f"spawning {self.CLI_NAME}",
            timeout_secs,
            f", resume {resume}" if resume else "",
        )
        started = time.monotonic()
        # The transcript's name, known up front: the resumed session's id, or
        # a fresh uuid the CLI is told to use.
        session_hint = resume or str(uuid.uuid4())
        # The concurrency slot is held for the subprocess only — parsing and
        # artifact copies run outside it. `elapsed` includes any queue wait,
        # which is itself signal (a saturated lane shows as long dispatches).
        limiter = (
            self._semaphore if self._semaphore is not None else contextlib.nullcontext()
        )
        try:
            async with limiter:
                proc = await self._runner.run(
                    self._argv(agent, resume, session_id=session_hint, label=label),
                    stdin_text=prompt,
                    timeout_secs=timeout_secs,
                    cwd=str(self._cwd(label)),
                )
        except DispatchTimeoutError as exc:
            # No proc came back, so no `total_cost_usd`: the run's most
            # expensive session shape would book $0 and leave no
            # transcript. Recover what the transcript holds.
            elapsed = time.monotonic() - started
            usage = _add_usage(
                usage, self._recover_timed_out_usage(label, session_hint, elapsed)
            )
            logger.error(
                "dispatch %s: TIMEOUT after %.1fs — %s%s",
                label,
                elapsed,
                exc,
                (
                    f" (transcript recovered: {usage.unmeasured_tokens:,} tokens, "
                    "cost unmeasured)"
                    if usage.unmeasured_tokens
                    else " (no transcript recovered — cost unmeasured)"
                ),
            )
            return ErrorInfo(error_class="timeout", detail=str(exc)), usage
        elapsed = time.monotonic() - started
        logger.info(
            "dispatch %s: %s exited %d after %.1fs",
            label,
            self.CLI_NAME,
            proc.returncode,
            elapsed,
        )

        # Best-effort output/session parse ONCE, so the artifact copy runs on
        # every completion path — success AND failure.
        cli = self._parse_cli_output(proc.stdout)
        session_id = cli.session_id if cli is not None else None
        failed = proc.returncode != 0 or cli is None or cli.error is not None
        self._persist_dispatch_artifacts(label, session_id, proc.stderr, failed=failed)

        structured_failure = (
            self.READS_ERROR_ON_NONZERO_EXIT
            and cli is not None
            and cli.error is not None
        )
        if proc.returncode != 0 and not structured_failure:
            detail = proc.stderr.strip() or proc.stdout.strip()
            # The stderr tail is the process-layer record nothing else keeps
            # (MCP spawn/handshake failures print here and nowhere else) — the
            # same text `_persist_dispatch_artifacts` writes beside the trace,
            # in the run's own log so it reads in sequence.
            logger.error(
                "dispatch %s: NONZERO EXIT %d — stderr tail:\n%s",
                label,
                proc.returncode,
                _tail(proc.stderr) or "(empty)",
            )
            return (
                ErrorInfo(
                    error_class="agent_crash",
                    detail=f"{self.CLI_NAME.split()[0]} exited {proc.returncode}:"
                    f" {detail[-2000:]}",
                ),
                usage,
            )

        if cli is None:
            logger.error(
                "dispatch %s: unparseable CLI output (not a JSON object);"
                " stdout tail:\n%s",
                label,
                _tail(proc.stdout) or "(empty)",
            )
            return (
                ErrorInfo(
                    error_class="agent_crash",
                    detail=f"unparseable {self.CLI_NAME.split()[0]} CLI output",
                ),
                usage,
            )

        round_usage = cli.usage
        if self.RESUME_COST_IS_SESSION_TOTAL and session_id:
            booked = self._session_cost.get(session_id, 0.0) if resume else 0.0
            self._session_cost[session_id] = round_usage.cost_usd
            round_usage = round_usage.model_copy(
                update={"cost_usd": max(round_usage.cost_usd - booked, 0.0)}
            )
        usage = _add_usage(usage, round_usage)
        logger.info(
            "dispatch %s: session %s · $%.2f · %d tokens",
            label,
            session_id or "unknown",
            round_usage.cost_usd,
            round_usage.tokens,
        )
        if proc.stderr.strip():
            # A zero-exit session that still printed to stderr is the quiet
            # case worth keeping: a degraded MCP server, a deprecation, a
            # retried connection. DEBUG so INFO stays a narrative.
            logger.debug("dispatch %s: stderr tail:\n%s", label, _tail(proc.stderr))

        if cli.error is not None:
            detail = cli.error
            error_class = _classify_cli_error(detail)
            logger.error(
                "dispatch %s: CLI reported an error (%s): %s",
                label,
                error_class,
                detail[:500],
            )
            if error_class in ("quota_exhausted", "auth"):
                # The agent lane's circuit breaker: terminal and
                # operator-actionable, same as the eval lane's — the run must
                # stop spending, not retry.
                raise ProviderQuotaError(
                    ErrorInfo(
                        error_class=error_class,  # type: ignore[arg-type]
                        detail=f"{self.CLI_NAME} ({label}): {detail}",
                    ),
                    usage=usage,
                )
            return (
                ErrorInfo(error_class=error_class, detail=detail),  # type: ignore[arg-type]
                usage,
            )

        parsed, parse_error = _extract_envelope(
            str(cli.result_text or ""), output_model
        )
        if parsed is None:
            logger.warning("dispatch %s: envelope rejected — %s", label, parse_error)
        return (parsed, parse_error, session_id), usage

    def _cwd(self, label: str | None) -> Path:
        """Where the session runs: the repository root, so `.claude/skills/`
        loads as it does for interactive sessions."""
        return self._repo_root

    def _parse_cli_output(self, stdout: str) -> CliRound | None:
        """`claude -p --output-format json` prints one JSON object. None when
        stdout is not one."""
        try:
            payload = json.loads(stdout)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        result_text = payload.get("result")
        return CliRound(
            session_id=payload.get("session_id"),
            result_text=None if result_text is None else str(result_text),
            usage=_usage_from_payload(payload),
            error=str(result_text or payload.get("subtype") or "unknown CLI error")
            if payload.get("is_error")
            else None,
        )

    def _transcript_dir(self) -> Path:
        return self._claude_projects_dir / _project_slug(str(self._repo_root))

    def _transcript_source(self, session_id: str) -> Path | None:
        """The CLI's own transcript of a session, or None when not found."""
        src = self._transcript_dir() / f"{session_id}.jsonl"
        return src if src.is_file() else None

    def _recover_timed_out_usage(
        self, label: str, session_id: str, elapsed_secs: float
    ) -> Usage:
        """Read the killed session's transcript — at the path its chosen
        session id names — for its tokens, copy it beside the traces as
        `-timeout`, and remember the id so a build role can resume it. The
        cost is NOT derivable (see `transcript_usage_tokens`), so the tokens
        are booked as UNMEASURED. Log-and-continue on any miss: this must
        never fail a dispatch."""
        usage = Usage(wall_secs=elapsed_secs)
        try:
            src = self._transcript_dir() / f"{session_id}.jsonl"
            if not src.is_file():
                return usage
            usage = Usage(
                wall_secs=elapsed_secs, unmeasured_tokens=transcript_usage_tokens(src)
            )
            self._timed_out_sessions[label] = session_id
            if self._traces_root is not None and self._run_id is not None:
                dest_dir = (
                    self._traces_root
                    / self._target.project_id
                    / self._target.task_id
                    / self._run_id
                )
                dest_dir.mkdir(parents=True, exist_ok=True)
                _copy_redacted(src, dest_dir / f"{label}-{session_id}-timeout.jsonl")
        except (OSError, ValueError) as exc:
            logger.warning("timeout transcript recovery failed for %s: %s", label, exc)
        return usage

    def _persist_dispatch_artifacts(
        self,
        label: str,
        session_id: str | None,
        stderr_text: str,
        *,
        failed: bool,
    ) -> None:
        """Per-dispatch trace copy + failure stderr persist. Copies the CLI's own
        session transcript from the Claude projects dir into the traces tree;
        repair-retry continuations re-copy the same (grown) file, overwriting. Log-and-
        continue on any miss — this must never fail a dispatch."""
        if self._traces_root is None or self._run_id is None:
            return
        try:
            dest_dir = (
                self._traces_root
                / self._target.project_id
                / self._target.task_id
                / self._run_id
            )
            dest_dir.mkdir(parents=True, exist_ok=True)
            if session_id:
                src = self._transcript_source(session_id)
                if src is not None:
                    _copy_redacted(src, dest_dir / f"{label}-{session_id}.jsonl")
                else:
                    logger.warning("trace copy: no transcript for %s", session_id)
            else:
                logger.warning("trace copy: no session_id for %s", label)
            if failed:
                # The process-layer record transcripts can't capture (issue
                # 9): MCP spawn/handshake failures print here and nowhere
                # else.
                stderr_path = dest_dir / (
                    f"{label}-{session_id or 'nosession'}-stderr.txt"
                )
                stderr_path.write_text(redact_secrets(stderr_text), encoding="utf-8")
        except (OSError, ValueError) as exc:
            logger.warning("trace copy failed for %s: %s", label, exc)

    @staticmethod
    def _failed_infra(
        output_model: type[M], error: ErrorInfo, usage: Usage
    ) -> AgentResult[M]:
        return AgentResult[output_model](
            status="failed_infra", data=None, error=error, usage=usage
        )
