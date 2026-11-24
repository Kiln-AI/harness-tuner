"""An attached skill has a body shorter than the reference and no other
file: smell `skill-too-small-to-earn-its-place`. One detection for each
such skill.

The body is the text of `SKILL.md` after its front matter. A skill with a
reference file or a text asset is never reported: a short body that routes
to other files uses progressive disclosure. A binary asset is not read
(`prompt_text.read_skill`), so a skill whose only other files are binary is
reported; the baseline reviewer reads the bundle.
"""

from __future__ import annotations

from kiln_harness_tuner.prompt_text import SKILL_MD
from kiln_harness_tuner.schemas import Detection
from kiln_harness_tuner.smells.model import Detector, DetectorInputs
from kiln_harness_tuner.smells.thresholds import (
    SMALL_SKILL_BODY_CHARS,
    SMALL_SKILL_BODY_CHARS_SOURCE,
)
from kiln_harness_tuner.smells.traces import NAME_CHARS


def skill_body(skill_md: str) -> str:
    """The text of a SKILL.md after its front matter, without the white
    space around it. A file with no closed front matter is all body."""
    lines = skill_md.splitlines()
    if lines and lines[0].strip() == "---":
        for index, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                return "\n".join(lines[index + 1 :]).strip()
    return skill_md.strip()


def detect(inputs: DetectorInputs) -> list[Detection]:
    found = []
    for skill in (inputs.texts.skills or {}).values():
        if set(skill.files) != {SKILL_MD}:
            continue
        chars = len(skill_body(skill.files[SKILL_MD]))
        if chars >= SMALL_SKILL_BODY_CHARS:
            continue
        name = (skill.name or skill.skill_id)[:NAME_CHARS]
        found.append(
            Detection(
                smell=DETECTOR.smell,
                detector=DETECTOR.id,
                scope="harness",
                fact=(
                    f"The body of the skill `{name}` has {chars:,} characters,"
                    " and the skill has no other file. The reference is"
                    f" {SMALL_SKILL_BODY_CHARS:,}."
                ),
                value=chars,
                reference=SMALL_SKILL_BODY_CHARS,
                reference_source=SMALL_SKILL_BODY_CHARS_SOURCE,
            )
        )
    return found


DETECTOR = Detector(
    id="small_skill", smell="skill-too-small-to-earn-its-place", run=detect
)
