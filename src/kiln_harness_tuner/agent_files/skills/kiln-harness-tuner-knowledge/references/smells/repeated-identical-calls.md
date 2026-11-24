---
title: Repeated identical calls
description: The agent makes the same tool call, with the same arguments, many times in one run. A repeat returns nothing new, and it costs one more model call.
reviewed: 2026-11-25
applies_to:
  - tools
detected_by:
  - code
  - agent
detectors:
  - repeated_call
---

# Repeated identical calls

## What it is

In one run, the agent calls one tool with the same arguments again and
again. When the tool reads fixed data, each repeat returns what the agent
already has. The run pays for one more model call and one more result in
the context, and it can go on until a limit stops it. The cause is in the
design: the tool's result does not tell the agent what to do next, or the
agent cannot see that it already has the answer. This is a property of the
design, whether an eval passes or fails.

Code finds the sure form: the same call made the reference count of times
or more in one stored train run. A reader finds the other forms: fewer
repeats, or calls that differ only in a detail that does not matter.

## How to recognise it

In the harness:

- a tool that returns an error or an empty result with no word on how to
  correct the call;
- a tool whose result is so long that one fact is hard to find in it.

In how the agent runs:

- the code detection in your input names the tool, the largest count of
  the same call in one run, the train item and the reference;
- a cycle: two or three calls that repeat in the same order with the same
  arguments;
- the same call again after an error, with nothing changed.

## How to confirm it

- Open the trace that the detection names. Compare the results of the
  repeated call. The same result each time confirms the smell.
- Read what comes before each repeat: an error, an empty result, a long
  result, or nothing.
- The reference is the count at which one coding agent stops a run as a
  loop. It is a product default, not a measured optimum. Read the
  detection's source before you state how sure you are.
- **Not this smell:** a call that reads a state that changes, between two
  writes or while a job runs. The arguments are the same and the results
  differ.
- **Not this smell:** one call for each item of a list. The arguments
  differ. See
  [tool results flood the context](tool-results-flood-the-context.md).

## What it blocks

A change for cost or latency on the same tool. While the agent repeats
the call, the number of calls in a run changes from run to run, and the
effect of a small change is hard to see.

## Remedies

- [Errors the agent can act on](../techniques/errors-the-agent-can-act-on.md),
  first, when the repeats follow an error: an error that says what was
  wrong and what to do next.
- [Directive in the tool response](../techniques/directive-in-the-tool-response.md),
  when the repeats follow an empty or partial result that does not say how
  to change the call.
- [Cap and preview wrapper](../techniques/cap-and-preview-wrapper.md),
  when the repeated result is very large and the agent reads it again to
  find one fact.
- [Roll-up tool](../techniques/roll-up-tool.md), when the agent reads the
  same context again at each step: one call that gives it at the start.
