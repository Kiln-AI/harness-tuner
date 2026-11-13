---
title: Dealing with a saturated eval
description: The baseline is at the ceiling of an eval, or the judge's fires are false. Measure the issue on the baseline, judge the fires, then grow or change the eval. No fix arm.
reviewed: 2026-11-12
kind: eval
applies_to: all
---

# Dealing with a saturated eval

## Situation

The measurement is the problem, not the agent. Signs:

- the baseline passes almost every item of the eval, so an arm has no
  room to show a gain;
- the eval fails items, and the outputs are right: the judge's reasoning
  ends in a pass and its score is a fail, or the judge fails an answer for
  a reason that the criteria do not state;
- an issue says the agent fails in some way, and the eval does not
  measure that way at all.

No change to the run config fixes an eval. A fix arm on a saturated eval
cannot win, and a fix arm on false fires chases the judge.

## What to try, in order

1. Measure the issue on the current baseline (a `measure_issue_baseline`
   item), when it is not clear that the baseline still fails in the way
   the issue says.
2. Read the fires. For each failing item, read the output, the expected
   output and the judge's reasoning. Name each fire as a config failure,
   an eval-data failure or a judge failure. Only a config failure is a
   candidate for an arm.
3. When the fires that remain change from repeat to repeat:
   [dealing with a noisy eval](dealing-with-a-noisy-eval.md).
4. When the eval is at the ceiling or the judge is wrong, the next step is
   a change to the eval: more items, harder items, or a judge calibrated
   against human labels. The tuner does not change an existing eval. Put
   it in `open_questions` for the eval's owner, with the items that show
   it. Only when `eval_building` is `true`, an `eval_only` item can
   commission a new eval for the issue, under the rules of the planner's
   skill for that item.
5. Take the issues that remain, measured on a sound eval, to their
   playbook:
   [ordering work](ordering-work.md).

## What not to do yet

- No fix arm on this eval. It cannot show a gain, so its spend is lost.
- Do not read a judge's score without its reasoning. A score that
  disagrees with the reasoning is a judge failure.
- Do not change the prompt to please the judge. That is the judge-copy
  failure, and the gate blocks it.

## Budget

The order does not change with the budget. Steps 1 and 2 cost little.

## Priors

- **A merchant assistant agent of a commerce platform, scored by LLM
  judges.** The team calibrated each judge against human labels, by
  iterative changes to the judge's prompt. The agreement with the human
  labels (Cohen's kappa) went from 0.02, near chance, to 0.61, against
  a human baseline of 0.69. One sample, not a rule.
  Source: public, the Shopify post "Building production-ready agentic
  systems: Lessons from Shopify Sidekick". 2025-08-26.
- **A data-analysis agent, scored by an LLM judge.** One eval had a trap:
  a common wrong method gave 2.03%, and the right answer was 2.04%. The
  judge accepted the wrong answer 35% of the time. One sample, not a rule.
  Source: public, the Hex post "How we built a lab to evaluate data
  agents". 2026-05-22.
