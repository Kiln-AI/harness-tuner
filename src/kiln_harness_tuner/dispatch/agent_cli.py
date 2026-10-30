"""`--agent-cli`: the command every loop session runs.

The operator names the agent CLI the way they would type it — `claude -p`,
`codex exec --model gpt-6.1-sol`, `opencode run --model zai-coding-plan/glm-5.3-flash
--variant high`. The executable's name picks the adapter; the rest is the
operator's own options, passed to every session.

The loop does not manage the CLI's credentials. The CLI uses its own sign-in
(`claude` login, `codex login`, `opencode auth login`). Before a run starts,
`check_cli` sends one short prompt through the command, and a command that
does not answer refuses the start with what it printed.

Two options are taken out of the operator's text and become the run-wide
defaults the per-role overrides sit on: the model (`--model`, or `-m` where
the CLI has it) and the reasoning effort (`--effort` for Claude, `--variant`
for OpenCode). Everything else passes through unchanged. An option the loop
sets itself (the output format, the MCP config, the permissions) must not be
repeated in the text: the CLI then sees it twice.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

# kind → the token that puts the CLI in its one-shot mode, and the options
# that name the model and the effort. Empty: the CLI has no such option.
_KINDS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "claude": (("-p", "--print"), ("--model",), ("--effort",)),
    "codex": (("exec", "e"), ("--model", "-m"), ()),
    "opencode": (("run",), ("--model", "-m"), ("--variant",)),
}
DEFAULT_AGENT_CLI = "claude -p"


@dataclass(frozen=True)
class AgentCli:
    """One parsed `--agent-cli` value."""

    text: str  # as the operator wrote it, for logs and errors
    kind: str  # "claude" | "codex" | "opencode"
    bin: str  # the executable, as written (a name on PATH or a path)
    options: tuple[str, ...]  # the operator's other options, verbatim
    model: str | None  # the run-wide model, None = the adapter's default
    effort: str | None  # the run-wide effort, None = the adapter's default


def _pop_option(
    tokens: list[str], names: tuple[str, ...]
) -> tuple[str | None, list[str]]:
    """Take every `NAME VALUE` / `NAME=VALUE` of `names` out of `tokens`.
    The last one wins, as it does for the CLIs."""
    value: str | None = None
    rest: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        head, eq, tail = token.partition("=")
        if eq and head in names:
            value = tail
        elif token in names:
            if i + 1 >= len(tokens):
                raise ValueError(f"{token} needs a value")
            value = tokens[i + 1]
            i += 1
        else:
            rest.append(token)
        i += 1
    return value, rest


def parse_agent_cli(text: str) -> AgentCli:
    """Parse `--agent-cli`. Raises ValueError with the supported forms."""
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise ValueError(f"cannot split {text!r}: {exc}") from exc
    if not tokens:
        raise ValueError("the command is empty")
    kind = Path(tokens[0]).name
    if kind not in _KINDS:
        raise ValueError(
            f"{tokens[0]!r} is not a supported agent CLI. Supported:"
            " `claude -p`, `codex exec`, `opencode run` (each with its own"
            " options, for example `codex exec --model gpt-6.1-sol`)"
        )
    modes, model_names, effort_names = _KINDS[kind]
    if kind == "claude":
        # `-p` is a flag and may sit anywhere; the adapter adds it once.
        rest = [t for t in tokens[1:] if t not in modes]
    else:
        # A subcommand: only in first place.
        rest = tokens[2:] if tokens[1:2] and tokens[1] in modes else tokens[1:]
    model, rest = _pop_option(rest, model_names)
    effort: str | None = None
    if effort_names:
        effort, rest = _pop_option(rest, effort_names)
    return AgentCli(
        text=text,
        kind=kind,
        bin=tokens[0],
        options=tuple(rest),
        model=model or None,
        effort=effort or None,
    )
