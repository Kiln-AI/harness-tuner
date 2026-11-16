---
status: draft
---

# Model and provider information

**Status of this document.** A record of the decisions made so far, so that
the project is easy to start later. It was split out of the
[biological_bootloader](../biological_bootloader/project_overview.md)
project on 2026-11-12, because that project was growing. It is not started.
It comes after `biological_bootloader`.

[design_notes.md](./design_notes.md) is a first technical design, written
during the architecture step of `biological_bootloader` before the split.
It is a starting point. It is not approved, and some of it is replaced by
the decisions below.

## 1. The problem

The loop agents of Harness Tuner choose models and providers. Today they
get very little to choose with: a list of model names, the thinking levels
of each, and one output mode. The planner's skill says that model prices
come "from a live model-info lookup", and no such lookup exists.

The `biological_bootloader` overview asks for two things (its section 6.4):

- **A model guide.** The reasonable models to try in a situation. This
  knowledge changes weekly.
- **A provider guide.** The same for providers: which are very fast, which
  offer zero data retention, which have gotchas.

## 2. Decisions so far

1. **Tools, not guides.** The data is dynamic, so the loop agents get it
   from tools. The working shape: one tool that lists models with filters,
   one that gives the details of one model, and the same pair for
   providers.
2. **The source is Kiln's model file** (`ml_model_list.py`). Kiln already
   publishes it over the air, at
   `https://remote-config.getkiln.ai/kiln_config_v2.json`. Harness Tuner
   pulls it in. This gives a publishing channel that does not need a PyPI
   release.
3. **The provider data comes from the same file.** It is a new concept
   there: today the file has nothing about a provider beyond its id.
4. **New fields in Kiln's model file are acceptable.**
5. **A fetch from an outside host at the start of a run is acceptable as
   the default**, with a flag to turn it off. It would be Harness Tuner's
   first call to a host that is not the Kiln server.
6. **The stable guidance stays in the knowledge system.** How to choose a
   model (lock a model class, tune the harness, scan again) is knowledge:
   playbooks and techniques in `biological_bootloader`. This project gives
   the data.
7. **The "very old model" smell detector waits for this project.** It needs
   to know that a newer model of the same line exists.

## 3. What is known about the data

Found by the investigation of 2026-11-12 (Kiln `main`, commit `3e67b49`):

- The model file has 306 models and 716 provider entries across 17
  providers.
- **It has:** `family`, `friendly_name`, `featured_rank` (13 models),
  `editorial_notes` (57 models), the provider's model id, capability flags
  (tools, structured output, vision, document extraction, reasoning),
  thinking levels, the recommended structured-output mode, the
  `suggested_for_*` flags, and `deprecated` for each provider entry.
- **It does not have:** price, speed, release date, context window, a link
  to the model that replaces it, an open-weights flag, or any data about a
  provider (zero data retention, speed).
- Price, speed and context size appear only as free text in some
  `editorial_notes`.
- An old client ignores an unknown field, and Kiln's publish workflow
  already tests backward compatibility. So a new optional field is safe.
- The Kiln server's `/api/available_models` drops `family`,
  `featured_rank` and `editorial_notes`. The published file has them.
- Harness Tuner does not import `kiln_ai`, by rule. It would read the
  published file with its own small models.

## 4. An existing tool to look at first

**The Kiln assistant already has an MCP tool for model information, in
another repository.** Before any design work, look at that tool. The
question is whether to pull it out and share it, in place of a second
implementation here. That decision can change most of `design_notes.md`,
including where the price and speed data comes from.

## 5. Scope

In:

- The tools for the loop agents, through the loop gateway.
- The fields to add to Kiln's model file, and the **backfill** of those
  fields for the existing models.
- The update of **Kiln's maintenance skill for the model list**
  (`claude-maintain-models`), so that the new fields are filled for each
  new model. That skill has no guidance today on `featured_rank` or
  `editorial_notes`, and Kiln's scheduled sweep is told not to change them.
- The "very old model" smell detector, and the lines in the loop skills
  that tell an agent to use the tools.

Open:

- **Price and speed.** The first design left them out: they change often,
  they differ by provider, and the loop measures both on the real task. The
  owner's recording asks for them ("excellent value", "extremely fast").
  The existing assistant tool may already solve this. Decide in this
  project.
- **Restrictions.** A machine-readable basis for "zero data retention
  only", "open models only" and "no models from country X". Today the model
  policy file states these as name patterns.

## 6. Proposals already on the table

From `design_notes.md`, section 6. None is decided.

| Field | On | Use |
|---|---|---|
| `superseded_by` | model | "A newer model of the same line exists" |
| `size_class` (small, medium, large) | model | "Pick the class and lock one model" |
| `release_date` | model | Order and age |
| `open_weights` | model | The restriction "open models only" |
| A provider list with `notes` and `zero_data_retention` | file | The provider tools, and restrictions on data retention |

## 7. Related

- `biological_bootloader`, overview section 6.4 and functional spec
  section 13.1.
- The research:
  [model and provider selection](../biological_bootloader/research/harness-optimization/model-and-provider-selection/summary.md).
  It lists machine-readable sources outside Kiln (Artificial Analysis,
  OpenRouter, models.dev) and what each one gives.
- [kiln_gaps.md](../biological_bootloader/kiln_gaps.md), gap 13 (OpenRouter
  routing controls).
