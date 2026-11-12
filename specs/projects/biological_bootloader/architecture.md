---
status: draft
---

# Architecture: Biological Bootloader

Input: [functional_spec.md](./functional_spec.md). This document gives the
decisions that cross the parts. Three component designs give the detail:

| Component | Document | Functional spec |
|---|---|---|
| Knowledge documents, indexes and checks | [components/knowledge_documents.md](./components/knowledge_documents.md) | sections 2 to 7, 9, 11 |
| Maintenance skill and backlog | [components/maintenance_skill.md](./components/maintenance_skill.md) | sections 4.8, 10, 13.2 |
| Smell detection and the baseline reviewer | [components/smell_detection.md](./components/smell_detection.md) | section 12 |

All paths are relative to the repository root. Line numbers are those of the
tree on 2026-11-12 and are for orientation only.

## 1. Constraints found in the code

These facts decide much of the design.

1. **Loop agents can read only the installed skills folder.** A run installs
   every file under `src/kiln_harness_tuner/agent_files/skills/` and
   `agent_files/AGENTS.md`, and nothing else (`install_agents.py`,
   `expected_files`). Each of the three agent CLIs fences reads to that
   folder. So the knowledge system is a skill.
2. **Every skill is installed for every role.** There is no allow list by
   role. Which files a role reads is set by its prompt template.
3. **A skill is loaded because the template says so in plain words.** A
   template can also name a file inside a skill. No mechanism checks the
   path, apart from tests.
4. **No generated file exists in the repository today.** The index
   generator and its staleness test are new.
5. **PyYAML and Pydantic are runtime dependencies already.** Nothing new is
   needed to parse front matter.
6. **The `technique` field on an experiment is a free string**, of at most
   60 characters. No list of its values exists in code.
7. **A raise before wave 1 is fatal and leaves no report.**
8. **Val is read in one function only**, and a role reads val only if it is
   in `VAL_READERS`. Nothing in this project adds a reader of val.
9. **`/.agents/` is git-ignored today.** The rule is from the time when a
   run was made inside this repository and the loop skills were installed
   there.

## 2. Decisions

### 2.1 The knowledge system is one skill

`src/kiln_harness_tuner/agent_files/skills/kiln-harness-tuner-knowledge/`.

- It ships in the wheel and is installed for every agent CLI with no change
  to the installer, the read fences or the packaging tests.
- `ruff` already excludes the folder.
- It has a `SKILL.md`, as every skill must. That file is short: what the
  knowledge system is, and which entry file is for which role.
- Each role's prompt template names its entry file directly.

### 2.2 The path gives the type, and front matter is small

A component is one Markdown file with YAML front matter. Its directory
(`levers/`, `techniques/`, `playbooks/`, `smells/`) gives its type. Its file
name without `.md` is its id. Front matter holds only what code filters on
or checks. Everything else is prose in required sections. Links to other
components are ordinary Markdown links in the body, and the checks read
them from the body. No link is written twice.

### 2.3 Indexes are generated blocks inside the entry files

A separate index file would be a level that only routes, which the spec
forbids. So each entry file has hand-written text and one or more blocks
between markers that a generator fills. A test fails when a block is stale.

### 2.4 One Python package does load, check and generate

`src/kiln_harness_tuner/knowledge/`. The tests call it, the maintenance
skill tells the agent to run it, and the loop uses its loader at run time to
validate smell ids.

It is run as a module. It adds no console script, because the install
documents list every console script and a test holds them to the code.

### 2.5 A lever document is named after the lever, and code is not changed

The id of a lever document is the short name that the planner writes in the
`technique` field: `prompt`, `skill`, `tool_config`, `new_tool`, `params`.
A technique names the levers it uses by those ids, and a check makes sure
that each one is a lever document.

An earlier draft added a constant in code with the lever names, and a test
that every name has a document. It is dropped. The field is a free string by
design, the planner can write a name of a few words, and nothing in the loop
depends on a closed list. The check that matters is inside the knowledge
system: a technique cannot name a lever that has no document.

### 2.6 The maintenance skill is in `.agents/skills/`

`.agents/skills/knowledge-maintenance/`, with its templates. The
`.gitignore` rule for `/.agents/` is removed, and so is the step in
`.github/CONTRIBUTING.md` that installs the loop skills into the root of
this repository: such an install goes into a new, clean folder. The root
`AGENTS.md` points to the skill, because Claude Code does not read
`.agents/skills/`. The backlog is `specs/backlog.md`, in the planning area,
because people read and rank it.

### 2.7 Smell detection has one call site

One function, `ensure_baseline_reviewed`, runs at the top of each wave when
the baseline is not the one last reviewed. That one call covers the start of
a run and every promotion. It runs the code detectors, then the baseline
reviewer. It never raises.

### 2.8 The baseline reviewer returns findings, and code writes the issues

This is the pattern of the baseline audit. The role gets no memory write
operation. Code validates each smell id against the knowledge system, then
writes one issue for each finding, with tags that name the smell and the run
config.

### 2.9 Two parts are separate projects

Decided by the owner on 2026-11-12. Each has a project overview that records
the decisions and findings so far, and each gets an entry in the backlog.

- **Model and provider information:**
  [model-provider-info](../model-provider-info/project_overview.md). In this
  project, no gateway operation is added and nothing is fetched from an
  outside host.
- **Promotion on a tie:**
  [tie-promotion](../tie-promotion/project_overview.md). In this project the
  "if it does not move the evals" section of a technique is knowledge only,
  and the promoter reads nothing from the knowledge system.

## 3. Repository layout after the project

```
src/kiln_harness_tuner/
  knowledge/                      new package: schema, load, check, index, CLI
  smells/                         new package: detection model, detectors, thresholds, run step
  agents/
    baseline_review.md            new prompt template
    batch_plan.md                 changed: reads the planner entry file
    create_experiment_rc.md       changed: reads the builder entry file
    analyze_experiment_results.md changed: reads the smells entry file
    report_writer.md              changed: reads the report entry file
  agent_files/skills/
    kiln-harness-tuner-knowledge/         new skill: the knowledge system
    kiln-harness-tuner-baseline-review/   new skill: the reviewer's procedure
    kiln-harness-tuner-batch-selection/
      references/lever-ladder.md          removed, moved into the knowledge system
  gateway/ops.py                  changed: one new role
  schemas.py                      changed: new models
  orchestrator.py                 changed: ensure_baseline_reviewed, planner input
  memory.py, memory_schema.py     changed: smell issues
  report.py, status.py            changed: the smells appendix
  cli.py                          changed: one flag
.agents/skills/knowledge-maintenance/
  SKILL.md                        the maintenance skill
  templates/                      one template per component type
specs/backlog.md                  the backlog
.gitignore                        changed: the /.agents/ rule is removed
.github/CONTRIBUTING.md           changed: no install into the repository root
AGENTS.md                         changed: the working rule, the pointer to the skill
docs/                             changed: agents.md, architecture.md, how-it-works.md, cli.md
tests/
  test_knowledge.py               new
  test_knowledge_checks.py        new
  test_backlog.py                 new
  test_smells.py                  new
  test_baseline_review.py         new
```

## 4. Data flow

```
each wave, when the baseline is not yet reviewed
  ensure_baseline_reviewed
    smells.run_detectors(harness, stored train trace stats) ──► ctx.detections
    dispatch.baseline_review(detections, trace sample) ──► findings
    code: one issue per finding, tags [issue, smell, smell_<id>, audited_rc_<rc>]
  planner: BatchPlanInput.detections + smell issues in memory
           + knowledge/references/planner.md (smells, playbooks) ──► techniques
  builder: knowledge/references/builder.md ──► the technique of its item ──► levers
  analyzer: knowledge/references/smells.md
end of run
  report: RunStatus.detections, RunStatus.baseline_reviews ──► the smells appendix
```

## 5. Error handling

| Failure | Result |
|---|---|
| A knowledge document breaks a rule | A test fails. It never reaches a run, because the check runs in CI. |
| The knowledge skill cannot be loaded at run time | The run goes on. Smell ids are not validated: a finding with an id is kept, and the event `knowledge_unreadable` is logged. |
| A smell detector raises | That detector is skipped, with the event `smell_detector_failed`. The other detectors run. |
| The baseline reviewer fails, times out or is turned off | The run goes on with the code detections. The event `baseline_review_skipped` gives the reason, and the report states it. |
| The reviewer names a smell id that does not exist | The finding is dropped, with the event `smell_unknown`. |

`ProviderQuotaError` is never swallowed. It keeps its present meaning: the
run ends with exit `quota_exhausted`.

## 6. Testing strategy

All tests are in the offline suite: no server, no model, no network. The
component designs name each test.

- **The real tree.** One test runs every structural check on the shipped
  knowledge skill and on the backlog.
- **Each rule.** For each structural rule, a test builds a small tree in a
  temporary directory with one deliberate violation, and asserts that the
  check reports that rule and nothing else.
- **Generated blocks.** A test generates the blocks in memory and compares
  them with the files.
- **Role wiring.** The existing tests that pin the role sets are extended
  for the new role. The fake dispatcher gets the new method.
- **Detectors.** Each detector is a pure function over a small input, with
  a test on each side of its threshold.
- **The orchestrator step.** Tests with the fake dispatcher and the fake
  Kiln cover: a review at wave 1, a review after a promotion, a review that
  fails, a review turned off, a finding with an unknown smell, and the
  planner input.
- **Documents held to code.** `docs/cli.md`, `docs/agents.md` and the
  gateway skill are already held to the code by tests. Those tests are
  extended, not replaced.

## 7. Order of the work

The implementation plan sets the phases. The dependencies are:

1. The `knowledge` package, the schemas, the checks and the generator, with
   one example of each component type.
2. The maintenance skill, the templates, the backlog and the `AGENTS.md`
   rule. This needs 1.
3. The V1 content, with the move of the lever ladder and the changes to the
   templates. This needs 1 and 2, and the V1 coverage phase decides it.
4. The smell detectors. This needs the smell documents of 3.
5. The baseline reviewer. This needs 4.
