---
title: The model and the provider
description: The model and the provider that a run config names, chosen from what the run's model policy allows. Existing fine-tunes are candidates too.
reviewed: 2026-11-25
gateway_ops:
  - get_run_config
  - list_models
  - get_model_details
  - list_providers
  - create_run_config
---

# The model and the provider

## What it is

A run config names one model and one provider, in `model_name` and
`model_provider_name`. The same model can be served by more than one
provider. A fine-tune that already exists is a model too, with the provider
`kiln_fine_tune`.

The settings of the model (thinking level, temperature, top-p) are the
lever [params](params.md). The output mode is the lever
[output_mode](output_mode.md).

## How the tuner changes it

1. Read what the run allows: `list_models` gives each model with its
   `ready` provider entries: the pairs that the Kiln server offers and the
   run's model policy allows. Each row has the list price, the context
   window, the release date and the tags. `get_model_details` gives one
   model's notes for agents (`agent_notes`), and per provider entry the
   capability flags (tool calls, structured output, vision), the thinking
   levels and `default_structured_output_mode`. `list_providers` gives
   the notes on each provider, for example data retention or routing.
2. Change it in one of two ways:
   - **A screening round**: candidates with `overrides` of `model_name`
     and `model_provider_name`, and a thinking level when the new model
     needs one. Code makes and measures each candidate's run config.
   - **An arm**: the builder makes a new run config with
     `create_run_config`. It copies every property of the parent, changes
     the model and the provider, and leaves the output mode to step 4.
3. A thinking level comes from the new model's own list. A level of the
   old model can be missing on the new one.
4. **The output mode is the new pair's, and code sets it.** A mode is
   chosen for one model and provider: the new provider can refuse the
   parent's mode, or the new model can follow it less well. A screening
   candidate that changes the model or the provider and names no
   `structured_output_mode` takes the mode of the new pair. So does an
   arm, when its `create_run_config` call leaves `structured_output_mode`
   out. The mode of the new pair is the
   `default_structured_output_mode` of step 1. It is the parent's mode
   when step 1 gives none, or the legacy `default` or `unknown`. **On a
   run config that attaches a tool or a skill**, Kiln refuses the modes
   that send the schema as a tool (the lever
   [output_mode](output_mode.md)). When the mode would be one of them,
   code sets `json_instruction_and_object`. Name `json_schema` there
   when a probe or an earlier screen shows that the new model supports
   it: step 1 gives only the default mode. The model preflight tests each
   allowed model in the mode that code sets. The one exception is an
   experiment that tests the output mode on the new model: it names the
   mode that it tests, and its hypothesis names both changes. Code keeps
   a named mode. When a swap names the parent's mode and the new pair has
   another, `create_run_config` says so in `notes`. On a task with no
   output schema, the mode has no effect.

Rules of the object:

- **The model policy is checked at create.** `create_run_config` refuses a
  model or a provider that the policy does not allow.
- **The properties of a run config are fixed when it is created.**
- **Code checks that the swap reached the call.** A drive that another
  model or provider served gives the verdict `lever not applied`.
- **Invalid output just after a swap points first to the output mode**,
  not to a weaker model. When a swap stops on invalid output, or every run
  fails, check its mode against step 4 before you record the model as
  refuted: the new pair's default, or the fallback on a run config with
  tools. Code also checks that the mode reached the call (the lever
  [output_mode](output_mode.md)).

## Limits

- **The model is a lever only when `allowed_models` lists another
  model.** The owner chooses that list at start-up (`--models keep`,
  `--models any` or a model policy). When the list holds the baseline's
  model alone, the subject model is the environment that the task is tuned
  for, and the planner plans no model swap. Guidance that forbids a model
  change also excludes it.
- The tuner does not start a fine-tune. It can choose one that exists,
  when the policy allows `kiln_fine_tune` and the server lists it. A
  fine-tune can be made for another task, or carry its own prompt and
  output mode: read what it is before you plan it.
- The capability flags, the prices and the notes are Kiln's and
  models.dev's data. They can be missing or wrong for one pair. A probe or
  one drive shows what the pair does on the task.
- `create_run_config` refuses a pair that the policy refuses. Use `ready`
  pairs only: a pair that the server does not offer fails when it runs. A
  good model with the status `not_connected` is a finding for the report:
  the user can connect that provider for the next run.

## See also

- The `kiln-harness-tuner-batch-selection` skill: when the model is a
  lever, the screening rounds and the speed and cost rules.
- The `kiln-harness-tuner-gateway` skill: `list_models`,
  `get_model_details`, `list_providers`, the four statuses, and the
  refusal of a run config outside the policy.
