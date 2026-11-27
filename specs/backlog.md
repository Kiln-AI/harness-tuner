# Backlog

This file lists the known gaps (what Kiln or the tuner cannot do yet) and
the pending ideas (a technique, playbook, smell, lever, detector or code
change that is not written yet). It is the one place to debate the next
change with the highest leverage. Loop agents do not read it. To add an
entry, add it at the end of `Unranked`, in the format below, and do not
rank it: the owners rank, and to move an entry into `Ranked` is their
decision. The order under `Ranked` is the rank. Each entry has a unique
title (`###`), the four fields in this order, and one to three lines of
text, each at most 100 characters. `Kind` is one of gap, technique,
playbook, smell, lever, detector or code. `Where` is one or more of
knowledge, tuner or kiln, joined by `, `. `Depends on` is `None`, or the
titles of other entries joined by `; `. `Source` is a link, a short
reference or `None`. `tests/test_backlog.py` checks the format.

## Ranked

### Input transforms

- Kind: gap
- Where: tuner
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 1; overview 7.3

The lever, and its techniques: keep only the fields the task needs, cut long text, add computed
features, remove a field that misleads. The run config field passes through, and nothing teaches,
screens or checks it.

### Sub-agents

- Kind: gap
- Where: tuner
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 2; overview 7.2

The lever, and its techniques: a stronger model for deep thinking; a specialist with its own tools;
a cheap reader for a large source. A fixed form for what comes back. Limits: one turn, and no fix
for a strong single agent. Tune a critical sub-agent on its own evals, then the main agent.

### Promotion on a tie

- Kind: code
- Where: tuner
- Depends on: None
- Source: [tie-promotion](projects/tie-promotion/project_overview.md)

Knowledge settles a tie, never a loss. The stretch goal is a difference inside the variance.

### Single-turn prompt caching in Kiln

- Kind: gap
- Where: kiln
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 3

The cost that Kiln measures can be wrong when a single-turn task gets no caching. It also decides
if `skill-into-the-prompt` can save money.

### The task shape for loop agents

- Kind: gap
- Where: tuner
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 4

`get_task` drops `turn_mode`. The step "know the agent before you plan" needs it. The model
capability flags are in `get_model_details` (project model-provider-info).

### RAG

- Kind: gap
- Where: tuner
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 5; overview 7.1

The lever. RAG or skill, and when each wins. Documents and extraction first; extract once and do
not tune it. Test that RAG helps before the chunker and the search type. Reranking. Size in tokens,
not in documents.

### Detectors from the trace counts

- Kind: detector
- Where: tuner
- Depends on: None
- Source: functional spec 12.3; [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), "Not a Kiln gap"

A very large tool result; repeated calls and loops; a run that ends at a limit; unused tools and
skills; a skill of a few words. The raw counts exist in `gateway/trace_files.py`.

### Budget-aware planning

- Kind: code
- Where: tuner
- Depends on: None
- Source: overview 5 and 5.1

Size each experiment so that it can pass its own test; do not split a small budget over many
experiments; probe for room to improve before a wording change; measure the baseline again in each
wave; a gate that uses support across repeats. The knowledge side is `spending-the-budget`.

### OpenEnv worlds

- Kind: lever
- Where: knowledge, tuner
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 9; overview 7.7; owner

A world is hard to change, so the tuner does not change it: it wraps it in Kiln code tools. Teach
what a world is, which worlds and fixtures a project has, and how to read them. The vendored API
reference lacks the `/worlds` routes.

## Unranked

### All tools in one general API tool, with a skill

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [design notes](projects/biological_bootloader/knowledge_system_design.md)

Replace many narrow tools with one general API tool, and add the skill that describes the API.

### A stronger model as an adviser to a cheap one

- Kind: technique
- Where: knowledge
- Depends on: Sub-agents
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 14

A cheap model calls a stronger model as a tool when it needs advice.

### A cheap model first, then a stronger one when it is not sure

- Kind: technique
- Where: knowledge
- Depends on: Sub-agents
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

A cascade: the cheap model answers, and a stronger model takes the cases where it is not sure.

### A short index of the reference material, always in context

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

A compact index of the reference material in the prompt, so that the agent knows what exists.

### One prompt for each type of input

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Send each type of input to its own prompt.

### A step outside the model that replaces a model step

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

A calculator, a date anchor or a rule table, in place of a step that the model does badly.

### A helper tool for what the agent works out again and again

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

A tool that returns what the traces show the agent computing on each run.

### A check step with one retry

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

The agent checks its answer once, and tries again once if the check fails.

### Rules for the agent's loop

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Stop when the goal is met. Do not repeat a call that returned nothing.

### A request sent to its own instruction set by its type

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Route each type of request to its own instruction set, with a review of the tool calls.

### A short hand-written version of a promoted change

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Rewrite a promoted change in fewer words, and measure that it keeps its effect.

### A rule written as a trigger and an action

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Write each rule as "when X, do Y", in the narrowest place that can hold it.

### A verbosity option on a tool

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

The agent asks for a short or a full result.

### Examples for complex tool parameters

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

An example call in the description of a tool with a complex parameter.

### Readable names in place of opaque ids

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

A tool result gives names that the agent can read, not only ids.

### Namespaced tool names

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Tool names with a common prefix for each service or group.

### Fine-tuning as a late step

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: overview 7.6

For cost and latency, after the harness is stable. Knowledge does not go in the weights. The tuner
does not start a fine-tune: an existing one is a model candidate.

### On-device models

- Kind: playbook
- Where: knowledge
- Depends on: None
- Source: [design notes](projects/biological_bootloader/knowledge_system_design.md); owner, 2026-11-13

How to tune an agent whose model must run on end-user devices (laptops, phones): the small models
that fit, the limits of memory and speed, and the harness changes that help a small model most.

### On-prem open-weight models

- Kind: playbook
- Where: knowledge
- Depends on: None
- Source: [design notes](projects/biological_bootloader/knowledge_system_design.md); owner, 2026-11-13

How to tune an agent whose model must run on the owner's own servers: open-weight models only, often
with a limit on model size, and the harness changes that help when the best hosted models are out.

### The conventions that a model was trained on

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Tool names, formats and the vendor's own prompt guide for the model.

### Prompt lines left from an older model

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Remove the workarounds that an older model needed and the current one does not.

### A small knowledge source in the prompt

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Put a small knowledge source in the prompt, and remove the search tool.

### Playbook: after a change of model

- Kind: playbook
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Order: the same prompt, a new baseline, fix only what got worse, then remove old workarounds.

### Playbook: a first pass on a new agent

- Kind: playbook
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Order: stable failures, items that flip, stable passes, and the spread of cost and latency.

### Playbook: the order of work for a support agent

- Kind: playbook
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Order: the plan of a run for an agent that answers customers.

### Playbook: the order of work for an analyst over data

- Kind: playbook
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Order: the plan of a run for an agent that queries data and reports on it.

### Playbook: a rule ignored in place, followed from a tool result

- Kind: playbook
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Failure: the agent ignores a rule where it is, and follows it when it comes back in a tool result.

### Playbook: a classifier whose answers collapse to one class

- Kind: playbook
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Failure: a classifier gives one class to most inputs.

### Playbook: a change that fixes its target and breaks passing items

- Kind: playbook
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Failure: the change fixes the items it was built for, and items that passed now fail.

### Playbook: a defect outside the harness

- Kind: playbook
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Failure: a broken tool, stale data or a quota. No harness change fixes it.

### Playbook: an eval too small to show the effect

- Kind: playbook
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Eval: the eval has too few items to show a difference of the expected size.

### Playbook: a few items carry all the signal

- Kind: playbook
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Eval: every difference between arms comes from the same few items.

### Playbook: eval feedback with a score and no reason

- Kind: playbook
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Eval: the judge gives a score and does not say why.

### Playbook: a structural change against plain retries

- Kind: playbook
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Eval: measure a structural change against plain retries with the same number of calls.

### Playbook: outputs that break while the score rises

- Kind: playbook
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Eval: the score goes up, and the outputs get worse in a way that the eval does not see.

### Smell: a long list of "must" and "never" lines

- Kind: smell
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

A prompt that is mostly absolute rules, with no reasons and no conditions.

### Smell: one loop for requests of different kinds

- Kind: smell
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

One prompt and one tool set for requests that need different handling.

### Smell: a low cache hit rate

- Kind: smell
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

The traces show that little of the prompt is read from the cache. Important for a long prompt or a
multi-turn agent, where the cache saves the most; less critical for a single turn with a short
prompt. The diagnosis is the technique `Prompt cache usage`.

### Smell: evals that do not cover the product

- Kind: smell
- Where: knowledge
- Depends on: None
- Source: [design notes](projects/biological_bootloader/knowledge_system_design.md)

The evals test less than the agent does in production.

### Compaction and tool-result clearing in Kiln

- Kind: gap
- Where: kiln
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 8; overview 7.9

Not in Kiln. Compaction is planned. Tool-result clearing is not planned yet.

### Run config fields

- Kind: gap
- Where: kiln
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 15

No `max_tokens`, seed, `tool_choice`, `top_k` or penalties. Low value: settings are a late lever.

### OpenRouter routing controls

- Kind: gap
- Where: kiln
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 13

Kiln sets a fixed provider order and never sends `zdr` or `sort`. So a zero-data-retention
restriction cannot be enforced through OpenRouter. To confirm.

### The `llm` tool inside code tools

- Kind: gap
- Where: tuner
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 11

A code tool can probably call the built-in `llm` tool: a cheap reader inside a wrapper. Confirm,
then teach.

### Prompt generators and few-shot

- Kind: gap
- Where: tuner
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 12

Reachable and not taught. A few-shot generator can copy split items into the prompt. Confirm the
leak risk before anything teaches it.

### Multi-turn eval inputs

- Kind: gap
- Where: tuner
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 10

The v2 eval-input routes, multi-turn synthetic items and world resets are not reachable. Decide
what the tuner must read.

### A decision that turns on one or two items

- Kind: code
- Where: tuner
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

The loop does not make a decision that turns on one or two items.

### Guards before a measurement

- Kind: code
- Where: tuner
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

A limit on how much a change grows the harness, and no phrase copied from a trace or an eval.

### What the planner is given for each failed item

- Kind: code
- Where: tuner
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

The input, the output, what was expected, the step that lost it, a matching success, and a label
for the kind of failure. Never a score alone.

### A lasting record of each issue and each rejected idea

- Kind: code
- Where: tuner
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

An idea is dropped after three failures.

### Graded predictions for each experiment

- Kind: code
- Where: tuner
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

For each experiment: one failure pattern, one surface, the items it should fix and the items at
risk. The next wave grades the predictions.

### A fix passes the items that led to it

- Kind: code
- Where: tuner
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

A fix must pass the items that led to it, and break none that passed.

### A check of the spread of labels, for a classifier

- Kind: code
- Where: tuner
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Compare the spread of the predicted labels with the spread of the expected labels.

### A "fix elsewhere" result in the report

- Kind: code
- Where: tuner
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

The report names a defect outside the harness, which no experiment can fix.

### Rename the code field `technique` to `lever`

- Kind: code
- Where: tuner
- Depends on: None
- Source: functional spec 2

The field on each experiment names the kind of surface it changes. That is a lever kind.

### Conflicting requirements

- Kind: smell
- Where: knowledge
- Depends on: None
- Source: owner, 2026-11-13

Two texts that the agent reads ask for things that cannot both hold: the prompt, a tool description,
a skill, a tool's response. The agent then follows one at random. The remedy can need the owner.

### Questions to the owner before wave 1

- Kind: code
- Where: tuner
- Depends on: Conflicting requirements
- Source: owner, 2026-11-13

A new project. After recon and the design review, ask the owner what only the owner can decide, such
as which of two conflicting requirements wins. Each question offers options, plus "other" as text.

### A repeat measurement the planner can ask for

- Kind: code
- Where: tuner, knowledge
- Depends on: None
- Source: [repeat-measurement](projects/repeat-measurement/project_overview.md)

A planner item, or a flag on `remeasure`, that drives and judges an existing run config again on
train items. Today only `--noise-check` repeats a measurement, one time, before wave 1.

### A read fence on expected outputs for the design reviewer

- Kind: code
- Where: tuner
- Depends on: None
- Source: [smell_detection.md](projects/biological_bootloader/components/smell_detection.md), 4.3; phase 7 code review

A hard read fence in code, so that the design reviewer cannot read `traces/_expected/`. Today only
the text of its skill keeps it out.

### Feature engineering on the data that reaches the model

- Kind: technique
- Where: knowledge
- Depends on: Input transforms
- Source: owner, 2026-11-18; [feature-engineering](projects/feature-engineering/project_overview.md)

Fix the data before the model reads it: via the input transform, or a code wrapper on a tool result.
Drop and dedupe fields, compute `is_open`, cut long text, wrap user text in tags, rename a wrong
field. The owner's notes, the moves, the risks and the research to do are in the overview.

### Prompt cache usage

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: owner, 2026-11-19; see also `Smell: a low cache hit rate`

On a long multi-turn trace that can use it, check that the prompt cache is used. If not, find why:
caching is off; the account or provider does not have it; the agent edits its own history. That
edit saves a few tokens, but it breaks the cache and can cost 10x more.

### One smell issue across baselines

- Kind: code
- Where: tuner
- Depends on: None
- Source: [architecture](../docs/architecture.md), section 18.6

A smell that a new baseline still shows gets a second issue, and the earlier issue stays open.
Carry one issue across baselines, or close the earlier one with a link to the new one.
### Output modes that a model supports

- Kind: gap
- Where: kiln
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 6

Kiln's model file gives one default output mode for each model and provider, and no list of the
modes that the pair supports. With a list, code can refuse a mode before a run config is made.

### Request parameters in a Kiln trace

- Kind: gap
- Where: kiln
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 6

A Kiln trace holds the messages, not the request. So `json_schema` and `json_mode` leave no mark
in it, and code proves them only by Kiln's own record of the mode on the saved run.

### A holed win that comes back through a merge

- Kind: code
- Where: tuner
- Depends on: None
- Source: [PR 46](https://github.com/Kiln-AI/harness-tuner/pull/46), review finding M3

The limit on holed val gates counts by rc id, and a merge has a new id. A win with its own val hole
can come back in merge after merge, each one a full val pass. Fix: count a merge's holed gate
against each of its wins too, and stamp a win at the limit. The risk is a count on a good win.
