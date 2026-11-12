---
title: Rewrite an inherited skill
description: A new version of a skill that the baseline attaches - split, merged, restructured or scrubbed - attached in place of the old one on a new run config.
reviewed: 2026-11-12
status: active
levers:
  - skill
applies_to: all
cost: medium
---

# Rewrite an inherited skill

## When to use

- The failure comes from a skill that the baseline attaches. The skill is
  wrong, stale, one long file with many topics, contradicts the task, or
  holds data shaped like test cases.
- Earlier arms added prompt rules around the skill, and the failure stayed.
- The skill describes tools, routes or fields that the agent does not have
  any more.

Everything that the baseline attaches is part of what the tuner can change.
An inherited skill is not a fixed input.

## When not to use

- **The skill is correct, and the agent does not read the right file.**
  That is a read failure:
  [path index in the skill description](path-index-in-the-skill-description.md).
- **The skill may not help at all.** Measure the agent without it first:
  [prune one group at a time](prune-one-group-at-a-time.md).
- **Do not add prompt rules around a skill that you can change.** A rule
  that says "ignore part X of the skill" leaves two texts that disagree.

## Alternatives

- [Path index in the skill description](path-index-in-the-skill-description.md),
  when only the reads fail.
- [Skill into the prompt](skill-into-the-prompt.md), when the skill is a
  few words.
- [Prune one group at a time](prune-one-group-at-a-time.md), when it is
  not known that the skill helps.

## How

The change is a new version of the [skill](../levers/skill.md), attached
in place of the old one on a new run config.

1. **Export the skill**, and read every file.
2. **Choose the rewrite** that the failure names:
   - **split** a long file into one reference file for each topic;
   - **merge** skills that overlap, so one topic has one place;
   - **restructure** a body into a router with a path index;
   - **correct** what is wrong or stale;
   - **scrub** data shaped like test cases: examples with real-looking ids,
     inputs with their expected answers.
3. **Install it under a new name**, with the old skill's id in
   `derived_from_ids`. Attach it and drop the old one. The procedure is
   "Rewriting an inherited skill" in the `kiln-harness-tuner-experiment-build`
   skill, `references/skill-authoring.md`.
4. **Change each mention of the old name** in the prompt and in other
   skills.
5. **Never write a value from a train item** into the new version. Code
   compares the new version with the old one, and searches it for train
   content.

## Check that it took effect

Read the diff of the new version against the old one: only the parts that
the hypothesis names changed. Smoke on two or three train items that need
the skill, and find the read of the new version in each trace.

## Budget

Medium cost: a bundle to write, and a measurement on every active eval that
the skill touches. On a small budget, take it when the traces tie the
failure to the skill's text.

## If it does not move the evals

It depends. Keep it when the rewrite removes content that is wrong, stale,
contradicts the task or looks like test cases, because such content can
mislead the agent on inputs that the evals do not cover. Otherwise, it must
move an eval.

## Priors

None yet.

## Confidence

Medium. It follows from the lever `skill`: an inherited skill is part of
what the tuner can change, so a rule added around it leaves two texts that
can disagree. No measured case is recorded here yet.
