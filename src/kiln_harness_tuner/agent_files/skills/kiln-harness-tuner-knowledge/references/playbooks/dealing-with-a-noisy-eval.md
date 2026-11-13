---
title: Dealing with a noisy eval
description: A score moves between repeats with no change to the agent. Repeat before you trust a small difference, and do not let a decision turn on one or two items.
reviewed: 2026-11-12
kind: eval
applies_to: all
---

# Dealing with a noisy eval

## Situation

The same run config, measured twice on the same items, gives different
scores. Signs:

- `train_noise` in your input shows a spread between repeats that is as
  large as the effects the arms predict;
- items flip between pass and fail with no change to the agent;
- an arm's win or loss rests on one or two items;
- a judge gives different verdicts on the same output.

The noise comes from the agent (it answers the same input in different
ways), from the judge, or from the synthetic user of a multi-turn eval. A
difference smaller than the noise is not a result, in either direction.

## What to try, in order

1. Repeat before you trust a small difference: a remeasure of the arm and
   of the baseline on the same items. Size each arm so that its predicted
   effect is larger than the noise:
   [spending the budget](spending-the-budget.md).
2. When the judge is the source (it gives different verdicts on the same
   output, or verdicts that disagree with its reasoning):
   [dealing with a saturated eval](dealing-with-a-saturated-eval.md),
   which gives how to read the fires.
3. When the agent is the source, and the task needs one stable answer for
   one input: one arm on a sampling setting, with the trace-level reason:
   [thinking level ladder](../techniques/thinking-level-ladder.md).
4. When the items that flip are the ones that carry the decision, choose
   the target items from the failures that repeat. A failure that repeats
   is a better target than one that comes and goes.

## What not to do yet

- Do not let a decision turn on one or two items. Two items can flip by
  chance alone.
- Do not plan many small arms on a noisy eval. Each one ends with no
  answer. Fewer, larger arms teach more.
- Do not read a single run of the baseline as its true score. Its own
  noise is in that number too.

## Budget

On a small budget, repeats are expensive, so plan fewer arms and give
each one enough items. On a large budget, a repeat of the baseline early
in the run shows the noise before the arms need it.

## Priors

- **A bank's customer-service agent, against a simulated customer, on a
  support benchmark.** Each task was run three times with the same
  skills. In every training and validation set, 16% to 53% of the tasks
  passed on some tries and failed on others. With one try for each task,
  each of those tasks reads as a pass or a fail by chance. One sample, not
  a rule.
  Source: public, the paper "EvoSkill v2: Fully Agentic Skill Evolution
  from Benchmark Adaptation to a Stateful Evolver". 2026-10-23.
