---
title: Tools on a live server
description: The agent's tools read or write a staging or live system, so the right answer to an eval item can change with time, and a measurement cannot be repeated.
reviewed: 2026-11-13
applies_to:
  - tools
detected_by:
  - agent
---

# Tools on a live server

## What it is

The agent's tools read or write a staging or a live system, not a fixed
copy of the data. The data changes between runs. So the right answer to an
eval item can change with time. A write by one run can change what the
next run reads. Two measurements of the same agent then differ for a
reason that is not the agent. This is a property of the design, whether an
eval passes or fails.

## How to recognise it

In the harness:

- tools that call a host name of a production or shared system;
- tools that create, change or delete records on such a host.

In how the agent runs:

- different tool results for the same input, between two stored runs;
- writes in a trace of an eval run, on a host that is live or shared.

A write tool alone is not a sign. It needs evidence that the system is
live or shared: a production host name, or different results for the
same input.

## How to confirm it

- Read the tool definitions and the code of each code tool for the system
  they reach.
- Compare the tool results of one input in two stored runs, where the
  store has two.
- **Not this smell:** a tool that reads a fixed copy of the data, or a test
  system that is reset before each run.

## What it blocks

A decision on a small difference between two arms. The two were measured
at different times, and the data can have changed between them.

## Remedies

No arm can fix the drift. Only the owner can give a fixed copy of the data
or a system that is reset before each run.

- [Dealing with a wrong eval](../playbooks/dealing-with-a-wrong-eval.md),
  first: report the issue to the owner in `open_questions` and in the
  report, as for an eval-data failure. Name the tools, the host and the
  evidence.
- [Dealing with a noisy eval](../playbooks/dealing-with-a-noisy-eval.md),
  when scores move between repeats with no change to the agent: repeat
  before you trust a small difference, until the owner fixes the data.
- [Guard tool calls at the boundary](../techniques/guard-tool-calls-at-the-boundary.md),
  when the agent can write to the live system: a wrapper that refuses a
  write that the task does not allow. It limits the harm. It does not fix
  the drift.
