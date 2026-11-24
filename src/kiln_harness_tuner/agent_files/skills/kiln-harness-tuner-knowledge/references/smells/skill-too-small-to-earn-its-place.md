---
title: Skill too small to earn its place
description: An attached skill holds a few words and no other file. Its description is in every run and opening it costs a call. Code measures the body of each skill.
reviewed: 2026-11-25
applies_to: all
detected_by:
  - code
  - agent
detectors:
  - small_skill
---

# Skill too small to earn its place

## What it is

An attached skill holds a few words in its body, and no reference file. Its
description is in the context of every run, and the agent needs one more
call to read the body. When the body is about as short as the description,
the skill saves no context and adds a call. This is a property of the
design, whether an eval passes or fails.

## How to recognise it

In the harness:

- the code detection in your input names the skill, the length of its
  body and the reference. Code reports a skill with no reference file and
  no text asset;
- with no detection: a skill whose bundle has only `SKILL.md`, with a body
  of a few lines;
- a body that repeats its own description, or adds one or two rules to it.

In how the agent runs:

- the agent opens the skill on most items, so its text is read on each of
  them;
- or the agent never opens it, so its content never reaches the model.

## How to confirm it

- Compare the length of the body with the length of the description.
- In the sampled traces, count the items where the agent opens the skill.
- The reference is the size that the Agent Skills specification gives for
  the metadata of a skill. It is not a measured optimum. Read the
  detection's source before you state how sure you are.
- **Not this smell:** a short body that routes to reference files. That
  skill is small because it uses progressive disclosure.

## What it blocks

Nothing.

## Remedies

- [Skill into the prompt](../techniques/skill-into-the-prompt.md), when
  the content is needed and short enough for the prompt: move it there and
  drop the skill.
- [Prune one group at a time](../techniques/prune-one-group-at-a-time.md),
  when it is not known that the content helps: drop the skill on one arm
  and measure it.
