---
title: Dealing with an eval near zero
description: The baseline passes almost no item of an eval. Read the raw outputs - a format or protocol failure can be fixed, a capability floor cannot, and a stronger model gives a signal.
reviewed: 2026-11-13
kind: eval
applies_to: all
---

# Dealing with an eval near zero

## Situation

The baseline passes almost no item of an eval, and the traces do not
suggest a fix. An arm on this eval has a small chance to show a change: a
fix that helps a few items reads as noise.

There are three causes, and each has its own path:

- **A format or protocol failure.** The content is often right, and every
  output fails on the same form: a missing block, broken JSON, a wrong
  tool protocol. This can be fixed.
- **A capability floor.** The model cannot do the task, on any prompt.
  The outputs fail in different ways, and the reasoning stops short or
  goes wrong. No change to the harness on this model gives a signal.
- **The eval is wrong.** The expected outputs cannot be met, or the judge
  fails right answers.

A model with a reasonable start gives the tuning a signal. As a guide, a
pass rate of about 20%, with room to rise, is a useful start.

## What to try, in order

1. Read the raw outputs of several failing items, before any arm. Name
   the cause of each.
2. A format failure:
   [the output contract is not met](output-contract-not-met.md). A
   malformed tool call:
   [malformed tool calls](malformed-tool-calls.md). A requirement that the
   prompt never states:
   [a requirement is missing from the prompt](requirement-missing-from-the-prompt.md).
3. An eval failure (expected outputs that cannot be met, a judge that
   fails right answers):
   [dealing with a saturated eval](dealing-with-a-saturated-eval.md), which
   gives how to read the fires.
4. A capability floor, when the model is a lever (`allowed_models` lists
   another model, and the guidance does not forbid a model change): a
   stronger model of the class, to get a signal, then lock it and tune the
   harness:
   [model swap inside a class](../techniques/model-swap-inside-a-class.md).
5. A capability floor where some items fail because the model does not
   think the problem through: one rung more thinking, on those items
   first:
   [thinking level ladder](../techniques/thinking-level-ladder.md).
6. When the model is not a lever and no step above applies, say so in
   `open_questions`: the eval cannot steer the run on this model.

## What not to do yet

- No text arms on an eval that shows no signal. A gain on one or two
  items is not a result.
- No settings scan to find a signal. A scan changes how the model reads
  the harness, and it does not lift a floor of capability on most items.
  Step 5 is one rung on the items that need more reasoning, not a scan.
- Do not tune on a very large model and expect the result to hold when
  the run goes back to a small one.

## Budget

On a small budget, steps 1 to 3 come first: they cost reads and one cheap
arm. A model change at step 4 needs a measurement of every eval, so it
fits a larger budget, or a run where this eval is the main goal.
