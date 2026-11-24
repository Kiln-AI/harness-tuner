---
status: complete
---

# Sub-agents

This project makes sub-agents a lever of Harness Tuner. It closes gap 2 of
[kiln_gaps.md](../biological_bootloader/kiln_gaps.md) and the entry
"Sub-agents" of the [backlog](../../backlog.md).

"Sub-agent" here means a sub-agent of the agent under tuning: another Kiln
task that the subject task calls as a tool (`kiln_task::<tool_server_id>`).
It does not mean a sub-agent of a loop session.

## 1. The brief (2026-11-25)

### Why sub-agents

The owner's reasons, from a draft note:

> Context management. A subagent does a piece of work in its own context,
> and only its result comes back. Encapsulation. A more complex,
> well-scoped task goes to a subagent. Cost. Sometimes a cheaper model can
> do a simple thing. The bigger, more expensive model does not need to do
> it.

Section 7.2 of the
[biological_bootloader overview](../biological_bootloader/project_overview.md)
gives the same picture, with the limits: one turn only, no state between
calls, and no universal fix, because a strong model can keep everything in
one context.

### What existed

- Kiln can attach a Kiln task as a tool. The Kiln API has the routes to
  create the task, the run config and the tool.
- The tuner could read a Kiln task tool (`get_kiln_task_tool`) and attach
  one that existed. A loop agent could not create one. The gateway is
  bound to one task.
- No document told the planner when to use a sub-agent.

### What the brief asked for

In this order:

1. **The capability.** The operations that a build agent needs to create a
   sub-agent and attach it on a new run config, with the rules around them:
   the model policy, the integrity checks, the cost and latency of a
   request, a check that the lever was applied, and what happens to the
   created records.
2. **The knowledge.** A lever document, the first techniques, the return
   contract, the limits, and the links from the playbooks and the smells.
3. **The docs and this record.**

## 2. Decisions

Each decision is built as stated. The decisions marked **Owner** are
choices that the owner can change: each one is the conservative choice.

1. **One operation creates a sub-agent.** `create_sub_agent` makes the
   task, one run config and the tool in one call, and every check runs
   before the first write. Only the build agent has it. Reason: three
   separate operations would let an agent leave a task with no tool, or
   write to a task that the run does not own.
2. **The gateway stays bound to the subject task.** The new task is a
   record that the run created, like a code tool. No operation reads its
   runs or its evals. The loop's code reads one thing from it: the `usage`
   of a run that a trace of the subject names.
3. **A sub-agent never changes.** The tool pins its run config. A new
   prompt, model or schema is a new sub-agent on a new run config of the
   subject. Reason: the baseline or another experiment can attach the old
   one, and a change in place would change what was measured.
4. **Owner: create only, no evals for the sub-agent.** The loop measures a
   sub-agent on the subject's evals. It builds no eval and no dataset for
   the sub-agent's task. The overview (7.2) describes the next step: write
   evals for a critical sub-agent, tune it, then tune the main agent. That
   needs a second train set and a second gateway scope. It is in the
   backlog.
5. **Owner: each variant is a new task.** A second version of a sub-agent
   is a second task, not a second run config on the first task. Reason: it
   needs no rule on which tasks an agent may write to. The cost is one more
   task in the project for each variant. A version operation is in the
   backlog.
6. **Owner: one level, and no OpenEnv tool.** A sub-agent that the loop
   creates cannot attach a sub-agent. Reason: no call chain can loop. A
   sub-agent that the user made can still have one, and the usage reader
   follows it three levels down. It cannot attach an OpenEnv tool either:
   the loop chooses the route of a drive from the subject's tools, and the
   run endpoint starts no episode. A probe showed that a sub-agent's
   OpenEnv tool works inside an eval job. Whether its writes reach the
   parent's episode is not verified.
7. **The model policy holds for the sub-agent.** Section 17 of the
   architecture says that the policy is about where the task's data is
   sent. A sub-agent's input is the task's data.
8. **Owner: `--models keep` keeps the sub-agent on the kept models.** Under
   the default of a quality run, a new sub-agent runs on the baseline's
   model, at any thinking level, or on the model of a sub-agent that the
   baseline attaches. So the techniques that need a second model need
   `--models any` or a policy file. The other choice was a separate list of
   models for sub-agents. It was not taken: one rule for one question.
9. **The integrity rules hold for the sub-agent's text.** Its tool
   description, its prompt, its output schema and the text of its own
   skills and code tools are read by the same checks as the subject's.
10. **Owner (2026-11-25): the cost of a sub-agent is added in Kiln, not in
    the tuner.** Kiln's usage numbers do not include a sub-agent's calls
    today (section 3). A Kiln change will make a run's usage hold the
    usage of the sub-agents it calls. So the tuner keeps one source for
    cost and latency, the `usage` it reads today, and it never adds a
    sub-agent's usage itself: a sum in both places would count it twice.
    Until the tuner can tell that the server has the fix, a candidate that
    calls a sub-agent cannot win on cost or on speed: its cost and latency
    are "not measured", in code. Section 3 has the guard and the switch.
11. **An arm that adds, drops or replaces a sub-agent gets timing
    drives**, under every objective. The lever check reads their traces
    before a judge is paid for, and they give the usage of the sub-agent's
    own runs, for information. The cost is the timing drives of that arm.
12. **The lever check is a call in a trace.** A new sub-agent that no timed
    drive calls ends the arm as "lever not applied".
13. **The run deletes nothing.** At the end of a run, code archives the
    tool of a sub-agent that no run config attaches. Every other sub-agent
    stays, and the report lists each one. Reason: a tool that a measured
    run config attaches must still run when a later run reuses that run
    config or merges it.
14. **Not in this project.** Promotion on a tie
    ([tie-promotion](../tie-promotion/project_overview.md)), which uses a
    sub-agent as an example. A probe that creates a sub-agent. A read of
    the sub-agent's own trace through the gateway.

## 3. The finding on cost and latency

Read in the Kiln library, version 1.1.2
(`kiln_ai/tools/kiln_task_tool.py`,
`kiln_ai/adapters/model_adapters/litellm_adapter.py`,
`kiln_ai/adapters/eval/eval_runner.py`):

- The `usage` of a task run sums the model calls of that run. The tool
  result of a Kiln task tool carries the sub-agent's output and a
  reference to its run. It carries no usage.
- The sub-agent's run is a task run of the sub-agent's task, tagged
  `tool_call`, with its own `usage`. The tool message of the parent's
  trace names it: `kiln_task_tool_data` is
  `<project id>:::<tool id>:::<task id>:::<run id>`.
- Kiln saves the sub-agent's run only when it saves the parent's run. The
  run endpoint saves. An eval job runs the task with saving off and saves
  the trace itself, so the sub-agent's run is not saved and its run id is
  `None`.

A live probe by another session, on a Kiln server that reports API
version 1.2.0, gave the same facts: the parent's `usage` and
`cumulative_usage` hold only the parent's own model calls; a run through
the run endpoint saves each sub-agent call as a task run of the
sub-agent's task, tagged `tool_call`; an eval job saves none, and the
tool message ends with `:::None`.

So the answer to "do Kiln's usage numbers include the sub-agent's calls"
is no. The latency is not there either: the tuner's latency is
`usage.total_llm_latency_ms`, the time of the run's own model calls, not
the wall-clock time of the request.

### The guard

While a run's usage leaves the sub-agent out, the cost and the latency of
a request that calls a sub-agent are not measured, in code:

- A run config with a timing drive that calls a sub-agent has no latency
  and no cost from the timing drives, on any item. The measurement engine,
  the gate's paired pass and the baseline timing all read those drives, so
  each comparison with that run config is incomplete and gives no verdict.
  Under `speed` and `cost` the candidate stops with the reason.
- The passive latency and cost of stored eval runs are left off the
  analysis table when a run of either side calls a sub-agent.
- A train result row whose run calls a sub-agent shows no cost and no
  latency to an agent.
- The record, the analyzer's input and the report say "not measured".

The usage of the sub-agent's own runs is read from the drives through the
run endpoint and shown for information. It is in no comparison and in no
spend figure. The spend of a run leaves the sub-agents' calls out.

### The switch: how the tuner knows which case it is in

It cannot tell today. The tuner has no Kiln version check, and no route or
schema field of the server says whether a run's usage holds its
sub-agents' usage. So the guard is on for every Kiln.

One function is the switch: `sub_agents.usage_includes_sub_agents()`. It
returns False. Each guard above reads it, and the tests run both cases.
When the Kiln change names a signal that a client can read, that function
reads it. **This is the owner's decision to make**, with two candidates:

1. The signal that the Kiln change defines, for example a field in the
   OpenAPI schema. The tuner already reads the OpenAPI for one such
   question (judge-free eval jobs).
2. A check on the data, with no version: on a drive through the run
   endpoint, the run's `usage.cost` against the sum of the cost of its own
   model calls (the `usage` of each assistant message of the trace) and
   the cost of the saved sub-agent runs. It needs no change in Kiln, but
   it depends on fields that the Kiln change can also move.

What stays open after the Kiln change: the switch above; whether the
latency of a run then holds the sub-agent's time; and the sub-agent's run
that an eval job does not save, which the "for information" line needs.
The backlog has one entry for these.

## 4. What a finished run shows

For a run that used the lever:

- The experiment's `technique` is `sub_agent`, in the wave log
  (`wave_start.items`) and on the experiment's memory record.
- The run's `gateway_created.jsonl` names the task, the run config and the
  tool, with the kinds `sub_agent_task`, `sub_agent_run_config` and
  `sub_agent_tool`.
- The promoted run config has the tool id `kiln_task::<id>` in
  `tools_config.tools`.
- The experiment's record has the line "sub-agent tool `<id>` called in N
  of M drives", and the wave log has a `lever_applied` event. The same
  line says "cost and latency not measured", with the usage of the
  sub-agent's own runs.
- The report has a `## Sub-agents` section, rendered by code: each created
  sub-agent with its tool id, task, run config, model, the run configs that
  attach it, and "in the final baseline" when the final baseline does.
