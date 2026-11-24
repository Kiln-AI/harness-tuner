"""The run config attaches more skills than the reference: smell
`too-many-skills`.

Kiln gives the attached skills to the model through one skill tool, and
the name and the description of each attached skill are in the context of
every call. So the count of attached skills is the count of descriptions
in context. The body of a skill is not counted: the agent reads it only
when it opens the skill.

The count needs only the run config. When the text of the skills can be
read, the fact also gives the total length of their descriptions.
"""

from __future__ import annotations

from kiln_harness_tuner.prompt_text import SKILL_MD
from kiln_harness_tuner.schemas import Detection
from kiln_harness_tuner.smells.detectors.long_skill_description import (
    skill_description,
)
from kiln_harness_tuner.smells.model import Detector, DetectorInputs
from kiln_harness_tuner.smells.thresholds import MANY_SKILLS, MANY_SKILLS_SOURCE


def detect(inputs: DetectorInputs) -> list[Detection]:
    skills = set(inputs.texts.skill_ids)
    if len(skills) <= MANY_SKILLS:
        return []
    fact = (
        f"The run config attaches {len(skills):,} skills. The reference is"
        f" {MANY_SKILLS:,}."
    )
    texts = inputs.texts.skills
    if texts is not None and skills <= set(texts):
        chars = sum(
            len(skill_description(texts[skill_id].files.get(SKILL_MD, "")) or "")
            for skill_id in skills
        )
        fact += f" Their descriptions have {chars:,} characters in all."
    return [
        Detection(
            smell=DETECTOR.smell,
            detector=DETECTOR.id,
            scope="harness",
            fact=fact,
            value=len(skills),
            reference=MANY_SKILLS,
            reference_source=MANY_SKILLS_SOURCE,
        )
    ]


DETECTOR = Detector(id="many_skills", smell="too-many-skills", run=detect)
