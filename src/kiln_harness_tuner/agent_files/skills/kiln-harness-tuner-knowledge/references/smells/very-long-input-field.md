---
title: Very long input field
description: One text field of the task input is very long on some items - a description, a thread, a log - so a few items take most of the input tokens and the decisive part is a small share.
reviewed: 2026-11-25
applies_to:
  - single_turn
detected_by:
  - agent
---

# Very long input field

## What it is

One text field of the task input is very long on some items: a
description, a message thread, a log, a page of text. The model reads the
whole field on each of those items. The part that decides the answer is a
small share of it. A few items then take most of the input tokens, and
the long text can hide the decisive part. This is a property of the
design, whether an eval passes or fails.

## How to recognise it

In the harness:

- the input schema has a free-text field with no length limit, and the
  run config has no input transform, or a transform that passes the field
  whole;
- among the train inputs, the longest items are many times the usual
  length, and one field holds the difference.

In how the agent runs:

- the input tokens of the longest items are many times the median;
- the failures are more frequent on the longest items;
- a run on a long item ends at a limit.

## How to confirm it

- Measure the length of each text field over the train inputs: the usual
  length and the longest.
- In a few long items, find where the content that decides the answer
  is: at the start, at the end, or anywhere.
- **Not this smell:** a long text that the task must read in full, for
  example a document to summarise. A long input on which the long items
  pass as often as the short ones, when the objective is quality only.

## What it blocks

Nothing.

## Remedies

- [Cut long input text](../techniques/cut-long-input-text.md), when the
  decisive content has a known place in the text, for example its start.
- [Keep only the fields the task needs](../techniques/keep-only-the-fields-the-task-needs.md),
  when the task does not need the long field at all.
- [Cheaper or faster](../playbooks/cheaper-or-faster.md), when the
  objective is cost or speed and the long items take most of the tokens.
