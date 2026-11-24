---
title: Tool results flood the context
description: A tool returns a very large result - a list with every field, a full schema, many pages - or the agent makes one call for each item of a list. Code measures the largest results.
reviewed: 2026-11-25
applies_to:
  - tools
detected_by:
  - code
  - agent
detectors:
  - large_tool_result
---

# Tool results flood the context

## What it is

A tool returns much more than the agent uses: a list with every field of
each row, a long text field in each record, a full schema, many pages. Or
the agent calls one tool once for each item of a list. Each result stays in
the context for the rest of the run. So the agent pays for each token, the
earlier context gets a smaller share of attention, and a run can end at a
limit. This is a property of the design, whether an eval passes or fails.

Code finds the sure form: a result in a stored train run that is longer
than the reference. A reader finds the other forms at any size: a result
much larger than what the agent uses, and one call for each item.

## How to recognise it

In the harness:

- a tool that lists records with no limit, no page size or no choice of
  fields;
- a general query tool that can return any number of rows.

In how the agent runs:

- the code detection in your input names the tool, the length of its
  largest result, the train item and the reference;
- the largest tool results in the traces are many times the length of the
  final answer;
- the agent reads a large result to take one small fact from it, for
  example one id for the next call;
- many calls of one tool in a row, one for each item;
- a run that ends at a limit after a large result.

## How to confirm it

- In the sampled traces, find the largest tool results, and check which
  fields of them the agent uses next.
- Count the calls of one tool in one run.
- The reference is a limit of one agent product on a tool result, not a
  measured optimum. Read the detection's source before you state how sure
  you are.
- **Not this smell:** a large result that the answer needs in full, for
  example a document that the agent summarises.

## What it blocks

A small fix elsewhere, and a prompt rule on how to use the tool. While the
result floods the context, the effect of a small change is hard to see, and
a rule does not change what the tool returns.

## Remedies

- [Cap and preview wrapper](../techniques/cap-and-preview-wrapper.md),
  first, when one result is very large: the fields this agent uses, a
  capped preview with a follow-up query, or an error that asks for a
  narrower query.
- [Roll-up tool](../techniques/roll-up-tool.md), when the agent makes one
  call for each item: one call for the list, with an error for each item.
- [Fit the tool layer to the agent](../playbooks/fit-the-tool-layer-to-the-agent.md),
  when other smells of the tools are present too.
