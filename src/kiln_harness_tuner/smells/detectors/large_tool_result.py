"""A tool result in the baseline's stored train runs is longer than the
reference: smell `tool-results-flood-the-context`. One detection for each
such tool, the largest result first.

A result is counted in characters, as the trace stores it. The call that
the stored trace cannot name (no tool call with the result's id) is not
reported.
"""

from __future__ import annotations

from kiln_harness_tuner.schemas import Detection
from kiln_harness_tuner.smells.model import Detector, DetectorInputs
from kiln_harness_tuner.smells.thresholds import (
    LARGE_TOOL_RESULT_CHARS,
    LARGE_TOOL_RESULT_CHARS_SOURCE,
)
from kiln_harness_tuner.smells.traces import NAME_CHARS, place


def detect(inputs: DetectorInputs) -> list[Detection]:
    runs = inputs.trace_stats
    # tool name -> (the largest result, its run, the runs over the reference)
    largest: dict[str, tuple[int, dict, int]] = {}
    for run in runs:
        for name, entry in (run.get("tools") or {}).items():
            chars = int(entry.get("max_result_chars") or 0)
            if name == "?" or chars <= LARGE_TOOL_RESULT_CHARS:
                continue
            best, where, count = largest.get(name, (0, run, 0))
            if chars > best:
                best, where = chars, run
            largest[name] = (best, where, count + 1)
    found = []
    for name, (chars, where, count) in sorted(
        largest.items(), key=lambda kv: (-kv[1][0], kv[0])
    ):
        found.append(
            Detection(
                smell=DETECTOR.smell,
                detector=DETECTOR.id,
                scope="train",
                fact=(
                    f"The tool `{name[:NAME_CHARS]}` returned {chars:,} characters"
                    f" in one result ({place(where)}). {count:,} of {len(runs):,}"
                    " stored train runs have a result of it over the reference."
                    f" The reference is {LARGE_TOOL_RESULT_CHARS:,}."
                ),
                value=chars,
                reference=LARGE_TOOL_RESULT_CHARS,
                reference_source=LARGE_TOOL_RESULT_CHARS_SOURCE,
            )
        )
    return found


DETECTOR = Detector(
    id="large_tool_result",
    smell="tool-results-flood-the-context",
    run=detect,
    reads_traces=True,
)
