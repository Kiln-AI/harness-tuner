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
existing ideas in section 2.5. I want to finalize this list.

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

## 2. The earlier record (2026-11-12)

Carried over from `specs/projects/model-provider-info/project_overview.md`
on branch `claude/trusting-ritchie-k5uos1`, where `biological_bootloader`
lives. That branch is not merged. [design_notes.md](./design_notes.md) is
the first technical design from that time. It is not approved.

### 2.1 The problem

The loop agents of Harness Tuner choose models and providers. Today they get
very little to choose with: a list of model names, the thinking levels of
each, and one output mode. The planner's skill says that model prices come
"from a live model-info lookup", and no such lookup exists.

The `biological_bootloader` overview asks for two things (its section 6.4):

- **A model guide.** The reasonable models to try in a situation. This
  knowledge changes weekly.
- **A provider guide.** The same for providers: which are very fast, which
  offer zero data retention, which have gotchas.

### 2.2 Decisions so far

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

### 2.3 What is known about the data

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

### 2.4 Scope (from the earlier record)

In: the tools for the loop agents through the loop gateway; the new fields in
Kiln's model file and their backfill; the update of `claude-maintain-models`
so that it fills the new fields; the "very old model" smell detector; the
lines in the loop skills that tell an agent to use the tools.

Open: price and speed; a machine-readable basis for restrictions ("zero data
retention only", "open models only", "no models from country X").

### 2.5 The earlier field proposals

| Field | On | Use |
|---|---|---|
| `superseded_by` | model | "A newer model of the same line exists" |
| `size_class` (small, medium, large) | model | "Pick the class and lock one model" |
| `release_date` | model | Order and age |
| `open_weights` | model | The restriction "open models only" |
| A provider list with `notes` and `zero_data_retention` | file | The provider tools, and restrictions on data retention |
