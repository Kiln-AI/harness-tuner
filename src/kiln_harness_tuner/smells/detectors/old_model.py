"""The baseline's model was released more than the reference time before the
run: smell `old-model`.

The release date comes from the run's model catalogue. A model with no
known date, such as a fine-tune or a custom model, is never reported.
"""

from __future__ import annotations

import calendar
from datetime import date

from kiln_harness_tuner.schemas import Detection
from kiln_harness_tuner.smells.model import Detector, DetectorInputs
from kiln_harness_tuner.smells.thresholds import (
    OLD_MODEL_MONTHS,
    OLD_MODEL_MONTHS_SOURCE,
)

# A friendly name and a model id are each under 80 characters in Kiln's file
# today. The cut keeps the fact inside the 300-character limit of a detection.
NAME_CHARS = 80


def add_months(d: date, months: int) -> date:
    """`d` plus `months` calendar months. The day is clamped to the last day
    of the month it lands in: 30 June plus 8 months is 28 February."""
    index = d.year * 12 + d.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def whole_months(start: date, end: date) -> int:
    """The whole calendar months from `start` to `end`: the largest n with
    `add_months(start, n) <= end`."""
    months = (end.year - start.year) * 12 + end.month - start.month
    return months - 1 if add_months(start, months) > end else months


def detect(inputs: DetectorInputs) -> list[Detection]:
    facts, today = inputs.model, inputs.run_date
    if facts is None or facts.release_date is None or today is None:
        return []
    released = facts.release_date
    if not add_months(released, OLD_MODEL_MONTHS) < today:
        return []
    months = whole_months(released, today)
    return [
        Detection(
            smell=DETECTOR.smell,
            detector=DETECTOR.id,
            scope="harness",
            fact=(
                f"The baseline's model, {facts.name[:NAME_CHARS]}"
                f" ({facts.model[:NAME_CHARS]}), was released on"
                f" {released.isoformat()}, {months} months before this run."
                f" The reference is {OLD_MODEL_MONTHS} months."
            ),
            value=months,
            reference=OLD_MODEL_MONTHS,
            reference_source=OLD_MODEL_MONTHS_SOURCE,
        )
    ]


DETECTOR = Detector(id="old_model", smell="old-model", run=detect)
