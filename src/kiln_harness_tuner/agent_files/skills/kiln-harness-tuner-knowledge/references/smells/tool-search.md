---
title: Tool search
description: The agent must guess that a tool exists, decide to search for it, and find the right search terms, before it can call it.
reviewed: 2026-11-12
applies_to:
  - tools
detected_by:
  - agent
---

# Tool search

## What it is

The agent does not see its tools. It sees a search tool, and it must find
each other tool through it. So before each call it must imagine that a tool
exists, decide to search, and choose the terms that find it. Each of the
three steps can fail, and each search costs a turn and context. This is a
property of the design, whether an eval passes or fails.

## How to recognise it

In the harness:

- a tool whose description says that it finds, lists or loads other tools;
- a tool list that holds few tools, while the task needs many actions.

In how the agent runs:

- searches for tools before the calls that do the work;
- searches with terms that find nothing, then a guess;
- an answer that says a capability is missing, when a tool for it exists.

## How to confirm it

- Open the search tool's definition, and confirm that it returns tools,
  not data.
- In the sampled traces, count the tool searches, and the searches that
  return nothing or the wrong tool.
- **Not this smell:** a search tool over documents or records. That tool
  finds data, not tools. A large tool list that is all visible to the agent
  is not this smell either.

## What it blocks

A change to the description of one tool that the agent reaches through
search. The agent reads that description only after the search finds the
tool.

## Remedies

- [Guide skill for a tool set](../techniques/guide-skill-for-a-tool-set.md),
  when the model opens skills: a side skill that lists the tools by
  progressive disclosure, so that the agent knows what exists.
