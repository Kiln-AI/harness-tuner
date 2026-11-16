---
status: complete
---

# Phase 4: Techniques, part 2

## Overview

This phase writes the techniques of the groups "Tools" and "Model and
settings" of the V1 coverage document (section 3), and the sub-files of
`guide-skill-for-a-tool-set`. Phase 1 wrote a first version of
`guide-skill-for-a-tool-set` with no sub-files. So this phase adds thirteen
technique documents, three sub-files, and changes one technique.

The approved priors of V1 coverage section 8 that belong to these
techniques are lines 1, 2 (`remove-or-scope-tools`), 10
(`thinking-level-ladder`), 11 and 19 (`model-swap-inside-a-class`). Each is
added only after its source is opened and its numbers are checked. No other
prior is written. The priors name their sources by title, with no URL.

No number appears outside a prior. No text uses an outcome of the tuner's
own runs (the evidence column of the lever ladder included) as a reason or
as a basis of confidence. Each reason is given from first principles.

No prompt template changes. No code changes, apart from the tests.

## Steps

1. Ten technique documents of the group "Tools" in
   `references/techniques/`, each with the nine required sections, the
   `levers` and `cost` of V1 coverage section 3, and `applies_to` that
   includes `tools`:
   - `cap-and-preview-wrapper` (`new_tool`, medium): a result trimmed to the
     fields this agent needs, a preview with a follow-up query, and an error
     when a result would be too large. Ladder row 6: its "Do not" (prompt
     alone) goes in "When not to use". Its "Then" (a prompt line to prefer
     the wrapper) is named as a second change, as in
     `guide-skill-for-a-tool-set`. It names the templates
     `preview_and_cap.py` and `paginate_all.py` and does not copy them.
   - `roll-up-tool` (`new_tool`, `prompt`, medium): the two cases of the V1
     note (a fixed chain folded into one tool; one tool that loads the
     context at the start, with a prompt line to call it first), and a loop
     of one call per item folded into one batch call. Templates
     `composite_resolve_fetch.py` and `batch_with_errors.py`.
   - `guide-skill-for-a-tool-set` (`skill`, medium): already present.
     Changes in step 2.
   - `new-tool-description` (`new_tool`, low): ladder row 4 "Try first", and
     the rename of row 7 "Then". Its "Do not" (prompt, skill body, skill
     description) goes in "When not to use".
   - `directive-in-the-tool-response` (`new_tool`, medium): ladder row 4
     "Then", and the variant that returns the derived facts that the reply
     states. Template `context_on_tool_surface.py`.
   - `normalize-inputs-wrapper` (`new_tool`, medium): ladder row 7. Its
     "Do not" (a prompt list of formatting rules) goes in "When not to
     use". Template `normalize_inputs.py`.
   - `errors-the-agent-can-act-on` (`new_tool`, medium): overview 6.3.
     Template `actionable_errors.py`.
   - `guard-tool-calls-at-the-boundary` (`new_tool`, `prompt`, medium): a
     check in the tool that refuses a call which breaks a rule the product
     enforces, and the prompt text that the guard takes over removed on the
     same arm. Template `readonly_guard.py`.
   - `remove-or-scope-tools` (`tool_config`, low): priors 1 and 2.
   - `consolidate-tools` (`new_tool`, `tool_config`, high): ladder row 11.
     Its "Do not" (a prompt list of which tool to use when) goes in "When
     not to use". It is planned as an arm that makes this change alone.
2. `guide-skill-for-a-tool-set.md`:
   - `Alternatives` links `new-tool-description`, `consolidate-tools` and
     `remove-or-scope-tools`, each with its condition.
   - `How` step 3 links the three sub-files, and says when to read each.
   - `Confidence` states its basis from first principles, with no
     observation as its basis.
   - `reviewed` stays `2026-11-12`.
3. Three sub-files in `references/techniques/guide-skill-for-a-tool-set/`,
   plain Markdown with no front matter, each inside 20,000 characters:
   - `sample-queries.md`: sample queries at each level of the guide, from
     the simple query to the complex one; where each sample goes; how to
     write one from the tools and the traces, not from the train items.
   - `schema-as-leaf-files.md`: the schema in leaf files that the complex
     query needs: one file per area, what each file holds (names, meanings,
     units, codes, sample values, joins), and what stays out of the body.
   - `timestamps.md`: the section on dates and times: the time zone of each
     field, the format, "today" and relative periods, fiscal periods, the
     bounds of a range, and how fresh the data is.
4. Four technique documents of the group "Model and settings":
   - `thinking-level-ladder` (`params`, low): ladder row 13, in the form
     the planner's skill uses today (a screening round, then a params arm).
     It uses the line that phase 3 took out of the lever `params`: "A
     setting is not a fix for a prompt, a tool or a skill. It changes how
     the same model reads the same harness." Prior 10.
   - `model-swap-inside-a-class` (`model`, high): lock a class, tune the
     harness, then scan the class again. It says that every promoted change
     is measured again after a swap, and it has a line on fine-tunes (an
     existing fine-tune is a candidate; a fine-tune is a late step and does
     not carry knowledge). It uses the line that phase 3 took out of the
     lever `model`: "Every change that the harness has gained was measured
     with the old model. A swap changes what each of those changes does."
     Priors 11 and 19.
   - `faster-provider-for-the-same-model` (`model`, low): the same model on
     a faster provider. The general form of the notes' example: a provider
     with no prompt cache can cost more on a long conversation.
   - `decision-model-for-a-classifier` (`model`, medium): applies to the
     classifier shape only (`single_turn`, `structured_output`,
     `classifier`, `no_tools`).
5. Run `uv run python -m kiln_harness_tuner.knowledge build` to rewrite the
   generated blocks of `builder.md`, `report.md` and `technique-index.md`.

## Tests

- `tests/test_knowledge.py`:
  - `V1_TECHNIQUE_LEVERS` gets the fourteen techniques of the two groups,
    so `test_the_v1_technique_is_present_with_its_levers` covers all
    twenty-eight techniques of V1 coverage section 3.
  - `test_the_guide_skill_for_a_tool_set_has_its_sub_files`: the three
    sub-files exist and the primary document links each one.
  - The existing `test_the_shipped_knowledge_passes_every_check` and
    `test_the_generated_blocks_are_current` cover the new documents.
