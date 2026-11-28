---
title: Add computed input features
description: An input transform that computes a fact in code - a combined flag, a count, a sum, a comparison - and gives it to the model as a named line, so the model does not derive it on each item.
reviewed: 2026-11-25
status: active
levers:
  - input_transform
applies_to:
  - single_turn
cost: low
---

# Add computed input features

## When to use

- The answer depends on a fact that the model must derive from more than
  one field: several flags combined (open means not closed, not archived
  and not expired), a count of the rows of a list, a sum, a comparison of
  two values.
- The failures are on the items where that derivation matters, and the
  traces show a wrong count, a wrong comparison or a flag that was
  missed.
- The rule to derive the fact is fixed, and it is the same on every item.
- The model is small, or it runs with no thinking, and the failures are
  arithmetic or logic.

## When not to use

- **The rule needs judgment.** A feature that code cannot compute with no
  doubt is a guess, and it is then wrong in the same way on every item.
- **The rule needs what the template language cannot do**: date
  arithmetic, a lookup in other data, a call. The lever document gives
  the limits.
- **The feature is the answer.** A feature computes a fact of the input.
  It never states the label, and it never uses the expected output.
- **The feature fits a few train items only.** A condition written for
  the items that failed is a copy of the train set, and the generality
  check refuses it.
- **The task is multi-turn.** The lever does not apply.

## Alternatives

- [Thinking level ladder](thinking-level-ladder.md), when the derivation
  differs from item to item, so the model must reason and code cannot.
- [Policy as step-by-step procedures](policy-as-step-by-step-procedures.md),
  when the model applies a business rule wrongly and the rule needs words,
  not a computation.
- [Normalize inputs wrapper](normalize-inputs-wrapper.md), when the value
  that needs a fixed form goes into a tool call, not into the model's
  first message.

## How

The change is a new template on a new run config. See the lever
[input_transform](../levers/input_transform.md) for the template language,
the steps and the limits.

1. **State the rule** in one sentence, from the task instruction. When
   the instruction does not give it, read it from many expected outputs,
   not from the failing items alone.
2. **Compute it in the template** with a Jinja expression: boolean logic
   on fields, `| length`, `| sum`, a comparison.
3. **Add it as a named line** with a clear name that says what it means,
   for example `is_open: true`. Put it near the fields it comes from.
4. **Keep the source fields**, unless they mislead. The model can then
   handle a case that the rule does not cover.
5. **Guard optional fields**, and decide what the feature is when a
   source field is missing.
6. **Check the rule on train items** before the measurement: render a few
   items of each kind, and compare the feature with what you compute by
   hand.

## Check that it took effect

Drive two or three train items of different kinds. In each trace, the
first user message has the feature line, and its value is correct for
that item. A feature with the same value on every item computes nothing:
check the expression.

## Budget

Low cost: one template and a measurement on the evals that the feature
serves. A good step on any budget when the failures are a derivation that
code can do, because it removes the step from the model instead of asking
the model to do it better.

## If it does not move the evals

It must move an eval. A feature that changes no result adds text to every
call and a rule that someone must maintain. Remove it, unless the traces
show that the model now uses the feature and the failures have another
cause.

## Priors

None yet.

## Confidence

Medium. It is field practice, and it follows from where the failure is:
models are less reliable than code at counting, arithmetic and combined
conditions, so code removes the step. Low for the size of the gain on a
strong model with thinking, which can derive the fact without help. No
measured case is recorded here yet.
