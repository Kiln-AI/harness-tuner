"""The system prompt is longer than the reference: smell `very-long-prompt`."""

from __future__ import annotations

from kiln_harness_tuner.schemas import Detection
from kiln_harness_tuner.smells.model import Detector, DetectorInputs
from kiln_harness_tuner.smells.thresholds import (
    LONG_PROMPT_CHARS,
    LONG_PROMPT_CHARS_SOURCE,
)


def detect(inputs: DetectorInputs) -> list[Detection]:
    prompt = inputs.texts.prompt
    if prompt is None or len(prompt) <= LONG_PROMPT_CHARS:
        return []
    return [
        Detection(
            smell=DETECTOR.smell,
            detector=DETECTOR.id,
            scope="harness",
            fact=(
                f"The system prompt has {len(prompt):,} characters."
                f" The reference is {LONG_PROMPT_CHARS:,}."
            ),
            value=len(prompt),
            reference=LONG_PROMPT_CHARS,
            reference_source=LONG_PROMPT_CHARS_SOURCE,
        )
    ]


DETECTOR = Detector(id="long_prompt", smell="very-long-prompt", run=detect)
