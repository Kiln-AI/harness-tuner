---
title: Very long prompt
description: The system prompt is longer than the reference length. A code detector measures it, and the detection gives the length, the reference and its source.
reviewed: 2026-11-13
applies_to: all
detected_by:
  - code
detectors:
  - long_prompt
---

# Very long prompt

## What it is

The system prompt is longer than the reference length. The model reads all
of it on every call, whether the request needs a part or not. It costs
tokens on every call, and in a long text the model can miss a rule that
the request needs. How much it misses depends on the model and on how the
text is structured. This is a property of the design, whether an eval
passes or fails.

## How to recognise it

In the harness:

- the code detection in your input gives the length of the prompt and the
  reference it was compared with;
- the prompt holds many topics, for example the rules of each thing that
  the agent can do, and each request needs only one or a few of them.

In how the agent runs:

- the prompt is most of the input of each call, whatever the request;
- the requests are short, and each of them is about one topic of the
  prompt.

## How to confirm it

- Read the prompt. Count its topics, and estimate how many of them one
  request needs.
- The reference is a default, not a measured optimum. Read the detection's
  source before you state how sure you are.
- **Not this smell:** a long prompt that is one topic, which every request
  needs in full, for example a rubric or a policy that applies to each
  item. That prompt can still be hard to follow: its structure is the
  question, not its length.

## What it blocks

A new rule added to the prompt. The model can miss one more line in a long
text, so a result that does not move the eval says little about the rule.

## Remedies

- [Long prompt into a skill](../techniques/long-prompt-into-a-skill.md),
  when the model reads skills and each request needs only some of the
  topics.
- [Structure a long prompt](../techniques/structure-a-long-prompt.md),
  when the task has no tools, the model does not read skills, or most
  requests need most of the content.
- [Prune one group at a time](../techniques/prune-one-group-at-a-time.md),
  when the prompt holds sections that may not help.
- [Lost in long instructions](../playbooks/lost-in-long-instructions.md),
  when the traces show the agent miss parts of the prompt.
