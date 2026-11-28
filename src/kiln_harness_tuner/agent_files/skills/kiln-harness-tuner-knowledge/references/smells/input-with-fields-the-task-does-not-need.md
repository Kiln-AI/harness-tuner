---
title: Input with fields the task does not need
description: The task input is a record made for another reader - ids, audit fields, metadata, one fact in several fields, a field with a wrong name - and the model reads all of it on every item.
reviewed: 2026-11-25
applies_to:
  - single_turn
detected_by:
  - agent
---

# Input with fields the task does not need

## What it is

The task input reaches the model as it was made for another reader: a
database row, an API response, a form export. It carries fields that the
task does not need, the same fact in more than one field, or a field whose
name does not say what it holds. The model reads all of it on every item.
Content that the task does not need can draw the model's attention away
from the fields that decide the answer, and each field costs tokens. This
is a property of the design, whether an eval passes or fails.

## How to recognise it

In the harness:

- the task has a structured input (an input schema), or a plaintext input
  that is JSON, and the run config has no input transform;
- the input schema or the train inputs have internal ids, timestamps,
  audit fields, metadata, or fields that are empty on most items;
- one fact is in several fields, for example a status, a flag and a date;
- a field holds an earlier decision, a label or a suggestion about the
  thing the task decides;
- a field name does not match its content.

In how the agent runs:

- the reasoning or the answer in the traces refers to a field that the
  task instruction does not need;
- the answer repeats the value of an earlier-decision field.

## How to confirm it

- Read the task instruction and a sample of train inputs. List the fields
  that a correct answer needs, and the fields that it does not.
- In the sampled traces, check if the model refers to a field of the
  second list.
- **Not this smell:** a small input in which every field matters. A field
  that looks like metadata and that the task instruction names. An input
  with many fields on a multi-turn task: the input transform does not
  apply there.

## What it blocks

Nothing.

## Remedies

- [Keep only the fields the task needs](../techniques/keep-only-the-fields-the-task-needs.md),
  when many fields are not needed, or one fact is in several fields.
- [Remove a misleading input field](../techniques/remove-a-misleading-input-field.md),
  when one field stands out: an earlier decision, a stale status, a wrong
  name.
- [The task input misleads the model](../playbooks/the-task-input-misleads-the-model.md),
  when failing items show that the model follows the input to a wrong
  answer.
