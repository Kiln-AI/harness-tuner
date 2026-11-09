"""A stand-in for `claude -p`, for the packaging smoke test.

`smoke_installed_wheel.py` writes this file to `<tmp>/fakebin/claude` under a
`#!<tool python>` line, so it runs on the installed tool's interpreter and
`mcp` imports. The tuner then starts it as it starts Claude Code. It answers
the two sessions a run makes before it needs a model or a Kiln server:

- **the CLI check** (its prompt holds `KILN-HARNESS-TUNER-READY`): it answers
  with that word;
- **the pre-flight** (it gets `--mcp-config`): it starts the run's gateway from
  that config, lists its tools, calls `ping` and relays the answer.

Each call appends one JSON line to `$FAKE_CLAUDE_LOG`: the folder it was
started in, its argv and the start of its prompt, plus the gateway's tools
and the ping's text (or the error) when it pinged.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# The word `dispatch.claude_cli.CLI_CHECK_WORD` asks for. Not imported: the
# fake must not depend on the code it stands in front of.
CLI_CHECK_WORD = "KILN-HARNESS-TUNER-READY"
GATEWAY_SERVER = "kiln_harness_tuner_gateway"


def option(argv: list[str], name: str) -> str | None:
    """The value of `name VALUE` or `name=VALUE` in argv. Every other flag is
    ignored."""
    for index, arg in enumerate(argv):
        if arg == name and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith(f"{name}="):
            return arg.split("=", 1)[1]
    return None


def result(text: str, session_id: str, *, is_error: bool = False) -> dict[str, Any]:
    """The one object `claude -p --output-format json` prints."""
    return {
        "type": "result",
        "subtype": "error_during_execution" if is_error else "success",
        "is_error": is_error,
        "result": text,
        "session_id": session_id,
        "total_cost_usd": 0,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


async def ping_gateway(config_path: str) -> tuple[list[str], str]:
    """Start the gateway the MCP config names, the way Claude Code does, and
    call its `ping`. Returns the tool names and the ping's text."""
    with open(config_path, encoding="utf-8") as config:
        server = json.load(config)["mcpServers"][GATEWAY_SERVER]
    params = StdioServerParameters(
        command=server["command"], args=server["args"], env=dict(os.environ)
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        reply = await session.call_tool("ping", {})
    text = "".join(part.text for part in reply.content if part.type == "text")
    return [tool.name for tool in tools.tools], text


def main() -> int:
    argv = sys.argv[1:]
    prompt = sys.stdin.read()
    session_id = option(argv, "--session-id") or str(uuid.uuid4())
    record: dict[str, Any] = {"cwd": os.getcwd(), "argv": argv, "prompt": prompt[:200]}
    config_path = option(argv, "--mcp-config")
    if CLI_CHECK_WORD in prompt:
        answer, code = result(CLI_CHECK_WORD, session_id), 0
    elif config_path is not None:
        try:
            record["tools"], record["ping"] = asyncio.run(ping_gateway(config_path))
        except Exception as exc:  # noqa: BLE001 — reported in the log and the answer
            record["error"] = f"{type(exc).__name__}: {exc}"
            answer = result(
                f"fake claude: {record['error']}", session_id, is_error=True
            )
            code = 1
        else:
            envelope = {
                "status": "ok",
                "data": {"ping": record["ping"]},
                "detail": None,
            }
            fenced = json.dumps(envelope, ensure_ascii=False)
            answer = result(f"```json\n{fenced}\n```", session_id)
            code = 0
    else:
        answer = result(
            "fake claude: no CLI check and no --mcp-config", session_id, is_error=True
        )
        code = 1
    log_path = os.environ.get("FAKE_CLAUDE_LOG")
    if log_path:
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(answer, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
