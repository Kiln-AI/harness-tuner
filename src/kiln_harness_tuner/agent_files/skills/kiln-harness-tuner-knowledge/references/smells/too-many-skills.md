---
title: Too many skills
description: The run config attaches more than 20 skills, and the name and description of each skill are in the context of every call. A code detector counts them.
reviewed: 2026-11-25
applies_to: all
detected_by:
  - code
detectors:
  - many_skills
---

# Too many skills

## What it is

The agent has more skills than the reference count. The agent reads the
body of a skill only when it opens the skill, but the name and the
description of each attached skill are in the context of every call. With
many skills, those descriptions take a part of the context before the agent
reads the request, and the agent chooses among all of them when it needs
one. This is a property of the design, whether an eval passes or fails.

## How to recognise it

In the harness:

- the code detection in your input gives the number of attached skills and
  the reference it was compared with. When code could read the skills, it
  also gives the total length of their descriptions.

In how the agent runs:

- the agent opens a few of the skills, and most of them are never opened;
- the agent opens a skill that does not fit the request, or opens two or
  three skills to find one fact.

## How to confirm it

- Read the descriptions as one list, as the agent gets them. Look for two
  skills that the same request fits, and for skills that this task has no
  use for.
- The reference is a default, not a measured optimum. It rests on two
  public numbers: about 100 tokens of name and description for each skill,
  and an agent product that starts to drop skill descriptions when their
  list is over 1% of the context window. Read the detection's source
  before you state how sure you are.
- A count over the reference with short, distinct descriptions is weak
  evidence. A count over the reference with long descriptions, or with
  descriptions that overlap, is stronger.
- **Not this smell:** one skill that holds many topics. That is
  [one-file skill with many topics](one-file-skill-with-many-topics.md).
- **Not this smell:** one description that is long. That is
  [very long skill description](very-long-skill-description.md). Report it
  on its own.
- A skill of a few words is a second smell,
  [skill too small to earn its place](skill-too-small-to-earn-its-place.md).
  Many such skills can be the cause of the count.

## What it blocks

A new skill added to the set. It is one more description among many on
each call, and its effect is hard to see.

## Remedies

- [Prune one group at a time](../techniques/prune-one-group-at-a-time.md),
  when it is not known which skills help: remove one skill on each arm and
  measure it, or add the skills one at a time to a base with none.
- [Rewrite an inherited skill](../techniques/rewrite-an-inherited-skill.md),
  when two or more skills hold the same topic: merge them into one skill
  with reference files, so one topic has one description.
- [Skill into the prompt](../techniques/skill-into-the-prompt.md), when a
  skill is a few words, or most runs need its content: move the content
  and drop the skill.
