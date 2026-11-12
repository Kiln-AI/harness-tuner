---
title: New tool description
description: A new description for a tool - a code-tool clone, or a wrapper - because the description is the one text the model reads at the moment it decides to call the tool.
reviewed: 2026-11-12
status: active
levers:
  - new_tool
applies_to:
  - tools
cost: low
---

# New tool description

## When to use

- **The failure is at the decision point of a call.** The failing message
  comes before or with a tool call: a preamble that the task does not
  allow, a narration with the call, a call that is not needed, or no call
  where one is needed.
- The agent calls the wrong tool of two that look alike, and the
  descriptions do not say when to use each.
- The agent passes a wrong argument, and the parameter description does
  not say what the argument is or what form it takes.
- The agent misspells the tool's name, or calls a name that does not
  exist. A clearer name is a new tool too.

## When not to use

- **The failure comes after the tool result**, in the reply. The text
  that the agent reads at that moment is the result:
  [directive in the tool response](directive-in-the-tool-response.md).
- **The tool works, and the call is right, but the result is too large**
  or the call fails on a form that code could fix. Change what the tool
  does, not what it says: [cap and preview wrapper](cap-and-preview-wrapper.md),
  [normalize inputs wrapper](normalize-inputs-wrapper.md).
- **Do not change the prompt, a skill body or a skill description for a
  failure at the decision point.** The model reads those far from the
  moment of the call. The tool's description is next to the decision.
- **The description is already long.** Add the missing point and remove
  text that does not help the decision. A longer description is read on
  every call.

## Alternatives

- [Directive in the tool response](directive-in-the-tool-response.md),
  when the failure is in the reply after the call.
- [Normalize inputs wrapper](normalize-inputs-wrapper.md), when the agent
  sends inputs in many forms and code can map each one.
- [Consolidate tools](consolidate-tools.md), when many tools overlap and
  no description can make the choice clear.

## How

The change is a new tool, because the description of an attached tool is
fixed: a clone of a code tool with the new description, or a wrapper of an
MCP or built-in tool. See the lever [new_tool](../levers/new_tool.md) for
the build, and drop the old tool on the same run config.

1. **Name the decision point.** From the analyzer's findings or the
   traces: which tool, and whether the failure came before, with or after
   the call.
2. **Write the description for that decision.** Say what the tool does,
   when to call it, and when not to. When the failure is a preamble or a
   narration, say what the agent does with the call (for example, "call
   this before you reply, with no text before the call").
3. **Write each parameter description**: what the value is, its form, and
   an invented example when the form is not plain.
4. **A name the agent can write.** When the traces show a misspelled or
   invented tool name, give the clone a short name in the words that the
   agent uses. The new tool needs a fresh function name in any case.
5. **Keep it short.** The description is read on every call of every
   item. Do not copy the prompt into it.
6. **Write from the traces and the tool**, never from the text of an eval
   or from a train item.

## Check that it took effect

Read the new run config: the new tool is attached, the old one is gone,
and the description that the model sees (`get_tool_definition`) is the
new text. Then smoke two or three train items that failed at that call,
and count the calls in the right form.

## Budget

Low cost: a clone or a thin wrapper, and one measurement. A good first
step on any budget when the failure is at the decision point of one call.

## If it does not move the evals

It must move an eval. The change exists to fix a failure at one call. When
the count of those failures does not fall, the description did not reach
the decision, or the failure is of another kind. A description that only
reads better is not worth a new tool.

## Priors

None yet.

## Confidence

Medium, for a failure at the decision point of one call. It is field
practice, and it follows from what the model reads: the description and
the parameters of a tool are next to the choice of the call, and the
prompt is not. Low, for a change of description with no failure that it
targets: a longer description can add steps.
