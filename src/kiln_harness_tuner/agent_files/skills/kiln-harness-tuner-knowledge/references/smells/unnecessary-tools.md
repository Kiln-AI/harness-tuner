---
title: Unnecessary tools
description: Tools that overlap - two tools used for the same kind of request - or tools never called in the traces. Code lists the tools that no stored train run calls.
reviewed: 2026-11-25
applies_to:
  - tools
detected_by:
  - code
  - agent
detectors:
  - unused_tool
---

# Unnecessary tools

## What it is

Some attached tools are not needed. Two tools do the same job, or a tool is
never called on this task. A good tool set has one tool for each job, and
each tool in it is needed. Each extra tool is one more definition in the
context, and each tool that looks like another is one more way to choose
wrong. This is a property of the design, whether an eval passes or fails.

## How to recognise it

In the harness:

- two tools with names or descriptions that say the same job;
- a tool for work that this task does not do.

In how the agent runs:

- for requests of the same kind, the agent calls one tool of a pair on
  some items and the other tool on others;
- the code detection in your input names the attached tools that no
  stored train run calls, and the number of train items that those runs
  cover;
- with no detection: an attached tool that is never called in the sampled
  traces.

## How to confirm it

- The trace evidence is required. A sign in the harness alone is not
  enough: two tools that look alike can each serve a different request.
- For an overlap, name the pair, the kind of request, and the items where
  the agent calls each tool.
- For a tool that is never called, with no detection, read more train
  traces, of every eval, before you report it. The sample is small, and a
  tool that one class of item needs is not unnecessary.
- A detection counts every stored train run. It is silent under the
  reference count of train items. It gives a bound on how often the tool
  is called, not a proof that no request needs it: check what the tool is
  for, and read the detection's source.
- **Not this smell:** many tools that each do a different job that the
  task needs. A count above the reference is a second smell:
  [too many tools](too-many-tools.md).

## What it blocks

A new description to tell two overlapping tools apart. When one of the two
can go, the description is not needed.

## Remedies

- [Remove or scope tools](../techniques/remove-or-scope-tools.md), first:
  drop the tools that are never called, and one tool of an overlapping
  pair, each on its own arm.
- [Consolidate tools](../techniques/consolidate-tools.md), when the
  overlapping tools each do part of the job well, so neither can go alone.
- [New tool description](../techniques/new-tool-description.md), when both
  tools of a pair are needed, and their descriptions do not say which is
  for which request.
- [Fit the tool layer to the agent](../playbooks/fit-the-tool-layer-to-the-agent.md),
  when other smells of the tools are present too.
- [Wrong tool chosen](../playbooks/wrong-tool-chosen.md), when the traces
  show the agent choose the wrong tool.
