---
title: State the output contract
description: One prompt line that states a format rule the model gets in no other way - a layout of free text, a closing block, an escaping rule, or a rule about a field.
reviewed: 2026-11-12
status: active
levers:
  - prompt
applies_to: all
cost: low
---

# State the output contract

## When to use

- The output breaks a format rule, and nothing gives the model that rule:
  a fixed layout of free text, a required closing block, an escaping rule,
  or a rule about the content of a field (the form of a date, what to
  write in the empty case).
- The traces show that the content of the answer is right and its form is
  wrong. The eval fails on the form.
- The model can produce the format. It does not produce it by default.

## When not to use

- **The shape of structured output, when a JSON schema already gives that
  shape to the model.** The schema states the shape, so a prompt line adds
  nothing. A rule about the content of a field is still in scope.
- **The content is wrong, not the form.** That is a requirement that the
  prompt does not ask for, which is a different problem.
- **The prompt already states the rule, and the model does not follow
  it.** Another line says the same thing again. Look for why the model
  misses it: a long prompt, or a rule that is far from the output
  instruction.
- **The model cannot produce the format at all**, on any prompt. That is a
  limit of the model, not of the instruction.
- Do not reach for a skill, a tool or a model swap first. One line costs
  less, and it is the first thing to measure.

## Alternatives

None.

## How

The change is one edit of the [prompt](../levers/prompt.md).

1. **Find the rule.** Read the task's instruction and requirements, and the
   failing outputs. State the rule in your own words. Do not copy the text
   of an eval or of its judge, and do not copy a value from a train item.
2. **Write one line that states the contract exactly**: the literal
   delimiters, the order of the parts, and the escaping. When an example
   helps, use invented values.
3. **Put the line beside the instruction about the output**, when the
   prompt has one.
4. **Replace, do not add.** When the prompt states the format less well in
   another place, remove that text on the same edit.
5. **Read the residual failures** after the measurement. When one edge case
   remains (an escaping case, the empty case), the next arm adds one line
   for that case.

## Check that it took effect

Smoke the new run config on two or three train items that failed on the
form. Count the outputs that obey the contract, by eye or with a regular
expression on the output. When none obey it, the line did not reach the
model, or the failure is not one of format.

## Budget

A good first step on any budget, when the failure is one of format. It
costs one prompt edit and one measurement.

## If it does not move the evals

It must move an eval. The change exists to fix a measured failure of
format. When the count of those failures does not fall, the line did not
take effect, or the failure is of another kind. Do not keep it as a better
design.

## Priors

- **A two-agent pipeline, on four tasks.** Six prompt optimisers ran with
  20 training questions, three times on each task, on one model. 49% of the
  72 runs scored below the unoptimised prompt. The one task on which all six
  methods won was the one whose output needed a format that the model could
  produce and did not produce by default. One sample, not a rule.
  Source: public, the paper "Prompt Optimization Is a Coin Flip", arXiv
  2604.14585. 2026-04-16.
- **An LLM judge that returns JSON, on a small open model.** The judge's
  prompt was optimised automatically, with DSPy. Malformed JSON fell from
  358 of 856 responses to 9 of 856. One sample, not a rule.
  Source: public, the Dropbox post "How we optimized Dash's relevance judge
  with DSPy". 2026-03-17.

## Confidence

Medium, for a format that the model can produce and does not produce by
default. In two public cases, a change to the prompt cut the failures of a
format that the model could produce. In one of them, that was the only kind
of task on which every prompt optimiser helped. Low, when the model cannot
produce the format at all: an instruction does not add a capability.
