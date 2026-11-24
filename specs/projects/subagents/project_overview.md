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
6. **Owner: one level.** A sub-agent that the loop creates cannot attach a
   sub-agent. Reason: the cost of a call is then one run to read, and no
   call chain can loop. A sub-agent that the user made can still have one,
   and the usage reader follows it three levels down.
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
10. **Kiln's usage numbers do not include a sub-agent's calls. The loop
    adds them where a record exists.** Section 3 has the finding.
11. **An arm that adds, drops or replaces a sub-agent is timed.** Its
    latency and cost then come from drives that include the sub-agent's
    calls, and the lever check reads the same drives. The cost is the
    timing drives of that arm.
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

So the answer to "do Kiln's usage numbers include the sub-agent's calls"
is no. The loop can add them for a drive through the run endpoint. It
cannot for an eval job. The spend of eval jobs, the passive latency and
cost of stored eval runs, and the drives of a multi-turn or OpenEnv task
leave them out. The backlog has the Kiln-side entry.

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
  of M drives", and the wave log has a `lever_applied` event.
- The report has a `## Sub-agents` section, rendered by code: each created
  sub-agent with its tool id, task, run config, model, the run configs that
  attach it, and "in the final baseline" when the final baseline does.
