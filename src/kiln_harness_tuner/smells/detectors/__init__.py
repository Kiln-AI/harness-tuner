"""The registration of the smell detectors. A new detector is one file in
this folder and one entry in `DETECTORS`."""

from __future__ import annotations

from kiln_harness_tuner.smells.detectors import (
    long_prompt,
    long_skill_description,
    long_tool_description,
    old_model,
)
from kiln_harness_tuner.smells.model import Detector

DETECTORS: tuple[Detector, ...] = (
    long_prompt.DETECTOR,
    long_tool_description.DETECTOR,
    long_skill_description.DETECTOR,
    old_model.DETECTOR,
)


def detector_smells(detectors: tuple[Detector, ...] = DETECTORS) -> dict[str, str]:
    """Detector id to smell id. The knowledge check holds the smell
    documents to it (rule `smell-detectors`)."""
    return {d.id: d.smell for d in detectors}
