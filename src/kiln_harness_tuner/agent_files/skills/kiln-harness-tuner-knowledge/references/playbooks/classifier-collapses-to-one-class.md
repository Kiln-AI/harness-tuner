---
title: A classifier collapses to one class
description: A classifier gives one class to most inputs, also to inputs that expect another class. Find why the other classes lose their items before you change the model or its settings.
reviewed: 2026-11-25
kind: failure
applies_to:
  - classifier
---

# A classifier collapses to one class

## Situation

The task returns a label from a small closed set, and one label takes the
items of the other classes. The pass rate shows that items fail. It does
not show that the failures have one shape.

Code shows the shape. On a task whose output schema has a closed-set field,
`label_spreads` gives one row for each label field: in the planner's input
for the baseline, and in the analysis table for a candidate and its
parent. A row counts, on the train items, the labels that the run config
gave beside the labels that the items expect. This playbook applies when a
row has `collapsed: true`:

- `top_label` is the class that takes the items;
- `collapse_fraction` is the net share of the items of the other classes
  that got it;
- `classes` shows which classes lose their items.

In the traces, look at the final output of the failing items. They expect
different classes, and they hold the same label.

When code gives no row (`label_spread_note` says why), count by hand: the
labels in the outputs from `get_train_results`, and the expected labels
from `get_train_expected`.

What looks like a collapse and is not:

- **A class that is common in the data.** One class for most inputs is
  correct when most inputs expect it. The row then has a
  `collapse_fraction` near 0.
- **Outputs with no label.** A large `predicted_outside` is a format
  failure: [the output contract is not met](output-contract-not-met.md).
- **Expected labels that are wrong.** When the outputs are right by the
  task instruction and the expected labels are not, see
  [dealing with a wrong eval](dealing-with-a-wrong-eval.md).

## What to try, in order

Read five to ten failing items of the classes that lose first. Each step
below has a condition that this reading settles.

1. [State the output contract](../techniques/state-the-output-contract.md),
   when the model does not get the list of classes: the output mode sends
   no schema, and the prompt does not name every class. One line that
   names the closed set.
2. [Add the missing requirement](../techniques/add-the-missing-requirement.md),
   when the prompt names the classes and does not say what puts an input
   in each one, or says it for `top_label` only. State the criterion of
   the class that loses the most items, in new words.
3. [One general rule per edit](../techniques/one-general-rule-per-edit.md),
   when a text in the prompt pushes to `top_label`: a default ("when in
   doubt, answer X"), a caution rule with no limit ("never miss an X"), or
   examples that mostly show one class. Replace that text with a rule that
   states the condition for the class. Do not add a rule beside it.
4. [Lost in long instructions](lost-in-long-instructions.md), when the
   prompt states the criteria and the model does not apply them, and the
   prompt is long.
5. [Thinking level ladder](../techniques/thinking-level-ladder.md), one
   rung up, when the criteria are stated and the items that fail need a
   step of reasoning (a count, a date, two conditions together).
6. [Model swap inside a class](../techniques/model-swap-inside-a-class.md),
   when steps 2 to 5 did not move the row and the model is a lever. When
   the run config uses a decision model, read
   [decision model for a classifier](../techniques/decision-model-for-a-classifier.md)
   first: its instructions need the criteria of each class, and it does
   not reason before it answers.

After each arm, read the candidate's row beside its parent's row in the
analysis table. A fix lowers `collapse_fraction` and the eval moves. A row
that is no longer collapsed with an eval that did not move is not a fix:
the errors went to other classes.

## What not to do yet

- Do not change the temperature or `top_p` to spread the labels (the lever
  [params](../levers/params.md)). A higher temperature adds chance to the
  answer. It does not add the criterion that the model lacks.
- Do not change the [output mode](../levers/output_mode.md) for a
  collapse. The mode decides the form of the output, not which class the
  model chooses. The exception is step 1.
- Do not tell the model how often each class occurs, and do not tell it to
  give `top_label` less often. That is a fact about the train set. It
  moves the counts and not the reasons, and it does not hold on new data.
- Do not write one rule for each failing item, and do not copy an item's
  text as an example. The generality check refuses it.
- Do not copy the text of the eval or of its judge into the prompt.
- The task's output schema is not a lever. When an input fits no class and
  the schema has no class for it, write an open question for the owner.

## Budget

Steps 1 to 3 are one prompt edit each, and they are the first steps on any
budget. Step 5 is a screening round. Step 6 is a scan of the class and a
full arm: on a small budget it waits for a run where this failure is the
main goal.

## Confidence

Low to medium. The order is field knowledge: the cheap text causes come
before the settings and the model. No measured case here compares the
steps.
