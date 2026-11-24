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

OLD_MODEL_MONTHS = 8
# A house default, set by the project owner on 2026-11-13. No published
# value. A model line usually has a newer release within this time.
OLD_MODEL_MONTHS_SOURCE = (
    "smells.thresholds.OLD_MODEL_MONTHS: a house default, set by the project"
    " owner on 2026-11-13; no published value is known"
)

LARGE_TOOL_RESULT_CHARS = 50_000
# Claude Code saves a text tool result longer than this to a file, and the
# model gets the path in its place (Claude Code documentation, "Connect
# Claude Code to tools via MCP", section "MCP output limits and warnings",
# read 2026-11-25). A product limit, not a measured optimum.
LARGE_TOOL_RESULT_CHARS_SOURCE = (
    "the length at which Claude Code saves a text tool result to a file in"
    " place of the context (Claude Code documentation, read 2026-11-25)"
)

REPEATED_CALL_COUNT = 5
# Gemini CLI flags a loop at this count of consecutive calls with the same
# tool name and the same arguments (`TOOL_CALL_LOOP_THRESHOLD` in
# `loopDetectionService.ts`, read 2026-11-25). It first sends the model a
# recovery message, and ends the turn if the loop goes on. The detector
# counts the same call anywhere in one run, not only in a row.
REPEATED_CALL_COUNT_SOURCE = (
    "the count of identical tool calls in a row at which Gemini CLI flags a"
    " loop (TOOL_CALL_LOOP_THRESHOLD, read 2026-11-25); here the calls do not"
    " have to be in a row"
)

KILN_TOOL_STEPS_PER_TURN = 100
# Kiln stops a turn with an error at this count of tool steps, and stores no
# result for the run. A step is one model call that asks for one tool call
# or more, so parallel calls in one step count once (`MAX_TOOL_CALLS_PER_TURN`
# in `kiln_ai`'s `litellm_adapter.py`, version 1.1.2, read 2026-11-25). A fact
# about Kiln, not a threshold: `NEAR_LIMIT_PERCENT` is the threshold.
KILN_TOOL_STEPS_PER_TURN_SOURCE = (
    "Kiln's limit on the tool steps of one turn (MAX_TOOL_CALLS_PER_TURN in"
    " kiln_ai, which counts model calls that ask for tools; read 2026-11-25)"
)

NEAR_LIMIT_PERCENT = 80
# A default of the tuner, proposed with the detector and not yet set by the
# project owner. No published value for an agent run is known.
NEAR_LIMIT_PERCENT_SOURCE = (
    "a default of the tuner: 80% of a hard limit; no published value for an"
    " agent run is known"
)

UNUSED_MIN_RUNS = 30
# The rule of three (Hanley and Lippman-Hand, JAMA, 1983): when an event is
# seen in none of n independent runs, the 95% upper bound of its rate is
# about 3/n. With no call in 30 runs, the bound is about 10% of runs.
UNUSED_MIN_RUNS_SOURCE = (
    "the rule of three: with no call in 30 runs, the 95% upper bound of the"
    " share of runs that make the call is about 10%"
)

SMALL_SKILL_BODY_CHARS = 400
# About 100 tokens, at four characters for each token: the size that the
# Agent Skills specification gives for the metadata of a skill, which is in
# the context of every run (agentskills.io, "Progressive disclosure", read
# 2026-11-25). A body under this size holds less than its metadata can cost.
SMALL_SKILL_BODY_CHARS_SOURCE = (
    "about 100 tokens: the size that the Agent Skills specification gives for"
    " the always-loaded metadata of a skill (read 2026-11-25)"
)
