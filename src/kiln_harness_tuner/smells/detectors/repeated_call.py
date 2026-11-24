"""One stored train run of the baseline makes the same call the reference
count of times or more: smell `repeated-identical-calls`. One detection for
each such tool, the largest count first.

The same call is the same tool with the same arguments
(`trace_files.trace_stats`, `same_call_max`). The calls do not have to be
in a row, so a cycle of two or more calls is counted too. Whether the state
changed between the calls needs a reader.
"""

from __future__ import annotations

from kiln_harness_tuner.schemas import Detection
from kiln_harness_tuner.smells.model import Detector, DetectorInputs
from kiln_harness_tuner.smells.thresholds import (
    REPEATED_CALL_COUNT,
    REPEATED_CALL_COUNT_SOURCE,
)
from kiln_harness_tuner.smells.traces import NAME_CHARS, place


def detect(inputs: DetectorInputs) -> list[Detection]:
    runs = inputs.trace_stats
    # tool name -> (the largest count, its run, the runs at the reference)
    most: dict[str, tuple[int, dict, int]] = {}
    for run in runs:
        for name, times in (run.get("same_call_max") or {}).items():
            times = int(times or 0)
            if times < REPEATED_CALL_COUNT:
                continue
            best, where, count = most.get(name, (0, run, 0))
            if times > best:
                best, where = times, run
            most[name] = (best, where, count + 1)
    found = []
    for name, (times, where, count) in sorted(
        most.items(), key=lambda kv: (-kv[1][0], kv[0])
    ):
        found.append(
            Detection(
                smell=DETECTOR.smell,
                detector=DETECTOR.id,
                scope="train",
                fact=(
                    f"One run called the tool `{name[:NAME_CHARS]}` {times:,} times"
                    f" with the same arguments ({place(where)}). {count:,} of"
                    f" {len(runs):,} stored train runs have {REPEATED_CALL_COUNT} or"
                    f" more such calls. The reference is {REPEATED_CALL_COUNT}."
                ),
                value=times,
                reference=REPEATED_CALL_COUNT,
                reference_source=REPEATED_CALL_COUNT_SOURCE,
            )
        )
    return found


DETECTOR = Detector(
    id="repeated_call",
    smell="repeated-identical-calls",
    run=detect,
    reads_traces=True,
)
