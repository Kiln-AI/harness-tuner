---
title: Specialist sub-agent with its own tools
description: Move one sub-job, with its tools and its instructions, out of the main agent into a sub-agent - the main agent then sees one tool and gets a short result.
reviewed: 2026-11-25
status: active
levers:
  - sub_agent
  - tool_config
  - prompt
applies_to:
  - tools
cost: high
---

# Specialist sub-agent with its own tools

## When to use

All of these hold:

- **One group of the agent's tools serves one sub-job**, for example the
  lookups of one system, and the main prompt has a section on how to do
  that sub-job.
- **The sub-job takes many steps.** The traces show several calls of the
  group's tools, with results that the main agent needs only as a
  summary. After them, the main agent loses track of the request, or it
  chooses a wrong tool of another group.
- **Every tool of the group is needed.** Tools that the task does not use
  are removed first, at no cost for each call.
- **The sub-job has a clear input and a short result**, and it does not
  need the conversation.

The sub-agent does the sub-job in a context of its own. Its tools, their
descriptions, its instructions and each result stay there. The main agent
keeps a short tool list and a short prompt.

## When not to use

- **The steps are the same each time.** A fixed chain needs no model
  between its calls: [roll-up tool](roll-up-tool.md).
- **The only problem is the size of a result.** Trim it in code:
  [cap and preview wrapper](cap-and-preview-wrapper.md).
- **The sub-job needs an answer from the user in the middle**, for
  example a confirmation before a write. A call is one turn.
- **The main agent needs the details later**, not a summary. Then the
  results must be in its context.
- **The agent has few tools, or a strong model handles them.** The split
  adds a call and a hand-over, and it removes context.

## Alternatives

- [Remove or scope tools](remove-or-scope-tools.md), when some tools of
  the group are not used.
- [Consolidate tools](consolidate-tools.md), when the tools overlap and a
  few general wrappers can replace them.
- [Guide skill for a tool set](guide-skill-for-a-tool-set.md), when the
  tools are right and the agent does not know how to use them.
- [Long prompt into a skill](long-prompt-into-a-skill.md), when the
  problem is the length of the instructions, not the tools.

## How

The change is a new sub-agent (the lever
[sub_agent](../levers/sub_agent.md)), the tool list of the main agent
(the lever [tool_config](../levers/tool_config.md)) and its prompt (the
lever [prompt](../levers/prompt.md)). They are one arm.

1. **Choose the group** from the traces: the tools that are called
   together for one purpose.
2. **Create the sub-agent with those tools** on its run config, and with
   the instructions of the sub-job as its prompt. Write the prompt again
   so that it stands alone: the sub-agent does not see the main prompt.
3. **Write the input schema**: what the sub-job needs to start (an id, a
   question, a constraint).
4. **Fix the form of the result**:
   [fixed return form for a sub-agent](fixed-return-form-for-a-sub-agent.md).
5. **Use the main agent's model first.** The arm then measures the
   structure alone. A cheaper model for the sub-agent is a second arm.
6. **On the new run config of the main agent**: attach the sub-agent's
   tool, drop the tools of the group, remove the section of the prompt
   that the sub-agent took over, and add one line that says when to call
   it. A tool that stays on both sides gives the model a choice.

## Check that it took effect

In smoke drives on items that need the sub-job: `sub_agent_calls` is at
least one, the main agent's trace has no call of the group's tools, and
the returned text holds what the answer needs. The trace summary shows
fewer turns and smaller results in the main agent's context.

## Budget

High: a sub-agent, a prompt cut and a tool change in one arm, and the arm
is timed. Take it after the cheaper fixes of the tool layer, and when the
traces point at one clear sub-job.

## If it does not move the evals

It depends. Under a quality objective, it must move an eval: the split
adds a call, a cost and one more part to maintain. Under a cost or speed
objective, keep it when quality holds and the timed drives show the gain.

## Priors

None yet.

## Confidence

Low to medium. That a sub-agent keeps a clean context for the main agent
is the reason most public agent systems give for the pattern. Field
practice is not settled: with the strongest models, many teams now keep
everything in one context and compact it. No measurement of ours exists. The result depends on the model class and on
how clean the border of the sub-job is.
