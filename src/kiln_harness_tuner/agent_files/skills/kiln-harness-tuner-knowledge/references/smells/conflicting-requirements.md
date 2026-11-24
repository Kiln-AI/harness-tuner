---
title: Conflicting requirements
description: Two texts that the agent reads - the prompt, a skill, a tool description, a tool's response - ask for things that cannot both hold, and no text says which one wins.
reviewed: 2026-11-25
applies_to: all
detected_by:
  - agent
---

# Conflicting requirements

## What it is

Two texts that the agent reads ask for things that cannot both hold for
the same request. The texts are the prompt, the task's instruction, a
skill, a tool description and the response of a tool. No text says which
one wins. The agent then follows one of them, and not the same one on each
run. This is a property of the design, whether an eval passes or fails.

## How to recognise it

In the harness:

- two rules about the same case with different actions: the prompt asks
  for one sentence, and a skill asks for the full procedure;
- one limit, format or default with two values: the prompt says to
  confirm each write, and a tool description says that no confirmation is
  necessary;
- a tool response that tells the agent to do what the prompt forbids.

In how the agent runs:

- on requests of one kind, some traces follow one text and other traces
  follow the other text;
- the agent names both rules in its reasoning, or it changes its answer
  after a tool result.

## How to confirm it

- Quote the two passages, each with its place: `prompt`, the skill and its
  file, the tool and `description` or `response`.
- State one request for which the two cannot both hold. A general rule and
  its stated exception do not conflict.
- Read the sampled traces of that kind of request, and count the runs that
  follow each text. A split confirms that the agent chooses. With no trace
  of the case, report the smell from the texts and say so in the
  confidence.
- Say whether one text is clearly stale or secondary, and why: it names a
  tool, a field or a step that the run config does not have, or it is an
  example or a comment against an explicit rule.
- When neither text is clearly stale or secondary, only the owner can say
  which one wins. The reviewer returns a question for the owner, with the
  two requirements as its options.
- **Not this smell:** a text that states the precedence. A fault inside
  one skill, with no second text: that is
  [Faulty inherited skill](faulty-inherited-skill.md). A rule that
  disagrees with what an eval scores: the review does not judge answers.

## What it blocks

A wording experiment on that case: its result depends on which text the
agent followed. An experiment that chooses a side for the owner.

## Remedies

- [Make conflicting texts agree](../techniques/make-conflicting-texts-agree.md),
  when one text is clearly stale or secondary. The tuner can change that
  text with no answer from the owner.
- [Make conflicting texts agree](../techniques/make-conflicting-texts-agree.md),
  after the owner said which requirement wins, when neither text is
  clearly stale or secondary. The run asks the owner before the first
  wave, and the planner gets the answer in `owner_answers`. With no
  answer, both texts stay as they are.
