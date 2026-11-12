---
status: complete
---

# Implementation Plan: Biological Bootloader

The specs give the detail. This file gives the order.

- [functional_spec.md](./functional_spec.md)
- [architecture.md](./architecture.md), and its component designs:
  [knowledge documents](./components/knowledge_documents.md),
  [maintenance skill](./components/maintenance_skill.md),
  [smell detection](./components/smell_detection.md)
- [v1_coverage.md](./v1_coverage.md): which documents V1 holds

Every phase ends with the repository checks: `uv run pytest`,
`uv run ruff check`, `uv run ruff format --check`. A phase that adds or
changes knowledge documents also ends with
`uv run python -m kiln_harness_tuner.knowledge build`.

## Phases

- [x] Phase 1: The knowledge package and the skill skeleton. The
  `knowledge` package (schema, load, check, index, command line). The skill
  `kiln-harness-tuner-knowledge` with `SKILL.md`, the five entry files and
  `technique-index.md`. One real document of each type, so that every check
  runs on content: lever `prompt`, technique `state-the-output-contract`,
  playbook `output-contract-not-met`, smell `tool-search` (a smell with no
  code detector). `tests/test_knowledge.py` and
  `tests/test_knowledge_checks.py`. No prompt template changes yet.
- [x] Phase 2: The maintenance skill, the backlog and the repository rules.
  `.agents/skills/knowledge-maintenance/` with its templates.
  `specs/backlog.md`, filled from section 7 of the V1 coverage document.
  The `.gitignore`, `.github/CONTRIBUTING.md` and root `AGENTS.md` changes.
  `tests/test_backlog.py` and the additions to the knowledge and link
  tests.
- [x] Phase 3: Levers and techniques, part 1. The other five levers. The
  fourteen techniques of the groups "Instructions" and "Knowledge". Each
  approved prior is added only after its source is opened and its numbers
  are checked.
- [ ] Phase 4: Techniques, part 2. The fourteen techniques of the groups
  "Tools" and "Model and settings", with their priors, and the sub-files of
  `guide-skill-for-a-tool-set`.
- [ ] Phase 5: Playbooks, and the move of the lever ladder. The other
  twenty playbooks. The lever ladder file is removed, and each reference to
  it is changed. The prompt templates of the planner, the builder, the
  analyzer and the report writer name their entry files. The planner's
  skill points to the knowledge system. The phase ends with the line-by-line
  check of section 6 of the V1 coverage document against the removed file.
- [ ] Phase 6: Smells and the code detectors. The other twelve smell
  documents. The `smells` package: the detection model, the three
  detectors, the thresholds, `run_detectors`. The detections in the
  planner's input and in the report. `tests/test_smells.py`.
- [ ] Phase 7: The baseline reviewer. The role end to end: the prompt
  template, its skill, the dispatcher method, the gateway role,
  `ensure_baseline_reviewed`, the smell issues in memory, the
  `--no-baseline-review` flag, the report. `tests/test_baseline_review.py`
  and the extended role tests.
- [ ] Phase 8: Documents and acceptance. `docs/agents.md`,
  `docs/architecture.md`, `docs/how-it-works.md` and `docs/cli.md`. The two
  acceptance checks of the maintenance skill that need a person (an agent
  adds a technique as one file; an agent stops on a conflict), with a short
  written record. A run on the demo project that shows a detection and a
  review in the report. The wheel smoke test.

## Notes on the order

- Techniques come before playbooks and smells, because a playbook and a
  smell link to techniques, and a check fails on a link that does not
  resolve.
- The prompt templates change in phase 5, not earlier, so that no loop
  agent is sent to an entry file that has little behind it.
- Phases 6 and 7 are the only ones that change how a run behaves. Phases 1
  to 5 add files that a run installs, and change what the planner reads.
- Phases 3 and 4 are content. They can be done in parallel with each other
  once phase 2 is in.
