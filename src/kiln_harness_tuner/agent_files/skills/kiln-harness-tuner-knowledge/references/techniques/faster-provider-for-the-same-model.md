---
title: Faster provider for the same model
description: Serve the same model from another provider - a faster one, or a cheaper one - with the harness unchanged, when the run allows that provider and the goal is latency or cost.
reviewed: 2026-11-12
status: active
levers:
  - model
applies_to: all
cost: low
---

# Faster provider for the same model

## When to use

- The objective is latency, or cost, and the run's list of allowed models
  has the same model on more than one provider.
- Quality is acceptable with this model, and the change must not move it.
  The same model with the same harness is the change that is least likely
  to move quality.
- Some providers run open-weight models on hardware made for fast
  inference. When the task needs short answers fast, such a provider can
  cut the latency a lot.

## When not to use

- **The objective is quality.** A provider change is not meant to raise
  quality, and it can lower it.
- **A long conversation with a long, repeated prefix, on a provider with
  no prompt cache.** Most of each call is the same prefix. With a cache,
  that prefix costs less on each call. Without one, a fast provider can
  cost more than a slower one with a cache. Read the cost of the drives,
  not only the price per token.
- **The provider is not the same model in practice.** A provider can serve
  another version, another precision or another context length under the
  same name, and the tool calls or the structured output can then behave
  differently. Measure it as a model change, on every eval.
- **The run's model policy does not allow the provider**, or a stated
  restriction excludes it (a region, data retention, an origin).

## Alternatives

- [Thinking level ladder](thinking-level-ladder.md), when the same
  provider at a lower level can reach the latency goal.
- [Model swap inside a class](model-swap-inside-a-class.md), when no
  provider of this model is fast or cheap enough, and the guidance allows
  another model.

## How

The change is the provider on a run config, with the same model (the lever
[model](../levers/model.md)). Everything else stays as the parent has it.

1. **List the providers of this model** that the run allows. The same
   model can have a different name on each provider.
2. **Screen the providers in code**, on the same items: the latency or the
   cost, and whether the baseline's passes are kept.
3. **Set the thinking level** from the new provider's own list. A level of
   one provider can be missing on another.
4. **Commit the best survivor** as an arm, and measure it on every eval.

## Check that it took effect

Code checks that the swap reached the call: a drive that another provider
served gives the verdict `lever not applied`. Compare the latency and the
cost of the drives with the parent's on the same items.

## Budget

Low cost: no build, and a screening round in code. A good first step on
any budget when the objective is latency or cost, because it changes the
least.

## If it does not move the evals

Keep it. The change exists to lower the latency or the cost and to keep
the evals where they are. The condition: the latency or the cost falls,
and no eval goes down. When neither the latency nor the cost falls, drop
it.

## Priors

None yet.

## Confidence

Medium, that a provider change moves latency and cost more than it moves
quality: the model is the same, and the provider changes the hardware and
the price. Low to medium, that quality holds: a provider can serve the
model in a form that behaves differently, so the measurement decides. No
measured case is recorded here yet.
