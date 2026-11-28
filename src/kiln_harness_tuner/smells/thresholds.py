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

MANY_SKILLS = 20
# A default of the tuner, not a measured optimum, from two public numbers.
# The name and the description of each skill cost about 100 tokens at
# startup (Agent Skills specification, agentskills.io). Claude Code starts
# to drop skill descriptions when their listing is over 1% of the model's
# context window (Claude Code documentation, "Extend Claude with skills").
# 20 skills reach that share of a 200,000-token window. Both read
# 2026-11-25. Every skill description is in the context of every call.
MANY_SKILLS_SOURCE = (
    "a default of the tuner, not a measured optimum: at about 100 tokens of"
    " name and description for each skill (Agent Skills specification), 20"
    " skills take 1% of a 200,000-token context window, the share at which"
    " Claude Code starts to drop skill descriptions (both read 2026-11-25)"
)

OLD_MODEL_MONTHS = 8
# A house default, set by the project owner on 2026-11-13. No published
# value. A model line usually has a newer release within this time.
OLD_MODEL_MONTHS_SOURCE = (
    "smells.thresholds.OLD_MODEL_MONTHS: a house default, set by the project"
    " owner on 2026-11-13; no published value is known"
)
