"""The detection model: what a smell detector reads, and what it is."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from kiln_harness_tuner.prompt_text import RcText
from kiln_harness_tuner.schemas import Detection


@dataclass(frozen=True)
class ModelFacts:
    """The baseline's model. `name`: the catalogue's friendly name, or the
    model id when the catalogue does not have the model. `release_date`:
    from the catalogue, an exact or a loose match; None when it is not
    known, for example for a fine-tune."""

    provider: str
    model: str
    name: str
    release_date: date | None


@dataclass(frozen=True)
class DetectorInputs:
    """Everything a detector can read about one baseline, collected once by
    `run_detectors`.

    `properties`: the run config's properties. `texts`: its prompt, skills
    and code tools (`prompt_text.rc_texts`). `tool_definitions`: one dict for
    each attached tool whose definition could be read, with `tool_id`,
    `name`, `description` and `parameters`. `trace_stats`: one
    `trace_files.trace_stats` dict for each stored train run of the
    baseline, with its `eval_id` and `item_id`. It is empty unless a
    registered detector reads traces. `model`: the baseline's model, with
    its release date from the run's model catalogue; None when the run
    config names no model. `run_date`: the date the run started; None when
    it is not known."""

    rc_id: str
    properties: dict[str, Any]
    texts: RcText
    tool_definitions: list[dict[str, Any]] = field(default_factory=list)
    trace_stats: list[dict[str, Any]] = field(default_factory=list)
    model: ModelFacts | None = None
    run_date: date | None = None


@dataclass(frozen=True)
class Detector:
    """One detector of one smell. `run` is a pure function: it makes no call
    to Kiln and reads no file, and it returns an empty list when the smell
    is not seen. `reads_traces`: the detector reads `trace_stats`, so
    `run_detectors` reads the stored train rows for it."""

    id: str  # lower case with "_", for example "long_prompt"
    smell: str  # the smell document's id
    run: Callable[[DetectorInputs], list[Detection]]
    reads_traces: bool = False
