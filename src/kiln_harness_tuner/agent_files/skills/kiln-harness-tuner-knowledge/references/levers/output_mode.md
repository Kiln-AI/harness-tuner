---
title: The output mode
description: How Kiln asks the model for output that matches the task's output schema - the run config's structured_output_mode. A change is a model-level swap.
reviewed: 2026-11-25
gateway_ops:
  - get_run_config
  - get_model_details
  - create_run_config
---

# The output mode

## What it is

The output mode is the field `structured_output_mode` in
`run_config_properties`. It tells Kiln how to ask the model for output that
matches the task's output schema. Kiln uses it only on a task with an
output schema.

The values:

| Value | What Kiln sends with the call |
|---|---|
| `json_schema` | The schema, in the provider's structured-output parameter. A provider that supports it constrains the output. |
| `function_calling` | The schema, as the parameters of one tool (`task_response`) that the model must call, with the strict flag set. |
| `function_calling_weak` | The same tool, without the strict flag, for a provider that refuses the flag. |
| `json_instruction_and_object` | The schema as text after the saved prompt, and a request for a JSON object. |
| `json_mode` | A request for a JSON object only. The shape comes only from the saved prompt. |
| `json_instructions` | The schema as text after the saved prompt. Nothing is asked of the provider. |
| `json_custom_instructions` | Nothing. The saved prompt must state the JSON shape itself. |
| `default` | Legacy. Kiln chooses at call time: `json_schema` on Ollama and Docker Model Runner, else the tool, with the strict flag only on OpenAI. |
| `unknown` | Legacy. Kiln looks up the model's default at call time. |

After the call, Kiln checks each output against the schema, in every mode.
An output that does not match fails the run.

## How the tuner changes it

1. Read the parent's mode: `get_run_config` gives `structured_output_mode`
   in `run_config_properties`.
2. Read the default mode of each model: `get_model_details` gives
   `default_structured_output_mode` for each provider entry. The planner's
   `allowed_models` input does not carry it.
3. Change it in one of three ways:
   - **A screening round**: a candidate with
     `overrides: {"structured_output_mode": "<value>"}`.
   - **A probe**: a `check` probe with the same override answers one
     mechanism question: does every output parse as JSON
     (`count: json_valid`), does the model still call an attached tool
     (`attach: probe_tool`, `count: tool_calls`). A probe can set the
     mode. It cannot set a model, a provider or a setting.
   - **An arm**: the builder makes a new run config with
     `create_run_config`. It copies every property of the parent and
     changes only `structured_output_mode`. The `technique` of the arm is
     `output_mode`.
4. A model swap changes the mode too. The lever [model](model.md) gives
   the step.

Rules of the object:

- **A change of the mode is a model-level swap** (`items.SWAP_KEYS`). The
  arm gets judge-free timing drives, and the residual block of the
  parent's failures does not gate it. A screening candidate keeps the
  round's size: under speed and cost, only a model or provider change
  takes the larger count of baseline passes.
- **Code refuses a mode that cannot run.** `create_run_config` and a
  screening round refuse a value that is not in the table, and a mode that
  Kiln refuses with tools (Limits). A screening candidate that changes
  only the mode on a task with no output schema is a dead end.
- **Code checks that the mode reached the call**, on the timing drives of
  a candidate or an arm whose mode differs from its parent's. The mark in
  the trace is a call of the tool `task_response` (the two
  `function_calling` modes), or Kiln's block `# Format Instructions` in
  the system message (`json_instructions`,
  `json_instruction_and_object`). The other modes leave no mark: the proof
  is Kiln's record of the mode on the saved run, which shows that Kiln
  read the mode, not that the provider obeyed it. A drive that records
  another mode, or calls `task_response` in a mode without that tool,
  gives `lever not applied`. A missing mark gives `lever unverified`, and
  the candidate goes on.
- **The check of the result is the output-validity count** (the stop rule
  `invalid_output_rate`): the outputs that do not parse as JSON or do not
  match the schema. By default, one stops a candidate or an arm. In a
  smoke, count the runs that fail with Kiln's schema error too.
- **The properties of a run config are fixed when it is created.**

## Limits

- Kiln's model list gives one default mode for each pair, and no list of
  the modes that the pair supports. One failed call shows a refused mode.
- Under `json_schema`, Kiln removes the number bounds (`minimum`,
  `maximum` and the like) from the schema that it sends. The provider does
  not enforce a range. Kiln checks it only after the call.
- **On a run config with a tool or a skill, Kiln refuses
  `function_calling`, `function_calling_weak`, and `default` outside
  Ollama and Docker Model Runner**. Each sends the schema as a tool. A
  skill counts: Kiln loads it through a tool. Every run would fail.
- Under another mode, the model can call its tools less. The setup
  reconnaissance asks whether the baseline model calls an attached tool
  under its mode, and under `json_instructions` when the mode is
  `json_schema`. In every mode, it asks whether every baseline output
  parses as JSON. The answers are in `probe_results`.
- A fine-tune can carry the mode it was trained with. Read it before you
  change it (the lever [model](model.md)).

## See also

- The playbook [the output contract is not met](../playbooks/output-contract-not-met.md):
  the modes ranked by how reliably the output matches the schema.
- The `kiln-harness-tuner-batch-selection` skill: screening rounds and
  `check` probes.
- The `kiln-harness-tuner-experiment-build` skill: how the arm is built.
- The `kiln-task-setup` skill: the fields of `run_config_properties`.
