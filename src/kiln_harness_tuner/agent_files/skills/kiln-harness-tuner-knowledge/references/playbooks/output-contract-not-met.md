---
title: The output contract is not met
description: The output breaks a format rule that nothing enforces - a closing block, a layout of free text, an escaping rule, or a rule about the content of a field.
reviewed: 2026-11-12
kind: failure
applies_to: all
---

# The output contract is not met

## Situation

The eval fails on the form of the output, not on its content. Look at the
end of the trace, in the final output of the agent:

- a required block is missing, or it is not closed;
- the free text does not have the layout that the task requires;
- a character is not escaped as the task requires;
- a field holds a value in the wrong form (a date form, the empty case).

The content of the answer is often right. When a JSON schema gives the
model the shape of the output, a wrong shape is not this situation: the
shape needs no prompt line. A rule about the content of a field still is.

## What to try, in order

1. [State the output contract](../techniques/state-the-output-contract.md),
   when nothing tells the model the rule. One prompt line that states the
   contract exactly.
2. Read the residual failures of that arm. When an edge case remains (an
   escaping case, the empty case), add one line for that case, with the
   same [technique](../techniques/state-the-output-contract.md), in the
   next arm.

## What not to do yet

- No skill, no tool and no model swap. One prompt line costs less, and it
  is the first thing to measure.
- Do not copy the text of the eval or of its judge into the prompt. State
  the rule in new words.
- Do not repeat a rule that the prompt already states. When the model
  ignores a rule that is there, the problem is where the rule is, or the
  length of the prompt.

## Budget

The order does not change with the budget.
