---
title: Very long skill description
description: The description of an attached skill is longer than the reference length. A code detector reads each skill's front matter, and each detection names the skill.
reviewed: 2026-11-13
applies_to: all
detected_by:
  - code
detectors:
  - long_skill_description
---

# Very long skill description

## What it is

The description in the front matter of an attached skill is longer than
the reference length. Every skill description is in the context of every
run, so its length is paid on every run, whether the agent opens the skill
or not. A description is for the choice to open the skill. The content
belongs in the body and in the reference files. This is a property of the
design, whether an eval passes or fails.

## How to recognise it

In the harness:

- the code detection in your input names the skill, the length of its
  description and the reference;
- the description holds rules or facts that the agent needs only after it
  opens the skill.

In how the agent runs:

- the agent acts on the description and does not open the skill.

Kiln limits this field to the same length today. So this detection shows a
skill from another source, or a change of that limit.

## How to confirm it

- Read the description, and sort its text in two parts: what helps the
  agent decide to open the skill, and what it needs only after.
- **Not this smell:** a path index of the reference files, with their key
  terms, that fits inside the reference length. That text helps the agent
  open the right file.

## What it blocks

Nothing.

## Remedies

- [Rewrite an inherited skill](../techniques/rewrite-an-inherited-skill.md),
  when the skill can get a new version: a short description, and the rules
  and facts moved into the body or the reference files. When the
  description is long because it lists the files, keep the paths and their
  key terms, and cut the other text.
- [Skill into the prompt](../techniques/skill-into-the-prompt.md), when
  most runs need the content of the skill.
