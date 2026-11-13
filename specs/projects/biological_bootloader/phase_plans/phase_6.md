---
status: complete
---

# Phase 6: Smells and the code detectors

## Overview

This phase writes the other twelve smell documents of V1 coverage section 2.
Phase 1 wrote `tool-search`. With it, the knowledge system holds all
thirteen V1 smells. Three of them have a code detector.

Then the `smells` package: the detection model, the three V1 detectors, the
named thresholds, and `run_detectors`. The detections of the present
baseline reach the planner in `BatchPlanInput.detections`, and the run
report gets a code-computed smells appendix with them. The knowledge check
gets the real detector registry in place of the empty one of phase 1.

The baseline reviewer, `ensure_baseline_reviewed`, the smell issues in
memory and the `--no-baseline-review` flag are phase 7. So this phase has
a smaller call site: `smells.run.ensure_detections`, called at the top of
each wave, runs the detectors once for each baseline. Phase 7 puts it
inside `ensure_baseline_reviewed`.

Content rules for this phase, from the owner's standing rules:

- A smell is an issue in the design of the agent, true whether an eval
  passes or fails. A reason why an eval item failed is not a smell.
- No text uses an outcome of the tuner's own runs as a reason or a basis.
  The evidence column of the removed lever ladder is not used. Each reason
  is from first principles.
- V1 coverage gives no prior to a smell, and a smell has no `Priors`
  section. No number appears in a smell document.
- Everything is guidance: each remedy states its condition.
- Ids of documents are links or plain text, never in backticks.

## Steps

1. **Smell documents** in
   `agent_files/skills/kiln-harness-tuner-knowledge/references/smells/`, from
   the template, each under 4,000 characters, with the five required
   sections:
   - code: `very-long-prompt` (detector `long_prompt`),
     `very-long-tool-description` (`long_tool_description`),
     `very-long-skill-description` (`long_skill_description`);
   - agent: `one-file-skill-with-many-topics`,
     `skill-too-small-to-earn-its-place`, `faulty-inherited-skill`,
     `tool-results-flood-the-context`, `fixed-call-chain`,
     `general-query-tool-without-a-guide`, `too-many-narrow-tools`,
     `document-behind-a-lookup-tool`, `tools-on-a-live-server`.
   Ladder rows (V1 coverage section 6): row 6 is
   `tool-results-flood-the-context`, its remedies in the row's order (the
   wrapper first, a loop of calls next), its "Do not" (prompt alone) in
   "What it blocks" or the remedy conditions. Row 8's smell is
   `one-file-skill-with-many-topics`. Row 9 is
   `document-behind-a-lookup-tool` (move into a skill first; a keyword
   contents file in front of the tool next; no prompt rules on which page).
   Row 10 is `faulty-inherited-skill` (rewrite as a new version; then
   progressive disclosure; no prompt rules around a skill that can change).
   Row 11's smell is `too-many-narrow-tools`.
2. **Links to the new smells.** `fit-the-tool-layer-to-the-agent` links each
   smell of the tools that it names in words today.
   `lost-in-long-instructions` links `one-file-skill-with-many-topics` and
   `very-long-prompt`; `answers-not-grounded` links
   `document-behind-a-lookup-tool`; `wrong-tool-chosen` links
   `too-many-narrow-tools`; `change-the-surface-not-the-words` links
   `faulty-inherited-skill` and `tool-results-flood-the-context`;
   `levers/prompt.md` links `very-long-prompt`. Then
   `uv run python -m kiln_harness_tuner.knowledge build`.
3. **`schemas.py`**: `Detection` as in section 2 of the component design
   (`fact` at most 300 characters). `BatchPlanInput.detections:
   list[Detection] = []`, with a comment that they describe the present
   baseline. `RunStatus.detections_by_rc: dict[str, list[Detection]] = {}`
   (rc id to the detections of that baseline, in the order of review), so
   that the report can give each baseline its own rows.
4. **Package `src/kiln_harness_tuner/smells/`**:
   - `model.py`: `DetectorInputs` (rc_id, properties, texts: `RcText`,
     tool_definitions, trace_stats) and `Detector` (id, smell, run,
     `reads_traces: bool = False`), both frozen dataclasses.
   - `thresholds.py`: `LONG_PROMPT_CHARS = 200_000`,
     `LONG_TOOL_DESCRIPTION_CHARS = 4_096`,
     `LONG_SKILL_DESCRIPTION_CHARS = 1_024`, each with a source comment and a
     `<NAME>_SOURCE` string that a detection carries as
     `reference_source`.
   - `detectors/long_prompt.py`, `long_tool_description.py`,
     `long_skill_description.py`: one pure function and one `DETECTOR`
     each. A fact names the thing, its length and the reference, with no
     word of judgement.
   - `detectors/__init__.py`: `DETECTORS` and `detector_smells()`.
   - `run.py`:

     ```
     async def run_detectors(ctx, rc_id, *, detectors=DETECTORS) -> list[Detection]
     async def ensure_detections(ctx, baseline: BaselineRef) -> None
     def skill_description(skill_md: str) -> str | None
     ```

     `run_detectors` follows section 3.5: read the properties
     (`probes.rc_props`), `rc_texts`, the definition of each attached tool
     (`GET …/tools/{id}/definition`; a tool whose definition cannot be read
     is left out and named in the event), and the stored train trace
     stats of the baseline only when a registered detector reads traces.
     Each detector runs inside `try/except`; a detection of an unknown
     smell is dropped; a knowledge tree that cannot be loaded keeps every
     detection, with the event `knowledge_unreadable`. Events:
     `smell_detector_failed`, `smell_unknown`, `smell_detection_skipped`,
     `smell_detections rc= count= detections= unread_tools=`.
     `ensure_detections` returns at once when `ctx.detected_rc_id` is the
     baseline; else it sets it and replaces `ctx.detections`.
   - `__init__.py`: exports `run_detectors`, `ensure_detections`,
     `DETECTORS`, `detector_smells`.
5. **`orchestrator.py`**: `Ctx.detections: list[Detection]` and
   `Ctx.detected_rc_id: str | None = None`. `_run_loop` calls
   `ensure_detections(ctx, baseline)` after the wave reads the baseline,
   before `_dispatch_plan`. `_dispatch_plan` passes
   `detections=list(ctx.detections)`.
6. **`status.py`**: `_on_smell_detections(rc, detections)` validates each
   detection and sets `detections_by_rc[rc]`.
7. **`report.py`**: `render_smells_appendix(status)`, heading
   `## Smells (code-computed)`. For each baseline with detections: its run
   config and a table (smell, fact, reference, source). Empty string when
   there is no detection. `_write` appends it beside the other appendices,
   so the agent's report and the fallback report both have it.
8. **`knowledge/__main__.py`**: `code_side()` returns
   `smells.detector_smells()` (a lazy import, so `knowledge` does not
   import `smells` at module load).
9. **Agent files.** The planner's skill
   (`kiln-harness-tuner-batch-selection/SKILL.md`) gets a short section
   "Smells": the detections are facts about the present baseline, each with
   a smell id; open the smell document from the planner entry file and read
   its remedies; a fix for a smell is a fix arm with `needs_eval: false` and
   the `eval_id` of an active eval the change serves, on the issue that the
   change serves; a smell is evidence, an experiment decides. The report
   writer's template says that code appends the smells appendix.
10. **Fakes.** `tests/fake_kiln.py` serves
    `GET …/tools/{id}/definition` from `tool_definitions` (404 when absent).

## Tests

`tests/test_smells.py`:

- `test_long_prompt_is_silent_at_the_reference`
- `test_long_prompt_reports_above_the_reference_with_value_and_source`
- `test_long_tool_description_is_silent_at_the_reference`
- `test_long_tool_description_reports_above_the_reference_with_value_and_source`
- `test_long_skill_description_is_silent_at_the_reference`
- `test_long_skill_description_reports_above_the_reference_with_value_and_source`
- `test_a_detector_is_silent_when_the_text_cannot_be_read`
- `test_skill_description_reads_the_front_matter` (and none on bad YAML)
- `test_a_detection_fact_has_no_judgement_word`
- `test_a_detector_that_raises_is_skipped_and_the_others_run`
- `test_a_detection_of_an_unknown_smell_is_dropped`
- `test_every_registered_detector_has_a_unique_id`
- `test_every_threshold_has_a_source_comment`
- `test_run_detectors_reads_the_baseline_and_emits_the_detections` (fake
  Kiln: a long prompt and a long tool description; one unreadable tool)
- `test_run_detectors_skips_when_the_run_config_cannot_be_read`
- `test_trace_stats_are_read_only_for_a_detector_that_reads_traces`
- `test_ensure_detections_runs_once_per_baseline`
- `test_the_planner_input_carries_the_detections` (the loop, with the fake
  dispatcher)
- `test_the_tracker_keeps_the_detections_of_each_baseline`
- `test_the_report_has_the_smells_appendix` and
  `test_the_smells_appendix_is_left_out_without_a_detection`

`tests/test_knowledge.py`:

- `test_the_v1_smell_is_present_with_its_detection` (parametrized over the
  thirteen V1 smells, with `detected_by` from V1 coverage section 2)
- `test_the_check_uses_the_real_detector_registry` (`code_side()` equals
  `detector_smells()`, and it is not empty)
