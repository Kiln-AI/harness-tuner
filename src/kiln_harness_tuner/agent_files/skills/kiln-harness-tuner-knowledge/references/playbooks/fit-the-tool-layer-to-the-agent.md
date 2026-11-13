---
title: Fit the tool layer to the agent
description: The agent's tools are the company's software APIs, and a good API can be a poor agent tool. Wrap them, do not rewrite them - which wrapper for which problem, with the prompt and the guide skill.
reviewed: 2026-11-12
kind: design
applies_to:
  - tools
---

# Fit the tool layer to the agent

## Situation

The agent's tools are often the company's software APIs. They were made
for software, and software reads extra data at no cost. An agent pays for
each token that a tool returns, and the model runs again between two
calls. So a good API can be a poor tool for an agent.

This playbook responds to the smells of the tool design. Each smell has
its own document in the planner's entry file, and that document says how
to recognise it. The smells of the tools are:
[tool results flood the context](../smells/tool-results-flood-the-context.md),
[fixed call chain](../smells/fixed-call-chain.md),
[general query tool without a guide](../smells/general-query-tool-without-a-guide.md),
[too many narrow tools](../smells/too-many-narrow-tools.md),
[tool search](../smells/tool-search.md),
[document behind a lookup tool](../smells/document-behind-a-lookup-tool.md)
and [very long tool description](../smells/very-long-tool-description.md).

The tuner does not change the company's tools. It writes a layer of code
tools over them: a wrapper calls the original tool and changes what goes
in or what comes back. The [code tools](../levers/new_tool.md) lever says
how. So many tool designs can be tried, and the original tools stay as
they are.

The harness is one object. A new tool often needs a prompt line that says
when to call it, and a guide skill can describe the tool set. The same arm
removes the prompt text that the new tool takes over.

## What to try, in order

Each item applies to its own smell. Start with the smell that blocks the
most other work.

1. A result that is very large, or that carries every field:
   [cap and preview wrapper](../techniques/cap-and-preview-wrapper.md).
   It returns the fields this agent uses, a preview with a follow-up
   query, or an error that asks for a narrower query.
2. The same chain of calls each time, with an id passed from one call to
   the next, or one call for each item of a list:
   [roll-up tool](../techniques/roll-up-tool.md).
3. A general query tool (SQL, GraphQL, a general API) with no examples and
   no guide to its schema, or tool search:
   [guide skill for a tool set](../techniques/guide-skill-for-a-tool-set.md).
4. Many narrow tools, or tools that overlap: first
   [remove or scope tools](../techniques/remove-or-scope-tools.md), when
   some of them are not needed; then
   [consolidate tools](../techniques/consolidate-tools.md), when the
   remaining tools still overlap or carry no rule of the product.
5. Errors that the agent cannot act on, or inputs in forms that the tool
   refuses:
   [errors the agent can act on](../techniques/errors-the-agent-can-act-on.md)
   and [normalize inputs wrapper](../techniques/normalize-inputs-wrapper.md).

When a smell is fixed, the failures that it hid can show. Take each of
them to its failure playbook.

## What not to do yet

- Do not ask for a change to the company's APIs. The tuner cannot make it,
  and a layer of wrappers can try many designs in the same time.
- Do not add prompt rules on how to use a tool in place of a change to the
  tool. The rule is far from the moment of the call, and the tool still
  returns what it returned.
- Do not put several unrelated wrappers in one arm. When it wins, nothing
  says which wrapper helped. A wrapper with its prompt line and its guide
  skill is one technique, and it is one arm.
- Do not plan a small fix elsewhere while a tool result can flood the
  context. The flood hides the effect of the fix.

## Budget

On a small budget, the low-cost steps come first: remove or scope tools,
and a new description where the description is the problem. A
consolidation of tools has a high cost: plan it when the budget leaves
room for an arm that makes that change alone.
