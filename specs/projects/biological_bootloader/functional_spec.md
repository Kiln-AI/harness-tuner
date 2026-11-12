---
status: draft
---

# Functional Spec: Biological Bootloader

Inputs: [project_overview.md](./project_overview.md),
[knowledge_system_design.md](./knowledge_system_design.md) (the owner's full
notes on the component design),
[the research summary](./research/harness-optimization/summary.md) and
[kiln_gaps.md](./kiln_gaps.md).

## 1. Purpose

Harness Tuner's loop works. Its weak point is the quality of its hypotheses:
which experiments the planner proposes, and in which order. This project
builds the **knowledge system** that improves those hypotheses, and the means
to keep that system correct as it grows.

**This project is groundwork, not completion.** It delivers a structure that
lets the team add knowledge in a systematic way, a small set of real content
that proves the structure, and lists of what comes next. V1 may not be a
large improvement to the tuner. It is the base for later improvement.

### 1.1 Goals

1. A knowledge system made of components (capabilities, techniques,
   playbooks) that the planner and the experiment builder read.
2. A design in which a new technique is one file plus a few edits.
3. A maintenance skill for the coding agents that work on this repository.
   It is the way to add to the knowledge system, change it and audit it:
   it gives the agent the templates, the placement rules and the checks, so
   that each change keeps the system's quality.
4. Automated tests that keep the structure correct.
5. A pluggable red-flag framework in code, with two detectors.
6. Model and provider tools for the loop agents, with data derived from
   Kiln's model list.
7. A backlog: one document, with a fixed format, that tracks the known gaps
   and the pending ideas. A to-do list is part of a system that can be
   maintained.

### 1.2 Not in this project

- **Gateway and API work.** No new gateway operation. Capabilities that the
  tuner cannot reach today (RAG, sub-agents, worlds and others) get an entry
  in the backlog, not a capability document. The one exception is the model
  and provider tools (section 13.1). See `kiln_gaps.md`.
- **A change to the promotion gate.** See section 8.
- **Proof that the knowledge improves convergence.** The chief scientist
  tests V1 after this project. This project makes that test possible.
- **The casebook.** It starts in parallel, outside this repository.
- **The collection of user restrictions** (overview section 8). A separate
  project.
- **Complete coverage.** V1 coverage is decided in its own phase
  (section 14).

## 2. Terms

| Term | Meaning |
|---|---|
| Knowledge system | The whole set of documents, indexes and lists in this spec. |
| Component | One document of a fixed type: capability, technique or playbook. |
| Capability | What a harness can do, and how the tuner creates and changes it. |
| Technique | A guide to one specific optimisation. |
| Playbook | Guidance centred on one goal or one situation. It links to techniques. |
| Prior | One real case in which a technique was used. |
| Backlog | The one document that lists the known gaps and the ideas that are not written yet. |
| Red flag | A fact about the harness or its traces that code finds, and that points to a playbook. |
| Maintenance skill | The skill a coding agent uses to add to, change and audit the knowledge system. |
| Planner | The batch-selection agent. |
| Builder | The experiment-build agent. |

**A name collision to resolve.** The code already uses the word
`technique`. It is a field on an experiment (`prompt`, `skill`,
`tool_config`, `new_tool` and others), and the key of `lever_attempts`. That
field names the *kind of surface* an experiment changes. It is closer to a
capability than to a technique in this spec. This spec calls the existing
field the **lever kind**. Proposed rule: this project does not rename the
code field. Each technique document states which lever kinds it uses, so
that the count in `lever_attempts` still works. See open question 1.

## 3. Readers

| Reader | Reads | Entry point |
|---|---|---|
| Planner | Playbooks and techniques, and the catalogue of common issues | Its own entry file |
| Builder | Techniques and capabilities | Its own entry file |
| Analyzer | The catalogue of common issues, so that it can name one when it reads traces. Not every issue can be found by code. | Its own entry file |
| Promoter | Only the guidance for a tie (section 8) | Given in its input, for the candidates at hand |
| Report writer | Playbooks, techniques and priors, where they help it explain the run | Its own entry file |
| Probe, reviewers, eval roles | Nothing from the knowledge system | — |
| Coding agents and people who maintain the system | Everything, plus the maintenance skill | The maintenance skill |

The probe answers one yes/no question that the planner asks. It does not
look for problems, so it does not read the catalogue. The component type for
common issues, and its name, are open (section 16).

- Each reading role has its own first file. The index and the
  progressive-disclosure path of each entry file fit that role. The planner
  starts from goals and situations. The builder starts from the technique
  named on its work item.
- The role's prompt template tells the agent to load its entry file. The
  system does not depend on the agent deciding to open a skill. (The
  research found that agents often do not open a skill by themselves.)
- A role does not need the other role's entry file to do its work.

## 4. Components

The architecture step locks the exact schema of each component. This section
states what each one must hold.

### 4.1 Common to every component

- A title and a one-line description. Both have length limits.
- A **last-reviewed date**.
- Text that follows the content rules in section 6.
- A location and a file name that follow one fixed convention. **The path
  gives the component type.** The file does not repeat it.
- A path from an entry file (section 5).
- **Sub-files, when it needs them.** A component can have its own reference
  files, in a directory beside it with the same name without `.md`. Example:
  a technique for agent-friendly API documents can carry reference files.
  The primary document is responsible for the disclosure of its sub-files
  and for the links to them.

### 4.2 Capability

A capability describes one thing a harness can do: for example the system
prompt, skills, code tools, the model, the thinking level.

It must hold:

- what the capability is;
- how the tuner creates and changes it: the gateway operations, the rules of
  the object (for example "a skill is immutable; a change is a new version"),
  and the limits;
- the lever kind or kinds it maps to;
- links to the existing how-to material (for example the code-tool authoring
  skill), without a copy of it.

It must not hold when or why to use the capability. That belongs to
techniques and playbooks.

A capability document exists only for a capability the tuner can change
today.

### 4.3 Technique

A technique is a guide to one specific optimisation. One capability can have
several techniques. One technique can use several capabilities.

It must hold:

- **When to use**: the design patterns, goals and failure modes that suggest
  it.
- **When not to use.**
- **Alternatives**: links to other techniques, each with the condition under
  which it is the better choice.
- **How**: the recipe, with examples where we have them. It links to the
  capabilities it uses.
- **Budget information**: whether it is expensive or cheap, and whether to
  leave it out on a small budget (section 7).
- **If it does not move the evals**: one of "keep it, it is a better
  design", "it must move an eval", or "it depends", with the conditions
  (section 8).
- **Priors**: a list of real cases (section 4.5). The list can be empty.
- **Confidence**: a statement in text, with its conditions, for the
  technique as a whole. A reader must be able to tell an idea that one
  person proposed from a result seen many times. Confidence is text, not an
  enum.
- The lever kinds it uses.

A technique that is out of fashion stays. Its "when to use" changes, and so
does each playbook that suggests it.

### 4.4 Playbook

A playbook is centred on one goal or one situation. There are three kinds:

- **User goal**: for example "much cheaper", "open models on-prem".
- **Eval problem**: for example an eval near zero with traces that suggest
  no fix, a saturated eval, train wins that validation keeps rejecting, high
  variance.
- **Design issue or red flag**: for example tool search, a very old model, a
  very long prompt.

It must hold:

- the goal or situation, and how to recognise it;
- an ordered set of things to try, each a link to a technique with the
  condition under which it applies;
- what to do first, and what not to do yet ("if X is broken, do not
  optimise Y");
- how the order changes with the budget, if it does.

Playbooks hold the opinions about order. There are fewer playbooks than
techniques. A small group owns them, by informal agreement.

### 4.5 Priors

- A prior is one sample, not a universal lesson. The text must say so.
- A prior gives enough detail to inform when, how and at what scale: the
  shape of the agent, what was tried, what happened.
- A prior never names a client and never holds client data.
- **Approval rule.** A person must approve a prior or case study before it
  is committed, unless it is clearly public information. A result can be
  sensitive even when no entity is named. Approval at pull-request review is
  too late: it comes before the commit.
- A prior never comes from the run's own target or from the moderation demo
  (section 6).
- Separate, longer prior documents (the "P3" idea) are not in V1. The
  schema must allow a prior to link to one later.

### 4.6 Other content

The three components are not the whole system. Process guidance, tips and
the two guides in section 10 also have a place. The architecture gives each
one a location and a path from an entry file.

### 4.7 The backlog

- **One document.** It is the one place to debate the next change with the
  highest leverage.
- It holds two kinds of entry:
  - **a gap**: something Kiln or the tuner cannot do yet (for example the
    tuner cannot create a sub-agent);
  - **a pending idea**: a technique, playbook, capability or detector that
    is not written yet.
- **A fixed format** for each entry: a title, the kind, one or two lines, a
  source if there is one, and what it depends on.
- An entry needs no debate to be added. Its rank does.
- Loop agents do not read the backlog. It is in the repository, outside the
  files that loop agents load.
- `kiln_gaps.md` in this spec folder is the first source of the gap entries.

## 5. Navigation

- **Generated indexes.** Code builds each index from the component files. A
  person does not edit an index. A test fails when an index is out of date.
- **The planner's entry file** lists every playbook, with its one-line
  description and a link. The planner reaches a technique through a
  playbook, because a choice by goal is better than a choice by name.
- **The builder's entry file** lets the builder reach the technique on its
  work item directly, and from there the capabilities.
- **A technique index** exists for one case only: the user's free-form
  guidance names a technique ("try a sub-agent for X"). The index carries
  the instruction "prefer a playbook; use this index only when the user
  names a technique".
- **Every file is reachable** from an entry file through links. A test
  proves it.
- **The path is short.** The research found that deep disclosure trees
  lower accuracy. The target is: entry file, then playbook, then technique,
  then capability, with no level that only routes. The architecture sets the
  limit and a test enforces it.
- **Size limits.** Each component type has a maximum length. A document over
  the limit must be split. The architecture sets the numbers.

## 6. Content rules

These rules apply to every document that a loop agent reads. The maintenance
skill enforces them, and the tests check what a test can check.

1. **Experiments decide.** No document states a rule of the form "you must
   do X". A document says what to try, in what order, and under which
   conditions. A measured result is final. A tie can be settled from the
   knowledge, or from intuition.
2. **State conditions, not defaults.** The research found a measured case
   where each "almost always" claim loses. A recommendation states the
   condition under which it holds.
3. **State confidence** (section 4.3).
4. **Date the guidance.** This field changes each month.
5. **The cost of learning against the cost of maintenance.** A large base
   of facts is hard to maintain. A fact about one model, or about one point
   in time, goes out of date quickly. If the loop learns a lesson easily,
   let it learn it. Example: a provider rejects a non-default temperature,
   and one failed call shows it. If a lesson is expensive to learn, save
   it. Be careful with anything that has a high maintenance cost and is
   likely to go out of date.
6. **Nothing from the target.** No measurement, lever advice or lesson that
   comes from the run's own target or from the moderation demo. This is the
   existing rule, and it stays.
7. **General knowledge and other projects are allowed.** Field knowledge,
   public sources, our own synthetic experiments and anonymised cases from
   other projects are allowed, under the approval rule in section 4.5. This
   replaces the present rule that bans all lever advice and all measured
   numbers from `agent_files/`. Section 13 has the new wording for
   `AGENTS.md`.
8. **Simplified Technical English**, as for the rest of the repository.
9. **Do not copy how-to material.** Link to the capability or to the
   existing authoring skill.

## 7. Budget

- Budget awareness is knowledge. This project does not change how the loop
  computes or enforces a budget.
- Each technique states its cost class and whether it fits a small budget.
- Each playbook states how its order changes with the budget, if it does.
- One playbook covers the order of work by budget size: for example, on a
  small budget pick a model with the model tools and start; on a large
  budget a model scan first, and a settings scan last, become reasonable.
- The planner must be able to read the run's budget and the cost of the
  evals. If its input does not carry these today, that is a finding for the
  architecture step, not a silent assumption.

## 8. A change that does not move the evals

- Each technique states what to do when it does not move an eval
  (section 4.3).
- **The promotion gate does not change in this project.** A candidate
  becomes the baseline only when held-out data confirms it.
- So in V1 this guidance has a limited effect: the planner can use it to
  decide whether an arm is worth a slot, and the report can state it. It
  cannot promote a "better design, same eval" change.
- A follow-up project changes the gate. It goes on the backlog.

## 9. The existing lever ladder

The planner's skill already has a lever ladder
(`kiln-harness-tuner-batch-selection/references/lever-ladder.md`): a table
from a failure class to the lever to try first. It is a playbook in all but
name.

- The lever ladder moves into the knowledge system. Its rows become
  playbooks of the "design issue" and "eval problem" kinds, and the levers
  they name become techniques.
- After the move there is one source of truth. The old file is removed, and
  the planner's skill links to the new entry file.
- The process rules that go with the ladder (name the decision point; after
  two failed text arms, change the surface class) stay with the planner's
  process, or become a playbook. The architecture decides.
- The ladder's present evidence rule ("cite nothing: no measured number, no
  model name") is replaced by the rules in section 6.
- No guidance is lost in the move. A check in the V1 coverage phase lists
  each row of the ladder and where it went.

## 10. The maintenance skill

**Reader.** A coding agent that edits the knowledge system, and the person
who directs it. Loop agents never read it.

**Reach.** It must work for any coding agent. `AGENTS.md` points to it. If
one agent CLI needs a special location, Claude Code is the one to support
first.

**Requests it must handle:**

| Request | What the skill makes the agent do |
|---|---|
| "Read this post and add this approach" | Find the right component type and level. Check for an existing technique that covers it. Add or extend. |
| "We learned a lesson, record this" | Add a prior to the right technique, under the approval rule. Change the confidence text if the lesson justifies it. |
| Add a technique, playbook or capability | Start from the template. Fill every required part. Add the links. Rebuild the indexes. Run the tests. |
| Add an idea or a gap for later | Add an entry to the backlog, in the fixed format. |
| A technique went out of fashion | Change its "when to use" and each playbook that suggests it. Do not delete it. |
| Audit | Check documents against the schema and the content rules, and report. |

**Rules it enforces:**

- **Right level.** A new item does not go to the top level because it is
  new. The skill gives the test for each level.
- **No silent removal.** An older approach is not deleted or rewritten to
  make room for a new one.
- **Conflict is a decision.** When new guidance conflicts with existing
  guidance, the agent stops and asks the person: deprecate the old one, or
  state when to use each. The decision is recorded in the documents.
- **Approval before commit** for a prior that is not clearly public.
- **Playbooks are owned.** A change to a playbook needs the owners. A change
  to a technique or a capability does not. The ownership is informal: there
  is no CODEOWNERS file.
- **Run the generator and the tests.** Each change ends with the indexes
  rebuilt and the tests passed.
- **Reasons stay with rules.** The research found that an instruction file
  grows without limit unless the reason is written next to each rule.

**It also holds** the templates and the writing guide for each component
type, and the guide for an agent that audits a component.

## 11. Automated tests

The tests are part of the offline suite. They need no server and no model.

- Each component has its required front matter, with valid values.
- Each component has its required sections, in order.
- The title and the descriptions are present and inside their length
  limits.
- Each document is inside its size limit.
- Each link resolves.
- Each file is reachable from an entry file, inside the depth limit.
- Each generated index matches the files.
- Each technique names lever kinds that exist in the code.
- Each playbook links to techniques that exist.
- Each capability names gateway operations that exist.
- The backlog has the required format.

A test cannot check the content rules that need judgement (conditions,
confidence, the approval rule). The maintenance skill's audit covers those.

## 12. Red flags

A red flag is a fact that code finds in the harness or in its traces before
the planner starts. It gives the planner the obvious problem at the start,
so that an LLM does not have to find it.

- **A pluggable framework.** A detector is a small unit of code with a fixed
  interface. A new detector is one file plus a registration. It needs no
  change to the loop.
- **Input.** The baseline run config and the objects it attaches (prompt,
  skills, tools, model). The framework must also allow a detector that reads
  traces in bulk, although V1 has no such detector.
- **Output.** For each flag: a statement of fact with its numbers, the
  reference value it was compared with and the source of that value, and a
  link to a playbook. A flag states a fact. It does not state a verdict.
  (The analysis code has a "no verdict labels" rule, and the red flags must
  agree with it.)
- **Delivery.** The planner gets the flags before wave 1, with the results
  of the setup reconnaissance. The run report lists them.
- **A flag does not block the run.** The playbook says what to do first.
- **Detectors in V1:**
  - **Very long prompt.** The system prompt is over a size limit.
  - One more that is as easy, chosen in the V1 coverage phase.
  - **Very old model** moves to the backlog. It needs model data.
- **Thresholds** are named values in one place, each with its source. The
  research found no measured optimum, only the product limits of agent
  vendors. Each threshold is therefore a default that can change.
- **The backlog** holds the detectors for later (a very old model, large tool results, long
  tool descriptions, an almost empty skill, too many skills, too many tools,
  repeated calls and others).
- This work is a later phase of the implementation plan.

## 13. Other deliverables

### 13.1 Model and provider tools

The data is dynamic, so the loop agents get it from **tools**, not from a
written guide.

- **Source.** Kiln's model list (`ml_model_list.py`), which Kiln already
  publishes over the air. Both the model data and the provider data are
  derived from it.
- **Model tools.** One tool lists models, with filters (for example by cost
  class, by speed, by task fit). One tool gives the details of one model.
  The exact names and filters are for the architecture step.
- **Provider tools.** A new concept, built the same way: which providers
  serve a model, and what is known about each (for example speed, and
  whether it offers zero data retention).
- If the fetch of the list fails, the run uses the copy that came with the
  installed Kiln or with the package, and says so in the log.
- The model policy still applies. A tool never returns a model that the
  policy does not allow.
- Kiln's model file may need new fields. Those are changes in the Kiln
  repository. The architecture step lists them, and this project depends on
  them.
- Benefits: a publishing channel that does not need a PyPI release, and an
  existing skill that maintains the file.
- The stable guidance about how to choose a model (lock a model class, tune,
  scan again) is knowledge, and goes in playbooks and techniques.
- This work is a later phase of the implementation plan.

### 13.2 The `AGENTS.md` working rule

The present rule says that lever advice, measured numbers and what worked in
an earlier run never go in `agent_files/`. The new rule:

- Nothing measured on the run's own target, and nothing from the moderation
  demo, goes in the files that loop agents load. This is unchanged.
- General field knowledge, public sources, our own synthetic experiments and
  anonymised cases from other projects are allowed in the knowledge system.
- A person approves a case study or a prior before it is committed, unless
  it is clearly public information.
- The knowledge system is edited through the maintenance skill.

## 14. V1 content

**The V1 coverage phase** comes after the architecture and before the
implementation plan. It decides which components V1 contains and what goes
in the backlog. The owner supplies a separate research report at the
start of that phase. The report seeds the lists. It is not committed.

What this spec fixes now:

- **Capabilities:** one document for each thing the tuner changes today:
  the system prompt, skills, code tools and tool wrappers, tool attachment,
  the model and provider, and the model settings (thinking level, sampling
  settings). Nothing for what the tuner cannot reach. Input transforms and
  the structured-output mode go in the backlog.
- **Playbooks and techniques:** at least everything the lever ladder holds
  today (section 9), and enough new ones to exercise each kind of playbook
  and each part of the technique schema. The coverage phase names them.
- **The backlog:** seeded from the overview, the owner's notes, the
  research and `kiln_gaps.md`.

## 15. Acceptance

1. A coding agent with the maintenance skill adds a new technique from a
   short description. The result passes the tests, and the change is one new
   file plus index and link edits.
2. The same agent, given guidance that conflicts with an existing technique,
   stops and asks for the decision.
3. The planner's template loads the planner's entry file, and the builder's
   template loads the builder's. The old lever ladder file is gone, and each
   of its rows has a place in the new system.
4. Each structural rule in section 11 has a test. A deliberate violation of
   each rule fails its test.
5. A run on the demo project shows the red flag in the planner's input and
   in the report, when the baseline has a very long prompt.
6. A loop agent lists models and reads the details of one, from data derived
   from Kiln's list. The tools fall back when the fetch fails.
7. `AGENTS.md` has the new rule.
8. The repository checks pass: `pytest`, `ruff check`, `ruff format --check`.

## 16. Open questions

1. **The component set and its names.** Under discussion with the owner:
   improvements to the capability, technique and playbook design, the new
   component type for common issues and its name, and the name collision
   with the code field `technique` (section 2). Sections 2 to 5 change when
   this is settled.
2. **A change that does not move the evals.** Whether this project changes
   how a tie is promoted (section 8). Under discussion.

Settled: playbook ownership is informal (section 10). The provider data is
derived from Kiln's model list (section 13.1). Input transforms and the
structured-output mode go in the backlog (section 14).
