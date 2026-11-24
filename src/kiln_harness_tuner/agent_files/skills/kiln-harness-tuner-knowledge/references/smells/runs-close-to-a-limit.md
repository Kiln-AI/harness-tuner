---
title: Runs close to a limit
description: Some runs use most of a hard limit - Kiln's limit on the tool steps of one turn, or the model's context window. A run that reaches the limit fails and stores no result.
reviewed: 2026-11-25
applies_to: all
detected_by:
  - code
  - agent
detectors:
  - run_near_limit
---

# Runs close to a limit

## What it is

The design leaves little margin under a hard limit. Kiln stops a turn at a
fixed count of tool steps (a step is one model call that asks for one tool
call or more), and a model call fails when its input is larger
than the context window. A run that reaches a limit ends with an error.
Kiln stores no result for it, so the eval gives that item no score. When
some runs use most of a limit, the longer inputs of the same task can go
past it. This is a property of the design, whether an eval passes or fails.

Code finds the runs that came close: a stored train run that uses the
reference share of a limit, or more. Code cannot see a run that a limit
stopped, because that run has no stored trace. A reader finds those.

## How to recognise it

In the harness:

- a task that needs many steps for each request, with one tool call for
  each step and no parallel calls;
- tools that return large results, with a long prompt or many tool
  definitions.

In how the agent runs:

- the code detection in your input gives the largest count in one run, the
  limit, the reference, the train item, and how many stored runs reach the
  reference;
- train items with no stored result, when the measurement reported an
  error that names a limit: too many tool calls, too many turns, or an
  input longer than the context window.

## How to confirm it

- Open the trace that the detection names, and find what uses the limit:
  one call for each item of a list, the same call again and again, or a
  few very large results.
- Check if the runs near the limit are of one kind of request or of all
  kinds.
- The reference share is a default of the tuner, not a measured optimum.
  Read the detection's source before you state how sure you are.
- **Not this smell:** one run on an input much longer than the others,
  with all other runs far under the limit. Report it only with that said
  in the confidence.
- When the cause is one of these smells, report that smell too:
  [tool results flood the context](tool-results-flood-the-context.md),
  [repeated identical calls](repeated-identical-calls.md) or
  [fixed call chain](fixed-call-chain.md).

## What it blocks

A small change that is measured on the items near the limit. A run that
fails at the limit gives no score, so the set of scored items can differ
from one arm to the next.

## Remedies

- [Cap and preview wrapper](../techniques/cap-and-preview-wrapper.md),
  when large tool results fill the context window.
- [Roll-up tool](../techniques/roll-up-tool.md), when the tool calls are
  one call for each item of a list, or a fixed chain: one call does the
  work of many.
- [Model swap inside a class](../techniques/model-swap-inside-a-class.md),
  when the context window is the limit, the results are needed in full,
  and an allowed model of the same class has a larger window.
- [Fit the tool layer to the agent](../playbooks/fit-the-tool-layer-to-the-agent.md),
  when other smells of the tools are present too.
