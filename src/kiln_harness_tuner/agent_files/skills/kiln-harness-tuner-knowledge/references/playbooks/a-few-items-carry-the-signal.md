---
title: A few items carry all the signal
description: Every difference between arms comes from the same few items. Find out why those items move - noise, a wrong expected output, or the only real failures - before you plan against them.
reviewed: 2026-11-25
kind: eval
applies_to: all
---

# A few items carry all the signal

## Situation

Several arms on one eval differ from their parent on the same few items,
and on almost no other item. Signs:

- the `fail_to_pass` and `pass_to_fail` counts, or `n_differing`, of each
  row are small, and the experiment records name the same items;
- arms with different changes win or lose on the same two or three items;
- arms end `— UNVERIFIED`: fewer than 3 items differ.

Code makes no decision on fewer than 3 items that differ. So an eval in
this state stops the loop from learning: each arm ends without a verdict.

The few items move for one of three reasons. The steps below check them
from the cheapest to read.

## What to try, in order

1. Read the traces of those items for the baseline and for two or three
   arms, with the expected output and the judge's reasoning. Read the
   items before you plan another arm against them.
2. **The items flip by noise.** They pass and fail with no change to the
   agent, or the judge gives different verdicts on the same output. The
   arms then differ by chance, not by the change. Leave the items out of
   the evidence for an arm, and plan as
   [dealing with a noisy eval](dealing-with-a-noisy-eval.md) says.
3. **The items are wrong.** The expected output contradicts the task's
   instructions, or contradicts the expected outputs of the same kind of
   input, or the judge fails a right answer. Report them as
   [dealing with a wrong eval](dealing-with-a-wrong-eval.md) says. No
   harness change fixes them.
4. **The items are the only real failures.** The baseline passes the rest,
   and these items fail for a real cause. Then the eval is too small to
   steer the work. Find the cause in the traces, and plan as
   [an eval too small to show the effect](an-eval-too-small-for-the-effect.md)
   says: a larger effect, another eval, or more items.
5. When the items share one cause, plan one arm for that cause, in
   general words. A rule that names the items, or copies their content,
   fits them and nothing else:
   [one general rule per edit](../techniques/one-general-rule-per-edit.md).

## What not to do yet

- Do not choose between arms by which one passes these items. The choice
  then follows the noise or the errors in the items.
- Do not plan more arms on this eval before step 1. Each arm costs a
  measurement and ends without a verdict.
- Do not count a fix of these items as a lesson about the lever until the
  traces show why they changed.

## Budget

The order does not change with the budget. Step 1 costs reads only, and
it decides which of steps 2 to 4 applies.
