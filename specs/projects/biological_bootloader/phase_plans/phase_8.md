---
status: complete
---

# Phase 8: Documents and acceptance

## Overview

Phases 1 to 7 built the knowledge system, the maintenance skill, the smell
detectors and the baseline reviewer. The reference documents still describe
the loop as it was before. This phase changes `docs/agents.md`,
`docs/architecture.md` and `docs/how-it-works.md` so that they describe the
code as built (the commit messages of phases 1 to 7 give each deviation from
the spec). `docs/cli.md` already has the `--no-baseline-review` row from
phase 7; this phase checks it and links it to the new architecture section.

It also adds the new packages to the layout blocks, with a test that keeps
each layout block in step with the package directories, and it closes the
acceptance items that need a person or a live run:

- acceptance 1 and 2 of the functional spec (the maintenance skill adds a
  technique as one file; it stops on a conflict), each run with a fresh
  agent session in a scratch clone, with a short written record;
- acceptance 5 (a demo-project run shows a detection in the planner's input
  and in the report), which needs a Kiln studio server and model spend;
- the wheel smoke test.

No code of the loop changes in this phase.

## Steps

1. **`docs/agents.md`.**
   - The role count becomes eleven. The role table gets the row
     `baseline_review` | `kiln-harness-tuner-baseline-review` | 20 min |
     nothing — the run goes on with the code detections.
   - A new section `## baseline_review`, after `generality_review`, in the
     form of the others: Dispatched when, Given (`BaselineReviewInput`),
     Must return (`BaselineReviewResult`), what code does with it (one
     smell issue per valid finding, tags, the cap of 15, the merge, unknown
     ids dropped), the gateway operations (read only, a trace reader, not a
     val reader, no `get_train_expected` and no `get_task_run`), Skill, and
     Blame.
   - The paragraph on the other skills: seven skills, and the knowledge
     skill with its five entry files and which role reads which.
   - The planner, builder, analyzer and report-writer sections name their
     entry files. The planner's input names `detections`.
   - "What every agent is forbidden": the trace readers list and the
     read-only roles list get the baseline reviewer.
2. **`docs/architecture.md`.**
   - Section 3.2: a new step between "Read the baseline" and "Plan": the
     smells step (`smells.ensure_baseline_reviewed`), once for each
     baseline, with its skip rules (off, a wrap-up wave, too little time,
     already reviewed) and the second deadline check after the review.
   - Section 8: the smell issues (`record_smell`, the tags, no eval
     binding, dedup on an open issue of the same smell and run config).
     Section 5: the sync leaves smell issues alone, as it does the
     baseline-audit issues.
   - Section 10: a row for the baseline reviewer failing, and one for a
     smell detector raising.
   - Section 11: the `## Smells` appendix of the report.
   - Section 12: the read-only roles and the trace readers get the
     baseline reviewer.
   - Section 13: `knowledge/` and `smells/` (and `onboarding/`, which the
     block also lacks), the new skills, `.agents/skills/` and
     `specs/backlog.md`.
   - Section 15: the knowledge checks and the layout guard.
   - A new section 18, "The knowledge system and smell detection": the
     skill and its entry files, the four component types, the generated
     blocks, the package and its two commands (`check`, `build`), the
     structural checks, the maintenance skill and the backlog, the
     detectors and their thresholds, and the baseline reviewer.
3. **`docs/how-it-works.md`.** The flowchart and "A wave" get the smells
   step. "Plan" says the planner reads the knowledge skill and the smell
   issues.
4. **`docs/cli.md`.** The `--no-baseline-review` row links to section 18
   of `architecture.md`. `docs/your-own-task.md`: the flag joins the list
   of flags that the guided start never asks.
5. **Root `AGENTS.md`.** The Layout block gets `knowledge/`, `smells/` and
   `onboarding/`. `.github/CONTRIBUTING.md`'s short layout gets
   `knowledge/` and `smells/`.
6. **`.agents/skills/knowledge-maintenance/SKILL.md`.** Section 1 says
   "and, later, the baseline reviewer"; the reviewer exists now.
7. **`tests/test_docs.py`.** Two guards:
   - `test_every_role_has_a_row_and_a_section_in_agents_md`: each role in
     `AGENT_TIMEOUTS_SECS` but `preflight` has a row in the role table and
     a level-2 section of `docs/agents.md`, and the count word in the first
     line matches.
   - `test_each_package_directory_is_in_the_layout_blocks`: each directory
     of `src/kiln_harness_tuner/` that is not `__pycache__` appears in the
     Layout block of the root `AGENTS.md` and in the layout block of
     `docs/architecture.md` section 13.
8. **Acceptance 1 and 2.** In two clones of the repository in the
   scratchpad, two fresh `claude -p` sessions, each given one request as a
   person would write it: (1) a new technique from a short description;
   (2) guidance that conflicts with `state-the-output-contract`. Then read
   `git status` and `git diff` of each clone, and run the checks in clone
   1. Nothing from the clones is committed.
9. **Acceptance 5.** Try only if this environment has a Kiln studio
   server. If it does not, record that, give the owner the exact steps and
   command, and show the appendix that the offline fixtures render,
   labelled as such.
10. **The record.** `specs/projects/biological_bootloader/acceptance.md`:
    what each acceptance item of functional spec section 15 is checked by,
    and what happened in steps 8 and 9.
11. **The wheel smoke test**: `uv build`, then
    `python tests/packaging/smoke_installed_wheel.py --dist dist`.

## Tests

- `test_every_role_has_a_row_and_a_section_in_agents_md`: fails when a
  role is added to `AGENT_TIMEOUTS_SECS` with no row or section, or when
  the count word is wrong.
- `test_each_package_directory_is_in_the_layout_blocks`: fails when a new
  package directory is missing from either layout block.
- The existing link check of `tests/test_docs.py` covers the new links in
  the documents.
- The full suite, `ruff check`, `ruff format --check`, and
  `uv run python -m kiln_harness_tuner.knowledge check`.
