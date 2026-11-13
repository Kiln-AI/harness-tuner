---
title: The model and the provider
description: The model and the provider that a run config names, chosen from what the run's model policy allows. Existing fine-tunes are candidates too.
reviewed: 2026-11-13
gateway_ops:
  - get_run_config
  - list_available_models
  - create_run_config
---

# The model and the provider

## What it is

A run config names one model and one provider, in `model_name` and
`model_provider_name`. The same model can be served by more than one
provider. A fine-tune that already exists is a model too, with the provider
`kiln_fine_tune`.

The settings of the model (thinking level, temperature, top-p) are the
lever [params](params.md).

## How the tuner changes it

1. Read what the run allows: `list_available_models` gives each model and
   provider that the run's model policy allows, with its thinking levels
   and its default `structured_output_mode`.
2. Change it in one of two ways:
   - **A screening round**: candidates with `overrides` of `model_name`
     and `model_provider_name`, and a thinking level when the new model
     needs one. Code makes and measures each candidate's run config.
   - **An arm**: the builder makes a new run config with
     `create_run_config`. It copies every property of the parent, and
     changes the model and the provider.
3. A thinking level comes from the new model's own list. A level of the
   old model can be missing on the new one.

Rules of the object:

- **The model policy is checked at create.** `create_run_config` refuses a
  model or a provider that the policy does not allow.
- **The properties of a run config are fixed when it is created.**
- **Code checks that the swap reached the call.** A drive that another
  model or provider served gives the verdict `lever not applied`.

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
- `list_available_models` does not give the model's capability flags (tool
  calls, structured output, vision). A probe or one drive shows them.

## See also

- The `kiln-harness-tuner-batch-selection` skill: when the model is a
  lever, the screening rounds and the speed and cost rules.
- The `kiln-harness-tuner-gateway` skill: `list_available_models` and the
  refusal of a run config outside the policy.
