"""Inputs for the smell detectors that read traces, and for `small_skill`.
A helper module, not a test module: `test_smells.py` and
`test_trace_detectors.py` read it."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from kiln_harness_tuner.gateway.trace_files import trace_stats
from kiln_harness_tuner.prompt_text import RcText, SkillText
from kiln_harness_tuner.smells import DetectorInputs, ModelFacts
from kiln_harness_tuner.smells.detectors.run_near_limit import share_of
from kiln_harness_tuner.smells.thresholds import (
    KILN_TOOL_STEPS_PER_TURN,
    LARGE_TOOL_RESULT_CHARS,
    REPEATED_CALL_COUNT,
    SMALL_SKILL_BODY_CHARS,
    UNUSED_MIN_RUNS,
)

Step = tuple[str, dict[str, Any], str]  # tool name, arguments, result


def trace(*steps: Step, input_tokens: int | None = None) -> list[dict[str, Any]]:
    """One stored trace: the system and user messages, one assistant message
    and one tool result for each step, then the final reply."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "Rules."},
        {"role": "user", "content": "A request."},
    ]
    for index, (name, args, result) in enumerate(steps):
        call = {
            "id": f"c{index}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }
        messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
        messages.append(
            {"role": "tool", "tool_call_id": f"c{index}", "content": result}
        )
    final: dict[str, Any] = {"role": "assistant", "content": "Done."}
    if input_tokens is not None:
        final["usage"] = {"input_tokens": input_tokens, "output_tokens": 5}
    messages.append(final)
    return messages


def run(
    item: str,
    *steps: Step,
    eval_id: str = "eval_1",
    input_tokens: int | None = None,
) -> dict[str, Any]:
    """The trace stats of one stored train run, as `run_detectors` gives
    them to a detector."""
    stats = trace_stats(trace(*steps, input_tokens=input_tokens))
    return {"eval_id": eval_id, "item_id": item, **stats}


def plain_runs(count: int, *steps: Step) -> list[dict[str, Any]]:
    """`count` runs, each of its own train item, with the same steps."""
    return [run(f"item_{i}", *steps) for i in range(count)]


def tool(name: str) -> dict[str, Any]:
    return {"tool_id": f"mcp::{name}", "name": name, "description": "Reads."}


def skill(name: str, body: str = "", files: dict[str, str] | None = None) -> SkillText:
    text = f"---\nname: {name}\ndescription: Reads {name}.\n---\n\n{body}\n"
    return SkillText(
        skill_id=f"id_{name}", name=name, files={"SKILL.md": text, **(files or {})}
    )


def given(
    runs: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    skills: list[SkillText] | None = None,
    window: int | None = None,
) -> DetectorInputs:
    by_id = {s.skill_id: s for s in skills or []}
    return DetectorInputs(
        rc_id="rc_base",
        properties={},
        texts=RcText(prompt="short", skill_ids=tuple(by_id), skills=by_id),
        tool_definitions=tools or [],
        trace_stats=runs or [],
        model=ModelFacts("openai", "gpt_x", "GPT X", date(2026, 1, 1), window),
    )


def same_call(times: int) -> list[Step]:
    return [("lookup", {"id": 7}, "row 7")] * times


# One input on which each detector of this file reports, by detector id.
FIRING: dict[str, DetectorInputs] = {
    "large_tool_result": given(
        [run("item_1", ("list_orders", {}, "r" * (LARGE_TOOL_RESULT_CHARS * 3)))]
    ),
    "repeated_call": given([run("item_1", *same_call(REPEATED_CALL_COUNT * 3))]),
    "run_near_limit": given(
        [
            run(
                "item_1",
                *[
                    ("step", {"n": n}, "ok")
                    for n in range(KILN_TOOL_STEPS_PER_TURN - 1)
                ],
                input_tokens=share_of(1_000) + 1,
            )
        ],
        window=1_000,
    ),
    "unused_tool": given(
        plain_runs(UNUSED_MIN_RUNS * 3, ("lookup", {}, "row")),
        tools=[tool("lookup"), tool("delete_all")],
    ),
    "unused_skill": given(
        plain_runs(UNUSED_MIN_RUNS * 3, ("skill", {"name": "returns"}, "body")),
        skills=[skill("returns"), skill("shipping")],
    ),
    "small_skill": given(skills=[skill("tone", "b" * (SMALL_SKILL_BODY_CHARS // 3))]),
}
