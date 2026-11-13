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
eval item can change with time, and the expected output can go stale. A
write by one run can change what the next run reads. Two measurements of
the same agent then differ for a reason that is not the agent. This is a
property of the design, whether an eval passes or fails.

## How to recognise it

In the harness:

- tools that call a host name of a staging or production system;
- tools that create, change or delete records;
- eval items whose expected output names values that change, for example
  a balance, a count or a status.

In how the agent runs:

- tool results that differ for the same item between two runs;
- writes in a trace of an eval run.

## How to confirm it

- Read the tool definitions and the code of each code tool for the system
  they reach.
- Compare the tool results of one item in two stored runs, where the store
  has two.
- **Not this smell:** a tool that reads a fixed copy of the data, or a test
  system that is reset before each run.

## What it blocks

A decision on a small difference between two arms. The two were measured
at different times, and the data can have changed between them.

## Remedies

- [Dealing with a noisy eval](../playbooks/dealing-with-a-noisy-eval.md),
  when scores move between repeats with no change to the agent: repeat
  before you trust a small difference.
- [Guard tool calls at the boundary](../techniques/guard-tool-calls-at-the-boundary.md),
  when the agent can write to the system: a wrapper that refuses a write
  that the task does not allow.
