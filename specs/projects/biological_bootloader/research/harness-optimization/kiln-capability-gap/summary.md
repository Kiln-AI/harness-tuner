# Kiln capability gap

Read 2026-11-10. Kiln `main` at commit `3e67b49a58b6` (`/home/user/kiln-ai/kiln`), the Harness Tuner tree on the same date, and docs.kiln.tech pages fetched the same day.

## Bottom Line

Harness Tuner's loop agents know and can reach the prompt, the four sampling settings, the model (under policy), code tools, skill bundles and tool attachment. The skills teach these well. Most of the rest of Kiln's harness surface is a gap. Sub-agents (Kiln task tools), MCP servers, RAG configs and worlds can only be **attached** if they already exist: no gateway operation creates or changes them. Input templates and the nine structured-output modes can be set only by **pass-through**: `create_run_config` forwards a free `run_config_properties` dict, but no skill teaches when to use them, no screen can sweep them, and the integrity check does not read the template. Decision models (Jev) and OpenEnv worlds are **already merged to Kiln main**, which corrects the overview. Kiln skills cannot run code.

## Key Findings

- **The run config surface is small, and all of it can be set by pass-through.** `KilnAgentRunConfigProperties` has `model_name`, `model_provider_name`, `prompt_id`, `temperature`, `top_p`, `structured_output_mode`, `thinking_level`, `tools_config` and `input_transform`, and nothing else: no max tokens, seed or tool choice (`libs/core/kiln_ai/datamodel/run_config.py:48-90`). The gateway forwards it as a free dict (`src/kiln_harness_tuner/gateway/models.py:457-461`, `gateway/ops.py:896-911`). See [capability-table.md](./capability-table.md), section 1.
- **Input templates can be reached but are not taught.** Only `kiln-task-setup/SKILL.md:31` names the field. It is not a technique, it is not in the lever ladder, it cannot be screened (`schemas.py:682-688`), and the integrity check does not read it. The Kiln docs list the techniques: filter, rename, decode codes, truncate, reorder ([Input Templates](https://docs.kiln.tech/docs/input-templates-and-feature-engineering)).
- **Structured-output mode has nine values and is barely taught** (`datamodel_enums.py:36-58`). It is not in `SCREEN_ONLY_OVERRIDES`, although the `ScreenCandidate` docstring says it is.
- **Sub-agents can only be attached.** A Kiln task tool pins one `task_id` and one `run_config_id` (`external_tool_server.py:55-60`). The gateway is bound to one task and has no tool-server operation. So a loop agent cannot make a sub-agent, change its model, or wire a tuned sub-agent in. The code confirms "one turn only" (`tools/kiln_task_tool.py:64-116`).
- **RAG is a full gap.** None of these is taught by a loop skill or created by an operation (`datamodel/rag.py`, `chunk.py`, `embedding.py`, `vector_store.py`, `reranker.py`, `extraction.py`):
  - extractor: `litellm` only
  - chunker: `fixed_window` or `semantic`
  - embedding: model and dimensions
  - search: `lancedb_fts`, `lancedb_vector` or `lancedb_hybrid`, with top-k, overfetch and nprobes
  - reranker: `cohere_compatible`, with `top_n`
  - the search tool's name, description and document-tag filter
- **Skills cannot run code.** The skill tool reads `references/` and `assets/` as UTF-8 text only (`tools/skill_tool.py:10`, `datamodel/skill.py:74-95`). This answers overview 6.2.
- **The decision model Jev is on main.** It uses the provider `typesafe` (`adapters/model_adapters/jev_adapter.py`). It needs a single-turn task whose structured output is enums, booleans, small integers or 0–1 numbers, and it has no tools. A loop agent can reach it as a model under policy, but no loop skill mentions it. Kiln has no docs page for it yet.
- **OpenEnv worlds are on main**: `datamodel/world.py`, world tool ids, and the `/worlds` routes. The gateway already drives world items as eval jobs. The vendored API reference is missing the `/worlds` and `eval_inputs` routes ([gateway-reach.md](./gateway-reach.md)).
- **The overview misses these capabilities** (detail in [overview-check.md](./overview-check.md)):
  - the `llm` built-in tool, which code tools can call (a cheap reader inside a wrapper)
  - prompt generators, including few-shot, which may leak split items into the prompt
  - chain-of-thought instructions
  - MCP run configs, whose agents run outside Kiln and cannot be tuned
  - memory tools on the subject agent
  - the RAG search tool's description and tag filter
  - the reranker and semantic chunking
  - world concurrency and versioning red flags
  - the fine-tune `task_filter`, which Harness Tuner ignores
- **One overview claim about evals is wrong.** Harness Tuner has an `eval_build` role that writes evals (off by default, `orchestrator.py:436`). It writes only single-turn, v1-style items: no synthetic users and no world resets.
- **Degrees of freedom:**
  - `turn_mode`
  - output schema, where "classifier-shaped" is a class of its own
  - input schema
  - `thinking_instruction`
  - run config type (`kiln_agent` or `mcp`)
  - tool kinds
  - model class
  - eval input type

  Loop agents cannot see `turn_mode`, because `TaskView` omits it (`gateway/models.py:65-73`). See [degrees-of-freedom.md](./degrees-of-freedom.md).

## Details

- [capability-table.md](./capability-table.md): the full table, one row per capability, with Knows and Reach columns and source lines.
- [gateway-reach.md](./gateway-reach.md): the 47 gateway operations by object, what is pass-through only, what cannot be reached, and the routes missing from the vendored API reference.
- [degrees-of-freedom.md](./degrees-of-freedom.md): the task, run config and eval fields that classify an agent, and suggested axes.
- [overview-check.md](./overview-check.md): each overview claim marked confirmed, corrected or missed.

## Open Questions / Gaps

- I could not find which parts of OpenEnv the overview calls "not merged". On main I found the data model, tool ids, routes and episode handling. The clone is shallow and has no other branches, so unmerged work could not be checked.
- No code or spec on main describes the "more decision-model uses" the overview says are coming. Jev could probably be used as a sub-agent behind a Kiln task tool today. That is untested.
- The few-shot leak is inferred from `adapters/prompt_builders.py:204-233`. I did not test whether the integrity check catches leaked examples in a frozen prompt.
- That a code tool can call `kiln_tool::llm` through its allowlist is inferred from the code; I did not run it.
- I compared routes with the vendored reference by grepping decorator lines. Routes declared in another style may be missed.
- No Kiln docs page covers Jev, worlds or the `mcp` run config in depth, so those findings rest on code only.

## Sources

- Kiln repository, `main`, commit `3e67b49a58b6` (2026-11-10). Authoritative for what Kiln can do:
  - `libs/core/kiln_ai/datamodel/{run_config,task,tool_id,input_transform,skill,code_tool,external_tool_server,rag,chunk,embedding,vector_store,reranker,extraction,world,eval,finetune,prompt_id,datamodel_enums}.py`
  - `tools/{skill_tool,kiln_task_tool,code_tool}.py` and `tools/built_in_tools/llm_tools.py`
  - `adapters/{prompt_builders.py,model_adapters/jev_adapter.py,ml_model_list.py}`
  - `app/desktop/studio_server/{tool_api,provider_api}.py`
  - `specs/projects/{templates,input_transform_ui,input_transform_create_ui,jev_provider}`
- Harness Tuner, 2026-11-10. Authoritative for what the loop agents know and reach: `gateway/{ops,models}.py`, `skill_installer/`, `schemas.py`, `model_policy.py`, `orchestrator.py`, `agent_files/skills/**`, `agents/*.md`, `docs/architecture.md`.
- docs.kiln.tech, fetched 2026-11-10:
  - [Input Templates & Feature Engineering](https://docs.kiln.tech/docs/input-templates-and-feature-engineering) (updated about August 2026)
  - [Skills](https://docs.kiln.tech/docs/skills)
  - [Documents & Search (RAG)](https://docs.kiln.tech/docs/documents-and-search-rag)
  - [Tasks](https://docs.kiln.tech/docs/tasks.md)
  - [Structured Data / JSON](https://docs.kiln.tech/docs/structured-data-json.md)
  - [Reasoning & Chain of Thought](https://docs.kiln.tech/docs/reasoning-and-chain-of-thought.md)
  - [Evaluations](https://docs.kiln.tech/docs/evaluations)
  - [Agents](https://docs.kiln.tech/docs/agents)
  - [sitemap](https://docs.kiln.tech/sitemap.md)
