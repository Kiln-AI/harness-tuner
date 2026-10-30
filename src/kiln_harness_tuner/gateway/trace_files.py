"""Trace files: the gateway writes train traces where agents can read them.

A stored trace of a multi-turn agent task is far larger than the answer an
MCP tool may give. Claude Code saves an answer over its limit to a file in a
directory the read fence denies, so the agent would see nothing at all. The
gateway writes each TRAIN trace to a file in the run's scratch tree and
answers with the path and a one-line summary.

The folder is `runs/<run_id>/scratch/traces/` (`traces_dir`):

    traces/
      <run_config_id>/
        <eval_id>/<item_id>.md      one trace of get_train_results
        _system/<hash>.md           the system prompt and the tool
                                    definitions, once per distinct text
        _stats.json, _stats.md      trace statistics of every item written
        _stats/<eval_id>/<item_id>.json   the statistics of one item
      task_runs/
        <task_run_id>.md            one trace of get_task_run or run_task
        _system/<hash>.md
      _expected/<item_id>.json      a train item's full expected output,
                                    when get_train_expected clips it

The caller decides what is train. This module writes what it is given and
nothing else: it never reads Kiln, the project or the train set.

Each trace file is plain text made for `Grep` and `Read` with a line range:
a header of `key: value` lines, then one block per message in trace order.
Every block starts with a line `## [<n>] <kind> …`. A tool result that
Kiln marks as an error has `TOOL_ERROR` on that line. The system and
developer messages are written once per distinct text to `_system/`, and
the trace keeps a one-line marker in their place, so the numbers stay those
of the stored trace.

Every write is atomic (a temporary file, then a rename), and a file whose
text has not changed is not written again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCRATCH = "scratch"
TRACES = "traces"
TASK_RUNS = "task_runs"
SYSTEM = "_system"
STATS = "_stats"
EXPECTED = "_expected"
SYSTEM_ROLES = frozenset({"system", "developer"})
ERROR_MARK = "TOOL_ERROR"
USAGE_KEYS = ("input_tokens", "output_tokens", "cached_tokens", "cost")


def traces_dir(run_dir: Path) -> Path:
    """The run's trace folder. The dispatcher opens it to the roles that
    read traces; the gateway writes into it."""
    return run_dir / SCRATCH / TRACES


def safe_name(value: Any) -> str:
    """A path component made from an id: letters, digits, `_` and `-`
    only, so no id can climb out of the folder (`..`, `/`)."""
    name = re.sub(r"[^A-Za-z0-9_-]", "_", str(value))[:120]
    return name or "_"


def write_atomic(path: Path, text: str) -> bool:
    """Write `text` to `path` through a temporary file and a rename. False
    when the file already holds exactly that text (nothing is written)."""
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except (OSError, UnicodeDecodeError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return True


# ---------------------------------------------------------------- parsing


def parse_trace(raw: Any) -> tuple[list[dict[str, Any]], list[Any]]:
    """The messages and the tool definitions of a stored trace.

    Kiln stores the OpenAI message list, as a list or as its JSON text. A
    trace stored as an object (`{"messages": [...], "tools": [...]}`) is
    read too, so tool definitions it carries go to the system file."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return [{"role": "trace", "content": raw}], []
    tools: list[Any] = []
    if isinstance(raw, dict):
        tools = raw.get("tools") if isinstance(raw.get("tools"), list) else []
        raw = raw.get("messages")
    messages = (
        [m for m in raw or [] if isinstance(m, dict)] if isinstance(raw, list) else []
    )
    return messages, tools


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text", "")) if isinstance(p, dict) else str(p) for p in content
        )
    return json.dumps(content, ensure_ascii=False, default=str)


def _calls(message: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(id, name, arguments) of each tool call of an assistant message."""
    out = []
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        args = fn.get("arguments", "")
        out.append(
            (
                str(call.get("id") or ""),
                str(fn.get("name") or "?"),
                args
                if isinstance(args, str)
                else json.dumps(args, ensure_ascii=False, default=str),
            )
        )
    return out


def _number(value: Any) -> float | int | None:
    return (
        value
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def _is_error(message: dict[str, Any]) -> bool:
    return message.get("is_error") is True or bool(message.get("error_message"))


# ---------------------------------------------------------------- statistics


def trace_stats(
    messages: list[dict[str, Any]], run_usage: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Counts of one trace: turns, tool calls by tool name, tool errors,
    tool-result sizes, message sizes by role, and the per-turn usage where
    the trace carries it (Kiln puts `usage` and `latency_ms` on each
    assistant message; an older trace has neither, and then only the run's
    usage is given)."""
    names: dict[str, str] = {}
    tools: dict[str, dict[str, int]] = {}
    chars: dict[str, int] = {}
    turns: list[dict[str, Any]] = []
    assistant = user = calls = errors = 0
    final_reply = 0
    for index, message in enumerate(messages):
        role = str(message.get("role") or "?")
        text = _text(message.get("content"))
        chars[role] = chars.get(role, 0) + len(text)
        if role == "assistant":
            assistant += 1
            made = _calls(message)
            for call_id, name, _args in made:
                names[call_id] = name
                entry = tools.setdefault(name, _tool_entry())
                entry["calls"] += 1
                calls += 1
            if text.strip() and not made:
                final_reply = len(text)
            usage = message.get("usage")
            if isinstance(usage, dict) or _number(message.get("latency_ms")):
                usage = usage if isinstance(usage, dict) else {}
                turns.append(
                    {
                        "message": index,
                        **{k: _number(usage.get(k)) for k in USAGE_KEYS},
                        "latency_ms": _number(message.get("latency_ms")),
                        "tool_calls": len(made),
                    }
                )
        elif role == "user":
            user += 1
        elif role == "tool":
            name = names.get(str(message.get("tool_call_id") or ""), "?")
            entry = tools.setdefault(name, _tool_entry())
            entry["result_chars"] += len(text)
            entry["max_result_chars"] = max(entry["max_result_chars"], len(text))
            if _is_error(message):
                entry["errors"] += 1
                errors += 1
    run = {k: _number((run_usage or {}).get(k)) for k in USAGE_KEYS}
    run["latency_ms"] = _number((run_usage or {}).get("total_llm_latency_ms"))
    return {
        "assistant_turns": assistant,
        "user_turns": user,
        "tool_calls": calls,
        "tool_errors": errors,
        "final_reply_chars": final_reply,
        "tools": dict(sorted(tools.items())),
        "chars_by_role": dict(sorted(chars.items())),
        "usage_source": "per_turn"
        if turns
        else ("per_run_only" if any(v is not None for v in run.values()) else "none"),
        "turn_usage": turns,
        "run_usage": run,
    }


def _tool_entry() -> dict[str, int]:
    return {"calls": 0, "errors": 0, "result_chars": 0, "max_result_chars": 0}


def summary_line(stats: dict[str, Any]) -> str:
    """The one line an answer carries in place of the trace."""
    return (
        f"{stats['assistant_turns']} assistant turns, {stats['user_turns']} user"
        f" turns, {stats['tool_calls']} tool calls ({stats['tool_errors']} tool"
        f" errors), final reply {stats['final_reply_chars']} chars"
    )


# ---------------------------------------------------------------- rendering


def _system_text(messages: list[dict[str, Any]], tools: list[Any]) -> str:
    parts = []
    for index, message in enumerate(messages):
        if str(message.get("role")) in SYSTEM_ROLES:
            parts.append(
                f"## [{index}] {message.get('role')}\n\n{_text(message.get('content'))}"
            )
    if tools:
        parts.append(
            "## tool definitions\n\n"
            + json.dumps(tools, ensure_ascii=False, indent=1, default=str)
        )
    return "\n\n".join(parts)


def _value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _messages_text(messages: list[dict[str, Any]], system_path: Path | None) -> str:
    names: dict[str, str] = {}
    lines: list[str] = []
    turn = 0
    for index, message in enumerate(messages):
        role = str(message.get("role") or "?")
        text = _text(message.get("content"))
        if role in SYSTEM_ROLES:
            lines.append(
                f"## [{index}] {role} (moved out: {len(text)} chars in {system_path})"
            )
            lines.append("")
            continue
        if role == "assistant":
            turn += 1
            usage = (
                message.get("usage") if isinstance(message.get("usage"), dict) else {}
            )
            bits = [f"turn {turn}"]
            bits += [
                f"{k}={_value(_number(usage.get(k)))}"
                for k in USAGE_KEYS
                if usage.get(k) is not None
            ]
            if _number(message.get("latency_ms")) is not None:
                bits.append(f"latency_ms={_value(message.get('latency_ms'))}")
            lines.append(f"## [{index}] assistant ({', '.join(bits)})")
            reasoning = message.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                lines += ["", "### reasoning", "", reasoning]
            if text:
                lines += ["", text]
            for call_id, name, args in _calls(message):
                names[call_id] = name
                lines += ["", f"### tool call {name} (id {call_id})", "", args]
        elif role == "tool":
            call_id = str(message.get("tool_call_id") or "")
            name = names.get(call_id, "?")
            mark = f" {ERROR_MARK}" if _is_error(message) else ""
            size = f"{len(text)} chars"
            lines.append(
                f"## [{index}] tool result {name} (id {call_id}, {size}){mark}"
            )
            detail = message.get("error_message")
            if isinstance(detail, str) and detail:
                lines += ["", f"error_message: {detail}"]
            lines += ["", text]
        else:
            lines += [f"## [{index}] {role}", "", text]
        lines.append("")
    return "\n".join(lines)


def _header(title: str, fields: Iterable[tuple[str, Any]]) -> str:
    lines = [f"# {title}", ""]
    for key, value in fields:
        if value is None:
            continue
        if isinstance(value, dict):
            value = ", ".join(f"{k}={_value(v)}" for k, v in value.items()) or "-"
        lines.append(f"{key}: {_value(value)}")
    return "\n".join(lines)


class TraceWriter:
    """Writes trace files under one root (`traces_dir`)."""

    def __init__(self, root: Path):
        self.root = root

    def _inside(self, path: Path) -> Path:
        root = self.root.resolve()
        resolved = path.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"{path} is outside the trace folder")
        return path

    def _system(self, folder: Path, messages: list[dict[str, Any]], tools: list[Any]):
        text = _system_text(messages, tools)
        if not text:
            return None, 0
        digest = hashlib.sha256(text.encode()).hexdigest()[:12]
        path = self._inside(folder / SYSTEM / f"{digest}.md")
        write_atomic(
            path,
            "# System prompt and tool definitions\n\n"
            "The traces that point here share this text. It is written once,"
            " not in each trace.\n\n" + text + "\n",
        )
        return path, len(text)

    def _write(
        self,
        folder: Path,
        path: Path,
        title: str,
        fields: list[tuple[str, Any]],
        raw_trace: Any,
        run_usage: dict[str, Any] | None,
        sections: list[tuple[str, Any]],
    ) -> tuple[Path, dict[str, Any]]:
        messages, tools = parse_trace(raw_trace)
        stats = trace_stats(messages, run_usage)
        system_path, system_chars = self._system(folder, messages, tools)
        stats["system_chars"] = system_chars
        head = _header(
            title,
            [
                *fields,
                ("assistant_turns", stats["assistant_turns"]),
                ("user_turns", stats["user_turns"]),
                ("tool_calls", stats["tool_calls"]),
                ("tool_errors", stats["tool_errors"]),
                ("final_reply_chars", stats["final_reply_chars"]),
                (
                    "system",
                    f"{system_path} ({system_chars} chars: the system prompt and"
                    " the tool definitions, moved out of this file)"
                    if system_path
                    else "none in the stored trace",
                ),
                ("messages", len(messages)),
            ],
        )
        body = [head, ""]
        for name, value in sections:
            if value is None:
                continue
            text = (
                value
                if isinstance(value, str)
                else json.dumps(value, ensure_ascii=False, indent=1, default=str)
            )
            body += [f"# {name}", "", text, ""]
        body += ["# Messages", "", _messages_text(messages, system_path)]
        write_atomic(self._inside(path), "\n".join(body).rstrip() + "\n")
        return path, stats

    def write_result(
        self,
        *,
        run_config_id: str,
        eval_id: str,
        eval_config_id: str,
        item_id: str,
        row: dict[str, Any],
        scores: dict[str, Any],
    ) -> tuple[Path, dict[str, Any]]:
        """One train eval run's trace, and its statistics."""
        folder = self.root / safe_name(run_config_id)
        path = folder / safe_name(eval_id) / f"{safe_name(item_id)}.md"
        usage = row.get("task_run_usage")
        usage = usage if isinstance(usage, dict) else {}
        intermediate = row.get("intermediate_outputs")
        path, stats = self._write(
            folder,
            path,
            f"Train trace: item {item_id}, eval {eval_id}",
            [
                ("item_id", item_id),
                ("eval_id", eval_id),
                ("eval_config_id", eval_config_id),
                ("run_config_id", run_config_id),
                ("eval_run_id", row.get("id")),
                ("scores", scores),
                ("skipped_reason", row.get("skipped_reason")),
                ("skipped_detail", row.get("skipped_detail")),
                ("task_cost_usd", _number(usage.get("cost"))),
                ("latency_ms", _number(usage.get("total_llm_latency_ms"))),
                ("input_tokens", _number(usage.get("input_tokens"))),
                ("output_tokens", _number(usage.get("output_tokens"))),
                ("cached_tokens", _number(usage.get("cached_tokens"))),
            ],
            row.get("task_run_trace"),
            usage,
            [
                (
                    "Judge reason",
                    intermediate
                    if isinstance(intermediate, dict) and intermediate
                    else None,
                ),
                (
                    "Input",
                    row.get("input") if isinstance(row.get("input"), str) else None,
                ),
                (
                    "Output",
                    row.get("output") if isinstance(row.get("output"), str) else None,
                ),
            ],
        )
        record = {
            "item_id": item_id,
            "eval_id": eval_id,
            "eval_run_id": str(row.get("id")) if row.get("id") else None,
            "trace_path": str(path),
            **stats,
        }
        write_atomic(
            self._inside(
                folder / STATS / safe_name(eval_id) / f"{safe_name(item_id)}.json"
            ),
            json.dumps(record, indent=1, ensure_ascii=False) + "\n",
        )
        return path, stats

    def write_task_run(
        self, *, run: dict[str, Any], item_id: str | None
    ) -> tuple[Path, dict[str, Any]]:
        """One train task run's trace (get_task_run, run_task)."""
        folder = self.root / TASK_RUNS
        run_id = str(run.get("id") or "unknown")
        usage = run.get("usage") if isinstance(run.get("usage"), dict) else {}
        out = run.get("output") if isinstance(run.get("output"), dict) else {}
        output = out.get("output") if isinstance(out, dict) else None
        return self._write(
            folder,
            folder / f"{safe_name(run_id)}.md",
            f"Train task run {run_id}",
            [
                ("task_run_id", run_id),
                ("item_id", item_id),
                ("task_cost_usd", _number(usage.get("cost"))),
                ("latency_ms", _number(usage.get("total_llm_latency_ms"))),
                ("input_tokens", _number(usage.get("input_tokens"))),
                ("output_tokens", _number(usage.get("output_tokens"))),
                ("cached_tokens", _number(usage.get("cached_tokens"))),
            ],
            run.get("trace"),
            usage,
            [
                (
                    "Input",
                    run.get("input") if isinstance(run.get("input"), str) else None,
                ),
                ("Output", output if isinstance(output, str) else None),
            ],
        )

    def write_expected(self, item_id: str, reference: Any) -> Path:
        """A train item's full expected output, as JSON."""
        path = self._inside(self.root / EXPECTED / f"{safe_name(item_id)}.json")
        write_atomic(
            path,
            json.dumps(reference, ensure_ascii=False, indent=1, default=str) + "\n",
        )
        return path

    # ------------------------------------------------------------ the stats file

    def stats_paths(self, run_config_id: str) -> tuple[Path, Path]:
        folder = self.root / safe_name(run_config_id)
        return folder / f"{STATS}.json", folder / f"{STATS}.md"

    def write_stats(self, run_config_id: str) -> tuple[Path, Path]:
        """Rebuild `_stats.json` and `_stats.md` of a run config from every
        item statistics file under it. Each call reads all of them, so two
        gateway processes that write at once lose no item."""
        folder = self.root / safe_name(run_config_id)
        items = []
        for path in sorted((folder / STATS).glob("*/*.json")):
            try:
                items.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        tools: dict[str, dict[str, int]] = {}
        for item in items:
            for name, entry in (item.get("tools") or {}).items():
                total = tools.setdefault(name, _tool_entry())
                for key in ("calls", "errors", "result_chars"):
                    total[key] += int(entry.get(key) or 0)
                total["max_result_chars"] = max(
                    total["max_result_chars"], int(entry.get("max_result_chars") or 0)
                )
        sources = sorted({str(i.get("usage_source")) for i in items})
        data = {
            "run_config_id": run_config_id,
            "items": items,
            "tools": dict(sorted(tools.items())),
            "usage_sources": sources,
            "note": USAGE_NOTE,
        }
        json_path, md_path = self.stats_paths(run_config_id)
        write_atomic(
            self._inside(json_path),
            json.dumps(data, indent=1, ensure_ascii=False) + "\n",
        )
        write_atomic(self._inside(md_path), _stats_markdown(data, json_path))
        return json_path, md_path


USAGE_NOTE = (
    "usage_source per item: per_turn = the stored trace carries usage and"
    " latency on each assistant message (turn_usage lists them); per_run_only ="
    " it does not, and run_usage is the run's total from the eval run;"
    " none = neither was stored. Tool errors are the tool results Kiln marks"
    " is_error or error_message."
)


def _stats_markdown(data: dict[str, Any], json_path: Path) -> str:
    lines = [
        f"# Trace statistics: run config {data['run_config_id']}",
        "",
        f"Full data, per turn and per tool: {json_path}",
        "",
        data["note"],
        "",
        "## Items",
        "",
        "| eval | item | turns | tool calls | tool errors | input tokens |"
        " output tokens | cached tokens | cost | usage source | largest tool"
        " result (chars) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in data["items"]:
        run = item.get("run_usage") or {}
        biggest = max(
            (e.get("max_result_chars", 0) for e in (item.get("tools") or {}).values()),
            default=0,
        )
        lines.append(
            "| "
            + " | ".join(
                _value(v)
                for v in (
                    item.get("eval_id"),
                    item.get("item_id"),
                    item.get("assistant_turns"),
                    item.get("tool_calls"),
                    item.get("tool_errors"),
                    run.get("input_tokens"),
                    run.get("output_tokens"),
                    run.get("cached_tokens"),
                    run.get("cost"),
                    item.get("usage_source"),
                    biggest,
                )
            )
            + " |"
        )
    lines += [
        "",
        "## Tools",
        "",
        "| tool | calls | errors | result chars | largest result |",
        "|---|---|---|---|---|",
    ]
    for name, entry in data["tools"].items():
        lines.append(
            f"| {name} | {entry['calls']} | {entry['errors']} |"
            f" {entry['result_chars']} | {entry['max_result_chars']} |"
        )
    return "\n".join(lines) + "\n"
