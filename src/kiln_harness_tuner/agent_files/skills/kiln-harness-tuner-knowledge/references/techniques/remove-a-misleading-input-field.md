---
title: Remove a misleading input field
description: An input transform that leaves out, or renames, one input field that leads the model to wrong answers - an earlier decision, a stale status, a field whose name does not say what it holds.
reviewed: 2026-11-25
status: active
levers:
  - input_transform
applies_to:
  - single_turn
cost: low
---

# Remove a misleading input field

## When to use

- The failing answers follow one field of the input, and that field is
  not what the task asks the model to decide from. Examples: an earlier
  decision or label that the model copies, a suggested category, a stale
  status.
- The model reads a field as its name says, and the name is wrong for
  what the field holds, for example a `closed_date` that is not the date
  the case closed.
- The failures have the same field value in common, and the passes do
  not.
- A prompt rule that tells the model to ignore the field was tried, and
  the failures stayed.

## When not to use

- **The field carries real signal for some classes.** Compare the field
  with the expected outputs on the train items. When it agrees with them
  most of the time, a removal can lose more than it gains.
- **The field is the subject of the task**, for example a task that
  reviews an earlier decision.
- **Many fields are noise, and none stands out.** Use
  [keep only the fields the task needs](keep-only-the-fields-the-task-needs.md).
- **You cannot name the field from the traces.** Then the cause is not
  known yet, and a removal is a guess.
- **The task is multi-turn.** The lever does not apply.

## Alternatives

- [Keep only the fields the task needs](keep-only-the-fields-the-task-needs.md),
  when the input has many fields that the task does not need.
- [Add computed input features](add-computed-input-features.md), when
  the field is correct and the model combines it wrongly with others.
- [Add the missing requirement](add-the-missing-requirement.md), when the
  field is needed and the model lacks the rule that says how to use it.

## How

The change is a new template on a new run config. See the lever
[input_transform](../levers/input_transform.md) for the template language,
the steps and the limits.

1. **Name the field and the mechanism** in the hypothesis: which field,
   and how it leads to the wrong answer.
2. **Choose the change**:
   - **Remove** the field, when the task does not need it.
   - **Rename** it, when the content is useful and the name is wrong.
     Print the value under a label that says what it holds.
   - **Relabel** it as context, when the task needs it and the model
     gives it too much weight. Say what it is and where it comes from,
     for example "decision of an earlier automatic check".
3. **Pass every other field as the parent does.** When the parent has no
   transform, print the other fields with their names and values, in a
   fixed order. Do not make other changes on this arm.
4. Remove a prompt line that asked the model to ignore the field, on the
   same arm.

## Check that it took effect

Drive two or three of the items that failed. In each trace, the first
user message does not have the field, or has it under the new label, and
every other field is present. Read the answers: they no longer repeat the
field's value where it was wrong.

## Budget

Low cost: one template and a measurement. A good first step when the
traces name the field, because the change is narrow and its result is
easy to read. Leave it out when no single field explains the failures.

## If it does not move the evals

It must move an eval. The change removes information, and it is made for
one failure. When the failures stay, the field was not the cause: put it
back. Check each class, not only the total score. A gain on one class and
a loss on another shows that the field carries signal for the second.

## Priors

None yet.

## Confidence

Medium that a misleading field can lower accuracy: public research shows
that irrelevant content in the input distracts models (Shi et al., "Large
Language Models Can Be Easily Distracted by Irrelevant Context", 2023).
Low for a given task: the field can also carry real
signal, and only a measurement by class separates the two. No measured
case is recorded here yet.
