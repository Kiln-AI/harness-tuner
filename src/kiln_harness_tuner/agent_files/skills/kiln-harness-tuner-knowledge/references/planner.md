# The planner's entry file

Use this file to choose what to try. The knowledge suggests. An experiment
decides, and a result measured on this task replaces what a document says.

## How to use the knowledge

1. **Before you plan, read each playbook of the kind `order`** in the table
   "Order of work" below. They say how to plan a run: know the agent first,
   order the work, and spend the budget.
2. **Read the smells of the present baseline.** The detections in your input
   and the smell issues in memory each name a smell id. Open that smell's
   document from the table "Smells" and read its remedies. A smell that
   blocks other work comes first.
3. **Take each failure and each eval problem to its playbook.** Find the
   playbook whose situation fits the issue, in the tables of the kinds
   `failure` and `eval`. Its steps are in order, and each step states its
   condition. Take the first step whose condition holds. Move to the next
   step when the step before it was measured, or when its condition does
   not hold.
4. **Reach a technique through a playbook or a smell.** A choice by goal or
   by situation is better than a choice by name. The technique document
   says how to build it, how to check that it took effect, and what to do
   when it does not move the evals.

The `technique` field of an experiment is the id of a lever document where
one exists: `prompt`, `skill`, `tool_config`, `new_tool`, `params`, `model`.

Each table gives the task shapes a document applies to. `all` means that the
shape does not matter.

## Smells

<!-- BEGIN GENERATED: smells -->
| Smell | Applies to | Description |
|---|---|---|
| [tool-search](smells/tool-search.md) | tools | The agent must guess that a tool exists, decide to search for it, and find the right search terms, before it can call it. |
<!-- END GENERATED: smells -->

## Order of work

<!-- BEGIN GENERATED: playbooks-order -->
| Playbook | Applies to | Description |
|---|---|---|
| [know-the-agent-before-you-plan](playbooks/know-the-agent-before-you-plan.md) | all | Classify the agent before the first plan - its turns, its output, a classifier shape, its tools, skills and model - because each document says which shapes it fits. |
| [ordering-work](playbooks/ordering-work.md) | all | How to order the work of a run - the priority comes from the smells and the failures of this agent, not from a fixed order of levers. All of it is guidance. |
| [spending-the-budget](playbooks/spending-the-budget.md) | all | How to spend a fixed budget - fewer experiments, each large enough to pass its own test, the cheap techniques first on a small budget, and the scans only on a large one. |
<!-- END GENERATED: playbooks-order -->

## Goals

<!-- BEGIN GENERATED: playbooks-goal -->
| Playbook | Applies to | Description |
|---|---|---|
| [cheaper-or-faster](playbooks/cheaper-or-faster.md) | all | The user wants the agent to cost less or answer sooner, with quality held - each step is marked cheaper, faster or both, because the two goals do not always agree. |
| [working-inside-restrictions](playbooks/working-inside-restrictions.md) | all | The user states a restriction - providers, the origin of a model, data retention, a tool that must not change. It is a hard limit on the plan. What to do when the best lever is not allowed. |
<!-- END GENERATED: playbooks-goal -->

## Failure patterns

A class of failure seen in the traces of eval items.

<!-- BEGIN GENERATED: playbooks-failure -->
| Playbook | Applies to | Description |
|---|---|---|
| [answers-not-grounded](playbooks/answers-not-grounded.md) | all | The answers need knowledge that is not in the agent's context - attach the source, then add scope discipline. A source behind a lookup tool can move into a skill. |
| [change-the-surface-not-the-words](playbooks/change-the-surface-not-the-words.md) | all | Two text changes failed on one issue, or the failure is mechanical. Change what the model touches before the words it reads. A policy failure is the exception - procedure text comes first. |
| [failure-at-a-tool-call](playbooks/failure-at-a-tool-call.md) | tools | The failing message comes before or with a tool call - a preamble, or a narration that rides the call. A new tool description, then a directive in the tool's response. |
| [lost-in-long-instructions](playbooks/lost-in-long-instructions.md) | all | The agent misses a part of a long prompt or of a one-file skill. Progressive disclosure when the model reads skills, a prompt structure when it does not. A probe first. |
| [malformed-tool-calls](playbooks/malformed-tool-calls.md) | tools | The agent calls a tool with the wrong case, the wrong form of an id, a body on a GET, or a misspelled tool name. A wrapper that normalises, errors the agent can act on, a guard at the tool. |
| [missing-context-the-agent-could-be-given](playbooks/missing-context-the-agent-could-be-given.md) | tools | The agent fails because it did not know a fact that a tool could give it - if it had known that, it would have done the right thing. One tool that loads it, and a prompt line to call it first. |
| [output-contract-not-met](playbooks/output-contract-not-met.md) | all | The output breaks a format rule that nothing enforces - a closing block, a layout of free text, an escaping rule, or a rule about the content of a field. |
| [requirement-missing-from-the-prompt](playbooks/requirement-missing-from-the-prompt.md) | all | The eval checks for something that neither the prompt nor the task instruction asks for. State the requirement in new words - do not copy the eval. |
| [skill-is-not-read](playbooks/skill-is-not-read.md) | all | The agent never loads a skill file, guesses a path, or loads the skill and does not follow it. The path index in the description, then a prompt rule. |
| [wrong-scope-or-refusal](playbooks/wrong-scope-or-refusal.md) | all | The agent answers requests outside its scope, or refuses valid ones. A paragraph of decline rules, then a rebalance for the refusals that remain. |
| [wrong-tool-chosen](playbooks/wrong-tool-chosen.md) | tools | The agent picks the wrong tool, or breaks a rule that the product enforces. Consolidate the tools with the policy inside, or remove and scope them, then better descriptions. |
<!-- END GENERATED: playbooks-failure -->

## Eval problems

The measurement is the problem.

<!-- BEGIN GENERATED: playbooks-eval -->
| Playbook | Applies to | Description |
|---|---|---|
| [dealing-with-a-noisy-eval](playbooks/dealing-with-a-noisy-eval.md) | all | A score moves between repeats with no change to the agent. Repeat before you trust a small difference, and do not let a decision turn on one or two items. |
| [dealing-with-a-saturated-eval](playbooks/dealing-with-a-saturated-eval.md) | all | The baseline is at the ceiling of an eval, or the judge's fires are false. Measure the issue on the baseline, judge the fires, then grow or change the eval. No fix arm. |
| [dealing-with-an-eval-near-zero](playbooks/dealing-with-an-eval-near-zero.md) | all | The baseline passes almost no item of an eval. Read the raw outputs - a format or protocol failure can be fixed, a capability floor cannot, and a stronger model gives a signal. |
| [dealing-with-train-wins-rejected-at-val](playbooks/dealing-with-train-wins-rejected-at-val.md) | all | Arms win on train, and validation rejects them again and again. Look for copied content, stop rules for small groups of failures, run fewer and larger experiments, then change the surface. |
<!-- END GENERATED: playbooks-eval -->

## Design

The response to a smell that needs more than one technique.

<!-- BEGIN GENERATED: playbooks-design -->
| Playbook | Applies to | Description |
|---|---|---|
| [fit-the-tool-layer-to-the-agent](playbooks/fit-the-tool-layer-to-the-agent.md) | tools | The agent's tools are the company's software APIs, and a good API can be a poor agent tool. Wrap them, do not rewrite them - which wrapper for which problem, with the prompt and the guide skill. |
<!-- END GENERATED: playbooks-design -->
