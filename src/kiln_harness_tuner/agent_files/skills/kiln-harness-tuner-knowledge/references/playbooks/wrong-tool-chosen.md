---
title: The wrong tool is chosen
description: The agent picks the wrong tool, or breaks a rule that the product enforces. Consolidate the tools with the policy inside, or remove and scope them, then better descriptions.
reviewed: 2026-11-13
kind: failure
applies_to:
  - tools
---

# The wrong tool is chosen

## Situation

The agent calls a tool that does not fit the request, or calls the right
tool in a way that the product does not allow. Look at the tool calls of
the failing items:

- the agent calls one of two tools that do almost the same thing, and the
  choice changes from item to item;
- it calls a narrow tool many times where one general tool would do;
- it calls a general tool with no rules, and breaks a rule of the product:
  a permission, a limit, a default;
- the attached tools are many, narrow, or overlap: the smell
  [too many narrow tools](../smells/too-many-narrow-tools.md).

When the right tool is called with a malformed input, see
[malformed tool calls](malformed-tool-calls.md).

## What to try, in order

1. [Consolidate tools](../techniques/consolidate-tools.md), when many
   narrow or overlapping tools do one job, or general tools carry no rule
   of the product. A few general wrappers by job, each with the product's
   policy inside: its permission checks, guards and defaults. Plan it as
   an arm that makes this change alone, so that its result says what it
   did: it is a large change.
2. [Remove or scope tools](../techniques/remove-or-scope-tools.md), when
   some attached tools are not needed for this task, or two tools overlap
   and one can go.
3. [New tool description](../techniques/new-tool-description.md), when the
   tool set is right and the descriptions do not say which tool is for
   which request. A new description on a clone or a wrapper of each tool
   that is confused with another.
4. [Guard tool calls at the boundary](../techniques/guard-tool-calls-at-the-boundary.md),
   when the right tool is called and the call breaks a rule that the
   product enforces.

## What not to do yet

- No prompt list of which tool to use when. Put when to use each tool in
  its own description. This is a very strong preference. Text in a tool
  moves with the tool. So a later experiment that adds, removes or changes
  a tool needs no prompt change, and its result stays clean. A prompt list
  ties the prompt to the tool set, and it grows with each tool.
- Do not put a consolidation and other changes in one arm. A large change
  together with others gives a result that no one can attribute.

## Budget

On a small budget, steps 2 and 3 come first: each has a low cost. Step 1
has a high cost. Plan it when the budget leaves room for an arm that makes
that change alone.
