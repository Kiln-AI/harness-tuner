---
title: A fix breaks items that passed
description: The change fixes the items it was built for, and items that passed now fail. Read what the broken items share, give the rule its condition, then move it to the narrowest place that holds it.
reviewed: 2026-11-25
kind: failure
applies_to: all
---

# A fix breaks items that passed

## Situation

An arm fixes its target items, and items that its parent passed now fail.
Code grades this on the train rows, and writes it on the experiment
record:

- `fix check: FLAG — … passing items broke, clearly above noise (p=…)`;
- `breaks not predicted: …` and `predicted risks: … broke …`, with the ids
  of the broken items for each eval;
- or the arm stopped: `— NOT LANDED (… guards broke, clearly above noise
  (p=…))`.

The words `within noise` mean that this playbook does not apply. A config
equal to its parent also breaks some items on each measurement:
[dealing with a noisy eval](dealing-with-a-noisy-eval.md).

In the trace of a broken item, look at the first message that differs
from the parent's trace of the same item. The usual causes, in the order
in which they are cheapest to check:

- **The rule fires outside its case.** The agent applies the new rule to
  an input that the rule was not written for. The rule has no condition,
  or a condition that many inputs meet.
- **The edit contradicts older text, or removed it.** The broken items
  needed a rule that the new text now opposes, or that the edit replaced.
- **The rule is in a place that every request reads.** It is about one
  tool or one type of request, and it sits in the prompt.
- **An example pulls the answers toward itself.** The broken items now
  copy the label, the format or the length of a new example.
- **The two groups ask for opposite behaviour.** The fixed items and the
  broken items have similar inputs, and the eval expects different
  answers. Then the measurement is the problem, not the change.

## What to try, in order

Rebuild each step from the arm's parent, not on top of the build that
broke the items.

1. Read the traces of the broken items before you plan, and group them by
   what they share. When they share nothing with each other and nothing
   with the change, treat them as noise:
   [dealing with a noisy eval](dealing-with-a-noisy-eval.md).
2. [One general rule per edit](../techniques/one-general-rule-per-edit.md),
   when the change is a text edit and the rule fires outside its case, or
   contradicts older text. State the cause as the condition of the rule,
   remove the contradiction, and state the rule once.
3. [New tool description](../techniques/new-tool-description.md), when the
   rule is about one tool. Text in a tool is read with that tool, so a
   request that does not use the tool is not changed. When the product
   can check the rule, use a guard:
   [guard tool calls at the boundary](../techniques/guard-tool-calls-at-the-boundary.md).
4. [Policy as step-by-step procedures](../techniques/policy-as-step-by-step-procedures.md),
   when the rule is for one type of request. A yes-or-no condition at the
   start of the procedure keeps the other types out of it. When the prompt
   is long and the model reads skills, put the procedure in its own
   reference file:
   [long prompt into a skill](../techniques/long-prompt-into-a-skill.md).
5. [Prune one group at a time](../techniques/prune-one-group-at-a-time.md),
   when the arm changed more than one thing (a merge, a large rewrite) and
   the traces do not show which part breaks the items. Keep the parts that
   fix, and drop the part that breaks.
6. [Change the surface, not the words](change-the-surface-not-the-words.md),
   when two text builds for the same target items each broke passing
   items.
7. [Dealing with a wrong eval](dealing-with-a-wrong-eval.md), when the
   fixed items and the broken items ask for opposite behaviour on similar
   inputs.

In the next arm on the same idea, name the broken items in
`at_risk_items`, and list their evals in `evaluation_target`. Code then
grades each of them after the train measurement.

## What not to do yet

- Do not add one exception line for each broken item. That is a rule for
  each case: the text grows, and the next arm breaks other items.
- Do not write a value or a name from a broken item into the fix. The
  generality check refuses it.
- Do not submit the same run config again, and do not merge it with
  another win so that the net result hides the breaks.
- Do not change to a stronger model to make a rule with no condition
  safe. The rule stays too wide, and each call costs more.
- Do not read a line that says `within noise` as a break.

## Budget

The order does not change with the budget. Step 1 costs reads only. On a
small budget, do steps 1 and 2 and stop: one rebuild with a condition is
the cheapest test of the idea. Steps 3 and 4 cost a larger build, and
step 5 costs one arm for each part.
