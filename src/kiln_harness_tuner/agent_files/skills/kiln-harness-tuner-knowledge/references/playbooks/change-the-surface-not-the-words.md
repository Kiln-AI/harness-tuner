---
title: Change the surface, not the words
description: Two text changes failed on one issue, or the failure is mechanical. Change what the model touches before the words it reads. A policy failure is the exception - procedure text comes first.
reviewed: 2026-11-12
kind: failure
applies_to: all
---

# Change the surface, not the words

## Situation

This playbook applies in two cases:

- **The failure is mechanical**: a wrong case or form of an id, pages
  that the agent does not follow, a result that floods the context, an
  error that the agent cannot act on, or a text that the model does not
  read at the moment it fails.
- **Two text changes failed on one issue.** A text change is a `prompt`
  arm, or a `skill` arm that only changes words (a description, a body).
  Your input's `lever_attempts` counts the arms opened on each issue and
  lever, with any outcome. The experiment records say which of them
  failed.

Instruction text is the cheapest change to build. For a mechanical
failure it can be the most expensive to learn from. The model that fails
a mechanical step does not stop because the words ask it to, and a rule
aimed at the failing items can fit the train items and not others. A
change to the surface (a tool, a wrapper, the structure of a skill)
removes the cause, instead of asking the model to avoid it.

Some causes are smells of the design, with their own documents:
[tool results flood the context](../smells/tool-results-flood-the-context.md)
and [faulty inherited skill](../smells/faulty-inherited-skill.md).

## What to try, in order

1. A wrapper that removes the mechanical failure, chosen by its cause:
   [normalize inputs wrapper](../techniques/normalize-inputs-wrapper.md)
   for input forms,
   [errors the agent can act on](../techniques/errors-the-agent-can-act-on.md)
   for unusable errors,
   [cap and preview wrapper](../techniques/cap-and-preview-wrapper.md) for
   floods and pages,
   [roll-up tool](../techniques/roll-up-tool.md) for a chain of calls, and
   [directive in the tool response](../techniques/directive-in-the-tool-response.md)
   for a text that the model does not read when it replies.
2. A guard at the tool, when the failure is a rule that the product can
   check:
   [guard tool calls at the boundary](../techniques/guard-tool-calls-at-the-boundary.md).
3. A structural change to a skill, when the failure is in what the agent
   reads: a router with reference files,
   [long prompt into a skill](../techniques/long-prompt-into-a-skill.md),
   or a new version of an inherited skill,
   [rewrite an inherited skill](../techniques/rewrite-an-inherited-skill.md).
4. **The exception: a policy failure.** When the agent breaks a rule of
   the business that only words can carry (who gets a refund, which case
   needs a person), procedure text comes first:
   [policy as step-by-step procedures](../techniques/policy-as-step-by-step-procedures.md).
5. When no surface change fits the issue, defer the issue with the reason
   in `rejected_summary`.

When a wrapper, a guard or a tool takes over a step, the same arm removes
the prompt text that asked the model to do that step.

## What not to do yet

- No third text arm on the same issue after two failed. The planner's
  skill gives the count and the rule.
- No prompt list of rules for a mechanical failure. A wrapper removes the
  failure.
- No model swap to hide a mechanical failure. The cause stays in the
  harness, and a stronger model costs more on every item.

## Budget

The order does not change with the budget. A wrapper costs more to build
than a text change. On a small budget, that cost is still lower than more
text arms that cannot win.

## Priors

- **A customer-service agent with tools, on a retail benchmark, tuned by
  an automatic optimiser.** The optimiser grouped the failures, and sent
  each group to the prompt, to a guard at the tool boundary, or to both.
  With that routing, the mean gain on held-out items was 14.9 points.
  With the routing removed and all else the same, it was 5.4 points. One
  sample, not a rule.
  Source: public, the paper "Beyond Prompts: Measuring and Optimizing LLM
  Tool-Agent Harnesses". 2026-09-09.
