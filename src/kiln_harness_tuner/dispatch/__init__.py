"""The agent-dispatch seam (docs/architecture.md section 2) — one of the two
seams. `AgentDispatcher` is the protocol; `ClaudeCliDispatcher` is the
reference implementation (`claude -p` subprocess per call)."""

from kiln_harness_tuner.dispatch.base import AgentDispatcher
from kiln_harness_tuner.dispatch.claude_cli import ClaudeCliDispatcher

__all__ = ["AgentDispatcher", "ClaudeCliDispatcher"]
