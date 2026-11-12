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
   condition.
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
None yet.
<!-- END GENERATED: playbooks-order -->

## Goals

<!-- BEGIN GENERATED: playbooks-goal -->
None yet.
<!-- END GENERATED: playbooks-goal -->

## Failure patterns

A class of failure seen in the traces of eval items.

<!-- BEGIN GENERATED: playbooks-failure -->
| Playbook | Applies to | Description |
|---|---|---|
| [output-contract-not-met](playbooks/output-contract-not-met.md) | all | The output breaks a format rule that nothing enforces - a closing block, a layout of free text, an escaping rule, or a rule about the content of a field. |
<!-- END GENERATED: playbooks-failure -->

## Eval problems

The measurement is the problem.

<!-- BEGIN GENERATED: playbooks-eval -->
None yet.
<!-- END GENERATED: playbooks-eval -->

## Design

The response to a smell that needs more than one technique.

<!-- BEGIN GENERATED: playbooks-design -->
None yet.
<!-- END GENERATED: playbooks-design -->
