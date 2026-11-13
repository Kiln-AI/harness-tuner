---
status: complete
---

# Phase 1: The Kiln-side project overview

## Overview

This phase writes one document:
`specs/projects/model-provider-info/kiln_side/project_overview.md`. It is
the overview of a separate project, `model-agent-info`, in the Kiln
repository. Another agent starts that project with `/spec new_project`. The
user moves the file there.

The content is functional spec section 9: the new fields, the starting tags,
the maintenance skill, the backfill, and what is not in that project. The
document also gives the Kiln-side agent the facts it needs to start without
this repository: where the published file comes from, how old clients read
it, who reads the new fields, and the starting values of the provider list.

This phase changes no code. The facts in the document were checked against
Kiln `main` at commit `a1dc3e4` and the models.dev file of 2026-11-13.

## Steps

1. Create `specs/projects/model-provider-info/kiln_side/project_overview.md`
   with front matter `status: draft` (the Kiln-side `new_project` step 1
   approves it), and these sections:
   - **Why.** Harness Tuner and other agents that choose a model need
     notes for an agent and labels that code can filter on. Price, limits
     and dates come from models.dev, not from this file.
   - **Background.** `ml_model_list.py`, `KilnModel`,
     `KilnModelProvider`; `remote_config.py` serializes the lists and
     publishes `kiln_config_v2.json` from the `remote_config` branch; the
     backward-compatibility test against v0.19; old clients ignore unknown
     fields and unknown top-level keys; `editorial_notes` is shown on the
     models page of the web UI.
   - **The new fields.** The table of functional spec section 9, with
     types, defaults and the docstring intent of `agent_notes`.
   - **What goes in `agent_notes`, and what does not.** Content rules and
     the 2,000-character limit that the tuner applies.
   - **Tags.** The format rule (lower case letters, digits and `_`, at
     most 40 characters, at most 12 for each entry), the seven starting tags
     with their meaning, and the rule for a new tag.
   - **The provider list.** Its fields, its place in the published file,
     the rule that it covers every `ModelProviderName`, and a table of the
     starting `models_dev_id` values (from the Kiln server's
     `KILN_TO_MODELS_DEV_PROVIDER` and `PROVIDERS_WITHOUT_MODELS_DEV`, with
     the models.dev ids checked against the models.dev file). The table
     notes that `cloudflare` maps to `cloudflare-workers-ai`, which the
     Kiln server's table does not have.
   - **The maintenance skill.** What `claude-maintain-models` must do:
     fill `agent_notes` and tags for each new model and provider entry,
     document the tags, and keep the provider list current (an item in its
     "Adding a Net-New Provider" touchpoint checklist).
   - **The backfill.** The scope rule, and its approximate size on
     2026-11-13 (13 featured models; about 143 models released in the last
     12 months with a provider entry that is not deprecated, by the
     models.dev release dates). The provider list in full.
   - **Who reads it.** Harness Tuner reads the published file and joins
     models.dev. It does not import `kiln_ai`. Its parse is tolerant.
   - **Not in this project.** `superseded_by`, size class, release date,
     open weights, zero data retention as a field; changes to the web UI,
     to `/api/available_models` and to the Kiln server's model info tool.
   - **Questions for the functional spec.** The open design choices for
     the Kiln-side agent: where the provider list lives in code, whether
     its friendly names reuse `provider_name_from_id`, and how the tags and
     the provider list are checked in Kiln's tests.

## Tests

None. This phase writes one Markdown document and no code. The project's
checks (`uv run pytest`, `uv run ruff check`, `uv run ruff format --check`)
still run, to confirm the tree is clean.
