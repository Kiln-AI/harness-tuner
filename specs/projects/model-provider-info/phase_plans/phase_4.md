---
status: complete
---

# Phase 4: The old-model detector, its smell document, and the backlog

## Overview

This phase adds the code detector `old_model`. It reads the release date of
the baseline's model from the run's model catalogue (phase 2) and fires
when the model was released more than `OLD_MODEL_MONTHS` (8) calendar
months before the run started. The detection points to a new smell
document, `old-model`, in the knowledge skill. The repo-level backlog
(`specs/backlog.md`) marks the entries "Model and provider information" and
"Smell: an old model" as done by this project, and the entry "The task
shape for loop agents" no longer names `list_available_models`.

Architecture section 7, functional spec section 8 and the backlog line of
section 10. The project-level `backlog.md` is not changed in this phase.

## Steps

1. `src/kiln_harness_tuner/smells/thresholds.py`: add

   ```
   OLD_MODEL_MONTHS = 8
   # A house default, set by the project owner on 2026-11-13. No published
   # value. A model line usually has a newer release within this time.
   OLD_MODEL_MONTHS_SOURCE = "smells.thresholds.OLD_MODEL_MONTHS: a house default, ..."
   ```

   The `<NAME>_SOURCE` string follows the convention of the other
   thresholds, which a test holds.

2. `src/kiln_harness_tuner/smells/model.py`:

   ```
   @dataclass(frozen=True)
   class ModelFacts:
       provider: str
       model: str
       name: str
       release_date: date | None
   ```

   and two new fields on `DetectorInputs`, both with defaults:
   `model: ModelFacts | None = None`, `run_date: date | None = None`.
   Export `ModelFacts` from `smells/__init__.py`.

3. `src/kiln_harness_tuner/smells/run.py`, `_collect`:
   - `_model_facts(ctx, properties) -> ModelFacts | None`: provider and
     model from `model_provider_name` and `model_name` (None when either is
     not a non-empty string); the row of `ctx.model_catalog.model(model)`
     gives `name` and `release_date` (parsed with `date.fromisoformat`, a
     bad value gives None); without a row, `ModelFacts(provider, model,
     model, None)`.
   - `run_date = ctx.run_started_at.date()` when set, else None.
   - No new call to Kiln.

4. `src/kiln_harness_tuner/smells/detectors/old_model.py` (new):

   ```
   def add_months(d: date, months: int) -> date      # day clamped to the month's last day
   def whole_months(start: date, end: date) -> int   # the largest n with add_months(start, n) <= end
   def detect(inputs: DetectorInputs) -> list[Detection]
   DETECTOR = Detector(id="old_model", smell="old-model", run=detect)
   ```

   Silent without facts, without a release date or without a run date.
   Fires when `add_months(release_date, OLD_MODEL_MONTHS) < run_date`. The
   fact is "The baseline's model, <name> (<model>), was released on
   <date>, <n> months before this run. The reference is 8 months." The
   name and the model id are each cut at 80 characters. `value` is the
   whole months, `reference` is 8, `reference_source` is
   `OLD_MODEL_MONTHS_SOURCE`. `scope` is `harness`.

5. `src/kiln_harness_tuner/smells/detectors/__init__.py`: add
   `old_model.DETECTOR` to `DETECTORS`.

6. `agent_files/skills/kiln-harness-tuner-knowledge/references/smells/old-model.md`
   (new), from the smell template, with the front matter of architecture
   7.4. What it is; how to recognise it (the detection; `list_models` with
   `sort: newest` and the baseline's family or price range; `agent_notes`
   in `get_model_details`); how to confirm it (not this smell: a model kept
   on purpose for a fine-tune base, a price, a latency, or one the user
   pinned; `--models keep`; a loose release date); what it blocks
   (nothing); remedies: model swap inside a class, faster provider for the
   same model, cheaper or faster. No measured number. Then
   `python -m kiln_harness_tuner.knowledge build` to update the generated
   blocks.

7. `tests/test_knowledge.py`: add `"old-model": ["old_model"]` to the map
   of smells and their detectors (and its comment).

8. `docs/architecture.md`: the detector row in 18.4, the text that the
   detectors read the catalogue snapshot, and in 17.1 that the orchestrator
   loads the snapshot for the old-model detector.

9. `specs/backlog.md`:
   - "Model and provider information": the text says it is done by the
     project `model-provider-info`.
   - "Smell: an old model": the text says it is done, with the smell
     document and the detector; its source links to the project.
   - "The task shape for loop agents": `list_available_models` goes;
     `get_model_details` now gives the capability flags, and the gap that
     stays is `turn_mode`.

## Tests

In `tests/test_smells.py`:

- `test_old_model_fires_after_eight_months`: released 2025-01-10, run on
  2025-09-11: one detection with value 8, reference 8, the source, the
  name, the id and the date in the fact.
- `test_old_model_is_silent_at_exactly_eight_months`: released 2025-01-10,
  run on 2025-09-10: nothing.
- `test_old_model_is_silent_without_a_date`: no facts, no release date, or
  no run date: nothing.
- `test_old_model_clamps_the_day_at_month_end`: released 31 May, run on
  1 Feb: fires with 8 months; `add_months` clamps 30 June + 8 to 28 Feb,
  and to 29 Feb in a leap year; released 30 June, run on 28 Feb is silent.
- `test_old_model_cuts_a_long_name`: a 200-character name and id give a
  fact that fits the 300-character limit.
- `test_collect_reads_the_release_date_from_the_catalogue`: `run_detectors`
  with a catalogue that dates the baseline's model and a run start 9 months
  later gives the `old_model` detection with the catalogue's friendly name;
  without a catalogue, or with a model the catalogue lacks, nothing; a bad
  date in the catalogue gives nothing.
- The existing tests that count the detectors and the thresholds, and the
  test that every detection fact has no judgement word, cover the new
  detector.
