---
status: complete
---

# Phase 3: Levers and techniques, part 1

## Overview

This phase writes the rest of the levers and the techniques of the groups
"Instructions" and "Knowledge" of the V1 coverage document (sections 1 and
3). Phase 1 wrote the levers `prompt` and `skill` and the technique
`state-the-output-contract`. So this phase adds four levers (`tool_config`,
`new_tool`, `params`, `model`) and thirteen techniques. With
`state-the-output-contract`, the two groups then hold their fourteen
techniques.

The lever ids `tool_config` and `new_tool` contain `_`, and the `id` rule
refuses `_` today. Architecture decision 2.5 fixes a lever's id as the short
name that the planner writes in the `technique` field. So the rule changes:
a lever id can join its words with `-` or `_`. The ids of the other types do
not change.

The approved priors of V1 coverage section 8 that belong to these
techniques are lines 3, 4 and 5. Each is added only after its source is
opened and its numbers are checked. No other prior is written. The priors
name their sources by title, with no URL.

No prompt template changes. No loop agent is sent to the knowledge skill
yet (phase 5).

## Steps

1. `src/kiln_harness_tuner/knowledge/schema.py`: add
   `LEVER_ID_PATTERN = re.compile(r"^[a-z0-9]+([-_][a-z0-9]+)*$")` beside
   `ID_PATTERN`, with a comment that gives the reason (architecture 2.5).
2. `src/kiln_harness_tuner/knowledge/check.py`: `_id` uses
   `LEVER_ID_PATTERN` for a document of the kind `levers` and `ID_PATTERN`
   for the others. The message of a lever says "joined by '-' or '_'".
3. `components/knowledge_documents.md` section 2 and
   `.agents/skills/knowledge-maintenance/SKILL.md` section 4.3: the id rule
   says that a lever id can also use `_`.
4. Four lever documents in `references/levers/`, each with `What it is`,
   `How the tuner changes it`, `Limits`, `See also`, and no "when to use"
   text:
   - `tool_config.md`: the tools that a run config attaches in
     `tools_config.tools`, attached or dropped on a new run config. The tool
     id forms. A new description is `new_tool`, a skill is `skill`. Limits:
     no new MCP server or RAG config; a dropped tool that the text still
     names. Operations `get_run_config`, `list_available_tools`,
     `get_tool_definition`, `list_code_tools`, `create_run_config`.
   - `new_tool.md`: code tools: a new tool, a clone with a new description,
     a wrapper of any tool. The rules of the object (immutable functional
     fields, a fresh function name, additive only, the allowlist rule, a
     dry run with reads only). Operations `list_code_tools`,
     `get_code_tool`, `get_tool_definition`, `trust_code_tools`,
     `test_code_tool`, `create_code_tool`, `create_run_config`,
     `archive_code_tool`. See also: the code-tool authoring skill and its
     wrapper templates.
   - `params.md`: `thinking_level`, `temperature`, `top_p` on a clone of
     the parent. A params arm and a screening round. The levels come from
     `list_available_models`. Limits: a provider can accept a setting and
     not apply it (the `lever not applied` verdict); the output mode is not
     part of this lever in V1. Operations `get_run_config`,
     `list_available_models`, `create_run_config`.
   - `model.md`: `model_name` and `model_provider_name`, under the run's
     model policy. Existing fine-tunes as candidates. Limits: a swap only
     when the guidance allows it or the objective is speed or cost; no new
     fine-tune. Operations `get_run_config`, `list_available_models`,
     `create_run_config`.
   - A lever holds no "when or why" text. Two lines move out of the levers,
     for phase 4 to use: "A setting is not a fix for a prompt, a tool or a
     skill. It changes how the same model reads the same harness." (for
     `thinking-level-ladder`), and "Every change that the harness has
     gained was measured with the old model. A swap changes what each of
     those changes does." (for `model-swap-inside-a-class`).
5. Thirteen technique documents in `references/techniques/`, each with the
   nine required sections, `levers` as V1 coverage section 3 gives, and the
   cost of that table:
   - Instructions: `add-the-missing-requirement`,
     `answer-only-from-the-source`, `decline-rules-paragraph`,
     `policy-as-step-by-step-procedures`, `structure-a-long-prompt`,
     `one-general-rule-per-edit`, `long-prompt-into-a-skill`,
     `skill-into-the-prompt`, `path-index-in-the-skill-description`,
     `rewrite-an-inherited-skill`, `prune-one-group-at-a-time`.
   - Knowledge: `attach-the-knowledge-source`, `document-into-a-skill`.
   - The content follows the V1 coverage notes: `long-prompt-into-a-skill`
     and `skill-into-the-prompt` name each other in `Alternatives`, with the
     size of the model, how often a run needs the content and the prompt
     cache; rule 3 of the lever ladder goes in "Check that it took effect"
     of `long-prompt-into-a-skill`; each "Do not" of the ladder rows that
     these techniques come from goes in their "When not to use".
   - Priors: line 3 on `prune-one-group-at-a-time`, line 4 on
     `policy-as-step-by-step-procedures`, line 5 on
     `attach-the-knowledge-source`. All other techniques: `None yet.`
   - No number outside a prior, and no "own experiment" prior.
6. `state-the-output-contract.md`: `Alternatives` links
   `add-the-missing-requirement`, for the case "the content is wrong, not
   the form", which its "When not to use" already names.
7. Run `uv run python -m kiln_harness_tuner.knowledge build` to rewrite the
   generated blocks of `builder.md`, `report.md` and `technique-index.md`.

## Tests

- `tests/test_knowledge_checks.py`:
  - `test_a_lever_id_with_an_underscore_is_accepted`: a lever
    `levers/new_tool.md`, named by the technique, gives no problem.
  - `test_an_underscore_in_a_lever_id_must_join_two_words_is_reported`: a
    lever `levers/_tool.md` gives the rule `id`.
  - `test_an_underscore_outside_a_lever_id_is_reported`: a technique
    `techniques/t_one.md` gives the rule `id`.
- `tests/test_knowledge.py`:
  - `test_v1_levers_and_instruction_and_knowledge_techniques_are_present`:
    the six levers and the fourteen techniques of the two groups exist, with
    the `levers` of V1 coverage section 3.
  - The existing `test_the_shipped_knowledge_passes_every_check` and
    `test_the_generated_blocks_are_current` cover the new documents.
