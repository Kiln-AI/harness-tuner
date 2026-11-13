---
title: Failure at a tool call
description: The failing message comes before or with a tool call - a preamble, or a narration that rides the call. A new tool description, then a directive in the tool's response.
reviewed: 2026-11-12
kind: failure
applies_to:
  - tools
---

# Failure at a tool call

## Situation

The failure is at the moment the agent calls a tool. Find the failing
message in the trace, and where it is compared with the tool calls:

- **before** a tool call: a preamble or a promise that the task does not
  want, written just before the call;
- **with** a tool call: a narration that rides in the same message as the
  call;
- **after** a tool's result: the reply ignores or restates the result in a
  way that the task does not want.

Naming the decision point is process. The planner's skill
(`kiln-harness-tuner-batch-selection`, Step 1) and the analyzer's skill
(`kiln-harness-tuner-experiment-analysis`, the `findings` field) give it:
the analyzer writes before, with or after which tool, and the planner
chooses the text that the model reads at that moment. Before or with a
call, that text is the tool's description. After a call, it is the tool's
response, or the prompt.

## What to try, in order

1. [New tool description](../techniques/new-tool-description.md), when the
   failing message is before or with the call. The description is the one
   text that the model reads at the moment it decides to call the tool. A
   code tool gets a new description on a clone. Any other tool gets one
   through a wrapper.
2. [Directive in the tool response](../techniques/directive-in-the-tool-response.md),
   when the failing message is after the call, or step 1 did not hold. A
   wrapper whose response carries a short directive for the reply, or the
   derived facts that the reply must state.

## What not to do yet

- No prompt rule, no change to a skill body and no change to a skill
  description for a failure at the call. The model reads those texts long
  before the moment it fails, and the tool's description is the text in
  front of it then.
- Do not plan a third text change on the same issue after two failed:
  [change the surface, not the words](change-the-surface-not-the-words.md).

## Budget

The order does not change with the budget. Step 1 has a low cost, and
step 2 a medium cost.
