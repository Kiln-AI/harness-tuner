---
title: Deep work in a stronger sub-agent
description: A fast, cheap main agent hands one hard, well-scoped part of the work to a sub-agent on a stronger model or a higher thinking level, and gets a short report back.
reviewed: 2026-11-25
status: active
levers:
  - sub_agent
  - prompt
applies_to: all
cost: high
---

# Deep work in a stronger sub-agent

## When to use

All of these hold:

- **The main agent runs on a fast or cheap model, and that fits most of
  each request**: a conversation, simple lookups, a short answer.
- **A small part of some requests needs deep reasoning**: a diagnosis, a
  plan, a calculation over many facts, a hard judgment. The traces show
  failures on that part and passes on the rest.
- **More reasoning fixes that part.** A probe with a stronger model, or
  with a higher thinking level, on the failing items shows the fix. But
  on the whole agent it costs too much or is too slow for the goal.
- **The hard part can be stated as one question with its facts**, and its
  answer is short.
- **The run allows it.** `allowed_models` lists a stronger model. When
  the run keeps the baseline's model, the sub-agent can run on that model
  at a higher thinking level.

When the main agent raises its own model or thinking level for one hard
step, the whole context is paid at that level from then on. A sub-agent
does the hard step in a context of its own, and only its report comes
back.

## When not to use

- **The main agent already runs on a strong model.** A sub-agent then
  adds a call and removes context. A strong model that keeps everything
  in one context often does better than the same model split in two.
- **The hard part needs the whole conversation, or many earlier tool
  results.** The sub-agent sees only the arguments of the call. The main
  model must write each fact again, which costs output tokens and loses
  detail.
- **The work needs more than one turn** with the user or with the main
  agent. A call is one turn, and a second call starts from nothing.
- **Most requests need the deep part.** Then the model or the thinking
  level of the whole agent is the smaller change.
- **The latency goal is tight on each request.** The sub-agent's whole
  run is added to the request each time it is called.

## Alternatives

- [Thinking level ladder](thinking-level-ladder.md), when the hard part
  is most of each request, or when one level up on the whole agent is
  inside the cost goal.
- [Model swap inside a class](model-swap-inside-a-class.md), when a
  stronger model on the whole agent is inside the cost goal.
- [Policy as step-by-step procedures](policy-as-step-by-step-procedures.md),
  when the hard part is a rule of the business that the main model
  follows once it is written as a procedure.

## How

The change is a new sub-agent (the lever
[sub_agent](../levers/sub_agent.md)) and one rule in the main agent's
prompt (the lever [prompt](../levers/prompt.md)). They are one arm.

1. **Name the hard part.** From the failing traces: the question that the
   main agent got wrong, and the facts that the right answer needs.
2. **Write the input schema.** One field for the question, and one for
   each kind of fact. A field with a `description` tells the main model
   what to write there.
3. **Write the sub-agent's prompt**: its role, the method, and what to
   return. State the method in general terms. It serves every future
   request, not the items in the traces.
4. **Fix the form of the report**:
   [fixed return form for a sub-agent](fixed-return-form-for-a-sub-agent.md).
   A short report keeps the gain: a long one fills the main context again.
5. **Choose the model.** The weakest setting that fixed the failing items
   in the probe: first a higher thinking level on the same model, then a
   stronger model.
6. **Tell the main agent when to call it.** One rule in the main prompt,
   stated as a property of the request, and a tool description that says
   what the sub-agent does. Say to pass every fact that the question
   needs.

## Check that it took effect

In smoke drives, `run_task` gives `sub_agent_calls`: at least one on an
item that needs the hard part, and none on an item that does not. Read
the call's arguments in the trace: they hold the facts that the answer
needs. Code checks the arm too: a sub-agent that no timed drive calls
ends the arm as `lever not applied`.

## Budget

High: two prompts and two schemas to build, and the arm gets timing
drives. On a small budget, leave it out unless a probe already
showed that more reasoning fixes the failing items.

## If it does not move the evals

It must move an eval. A sub-agent adds cost and latency to each request
that calls it, and one more part to maintain. Under a cost or speed
objective, the measure it must move is the objective: quality holds, and
the loop measures a lower cost for the whole request, sub-agent included.
When the loop does not measure that cost (the limits of the lever), this
arm cannot win there.

## Priors

None yet.

## Confidence

Low to medium. The pattern is common in public agent systems: an
orchestrator that hands work to workers, and a cheap model that asks a
stronger model for advice through a tool. No measurement of ours exists.
The usual failure is at the hand-over: the main agent writes too little
into the call, and the sub-agent answers a different question. So the
input schema and the rule in the main prompt matter as much as the
sub-agent's model.
