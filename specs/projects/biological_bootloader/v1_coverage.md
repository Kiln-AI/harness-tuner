---
status: draft
---

# V1 Coverage: Biological Bootloader

This phase is special to this project. It comes after the architecture and
before the implementation plan. It decides which documents the first version
of the knowledge system holds, and what goes in the backlog.

**The rule.** Every idea that this project has covered is captured. The only
decision for each idea is **V1** or **backlog**.

- **V1**: the tuner can act on it today (prompt, skills, tools, settings,
  model), or it is guidance that the planner can use directly.
- **Backlog**: it needs a lever the tuner cannot reach yet, or it needs code
  that this project does not write, or its evidence is thin. Each idea has
  its own entry, so the detail is kept until its turn.

**Sources, in order of weight.**

1. The owner's ideas: the [project overview](./project_overview.md) (the
   voice memo and the later additions) and the
   [design notes](./knowledge_system_design.md).
2. What the tuner tells its agents today: the lever ladder
   (`kiln-harness-tuner-batch-selection/references/lever-ladder.md`) and the
   planner's skill.
3. The [research in this repository](./research/harness-optimization/summary.md)
   and [kiln_gaps.md](./kiln_gaps.md).
4. The owner's separate research report. It is not in this repository. Its
   lessons are written here in our own words. No prior from it is written
   until the owner approves the line.

In the tables, "Overview" is a section of the project overview, "Ladder" is
a row or rule of the lever ladder, "Notes" is the design notes, and
"Research" is source 3 or 4. The ids are the file names.

## 1. Levers (6)

One for each thing the tuner changes today.

| Id | What it covers | Source |
|---|---|---|
| `prompt` | The system prompt, chain-of-thought instructions | Overview 6.1 |
| `skill` | Skill bundles: install, a new version of an inherited skill, attach and drop | Overview 6.2 |
| `new_tool` | Code tools: a new tool, a clone with a new description, a wrapper of any tool | Overview 6.3 |
| `tool_config` | Which tools the agent has: attach and drop | Overview 6.3 |
| `params` | The model's settings: thinking level, temperature, top-p | Overview 6.4 |
| `model` | The model and the provider. Existing fine-tunes are candidates. | Overview 6.4, 7.6 |

## 2. Smells: V1 (30)

| Id | Recognised by | Detected by | Source |
|---|---|---|---|
| **About the harness** | | | |
| `prompt-does-not-state-a-requirement` | The eval wants X, and the prompt never asks for X | agent | Overview 6.1 |
| `output-contract-not-met` | Wrong shape, no closing block, bad escaping | agent | Ladder row 1 |
| `answers-not-grounded` | The answer needs knowledge that is not in the context | agent | Ladder row 2 |
| `wrong-scope-or-refusal` | Off-topic answers, or refusals of valid requests | agent | Ladder row 3 |
| `context-the-agent-had-to-ask-for` | "If it had known that, it would have done the right thing", and a tool could have told it | agent | Overview 7.5 |
| `lost-in-long-instructions` | A long prompt or a one-file skill, and the agent misses a part | agent | Ladder row 8 |
| `very-long-prompt` | The system prompt is over the reference length | code | Overview 9 |
| `skill-resource-not-read` | The agent never loads a skill file, or guesses a path, or loads it and does not follow it | agent | Ladder row 5, research |
| `skill-too-small-to-earn-its-place` | A skill with a few words and no other file: its description costs more than it saves | agent | Overview 9 |
| `very-long-skill-description` | A skill description is over the reference length | code | Architecture |
| `faulty-inherited-skill` | An attached skill is wrong, stale, or holds data shaped like test cases | agent | Ladder row 10 |
| `harness-copies-eval-content` | The prompt, a skill or a tool holds text copied from eval items or from a judge | code (the existing baseline audit), agent | The planner's skill, research |
| **About the tools** | | | |
| `tool-results-flood-the-context` | A very large tool result, or one call for each item | agent | Overview 6.3 and 9, ladder row 6 |
| `fixed-call-chain` | The agent calls the same tools in the same order each time, and passes an id from one to the next | agent | Overview 6.3 |
| `general-query-tool-without-a-guide` | A SQL, GraphQL or general API tool, and the agent must learn the schema by trial | agent | Overview 6.3 and 10 |
| `failure-at-a-tool-call` | The failing message comes before or with a tool call | agent | Ladder row 4 |
| `malformed-tool-calls` | Wrong case, wrong id form, a misspelled tool name | agent | Ladder row 7 |
| `too-many-narrow-tools` | The agent picks the wrong tool, or breaks a rule of the product | agent | Ladder row 11, overview 6.3 |
| `tool-search` | The agent must guess that a tool exists and search for it | agent | Overview 6.3, notes |
| `lookup-tool-over-a-long-document` | A document behind a paged or search tool, and the agent picks the wrong part | agent | Ladder row 9 |
| `very-long-tool-description` | A tool description is over the reference length | code | Overview 9 |
| **About the evals and the run** | | | |
| `expected-answers-are-wrong` | A failing item reads as a correct answer, or the task is contradictory | agent | Research |
| `unreliable-judge` | A judge's score disagrees with its own reasoning, or it accepts a near miss | agent | Ladder row 12, research |
| `saturated-eval` | The baseline is at the ceiling: the train rows cannot show a win | code, agent | Ladder row 12, notes |
| `eval-near-zero` | Almost every item fails, and the traces suggest no fix | agent | Overview 6.4, notes |
| `noisy-eval` | The same config gives a different result on a second run | code | Notes, the noise check |
| `evals-against-a-live-server` | The evals read or write a staging or live system, so the right answer changes with time | agent | Overview 7.7 |
| `train-wins-rejected-at-val` | Fixes that win on train keep losing at the gate | agent | Ladder rule 2, notes |
| `fix-breaks-passing-items` | A change fixes its target and breaks items that passed | agent | Research |
| `defect-outside-the-harness` | A broken tool, stale data in a source, a quota or a timeout. Changes to the wording stay flat. | agent | Research |

## 3. Techniques: V1 (28)

| Id | Levers | Cost | Source |
|---|---|---|---|
| **Instructions** | | | |
| `add-the-missing-requirement` | prompt | low | Overview 6.1 |
| `state-the-output-contract` | prompt | low | Ladder row 1 |
| `answer-only-from-the-source` | prompt | low | Ladder row 2 |
| `decline-rules-paragraph` | prompt | low | Ladder row 3 |
| `policy-as-step-by-step-procedures` | prompt, skill | low | Research |
| `structure-a-long-prompt` | prompt | low | Ladder row 14 |
| `one-general-rule-per-edit` | prompt, skill | low | Research, the planner's skill |
| `long-prompt-into-a-skill` | skill, prompt | medium | Overview 6.2, ladder row 8 |
| `skill-into-the-prompt` | prompt, skill | low | Overview 6.2 and 9 |
| `path-index-in-the-skill-description` | skill | low | Ladder row 5 |
| `rewrite-an-inherited-skill` | skill | medium | Ladder row 10 |
| `prune-one-group-at-a-time` | prompt, skill, tool_config | low | Research, overview 6.3 |
| **Knowledge** | | | |
| `attach-the-knowledge-source` | tool_config, skill | medium | Ladder row 2 |
| `document-into-a-skill` | skill, tool_config | medium | Ladder row 9 |
| **Tools** | | | |
| `cap-and-preview-wrapper` | new_tool | medium | Overview 6.3, ladder row 6 |
| `roll-up-tool` | new_tool, prompt | medium | Overview 6.3 and 7.5 |
| `guide-skill-for-a-tool-set` | skill | medium | Overview 6.3 and 10 |
| `new-tool-description` | new_tool | low | Ladder row 4 |
| `directive-in-the-tool-response` | new_tool | medium | Ladder row 4 |
| `normalize-inputs-wrapper` | new_tool | medium | Ladder row 7 |
| `errors-the-agent-can-act-on` | new_tool | medium | Overview 6.3 |
| `guard-tool-calls-at-the-boundary` | new_tool, prompt | medium | Research |
| `remove-or-scope-tools` | tool_config | low | Overview 6.3 |
| `consolidate-tools` | new_tool, tool_config | high | Ladder row 11, notes |
| **Model and settings** | | | |
| `thinking-level-ladder` | params | low | Ladder row 13 |
| `model-swap-inside-a-class` | model | high | Overview 6.4 |
| `faster-provider-for-the-same-model` | model | low | Overview 6.4, notes |
| `decision-model-for-a-classifier` | model | medium | Overview 7.4 |

Notes:

- `roll-up-tool` covers the two cases of the overview: a fixed chain of
  calls folded into one tool, and one tool that loads the context the agent
  needs at the start, with a prompt line to call it first.
- `cap-and-preview-wrapper` covers a result trimmed to what this agent
  needs, a preview with a follow-up query, and an error when a result would
  be too large.
- `guide-skill-for-a-tool-set` has sub-files for the owner's tips on a query
  tool: sample queries at each level, the schema as leaf files, a section on
  timestamps.
- `long-prompt-into-a-skill` and `skill-into-the-prompt` name each other in
  `Alternatives`, with the conditions: the size of the model, how often a run
  needs the content, and the prompt cache.
- `model-swap-inside-a-class` says that every promoted change is measured
  again after a swap.
- `decision-model-for-a-classifier` applies to tasks with a classifier
  shape only. That is what Kiln supports today.
- The evidence column of the lever ladder becomes priors with the source
  "own experiment". That text is already public in this repository.

## 4. Playbooks: V1 (12)

| Id | Kind | What it holds | Source |
|---|---|---|---|
| `know-the-agent-before-you-plan` | Order | Classify the agent first: single-turn or multi-turn, structured output or free text, classifier shape, tools. A multi-turn eval is driven by a synthetic user. Then read the smells that apply. | Overview 4 and 7.8 |
| `default-order-of-work` | Order | Step 0: check that the eval measures the right thing. Lock a model that has a reasonable start on the task. Fix what blocks other work. Then send each failure to the surface that can fix it. The settings and the model scan come last. One change in one arm. | Overview 6.4 and 10, ladder, research |
| `spending-the-budget` | Order | The budget is known at the start. What a small budget leaves out and what a large budget opens. Fewer arms, each with enough items to pass its own test. The smallest gain that a given number of items can show. | Overview 5 and 5.1, research |
| `check-the-eval-first` | Situation | Before any change to the agent: read the failing items for wrong expected answers, test the judge, look for copied eval content, check that the eval can show a win. | Research |
| `fit-the-tool-layer-to-the-agent` | Design issue | The tools are the company's software APIs. Wrap them, do not rewrite them. Which wrapper for which symptom. The prompt and the guide skill change with the tools. | Overview 6.3 |
| `change-the-surface-not-the-words` | Response to a smell | When a failure is mechanical, or two text arms failed on one issue: change what the model touches before the words it reads. A policy failure is the exception: procedure text comes first. | Ladder, research |
| `dealing-with-a-saturated-eval` | Response to a smell | Measure the issue on the baseline, judge the fires, then grow or change the eval. No fix arm. | Ladder row 12, notes |
| `dealing-with-an-eval-near-zero` | Response to a smell | Read the raw outputs. A format or protocol failure can be fixed. A capability floor cannot: use a stronger model to get a signal. A reasonable start is about 20%, with room to rise. | Overview 6.4, notes, research |
| `dealing-with-a-noisy-eval` | Response to a smell | Measure the baseline again on the same day. Repeat before you trust a small difference. Do not let a decision turn on one or two items. | Notes, research |
| `dealing-with-train-wins-rejected-at-val` | Response to a smell | Look for copied eval content and wrong expected answers. Stop writing rules for small groups of failures. Fewer and larger arms. Then change the surface. | Notes, ladder rule 2, research |
| `cheaper-or-faster` | User goal | Another provider of the same model, less thinking, a shorter prompt, fewer output tokens, then a smaller model with the harness adapted to it. Apply the cost goal after quality is stable. | Notes, the planner's skill |
| `working-inside-restrictions` | Situation | A stated restriction is a hard limit on the plan: providers, model origin, data retention, tools that must not change. What to do when the best lever is not allowed. | Overview 8 |

`tool-less-generation-task` (ladder row 14) is not a separate playbook. Its
steps go into the remedies of `lost-in-long-instructions` and into
`structure-a-long-prompt`.

## 5. Where each idea of the owner goes

One row for each idea of the project overview and the design notes.
"V1" names the document. "Backlog" names the entry of section 7.

| Idea | Overview | Goes to |
|---|---|---|
| Experiments decide; guidance is never a "must" | 3 | The content rules; `SKILL.md` |
| A tie can be settled from knowledge | 3 | The content rules. Backlog: Promotion on a tie |
| Cost of learning against cost of maintenance | 3 | The content rules; the maintenance skill |
| Classify the agent first: turns, output, others | 4 | V1 `know-the-agent-before-you-plan`; the task shapes |
| Targeted experiments, many cheap experiments | 5 | V1 `spending-the-budget` |
| Being right often is the largest saving | 5 | V1 `spending-the-budget` |
| Plan with the budget in view; a large budget opens options | 5.1 | V1 `spending-the-budget` |
| The eval wants X and the prompt never asks for it | 6.1 | V1 `prompt-does-not-state-a-requirement`, `add-the-missing-requirement` |
| The prompt is tuned with every other variable, not alone | 6.1 | V1 `default-order-of-work` |
| Progressive disclosure: a skill for each thing the agent can do | 6.2 | V1 `long-prompt-into-a-skill` |
| A long prompt into a skill is almost always worth an experiment | 6.2 | V1 `long-prompt-into-a-skill` |
| The reverse: a cached long prompt can beat a skill walk | 6.2 | V1 `skill-into-the-prompt` |
| It depends on the size of the model | 6.2 | V1, both techniques; `default-order-of-work` |
| The best harness includes the model | 6.2, 6.4 | V1 `default-order-of-work`, `model-swap-inside-a-class` |
| A good API can be a poor agent tool | 6.3 | V1 `fit-the-tool-layer-to-the-agent` |
| A list result with every field | 6.3 | V1 `tool-results-flood-the-context`, `cap-and-preview-wrapper` |
| A chain of calls that passes an id along | 6.3 | V1 `fixed-call-chain`, `roll-up-tool` |
| A general SQL or GraphQL tool with a schema to learn | 6.3 | V1 `general-query-tool-without-a-guide`, `guide-skill-for-a-tool-set` |
| Wrap, do not rewrite: code tools as a layer | 6.3 | V1 `fit-the-tool-layer-to-the-agent`; the lever `new_tool` |
| A wrapper that refuses a result that is too large | 6.3 | V1 `cap-and-preview-wrapper` |
| Two styles of agent; why the tuner exists | 6.3 | `SKILL.md` and the planner's entry file |
| The harness is a complete object | 6.3 | V1: a technique is one set of changes; `fit-the-tool-layer-to-the-agent` |
| Tool search is a smell | 6.3 | V1 `tool-search` |
| Tool removal | 6.3 | V1 `remove-or-scope-tools`, `prune-one-group-at-a-time` |
| Error-message design | 6.3 | V1 `errors-the-agent-can-act-on` |
| Settings: a review of what Kiln supports | 6.4 | V1 the lever `params`. Backlog: structured-output mode |
| A setting that one model needs (an illustration) | 6.4 | Not kept: the loop learns it from one call |
| The model is the largest single lever | 6.4 | V1 `model-swap-inside-a-class`, `default-order-of-work` |
| A model guide and a provider guide | 6.4 | Backlog: Model and provider information |
| Very fast providers | 6.4, notes | V1 `faster-provider-for-the-same-model` |
| Model and harness are a pair | 6.4 | V1 `default-order-of-work` |
| Lock a model of the right class with a reasonable start | 6.4 | V1 `default-order-of-work`, `dealing-with-an-eval-near-zero` |
| A scan of models before the lock, on a large budget only | 6.4 | V1 `spending-the-budget` |
| Come back to the model after the harness | 6.4 | V1 `model-swap-inside-a-class` |
| A scan of the settings last, if at all | 6.4 | V1 `spending-the-budget`, `thinking-level-ladder` |
| RAG or skill; when each wins | 7.1 | Backlog: RAG |
| RAG: documents and extraction first; extract once | 7.1 | Backlog: RAG |
| RAG: test that it helps before you tune it | 7.1 | Backlog: RAG |
| Sub-agent for deeper thinking | 7.2, notes | Backlog: Sub-agents |
| Sub-agent as a cheap reader | 7.2, notes | Backlog: Sub-agents |
| Sub-agent with its own tools and skills | Notes | Backlog: Sub-agents |
| Sub-agents are not a universal fix; one turn only | 7.2 | Backlog: Sub-agents |
| A sub-agent can be tuned with its own evals | 7.2 | Backlog: Sub-agents |
| Input transforms: fewer fields, cut text, added features, a field removed | 7.3 | Backlog: Input transforms |
| Decision models for every classification task | 7.4 | V1 `decision-model-for-a-classifier`. Backlog: other uses |
| Context loading | 7.5 | V1 `context-the-agent-had-to-ask-for`, `roll-up-tool` |
| Fine-tuning is late; knowledge does not go in weights | 7.6 | V1 a line in `model-swap-inside-a-class`. Backlog: Fine-tuning |
| Stateful agents need an environment; stale evals | 7.7 | V1 `evals-against-a-live-server`. Backlog: OpenEnv worlds |
| No evals: send the user to the eval builder | 7.8 | The tuner does this today. V1 `know-the-agent-before-you-plan` |
| Synthetic users drive multi-turn evals | 7.8 | V1 `know-the-agent-before-you-plan` |
| Compaction; a switch in mid-conversation; guardrails | 7.9 | Backlog: Not in Kiln yet |
| Restrictions | 8 | V1 `working-inside-restrictions`. Backlog: Collect restrictions |
| Anything that can be code must be code | 9 | The smell detectors |
| A very large tool result | 9 | V1 smell. Backlog: its detector |
| A very long tool description; a very long prompt | 9 | V1 smells with detectors |
| A skill of a few words | 9 | V1 `skill-too-small-to-earn-its-place`. Backlog: its detector |
| Thousands of skills | 9 | Backlog: Too many skills |
| A problem that blocks everything else | 9, 10 | V1 "What it blocks" in each smell; `default-order-of-work` |
| A library of wins from real cases | 2, 10 | Priors; the maintenance skill |
| Tips for a query tool: timestamps, sample queries | 10 | V1 `guide-skill-for-a-tool-set`, in sub-files |
| The stack rank | 10 | V1 `default-order-of-work` |
| Playbook: much cheaper | Notes | V1 `cheaper-or-faster` |
| Playbook: open models on-prem | Notes | Backlog |
| Playbook: an eval near zero | Notes | V1 |
| Playbook: a saturated eval | Notes | V1 |
| Playbook: train wins that validation rejects | Notes | V1 |
| Playbook: variance too high | Notes | V1 `dealing-with-a-noisy-eval` |
| Smell: old models | Notes | Backlog |
| Smell: evals do not cover the product | Notes | Backlog |
| All tools into one general API tool, with a skill | Notes | Backlog |
| Technique: on-device models; on-prem models | Notes | Backlog |
| What to do when a change does not move the evals | Notes | V1: a section of every technique. Backlog: Promotion on a tie |
| The casebook | Notes | Outside this project |

## 6. Where each part of the lever ladder goes

| Part of the ladder | Goes to |
|---|---|
| Rows 1 to 12 of the table | The smells and techniques above. "Try first" and "Then" become the smell's remedies, in order. "Do not" becomes "When not to use" of the techniques it names, and a line in the smell. |
| Row 13 (provider parameters) | `thinking-level-ladder` |
| Row 14 (a tool-less task with a long prompt) | `lost-in-long-instructions` and `structure-a-long-prompt` |
| The evidence column | Priors |
| The evidence rule at the top | Replaced by the content rules of the functional spec, section 6 |
| "The rule above the table" | `change-the-surface-not-the-words` |
| "Everything the baseline attaches is in the action space" | The levers `skill` and `new_tool` |
| Rule 1, name the decision point | Stays in the planner's and the analyzer's skills: it is process. `failure-at-a-tool-call` links to it. |
| Rule 2, two failed text arms | `change-the-surface-not-the-words`. The count from `lever_attempts` stays in the planner's skill. |
| Rule 3, a skill bundle is measured against every eval | "Check that it took effect" of `long-prompt-into-a-skill` |
| "What bake it into code means" | The lever `new_tool`, which links to the wrapper templates |
| "Cost order" | The `cost` of each technique, and `spending-the-budget` |

The implementation phase that moves the ladder ends with a check of this
table against the removed file, line by line.

## 7. The backlog

The first contents of `specs/backlog.md`. The order under "Ranked" is a
first proposal. The owners rank.

### Ranked

| Title | Kind | Where | What it holds |
|---|---|---|---|
| Input transforms | gap | tuner | The lever, and its techniques: keep only the fields the task needs, cut long text, add computed features, remove a field that misleads |
| Sub-agents | gap | tuner | The lever, and its techniques: a smarter model for deep thinking; a specialist with its own tools; a cheap reader for large sources. A fixed form for what comes back. The limits: one turn, and no fix for a strong single agent. Tune a critical sub-agent with its own evals, then the main agent. |
| Model and provider information | code | tuner, kiln | [The project](../model-provider-info/project_overview.md): the model guide, the provider guide, the "old model" smell and its detector |
| Promotion on a tie | code | tuner | [The project](../tie-promotion/project_overview.md) |
| Single-turn prompt caching in Kiln | gap | kiln | The cost that Kiln measures can be wrong. It also decides if `skill-into-the-prompt` can save money. |
| The task shape for loop agents | gap | tuner | `turn_mode` and the model capability flags |
| RAG | gap | tuner | The lever. RAG or skill, and when each wins. Documents and extraction first; extract once and do not tune it. Test that RAG helps before the chunker and the search type. Reranking. Size in tokens, not in documents. |
| Structured-output mode | gap | tuner | The lever, and its screen |
| Detectors from the trace counts | detector | tuner | A very large tool result; repeated calls and loops; a run that ends at a limit; unused tools and skills; a skill of a few words |
| An eval that is too small to show the effect | smell | knowledge, tuner | The smell, with a detector from the item counts |
| A change that makes the outputs break while the score rises | smell | knowledge | Read the raw outputs of a winner |
| Detectors on the process of a run | detector | tuner | Train wins rejected at val; repeated failed arms; items that flip |
| OpenEnv worlds | gap | tuner, kiln | Read and change a world. An environment for a stateful agent. |
| Collect restrictions at the start | code | tuner | "Ask the user the five best questions" |

### Unranked

**Techniques.**

- All tools into one general API tool, with the skill that describes it.
- Tool-result clearing, separate from compaction.
- A stronger model as an adviser to a cheap one.
- A cheap model first, and a stronger one when it is not sure.
- Examples chosen for each input; examples from the agent's own successes.
- A check of each example against its input, before it is added.
- A short index of the reference material that is always in context.
- One prompt for each type of input; one call for each field.
- A step outside the model that replaces a model step: a calculator, a
  date anchor, a rule table.
- A helper tool for what the agent works out again and again.
- A check step with one retry.
- Rules for the agent's loop: stop when the goal is met, do not repeat a
  call that returned nothing.
- A request sent to its own instruction set by its type, with a review of
  tool calls.
- A thinking level set by the type of input.
- A short hand-written version of a promoted change.
- A rule written as a trigger and an action, in the narrowest place that
  can hold it.
- A verbosity option on a tool; examples for complex parameters; readable
  names in place of opaque ids; namespaced tool names.
- A probe for room to improve, before an arm on the wording.
- Fine-tuning as a late step, for cost and latency.
- On-device models; on-prem open-weight models.
- The conventions that a model was trained on: tool names, formats, the
  vendor's own prompt guide.
- Prompt lines left from an older model.
- A small knowledge source put in the prompt, with the search tool removed.

**Playbooks.**

- Open models on-prem.
- After a change of model: the same prompt, a new baseline, fix only what
  got worse, then remove old workarounds.
- A structural change measured against plain retries with the same number
  of calls.
- A first pass on a new agent: stable failures, items that flip, stable
  passes, and the cost and latency spread.
- A support agent: the order of work. An analyst over data: the order of
  work.

**Smells.**

- Too many tools, or tools that overlap (a count).
- Very many skills.
- An old model.
- Evals that do not cover the product.
- A low cache hit rate.
- A rule in the wrong place: the agent ignores it where it is, and follows
  it when it comes back in a tool result.
- Eval feedback with a score and no reason.
- A long list of "must" and "never" lines.
- A classifier whose answers collapse to one class.
- One loop for requests of different kinds.
- A few items carry all the signal.

**Levers and gaps.** The rest of [kiln_gaps.md](./kiln_gaps.md):
compaction, a switch of model or thinking level in mid-conversation, inline
guardrails, run config fields, OpenRouter routing controls, the `llm` tool
in code tools, prompt generators, multi-turn eval inputs, code files in
skills, other decision-model providers, decision models for uses other than
a classifier.

**Code: ideas for the loop.** These change how the tuner works. They are not
knowledge documents.

- A check of the eval and the judge as the first class of experiment.
- A probe for room to improve before an arm on the wording.
- The baseline measured again in each wave, paired and on the same day.
- A gate that uses support across repeats, not a mean.
- A decision that turns on one or two items is not made.
- Guards before a measurement: a limit on how much a change grows the
  harness, and no phrase copied from a trace or an eval.
- What the planner is given for each failed item: the input, the output,
  what was expected, the step that lost it, a matching success, and a label
  for the kind of failure. Never a score alone.
- A lasting record of each issue and each rejected idea, and an idea
  dropped after three failures.
- For each experiment: one failure pattern, one surface, the items it
  should fix and the items at risk. The predictions are graded in the next
  wave.
- A fix must pass the items that led to it, and break none that passed.
- A check of the spread of labels, for a classifier.
- A "fix elsewhere" result in the report, for a defect outside the harness.
- A baseline review that can close a smell issue which a new baseline no
  longer shows.
- The rename of the code field `technique` to `lever`.

## 8. Priors

**Approved by the owner on 2026-11-12.** Each line below is from a public
source. Before a line is written into a document, the implementation opens
the source and checks the numbers. A line whose numbers do not match the
source is corrected or dropped.

| # | Document | Line | Source |
|---|---|---|---|
| 1 | `remove-or-scope-tools` | From Vercel's d0 post, an analytics agent's 17 specialised tools were replaced by bash over a documented semantic layer: success went from 4 of 5 to 5 of 5 queries, 3.5x faster, with 37% fewer tokens. The author says it worked only because the semantic layer was already good documentation. | https://vercel.com/blog/we-removed-80-percent-of-our-agents-tools, 2025-12-22 |
| 2 | `remove-or-scope-tools` | From GitHub's Copilot post, cutting the default toolset from 40 to 13 with routing improved success by 2 to 5 points on two coding benchmarks. | https://github.blog/ai-and-ml/github-copilot/how-were-making-github-copilot-smarter-with-fewer-tools/, 2025-11-19 |
| 3 | `prune-one-group-at-a-time` | From Ramp's benchmarking post, an accounting agent's skills were loaded one at a time from a bare base: only two were positive on average, and cutting one skill by 64% kept its effect. | https://builders.ramp.com/post/stack-benchmarking, 2026-06-03 |
| 4 | `policy-as-step-by-step-procedures` | From Quesma's post, rewriting a policy as step-by-step procedures for a small model on a telecom support benchmark raised the pass rate from 0.55 to 0.675. | https://quesma.com/blog/tau2-benchmark-improving-results-smaller-models, 2025-09-12 |
| 5 | `attach-the-knowledge-source` | From LinkedIn's text-to-SQL paper, example queries took an analytics agent's correct-or-nearly-correct answers from 24% to 49% on 133 questions, and adding domain-knowledge records lowered it to 42%. | https://arxiv.org/html/2507.14372, 2025-07-18 |
| 6 | `default-order-of-work` | From Nubank's support-agent paper, an agent that started with a knowledge tool and a human-transfer tool gained a routine from the operating procedures, domain tools and escalation, then tool hierarchies and conciseness budgets. | https://arxiv.org/abs/2606.08867, 2026-06-07 |
| 7 | `unreliable-judge` | From Shopify's Sidekick post, calibrating LLM judges to human labels moved agreement (Cohen's kappa) from 0.02 to 0.61, against a human baseline of 0.69. | https://shopify.engineering/building-production-ready-agentic-systems, 2025-08-26 |
| 8 | `unreliable-judge` | From Hex's data-agent post, on an eval with a near-miss trap (2.03% against 2.04%), the judge accepted the difference 35% of the time. | https://hex.tech/blog/evaluate-data-agents/, 2026-05-22 |
| 9 | `expected-answers-are-wrong` | From Anthropic's agent-evals post, fixing rigid grading, ambiguous specs and a too-constrained scaffold took one model on an agent benchmark from 42% to 95%. | https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents, 2026-01-09 |
| 10 | `thinking-level-ladder` | From LangChain's harness post, running the highest reasoning level everywhere scored 53.9% against 63.6% at "high", because of agent timeouts. | https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering, 2026-02-17 |
| 11 | `model-swap-inside-a-class` | From Hex's Quick Edits post, a model with a pass rate about 1 point lower was adopted because it made 20% fewer wrong edits at half the cost. | https://hex.tech/blog/we-used-evals-to-improve-ai-feature, 2026-10-08 |
| 12 | `skill-resource-not-read` | From Vercel's AGENTS.md post, a skill was never invoked in 56% of cases and the pass rate equalled the no-docs baseline (53%). An explicit trigger instruction raised it to 79%, and a compact index always in context reached 100%. | https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals, 2026-01-27 |
| 13 | `skill-resource-not-read` | From a 2026 analysis of harness updating, one 32B model loaded its skills at a rate of 0.251 against 0.957 for a frontier model, and another model loaded at 0.961 but followed at only 0.350. | https://arxiv.org/html/2605.30621, 2026-05-28 |
| 14 | `change-the-surface-not-the-words` | From a 2026 study of a customer-service tool agent, failures were clustered and each cluster sent to the prompt, a tool-boundary guard or both. Removing that routing cut the held-out gain from 14.9 to 5.4 points. | https://arxiv.org/html/2609.05736, 2026-09-09 |
| 15 | `default-order-of-work` | From a 2026 study of a tool-using assistant, scheduling code and loop changes before text-only changes gave a test pass rate of 60.7 against 54.8 without that order. | https://arxiv.org/html/2608.23041, 2026-08-24 |
| 16 | `state-the-output-contract` | From a study of six prompt optimisers with 20 training items, 49% of 72 runs scored below the unoptimised prompt, and every method won only on the one task where the output needed a format the model could produce but did not by default. | https://arxiv.org/html/2604.14585v1, 2026-04-16 |
| 17 | `state-the-output-contract` | From Dropbox's relevance-judge post, a small open model returned malformed JSON in 358 of 856 responses, and prompt optimisation cut that to 9 of 856. | https://dropbox.tech/machine-learning/optimizing-dropbox-dash-relevance-judge-with-dspy, 2026-03-17 |
| 18 | `dealing-with-train-wins-rejected-at-val` | From a 2026 study of a research agent, the gap between validation and held-out results ranged from 0.034 to 0.160 across three seeds, and reducing per-criterion feedback to one score made held-out transfer negative. | https://arxiv.org/abs/2610.12601, 2026-10-08 |
| 19 | `model-swap-inside-a-class` | From a vendor study of eight environments, the best variant changed model in six of eight tasks. | https://www.tensorzero.com/blog/automated-ai-engineer/, 2026-03-23 |
| 20 | `dealing-with-a-noisy-eval` | From a 2026 study of skill optimisation, 16% to 53% of tasks on a banking support benchmark gave mixed results over three tries. | https://arxiv.org/html/2610.25931, 2026-10-23 |

**Not used.** Results from the owner's own runs that the separate research
mentions are kept out for now, by the owner's decision of 2026-11-12.
