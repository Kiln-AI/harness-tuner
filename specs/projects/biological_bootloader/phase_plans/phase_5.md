---
status: complete
---

# Phase 5: Playbooks, and the move of the lever ladder

## Overview

This phase writes the other twenty playbooks of the V1 coverage document
(section 4). Phase 1 wrote `output-contract-not-met`. With it, the knowledge
system holds all twenty-one V1 playbooks.

Then the lever ladder moves. The file
`kiln-harness-tuner-batch-selection/references/lever-ladder.md` is removed.
Each reference to it, and each mention of "the lever ladder", is changed.
The prompt templates of the planner, the builder, the analyzer and the report
writer name the knowledge skill and their entry files. The planner's skill
points to the knowledge system in place of the ladder. The baseline
reviewer's template comes in phase 7.

The approved priors of V1 coverage section 8 that belong to these playbooks
are lines 6 and 15 (`ordering-work`), 7 and 8
(`dealing-with-a-saturated-eval`), 12 and 13 (`skill-is-not-read`), 14
(`change-the-surface-not-the-words`), 18
(`dealing-with-train-wins-rejected-at-val`) and 20
(`dealing-with-a-noisy-eval`). Line 9 is held: it has no V1 document. Each
prior is added only after its source is opened and its numbers are checked.
The priors name their sources by title, with no URL.

The schema has no `Priors` section for a playbook, and allows other H2
sections after the required ones. So a playbook with priors gets an
optional `## Priors` section after `## Budget`, in the form of section 3.7
of the component design. A new rule, `playbook-priors`, checks it as
`technique-priors` checks a technique.

Content rules for this phase, from the owner's standing rules:

- No number appears outside a prior, apart from the owner's own guidance in
  the project overview (a reasonable start of about 20%).
- No text uses an outcome of the tuner's own runs as a reason, a basis or an
  example. The evidence column of the ladder, and the evidence in "the rule
  above the table", are not used. Each reason is given from first
  principles.
- Everything is guidance. Each step states its condition. No playbook gives
  a default order of levers: `ordering-work` says that the priority comes
  from the smells and the failures.
- Ids of documents are written as links or as plain text, never in
  backticks: a test reads a backticked kebab-case word as a skill name.

## Steps

1. `src/kiln_harness_tuner/knowledge/check.py`: the helper of
   `_technique_priors` takes the kind and the rule id. `_playbook_priors`
   uses it for `playbooks` with the rule id `playbook-priors`. A playbook
   with no `Priors` section has no problem. Add it to `RULES`.
2. `components/knowledge_documents.md`: section 3.5 says that a playbook can
   have an optional `## Priors` section after `## Budget`; section 3.7 says
   that a prior is an item of the `Priors` section of a technique or of a
   playbook; section 6.3 gets the rule `playbook-priors`.
   `.agents/skills/knowledge-maintenance/SKILL.md` section 8 gets the same
   sentence.
3. Twenty playbook documents in `references/playbooks/`, each with the four
   required sections, the `kind` of V1 coverage section 4, inside 8,000
   characters:
   - Kind `order`:
     - `know-the-agent-before-you-plan` (all): classify the agent (turns,
       output, classifier shape, tools, skills, model). A multi-turn eval is
       driven by a synthetic user. No evals: the tuner does not write them
       unless eval building is on. Steps link `ordering-work`,
       `decision-model-for-a-classifier`, `structure-a-long-prompt`,
       `guide-skill-for-a-tool-set` with their conditions.
     - `ordering-work` (all): the priority comes from the smells and the
       failures, not from a fixed order of levers. A model with a reasonable
       start, when the model is a lever. Fix first what a smell says blocks
       other work. Each failure to its playbook. One technique in one
       experiment. The settings and the model scan are late and depend on
       the budget. Priors 6 and 15.
     - `spending-the-budget` (all): strategy with no numbers. The planner's
       input fields that carry the budget, the prices, what each eval can
       show and the noise. Fewer experiments, each large enough to pass its
       own test. What a small budget leaves out and what a large budget
       opens. The cost class of each technique, with the ladder's cost order
       given as reasons, not as a list.
   - Kind `goal`:
     - `cheaper-or-faster` (all): each step marked cheaper, faster or both.
       Another provider of the same model, less thinking, a shorter prompt,
       fewer output tokens, fewer calls, then a smaller model or a decision
       model. The cost goal after quality is stable.
     - `working-inside-restrictions` (all): a stated restriction is a hard
       limit; the model policy; what to do when the best lever is not
       allowed.
   - Kind `design`:
     - `fit-the-tool-layer-to-the-agent` (tools): wrap, do not rewrite;
       which wrapper for which problem; the prompt and the guide skill change
       with the tools. It names the smells of phase 6 in words, not as
       links, because those files do not exist yet.
   - Kind `failure`, one for each ladder row of V1 coverage section 6:
     - `requirement-missing-from-the-prompt` (all), overview 6.1.
     - `answers-not-grounded` (all), ladder row 2. Also links
       `document-into-a-skill` for knowledge behind a lookup tool (row 9),
       so that the row has a path from a playbook before its smell exists.
     - `wrong-scope-or-refusal` (all), ladder row 3.
     - `missing-context-the-agent-could-be-given` (tools), overview 7.5.
     - `failure-at-a-tool-call` (tools), ladder row 4. It names rule 1 (name
       the decision point) as process of the planner's and the analyzer's
       skills, by skill name.
     - `malformed-tool-calls` (tools), ladder row 7.
     - `wrong-tool-chosen` (tools), ladder row 11.
     - `skill-is-not-read` (all), ladder row 5. Priors 12 and 13.
     - `lost-in-long-instructions` (all), ladder rows 8 and 14. Also links
       `rewrite-an-inherited-skill` (row 10).
     - `change-the-surface-not-the-words` (all), the rule above the table
       and rule 2. Prior 14.
   - Kind `eval`:
     - `dealing-with-a-saturated-eval` (all), ladder row 12. Priors 7, 8.
     - `dealing-with-an-eval-near-zero` (all).
     - `dealing-with-a-noisy-eval` (all). Prior 20.
     - `dealing-with-train-wins-rejected-at-val` (all). Prior 18.
4. Remove `kiln-harness-tuner-batch-selection/references/lever-ladder.md`.
5. `kiln-harness-tuner-batch-selection/SKILL.md`:
   - Step 1: name the failure class, then take it to its playbook through
     `references/planner.md` of `kiln-harness-tuner-knowledge`, and take the
     first step whose condition holds. It keeps the decision-point mapping
     (rule 1) and the count of `lever_attempts` (rule 2): what "text"
     means, "any outcome", and that the count does not say which arms
     failed.
   - The `technique` field is the id of a lever document where one exists,
     and the key of `lever_attempts`.
   - Each other mention of the ladder (inherited skills, the reuse of an
     rc, the cut name) names `lever_attempts` or the knowledge skill.
   - Related: `kiln-harness-tuner-knowledge` in place of the ladder.
   - The description names the knowledge skill.
6. The other references:
   - `agents/batch_plan.md`: "Read `references/planner.md` of the
     `kiln-harness-tuner-knowledge` skill before Step 1." The ladder line
     becomes the count rule of Step 1.
   - `agents/create_experiment_rc.md`: the builder reads
     `references/builder.md` of the knowledge skill, then the technique of
     its item.
   - `agents/analyze_experiment_results.md`: the analyzer reads
     `references/analyzer.md` to name the failure pattern.
   - `agents/report_writer.md`: the report writer reads `references/report.md`
     when the knowledge helps it explain the run.
   - `kiln-harness-tuner-experiment-build/SKILL.md` Related, and
     `references/skill-authoring.md`: the knowledge skill in place of the
     ladder.
   - `kiln-harness-tuner-code-tool-authoring/SKILL.md` and
     `references/wrappers/README.md`: "one per mechanical failure", with the
     knowledge skill's builder entry file in place of the ladder.
   - `kiln-harness-tuner-experiment-analysis/SKILL.md`: the decision point
     and the `technique:` line name `lever_attempts`, not the ladder.
   - `docs/agents.md`: the planner's skill line names the knowledge skill.
   - Comments in `schemas.py`, `memory.py`, `memory_schema.py` and
     `tests/test_memory.py`: "the lever ladder" becomes the count
     `lever_attempts`.
7. Run `uv run python -m kiln_harness_tuner.knowledge build`.
8. The line-by-line check of V1 coverage section 6 against the removed
   file: each row and rule of the ladder is in the document that the table
   names, or in a technique that phase 3 or 4 wrote, or waits for a smell of
   phase 6. The result goes in the return summary.

## Tests

- `tests/test_knowledge_checks.py`:
  - `test_playbook_priors_is_reported`: a playbook prior with a bad
    `Source:` reports `playbook-priors` only.
  - `test_a_playbook_with_no_priors_section_is_accepted`: the valid tree has
    no `Priors` in its playbook and no problem (the existing valid-tree test
    covers it; this one adds a section with `None yet.` and a valid prior).
- `tests/test_knowledge.py`:
  - `V1_PLAYBOOK_KINDS`: the twenty-one V1 playbooks with their kinds.
    `test_the_v1_playbook_is_present_with_its_kind` (parametrized).
  - `test_each_role_template_names_its_entry_file`: `batch_plan.md`,
    `create_experiment_rc.md`, `analyze_experiment_results.md` and
    `report_writer.md` each name `kiln-harness-tuner-knowledge` and
    `references/<entry>.md`, and the entry file exists.
  - `test_the_lever_ladder_file_is_gone_and_nothing_names_it`: the file is
    gone, and no file under `src/` or `docs/` names `lever-ladder.md` or
    "lever ladder".
  - `test_the_planner_skill_points_to_the_knowledge_system`: the planner's
    skill names `kiln-harness-tuner-knowledge` and `references/planner.md`.
  - `test_spending_the_budget_names_real_planner_input_fields`: each field
    of the planner's input that the playbook names in backticks is a field
    of `BatchPlanInput`.
- `tests/test_agent_templates.py`:
  `test_planner_skill_names_the_lever_attempts_input_and_the_ladder`
  becomes `..._and_the_knowledge`: the planner's skill names
  `lever_attempts`, "ANY outcome", "which arms failed" and the knowledge
  skill. The lever `new_tool` names `wrappers/README.md` (the ladder's
  "bake it into code" section).
