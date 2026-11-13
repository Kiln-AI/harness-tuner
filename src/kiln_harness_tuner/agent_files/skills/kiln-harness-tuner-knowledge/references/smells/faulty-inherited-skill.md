---
title: Faulty inherited skill
description: A skill that the baseline attaches is wrong, stale, contradicts the task, or holds data shaped like test cases. It can be changed as a new version.
reviewed: 2026-11-13
applies_to: all
detected_by:
  - agent
---

# Faulty inherited skill

## What it is

A skill that the baseline attaches has a fault in its content: a fact that
is wrong, a description of tools, routes or fields that the agent does not
have, a rule that contradicts the task, or examples shaped like test cases
that teach the model the wrong pattern. The agent follows the skill, so the
fault reaches the output. This is a property of the design, whether an eval
passes or fails.

## How to recognise it

In the harness:

- a skill that names tools, fields or steps that the run config does not
  have;
- a rule in a skill that says the opposite of the prompt or of the task's
  instruction;
- a block of examples with inputs and answers, in the form of eval items.

In how the agent runs:

- the agent calls a tool or a field that the skill names and that does not
  exist;
- the agent follows the skill against the prompt.

## How to confirm it

- Read the whole skill, with its reference files, beside the prompt, the
  task's instruction and the tool list.
- State each fault with the file and the words.
- **Not this smell:** a skill that copies train items word for word. The
  baseline audit reports that as its own issue. A skill that is right and
  that the agent does not open is a failure pattern, not a fault of the
  skill.

## What it blocks

A prompt rule that works around the skill. The skill is part of what the
tuner can change, and a rule around it leaves the fault in place.

## Remedies

- [Rewrite an inherited skill](../techniques/rewrite-an-inherited-skill.md),
  first: a new version of the skill, with the fault removed, attached in
  place of the old one. When the agent does not open the reference file it
  needs, the new version also restructures it: the full path of each file,
  with its key terms, in the description and in the body map.
- [Prune one group at a time](../techniques/prune-one-group-at-a-time.md),
  when the skill may not help at all: drop it on one arm and measure it.
