"""The registration of the smell detectors. A new detector is one file in
this folder and one entry in `DETECTORS`."""

from __future__ import annotations

from kiln_harness_tuner.smells.detectors import (
    large_tool_result,
    long_prompt,
    long_skill_description,
    long_tool_description,
    many_tools,
    old_model,
    repeated_call,
    run_near_limit,
    small_skill,
    unused_skill,
    unused_tool,
)
from kiln_harness_tuner.smells.model import Detector

DETECTORS: tuple[Detector, ...] = (
    long_prompt.DETECTOR,
    long_tool_description.DETECTOR,
    long_skill_description.DETECTOR,
    many_tools.DETECTOR,
    old_model.DETECTOR,
    small_skill.DETECTOR,
    large_tool_result.DETECTOR,
    repeated_call.DETECTOR,
    run_near_limit.DETECTOR,
    unused_tool.DETECTOR,
    unused_skill.DETECTOR,
)


def detector_smells(detectors: tuple[Detector, ...] = DETECTORS) -> dict[str, str]:
    """Detector id to smell id. The knowledge check holds the smell
    documents to it (rule `smell-detectors`)."""
    return {d.id: d.smell for d in detectors}
