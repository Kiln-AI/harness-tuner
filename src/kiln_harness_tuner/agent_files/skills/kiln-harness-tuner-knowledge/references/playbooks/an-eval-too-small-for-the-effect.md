---
title: An eval too small to show the effect
description: The eval has too few items, or too few that fail, to show a change of the size you predict. Check the size before the arm, then aim at more items, a larger effect, or a merge.
reviewed: 2026-11-25
kind: eval
applies_to: all
---

# An eval too small to show the effect

## Situation

An arm can only win when enough items change. Two limits apply:

- **Code's minimum.** The loop makes no decision on fewer than 3 items
  that differ between the two sides, on train and at the val gate. Below
  that, the arm ends `— UNVERIFIED`, or the gate gives "unverified at
  val".
- **Significance.** A significant win needs more: `train_detectability`
  gives `floor` and `fewest_wins` for each score, and `best_case_change`,
  the smallest change the eval can show in the best case.

Signs that the eval is too small for the arm:

- the predicted effect, in items, is below 3 or below `fewest_wins`.
  For example, a change that fixes one failure class of 2 items, on an
  eval with 4 failing items;
- `room` is small, so that even a perfect arm changes few items;
- an arm ends `— UNVERIFIED`, or a gate gives "unverified at val" or "a
  gain at val, not clear".

This is a property of the eval and the arm together. A noisy eval is a
different problem: there the same config changes items between repeats.
[Dealing with a noisy eval](dealing-with-a-noisy-eval.md) covers it. An
eval with no room at all is
[a saturated eval](dealing-with-a-saturated-eval.md).

## What to try, in order

1. Before the arm, convert the predicted effect to items: the predicted
   change times the target eval's `n`, capped by its `room`. When the
   result is below 3, the arm cannot get a verdict. When it is below
   `fewest_wins`, it can pass on train and still fail to show at the val
   gate. Plan against these numbers, as
   [spending the budget](spending-the-budget.md) says.
2. Choose a target eval with more items that fail in the way the issue
   says, when one exists. The same fix can be measured where more of its
   items are.
3. Make the effect larger. Group the failures by cause, and write one
   general change for the cause that explains the most items, not a rule
   for the few items that failed. A rule for two items does not transfer:
   [one general rule per edit](../techniques/one-general-rule-per-edit.md).
4. Merge wins on the same eval. Two arms that each change 2 items cannot
   get a verdict alone. Merged, they can change 4. Do this only when the
   two changes do not conflict, under the merge timing of your planner
   skill.
5. When the eval is the limit and no other eval can measure the issue,
   say so in `open_questions`: the eval needs more items, or more items of
   that kind, to steer the tuning. The tuner does not change an existing
   eval. Only when `eval_building` is `true`, an `eval_only` item can
   commission a new eval with more items for an open issue, under the
   rules of the planner's skill for that item.

## What not to do yet

- Do not plan an arm whose best case is fewer than 3 items. It costs a
  build and a measurement, and it can only end unverified.
- Do not read `— UNVERIFIED` or "unverified at val" as a failed idea. The
  idea is untested at this size. Do not stop work on it for that reason
  alone.
- Do not run the same arm again on the same items to get more items. A
  `remeasure` fills only items with no stored row, so it returns the same
  rows.

## Budget

On a small budget, step 1 comes first: it costs nothing and it removes
arms that cannot get a verdict. Steps 2 and 3 cost a plan, not a
measurement. A merge (step 4) costs a full val pass at the gate, so on a
small budget merge only wins that each show a gain on train.
