"""Attached tools that no stored train run of the baseline calls: smell
`unnecessary-tools`. One detection that names the tools.

It is silent until the stored runs cover the reference count of train
items: a tool that few items need is not seen in a small sample. Only the
tools whose definition could be read are checked, because a trace names a
tool by its function name. Skills are not tools here: `unused_skill` reads
them.
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
    if items < UNUSED_MIN_RUNS:
        return []
    attached = list(
        dict.fromkeys(
            str(tool["name"]) for tool in inputs.tool_definitions if tool.get("name")
        )
    )
    called = {name for run in runs for name in run.get("tools") or {}}
    unused = [name for name in attached if name not in called]
    if not unused:
        return []
    return [
        Detection(
            smell=DETECTOR.smell,
            detector=DETECTOR.id,
            scope="train",
            fact=(
                f"{len(unused):,} of {len(attached):,} attached tools are called"
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
    id="unused_tool", smell="unnecessary-tools", run=detect, reads_traces=True
)
