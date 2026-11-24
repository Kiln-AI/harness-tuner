---
status: complete
---

# Functional spec: Sub-agents

Read [project_overview.md](./project_overview.md) first. It has the reasons
and the decisions. This document says what the feature does.

## 1. Purpose

A build agent can create a sub-agent for the subject task and attach it on
a new run config. The loop holds the sub-agent to the same rules as the
subject, measures the arm with the sub-agent's cost included where Kiln
keeps a record, and checks that the sub-agent was called. The knowledge
skill tells the planner when a sub-agent is worth an arm.

## 2. Scope

In:

- The gateway operation `create_sub_agent` (section 3).
- The checks on a run config that attaches a `kiln_task::` tool
  (section 4).
- The model policy for sub-agents (section 5).
- The integrity checks on a sub-agent's text (section 6).
- The usage of a sub-agent's calls, and the timing of an arm (section 7).
- The lever check (section 8).
- The end of a run: the archive and the report (section 9).
- The knowledge: one lever document, four techniques, and links
  (section 10).

Out:

- Evals, datasets or tuning for the sub-agent's task.
- A change to a sub-agent in place, and a delete of a task.
- A new version of a sub-agent on the same task.
- A sub-agent of a sub-agent that the loop creates.
- A read of the sub-agent's own trace through the gateway.
- A probe that creates a sub-agent.
- Any change to Kiln.

## 3. `create_sub_agent`

Role: `create_experiment_rc` only.

Request fields:

| Field | Rule |
|---|---|
| `name` | The name of the sub-agent's task. Made valid for Kiln, as for a run config. |
| `tool_name` | The function name that the subject model calls. Kiln's rule: snake_case, a letter first, no `_` at the end, no `__`, at most 64 characters. |
| `tool_description` | 1 to 128 characters (Kiln's limit). |
| `instruction` | The sub-agent's prompt. It is the instruction of the new task. |
| `thinking_instruction` | Optional. |
| `input_json_schema` | Optional. An object schema with `properties`: the tool's parameters. Absent: one string parameter, `input`. |
| `output_json_schema` | Optional. An object schema with `properties`: the form of the result. Absent: plain text. |
| `run_config_properties` | As for `create_run_config`. `type` defaults to `kiln_agent`, and `prompt_id` to `simple_prompt_builder`. |
| `description`, `provenance` | Optional. The provenance goes on the run config. |

Checks, all before the first write:

1. The request is valid (the table above).
2. The run config is a `kiln_agent` config, and its model passes the model
   policy.
3. Its `tools_config.tools` has no `kiln_task::` tool, no OpenEnv tool, no
   `kiln_tool::call_kiln_api` and no `kiln_tool::memory::` tool.

Writes, in this order:

1. The task: single-turn, with the instruction and the schemas. Its
   description says that it is a sub-agent of the subject task, names the
   tool, and names the run that created it.
2. The run config, on the new task.
3. The Kiln task tool, which pins the task and the run config.

Each record is written to the run's registry when it exists. When the
second or the third write fails, the error names the records that exist.

Answer: `tool_id` (`kiln_task::<id>`), `task_id`, `run_config_id`,
`tool_name`, `tool_description`, the model and the provider, and a note
that says how to attach the tool.

## 4. A run config that attaches a sub-agent

`create_run_config` and `run_task` check each `kiln_task::` tool id of the
run config. Each is refused with a reason when:

- the id names no tool server, or a server that is not a Kiln task tool;
- the tool calls the subject task itself;
- the run config that the tool pins does not exist;
- the model of that run config is outside the model policy.

`run_task` answers with `sub_agent_calls` (the calls of sub-agent tools in
the trace), `sub_agent_usage` (the usage of the sub-agents' own runs,
beside `usage` and never added to it), and `sub_agent_note`, which says
how to read the two and when a call has no usage record.

`get_train_results` gives no cost and no latency for a row whose run calls
a sub-agent, and a `usage_note` that says why.

## 5. The model policy

The policy is checked for a sub-agent's (provider, model) pair:

- in the gateway: `create_sub_agent`, `create_run_config`, `run_task`;
- in the loop: before each measurement of a run config, and at the gate.

The baseline chain is exempt, as for its own model. A tool id that names
no sub-agent is refused. Under `--models keep`, the pinned policy holds
the baseline's pair and the pair of each sub-agent that the baseline
attaches.

## 6. The integrity checks

The text of a sub-agent is:

- `description`: the tool name, the tool description and the parameters;
- `prompt`: the prompt of the pinned run config, and the task's thinking
  instruction;
- `output schema`, when the task has one;
- each file of each skill and code tool that the pinned run config
  attaches.

The checks read it as they read a code tool's files:

- the exact-match pre-check of the generality check: a train id, number,
  passage or expected value in the added text is a leak;
- the generality review: the reviewer gets `sub_agent_changes`;
- the judge-copy check: eight words in a row from an eval, or an eval's
  name, in the whole text is a copy. The baseline's text is exempt;
- the baseline audit, on the baseline's whole text.

A finding names its place: "sub-agent `<tool name>`, prompt". A new
sub-agent that replaces exactly one sub-agent is diffed against it. Else
its whole text is new.

A prompt that Kiln builds from the sub-agent task's stored runs (few-shot,
multi-shot, repairs) is not rendered. The check then says that the
sub-agents' text was not read, as it does for a skill that it cannot read.

## 7. Cost, latency and timing

The cost of a sub-agent is added to the calling run in Kiln, not in the
tuner (project overview, decision 10). The tuner reads cost and latency
from a run's `usage`, as before, and adds nothing to it.

`usage_includes_sub_agents()` says whether a run's usage on this server
holds its sub-agents' usage. It is False today. While it is False:

- A run config with a timing drive that calls a sub-agent has no latency
  and no cost from the timing drives, on any of its items. Each item has
  the reason: "not measured: a drive of this run config calls a sub-agent,
  and Kiln's usage of a run leaves the sub-agent's calls out". Such a
  drive is not retried, and it is not diagnosed as a failure.
- A comparison with that run config, as the candidate, the baseline or the
  parent, has no pair. It is incomplete and never "clearly faster" or
  "clearly cheaper".
- Under `speed` and `cost`, the candidate stops at the end of its timing,
  before a judge is paid for, with "cost and latency not measured: the
  drives of the candidate call a sub-agent, …". Its outcome is
  `not_landed`.
- The rows of the analysis table carry no passive latency and no passive
  cost when a stored run of either side calls a sub-agent.
- An arm with no paired timing, on a comparison where a run config
  attaches a sub-agent, gets one line on its record and one entry in the
  analyzer's `measurement_notes`: "latency and cost: not measured. …".
- The measured line of an arm says "cost and latency not measured: N of M
  drives call a sub-agent, …", with the median cost and latency of the
  sub-agents' own runs when Kiln saved them.

When it is True, none of this applies: the numbers are read as Kiln gives
them, and the measured line says "N of M drives call a sub-agent".

In both cases:

- An arm that adds, drops or replaces a `kiln_task::` tool against its
  parent gets timing drives under every objective.
- The usage of the sub-agents' own runs is information. It is in no
  comparison and in no spend figure.
- The spend of a run, and so the budget and the spending caps, hold what
  Kiln reports for each run.

## 8. The lever check

For each `kiln_task::` tool that the candidate attaches and its parent
does not, on the timing drives:

| Evidence | Verdict |
|---|---|
| A drive's trace has a call of the tool | Applied. The measured line says "sub-agent tool `<id>` called in N of M drives", and a `lever_applied` event is logged. |
| Three or more drives have a trace, and none has a call | Not applied. The arm ends `dead_end` before it is judged. |

The proof of a call is the tool message of the parent's trace
(`kiln_task_tool_data`). It does not need a saved run of the sub-agent,
which an eval job does not make.

| Fewer than three drives have a trace, and none has a call | Unverified. Logged, and the arm goes on. |

## 9. The end of a run

Before the report, code reads the registry. For each sub-agent that the
run created:

- it lists the run configs of the subject task that attach the tool;
- when no run config attaches the tool, and the list of run configs was
  read, it archives the tool;
- it never archives a tool that a run config attaches, and it deletes
  nothing.

The report has a `## Sub-agents` section when the run created one: a row
for each, with the tool name, the tool id, the task, the run config, the
model, the run configs that attach it, and its state: in the final
baseline, not in the final baseline, archived, or not complete. While a
run's usage leaves the sub-agent out, a paragraph says that the cost and
the latency of a run config that calls a sub-agent were not measured.

## 10. The knowledge

| Document | Type |
|---|---|
| `levers/sub_agent.md` | Lever |
| `techniques/deep-work-in-a-stronger-sub-agent.md` | Technique |
| `techniques/specialist-sub-agent-with-its-own-tools.md` | Technique |
| `techniques/cheap-reader-for-a-large-source.md` | Technique |
| `techniques/fixed-return-form-for-a-sub-agent.md` | Technique |

Links to the techniques:

- the smells `too-many-tools` and `tool-results-flood-the-context`;
- the playbooks `fit-the-tool-layer-to-the-agent` (step 6) and
  `change-the-surface-not-the-words` (step 5). The playbook
  `cheaper-or-faster` has no link: an arm with a sub-agent cannot win on
  cost or on speed while its cost is not measured. The link belongs there
  when the switch of section 7 is True.

In each playbook the new step is last or next to last, with its reason: a
sub-agent is a second agent to build and to pay for.

The lever document `tool_config.md` no longer says that the tuner cannot
create a Kiln task tool.
