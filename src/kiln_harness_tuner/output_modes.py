"""The structured-output mode of a run config: the rules that code applies.

`structured_output_mode` tells Kiln how to ask the model for output that
matches the task's output schema. Kiln uses it only on a task with an
output schema. This module holds the three things code knows about it, so
that the screens, the gateway, the model preflight and the lever check
give the same answer:

- **The mode of a model swap** (`swap_mode`). A mode is chosen for one
  model and provider. So a run config that moves to another pair, and names
  no mode, takes the default mode that Kiln's model list gives for the new
  pair. It keeps the parent's mode when the list gives no default, or the
  legacy `default` or `unknown`. When the run config attaches a tool or a
  skill and the mode would send the schema as a tool, it takes
  `TOOLS_FALLBACK`.
- **The mode that Kiln refuses** (`refusal`). On a run config with a tool
  or a skill, Kiln refuses a mode that sends the schema as a tool: its
  adapter raises at the final call of every run
  (`kiln_ai.adapters.model_adapters.litellm_adapter`). Code refuses it
  before a run config is made.
- **The signal that the mode reached the call** (`mode_evidence`,
  `mode_signal`). What a saved run records, by mode:

  - `function_calling`, `function_calling_weak`: the final assistant
    message calls the tool `task_response`. Kiln offers that tool only in
    these modes.
  - `json_instructions`, `json_instruction_and_object`: the system message
    holds Kiln's format block (`FORMAT_BLOCK`), with the schema.
  - `json_schema`, `json_mode`, `json_custom_instructions`: nothing in the
    trace. Kiln sends `response_format` (or nothing) with the request, and
    a trace holds messages only.

  In every mode, Kiln copies the mode that its adapter ran with into
  `output.source.properties.structured_output_mode`. That record shows that
  Kiln read the mode. It does not show that the provider obeyed it.

Kiln's model list gives one default mode for each pair, and no list of the
modes that a pair supports. So code cannot refuse a mode because the pair
does not support it: one failed call shows that.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

MODES = (
    "json_schema",
    "function_calling",
    "function_calling_weak",
    "json_instruction_and_object",
    "json_mode",
    "json_instructions",
    "json_custom_instructions",
    "default",
    "unknown",
)
# Kiln chooses the real mode at call time: `default` by the provider,
# `unknown` from the default of the model and provider.
LEGACY = ("default", "unknown")
# The modes that send the schema as the parameters of the tool `task_response`.
TOOL_MODES = ("function_calling", "function_calling_weak")
# The modes that add the schema as text after the saved prompt.
INSTRUCTION_MODES = ("json_instructions", "json_instruction_and_object")
# On these providers `default` is `json_schema`, not a tool.
JSON_SCHEMA_DEFAULT_PROVIDERS = ("ollama", "docker_model_runner")
# The mode of a swap on a run config with tools, when the mode it would take
# sends the schema as a tool. It asks the provider for a JSON object only,
# which more providers accept than `json_schema`.
TOOLS_FALLBACK = "json_instruction_and_object"
RESPONSE_TOOL = "task_response"
# The start of the block that Kiln's prompt builder adds in `INSTRUCTION_MODES`
# (`kiln_ai.adapters.prompt_builders.BasePromptBuilder.build_prompt`).
FORMAT_BLOCK = (
    "# Format Instructions\n\nReturn a JSON object conforming to the following schema:"
)
# The run-config properties that name the model and provider pair.
PAIR_KEYS = ("model_name", "model_provider_name")
NO_SCHEMA = (
    "the task has no output schema: Kiln uses structured_output_mode only on a"
    " task with one, so this change has no effect"
)


def mode_of(props: Mapping[str, Any] | None) -> str | None:
    mode = (props or {}).get("structured_output_mode")
    return mode if isinstance(mode, str) and mode else None


def attached_tools(props: Mapping[str, Any] | None) -> list[str]:
    """The tool ids of `tools_config.tools`. A skill is one of them: Kiln
    loads a skill through a tool."""
    config = (props or {}).get("tools_config")
    if not isinstance(config, Mapping):
        return []
    return [str(t) for t in (config.get("tools") or []) if isinstance(t, str)]


def sends_schema_as_tool(mode: str | None, provider: Any) -> bool:
    """Does Kiln send the schema as the tool `task_response` in this mode?
    `unknown` is not decided here: Kiln resolves it to the default of the
    model and provider, which the caller reads from the model list."""
    if mode in TOOL_MODES:
        return True
    return mode == "default" and provider not in JSON_SCHEMA_DEFAULT_PROVIDERS


def refusal(
    props: Mapping[str, Any], *, structured: bool | None, default: str | None = None
) -> str | None:
    """Why this run config cannot run, or None. `structured`: the task has
    an output schema (None: not known, and then nothing is refused for the
    tools rule). `default`: the default mode of the pair, to resolve
    `unknown`."""
    mode = mode_of(props)
    if mode is None:
        return None
    if mode not in MODES:
        return (
            f"structured_output_mode {mode!r} is not a mode of Kiln. The modes: "
            + ", ".join(MODES)
            + "."
        )
    provider = props.get("model_provider_name")
    resolved = default if mode == "unknown" and default else mode
    if (
        structured
        and attached_tools(props)
        and sends_schema_as_tool(resolved, provider)
    ):
        named = mode if resolved == mode else f"{mode} (here {resolved})"
        return (
            f"structured_output_mode {named} sends the output schema as a tool,"
            " and this run config attaches a tool or a skill. Kiln refuses that"
            " combination: every run fails at its final call. Use json_schema"
            " when the model supports it, else json_instruction_and_object."
        )
    return None


@dataclass(frozen=True)
class ModeChoice:
    mode: str | None  # None: no parent mode and no usable default
    why: str  # one clause, for a log line and a gateway note


def swap_mode(
    parent_mode: str | None,
    default: str | None,
    *,
    provider: Any,
    tools: bool,
) -> ModeChoice:
    """The mode of a run config that moves to another model or provider and
    names no mode (the module doc). `default`: Kiln's default for the new
    pair; `provider`: the new provider; `tools`: the run config attaches a
    tool or a skill."""
    if default and default not in LEGACY:
        if tools and sends_schema_as_tool(default, provider):
            return ModeChoice(
                TOOLS_FALLBACK,
                f"Kiln's default for the pair, {default}, sends the schema as a"
                " tool, and the run config attaches a tool or a skill",
            )
        return ModeChoice(default, "Kiln's default for the pair")
    if parent_mode and tools and sends_schema_as_tool(parent_mode, provider):
        return ModeChoice(
            TOOLS_FALLBACK,
            f"Kiln's model list gives no default for the pair, and the parent's"
            f" mode, {parent_mode}, sends the schema as a tool on this provider",
        )
    return ModeChoice(
        parent_mode,
        "the parent's mode: Kiln's model list gives no default for the pair",
    )


def server_default(offered: Any, provider: Any, model: Any) -> str | None:
    """The default mode of (provider, model) in one `GET
    /api/available_models` payload, or None."""
    for entry in offered if isinstance(offered, list) else []:
        if not isinstance(entry, Mapping) or entry.get("provider_id") != provider:
            continue
        for row in entry.get("models") or []:
            if isinstance(row, Mapping) and row.get("id") == model:
                mode = row.get("structured_output_mode")
                return mode if isinstance(mode, str) and mode else None
    return None


# ---- the signal that the mode reached the call


def _tool_names(message: Mapping[str, Any]) -> list[str]:
    calls = message.get("tool_calls")
    names: list[str] = []
    for call in calls if isinstance(calls, list) else []:
        if not isinstance(call, Mapping):
            continue
        fn = call.get("function")
        name = fn.get("name") if isinstance(fn, Mapping) else call.get("name")
        names.append(str(name or ""))
    return names


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "") for part in content if isinstance(part, Mapping)
        )
    return ""


def mode_evidence(run: Mapping[str, Any]) -> dict[str, Any]:
    """What one task run records about the output mode:

    - `mode_recorded`: the mode that Kiln's adapter ran with (None when the
      run does not record it);
    - `response_tool`: an assistant message calls `task_response`;
    - `format_block`: the system message holds Kiln's format block (None
      when the trace has no system message)."""
    trace = [m for m in (run.get("trace") or []) if isinstance(m, Mapping)]
    recorded = None
    output = run.get("output")
    source = output.get("source") if isinstance(output, Mapping) else None
    if isinstance(source, Mapping):
        config = source.get("run_config")
        if isinstance(config, Mapping):
            inner = config.get("run_config_properties")
            recorded = mode_of(inner if isinstance(inner, Mapping) else config)
        props = source.get("properties")
        if isinstance(props, Mapping):
            recorded = mode_of(props) or recorded
    system = [_text(m.get("content")) for m in trace if m.get("role") == "system"]
    return {
        "mode_recorded": recorded,
        "response_tool": any(
            RESPONSE_TOOL in _tool_names(m)
            for m in trace
            if m.get("role") == "assistant"
        ),
        "format_block": any(FORMAT_BLOCK in s for s in system) if system else None,
        "has_trace": bool(trace),
    }


@dataclass(frozen=True)
class ModeSignal:
    """`status`: "applied", "not_applied" or "unverified". `proof`: what the
    verdict stands on — "trace" (a mark of the mode in the trace), "record"
    (Kiln's record of the mode on the run, and no mark of another mode),
    "none"."""

    status: str
    proof: str
    reason: str


def mode_signal(
    mode: str | None, evidence: Mapping[str, Mapping[str, Any]]
) -> ModeSignal:
    """Did `mode` reach the call, by the drives' `mode_evidence`
    (item id -> evidence)? Only a direct contradiction is "not_applied": a
    run that records another mode, or a `task_response` call in a mode that
    offers no such tool. A missing mark is "unverified", because Kiln can
    change the text of its format block, and a provider can ignore a forced
    tool without an error."""
    n = len(evidence)
    if mode is None or mode in LEGACY or not n:
        return ModeSignal(
            "unverified",
            "none",
            f"structured_output_mode={mode}: Kiln chooses the real mode at call time"
            if n
            else f"structured_output_mode={mode}: no drive to read",
        )
    drives = list(evidence.values())
    other = sorted(
        {
            str(e["mode_recorded"])
            for e in drives
            if e.get("mode_recorded") and e["mode_recorded"] != mode
        }
    )
    if other:
        count = sum(
            1 for e in drives if e.get("mode_recorded") and e["mode_recorded"] != mode
        )
        return ModeSignal(
            "not_applied",
            "record",
            f"lever not applied: the run config names structured_output_mode="
            f"{mode}, but {count} of {n} drives record {', '.join(other)}",
        )
    tool_calls = sum(1 for e in drives if e.get("response_tool"))
    if mode not in TOOL_MODES and tool_calls:
        return ModeSignal(
            "not_applied",
            "trace",
            f"lever not applied: structured_output_mode={mode} offers no"
            f" {RESPONSE_TOOL} tool, but {tool_calls} of {n} drives call it",
        )
    recorded = sum(1 for e in drives if e.get("mode_recorded") == mode)
    if mode in TOOL_MODES:
        if tool_calls:
            return ModeSignal(
                "applied",
                "trace",
                f"structured_output_mode={mode}: {tool_calls} of {n} drives call"
                f" the {RESPONSE_TOOL} tool",
            )
        missing = f"no drive calls the {RESPONSE_TOOL} tool"
    elif mode in INSTRUCTION_MODES:
        blocks = sum(1 for e in drives if e.get("format_block"))
        if blocks:
            return ModeSignal(
                "applied",
                "trace",
                f"structured_output_mode={mode}: the system message of {blocks}"
                f" of {n} drives holds Kiln's format block",
            )
        missing = "no drive shows Kiln's format block in a system message"
    else:
        if recorded:
            return ModeSignal(
                "applied",
                "record",
                f"structured_output_mode={mode}: {recorded} of {n} drives record"
                " the mode, and none shows the mark of another mode. A trace"
                " does not hold the request parameter of this mode",
            )
        missing = "no drive records the mode, and a trace cannot show it"
    return ModeSignal("unverified", "none", f"structured_output_mode={mode}: {missing}")
