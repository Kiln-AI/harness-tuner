---
status: draft
---

# Model Info MCP

We want to improve Harness Tuner. This project continues the record of
decisions in section 2 below, which was split out of `biological_bootloader`
on 2026-11-12.

## 1. The brief (2026-11-13)

### Background

We built something similar in the Kiln server project. See
`api/kiln_fastapi_api/tools/` in `kiln_server`.

### Part 1: more model and provider info from Kiln

`ml_model_list.py` in the core Kiln project is our home for publishing info
about models: what we recommend, what works in Kiln, and so on. It includes a
maintenance skill (`claude-maintain-models`) which reads new models and
updates this file. We can ship it over the air, so clients get updated
details between client releases.

We want to extend this with a set of fields for Harness Tuner. See the
existing ideas in section 3.5. I want to finalize this list.

### Part 2: information from other sources

The models.dev style work from the Kiln server should be accessible here
too. It gives us cost, speed and so on.

### Design questions

- Reuse the Kiln server one (this would require moving it to a public repo,
  and importing it from both), or maintain two versions?
  - Do we have unique design goals here that make a new implementation a
    better idea?
  - How much code is there really? Is it worth the cost of a shared library
    and a dependency?
  - Note: we own both codebases. We can take what we want from either.
- What data do we add to the model and provider info?
  - What is unique to Harness Tuner? Or should it all be covered by
    editorial notes?
- Providers:
  - How do we tell Harness Tuner which providers are available?
- What is the tool shape we need in Harness Tuner?
  - Tool or skill?
  - Basic design options.

### This project also adds

- A code-based smell detector for "old model": the baseline's model is more
  than 8 months old.

## 2. Decisions of 2026-11-13

1. **Build on `biological_bootloader`.** This branch starts from
   `claude/trusting-ritchie-k5uos1`. The old-model detector uses its smell
   detection package (`src/kiln_harness_tuner/smells/`).
2. **Two versions, not a shared library.** Port the useful logic of the
   Kiln server's `model_builder.py` (the provider map to models.dev, the
   checks on prices and limits). Do not port its async cache. One snapshot
   for the length of a run is enough.
3. **Take from models.dev what models.dev has.** Price, context window,
   maximum output, release date, knowledge cutoff, open weights and reasoning
   are not added to Kiln's file.
4. **`editorial_notes` stays as it is.** It is short text for the Kiln UI.
   The data for the optimizer goes in a new field.
5. **Few typed fields.** Prefer free text and a flexible form such as tags
   over many typed fields and enums. A model can infer most relations (for
   example that Opus 5.5 replaces Opus 5). Price is close to a tier. Speed
   goes in notes or tags, not in a live lookup.
6. **`list_models` has `include_not_available`, default false.** An agent
   can then find a model on a provider that is not connected, and ask the
   user to connect that provider for the next run.
7. **No price code in the cost estimate.** The agents use the price data
   with their knowledge. `estimate.py` does not change.
8. **The Kiln-side work is a separate project.** This project writes its
   `project_overview.md`, for another agent to start in the Kiln repository.
   That is phase 1 of this project.

## 3. The earlier record (2026-11-12)

Carried over from the first version of this file, written when this work was
split out of [biological_bootloader](../biological_bootloader/project_overview.md).
[design_notes.md](./design_notes.md) is the first technical design from that
time. It is not approved, and section 2 replaces parts of it.

### 3.1 The problem

The loop agents of Harness Tuner choose models and providers. Today they get
very little to choose with: a list of model names, the thinking levels of
each, and one output mode. The planner's skill says that model prices come
"from a live model-info lookup", and no such lookup exists.

The `biological_bootloader` overview asks for two things (its section 6.4):

- **A model guide.** The reasonable models to try in a situation. This
  knowledge changes weekly.
- **A provider guide.** The same for providers: which are very fast, which
  offer zero data retention, which have gotchas.

### 3.2 Decisions so far

1. **Tools, not guides.** The data is dynamic, so the loop agents get it from
   tools. The working shape: one tool that lists models with filters, one
   that gives the details of one model, and the same pair for providers.
2. **The source is Kiln's model file** (`ml_model_list.py`). Kiln already
   publishes it over the air, at
   `https://remote-config.getkiln.ai/kiln_config_v2.json`.
3. **The provider data comes from the same file.** It is a new concept
   there: today the file has nothing about a provider beyond its id.
4. **New fields in Kiln's model file are acceptable.**
5. **A fetch from an outside host at the start of a run is acceptable as the
   default**, with a flag to turn it off.
6. **The stable guidance stays in the knowledge system.** How to choose a
   model is knowledge (playbooks and techniques in `biological_bootloader`).
   This project gives the data.
7. **The "very old model" smell detector waits for this project.** It needs
   to know that a newer model of the same line exists.

### 3.3 What is known about the data

Re-checked on 2026-11-13 (Kiln `main`, commit `a1dc3e4`):

- 308 models and 718 provider entries across 21 provider ids.
- **It has:** `family`, `friendly_name`, `featured_rank` (13 models),
  `editorial_notes` (59 models), the provider's model id, capability flags,
  thinking levels, the structured-output mode, the `suggested_for_*` flags,
  and `deprecated` for each provider entry.
- **It does not have:** price, speed, release date, context window, a link
  to the model that replaces it, an open-weights flag, or any data about a
  provider.
- An old client ignores an unknown field, and Kiln's publish workflow tests
  backward compatibility. So a new optional field is safe.
- The Kiln server's `/api/available_models` drops `family`, `featured_rank`
  and `editorial_notes`. The published file has them.
- Harness Tuner does not import `kiln_ai`, by rule.

### 3.4 Scope (from the earlier record)

In: the tools for the loop agents through the loop gateway; the new fields in
Kiln's model file and their backfill; the update of `claude-maintain-models`
so that it fills the new fields; the "very old model" smell detector; the
lines in the loop skills that tell an agent to use the tools.

Open: price and speed; a machine-readable basis for restrictions ("zero data
retention only", "open models only", "no models from country X").

### 3.5 The earlier field proposals

| Field | On | Use |
|---|---|---|
| `superseded_by` | model | "A newer model of the same line exists" |
| `size_class` (small, medium, large) | model | "Pick the class and lock one model" |
| `release_date` | model | Order and age |
| `open_weights` | model | The restriction "open models only" |
| A provider list with `notes` and `zero_data_retention` | file | The provider tools, and restrictions on data retention |
