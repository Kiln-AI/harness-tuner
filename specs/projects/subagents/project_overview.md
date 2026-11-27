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
   sub-agent. Reason: no call chain can loop. A sub-agent that the user
   made can still have one, and the usage reader follows it three levels
   down.
6a. **Owner (2026-11-25): a sub-agent can attach an OpenEnv tool, and the
   drive route sees through sub-agents.** A general use of a sub-agent is
   a reader that gets data from the task's world at run time. An earlier
   version of this PR refused the tool, because the loop chose the route
   of a drive from the subject's tools only. Now `EvalJobDriver.needed`
   answers yes when a run config, or a sub-agent that it attaches, lists
   an OpenEnv tool. Such a run config is driven as an eval job on every
   item, like one that lists the tool itself. Section 5 has the Kiln facts
   and what stays open.
6b. **Owner (2026-11-25): a flag for write tools behind a new sub-agent.**
   The gate's integrity check (c) flags a kind of write action that is in
   the candidate's val traces and in none of the baseline's. It reads the
   tool calls of the run's own trace. A write that a sub-agent makes is in
   the sub-agent's trace, which an eval job does not save. So a candidate
   could move a write tool into a sub-agent, and (c) would not fire. It
   matters more since a sub-agent can hold a world's write tool. Check (e)
   reads, for each sub-agent that the candidate attaches and the baseline
   does not, the tools of the sub-agent's run config, and flags each one
   whose name names a write, with the write-verb match of (c). It is a
   flag, not a block, and it shows where the flag of (c) shows: on the
   candidate's `integrity_flags` at the promotion gate, on the `integrity`
   line of the log, and in the promotion's reasons, which the report
   carries.
7. **The model policy holds for the sub-agent.** Section 17 of the
   architecture says that the policy is about where the task's data is
   sent. A sub-agent's input is the task's data.
8. **Owner: `--models keep` keeps the sub-agent on the kept models.** Under
   the default of a quality run, a new sub-agent runs on the baseline's
   model, at any thinking level, or on the model of a sub-agent that the
   baseline attaches. So the techniques that need a second model need
   `--models any` or a policy file. The models of the baseline's
   sub-agents are `sub_agent_exceptions` of the policy: pairs that a
   sub-agent may use and the subject may not. (A self-review found that
   plain exceptions let the subject move to its sub-agent's model, which
   breaks the choice to keep it.)
9. **The integrity rules hold for the sub-agent's text.** Its tool
   description, its prompt, its output schema and the text of its own
   skills and code tools are read by the same checks as the subject's.
10. **Owner (2026-11-25): the cost of a sub-agent is added in Kiln, not in
    the tuner.** Kiln-AI/Kiln#2077 makes a run's usage hold the tokens and
    the cost of the sub-agents it calls. So the tuner keeps one source for
    cost, the `usage` it reads today, and it never adds a sub-agent's
    usage itself: a sum in both places would count it twice. A candidate
    that calls a sub-agent must not win on a number that leaves the
    sub-agent out. So its cost counts as measured for a run only when that
    run holds the sub-agent's usage, and its latency never counts as
    measured. Section 3 has the test and the guard. **The cost half
    depends on Kiln-AI/Kiln#2077 as written** (open, not merged, on
    2026-11-25). **Latency stays not measured** for a candidate that calls
    a sub-agent, with or without that change: this is open with the owner.
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

### Kiln-AI/Kiln#2077

Read from the pull request as written on 2026-11-25. A review can still
change it.

- A run's `usage` and `cumulative_usage` then hold the tokens and the
  cost of each sub-agent call. This holds in an eval job too, and it does
  not need a saved run of the sub-agent. A nested call is counted once.
- The sub-agent's usage is written as a `usage` key on the tool message of
  the parent's trace, the message that has `kiln_task_tool_data`. A run
  from a Kiln without the change has no such key. The pull request names
  this as the way a reader tells an old run from a new one, and Kiln's own
  Run page uses it.
- The places to read the full cost do not change: `task_run_usage.cost` of
  an eval result, and `usage.cost` of the task run of a drive. These are
  the places the tuner reads.
- **Latency is not changed.** `usage.total_llm_latency_ms` stays the time
  of the run's own model calls. Kiln stores no wall-clock time of a run,
  and tool calls run in parallel.
- Not covered: a sub-agent that a code tool calls adds no usage to the
  run, and a failed sub-agent call adds none.

### How the tuner knows which case it is in

Section 3 of an earlier version of this document had two candidates: a
signal that the Kiln change defines, and an arithmetic check on a drive.
The Kiln change defines a signal in the data, so the first is taken, as a
test **per run**, for **cost only**:

- `sub_agents.cost_measured(run)`: true when the run calls no sub-agent,
  and when every sub-agent tool message of its trace has a `usage` key.
  One message without the key, and the cost of that run is not measured.
- `sub_agents.latency_measured(run)`: true only when the run calls no
  sub-agent. No signal changes this.

The per-run test fits the design for four reasons. It needs no version
check. It works for a stored eval run and for a drive, because both carry
the trace. It is right for a project that holds runs from before and
after a Kiln upgrade. And it fails closed: a run with no key, a failed
sub-agent call, or a changed key name gives "not measured", never a cost
that leaves the sub-agent out. The arithmetic check is not used.

### The guard

A metric that is not measured is withheld, in code:

- A run config with a timing drive that calls a sub-agent has no latency
  from the timing drives, on any item. It has no cost either when the run
  of one such drive does not hold the sub-agent's usage. A drive with no
  trace, of a run config that attaches a sub-agent, is withheld too. The
  measurement engine, the gate's paired pass and the baseline timing all
  read those drives, so a candidate's comparison of a withheld metric is
  incomplete and gives no verdict.
- The withheld values of the baseline are a lower bound: a baseline that
  calls a sub-agent is cheaper and faster on paper than it is. So they
  stay as the reference side of a comparison, and a candidate that drops
  the sub-agent and still beats them wins. The record says so.
- Under `speed`, a candidate with no latency stops with the reason. Under
  `cost`, a candidate with no cost does. So a candidate that calls a
  sub-agent never wins on speed, and it wins on cost only on a Kiln with
  the change.
- The passive latency and cost of stored eval runs are left off the
  analysis table by the same two tests, applied to the candidate's runs.
- A train result row shows no latency, and no cost unless the run holds
  the sub-agent's usage.
- The record, the analyzer's input and the report say what was not
  measured.

The usage of the sub-agent's calls is shown for information: from the tool
message when Kiln wrote it, else from the sub-agent's saved run. It is in
no comparison and in no spend figure. The spend is what Kiln reports for
each run.

What stays open: an owner decision on latency; a sub-agent that a code
tool calls, which the guard cannot see; and the sub-agent's run that an
eval job does not save, which only the "for information" latency needs.
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
  line says "latency and cost not measured", or "latency not measured,
  cost includes the sub-agent", with the usage of the sub-agent's calls.
- The report has a `## Sub-agents (code-computed)` section, rendered by code: each created
  sub-agent with its tool id, task, run config, model, the run configs that
  attach it, and "in the final baseline" when the final baseline does.

## 5. OpenEnv tools on a sub-agent

Facts from a live probe by another session, on a Kiln server that reports
API version 1.2.0. The parent task's only tool was a `kiln_task::` tool.
The sub-agent's run config had two OpenEnv tools of a world, one read and
one write. The eval inputs carried an `openenv_reset`. A code scorer read
`openenv_episode["final_state"]`.

- **In an eval job** (3 items, concurrency 1): the sub-agent's read
  returned the data of the scenario that the eval input reset. On all 3
  items the scorer received an episode, and the final state showed the
  value that the sub-agent wrote. So a sub-agent's world calls run in the
  parent's episode, for reads and for writes.
- **Through the run endpoint**, with the same parent and no eval input:
  Kiln attempted an OpenEnv reset with no reset arguments, the world
  refused it, and the run failed with the error code
  `openenv_reset_rejected`.
- **Not tested**: a concurrency above 1 (whether the sub-agents of items
  that run at the same time keep their own episodes), and a sub-agent
  whose tool is a code tool that calls an OpenEnv tool.

What stays open:

- **A code tool's allowlist.** The route reads the tool list of a run
  config and of its sub-agents. It does not read the allowlist of a code
  tool. A code tool whose allowlist names an OpenEnv tool, on the subject
  or on a sub-agent, does not force the eval-job route. `create_sub_agent`
  does not read allowlists either. This is the behaviour of main for the
  subject, and the owner has not decided to change it.
- **What the write-tool flag cannot see** (decision 6b). A name match
  cannot prove that a tool writes, or that it does not. The flag does not
  read the code of a code tool, only its name and the names of its
  allowlist. It has no `method` or `action` argument to read, which the
  trace check (c) has. A tool that writes under a neutral name is not
  seen.
- **The statement "the run endpoint starts no OpenEnv episode".** Main has
  it in `job_drives.py` (the module text), in the text of `run_task` in
  `gateway/ops.py`, in `docs/architecture.md` section 16 and in
  `docs/your-own-task.md`. The probe above saw the run endpoint attempt a
  reset. This PR does not change those four places. They need a check
  against Kiln's source.
