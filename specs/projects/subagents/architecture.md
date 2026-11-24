---
status: complete
---

# Architecture: Sub-agents

Read [functional_spec.md](./functional_spec.md) first. This document says
where each part is. Section numbers in the form "FS 5" point to the
functional spec. Section 19 of
[docs/architecture.md](../../../docs/architecture.md) describes the code as
it is.

## 1. Overview

```
build session (create_experiment_rc)
  └─ gateway create_sub_agent        POST tasks, run_configs, kiln_task_tool
       └─ State.record               gateway_created.jsonl, 3 rows
  └─ gateway create_run_config       _sub_agent_check: tool, task, policy
  └─ gateway run_task                sub_agent_calls, sub_agent_usage (beside usage)

item path (items.py)
  └─ generality, judge_copy          prompt_text.rc_texts → RcText.sub_agents
  └─ rc_guard → policy_refusal       rc_sub_agent_pairs
  └─ _measure_arm                    timing = … or _changes_sub_agents
       └─ latency.timed_drives       latency_measured, cost_measured per drive
       └─ levers.lever_verdict       sub_agent_calls in each drive's evidence

end of run (orchestrator._orchestrate)
  └─ close_sub_agents                registry → attached_by → archive
       └─ "sub_agents" event         status.RunStatus.sub_agents
  └─ report                          render_sub_agents_appendix
```

## 2. The module `sub_agents.py`

One module with no import of the gateway or the orchestrator. Both use it.

| Name | What it does |
|---|---|
| `sub_agent_tool_ids(props)` | The `kiln_task::` ids that run-config properties attach. |
| `read_sub_agent(call, project_id, tool_id)` | The tool server's saved record (`…/tool_servers/<id>/config`), the task and its run configs. Returns `SubAgent`, or None for another kind of server. Never reads runs or evals. |
| `SubAgent.pair`, `.prompt_reads_runs` | The (provider, model) of the pinned run config. Whether its prompt is a generator that reads stored runs. |
| `sub_agent_calls(run)`, `calls_by_tool(run)` | The calls in a trace, from the tool messages' `kiln_task_tool_data`. None when the run has no trace. |
| `latency_measured(run)`, `cost_measured(run)` | The two tests of the guard, for a run or a stored eval-run row. Cost: every sub-agent tool message has a `usage` key. Latency: the run calls no sub-agent. |
| `read_sub_agent_usage(call, project_id, run)` | The usage of the sub-agent calls of one task run, as a `SubAgentUsage` (calls, unread, cost, latency, usage, in_run_usage): from the tool message, else from the saved run. For information: it is never added to a run's usage. |
| `usage_line(infos, drives)`, `not_measured_note(who, metrics)`, `LATENCY_NOT_MEASURED`, `COST_NOT_MEASURED` | The lines for a record. |
| `created_sub_agents(run_dir)` | The sub-agents in the run's registry, as `CreatedSubAgent`. |

The registry constants (`CREATED_FILE`, the three kinds) and the list of
prompts that read no stored run (`RUN_FREE_PROMPT_*`) are here. The
gateway imports them.

## 3. The gateway (FS 3, FS 4)

- `gateway/models.py`: `SubAgentCreate` (the request, with two validators:
  the tool name, and the object schemas), `SubAgentCreated` (the answer),
  three fields on `TaskRunView` (`sub_agent_calls`, `sub_agent_usage`,
  `sub_agent_note`) and `TrainResultRow.usage_note`.
- `gateway/ops.py`:
  - `Gateway.create_sub_agent`: the checks, then the three writes.
    `SUB_AGENT_REFUSED_TOOL_PREFIXES` lists what its run config cannot
    attach. An OpenEnv tool is not in it.
  - `Gateway._sub_agent_check`: the rules for an attached tool. Called by
    `create_run_config` and `run_task`.
  - `Gateway._driven_view`: the view of a drive, with the sub-agents'
    own usage beside the run's. Called by both routes of `run_task`.
  - `Gateway._result_row`: no latency for a row whose run calls a
    sub-agent, and no cost unless the run holds the sub-agent's usage.
  - `get_kiln_task_tool` uses `read_sub_agent`. Its answer did not change.
  - `OPS` and `ROLE_OPS`: the operation, for `create_experiment_rc`.
- A registry row is `{id, kind, split, eval_id}`. For a sub-agent the id
  is `<kind>:<record id>`, the split is `meta`, and `eval_id` holds the
  task id for the run config and the tool. `created_train` reads rows of
  the split `train` only, so no sub-agent row is a train record.

`job_drives.EvalJobDriver.sub_agent_lists_openenv_tool` reads the
sub-agents of a run config with `read_sub_agent`, and `needed` calls it
after its three cheaper tests. The answer for a tool id is kept on the
driver: a tool pins its run config. The three callers of `needed` did not
change.

## 4. The model policy (FS 5)

- `orchestrator.rc_sub_agent_pairs(ctx, rc_id)`: tool id to pair. Two
  caches on `Ctx`: the tool ids of each run config (filled with
  `_rc_pairs`, cleared with it) and the pair of each tool (never cleared:
  a tool pins its run config).
- `orchestrator.policy_refusal`: the run config's own pair, then each
  sub-agent's.
- `orchestrator.pin_baseline_model` and `model_policy.keep_policy`: the
  baseline's sub-agents' pairs as exceptions.

## 5. The integrity checks (FS 6)

- `prompt_text.py`: `SubAgentText` (a bundle with `tool_id`, `name`,
  `files`), `read_sub_agent_text`, the fields `RcText.sub_agent_ids` and
  `RcText.sub_agents`, `sub_agent_texts(rc)`, and one more kind in
  `whole_texts`: "sub-agent `<name>`, `<file>`". `rc_prompt_text` takes a
  `task_id`, to render a prompt of another task.
- `generality.py`: `sub_agent_changes`, with the same `_changes` as the
  skills and the code tools; the third list of `_whole_blocks`; the
  "could not be read" notes.
- `judge_copy.py`: the note for unread sub-agent text. The matcher reads
  `whole_texts`, so it needed no other change.
- `schemas.GeneralityReviewInput.sub_agent_changes`, and the template
  `agents/generality_review.md`.

## 6. Usage, timing and the lever check (FS 7, FS 8)

- `latency.timed_drives`: for each returned run, `read_sub_agent_usage`.
  A drive with a sub-agent call keeps its output, its evidence and its
  cost, and gets no latency. After the pass, a run config with one such
  drive has its latencies cleared, and `LATENCY_NOT_MEASURED` in
  `failures` for each item. Its costs are cleared too when one such run
  does not hold the sub-agent's usage. `DrivePass.unmeasured_latency` and
  `.unmeasured_cost` name those run configs, and `DrivePass.sub_agents`
  holds the `SubAgentUsage` of each drive with a call. The withheld values
  are kept in `DrivePass.lower_latencies` and `.lower_costs`, and
  `reference_latencies` / `reference_costs` give them for the reference
  side of a comparison: the gate's `paired_drive_pass` and the engine use
  them for the baseline, never for a candidate.
- `measure.py`: two sets of withheld run configs for the whole
  measurement. After each timing chunk, the withheld values of a side are
  cleared, and `Evidence.unmeasured[metric]` holds the reason when the
  side is the candidate. A withheld reference is not cleared: its lower
  bound stays, and `lower_bound_note` goes on the measured line. `Measured.latency` and `.cost`
  are built only for a metric that is measured. `Measured.usage_note` is
  `usage_line(…)`. An applied verdict with a reason becomes the
  `lever_note` and a `lever_applied` event.
- `stop_rules._not_better`: when the objective's metric is in
  `Evidence.unmeasured`, the rule stops the candidate with that reason.
- `analysis.py`: the passive `latency` of a comparison is None when a run
  of either side fails `latency_measured`, and the passive `cost` when one
  fails `cost_measured`.
- `levers.py`: `lever_evidence` adds `sub_agent_calls`. `lever_verdict`
  adds the sub-agent rule after the model rule. `MIN_SUB_AGENT_DRIVES` is
  3.
- `items.py`: `_changes_sub_agents` joins `_is_model_swap` in the `timing`
  flag of `_measure_arm`. The analyzer's notes are the reasons in
  `Evidence.unmeasured`; `SUB_AGENT_NOT_MEASURED_NOTE` is the line for an
  arm with no timing drives. `schemas.AnalysisInput.measurement_notes`
  carries it to the analyzer.

## 6a. The write-tool flag (FS 8a)

- `integrity.py`: `tool_name_of_id`, `write_named` (the regex of check
  (c)) and `sub_agent_write_findings`, all pure.
- `orchestrator.callable_tool_names`: the names behind a list of tool ids,
  with `prompt_text.read_code_tool` (which now gives a code tool's
  `allowlist`) and `read_sub_agent`. `WRITE_FLAG_DEPTH` is 3.
- `orchestrator.sub_agent_write_flags`: called in `apply_integrity_checks`
  for each candidate, after checks (b) and (c).

## 7. The end of a run (FS 9)

- `orchestrator.close_sub_agents`: called in `_orchestrate` before
  `write_report`, on the normal exit. Best-effort: a failed read or a
  failed archive is logged and never stops the run.
- `schemas.SubAgentRecord` and `RunStatus.sub_agents`;
  `status.RunStatusTracker._on_sub_agents`.
- `report.render_sub_agents_appendix`, appended after the design review
  section.

## 8. Tests

| File | What it covers |
|---|---|
| `tests/test_sub_agents.py` | The reader, the calls and the usage, the lever rule, the timing flag, the policy in the loop, the text in the generality check, the judge-copy check and the baseline audit, the registry, the end of a run, the report section, and two arms from the build to the outcome. |
| `tests/test_gateway.py`, `TestSubAgents` | `create_sub_agent`, the checks on an attached tool, the usage on a drive, the role. |
| `tests/fake_kiln.py` | `add_sub_agent`, and the routes of a tool server and of a sub-agent's task. |

## 9. Known limits

- The archive and the report section do not run after a quota stop, a
  crash or an interrupt. The registry still names what the run created.
- The usage reader makes one read for each sub-agent call of a drive.
- The cost test depends on Kiln-AI/Kiln#2077 as written: the `usage` key
  on the sub-agent tool message. A change of that key in review turns the
  test off, and the cost is then not measured.
- With a sub-agent in the baseline, a candidate that keeps one cannot be
  compared with it on speed. A candidate that drops it is compared with
  the baseline's lower bound.
- A sub-agent that a code tool calls leaves no tool message of its own,
  so the guard does not see it.
