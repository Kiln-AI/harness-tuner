---
status: complete
---

# Phase 1: The knowledge package and the skill skeleton

## Overview

This phase builds the structure of the knowledge system and proves it on
content. It adds the Python package `kiln_harness_tuner.knowledge` (schema,
load, check, generated blocks, command line), the skill
`kiln-harness-tuner-knowledge` with its `SKILL.md`, the five role entry
files and `technique-index.md`, and one real document of each component
type. It adds the two test files. It does not change a prompt template, so
no loop agent is sent to the new skill yet.

The plan names the smell `tool-search`. Its remedy is the technique
`guide-skill-for-a-tool-set`, and that technique uses the lever `skill`.
A smell must have a remedy that resolves (rule `smell-remedies`), and a
technique must name lever documents that exist (rule
`technique-lever-ids`). So this phase also writes a first version of the
technique `guide-skill-for-a-tool-set` (no sub-files; phase 4 adds them) and
the lever `skill` (phase 3 then writes four other levers, not five).

The approved public priors of `state-the-output-contract` (V1 coverage,
section 8, lines 16 and 17) are added only when their sources are opened
and checked. This phase adds the prior from the lever ladder's evidence
column (source "own experiment"), which the V1 coverage document assigns to
this technique.

## Steps

1. `src/kiln_harness_tuner/knowledge/schema.py`:
   - `TASK_SHAPES`, `COMPONENT_DIRS`, `MAX_CHARS`, `MAX_DEPTH = 5`,
     `REQUIRED_SECTIONS`, `EVAL_STANCES`, `PRIOR_SOURCES`, `ID_PATTERN`,
     `ENTRY_FILES` (the five role files), `INDEX_FILE`, `ROOTS`,
     `ENTRY_BLOCKS` (entry file name to its block names, per section 4 of
     the component design).
   - `Problem(rule, path, message)`, a frozen dataclass.
   - `CommonMeta`, `LeverMeta`, `TechniqueMeta`, `PlaybookMeta`,
     `SmellMeta` (Pydantic, `extra="forbid"`), with validators: title 3 to
     60 characters, one line; description 40 to 200 characters, one line,
     no `|`; `reviewed` not in the future; non-empty `levers`,
     `detected_by` and `applies_to` lists.
   - `META_MODELS: dict[str, type[CommonMeta]]` by directory.
2. `src/kiln_harness_tuner/knowledge/load.py`:
   - `resolve_link(base: Path, target: str, inside: Path) -> Path | None`:
     the case-exact walk of `tests/test_docs.py`, moved here and shared.
   - `Link(target, resolved, external)`, `Section(heading, text, links)`,
     `Document(path, kind, id, meta, sections, links, chars, text,
     problems)`, `KnowledgeBase(root, documents, other_files)` with
     `by_kind`, `get`, `references`.
   - `KnowledgeError`, `default_root()`, `load(root=None)`.
   - Front matter between the first two `---` lines, `yaml.safe_load`, then
     the model of the directory. A failure is a `front-matter` problem on
     the document, never a raise.
   - Sections split on `## ` lines and links read from `](target)`, both
     outside fenced blocks.
   - `other_files`: every file under `references/` that is not loaded as a
     document (not Markdown, or outside the layout).
3. `src/kiln_harness_tuner/knowledge/check.py`:
   `check(kb, *, gateway_ops, detectors) -> list[Problem]`, one function per
   rule id of section 6.3 of the component design, sorted output.
4. `src/kiln_harness_tuner/knowledge/index.py`: `render_blocks(kb)` and
   `write_blocks(kb)`, plus `read_block(text, name)` used by the `index`
   check. A table row per document, sorted by id, no padding. An empty
   table is the line `None yet.`
5. `src/kiln_harness_tuner/knowledge/__main__.py` and `__init__.py`:
   `check` and `build`, each with `--root DIR`. The CLI passes
   `gateway.ops.OPS` and an empty detector mapping (the `smells` package
   comes in phase 6).
6. `tests/test_docs.py`: `resolves_inside_the_repository` calls
   `resolve_link`.
7. The skill `src/kiln_harness_tuner/agent_files/skills/kiln-harness-tuner-knowledge/`:
   - `SKILL.md`: what the knowledge system is, the golden rule
     (experiments decide), the role-to-entry-file table, and a `## Related`
     line.
   - `references/planner.md`, `builder.md`, `smells.md`, `analyzer.md`,
     `report.md`, `technique-index.md`: hand-written text and the
     generated blocks of section 4.
   - `references/levers/prompt.md`, `references/levers/skill.md`.
   - `references/techniques/state-the-output-contract.md`,
     `references/techniques/guide-skill-for-a-tool-set.md`.
   - `references/playbooks/output-contract-not-met.md`.
   - `references/smells/tool-search.md`.
8. Run `uv run python -m kiln_harness_tuner.knowledge build`, then the
   repository checks.

## Tests

`tests/test_knowledge.py` (the real tree):

- `test_the_shipped_knowledge_passes_every_check`
- `test_the_generated_blocks_are_current`
- `test_each_entry_file_exists_and_has_its_blocks`
- `test_skill_md_links_each_entry_file`
- `test_the_knowledge_skill_installs_for_each_agent_cli`
- `test_each_role_template_names_its_entry_file` and
  `test_the_lever_ladder_file_is_gone_and_nothing_names_it` are phase 5
  work. They are not written in this phase.

`tests/test_knowledge_checks.py` (a `make_tree` fixture writes a valid
minimal tree, and each test applies one change):

- `test_a_valid_minimal_tree_has_no_problem`
- `test_<rule>_is_reported` for each rule id: `front-matter`, `id`,
  `sections`, `size`, `shape`, `link`, `reachable`, `depth`,
  `technique-lever-ids`, `lever-ops`, `technique-lever-links`,
  `technique-stance`, `technique-priors`, `alternatives`,
  `playbook-steps`, `smell-remedies`, `smell-detectors`, `subfile`,
  `index`, `stray`. Each asserts that exactly that rule is reported.
- Extra cases where a rule has more than one way to fail (for example a
  detector that the registry does not know, and a registry entry that no
  smell lists).
- `test_a_heading_inside_a_fence_is_not_a_section`
- `test_a_link_inside_a_fence_is_not_a_link`
- `test_a_dormant_technique_is_left_out_of_the_index`
- `test_render_blocks_is_stable`
- `test_build_rewrites_only_between_the_markers`
- `test_load_records_a_problem_and_does_not_raise`
- `test_load_raises_when_the_root_is_missing`
- `test_the_command_line_exits_1_on_a_problem`
- `test_the_command_line_build_writes_the_blocks_and_exits_0`
