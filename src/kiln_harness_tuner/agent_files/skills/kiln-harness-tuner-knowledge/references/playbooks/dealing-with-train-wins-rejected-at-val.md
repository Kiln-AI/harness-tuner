---
title: Dealing with train wins rejected at validation
description: Arms win on train, and validation rejects them again and again. Look for copied content, stop rules for small groups of failures, run fewer and larger experiments, then change the surface.
reviewed: 2026-11-12
kind: eval
applies_to: all
---

# Dealing with train wins rejected at validation

## Situation

The experiment records show the same pattern more than once: an arm wins
on train, and its record ends `— REJECTED AT VAL`. The arms do not carry
to items they were not built on.

The causes, in the order in which they are cheapest to check:

- **Copied content.** The arm carries a value, a phrase or a case from the
  train items or from the eval. It fits those items and nothing else.
- **Rules for small groups.** Each arm adds a rule for the few items that
  failed. Each rule fits its items and does not transfer.
- **Selection under noise.** Many small arms are measured, and the one
  that wins on train is the one that the noise favoured. Its gain is
  smaller, or gone, on new items.
- **Text where the surface is the problem.** A text rule asks the model
  to avoid a mechanical failure that a tool change would remove.

## What to try, in order

1. Read the arms that were rejected for copied content: a train item's
   value, a case-by-case rule, a phrase of the judge. The generality check
   and the judge-copy check find some of it, not all. A rejected arm with
   copied text is rebuilt with each rule in new, general words:
   [one general rule per edit](../techniques/one-general-rule-per-edit.md).
2. Stop writing rules for small groups of failures. Group the failures by
   cause, and write one general rule for the cause that explains the most
   items, with the same
   [technique](../techniques/one-general-rule-per-edit.md).
3. Fewer and larger experiments, each with enough items to pass its own
   test, so that the noise picks fewer false winners:
   [spending the budget](spending-the-budget.md) and
   [dealing with a noisy eval](dealing-with-a-noisy-eval.md).
4. Then change the surface, when the failure is mechanical or two text
   arms failed on one issue:
   [change the surface, not the words](change-the-surface-not-the-words.md).

## What not to do yet

- Do not plan another text arm of the same kind for the same issue.
- Do not read a train win as a result until validation confirms it. A
  train win is a candidate.
- Do not decide from a score alone. The reasons of a judge and the
  failing items say what to change. A score alone says only that
  something changed.

## Budget

On a small budget, steps 1 and 2 come first: they cost reads and one
rebuild. Step 3 means fewer arms in each wave, not more spend.

## Priors

- **A multi-agent research agent, tuned by a prompt optimiser, on a split
  with no topic shared between train, validation and test.** Over three
  seeds, the gap between the gain on validation and the gain on held-out
  items ranged from 0.034 to 0.160. When the feedback for each criterion
  was reduced to one score, the transfer to held-out items became
  negative. One sample, not a rule.
  Source: public, the paper "Reflective Prompt Optimization of a
  Deep-Research Agent: A Topic-Disjoint, Multi-Seed Protocol for
  Measuring Transferable Gains and Prompt-Level Overfitting". 2026-10-08.
