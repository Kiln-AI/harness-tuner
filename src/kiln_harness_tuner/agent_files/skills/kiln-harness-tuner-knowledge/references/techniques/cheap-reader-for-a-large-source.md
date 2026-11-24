---
title: Cheap reader for a large source
description: A sub-agent on a cheap model reads a large source - a long document, a large search or tool result - and returns only the lines that the main agent needs.
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

# Cheap reader for a large source

## When to use

All of these hold:

- **The agent reads a large source to use a small part of it**: a manual,
  a long record, many search results. In the traces, most input tokens of
  the main agent are source text, and each large result stays in its
  context for the rest of the run.
- **To find the part takes reading and judgment.** Which lines matter
  depends on the request, so code cannot select them by a field, a limit
  or a keyword.
- **The source is behind a tool, a RAG search tool or a skill.** The
  sub-agent can then get it without the main agent.
- **The run allows a cheap model that reads well**, with a context window
  that holds the source.

The main agent pays for its own model on each token of the source, again
on each later call. A reader on a cheap model pays less for each token,
and its context ends with its call.

## When not to use

- **The selection is mechanical.** A wrapper in code has no model cost:
  [cap and preview wrapper](cap-and-preview-wrapper.md).
- **The source arrives in the task's input.** The main agent has then
  read it already. To pass it to a sub-agent, the main model writes it
  again as an argument, which costs more than it saves.
- **The answer needs the exact words or numbers in their context.** A
  digest can drop or change them. A reader that returns quotes reduces
  the risk and does not remove it.
- **The document is stable and fits in a skill.** The agent then reads
  only the file it needs:
  [document into a skill](document-into-a-skill.md).
- **The main agent runs on a model with a large context, and the cost
  goal does not ask for the change.** One context is simpler, and it
  loses nothing at a hand-over.

## Alternatives

- [Cap and preview wrapper](cap-and-preview-wrapper.md), when code can
  select the part.
- [Document into a skill](document-into-a-skill.md), when the source is a
  stable document of a size that a skill holds.
- [Roll-up tool](roll-up-tool.md), when the large context comes from many
  small calls, not from one large source.

## How

The change is a new sub-agent (the lever
[sub_agent](../levers/sub_agent.md)), the tool list of the main agent
(the lever [tool_config](../levers/tool_config.md)) and its prompt (the
lever [prompt](../levers/prompt.md)). They are one arm.

1. **Give the source to the reader, not to the main agent.** The
   sub-agent's run config attaches the tool that returns the large
   result, the RAG search tool, or the skill that holds the document. The
   main agent's new run config drops it. The main agent passes the
   question and what it knows (an id, a name), never the source.
2. **Write the reader's prompt**: read the source, answer only from it,
   quote the lines that the answer rests on, and say so when the source
   does not hold the answer.
3. **Fix the form of the result**:
   [fixed return form for a sub-agent](fixed-return-form-for-a-sub-agent.md).
   Useful fields here: the answer, the quoted lines with their place in
   the source, and a status for "not found".
4. **Choose the cheapest allowed model that reads reliably.**
   `get_model_details` gives its context window and its price.
5. **Tell the main agent when to call it**, in one line of the prompt and
   in the tool description. Tell it not to guess when the reader found
   nothing.

## Check that it took effect

In smoke drives on items that need the source: `sub_agent_calls` is at
least one, the main agent's trace no longer holds the large result, and
the returned text holds the fact that the answer needs. Compare the
`usage` of the drive with the parent's on the same item: it includes the
reader's calls.

## Budget

High to build, and the arm is timed. Under a cost objective it is a good
candidate when the traces show that source text is most of the input. On
a small quality budget, leave it out.

## If it does not move the evals

It depends. Under a cost or speed objective, keep it when quality holds
and the timed drives show a lower cost. Under a quality objective it must
move an eval, for example fewer failures after a long read.

## Priors

None yet.

## Confidence

Low to medium. The saving follows from the prices: fewer tokens on the
main model, and the same tokens once on a cheaper model. How much quality
it keeps is the open question, because a digest loses detail. Public
coding agents use this pattern for search and for reading files. No
measurement of ours exists.
