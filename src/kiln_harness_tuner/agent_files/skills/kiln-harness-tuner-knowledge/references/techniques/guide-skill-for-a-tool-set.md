---
title: Guide skill for a tool set
description: A side skill that describes the agent's tools by progressive disclosure - which tool for which work, then one leaf file per tool or per query area.
reviewed: 2026-11-12
status: active
levers:
  - skill
applies_to:
  - tools
cost: medium
---

# Guide skill for a tool set

## When to use

- The agent must find a tool before it can use it: it searches for tools,
  or it guesses that a tool exists.
- The agent has a general tool (SQL, GraphQL, a general API) and must learn
  a schema, its traps and its conventions before a query is right.
- The traces show calls to the wrong tool, or calls that fail because the
  agent did not know an input, and the tool descriptions alone cannot hold
  what the agent needs to know.
- A redesign of the tools changes what the agent must know. The guide
  changes with the tools, as one set of changes.

## When not to use

- **The agent has a few tools, and their descriptions say all there is to
  know.** A skill adds a step and a description in every call.
- **The failure is at the decision point of one call** (a preamble, a wrong
  argument, a narration with the call). The text that the agent reads at
  that moment is the tool's own description, not a skill.
- **The model does not open skills.** Probe that first, on this model.
- **The tools return very large results.** A guide does not make a result
  smaller. Fix the result first.

## Alternatives

None.

## How

The change is a new skill bundle, attached on a new run config. See the
lever [skill](../levers/skill.md) for how a skill is built and installed.

1. **The description** says which work the skill is for, and lists the path
   of each reference file with the key terms of that file.
2. **The `SKILL.md` body** is a map: for each type of work, which tool to
   use and which file to read. It does not repeat the files.
3. **One leaf file for each tool**, or for each area of a query tool: what
   it does, its inputs and outputs, and the traps. For a query tool, put
   sample queries at each level, and keep the schema in leaf files for the
   complex query that needs it. Dates and time zones are a frequent trap
   and deserve their own section.
4. **A prompt line is not part of this technique.** When the agent must be
   told to open the guide, that is a second change, measured on its own or
   stated as part of the hypothesis.
5. **Write from the tools and the traces**, never from the train items:
   no value of an item goes into the guide.

## Check that it took effect

Smoke on two or three train items that need the tools. In each trace, find
the read of the skill and of one leaf file before the tool call that needs
it. When the skill is never read, the change did not reach the agent.

## Budget

Medium cost: a bundle to write, and a measurement against each active eval,
because a skill can change how the agent works on every item. On a small
budget, take it only when a smell or a failure pattern names it. It is not
a first step when a cheaper change to one tool description fits.

## If it does not move the evals

It depends. Keep it when it replaces tool search or long tool descriptions
and the cost per item falls, because the guide is then a better design. When
the cost does not fall and no eval moves, it is not worth the extra step.

## Priors

None yet.

## Confidence

Low to medium. It is field practice and an idea of this project's owners:
a list of tools by progressive disclosure is better than tool search in
most cases that they have seen. No measured case is recorded here yet.
