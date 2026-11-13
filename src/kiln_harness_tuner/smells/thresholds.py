"""The reference values of the smell detectors.

Each value is a named constant with a comment that gives its source, and a
`<NAME>_SOURCE` string that a detection carries as its `reference_source`.
No measured optimum is known for any of them, so each is a default that can
change.
"""

from __future__ import annotations

LONG_PROMPT_CHARS = 200_000
# No published limit for a system prompt. A house default, set by the
# project owner: about 50,000 tokens.
LONG_PROMPT_CHARS_SOURCE = (
    "a default of the tuner, about 50,000 tokens; no published limit for a"
    " system prompt is known"
)

LONG_TOOL_DESCRIPTION_CHARS = 4_096
# Claude Code cuts the description of a tool that loads up front at this
# length (Claude Code documentation, "Connect Claude Code to tools via MCP",
# read 2026-11-13).
LONG_TOOL_DESCRIPTION_CHARS_SOURCE = (
    "Claude Code cuts the description of a tool that loads up front at this"
    " length (Claude Code documentation, read 2026-11-13)"
)

LONG_SKILL_DESCRIPTION_CHARS = 1_024
# The maximum length of a skill description in the Agent Skills
# specification (agentskills.io, read 2026-11-13). Every skill description
# is in the context of every run.
LONG_SKILL_DESCRIPTION_CHARS_SOURCE = (
    "the maximum length of a skill description in the Agent Skills"
    " specification (read 2026-11-13)"
)

MANY_TOOLS = 40
# A house default, set by the project owner, not a measured optimum. Every
# attached tool definition is in the context of every call. A skill is not
# counted.
MANY_TOOLS_SOURCE = (
    "a default of the tuner, set by the project owner; not a measured optimum"
)
