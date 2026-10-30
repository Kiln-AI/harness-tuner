---
name: kiln-api
description: |
  Complete reference for the Kiln REST API — all 265 endpoints the `call_kiln_api` tool can reach, across 25 catalogs: Agent, Code Tools, Copilot, Documents, Eval Builder, Evals, Feedback, Fine-tuning, Git Sync, Jobs, Memory, Multiturn SDG, Projects, Prompt Optimization, Prompts, Providers & Models, Run Configs, Runs, Settings & Utilities, Skills, Specs, Statistics, Synthetic Data, Tasks, Tools & MCP. Load this skill before any Kiln API call — route by intent to a catalog, then read the endpoint's reference file for its exact parameters, body fields and response shape. Use it instead of discovering endpoints at run time.
metadata:
  generated_by: an OpenAPI-to-skill generator, from the spec below
  spec_title: "Kiln AI API"
  spec_version: "1.0.4"
  spec_sha256: "0d712d5a67c4ab935af609ececa6efa5aa560858559f2e6f568d130efb89d742"
---

# kiln-api

Every endpoint of the Kiln API, generated from the server's own OpenAPI
document: 265 endpoints in 25 catalogs under
`references/api_docs/`. The 7 Judge Feedback Batches endpoints and their
catalog file are removed by hand: Kiln no longer serves them, so a call to any
of them returns 404. `spec_sha256` above names the document before that
removal. This is the control surface of a Kiln workspace — projects, tasks,
datasets, run configs, evals, jobs, model connections.

Everything below the routing index is a file you `Read`. You load this skill
once; after that you never invoke a skill again to reach an endpoint.

## Making a call

`call_kiln_api(method=, url_path=, body=, query_params=, jq_filter=)` — exposed
to interactive sessions as `mcp__kiln_api_mcp__call_kiln_api`. Kiln Harness
Tuner loop agents do not have this tool: they reach Kiln through the loop
gateway (the `kiln-harness-tuner-gateway` skill).

- **`method` and `url_path` are the two things you look up.** Take them from the
  catalog row exactly as printed, substituting ids inline:
  `call_kiln_api(method="GET", url_path="/api/projects/{project_id}/tasks/{task_id}/runs")`.
- **Query arguments go in `query_params`.** Never append `?key=value` to
  `url_path`. Correct:
  `call_kiln_api(method="GET", url_path="/api/jobs", query_params={"type": "eval"})`.

  **Nothing stops you getting this wrong.** A `url_path` carrying a `?` is
  forwarded as-is; it does not match a route, and what comes back is
  `{"status_code": 404, "body": "<!doctype html>…"}` — the studio web page, not
  an error message. Treat an HTML body as proof that the path never reached the
  API, and fix the call rather than reading the page.
- **`body` is the request body**, a dict shaped by the endpoint's reference file.
  Do not guess a body from an endpoint's name.
- **`jq_filter` is raw jq** applied to the response — see below.

## Finding the endpoint

1. Route by intent in the index below and `Read`
   `references/api_docs/<catalog file>`.
2. Pick the row whose path and method match what you want. Its **last column**
   names that endpoint's reference file.
3. **`Read` `references/api_docs/<filename>` before you call.** Every reference
   file sits directly in that one flat directory. Parameters, body fields,
   enums and the response shape live only there — never in the catalog row and
   never in this file.

Calling from memory of a catalog row, or from this file, is the failure mode
this skill exists to prevent. One extra `Read` costs less than one wrong call.

## Routing index

| Catalog | Endpoints | Use it for |
|---|---:|---|
| `agent_api_list.md` | 1 | Token-efficient overview endpoints designed for the Kiln chat agent. |
| `code_tools_api_list.md` | 7 | Create, test, and manage user-authored Python code tools. |
| `copilot_api_list.md` | 25 | AI copilot for spec generation, refinement, and data generation. |
| `documents_api_list.md` | 41 | Manage documents, extraction, chunking, embedding, vector stores, and RAG configurations. |
| `eval_builder_api_list.md` | 4 | Eval builder V2 review pipeline: judge and claim/evidence generation over alignment traces. |
| `evals_api_list.md` | 21 | Create and run evaluations for tasks. |
| `feedback_api_list.md` | 3 | Create and list feedback on task runs. |
| `finetuning_api_list.md` | 10 | Create and manage fine-tuning jobs. |
| `git_sync_api_list.md` | 14 | Git-based synchronization setup, configuration, and management. |
| `jobs_api_list.md` | 13 | Run, monitor, and control background jobs, and stream their events. |
| `memory_api_list.md` | 6 | The Kiln assistant's durable memory for a project (save, list, get, update, delete, summary). |
| `multiturn_sdg_api_list.md` | 2 | Generate multi-turn synthetic-user conversation datasets for eval. |
| `projects_api_list.md` | 6 | Create, read, update, delete, and import projects. |
| `prompt_optimization_api_list.md` | 7 | Run and monitor prompt optimization jobs. |
| `prompts_api_list.md` | 6 | Create and manage prompts for tasks. |
| `providers_models_api_list.md` | 18 | List and manage AI providers and models. |
| `run_configs_api_list.md` | 6 | Manage run configurations for tasks and evals. |
| `runs_api_list.md` | 14 | Execute tasks. View and manage the task run datastore. |
| `settings_utilities_api_list.md` | 8 | Server settings, connectivity checks, and utility endpoints. |
| `skills_api_list.md` | 6 | Create and manage agent skills within projects. |
| `specs_api_list.md` | 5 | Create and manage Kiln Specs: AI guided evaluation generation. |
| `statistics_api_list.md` | 1 | Confidence intervals and significance tests on eval metrics. |
| `synthetic_data_api_list.md` | 15 | Generate synthetic data for evals and fine-tuning. |
| `tasks_api_list.md` | 9 | Create and manage tasks within projects. |
| `tools_mcp_api_list.md` | 17 | Manage tool servers and MCP connections. |

## Writing a `jq_filter`

Responses are large — run, model and document payloads especially. Filter every
call down to the fields you actually need.

- **Quote the reference file's 200-response shape before writing the filter.**
  `array of X` → `.[] | {fields}`. A named wrapper (`data`, `items`) →
  `.wrapper[]`. Never write `.items[]` unless the documented shape has that key.
- `jq_filter` is raw jq inside a JSON string. Do not wrap it in shell quotes and
  do not quote field paths — `".output.output"` becomes a literal string.
  Correct: `{id, output: .output.output}`.

## Diagnose before you retry

A failed call is a **reading** problem, not a phrasing problem. Literal-string
output, a filter echoed back at you, `Cannot index array with string '…'`, or a
null where you expected a value all mean the shape you assumed is not the shape
the endpoint returns. The same entity can carry different field paths on
different endpoints — an aggregate snapshot is not the full entity list.

**Before re-issuing a failed call, quote in your reasoning the specific line of
that endpoint's reference file that names the correct field path.** A retry
without that cite is a violation whether or not the file is already in context;
the cite is what makes "re-read the doc" observable rather than a no-op. A
second guess from memory is banned, and so is abandoning this tool for a shell
after one failure.
