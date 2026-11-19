---
title: Tools on a live server
description: The agent's tools create, change or delete records on a staging or live system, so one run changes what the next reads. A read-only tool on live data (RAG, a web search) is not this smell.
reviewed: 2026-11-19
applies_to:
  - tools
detected_by:
  - agent
---

# Tools on a live server

## What it is

The agent's tools create, change or delete records on a staging or a live
system, not on a fixed copy of the data. A write by one run changes what
the next run reads. So the right answer to an eval item can change between
runs, and two measurements of the same agent differ for a reason that is
not the agent. A write can also do real harm on a live system. This is a
property of the design, whether an eval passes or fails.

This smell is about write tools (CRUD). A read-only tool on live data is
not this smell: a retrieval tool over a document store (RAG), or a web
search. Its results can change with time, which adds some noise. But it is
an expected part of many agents, and a fixed copy is often not realistic:
no one can build a fixed copy of the web. Do not report it as a defect.

## How to recognise it

In the harness:

- tools that create, change or delete records on a host of a production
  or shared system.

In how the agent runs:

- writes in a trace of an eval run, on a host that is live or shared;
- a tool result that shows an earlier run's write, for example a record
  that an earlier eval item created.

A write tool alone is not a sign. It needs evidence that the system is
live or shared: a production host name, or a result that shows an earlier
run's write. Different results from a read-only tool are not a sign.

## How to confirm it

- Read the tool definitions and the code of each code tool for the system
  they reach.
- Compare the tool results of one input in two stored runs, where the
  store has two. A difference counts only when a write explains it.
- **Not this smell:** a read-only tool, on a fixed copy or on live data
  (RAG, a web search); or a test system that is reset before each run.

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
  This also applies to the drift of a read-only tool on live data, which
  is not this smell.
- [Guard tool calls at the boundary](../techniques/guard-tool-calls-at-the-boundary.md),
  when the agent can write to the live system: a wrapper that refuses a
  write that the task does not allow. It limits the harm. It does not fix
  the drift.
