# Concepts

The vocabulary the rest of these documents, the terminal display and the run
report all use. Terms are ordered so that each one is defined before it is
used, not alphabetically.

Where this repository uses a word differently from the way the wider field
uses it, the definition says what the word is **not** here. Three words need
that: *arm*, *lever* and *probe*.

> **The worked example.** Several terms below are illustrated on one invented
> task: a **freight depot operations desk**, where an assistant answers
> questions about shipments and acts on them through a depot API. Every
> identifier in it is invented.

## The things the loop works on

**Task.** A Kiln task: a prompt, an output schema, and the run configs, evals
and eval inputs attached to it. The loop optimizes exactly one task per run and
never touches another.

**Run config.** One complete, runnable configuration of a task — a model, a
prompt, tools, skills and model properties such as reasoning level. It is the
unit the loop changes. Every experiment produces a **new** run config; the loop
never edits an existing one, so every measurement stays attributable to
something still on disk.

**Eval.** A Kiln eval: a set of inputs, a scoring method, and one or more
output scores. Two kinds matter here.

- A **code eval** is Python that reads the record of what the agent did and
  returns numbers. It is free, it is identical every time, and it can only
  check machine-comparable facts.
- An **LLM judge** is a second model reading the conversation against a written
  rubric. It can judge prose, it costs money on every item it has not already
  judged, and it is sometimes wrong.

Each output score carries a direction — `higher_is_better`, `lower_is_better`
or `informational`. The loop sign-normalizes every delta to good-direction
units using that flag, so a positive number is always an improvement and no
result needs a label. An `informational` score never drives a verdict.

**Split.** Each eval's inputs are divided into three disjoint sets by tag.

| Split | Who reads it | What it may influence |
|---|---|---|
| `train` | every experiment measurement, and the analyst's judgment | everything the loop learns — experiments, issues, lessons, verdicts |
| `val` | the promotion gate, and nothing else | which run config becomes the baseline |
| `test` | **nobody in the loop, ever** | nothing |

This is an information-flow design, not bookkeeping. Memory records are the
planner's input, so a record written from the selection set would let the loop
steer toward `val` over many waves — overfitting through memory. `test` is
withheld from the loop entirely, so the number a reader ships on is one the
loop never optimized against.

**Issue.** A memory record standing for one thing the task gets wrong. Issues
are the planner's fuel. Each is a derived child of an eval, bound by an
`eval_<eval_id>` tag: the eval is the parent, and the issue is the prose
statement of what that eval catches.

> *Example.* Eval `424242000042` fails when the assistant says a container was
> released and the depot API refused the release. The issue bound to it reads
> `UNMEASURED: issue: reports a release as done when the API returned a refusal
> in the response body`.

**Lever.** A **class of change** to a run config: the prompt, a skill, a tool,
a tool wrapper, a model property. Not a specific edit — "rewrite the tool
description" is a lever, and the particular rewrite is one attempt at it. A
tool's description does not change in place: the rewrite is a code-tool clone
or a wrapper. The
planner tracks attempts per (issue, lever) so that two failed attempts on the
same surface push it to a different surface class rather than to a third
wording.

## The unit of work

**Run.** One invocation of `harness-tuner`. It has a run id of the form
`YYMMDD-HHMM`, a run folder at `runs/<run_id>/` and a report at
`reports/kiln-harness-tuner-<run_id>.md`, both in the state folder
(`~/.kiln_ai/harness_tuner/` by default,
[install.md](install.md#where-the-tuner-keeps-its-files)), and one task. Only
one run per task may be live at a time.

**Guidance.** The free text a person passes with `--guidance`. It reaches every
planner, every wave, verbatim, and the promotion gate. It is the one place a
person steers the run: what to focus on, what is forbidden, and what a
constraint the loop asked about is actually answered as.

**Wave.** One iteration of the loop: plan, run the planned items in parallel,
then the promotion gate. Waves run until the budget, the planner or the
tripwire ends the run.

**Batch plan.** What the planner returns for one wave: a list of **items**, a
one-line headline, a summary of what it considered and rejected, and any open
questions it wants a person to answer later.

**Item.** One unit of work in a wave. Five types:

| Type | What it does | Agents |
|---|---|---|
| `experiment` | build one run config implementing one hypothesis, measure it, judge it | build + analysis |
| `eval_only` | commission a new eval for an issue that has none | eval build |
| `measure_issue_baseline` | run an existing eval against the baseline to settle whether an `UNMEASURED:` issue is real | none — code |
| `remeasure` | complete an existing run config's measurement where coverage was lost | none — code |
| `probe` | answer one yes/no question about a mechanism | code, or a short agent session |

An `experiment` item carries one of two kinds of **work**, and the distinction
runs through the whole loop:

- **fix work** — one hypothesis about one issue, one lever, one change.
- **merge work** — a combination of wins from earlier experiments, which is
  always a promotion candidate. Dividing the problem is only useful if the
  fixes can be put back together, so merge work is where that is confirmed.

**Experiment.** An `experiment` item from plan to verdict, with a memory record
of its own that opens `— RUNNING` and closes with a verdict, `— FAILED` or
`— ABANDONED`.

**Arm.** One experiment's candidate run config, measured against its parent.
The word is borrowed from trial design and is used here in that sense — one
treatment against a control — **not** in the bandit sense. The loop does not
re-pull an arm to reduce its variance; it measures once and moves on.

> *Example.* The issue above gets a `fix` item whose lever is a tool wrapper.
> The build agent creates run config `424242000017` — the baseline plus a
> wrapper that turns a refusal in the response body into a non-2xx result the
> model cannot miss. That run config is the arm.

**Probe.** The cheap check run **before** an arm is spent on a mechanism nobody
has seen work on this task. It answers one yes/no question — does this model
call an attached tool under its output mode, does it read an attached skill,
does a higher reasoning level change how much it reasons — by forking a
throwaway run config, driving a handful of train items, and counting in the
traces. No judge, no eval job, no measurement, no experiment record.

A probe is **not** a small experiment. It answers "does the lever move the
behaviour"; an arm answers "does the lever score". A yes promotes the idea to a
measured arm in the same wave; a no retires it without a build.

**Reconnaissance battery.** The standing set of probes the loop runs on the
baseline before the first plan, so the first planner reads facts about this
model on this task instead of assuming them. Their item ids begin `r0-`. Skip
it with `--no-recon`. It is shaped by the task and the objective: tool and
skill probes run only when the baseline has a tool or a skill attached;
thinking probes use only levels the provider offers — `high` under the
quality objective, one step down and off under the speed objective, which
also time every probe against the baseline.

## Baselines and promotion

**Baseline.** The run config every experiment in a wave branches from. It is
held by a single task-scope memory record tagged `baseline`, whose `rc_<id>`
tag is the pointer. That record carries **no metrics**: val numbers live in a
separate record tagged `val`, which the planner may not read.

**Promotion candidate.** An arm that is both *intended* as a baseline (the
planner said so when it planned the item) and *measured successful* on train
(the analyst said so). Both are required. A knowledge-only experiment, or one
that did not meet its own goals, never reaches the gate.

**The promotion gate.** After every wave, if there are candidates: run every
active eval on `split=val` for each candidate and for the current baseline,
assemble the comparison in code, and pick at most one winner. This is the only
place the loop reads `val`, and it runs *after* the analysis, so no
memory-writing context ever holds val data.

With one candidate the decision is a code rule. With two or more, the promotion
agent ranks them. `--gate-agent always` dispatches the agent either way.

**Coverage floor.** A candidate whose val measurement is less complete than
`DEFAULT_COVERAGE_FLOOR` (0.95, `--coverage-floor`) cannot win that wave, no
matter what the agent decides. A comparison with holes in it is not a
comparison.

**Promotion.** Applying the winner: point the `baseline` record at it, write
the val numbers to the `val` record, mark the issues it fixed, flag the issues
it regressed, and stamp its experiment record. The order is deliberate and
there is no rollback — the baseline pointer is written first, so any partial
apply leaves the system pointing at the right run config.

## Memory

Durable state lives in Kiln memory records, not in files and not in passed
context. Every agent is dispatched fresh and re-orients from memory, which is
why a crashed run loses spend but not knowledge.

| Kind | Scope | Holds |
|---|---|---|
| `baseline` | task, singleton | which run config is the baseline, and the promotion chain |
| `val` | task, singleton | the val numbers behind each promotion |
| `issue` | task | one thing the task gets wrong, bound to its eval |
| `experiment` | task | one experiment's hypothesis, verdict and state |
| `constraint` | project | an open question for a person, and its re-ask condition |
| `lesson` | project | something learned about this world, or about working in it |
| `dead_end` | task | an approach ruled out, with the conditions under which it is dead |

The memory API has no structured fields beyond tags, so state is encoded in
text by convention: lifecycle prefixes on an issue's overview (`UNMEASURED:`,
`UNMEASURABLE:`, `FIXED:`, `WONT_FIX:`) and state suffixes on an experiment's
(`— RUNNING`, `— FAILED`, `— ABANDONED`, `— PROMOTED`, `— REJECTED AT VAL`,
`— HELD`). One module owns those strings, so there is one writer and one
parser. Overviews are capped at `OVERVIEW_MAX_CHARS` and content at
`CONTENT_MAX_CHARS`, both enforced by the API.

## Guarantees with names

**One-crack.** One experiment gets one attempt at its hypothesis. The build
agent finishes and exits **before** any results exist, so it cannot see its own
measurement and tune against it. A second attempt is a new item in a later
wave, planned with the first one's verdict in hand.

**Eval-freeze.** An eval is frozen at handoff. The agent that builds a fix
never authors the eval that measures it, and the agent that reads the results
is dispatched afterward, with data and ids rather than with edit surface.

Both start as **sequencing** properties: the order of dispatch makes the
forbidden writes pointless. The loop gateway also enforces part of each in
code. Each role gets only its typed operations, only the eval
builder can write an eval, and it can write only an eval the same run created.
What still rests on sequencing is in [limitations.md](limitations.md).

**Screen-out.** When an arm's parent has never been measured on the targeted
eval, the loop first compares the arm against the most recent baseline that
*has* full train coverage. An arm that does not beat it is recorded as "did not
beat the latest measured baseline, attribution unmeasured" and costs nothing
more. This is a screen, not a verdict: it never means the change hurt.

**Tripwire.** When more than half a wave's items fail with the same
infrastructure error class, the wave is marked aborted and the loop backs off
once. Two consecutive tripwires end the run — after the promotion gate has run
over whatever did complete, so a measured winner is never stranded behind an
infrastructure failure.

**Janitor.** At startup, every experiment record still reading `— RUNNING` is
stamped `— ABANDONED`. Inside a live run nothing can be left `— RUNNING`, so
that state at startup can only be debris from a dead orchestrator.

**Wrap-up wave.** The last wave, planned as such. Code decides *when* — one
wave's worth of time left — and the planner decides *how*, which is usually to
merge the wins that are pooled and unpromoted. The planner is told only the
conclusion, never the clock.

## Where to go next

- [architecture.md](architecture.md) — how these pieces are built and wired.
- [agents.md](agents.md) — the ten agents and what each is given.
- [your-own-task.md](your-own-task.md) — what a task needs before the loop can
  work on it.
- [limitations.md](limitations.md) — what the loop does not do.
