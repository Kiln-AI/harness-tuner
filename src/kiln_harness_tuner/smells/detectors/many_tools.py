"""The run config attaches more tools than the reference: smell
`too-many-tools`.

A Kiln run config puts the definition of each attached tool in the context
of every call, so the count of attached tools is the count in context. A
tool that a search tool finds and loads at run time is not attached, so it
is not counted. Skills are not counted: Kiln gives all the attached skills
to the model through one skill tool, and skills have their own smells.
"""

from __future__ import annotations

from kiln_harness_tuner.prompt_text import SKILL_TOOL_PREFIX
from kiln_harness_tuner.schemas import Detection
from kiln_harness_tuner.smells.model import Detector, DetectorInputs
from kiln_harness_tuner.smells.thresholds import MANY_TOOLS, MANY_TOOLS_SOURCE


def detect(inputs: DetectorInputs) -> list[Detection]:
    tools = {
        tool_id
        for tool_id in inputs.texts.tool_ids
        if not tool_id.startswith(SKILL_TOOL_PREFIX)
    }
    if len(tools) <= MANY_TOOLS:
        return []
    return [
        Detection(
            smell=DETECTOR.smell,
            detector=DETECTOR.id,
            scope="harness",
            fact=(
                f"The run config attaches {len(tools):,} tools, not counting"
                f" skills. The reference is {MANY_TOOLS:,}."
            ),
            value=len(tools),
            reference=MANY_TOOLS,
            reference_source=MANY_TOOLS_SOURCE,
        )
    ]


DETECTOR = Detector(id="many_tools", smell="too-many-tools", run=detect)
