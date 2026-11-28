---
title: A defect outside the harness
description: A tool is broken, its data is stale, or a quota stops it. No harness change fixes that. Tell it from a harness problem, do what the harness still can, then record it for the owner.
reviewed: 2026-11-25
kind: failure
applies_to:
  - tools
---

# A defect outside the harness

## Situation

The failing message comes after a tool's result, and the result is the
problem. What the agent did with it is not. In the trace, read the tool
call and the tool result that come just before the failing message:

- **a broken tool**: the call fits the tool's schema, and the result is an
  error of the server (a 5xx status, a timeout, a stack trace, a refused
  login that the agent cannot repair), or a result that is wrong for the
  call (another record, a field that is empty on every item);
- **stale data**: the result is well formed and the agent uses it as it
  is, and the answer is still wrong, because the source is old or
  incomplete. The expected output needs a value that the source does not
  hold;
- **a quota**: the tool answers with a rate limit or a quota error (HTTP
  429, "quota exceeded"), often on the later items of a run, or when many
  items run at the same time.

In each case the fact that the answer needs never reached the agent. No
prompt, model, skill or tool description can give it that fact.

These look like such a defect and are not:

- the agent's call is wrong: its form, its arguments, or the tool that it
  chose. Step 1 sends those items to their own playbook;
- the tool gave an error that the agent could use, and the agent hid it,
  retried the same call, or invented the data. That is a harness problem,
  and step 4 treats it;
- the expected output is what is old or wrong. Step 3 sends it to the eval
  playbook;
- the tool reads data that people change, so that two runs differ. That
  is the smell
  [tools on a live server](../smells/tools-on-a-live-server.md), and its
  remedies apply;
- an error of the model provider (its rate limit, its quota). Code handles
  it, and it is not about the task's tools.

## What to try, in order

1. Check that the call is valid. Read the tool's definition, and compare
   the name and the arguments of the call with it. When the form is wrong:
   [malformed tool calls](malformed-tool-calls.md). When the tool is the
   wrong one: [wrong tool chosen](wrong-tool-chosen.md). Those items leave
   this playbook.
2. Check that the failure is not from one run. Find the same call on the
   same item in another run config (the baseline and a candidate), or in
   another stored run of the same run config. A defect shows on each. A
   failure seen one time, which does not come back, gets nothing until it
   recurs: [dealing with a noisy eval](dealing-with-a-noisy-eval.md).
3. Check that the eval is right. Read the expected output of the item.
   When the tool's result is correct and the expected output is the old
   one: [dealing with a wrong eval](dealing-with-a-wrong-eval.md).
4. Do what the harness can still do, when the traces show that the agent
   handles the failure badly: it repeats the same call, it hides the
   failure, or it invents the data.
   [Errors the agent can act on](../techniques/errors-the-agent-can-act-on.md):
   a wrapper that says what failed, and if a new call can work. Use it when
   an eval can pass an answer that reports the failure, and target only
   the items that such an answer passes. The wrapper does not repair the
   tool, so it does not make an item pass that needs the missing fact.
5. Record the defect, with the gateway operation `record_external_defect`
   (the gateway skill gives its fields). Name the tool or the source, the
   train items whose traces show it, what the traces show, and how steps
   1 to 3 came out. In `blocked_item_ids`, list the items that cannot pass
   while the defect is there, and leave out the items that step 4 can
   still serve. Record a defect one time: a later report of the same
   source is added to the same record. The report names each record, so
   that the owner can fix the tool, the data or the quota. Code keeps the
   blocked items out of the targets of later experiments.

## What not to do yet

- No fix arm on an item that cannot pass until the defect is fixed. The
  spend is lost, and the arm ends NOT LANDED on that item.
- Do not tune around the defect: no prompt line that gives the value that
  the tool did not return, no example that holds the answer, no rule that
  names the broken case. Such an arm fits the train items, and the
  generality check refuses it.
- Do not record a defect from one trace. One failed call can be a
  transient error. Wait for the same failure on a second run config, a
  second run, or more than one item.
- Do not record an eval problem or a smell as a defect. A wrong expected
  output goes to the eval's owner through `open_questions`, and a smell
  has its own issue.
- Do not plan a model swap for it. Another model reads the same result.

## Budget

The order does not change with the budget. Steps 1, 2, 3 and 5 are reads
and one write, and they cost no measurement. Step 4 has a medium cost.
