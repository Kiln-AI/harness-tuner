---
title: Make conflicting texts agree
description: Settle a conflict between two texts that the agent reads - change the text that loses, so that the requirement is stated in one place - when it is known which text wins.
reviewed: 2026-11-25
status: active
levers:
  - prompt
  - skill
  - new_tool
applies_to: all
cost: low
---

# Make conflicting texts agree

## When to use

The smell
[Conflicting requirements](../smells/conflicting-requirements.md) is
confirmed, and it is known which text wins. One of these conditions tells
you:

- **One text is clearly stale.** It names a tool, a field or a step that
  the run config does not have, or the other text replaces it by name.
- **One text is clearly secondary.** It is an example, a comment or a
  default in a tool description, against an explicit rule of the prompt or
  of the task's instruction.
- **The owner said which one wins.** The answer is in the planner's input
  `owner_answers`.

It helps most when the traces show the split: on requests of one kind,
some runs follow one text and some follow the other.

## When not to use

- **Neither text is clearly stale or secondary, and the owner did not
  answer.** The choice is then the owner's policy. Do not choose. The run
  asks the owner before the first wave. With no answer, both texts stay,
  and the report lists the question. An arm that keeps the side with the
  higher train score makes an eval decide the owner's policy.
- **The two texts apply to different cases.** A general rule and its
  stated exception do not conflict.
- **The requirement that loses is what an eval scores.** Then the problem
  is in the eval or in the choice of the winner, not in the text.

## Alternatives

- [Rewrite an inherited skill](rewrite-an-inherited-skill.md), when the
  fault is inside one skill and no second text is part of it.
- [Prune one group at a time](prune-one-group-at-a-time.md), when it is
  not known if the text that loses helps at all: drop it on one arm and
  measure it.
- [Guard tool calls at the boundary](guard-tool-calls-at-the-boundary.md),
  when the requirement that wins is a rule that the product enforces. A
  check in a wrapper holds the rule, whichever text the agent follows.

## How

The change is on the text that loses: an edit of the
[prompt](../levers/prompt.md), a new version of a
[skill](../levers/skill.md), or a [new tool](../levers/new_tool.md) that
clones or wraps the tool whose description or response loses.

1. **Write down the requirement that wins, and its source**: the text that
   states it, or the owner's answer. An owner's answer is the owner's
   policy. State it in the harness in the owner's sense, and name the
   answer as the source of the rule.
2. **Change the text that loses.** Remove the sentence, or rewrite it so
   that it agrees. Do not keep both sentences and add a third that says
   "ignore the rule above": the agent then reads three texts.
   - In the prompt: an edit of the prompt.
   - In a skill: a new version of the skill.
     [Rewrite an inherited skill](rewrite-an-inherited-skill.md) has the
     steps.
   - In a tool description:
     [New tool description](new-tool-description.md) has the steps.
   - In a tool response: a wrapper that removes or rewrites the sentence.
     [Directive in the tool response](directive-in-the-tool-response.md)
     has the steps.
3. **State the requirement in one place.** When two texts must name it,
   one states the rule and the other points to it.
4. **When the text that loses cannot be changed**, state the precedence in
   the prompt, in one sentence that names the case. This is the weaker
   form: the agent still reads both texts.
5. **Search for a third text** that states the same requirement: the other
   skills and the other tool descriptions.
6. **Settle one conflict on each arm**, so that the effect of each one can
   be read.

## Check that it took effect

Read the diff: the sentence that loses is gone, or it agrees. Then smoke
on two or three train items of the kind of request where the texts
conflicted. Each trace follows the requirement that wins. Add one item of
another kind that passed before.

## Budget

Low cost. It is a good first step when the traces confirm the split: a
later wording arm on the same case measures chance until the conflict is
settled. When no train item holds the case, no eval can show the effect.
The change is still small, and the next section applies.

## If it does not move the evals

Keep it. When the conflict was confirmed and no eval fell, the harness
states the requirement one time, and the behaviour on that case does not
depend on chance. When an eval fell, read the failing traces: the text
that lost carried something that the agent needs, or the winner was the
wrong choice. When the tuner chose the winner with no answer from the
owner, record the question for the owner.

## Priors

None yet.

## Confidence

Medium for the direction, low for the size. The basis is field knowledge,
and the public research on instruction files that
[One general rule per edit](one-general-rule-per-edit.md) names: rules
that contradict each other lower how well a model follows the file. No
measured case of this technique is recorded here. The size of the effect
depends on how often the case occurs in the evals.
