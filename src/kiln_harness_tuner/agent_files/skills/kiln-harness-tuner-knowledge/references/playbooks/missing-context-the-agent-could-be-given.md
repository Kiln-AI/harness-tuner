---
title: Missing context the agent could be given
description: The agent fails because it did not know a fact that a tool could give it - if it had known that, it would have done the right thing. One tool that loads it, and a prompt line to call it first.
reviewed: 2026-11-12
kind: failure
applies_to:
  - tools
---

# Missing context the agent could be given

## Situation

The agent answers from what it has, and what it has is not enough. A
tool could give it the fact, and it did not think to call that tool. Read
a failing trace and ask: "If it had known that, would it have done the
right thing?" Signs:

- the answer is right for the request as stated, and wrong for the state
  of the account, the order or the case;
- the trace has no call to the tool that holds that state, or the call
  comes after the answer was decided;
- in a passing item of the same kind, the agent called that tool, or the
  request itself stated the fact.

Example: a support agent answers a billing question and does not check
for a pending cancellation. When it is told that the account will close
in three days, it answers correctly.

The facts that the agent needs are often the same kind on every item.
List them from the failures: what the agent must be given, not left to
request.

## What to try, in order

1. [Roll-up tool](../techniques/roll-up-tool.md), when the facts come from
   several tools, or from one tool that the agent does not think to call.
   One tool that loads the context the agent needs at the start, in a
   compact form, and a prompt line that says to call it first.
2. [New tool description](../techniques/new-tool-description.md), when one
   existing tool already gives the fact in a compact form. A new
   description that says when to call it, on a clone or a wrapper.
3. [Cap and preview wrapper](../techniques/cap-and-preview-wrapper.md),
   when the context tool returns much more than the facts the failures
   show. A context loader that returns everything floods the context.

## What not to do yet

- Do not give the agent many tools that each return mostly noise. The
  context loader returns only the facts that the failures show the agent
  needs.
- Do not put the facts of train items into the prompt. They change from
  item to item, and the generality check refuses them.
- Do not rely on a prompt rule alone ("check the account first") when no
  single tool gives the fact. The agent still has to find and chain the
  calls.

## Budget

On a small budget, step 2 comes first when one tool already gives the
fact: it has a low cost. Step 1 has a medium cost.
