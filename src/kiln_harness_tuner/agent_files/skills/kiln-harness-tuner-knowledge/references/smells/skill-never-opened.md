---
title: Skill never opened
description: An attached skill that no run opens. Its description is in the context of every run, and its content never reaches the model.
reviewed: 2026-11-25
applies_to: all
detected_by:
  - code
  - agent
detectors:
  - unused_skill
---

# Skill never opened

## What it is

The run config attaches a skill, and the agent does not open it on this
task. The description of the skill is in the context of every run, so
every run pays for it. The body and the reference files never reach the
model, so the skill changes nothing. Either the task does not need the
skill, or it needs it and the agent does not know when to open it. This is
a property of the design, whether an eval passes or fails.

Code finds the sure form: a skill that no stored train run of the baseline
opens, when those runs cover the reference count of train items. A reader
finds the same in a smaller sample, with less confidence.

## How to recognise it

In the harness:

- a description that says what the skill holds, and not which requests
  need it;
- a skill for work that this task does not do;
- a prompt that never names the skill.

In how the agent runs:

- the code detection in your input names the skills that no stored train
  run opens, and the number of train items that those runs cover;
- no call of the skill tool with the name of the skill in the sampled
  traces.

## How to confirm it

- Check that the stored runs cover each eval. A skill that one rare kind
  of request needs is not unnecessary, and a small sample can miss that
  kind.
- Check if the agent opens any skill. When no run opens any skill, the
  cause can be the model or the run config, and not this skill.
- The reference is a count of train items with no call. It gives a bound
  on how often the skill is opened, not a proof that it is never needed.
  Read the detection's source before you state how sure you are.
- **Not this smell:** a skill that the agent opens and does not follow.
  The content reaches the model.

## What it blocks

An edit of the body or of the reference files of the skill. While no run
opens the skill, a change to its content cannot reach the model.

## Remedies

- [The skill is not read](../playbooks/skill-is-not-read.md), first, when
  failing items need what the skill holds. Its steps for a skill that is
  not opened: a prompt rule that says which requests need it, or the
  content in the prompt.
- [Prune one group at a time](../techniques/prune-one-group-at-a-time.md),
  when it is not known that the content helps: drop the skill on one arm
  and measure it.
- [Skill into the prompt](../techniques/skill-into-the-prompt.md), when
  the content is needed and short, or when the model does not open skills
  under this run config.
