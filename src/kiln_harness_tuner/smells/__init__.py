"""Smell detectors in code: small pure functions that state facts about the
design of the baseline agent, each the evidence of one smell document of the
knowledge skill (`kiln-harness-tuner-knowledge/references/smells/`).

`smells` imports `knowledge`. `knowledge` does not import `smells`: its
command line passes `detector_smells()` to the check.
"""

from kiln_harness_tuner.smells.detectors import DETECTORS, detector_smells
from kiln_harness_tuner.smells.model import Detector, DetectorInputs
from kiln_harness_tuner.smells.run import ensure_detections, run_detectors

__all__ = [
    "DETECTORS",
    "Detector",
    "DetectorInputs",
    "detector_smells",
    "ensure_detections",
    "run_detectors",
]
