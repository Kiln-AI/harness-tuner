---
title: Directive in the tool response
description: A wrapper whose response carries a short directive for the reply, or the derived facts the reply must state, so that the agent reads it at the moment it writes the reply.
reviewed: 2026-11-13
status: active
levers:
  - new_tool
applies_to:
  - tools
cost: medium
---

# Directive in the tool response

## When to use

- **The failure is in the reply after a tool call.** The call is right,
  and the reply breaks a rule that the prompt states: a format, a step to
  take, a thing not to say.
- The agent must state facts that come from the result, and it computes
  them wrong or leaves them out: counts, the filters applied, dates and
  time zones resolved from a relative date.
- A new tool description fixed the call, and the reply that follows the
  call still fails.

## When not to use

- **The failure is before or with the call.** The text that the agent
  reads at that moment is the tool's description:
  [new tool description](new-tool-description.md).
- **The reply needs no tool.** A directive can only come back from a call
  that the agent makes. The rule then belongs where the agent reads it
  without a tool.
- **The directive changes with the item.** A directive is fixed text, or
  facts that code derives. When it needs judgement about the item, it is a
  rule for the model, not for the tool.
- **The rule is a hard limit of the product.** A directive asks. A guard
  refuses: [guard tool calls at the boundary](guard-tool-calls-at-the-boundary.md).

## Alternatives

- [New tool description](new-tool-description.md), when the failure is at
  the decision to call.
- [Roll-up tool](roll-up-tool.md), when the agent does not call a tool at
  all and a context loader with a prompt line would bring the facts in.
- [Guard tool calls at the boundary](guard-tool-calls-at-the-boundary.md),
  when the reply leads to a call that the product does not allow.

## How

The change is a wrapper of the tool that comes before the reply, on a new
run config, with the wrapped tool dropped. See the lever
[new_tool](../levers/new_tool.md) for the build. The template
`context_on_tool_surface.py` of the code-tool authoring skill has this
shape: a description read before the call, and a response that carries a
directive for the reply.

1. **Find the call that comes before the failing reply** in a few traces.
2. **Choose what the response carries.**
   - **A directive**: one or two short lines in a named field of the
     response, for example `next_step`, that say what the reply does.
     Write it as a step, not as a warning.
   - **Derived facts**: the values that the reply must state, computed by
     code from the result (the count, the filters applied, the resolved
     date and its time zone). The agent then copies them and does not
     compute them.
3. **Keep the rest of the result as the wrapped tool returns it**, unless
   another change trims it.
4. **Remove the prompt text that the wrapper takes over** on the same arm.
   Two places that state one rule can disagree after a later change.
5. **Write from the rule and the traces**, never from the text of an eval
   or from a train item.

## Check that it took effect

Dry-run the wrapper and read its response: the field is there, with the
expected text or the expected facts. Then smoke two or three train items
that failed in the reply. In each trace, find the field in the tool result
and the reply that follows it.

## Budget

Medium cost: a wrapper and a dry run, and a measurement on its target
evals. When it fixes its target, the val gate measures every eval before a
promotion, the evals that the reply touches too. On a small budget, take
it after a new description, when the reply still fails after a right
call.

## If it does not move the evals

It must move an eval. The change exists to fix a failure in the reply.
When the count of those failures does not fall, the agent did not read the
field, or the failure is not at that reply.

## Priors

None yet.

## Confidence

Low to medium. It is reasoning from what the model reads: a tool result is
the last text before the reply, and it is next to the moment of the
failure. Facts that code derives remove a computation that the model can
get wrong. No measured case is recorded here yet.
