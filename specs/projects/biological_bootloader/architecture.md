---
status: draft
---

# Architecture: Biological Bootloader

Input: [functional_spec.md](./functional_spec.md). This document gives the
decisions that cross the parts. Four component designs give the detail:

| Component | Document | Functional spec |
|---|---|---|
| Knowledge documents, indexes and checks | [components/knowledge_documents.md](./components/knowledge_documents.md) | sections 2 to 7, 9, 11 |
| Maintenance skill and backlog | [components/maintenance_skill.md](./components/maintenance_skill.md) | sections 4.8, 10, 13.2 |
| Smell detection and the baseline reviewer | [components/smell_detection.md](./components/smell_detection.md) | section 12 |
| Model and provider tools | [components/model_tools.md](./components/model_tools.md) | section 13.1 |

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
6. **`src/kiln_harness_tuner` never imports `kiln_ai`** and makes no HTTP
   call to an outside host today.
7. **The `technique` field on an experiment is a free string**, of at most
   60 characters. No list of its values exists in code.
8. **A raise before wave 1 is fatal and leaves no report.**
9. **Val is read in one function only**, and a role reads val only if it is
   in `VAL_READERS`. Nothing in this project adds a reader of val.
10. **`.claude/skills/` and `.agents/` are git-ignored**, because they are
    targets of `harness-tuner-install-agents --dest .`. A skill for
    contributors cannot live there.

## 2. Decisions

### 2.1 The knowledge system is one skill

`src/kiln_harness_tuner/agent_files/skills/kiln-harness-tuner-knowledge/`.

- It ships in the wheel and is installed for every agent CLI with no change
  to the installer, the read fences or the packaging tests.
- `ruff` already excludes the folder.
- Each role is sent to its own entry file by its prompt template. The
  `SKILL.md` is short and only says which file is for which role.

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

### 2.5 Lever kinds get a constant, and the field stays open

A new constant `CORE_LEVER_KINDS` in `schemas.py` lists the lever kinds
that have a lever document. `FixWork.technique` stays a free string, because
multi-word values are part of its contract and a test protects that. A check
holds the lever documents and the constant to each other.

### 2.6 The contributor material lives in `maintainers/`

A new top-level directory. It holds the maintenance skill, the templates and
the backlog. The root `AGENTS.md` points to it, so every coding agent that
reads `AGENTS.md` finds it. It is in the repository and outside
`agent_files/`, so no loop agent loads it and it is not in the wheel.

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

### 2.9 The model catalogue is fetched once and read by the gateway

The orchestrator fetches Kiln's published model list at the start of a run
and writes it to the run directory. Each gateway process reads it from
there, in the way it reads the model policy. If the fetch fails, the tools
serve what the Kiln server gives, and the log says so. The tuner keeps its
own tolerant models of the file, so it does not import `kiln_ai`.

### 2.10 The tie rule moves to the backlog

The functional spec (section 8) allows this when the change is too large.
The investigation found that it is:

- A tie never reaches the gate. The train-side rules (`success_by_rule`,
  the `futility` and `residual` stop rules) need a positive train delta, so
  they must change too.
- Under the quality objective, the gate has no "not worse" test for cost
  and latency on val. It needs extra paid drives at each gate.
- The stretch goal needs a variance measure on val. None exists. The noise
  check is train-only, optional and run once.
- About ten documented guarantees change from "confirms better" to
  "confirms not worse". A design mark that train-side agents can see is a
  new val-to-train channel.

So in this project the "if it does not move the evals" section of a
technique is knowledge only: the planner uses it to decide if an arm is
worth a slot, and the report can state it. The promoter reads nothing from
the knowledge system. The backlog entry carries these findings. **This
decision needs the owner's confirmation**, and section 8 of the functional
spec changes to match when it is confirmed.

## 3. Repository layout after the project

```
src/kiln_harness_tuner/
  knowledge/                      new package: schema, load, check, index, CLI
  smells/                         new package: detection model, detectors, thresholds, run step
  model_catalog.py                new: fetch and parse Kiln's published model list
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
    kiln-harness-tuner-gateway/SKILL.md   changed: the model and provider tools
  gateway/                        changed: three new operations, one new role
  schemas.py                      changed: new models, CORE_LEVER_KINDS
  orchestrator.py                 changed: ensure_baseline_reviewed, planner input
  memory.py, memory_schema.py     changed: smell issues
  report.py, status.py            changed: the smells appendix
  cli.py                          changed: two flags
maintainers/
  knowledge/
    SKILL.md                      the maintenance skill
    templates/                    one template per component type
    BACKLOG.md                    the backlog
AGENTS.md                         changed: the working rule, the pointer to maintainers/
docs/                             changed: agents.md, architecture.md, how-it-works.md, cli.md
tests/
  test_knowledge.py               new
  test_knowledge_checks.py        new
  test_backlog.py                 new
  test_smells.py                  new
  test_baseline_review.py         new
  test_model_catalog.py           new
```

## 4. Data flow

```
start of run
  model_catalog.fetch ──► <run dir>/model_catalog.json ──► gateway: list_models,
                                                            get_model_details, list_providers
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
| The model catalogue cannot be fetched or parsed | The tools serve the Kiln server's data only. The event `model_catalog_unavailable` gives the reason. |
| One entry of the model catalogue is not valid | That entry is dropped. The rest is used. |

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
- **The model catalogue.** `respx` serves the published file. Tests cover a
  good file, a file with a bad entry, a timeout and a file of the wrong
  shape.
- **Documents held to code.** `docs/cli.md`, `docs/agents.md` and the
  gateway skill are already held to the code by tests. Those tests are
  extended, not replaced.

## 7. Changes outside this repository

The model and provider tools become more useful when Kiln's model file has
more fields. The tools work with today's fields. Each proposed field is
optional with a default, so an older Kiln ignores it.
[components/model_tools.md](./components/model_tools.md) lists the proposals.
The owner decides them in the Kiln repository. No phase of this project
waits for them.

## 8. Order of the work

The implementation plan sets the phases. The dependencies are:

1. The `knowledge` package, the schemas, the checks and the generator, with
   one example of each component type.
2. The maintenance skill, the templates, the backlog and the `AGENTS.md`
   rule. This needs 1.
3. The V1 content, with the move of the lever ladder and the changes to the
   templates. This needs 1 and 2, and the V1 coverage phase decides it.
4. The smell detectors. This needs the smell documents of 3.
5. The baseline reviewer. This needs 4.
6. The model and provider tools. This needs nothing from 1 to 5.

## 9. Open points for the owner

1. **The tie rule** (section 2.10): confirm that it moves to the backlog.
2. **The first call to an outside host** (section 2.9): a run will fetch
   `https://remote-config.getkiln.ai/kiln_config_v2.json` at its start. A
   flag turns it off. Confirm that this is acceptable as the default.
3. **`maintainers/`** (section 2.6) as the name and place of the contributor
   material.
4. **Fields for Kiln's model file**
   ([components/model_tools.md](./components/model_tools.md), section 6):
   the proposals avoid prices and speeds, because those have a high
   maintenance cost.
