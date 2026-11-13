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

### Model and provider information

- Kind: code
- Where: tuner, kiln
- Depends on: None
- Source: [model-provider-info](projects/model-provider-info/project_overview.md)

Done in the project model-provider-info: the model catalogue, the gateway operations
`list_models`, `get_model_details` and `list_providers`, and the "old model" smell and detector.

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

### Structured-output mode

- Kind: gap
- Where: tuner
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 6

The lever document and the screen exist. Missing: code keeps the parent's mode on a model swap
(no switch to Kiln's default); no signal shows that a mode reached the call; the model list does
not say which modes a model supports; the setup probe of valid JSON skips some modes.

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

- Kind: gap
- Where: tuner, kiln
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 9; overview 7.7

Operations to read and change a world: an environment for a stateful agent. Refresh the vendored
API reference, which lacks the `/worlds` routes.

### Collect restrictions at the start

- Kind: code
- Where: tuner
- Depends on: None
- Source: overview 8

"Ask the user the five best questions": the providers, the model origin, the data retention, the
tools that must not change.

## Unranked

### All tools in one general API tool, with a skill

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [design notes](projects/biological_bootloader/knowledge_system_design.md)

Replace many narrow tools with one general API tool, and add the skill that describes the API.

### Tool-result clearing

- Kind: technique
- Where: knowledge, kiln
- Depends on: Compaction and tool-result clearing in Kiln
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Clear old tool results from the context. It is separate from compaction.

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

### Examples chosen for each input

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Few-shot examples selected for the input at hand, not one fixed set.

### Examples from the agent's own successes

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Few-shot examples taken from train items that the agent got right.

### A check of each example against its input

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Check that each example agrees with its input before it is added to the prompt.

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

### One call for each field

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Fill each field of a structured output with its own call.

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

### A thinking level set by the type of input

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

More thinking for the hard types of input, less for the easy ones.

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

### A probe for room to improve

- Kind: technique
- Where: knowledge
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

Before an arm on the wording, probe whether the failing items can pass at all.

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

### Smell: very many skills

- Kind: smell
- Where: knowledge
- Depends on: None
- Source: overview 9

The agent has so many skills that their descriptions crowd its context.

### Smell: an old model

- Kind: smell
- Where: knowledge
- Depends on: Model and provider information
- Source: [design notes](projects/biological_bootloader/knowledge_system_design.md); functional spec 12.3; [model-provider-info](projects/model-provider-info/functional_spec.md), section 8

Done in the project model-provider-info: the smell document `old-model` and the code detector
`old_model`, which reads the release date from the model catalogue.

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

The traces show that little of the prompt is read from the cache.

### Smell: evals that do not cover the product

- Kind: smell
- Where: knowledge
- Depends on: None
- Source: [design notes](projects/biological_bootloader/knowledge_system_design.md)

The evals test less than the agent does in production.

### Detectors from the harness counts

- Kind: detector
- Where: tuner
- Depends on: None
- Source: functional spec 12.3

Too many skills; too many tools.

### Compaction and tool-result clearing in Kiln

- Kind: gap
- Where: kiln
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 8; overview 7.9

Not in Kiln. Compaction is planned. Tool-result clearing is not planned yet.

### A switch of model or thinking level in mid-conversation

- Kind: gap
- Where: kiln
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 14; overview 7.9

Not in Kiln. A sub-agent can approximate the advisor pattern today.

### Inline guardrails

- Kind: gap
- Where: kiln
- Depends on: None
- Source: overview 7.9

Not in Kiln.

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

### Code files in skills

- Kind: gap
- Where: kiln
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 17

Kiln skills read text only and cannot run code. Code goes in code tools. The knowledge states it.

### Other decision-model providers

- Kind: gap
- Where: kiln
- Depends on: None
- Source: [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 16

Only one decision-model provider exists. This matters when a restriction excludes it.

### Decision models for uses other than a classifier

- Kind: gap
- Where: kiln
- Depends on: None
- Source: overview 7.4; [kiln_gaps.md](projects/biological_bootloader/kiln_gaps.md), gap 7

Kiln supports decision models for tasks with a classifier shape only.

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

### A baseline review that closes a smell issue

- Kind: code
- Where: tuner
- Depends on: None
- Source: [V1 coverage](projects/biological_bootloader/v1_coverage.md), section 7

The baseline reviewer closes a smell issue that a new baseline no longer shows.

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
