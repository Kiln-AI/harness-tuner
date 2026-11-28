---
title: Keep only the fields the task needs
description: An input transform that passes the model the fields that decide the answer, one field for each fact, and leaves out ids, audit data, metadata and empty fields.
reviewed: 2026-11-25
status: active
levers:
  - input_transform
applies_to:
  - single_turn
cost: low
---

# Keep only the fields the task needs

## When to use

- The task input is a record made for another reader: a database row, an
  API response, a form export. It has internal ids, timestamps, audit
  fields, metadata, or fields that are empty on most items.
- The same fact is in more than one field, for example a status, a flag
  and a date that all say "closed". The model can read three signals
  where there is one.
- In failing traces the reasoning or the answer relies on a field that
  the task does not need.
- Most of the input tokens are fields that no correct answer uses, and
  the objective is cost or speed.

## When not to use

- **The input is plain text, or a few fields that all matter.** There is
  nothing to leave out.
- **You cannot say which fields decide the answer.** Read the task
  instruction and a sample of train items first. A field that looks like
  metadata can be the one a class depends on.
- **One field misleads the model, and the others are harmless.** Remove
  that one field and keep the rest:
  [remove a misleading input field](remove-a-misleading-input-field.md).
  A narrow change gives a result that is easier to read.
- **The task is multi-turn.** The lever does not apply.
- **Do not try a prompt line alone** ("ignore the audit fields") when the
  fields can be removed. The line asks the model to ignore what it still
  reads on every item.

## Alternatives

- [Remove a misleading input field](remove-a-misleading-input-field.md),
  when one field causes the failures.
- [Cut long input text](cut-long-input-text.md), when one long text field
  holds most of the tokens and the other fields are few.
- [Cap and preview wrapper](cap-and-preview-wrapper.md), when the data
  that floods the context comes from a tool result, not from the task
  input.

## How

The change is a new template on a new run config. See the lever
[input_transform](../levers/input_transform.md) for the template language,
the steps and the limits.

1. **List the fields** of the input, from the input schema and from a
   sample of train items.
2. **Mark each field**: needed for the answer, the same fact as another
   field, or not needed. Use the task instruction and the expected outputs
   to decide. When you are not sure about a field, keep it.
3. **Write a template that names each kept field**, one line for each,
   with a label that says what the field holds. Keep the same order on
   every item.
4. **Keep one field for each fact.** When three fields state one fact,
   keep the one that is most direct.
5. **Guard each optional field**, so that an item without it still
   renders.
6. Leave the prompt as it is, unless it names a field that the template
   removed. Then change that prompt text on the same arm.

## Check that it took effect

Drive two or three train items. In each trace, the first user message
holds the kept fields and none of the removed ones. Drive one item that
lacks an optional field: it returns an output, not an error.

## Budget

Low cost: one template, a compile check and a measurement. A good early
step when the input is a wide record, because every later experiment then
runs on a smaller and clearer input. Leave it out when the input is small.

## If it does not move the evals

It depends. Keep it when the input tokens fall and no eval goes down: each
call costs less, and the input is easier to reason about in later
experiments. Check each class or kind of item, not only the total score. A
loss on one class shows that a removed field decided that class: put the
field back.

## Priors

None yet.

## Confidence

Medium that irrelevant content in the input can lower accuracy: public
research shows it for added irrelevant sentences (Shi et al., "Large
Language Models Can Be Easily Distracted by Irrelevant Context", 2023) and
for distractors in long contexts (Chroma, "Context Rot", 2025). Low for
the size of the gain on a given agent: we found no controlled study of
field removal for a structured input, and a strong model can ignore the
extra fields. Treat each use as an experiment.
