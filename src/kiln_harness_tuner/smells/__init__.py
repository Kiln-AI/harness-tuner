"""Smells in the design of the baseline agent. Detectors in code: small pure
functions that state facts, each the evidence of one smell document of the
knowledge skill (`kiln-harness-tuner-knowledge/references/smells/`). Then
the baseline reviewer, a loop agent that names the smells that need a
reader (`ensure_baseline_reviewed`).

`smells` imports `knowledge`. `knowledge` does not import `smells`: its
command line passes `detector_smells()` to the check.
"""

from kiln_harness_tuner.smells.detectors import DETECTORS, detector_smells
from kiln_harness_tuner.smells.model import Detector, DetectorInputs
from kiln_harness_tuner.smells.run import (
    baseline_trace_samples,
    ensure_baseline_reviewed,
    ensure_detections,
    run_detectors,
)

__all__ = [
    "DETECTORS",
    "Detector",
    "DetectorInputs",
    "baseline_trace_samples",
    "detector_smells",
    "ensure_baseline_reviewed",
    "ensure_detections",
    "run_detectors",
]
