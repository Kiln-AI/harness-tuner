---
title: Fixed return form for a sub-agent
description: Give a sub-agent an output schema, so the main agent gets the same named fields from each call - the result, the evidence, and what was not done or not found.
reviewed: 2026-11-25
status: active
levers:
  - sub_agent
applies_to: all
cost: low
---

# Fixed return form for a sub-agent

## When to use

- **A new sub-agent whose result the main agent acts on.** The main agent
  takes a value from the result, chooses a branch on it, or quotes it.
  The form is then part of the same build as the sub-agent.
- **The main agent misreads a report in free text.** The traces show that
  it takes a guess for a fact, does not see that the sub-agent found
  nothing, calls the sub-agent again for the same thing, or repeats a
  long report in its answer.
- **A sub-agent of the baseline returns free text**, and the failures are
  at the step after its call.

The main agent sees only what the sub-agent returns. It does not see how
the sub-agent worked. So the returned text is the whole interface between
the two.

## When not to use

- **The result is prose that the main agent passes on almost whole**, for
  example a drafted reply. A schema with one text field adds nothing.
  State the layout in the sub-agent's prompt.
- **The sub-agent's model does not give structured output reliably.**
  `get_model_details` gives `supports_structured_output` and the default
  output mode. A form that fails to parse fails the call. State the form
  in the sub-agent's prompt as a layout, and keep plain text.

## Alternatives

- [State the output contract](state-the-output-contract.md), when the
  form that fails is the main agent's own output, not the sub-agent's.
- [Directive in the tool response](directive-in-the-tool-response.md),
  when the main agent has the right result and states it wrongly: one
  line in the returned text that says what the result means.

## How

The change is the output schema of the sub-agent, set when the sub-agent
is created (the lever [sub_agent](../levers/sub_agent.md)). A sub-agent
is never changed: a new form is a new sub-agent.

1. **List what the main agent does with the result.** Read the main
   prompt and the traces: which value it needs next, and which decision
   it makes.
2. **Write the schema.** These fields are ideas, not a fixed list:
   - `status`: a small closed set, for example `done`, `partial`,
     `not_found`, `failed`. The main agent then does not read a failure
     as an answer.
   - The result fields, each named and typed for its use: an id, a
     count, a decision from a closed set, a short text.
   - `evidence`: the lines, the record ids or the sources that the result
     rests on. The main agent can quote them, and a reader of the trace
     can check them.
   - `not_done`: what the sub-agent could not do or did not check.
3. **Keep it small.** Each field goes into the main agent's context on
   each call. A field that the main agent does not use is removed.
4. **Name the fields in plain words.** The main model reads the returned
   JSON as text. A name such as `refund_allowed` needs no explanation.
5. **Say in the sub-agent's prompt what each field holds**, when to use
   each status, and how long a text field can be. Give a field a
   `description` in the schema too.
6. **Say in the main prompt how to act on each status.** For example:
   when `status` is `not_found`, say that the fact was not found, and do
   not guess.

## Check that it took effect

In a smoke drive on a train item, read the returned text in the main
agent's trace: it parses, and it has each field. Drive one item where the
sub-agent has nothing to find. The status is then not `done`, and the
main agent's answer says what was not found.

## Budget

Low when it is part of a new sub-agent: the same arm, a few more lines.
For a sub-agent that already exists, it is a new sub-agent and a new run
config: a medium arm of its own.

## If it does not move the evals

It depends. As part of a new sub-agent, keep it: it costs a few tokens on
each call, and it makes each trace possible to check. A rebuild of an
existing sub-agent only to add the form must move an eval.

## Priors

None yet.

## Confidence

Medium for the principle, low for the size of the effect. The principle
follows from how the object works: the returned text is the only thing
that the main agent receives. Public descriptions of systems with an
orchestrator and workers report vague hand-overs, in both directions, as
a frequent cause of failure. No measurement of ours exists. Which fields
help depends on the task.
