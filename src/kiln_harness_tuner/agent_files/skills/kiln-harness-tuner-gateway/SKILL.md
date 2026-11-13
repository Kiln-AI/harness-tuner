---
name: kiln-harness-tuner-gateway
description: How a Kiln Harness Tuner agent reaches Kiln — the typed gateway operations (mcp__kiln_harness_tuner_gateway__<operation>) its role is given, each with its fields, its answer and an example. Load in any Kiln Harness Tuner agent session before the first Kiln read or write. Covers what "train only" means for every answer, the memory scopes, drives on train items, the models and providers with their prices and status, run configs under the model policy, the eval-build operations, and what to do when an operation is refused.
---

# kiln-harness-tuner-gateway

In a Kiln Harness Tuner session you reach Kiln through **gateway operations** and
nothing else. Each is a typed tool named `mcp__kiln_harness_tuner_gateway__<name>`;
your session has the operations your role needs (table below) and no others.
There is no generic Kiln API tool in a loop session: no `call_kiln_api`, no
HTTP from Bash (the sandbox has no network), no reading the project's files
(the file fence denies them).

**Every answer is train-only.** The gateway answers from records whose item
is in the run's TRAIN set — computed by the loop at start-up from every
eval's own splits — or that this run created on a train item. Anything
else is dropped before you see it: a results list may count `dropped` rows
you never see, and a record that is not provably train is refused ("not
provably a train record"). That answer is final; do not look for it
another way. Answers are the gateway's own shapes: only the fields named
below, never Kiln's raw response.

**A refusal is an answer.** "not available to the <role> role", "outside
the model policy", "not a train item", "scope must be …": say what you
needed in what you return, and go on with what you have.

## Calling an operation

Arguments are the operation's fields, by name. Examples:

```
mcp__kiln_harness_tuner_gateway__get_train_results(eval_id="424242100001",
    run_config_id="424242100003")
mcp__kiln_harness_tuner_gateway__memory_list(scope="task::424242770014",
    tags=["issue"], limit=50)
mcp__kiln_harness_tuner_gateway__run_task(run_config_id="424242100007",
    item_id="424242500010", tags=["smoke"])
```

A field marked `?` below is optional. An unknown field, a wrong type or a
value out of bounds is refused before Kiln is called.

## Traces are files, and answers are small

No answer is larger than 40,000 characters. Three rules keep each answer
under that cap:

- **A trace is a file, not an answer.** `get_train_results`,
  `get_task_run` and `run_task` write each train trace to a file under the
  run's trace folder (`runs/<run_id>/scratch/traces/`) and give you its
  path (`trace_path`) and a one-line `trace_summary`. Read the file with
  `Grep` and `Read` (with `offset` and `limit` for a line range; under
  OpenCode, `Read` only). Your role
  has an explicit read grant on that folder. You cannot write it; only the
  gateway does.
- **A list comes in pages.** `get_train_results` and `get_train_expected`
  give one page per call. When `next_offset` is not null, call again with
  `offset=next_offset`. A page also ends early when the next row would take
  the answer over the cap.
- **Any other answer over the cap comes in pieces.** The answer is then
  `{truncated: true, continuation, answer_chars, offset, next_offset,
  part}`: `part` is a piece of the answer's JSON text. Call
  `get_answer_part(continuation=…, offset=next_offset)` until
  `next_offset` is null, and join the pieces. The gateway keeps the last 16
  such answers of your session. A piece never runs the operation again.
  A narrower request (fewer ids, a smaller page) is usually better.

The trace folder:

```
traces/
  <run_config_id>/
    <eval_id>/<item_id>.md       one train result's trace
    _system/<hash>.md            the system prompt and the tool definitions
    _stats.md, _stats.json       counts per item and per tool (cost work)
  task_runs/<task_run_id>.md     a task run's trace (get_task_run, run_task)
  _expected/<item_id>.json       an expected output too large for an answer
```

A trace file starts with a header of `key: value` lines: `item_id`,
`eval_id`, `run_config_id`, `eval_run_id`, `scores`, the cost and latency,
`input_tokens`, `output_tokens`, `cached_tokens`, `assistant_turns`,
`tool_calls`, `tool_errors`, and `system` (the path of its `_system` file).
A `# Judge reason` section follows when the eval row has one, then
`# Input`, `# Output`, and `# Messages`: one block per message, in order,
each starting with a line `## [<n>] <role> …`. An assistant block names its
turn and its usage, and each tool call (`### tool call <name> (id …)`,
then its arguments). A tool result block (`## [<n>] tool result <name> (id
…, <chars> chars)`) holds the result in full, and ends its first line with
`TOOL_ERROR` when Kiln marks the result as an error. The system prompt and
the tool definitions are not repeated in each trace: the trace keeps a
one-line `## [0] system (moved out: …)` marker, and the text is in the
`_system` file, once.

To find a failure: `Grep` for `TOOL_ERROR` (or a tool name, or text from
the expected output) over the run config's folder with line numbers, then
`Read` the trace from a few lines above the hit. For a cost or speed
question, read `_stats.md` first. It lists per item the turns, the tool
calls, the tool errors, the tokens and the cost, and per tool the calls,
errors and result sizes. `_stats.json` has the per-turn tokens
(`turn_usage`) when the stored trace carries them. Its `usage_source` says
whether it does (`per_turn`) or only the run total is known
(`per_run_only`).

## The operations

### Reading the task

| Operation | Fields | Answer |
|---|---|---|
| `ping` | — | `{ok}` — the Kiln server answers (preflight only) |
| `get_task` | — | name, description, instruction, thinking_instruction, requirements, input/output JSON schemas |
| `list_run_configs` | — | every run config: id, name, description, model, provider, thinking level, prompt id, tools, properties, and the frozen prompt it runs with (`prompt`) |
| `get_run_config` | `id` | one run config |
| `list_prompts` | — | the task's saved prompts, with their text |
| `get_prompt_text` | `id` | the prompt text a prompt id renders (e.g. `simple_prompt_builder`, `id::…`) |

### Models and providers

The run fetches a model catalogue once, at its start: Kiln's published
model file, joined with models.dev. Every session of the run reads the
same snapshot. The three operations join that snapshot with what the Kiln
server offers now and with the run's model policy.

Each (provider, model) pair has a `status`:

| Status | Meaning |
|---|---|
| `ready` | The Kiln server offers the pair, and the run's model policy allows it. You can make a run config with it. |
| `blocked_by_policy` | The server offers the pair. The model policy refuses it. |
| `not_offered` | The provider is connected in Kiln, but the server does not offer this model on it. |
| `not_connected` | The server offers nothing on this provider. Usually Kiln has no API key for it. |

The status is information. `create_run_config` refuses a pair that the
model policy refuses. Use `ready` pairs only: a pair that the server does
not offer fails when it runs. When a good model is `not_connected`, say so
in your output, so that the report can ask the user to connect that
provider for the next run.

Every answer has `catalog`: `kiln` and `models_dev` (`ok` or `none`), and
`fetched_at`. A source that is `none` gives null fields. With `kiln:
"none"`, the operations list only what the Kiln server offers. A pair that
the catalogue does not have (a fine-tune, a custom model) is listed with
null catalogue fields. A price is a list price in USD per million tokens,
from models.dev. The price that the loop measures on the task wins over it.

| Operation | Fields | Answer |
|---|---|---|
| `list_models` | `family?`, `provider?`, `tags?` (≤12), `tools?`, `vision?`, `reasoning?`, `open_weights?`, `max_input_price?`, `include_not_available?` (default false), `include_deprecated?` (default false), `sort?` (`featured`, `price` or `newest`; default `featured`), `limit?` (≤200, default 50), `offset?` | one row per model: `model`, `name`, `family`, `featured_rank`, `tags`, `release_date`, `context_window`, `open_weights`, `thinking`, and `providers` (per entry: `provider`, `status`, `input_price`, `output_price`, `tags`, `deprecated`); plus `catalog` and `total` (the rows after the filters, on every page) |
| `get_model_details` | `model`, `provider?` | everything about one model: the row's fields, `editorial_notes`, `agent_notes` (notes for an agent that chooses or configures the model), `knowledge_cutoff`, `max_output_tokens`, `release_date_match` (`exact` or `loose`), and per provider entry its status, the four prices (input, output, cache read, cache write), `context_window`, `max_output_tokens`, `tags`, the provider's own `model_id`, `supports_tools`, `supports_structured_output`, `supports_vision`, `supports_doc_extraction`, `reasoning_capable`, `thinking_levels` (lowest first) and `default_thinking_level`, `structured_output_mode`, `takes_temperature`, `suggested_for`, `untested`, `deprecated` |
| `list_providers` | `include_not_available?` (default false) | per provider: `provider` (Kiln's id), `name`, `status` (`connected` or `not_connected`), `ready_models` (the count of `ready` pairs), `tags`, `notes` |

`list_models` rules:

- Filters are joined with AND. `tools`, `vision`, `reasoning`, `provider`
  and `max_input_price` test each provider entry, and remove the entries
  that fail. A row with no entry left is not listed. An entry with an
  unknown value for a filter that tests it does not pass.
- `family`, `open_weights` and `tags` test the row. A `tags` filter
  matches a tag on the model or on one of its listed entries.
- Without `include_not_available`, a row lists its `ready` entries only.
- `featured` is Kiln's rank, then the order of Kiln's file. `price` is the
  lowest `input_price + output_price` of the row's listed entries. `newest`
  is the release date. A row with no value is last.

`get_model_details` lists every entry of the model, of every status, and
deprecated entries too. For a pair that the server offers, the thinking
levels, the default level and `structured_output_mode` are the server's.

Some providers have no models.dev data, so their entries have no price
and no limits. Then:

- Call `get_model_details` without `provider`. The answer gives every
  provider entry of the model, also the entries on providers that are not
  connected.
- The price on another provider of the same model is a proxy. It is often
  close, but it is not the price that you will pay. Say in your notes that
  it is a proxy.
- A local provider (Ollama, Docker Model Runner) has no per-token price.
  Do not use another provider's price as a proxy for it. Use the cost that
  the loop measures.
- The price that the loop measures on the task wins over a listed price
  and over a proxy.

A model that is in neither the catalogue nor the server's list is
refused. A release date with `release_date_match: "loose"` came from a
match on the model's name only.

Examples:

```
mcp__kiln_harness_tuner_gateway__list_models(tools=true, sort="price",
    max_input_price=1.0)
mcp__kiln_harness_tuner_gateway__get_model_details(model="<model id>",
    provider="openrouter")
mcp__kiln_harness_tuner_gateway__list_providers(include_not_available=true)
```

### Reading the evals

| Operation | Fields | Answer |
|---|---|---|
| `list_evals` | — | every eval: id, name, description, current config id, output scores, and its TRAIN item count |
| `get_eval` | `id` | one eval |
| `get_eval_config` | `eval_id`, `eval_config_id` | the judge: type, model, properties (its prompt or steps) |
| `list_specs` / `get_spec` | — / `id` | specs: name, definition, priority, status, tags, eval id |
| `get_train_results` | `eval_id`, `run_config_id`, `item_ids?`, `limit?` (≤500, default 100), `offset?` | the run config's stored results on the eval's TRAIN items, one page: per item the scores, cost, latency, `output_preview` (the first 300 characters), `skipped_reason` when the run was skipped rather than scored, `trace_path` and `trace_summary`; plus `dropped` (rows not provably train), `total`, `next_offset`, `stats_path` (the run config's `_stats.md`) and `traces_dir` |

`get_train_results` always reads split `train` and the eval's current
(default) config — you never name a split or a config. Each call writes
the trace file of every train row it matched (all pages, not only the one
it returns) and rebuilds the run config's `_stats` files. A file whose
row changed is written again; the others are left as they are.

### Items, drives and runs

| Operation | Fields | Answer |
|---|---|---|
| `list_train_items` | `eval_id?`, `limit?` (≤200), `offset?` | train items: id, the evals it is train for, its input. `multi_turn: true`: the input is the opening user message, and a simulated user writes the rest of the conversation when the item is driven |
| `get_train_expected` | `eval_id?`, `item_ids?` (≤200), `limit?` (≤200, default 10), `offset?` | one page `{items, total, offset, next_offset}` of train items' expected outputs: id, the evals it is train for, `reference` (what a code eval compares against; JSON text with `clipped: true` past 12,000 characters, and then the whole of it in the file `reference_path`), its tags. `offset` and `limit` page through `item_ids` too. A named item that is not train is refused |
| `run_task` | `run_config_id`, `item_id`, `tags?` | drives a SAVED run config on ONE train item; the input is the item's own. On a multi-turn task, a run config with a world tool, or an item with a world reset, the drive is a one-item train eval job: the whole conversation, in a fresh episode, and `tags` do not reach the task run. Answer: the task run (output, usage, `trace_path`, `trace_summary`) |
| `get_task_run` | `id` | a task run — only when it ran on a train item, or this run created it: output, usage, `trace_path`, `trace_summary` |
| `get_answer_part` | `continuation`, `offset` | the next piece of an answer that was over the cap (every role but the preflight) |

There is no free-text drive: to drive a variant, create its run config
first (`create_run_config`), then `run_task` it on train items. A tag that
names a held-out split (`val`, `test`, `val_…`, `test_…`, `holdout…`, `heldout…`, any case) is refused.

### Memory

| Operation | Fields | Answer |
|---|---|---|
| `memory_list` | `scope`, `tags?`, `content_match?`, `limit?` (≤50), `offset?` | a page of records: id, overview, tags, scope, content length |
| `memory_get` | `ids` | the records, with content |
| `memory_summary` | `scope?` | counts per scope |
| `memory_create` | `overview` (≤280), `scope`, `content?`, `tags?` | the new record |
| `memory_update` | `id`, `overview?`, `content?`, `tags?` | the updated record |

`scope` is `project` or `task::<this task id>` — nothing else. The loop's
`val` record is not listed or fetched, except by the promotion agent and
the report writer. No agent can write a record with the `val` tag. The `memory` skill says what to write.

### Building a candidate

| Operation | Fields | Answer |
|---|---|---|
| `create_run_config` | `name`, `description?`, `run_config_properties`, `provenance?` | the new run config — refused when its model or provider is outside the run's model policy, or it is not a `kiln_agent` config |
| `rename_run_config` | `id`, `name` | the renamed config |
| `create_prompt` | `name`, `prompt`, `chain_of_thought_instructions?`, `description?`, `provenance?` | the new prompt |
| `list_code_tools` / `get_code_tool` | — / `id` | code tools, with their code |
| `create_code_tool` | `name`, `tool_function_name`, `tool_description`, `parameters_schema`, `code`, `description?`, `timeout_seconds?`, `tool_allowlist?`, `provenance?` | the new tool |
| `test_code_tool` | `tool_function_name`, `parameters_schema`, `code`, `params?`, … | a dry run: `result`, `error`, `traceback`, `stdout`, `stderr`, `not_trusted`, `tool_call_log`, `duration_ms` |
| `trust_code_tools` | — | trusts the project's code tools to run |
| `archive_code_tool` | `id`, `archived?` | the archived tool |
| `list_skills` / `get_skill` / `get_skill_content` | — / `id` / `id` | skills and their SKILL.md |
| `archive_skill` | `id`, `archived?` | the archived skill |
| `list_available_tools` / `get_tool_definition` | — / `id` | the tools a run config can attach: per set its `type`; per tool its id, name and `function_name` |

A skill is installed with the skill installer tool, not the gateway.

### Building an eval (eval_build)

Eval building is experimental and off by default. The loop starts an
eval_build session only when the run turns eval building on; while it is
off, no session has these operations.

| Operation | Fields | Answer |
|---|---|---|
| `create_spec` | `name` (32 characters at most), `definition`, `properties`, `priority?`, `status?`, `tags?`, `evaluate_full_trace?` | the spec |
| `create_eval` | `name`, `output_scores`, `evaluation_data_type`, `description?`, `template?`, `template_properties?` | the eval — the GATEWAY assigns its splits |
| `add_eval_items` | `eval_id`, `split` (`train`/`val`/`test`), `items` (`input`, `output`) | the created items — for an eval created through the gateway only |
| `create_eval_config` | `eval_id`, `name`, `type`, `properties`, `model_name?`, `provider?` | the config — for an eval you created only |
| `set_current_eval_config` | `eval_id`, `eval_config_id` | makes it the eval's judge — for an eval you created only |
| `test_judge` | `eval_id`, `properties`, `final_message`, `task_input?` | the judge's scores on the output you give, or why it skipped (`skipped_reason`, `score_range_errors`) |
| `run_train_eval` | `eval_id`, `run_config_id`, `item_ids?` | an eval job on the eval's TRAIN items only; the job's counts, and `error_message` when it failed |

Val and test items you add are never readable back: `get_train_results`
shows the train items only. Read results with `get_train_results` after
`run_train_eval` returns.

### The val readers (promote_best, report_writer)

| Operation | Fields | Answer |
|---|---|---|
| `get_val_summary` | — | the latest promotion gate's code-assembled val tables |
| `compute_statistics` | `operation`, numbers… | a statistics operation's numbers (promote_best) |

## Which role has which operations

| Role | Operations |
|---|---|
| preflight | `ping` |
| describe_eval | `get_task`, `list_evals`, `get_eval`, `get_eval_config`, `list_specs`, `get_spec` |
| batch_plan | reading the task, models and providers, reading the evals, `get_train_results`, `get_train_expected`, the artifact reads, memory |
| create_experiment_rc | batch_plan's, plus `list_train_items`, `get_task_run`, `run_task`, `create_run_config`, `rename_run_config`, `create_prompt`, the code-tool writes, `archive_code_tool`, `archive_skill` |
| probe | reading the task, models and providers, `list_train_items`, `get_train_expected`, `run_task`, `get_task_run`, `create_run_config`, the code-tool writes, `list_code_tools`, `list_skills`, memory (no update) |
| analyze_experiment_results | reading the task and the evals, `get_train_results`, `get_train_expected`, `get_task_run`, `get_code_tool`, `get_skill`, `get_skill_content`, memory |
| promote_best | `get_task`, `list_run_configs`, `list_evals`, `get_eval`, memory reads, `get_val_summary`, `compute_statistics` |
| report_writer | `get_task`, `list_run_configs`, `list_evals`, memory reads, `get_val_summary` |
| eval_build | `get_task`, `list_run_configs`, reading the evals, `get_train_results`, memory, the eval-build operations, the code-tool writes |
| baseline_review | reading the task and the evals, `get_train_results`, `list_train_items`, `get_skill`, `get_skill_content`, `get_code_tool`, `list_available_tools`, `get_tool_definition`, memory reads (no write of any kind) |
| integrity_review | none: no MCP server and no tool; its input is all it reads |
| generality_review | none: no MCP server and no tool; its input is all it reads |

Every role that has operations, except the preflight, also has
`get_answer_part`. The roles that read trace files — batch_plan,
create_experiment_rc, probe, analyze_experiment_results, eval_build and
baseline_review — may `Read`, `Grep` and `Glob` the trace folder (under
OpenCode, `Read` only).

The exact lists are `kiln_harness_tuner/gateway/ops.py` (`ROLE_OPS`). A test checks that
this skill names every operation. No test checks this table.

## Related

`memory` (what to write, and the record kinds) · `kiln-harness-tuner-batch-selection`
· `kiln-harness-tuner-experiment-build` · `kiln-harness-tuner-experiment-analysis` ·
`kiln-harness-tuner-probe` · `kiln-harness-tuner-promote` · `kiln-harness-tuner-report` ·
`kiln-harness-tuner-baseline-review`
