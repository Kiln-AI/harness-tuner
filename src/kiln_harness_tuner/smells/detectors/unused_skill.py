"""Attached skills that no stored train run of the baseline opens: smell
`skill-never-opened`. One detection that names the skills.

Kiln gives the attached skills to the model through one skill tool, called
with the skill's name (`trace_files.trace_stats`, `skills_opened`). The
detector is silent until the stored runs cover the reference count of train
items, and when a skill cannot be read.
"""

from __future__ import annotations

from kiln_harness_tuner.schemas import Detection
from kiln_harness_tuner.smells.model import Detector, DetectorInputs
from kiln_harness_tuner.smells.thresholds import (
    UNUSED_MIN_RUNS,
    UNUSED_MIN_RUNS_SOURCE,
)
from kiln_harness_tuner.smells.traces import item_count, listed

NAMES_BUDGET = 150


def detect(inputs: DetectorInputs) -> list[Detection]:
    runs = inputs.trace_stats
    items = item_count(runs)
    if items < UNUSED_MIN_RUNS or not inputs.texts.skills:
        return []
    attached = list(
        dict.fromkeys(s.name for s in inputs.texts.skills.values() if s.name)
    )
    opened = {name for run in runs for name in run.get("skills_opened") or {}}
    unused = [name for name in attached if name not in opened]
    if not unused:
        return []
    return [
        Detection(
            smell=DETECTOR.smell,
            detector=DETECTOR.id,
            scope="train",
            fact=(
                f"{len(unused):,} of {len(attached):,} attached skills are opened"
                f" in no stored train run, over {items:,} train items:"
                f" {listed(unused, NAMES_BUDGET)}. The reference is"
                f" {UNUSED_MIN_RUNS} items or more."
            ),
            value=items,
            reference=UNUSED_MIN_RUNS,
            reference_source=UNUSED_MIN_RUNS_SOURCE,
        )
    ]


DETECTOR = Detector(
    id="unused_skill", smell="skill-never-opened", run=detect, reads_traces=True
)
