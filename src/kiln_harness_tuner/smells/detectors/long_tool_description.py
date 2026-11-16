"""An attached tool's description is longer than the reference: smell
`very-long-tool-description`. One detection for each such tool."""

from __future__ import annotations

from kiln_harness_tuner.schemas import Detection
from kiln_harness_tuner.smells.model import Detector, DetectorInputs
from kiln_harness_tuner.smells.thresholds import (
    LONG_TOOL_DESCRIPTION_CHARS,
    LONG_TOOL_DESCRIPTION_CHARS_SOURCE,
)


def detect(inputs: DetectorInputs) -> list[Detection]:
    found = []
    for tool in inputs.tool_definitions:
        description = tool.get("description")
        if not isinstance(description, str):
            continue
        if len(description) <= LONG_TOOL_DESCRIPTION_CHARS:
            continue
        # Kiln and the providers limit a tool name to 64 characters; the
        # cut keeps the fact inside its length limit for any other source.
        name = str(tool.get("name") or tool.get("tool_id") or "?")[:64]
        found.append(
            Detection(
                smell=DETECTOR.smell,
                detector=DETECTOR.id,
                scope="harness",
                fact=(
                    f"The description of the tool `{name}` has"
                    f" {len(description):,} characters. The reference is"
                    f" {LONG_TOOL_DESCRIPTION_CHARS:,}."
                ),
                value=len(description),
                reference=LONG_TOOL_DESCRIPTION_CHARS,
                reference_source=LONG_TOOL_DESCRIPTION_CHARS_SOURCE,
            )
        )
    return found


DETECTOR = Detector(
    id="long_tool_description", smell="very-long-tool-description", run=detect
)
