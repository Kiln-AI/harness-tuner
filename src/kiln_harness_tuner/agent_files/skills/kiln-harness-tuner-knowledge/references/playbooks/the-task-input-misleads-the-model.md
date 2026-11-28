---
title: The task input misleads the model
description: The wrong answers come from how the task input reaches the model - a field it should not follow, a fact it must derive, a long text. Change the input with an input transform, the narrow change first.
reviewed: 2026-11-25
kind: failure
applies_to:
  - single_turn
---

# The task input misleads the model

## Situation

The prompt states the task correctly, and the model still answers wrongly
on a group of items. Read the failing traces next to the task input. The
cause is in the input when one of these holds:

- the answer follows a field that the task does not decide from: an
  earlier decision, a suggested label, a stale status;
- the model reads a field as its name says, and the name is wrong for
  the content;
- the items that fail are those where several fields must be combined,
  counted or compared, and the traces show the wrong derivation;
- the failures are more frequent on the items with a very long text
  field;
- the reasoning refers to ids, metadata or audit fields.

The same failure appears on many items, and the data that causes it is
the same kind on each. A failure of this kind stays after a prompt rule,
because the model still reads the same input on every item.

Two smells of the design describe the input itself:
[input with fields the task does not need](../smells/input-with-fields-the-task-does-not-need.md)
and [very long input field](../smells/very-long-input-field.md).

## What to try, in order

1. [Remove a misleading input field](../techniques/remove-a-misleading-input-field.md),
   when the traces name one field that the wrong answers follow. It is
   the narrowest change, so its result is the easiest to read.
2. [Add computed input features](../techniques/add-computed-input-features.md),
   when the failures are a derivation with a fixed rule: combined flags,
   a count, a sum, a comparison.
3. [Keep only the fields the task needs](../techniques/keep-only-the-fields-the-task-needs.md),
   when no single field stands out and the input is a wide record.
4. [Cut long input text](../techniques/cut-long-input-text.md), when the
   failures are on the long items and the decisive content has a known
   place.
5. When the derivation differs from item to item, code cannot compute
   it. Give the model more room to reason:
   [thinking level ladder](../techniques/thinking-level-ladder.md).

One change for each arm. Two changes to the template on one arm give a
result that names neither.

## What not to do yet

- Do not write a condition in the template for the items that failed. A
  rule that fits a few train inputs is a copy of the train set, and the
  generality check refuses it.
- Do not remove a field before you compare it with the expected outputs.
  A field that agrees with them most of the time carries signal.
- Do not add a prompt rule and a template change for one cause on one
  arm. When the template removes the cause, the rule is not needed.
- Do not use this playbook on a multi-turn task. The input transform
  does not apply there.

## Budget

The order does not change with the budget. Each step has a low cost: one
template and a measurement.
