"""PreToolUse hook for loop sessions: only the role's gateway
operations, and the skill installer where the role has it, are callable
among MCP tools.

Loop agents have no generic Kiln API tool: their MCP config holds the
gateway (and, for the builder and the probe, the skill installer) and
nothing else, and `--allowedTools` names the role's operations one by one.
This hook is the third, independent check: any `mcp__…` tool that is not
the role's own is refused, whatever the config held. File tools are fenced
by the rendered permissions and the OS sandbox, not here.

Exit code 2 blocks the call and hands the message back to the model.
"""

from __future__ import annotations

import argparse
import json
import sys

from kiln_harness_tuner.gateway.ops import ROLE_OPS, role_tools

BLOCK_EXIT_CODE = 2
MCP_MATCHER = "mcp__.*"
SKILL_INSTALLER_TOOLS = {
    "create_experiment_rc": (
        "mcp__skill_installer__install_skill_bundle",
        "mcp__skill_installer__export_skill_bundle",
    ),
    "probe": ("mcp__skill_installer__install_skill_bundle",),
}


def allowed_tools(role: str) -> frozenset[str]:
    return frozenset(role_tools(role)) | frozenset(SKILL_INSTALLER_TOOLS.get(role, ()))


def decide(tool_name: str, role: str) -> str | None:
    """None to allow, or the message that blocks."""
    if not tool_name.startswith("mcp__"):
        return None
    if role not in ROLE_OPS:
        return f"blocked: role {role!r} has no gateway operations."
    if tool_name in allowed_tools(role):
        return None
    return (
        f"blocked: {tool_name} is not one of the {role} role's tools. Loop agents"
        " reach Kiln only through their gateway operations"
        " (mcp__kiln_harness_tuner_gateway__…); if you need something they do not"
        " offer, say so in what you return."
    )


def hook_command(role: str, python: str | None = None) -> str:
    import shlex

    parts = [
        python or sys.executable,
        "-m",
        "kiln_harness_tuner.gateway.hook",
        "--role",
        role,
    ]
    return " ".join(shlex.quote(p) for p in parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kiln-harness-tuner-gateway-hook")
    parser.add_argument("--role", required=True)
    args = parser.parse_args(argv)
    try:
        event = json.load(sys.stdin)
    except ValueError:
        # An unreadable event is refused: the hook cannot tell what it is.
        print("blocked: unreadable tool event", file=sys.stderr)
        return BLOCK_EXIT_CODE
    name = str(event.get("tool_name") or "") if isinstance(event, dict) else ""
    message = decide(name, args.role)
    if message is None:
        return 0
    print(message, file=sys.stderr)
    return BLOCK_EXIT_CODE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
