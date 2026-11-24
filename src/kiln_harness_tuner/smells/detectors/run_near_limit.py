"""A stored train run of the baseline uses the reference share of a hard
limit, or more: smell `runs-close-to-a-limit`.

Two limits are read, and each gives at most one detection:

- Kiln's limit on the tool steps of one turn. A step is one model call
  that asks for one tool call or more. Kiln stops a turn at the limit with
  an error and stores no result, so a stored run is always under it.
- The context window of the baseline's model, against the input tokens of
  one model call. It is read only when the model catalogue knows the window
  and the trace stores the usage of each call.

A run that a limit stopped has no stored trace, so this detector cannot
see it. It reports the runs that came close.
"""

from __future__ import annotations

from kiln_harness_tuner.schemas import Detection
from kiln_harness_tuner.smells.model import Detector, DetectorInputs
from kiln_harness_tuner.smells.thresholds import (
    KILN_TOOL_STEPS_PER_TURN,
    KILN_TOOL_STEPS_PER_TURN_SOURCE,
    NEAR_LIMIT_PERCENT,
    NEAR_LIMIT_PERCENT_SOURCE,
)
from kiln_harness_tuner.smells.traces import place

MODEL_CHARS = 40


def share_of(limit: int) -> int:
    """The reference share of a limit, as a whole count, rounded up."""
    return -(-limit * NEAR_LIMIT_PERCENT // 100)


def _largest_input(run: dict) -> int:
    """The largest input of one model call of a run, in tokens; 0 when the
    trace does not store the usage of each call."""
    tokens = [turn.get("input_tokens") for turn in run.get("turn_usage") or []]
    return int(max((t for t in tokens if isinstance(t, (int, float))), default=0))


def _tool_steps(inputs: DetectorInputs) -> list[Detection]:
    runs = inputs.trace_stats
    reference = share_of(KILN_TOOL_STEPS_PER_TURN)
    counts = [(int(run.get("max_tool_steps_in_turn") or 0), run) for run in runs]
    near = [(steps, run) for steps, run in counts if steps >= reference]
    if not near:
        return []
    steps, where = max(near, key=lambda pair: pair[0])
    return [
        Detection(
            smell=DETECTOR.smell,
            detector=DETECTOR.id,
            scope="train",
            fact=(
                f"One turn of a run has {steps:,} tool steps ({place(where)})."
                f" Kiln stops a turn at {KILN_TOOL_STEPS_PER_TURN:,} tool steps."
                f" {len(near):,} of {len(runs):,} stored train runs have a turn"
                f" with {reference:,} or more. The reference is"
                f" {NEAR_LIMIT_PERCENT}% of the limit."
            ),
            value=steps,
            reference=reference,
            reference_source=(
                f"{NEAR_LIMIT_PERCENT_SOURCE}; {KILN_TOOL_STEPS_PER_TURN_SOURCE}"
            ),
        )
    ]


def _context_window(inputs: DetectorInputs) -> list[Detection]:
    model = inputs.model
    window = model.context_window if model is not None else None
    if model is None or not window or window <= 0:
        return []
    runs = [run for run in inputs.trace_stats if _largest_input(run) > 0]
    reference = share_of(window)
    near = [run for run in runs if _largest_input(run) >= reference]
    if not near:
        return []
    where = max(near, key=_largest_input)
    return [
        Detection(
            smell=DETECTOR.smell,
            detector=DETECTOR.id,
            scope="train",
            fact=(
                f"One model call read {_largest_input(where):,} input tokens"
                f" ({place(where)}). The context window of"
                f" {model.name[:MODEL_CHARS]} is {window:,} tokens."
                f" {len(near):,} of {len(runs):,} stored train runs with usage"
                f" for each call reach {reference:,}. The reference is"
                f" {NEAR_LIMIT_PERCENT}% of the window."
            ),
            value=_largest_input(where),
            reference=reference,
            reference_source=(
                f"{NEAR_LIMIT_PERCENT_SOURCE}; the context window is from the"
                " run's model catalogue"
            ),
        )
    ]


def detect(inputs: DetectorInputs) -> list[Detection]:
    return [*_tool_steps(inputs), *_context_window(inputs)]


DETECTOR = Detector(
    id="run_near_limit",
    smell="runs-close-to-a-limit",
    run=detect,
    reads_traces=True,
)
