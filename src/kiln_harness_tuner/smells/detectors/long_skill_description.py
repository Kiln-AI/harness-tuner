"""An attached skill's description is longer than the reference: smell
`very-long-skill-description`. One detection for each such skill.

Kiln limits this field to the same length today, so this detector reports
only when that limit changes or a skill comes from another source.
"""

from __future__ import annotations

import yaml

from kiln_harness_tuner.prompt_text import SKILL_MD
from kiln_harness_tuner.schemas import Detection
from kiln_harness_tuner.smells.model import Detector, DetectorInputs
from kiln_harness_tuner.smells.thresholds import (
    LONG_SKILL_DESCRIPTION_CHARS,
    LONG_SKILL_DESCRIPTION_CHARS_SOURCE,
)


def skill_description(skill_md: str) -> str | None:
    """The `description` of a SKILL.md front matter, or None when the file
    has no front matter, it does not parse, or it has no text description."""
    lines = skill_md.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return None
    try:
        meta = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError:
        return None
    description = meta.get("description") if isinstance(meta, dict) else None
    return description if isinstance(description, str) else None


def detect(inputs: DetectorInputs) -> list[Detection]:
    found = []
    for skill in (inputs.texts.skills or {}).values():
        description = skill_description(skill.files.get(SKILL_MD, ""))
        if description is None or len(description) <= LONG_SKILL_DESCRIPTION_CHARS:
            continue
        name = (skill.name or skill.skill_id)[:64]
        found.append(
            Detection(
                smell=DETECTOR.smell,
                detector=DETECTOR.id,
                scope="harness",
                fact=(
                    f"The description of the skill `{name}` has"
                    f" {len(description):,} characters. The reference is"
                    f" {LONG_SKILL_DESCRIPTION_CHARS:,}."
                ),
                value=len(description),
                reference=LONG_SKILL_DESCRIPTION_CHARS,
                reference_source=LONG_SKILL_DESCRIPTION_CHARS_SOURCE,
            )
        )
    return found


DETECTOR = Detector(
    id="long_skill_description", smell="very-long-skill-description", run=detect
)
