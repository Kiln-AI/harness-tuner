---
title: Path index in the skill description
description: A new skill version whose description lists the full path of each reference file, with its key terms, and says that no other paths exist.
reviewed: 2026-11-12
status: active
levers:
  - skill
applies_to: all
cost: low
---

# Path index in the skill description

## When to use

- The agent opens the skill, and then does not load the reference file it
  needs.
- The agent guesses a path: the traces show reads of files that do not
  exist, or of a file near the right one.
- The agent reads the wrong reference file for the request.

## When not to use

- **The model does not open skills at all** under this run config. Probe
  it first. An index in the description does not help a model that never
  reads skills.
- **The skill is one file.** There is nothing to index. When it holds many
  topics, see [long prompt into a skill](long-prompt-into-a-skill.md): the
  same split applies to a skill.
- **Do not make the `SKILL.md` body longer** to fix a read failure. The
  body is read after the decision that fails.
- **The index does not fit** in the 1,024 characters of a description.
  The skill has too many files for one entry point: split it.

## Alternatives

- [Skill into the prompt](skill-into-the-prompt.md), when the skill is
  small, or most runs need it.
- [Rewrite an inherited skill](rewrite-an-inherited-skill.md), when the
  files themselves are wrong, or one file mixes many topics.

## How

The change is a new version of the [skill](../levers/skill.md), attached
in place of the old one on a new run config.

1. **Write the description in three parts**: which work the skill is for;
   the full path of each `references/` file, each with the key terms of
   that file; and the sentence "No other paths exist."
2. **Use the exact paths** that the agent passes to read a file.
3. **Keep the body** as it is, unless its paths disagree with the index.
4. **A new version is a new name.** For an inherited skill: export it,
   change the copy, install it under a new name, attach it and drop the old
   one. The procedure is in the `kiln-harness-tuner-experiment-build`
   skill, `references/skill-authoring.md`.
5. A prompt rule that tells the agent when to read the skill is a second
   change. Measure it on its own arm, or state it in the hypothesis.

## Check that it took effect

Smoke on two or three train items that need a reference file. In each
trace, count the reads by exact path, and the failed reads. When the agent
still guesses, the description did not reach the decision.

## Budget

Low cost: a description change, as a new skill version. A good first step
when the traces show guessed paths.

## If it does not move the evals

It must move an eval. The change exists to fix a measured read failure.
When the reads now succeed and no eval moves, the content of the files is
not what the eval needs.

## Priors

None yet.

## Confidence

Low to medium. It is reasoning: the description is read before the
decision to open a file, and the body is read after it. Public guidance on
skills agrees that the description decides whether and how a skill is
used. No measured case is recorded here yet.
