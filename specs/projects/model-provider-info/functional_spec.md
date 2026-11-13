---
status: draft
---

# Functional spec: Model Info MCP

## 1. Purpose

The loop agents choose models and providers. Today they get a list of model
names, thinking levels and one output mode. This project gives them the
data to choose with: price, context size, release date, Kiln's notes for an
optimizer, tags such as `very_fast`, and notes about each provider. It also
adds a code detector for an old baseline model.

The data comes from two public files, fetched once at the start of a run:

- **Kiln's published model file**, which Kiln ships over the air between
  releases. A separate project in the Kiln repository adds the new fields to
  it (section 9).
- **models.dev**, for price, limits, release date and other facts that an
  outside source already keeps.

## 2. Scope

In:

- The model catalogue: the fetch, the join of the two sources, and one
  snapshot file for each run (section 3).
- Three gateway operations: `list_models`, `get_model_details` and
  `list_providers` (sections 4 to 6). `list_models` replaces
  `list_available_models`.
- The old-model detector and its smell document (section 8).
- The text in the loop skills and the docs that names these tools
  (section 10).
- The `project_overview.md` of the Kiln-side project (section 9). It is the
  first phase of this project. Another agent starts that project.

Out:

- The Kiln-side changes themselves: the new fields, the backfill and the
  maintenance skill. They are the separate project.
- Price code in the cost estimate. `estimate.py` does not change. The
  agents use the price data with their knowledge.
- A live speed lookup. Speed is in tags and notes.
- A standalone MCP server. The tools are gateway operations only.
- Model data in the guided start (the model choice step).
- Restrictions in the model policy based on the new data (for example
  "open models only"). The policy stays a list of name patterns.

## 3. The model catalogue

### 3.1 The sources

| Source | URL | What the catalogue takes |
|---|---|---|
| Kiln's published file | `https://remote-config.getkiln.ai/kiln_config_v2.json` | Each model: id, friendly name, family, `featured_rank`, `editorial_notes`, `agent_notes`, `tags`, and each provider entry (provider id, the provider's model id, capability flags, thinking levels, structured-output mode, `tags`, `deprecated`). The top-level `provider_list`, when it is there. |
| models.dev | `https://models.dev/api.json` | For each matched provider entry: input, output, cache-read and cache-write price per million tokens; context window; maximum output; release date; knowledge cutoff; open weights; reasoning; tool calls; whether it takes a temperature; input modalities. |

The Kiln fields `agent_notes`, `tags` and `provider_list` do not exist
until the Kiln-side project ships. Until then they are empty, and the tools
work with the other fields.

### 3.2 When it runs

- Once for each run, at the start, before the first agent is dispatched. The
  two fetches run at the same time.
- Each fetch has a timeout of 10 seconds and no retry. A response over
  50 MiB is refused.
- The flag `--no-model-catalog` turns both fetches off.
- The fetch is a plain GET of a public file. It sends nothing about the run,
  the task or the user.
- The snapshot does not change during the run. Every agent of the run sees
  the same data.

### 3.3 The join

**Price and limits are matched for each (provider, model) pair.** The
provider entry's `model_id` is looked up under the provider's models.dev id.
The provider's models.dev id comes from `provider_list[].models_dev_id` when
Kiln publishes it. When Kiln does not, it comes from a built-in table, which
is a port of the Kiln server's `KILN_TO_MODELS_DEV_PROVIDER` and
`PROVIDERS_WITHOUT_MODELS_DEV`. The published value wins over the built-in
table.

**Model facts take the first match in a fixed order.** The release date,
knowledge cutoff, open weights and context window of the model come from the
first matched provider entry, in the Kiln server's order
(`MODELS_DEV_PROVIDER_PREFERENCE`, then the other providers by name).

**The release date has a looser fallback.** When no provider entry matches,
the release date is looked up across all of models.dev, by
`canonical_model_id` or by the last part of the model id with punctuation
removed. A date found this way is marked `release_date_match: "loose"`. A
date from an exact match is `"exact"`.

On 2026-11-13 the exact match found 212 of the 264 Kiln models that have a
provider entry that is not deprecated. The loose fallback raised the count
with a release date to 242.

**Values are checked.** A price that is negative, not a number, NaN or
infinite is dropped. A limit that is not a positive integer is dropped. This
is the Kiln server's `_is_valid_price` and `_extract_limits`.

### 3.4 Tolerance

- A Kiln model that is not valid is dropped, and the rest are kept. A
  provider entry that is not valid is dropped, and its model is kept.
- A field the tuner does not know is ignored.
- A tag that is not lower case letters, digits and `_`, or is longer than
  40 characters, is dropped. At most 12 tags are kept on each entry.
- A note longer than 2,000 characters is cut, and ends with `[cut]`.

### 3.5 Failures

| What fails | Result |
|---|---|
| models.dev | The catalogue has the Kiln data. Every models.dev field is null. The snapshot records `models_dev: "none"` and the reason. |
| Kiln's file | The catalogue has no Kiln data. The tools serve what the Kiln server's `/api/available_models` gives, with the catalogue fields null. models.dev is not joined, because the join needs the provider's model id. The snapshot records `kiln: "none"` and the reason. |
| Both, or the flag | Same as a failure of Kiln's file. |

A failure never stops the run. The run's log has the event
`model_catalog` with the counts, or `model_catalog_unavailable` with the
source and the reason. Each tool answer has a `catalog` field that says
which sources the answer has.

### 3.6 The snapshot file

`model_catalog.json` in the run directory. The gateway process loads it at
start, as it loads `model_policy.json`. The orchestrator loads it once for
the old-model detector.

It holds:

- `fetched_at` (ISO time, UTC), and the status and reason of each source;
- every Kiln model, with its provider entries, and the models.dev fields
  joined on;
- the provider list.

It does not hold availability. The gateway reads availability from the
Kiln server at each call, as `list_available_models` does today.

## 4. Availability

A (provider, model) pair has one of four statuses:

| Status | Meaning |
|---|---|
| `ready` | The Kiln server offers the pair, and the run's model policy allows it. An agent can make a run config with it. |
| `blocked_by_policy` | The Kiln server offers the pair. The model policy refuses it. |
| `not_offered` | The provider is connected in Kiln, but the Kiln server does not offer this model on it. |
| `not_connected` | The Kiln server offers nothing on this provider. Usually it has no API key in Kiln. |

- The Kiln server's `/api/available_models` decides what is offered. The
  model policy decides what is allowed. This is the same rule as today.
- A pair that the Kiln server offers and the catalogue does not have (a
  fine-tune, a custom model) is listed as `ready` or `blocked_by_policy`,
  with the catalogue fields null.
- `create_run_config` still refuses every pair that is not `ready`. The
  status in a tool answer is information. It does not change what an agent
  can build.
- An agent that finds a good model with the status `not_connected` can say
  so in its output, so that the report asks the user to connect that
  provider for the next run. This project does not add a new output field
  for this. The planner's existing notes and the report carry it.

## 5. `list_models`

Lists models, one row for each model, with the model's providers in the row.

### 5.1 Arguments

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `family` | string | none | Only this Kiln family. |
| `provider` | string | none | Only this provider. Other providers are left out of each row. |
| `tags` | list of strings | none | Only models where at least one of these tags is on the model or on one of its listed provider entries. |
| `tools` | bool | none | Only models whose listed provider entries support function calling (true), or do not (false). |
| `vision` | bool | none | The same, for vision input. |
| `reasoning` | bool | none | The same, for thinking levels or reasoning. |
| `open_weights` | bool | none | The same, for open weights. A model where this is unknown is left out by a filter on it. |
| `max_input_price` | number | none | Only provider entries with a known input price at or below this, in USD per million tokens. |
| `include_not_available` | bool | false | Also list provider entries that are not `ready`. |
| `include_deprecated` | bool | false | Also list provider entries that Kiln marks as deprecated. |
| `sort` | `featured`, `price`, `newest` | `featured` | The order of the rows. |
| `limit` | int, 1 to 200 | 50 | Rows per answer. |
| `offset` | int, 0 or more | 0 | Rows to skip. |

- Filters are joined with AND.
- A filter on a provider entry removes that entry from the row. A row with
  no provider entry left is not listed.
- With `include_not_available` false, a row lists only `ready` entries.

### 5.2 The row

| Field | Meaning |
|---|---|
| `model` | Kiln's model id. |
| `name` | The friendly name. |
| `family` | Kiln's family. |
| `featured_rank` | Kiln's rank, or null. |
| `tags` | The model's tags. |
| `release_date` | Or null. |
| `context_window` | Tokens, or null. |
| `open_weights` | Or null. |
| `thinking` | True when a listed provider entry has thinking levels or is reasoning capable. |
| `providers` | The listed provider entries: `provider`, `status`, `input_price`, `output_price`, `tags`, `deprecated`. |

The row has no notes. `get_model_details` gives them.

### 5.3 The answer

`catalog` (the sources, section 3.5), `fetched_at`, `total` (the rows after
the filters, before `limit`), and `models`.

### 5.4 The order

- `featured`: `featured_rank` ascending with null last, then the order of
  Kiln's file, then models that are not in the catalogue, by id.
- `price`: the lowest `input_price + output_price` of the row's listed
  provider entries, ascending. A row with no known price is last.
- `newest`: `release_date` descending. A row with no date is last.
- Ties keep the `featured` order.

## 6. `get_model_details` and `list_providers`

### 6.1 `get_model_details`

Arguments: `model` (required), `provider` (optional).

The answer has everything that the row of `list_models` has, and:

- `editorial_notes` and `agent_notes`;
- `knowledge_cutoff`, `max_output_tokens`, and `release_date_match`;
- for each provider entry: its status, the four prices, its tags, the
  provider's model id, `supports_tools`, `supports_structured_output`,
  `supports_vision`, `supports_doc_extraction`, `reasoning_capable`, the
  thinking levels and the default level, the structured-output mode,
  `takes_temperature`, `suggested_for`, `untested`, `deprecated`.

- With `provider`, the answer has only that provider entry.
- The answer includes provider entries of every status and deprecated
  entries, each with its status. An agent can read about a model that it
  cannot run.
- A model id that is in neither the catalogue nor the Kiln server's list is
  refused: "Model not found: <id>. Use list_models to find models."

### 6.2 `list_providers`

Argument: `include_not_available` (bool, default false).

Each row: `provider` (Kiln's id), `name`, `status` (`connected` or
`not_connected`), `ready_models` (the count of `ready` pairs), `tags`, and
`notes`.

- With `include_not_available` false, only connected providers.
- The list is the union of the catalogue's `provider_list`, the provider ids
  in Kiln's file, and the providers that the Kiln server offers.
- Until Kiln publishes `provider_list`, `name`, `tags` and `notes` are null
  or empty.

There is no details operation for a provider. The list is short, and each
row has the notes.

## 7. Roles and the old operation

- `list_available_models` is removed. `list_models` replaces it in each role
  that has it: `batch_plan`, `create_experiment_rc` and `probe`.
- `get_model_details` and `list_providers` go to the same three roles.
- The planner's input field `allowed_models` does not change. It stays the
  list an arm must choose from. The tools add detail to it.

## 8. The old-model detector

### 8.1 The rule

The baseline's model is old when its release date plus 8 calendar months is
before the date the run started.

- The threshold is the constant `OLD_MODEL_MONTHS = 8` in
  `smells/thresholds.py`, with a comment that gives its source: a house
  default, set by the project owner on 2026-11-13.
- The release date comes from the snapshot, for the baseline's
  (provider, model) pair. An exact or a loose match counts.
- When the date is unknown, the detector returns nothing. A fine-tune or a
  custom model has no date, so it never fires.
- The detector runs with `--models keep` too. The planner cannot change the
  model then, but the report can tell the user.

### 8.2 The detection

- Detector id `old_model`, smell `old-model`, `scope` `harness`.
- `fact`: "The baseline's model, <name> (<model id>), was released on
  <date>, <n> months before this run. The reference is 8 months."
- `value`: the age in whole months. `reference`: 8. `reference_source`:
  the constant and its source.

### 8.3 The smell document

`references/smells/old-model.md` in the knowledge skill, written by the
rules of `.agents/skills/knowledge-maintenance/SKILL.md`, in the form of the
other smell documents. It says:

- what it is: a newer model of the same line or price often does the same
  work better, cheaper or faster;
- how to confirm it: use `list_models` with `sort: newest` and the
  baseline's family or price range, and read `agent_notes`;
- not this smell: an old model kept on purpose, for example for a fine-tune
  base, a price, a latency, or a model the user pinned;
- remedies: links to the model lever and the existing documents that apply.

It holds no measured number from a run, by the working rule of
`agent_files/`.

## 9. The Kiln-side project

Phase 1 of this project writes
`specs/projects/model-agent-info/project_overview.md` in a form that another
agent can start with `/spec new_project` in the Kiln repository. It is
written here, in this repository's spec folder, and the user moves it.

It states:

**The new fields.** Each is optional with a default, so an older client
ignores it.

| Field | On | Type | Purpose |
|---|---|---|---|
| `agent_notes` | `KilnModel` | `str \| None` | Notes for an agent that chooses or configures the model, such as the Harness Tuner optimizer or the Kiln assistant. The docstring says this. Strengths compared with models at the same price, the settings it responds to, output quirks, tool-call reliability, and, when it matters, the model that replaces it. Not price, limits or dates, which come from models.dev. `editorial_notes` does not change. |
| `tags` | `KilnModel` | `list[str]`, default empty | Short labels that code can filter on. |
| `tags` | `KilnModelProvider` | `list[str]`, default empty | Labels true of one provider entry only, for example speed. |
| `provider_list` | the published file, top level | list of `id`, `friendly_name`, `notes`, `tags`, `models_dev_id` | Notes about each provider: data retention, rate limits, routing. The one map from Kiln's provider ids to models.dev. |

**The starting tags**, documented in `claude-maintain-models` with the
meaning of each:

- on a provider entry: `very_fast`, `fast`;
- on a model: `best_value`, `strong_agentic`, `strong_coding`,
  `strong_writing`, `verbose`.

The rule for a new tag: lower case and `_`; a claim the maintainer would
stand behind compared with models at the same price; documented in the skill
before it is used.

**The maintenance skill.** `claude-maintain-models` fills `agent_notes` and
`tags` for each new model and for each new provider entry, and it keeps the
provider list current when it adds a provider.

**The backfill.** The scope is open (section 12).

**Not in that project.** `superseded_by`, a size class, release date, open
weights, zero data retention as a field. The reasons are in this project's
overview, section 2.

## 10. Text and docs

- `kiln-harness-tuner-gateway/SKILL.md`: a section "Models and providers"
  with the three operations, their arguments and one example each. The row
  for `list_available_models` goes.
- `experiment-costing/SKILL.md`: "from a live model-info lookup" becomes
  true. The text names `list_models` and `get_model_details`. It says that a
  listed price is a list price, and that the price the loop measures on the
  task wins.
- The knowledge skill's `builder.md`, `levers/model.md` and
  `levers/params.md`: `list_available_models` becomes `list_models` and
  `get_model_details`. The line in `levers/model.md` that says the tool does
  not give capability flags is removed.
- `docs/cli.md`: the row for `--no-model-catalog`.
- `docs/architecture.md`: the catalogue, the two outside hosts, and the
  snapshot file.
- `specs/backlog.md`: the entries "Model and provider information" and
  "Smell: an old model" point to this project as done.

## 11. Edge cases

- **A model in the catalogue that the installed Kiln does not know** (the
  over-the-air file is newer than the installed Kiln): `not_offered` or
  `not_connected`. It is listed only with `include_not_available`.
- **The same model on many providers**: one row, many provider entries.
  `thinking` and the filters on capabilities look at the listed entries
  only.
- **A provider id that Kiln's file has and the server does not know**:
  `not_connected`.
- **A deprecated entry that is the baseline's pair**: listed with
  `deprecated: true` when `include_deprecated` is true, and always in
  `get_model_details`.
- **An answer over the gateway's answer cap**: the cap that exists today
  splits it, and `get_answer_part` gives the rest.
- **A run with no network**: both fetches fail in at most 10 seconds. The
  tools serve the Kiln server's list, and the detector is silent.

## 12. Open questions

1. **The backfill scope of the Kiln-side project.** Proposal: `agent_notes`
   and tags for the featured models and for each model with a provider entry
   that is not deprecated and that was released in the last 12 months. The
   others stay empty until a maintainer touches them. The provider list is
   filled in full.
