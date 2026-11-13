---
title: Too many narrow tools
description: The agent has many tools that each do one small thing, or tools that overlap, or general tools that carry no rule of the product.
reviewed: 2026-11-13
applies_to:
  - tools
detected_by:
  - agent
---

# Too many narrow tools

## What it is

The agent has many tools that each do one small thing, or tools whose jobs
overlap, or general tools that carry no rule of the product. Each tool
definition is in the context of every call. At each step the agent must
choose among them, and each tool that looks like another is one more way to
choose wrong. The product's rules are then in the prompt, far from the
moment of the call. This is a property of the design, whether an eval
passes or fails.

## How to recognise it

In the harness:

- a long tool list, with names or descriptions that look alike;
- two tools that can do the same job;
- tools that this task never needs;
- prompt rules on which tool to use when, or on what a tool must not do.

In how the agent runs:

- for requests of the same kind, the agent calls one tool of an
  overlapping pair on some items and the other tool on others;
- some attached tools are never called in the sampled traces;
- many calls of narrow tools in one run, where one general tool could
  take one call.

## How to confirm it

- List the tools by job, and mark the ones that overlap.
- In the sampled traces, count the calls of each tool, and the calls of
  each tool of an overlapping pair.
- **Not this smell:** many tools that each do a different job that the
  task needs, with descriptions that say when to use each one.

## What it blocks

A prompt list of which tool to use when. The agent reads the list far from
the moment it chooses, and the list grows with each new tool.

## Remedies

- [Consolidate tools](../techniques/consolidate-tools.md), first, when
  many narrow or overlapping tools do one job, or general tools carry no
  rule of the product: a few general wrappers by job, with the policy
  inside, in an arm that makes this change alone.
- [Remove or scope tools](../techniques/remove-or-scope-tools.md), when
  some tools are not needed for this task, or one of two overlapping tools
  can go.
- [New tool description](../techniques/new-tool-description.md), when the
  tool set is right and the descriptions do not say which tool is for
  which request.
- [Wrong tool chosen](../playbooks/wrong-tool-chosen.md), when the traces
  show the agent choose the wrong tool.
