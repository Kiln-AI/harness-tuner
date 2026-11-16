# Kiln harness capabilities: known and reachable by the loop agents

Date of reading: 2026-11-10. Kiln: `/home/user/kiln-ai/kiln`, branch `main`,
commit `3e67b49a58b6` (shallow clone, committed 2026-11-10). Harness Tuner:
the working tree of `/home/user/harness-tuner` on the same date.

## How to read the table

- **Knows**: is the capability described to a loop agent in a loop skill
  (`src/kiln_harness_tuner/agent_files/skills/`) or a role prompt
  (`src/kiln_harness_tuner/agents/`)?
  - **Taught**: a skill tells the agent when to use it (a lever).
  - **Named**: a skill names the field or the tool id, but gives no
    guidance on when it helps.
  - **No**: no loop skill or prompt mentions it.
  - The `kiln-api` skill (the vendored REST reference) is installed for
    loop agents too, but its own text says loop agents do not have
    `call_kiln_api` (`agent_files/skills/kiln-api/SKILL.md:27-30`). A
    capability that is only in `kiln-api` is marked "No (API doc only)".
- **Reach**: can a loop agent change it through the gateway
  (`gateway/ops.py`, `OPS` at line 1252 and `ROLE_OPS` at line 1353) or the
  skill installer (`skill_installer/mcp_server.py`)?
  - **Yes**: a typed operation does it.
  - **Pass-through**: `create_run_config` takes `run_config_properties` as a
    free `dict` (`gateway/models.py:457-461`) and forwards it to Kiln after a
    model-policy check (`gateway/ops.py:896-911`). Any field Kiln's run
    config accepts can be set this way, but no operation validates or
    describes it.
  - **Attach only**: an existing object can be attached to a new run config
    by its tool id (`list_available_tools`, `get_tool_definition`), but no
    operation creates or changes the object.
  - **No**: no operation reaches it.

"Inferred" marks a claim I reasoned from code and did not run.

## 1. Run config fields and model settings

Source: `libs/core/kiln_ai/datamodel/run_config.py:48-110`
(`KilnAgentRunConfigProperties`).

| Capability | Kiln source | Knows | Reach | Notes |
|---|---|---|---|---|
| `model_name`, `model_provider_name` | `run_config.py:56-61` | Taught (model swap is off by default; `batch-selection/SKILL.md:58-70`) | Yes (`create_run_config`, policy-checked; screens use `SCREEN_ONLY_OVERRIDES`, `schemas.py:682-688`) | `list_available_models` returns only provider, model, name, thinking levels and the default output mode (`gateway/models.py:114-119`). It drops Kiln's capability flags: `supports_function_calling`, `supports_structured_output`, `supports_logprobs`, `supports_vision`, `default_thinking_level`, `deprecated`, `task_filter`, `model_specific_run_config` (`app/desktop/studio_server/provider_api.py:160-186`). |
| `prompt_id` (saved prompt, generator, frozen, fine-tune prompt) | `datamodel/prompt_id.py:11-17, 45-68` | Named (`kiln-task-setup/SKILL.md:31`; `get_prompt_text` example) | Yes (`create_prompt` + `create_run_config`) | The prompt generators (`few_shot_prompt_builder`, `multi_shot_…`, `…_chain_of_thought_…`, `repairs_prompt_builder`) are not taught as a lever. See the leakage note below. |
| `temperature` (0–2), `top_p` (0–1) | `run_config.py:63-70, 102-110` | Taught (`params` arm; `lever-ladder.md:55`) | Yes | |
| `thinking_level` | `run_config.py:75-78`; levels per model in `adapters/ml_model_list.py` (e.g. lines 664-693) | Taught (the thinking ladder; `batch-selection/SKILL.md:460-480`) | Yes | Code checks that a level took effect (`levers.py`). |
| `structured_output_mode` | `run_config.py:71-73`; enum `datamodel_enums.py:36-58` | Named, partly taught (a probe example switches to `json_instructions`/`json_mode`, `batch-selection/SKILL.md:866-868`; `lever-ladder.md:56` says an output-mode change "is a lever of its own") | Pass-through (it is not in `SCREEN_ONLY_OVERRIDES`; the `ScreenCandidate` docstring says "output mode" but the tuple omits it — `schemas.py:682-695`) | Nine values: `default`, `json_schema`, `function_calling_weak`, `function_calling`, `json_mode`, `json_instructions`, `json_instruction_and_object`, `json_custom_instructions`, `unknown`. No skill explains them. |
| `tools_config.tools` | `run_config.py:79-82` | Taught (`tool_config` technique) | Yes (tool ids) | |
| `input_transform` (Jinja) | `run_config.py:82-89`; `datamodel/input_transform.py` | Named only (`kiln-task-setup/SKILL.md:31`) | Pass-through (the gateway shows it in views: `RC_PROP_KEYS`, `gateway/ops.py:53-65`) | Not a technique, not in the lever ladder, not in screen overrides. The integrity check reads prompt, skill and code-tool text, not the template (inferred from `docs/architecture.md:315-350`; no `input_transform` reference in `integrity.py`). Kiln's `POST /api/validate_input_transform_template` has no gateway operation; an invalid template fails `create_run_config` with 422. |
| Run config `type: "mcp"` (run the task through an external agent served over MCP) | `run_config.py:113-121`; route `/tasks/{task_id}/mcp_run_config` | No (API doc only) | No — refused by design: "only kiln_agent run configs can be created here" (`gateway/ops.py:897-898`) | Kiln docs: "Connect to Existing Agents … using MCP" (docs.kiln.tech sitemap). A task whose baseline is an MCP run config has no harness the tuner can edit. |
| `skills_config` | none on Kiln main | — | — | Harness Tuner lists `skills_config` in `RC_PROP_KEYS` (`gateway/ops.py:63`). No such field exists on Kiln main (`grep -r skills_config` finds nothing). Skills attach as tools (`kiln_tool::skill::<id>`). A stale key; harmless. |
| Not tunable on Kiln main | — | — | — | No `max_tokens`, seed, stop sequences, `tool_choice`, parallel-tool-call switch, penalties or context limit in the run config (`run_config.py:48-90`). |

**Leakage note (inferred, not tested).** `MultiShotPromptBuilder.collect_examples`
takes examples from `task.runs()` — repaired runs first, then runs rated
4 stars or more (`adapters/prompt_builders.py:204-233`). It does not look at
eval split tags. In a project whose eval items are tagged dataset runs (the
shape Harness Tuner's own `add_eval_items` writes, `gateway/ops.py:1092-1115`),
a few-shot generator could put val or test items into the prompt. Kiln freezes
a generator prompt into the run config (`kiln-task-setup/SKILL.md:31`), and
the integrity check reads the frozen prompt text, so it may catch this. Not
confirmed.

## 2. Tools

Tool id forms: `libs/core/kiln_ai/datamodel/tool_id.py:13-49`.

| Capability | Kiln source | Knows | Reach | Notes |
|---|---|---|---|---|
| Code tools (sandboxed Python, nested tool calls through `tool_allowlist`, timeout) | `datamodel/code_tool.py:39-80`; `tools/code_tool.py` | Taught (`kiln-harness-tuner-code-tool-authoring`, wrappers README, lever ladder rows) | Yes (`create_code_tool`, `test_code_tool`, `trust_code_tools`, `archive_code_tool`) | Strongest-covered lever. |
| Built-in `llm` tool, callable from code tools (`kiln_tool::llm`) | `tools/built_in_tools/llm_tools.py:1-60`; listed as set "AI Models" / `sandbox_code` in `tool_api.py:300-330` | No (the code-tool skill lists allowlist kinds but not `llm`; `code-tool-authoring/SKILL.md:115-130`) | Yes in principle: a code tool's `tool_allowlist` may name it (inferred; not tested) | A code tool can call a cheap model to summarise or extract from a large tool result. This is a pipeline step and a cheap "reader" without a full sub-agent. Not in the overview. |
| `llm_judge` built-in | same file | No | Only inside code judges | Eval-side only. |
| MCP tool servers, local and remote | `datamodel/external_tool_server.py:18-60`; routes `connect_local_mcp`, `connect_remote_mcp` | Named (tool ids; wrappers over MCP tools are taught) | Attach only. No operation connects a new server. | Wrapping an MCP tool in a code tool is reachable and taught. |
| Kiln task as tool (sub-agent) | `external_tool_server.py:55-60` (`task_id`, `run_config_id`, `name`, `description`); `tools/kiln_task_tool.py:64-116` | Named only (`kiln-task-setup/SKILL.md:33`; `merging.md:19` says "sub-agent wiring") | Attach only. No operation creates a task tool, edits one (`edit_kiln_task_tool`), or creates a run config on the sub-task: the gateway is bound to one task (`State.base`). | The tool pins one run config of the sub-task. To try a different sub-agent model or prompt, a new task tool is needed. Each call runs a fresh task run with no `parent_task_run_id` (`kiln_task_tool.py:94-110`): confirms "one turn only". The sub-agent's skills load from its own run config (`load_skills_for_task`, line 91). |
| RAG search tool (`kiln_tool::rag::<rag_config_id>`) | `tool_id.py:41, 109-115`; `tools/rag_tools.py` | Taught weakly: one lever-ladder row, "Attach the source (RAG tool or skill)" (`lever-ladder.md:44`) | Attach only (existing RAG configs) | See section 3. |
| Skills (`kiln_tool::skill::<id>`) | `datamodel/skill.py`; `tools/skill_tool.py` | Taught (`experiment-build/references/skill-authoring.md`, lever ladder) | Yes (`install_skill_bundle`, `export_skill_bundle`, `archive_skill`, `list_skills`, `get_skill_content`) | See section 4. |
| Memory tools for the subject agent (`kiln_tool::memory::save|list|get|update|delete|summary`) | `tool_id.py:20, 47, 175-200`; `tools/memory_tools.py` | Named as forbidden in code-tool allowlists (`code-tool-authoring/SKILL.md:120-125`) | Attach only (pass-through id) | Not presented as a lever for the subject agent. They read and write the project memory store, which the tuner also uses, so attaching them to a subject is risky. |
| World tools (`kiln_tool::world::<world_id>::<tool>`) | `tool_id.py:22-24, 249-275`; `datamodel/world.py` | Named (gateway and analysis skills know that a world drive is an eval job) | Attach only. No world operation; the vendored API docs have no `/worlds` routes. | See section 6. |
| Unmanaged SDK tools (`kiln_unmanaged::<slug>`) | `tool_id.py:21, 52-80` | No | No | Injected by an SDK caller; not a harness lever in Kiln projects. |
| Demo math tools, `call_kiln_api` | `tool_id.py:30-38` | Named (`call_kiln_api` forbidden in allowlists) | Attach only | Not levers. |

## 3. RAG configs

Sources: `datamodel/rag.py:13-66`, `chunk.py:36-113`, `embedding.py:15-38`,
`vector_store.py:14-69`, `reranker.py:14-47`, `extraction.py:53-180`.
Docs: <https://docs.kiln.tech/docs/documents-and-search-rag> (fetched
2026-11-10).

| Parameter | Values on Kiln main | Knows | Reach |
|---|---|---|---|
| RAG config (bundle of the four configs below, plus `tool_name`, `tool_description`, `tags` document filter, optional reranker) | `rag.py:30-63` | No (API doc only) | No create (`create_rag_config` not in gateway); attach only |
| Tool name and description of the search tool | `rag.py:30-37` | No | No. A code-tool wrapper can give it a new description (taught generally). |
| Document tag filter | `rag.py:60-63` | No | No |
| Extractor | type `litellm` only; model, provider, output format text or markdown, passthrough mime types, four prompts (document, image, video, audio) | No | No |
| Chunker | `fixed_window` (`chunk_size`, `chunk_overlap`) or `semantic` (embedding config, `buffer_size`, `breakpoint_percentile_threshold`, metadata flags) | No | No |
| Embedding | provider, model, optional `dimensions` | No | No |
| Search type (vector store) | `lancedb_fts`, `lancedb_vector`, `lancedb_hybrid`; `similarity_top_k`, `overfetch_factor`, `nprobes` (vector and hybrid) | No | No |
| Reranker | type `cohere_compatible`; provider, model, `top_n` | No | No |
| Documents (upload, tags, extraction runs) | `extraction.py:259-300` | No | No (and Harness Tuner policy says agents ask the user to upload) |

Every RAG parameter is a gap of both kinds. A new search configuration also
needs a long indexing job (extract, chunk, embed), which does not fit the
gateway's short-call design (inferred).

## 4. Skills

| Capability | Kiln | Knows | Reach |
|---|---|---|---|
| `SKILL.md` with name and description (≤1024 chars) | `skill.py:13-40` | Taught | Yes (installer) |
| `references/` text files, nested | `skill.py:60-95`; `skill_tool.py:10` | Taught | Yes (installer requires UTF-8 under `references/`; `skill_installer/bundle.py:246-252`) |
| `assets/` files | `skill.py:66-72` | Named | Yes (installer) |
| Executable code in a skill (`scripts/`) | **Not supported.** The skill tool reads only `references/` and `assets/` as UTF-8 text (`skill_tool.py:10`; `skill.py:74-95` decodes every resource as text). Kiln docs list only `references/` (<https://docs.kiln.tech/docs/skills>). | n/a | n/a |
| Edit in place | Not allowed: skills are immutable; clone instead (docs, "Editing / Cloning Skills"; `PATCH` toggles `is_archived` only) | Taught | Yes (new bundle under a new name) |

Answer to the overview's question (section 6.2): Kiln skills do **not** run
code. Code goes in code tools. A skill can hold code as text (an example
file in `assets/`), which the model reads but cannot run.

## 5. Input templates and feature engineering

| Capability | Kiln | Knows | Reach |
|---|---|---|---|
| Jinja2 input transform on the run config; one variable `input`; built-in filters only; sandboxed; plaintext JSON is parsed for field access | `input_transform.py`; docs <https://docs.kiln.tech/docs/input-templates-and-feature-engineering> (updated about Aug 2026) | Named only | Pass-through |
| Template validation endpoint | `/api/validate_input_transform_template` | No (API doc only) | No (422 on save is the only check) |
| Other transform types | Only `jinja` on main (`input_transform.py:31-34`) | — | — |

The Kiln docs list filtering IDs, renaming fields, decoding enum codes,
truncation (`{{ input.description[:300] }}`), ordering, and pretty-printing
JSON as techniques. The template applies to the first user message only.
On a multi-turn task the input is plain text (inferred from
`task.py:191-205`).

## 6. Models outside plain LLMs

| Capability | Kiln | Knows | Reach |
|---|---|---|---|
| Fine-tunes as models (provider `kiln_fine_tune`) | `provider_api.py:922-923, 2299`; `datamodel/finetune.py:24-93` | Named, excluded as a lever unless the kickoff guidance asks (`batch-selection/SKILL.md:58-61`) | Yes as a model, when the policy allows `kiln_fine_tune` and the server lists it. Creating a fine-tune: No (correct per overview). | `allowed_models` ignores `task_filter` and `model_specific_run_config` (`model_policy.py:241-271`): a fine-tune made for another task can appear, and its baked run config (prompt, output mode) is not shown (inferred). |
| Decision model: Jev 1.13 by TypeSafe AI | **Merged to main**: provider `typesafe` (`datamodel_enums.py:137`), adapter `adapters/model_adapters/jev_adapter.py`, model entry `ml_model_list.py:12220-12236`; Kiln spec `specs/projects/jev_provider/` status complete | No | Yes as a model (`create_run_config` with `model_provider_name: "typesafe"`), when the user has a TypeSafe key and the policy allows it (inferred) | Constraints from code: single-turn, structured output only, schema properties must be a string enum, integer enum, boolean, integer with 2–10 values, or a number 0–1 (`jev_adapter.py:44-48`); no tools, no function calling, no logprobs (`ml_model_list.py:12230-12236`). The input becomes a `state` object `{task_instructions, input}` (`jev_adapter.py:52-60`). No Kiln docs page exists for Jev (sitemap, 2026-11-10). |
| Kiln's automatic prompt optimizer (hosted job) | `datamodel/prompt_optimization_job.py`; route `prompt_optimization_jobs/start` | Excluded on purpose (`schemas.py:188-189`) | No | Out of scope by design. |

## 7. OpenEnv worlds (stateful eval environments)

| Capability | Kiln | Knows | Reach |
|---|---|---|---|
| `World` (pointer to a running OpenEnv env at `env_url`), `WorldReset` on an eval input, `WorldEpisode` on the trace | **Merged to main**: `datamodel/world.py:1-150`; routes `/projects/{id}/worlds`, `/worlds/{world_id}`, `/worlds/{world_id}/tools` | Named: a world drive is a one-item eval job (`gateway/SKILL.md:141`; `gateway/ops.py:924-934`); analysis skill can name a world failure (`experiment-analysis/SKILL.md:347`) | Attach only (world tool ids pass through). No world operation. The vendored API docs lack the `/worlds` routes. | The world doc says: up to 25 eval jobs at once, so the env needs `max_concurrent_envs` ≥ 25; an env must bump its `/metadata` version when it changes, or old traces are reused (`world.py:12-25`). These are eval-environment red flags the tuner could check. The overview says "not all of it is merged"; I found the data model, tool ids, routes and runner on main. Which parts the owner means as unmerged I could not determine. |

## 8. Evals, synthetic users and the eval builder

| Capability | Kiln | Knows | Reach |
|---|---|---|---|
| Eval v2 judge types: `llm_judge` (with G-Eval and its own run config), `exact_match`, `pattern_match`, `contains`, `set_check`, `tool_call_check`, `step_count_check`, `code_eval` | `datamodel/eval.py:99-290` | Named in eval-build role (inferred; `create_eval_config` takes free `type` and `properties`) | Yes for eval-build role only (`create_eval_config`, `test_judge`); off unless `eval_building` is on (`orchestrator.py:436`) | Harness Tuner's eval build writes v1-style items: tagged dataset runs with `input`/`output` strings (`gateway/ops.py:1092-1115`, `gateway/models.py:529-540`). |
| Eval inputs v2 (`EvalInput`: `data`, `reference`, `tags`, `world_reset`) | `eval.py:704-730`; routes `eval_inputs` | No | No (vendored docs lack `eval_inputs`) | |
| Multi-turn synthetic users: `SyntheticUserInfo` (`persona`, `goal`, `behavior_guidance`), `MultiTurnDriveConfig` (user model, provider, `turns`) | `eval.py:605-670` | Named (multi-turn synthetic drive in `experiment-costing/SKILL.md:74`; gateway runs them as eval jobs) | Measured, not editable. The eval-build role cannot write a multi-turn item. | The synthetic user's model and turn count are eval settings, not harness settings. |
| Eval builder (single-turn and multi-turn pipelines, claims, judge authoring, judge refine, trace review) | routes `eval_builder/*` (`single_turn_pipeline`, `multi_turn_pipeline`, `author_judge`, `build_claims`, `refine_judge`, `judge_traces`, `review_traces`, `review_pipeline`, `preflight_model`) | No (one line in `kiln-api/SKILL.md` lists the catalog) | No | Vendored docs have four of these and lack `single_turn_pipeline`, `multi_turn_pipeline`, `author_judge`, `judge_traces`, `preflight_model`. |
| Synthetic data generation, multi-turn SDG, data guides | routes `generate_inputs*`, `multiturn_sdg/*`, `data_gen_guide*` | No (API doc only) | No | |

## 9. Other Kiln features relevant to a harness

| Capability | Kiln | Knows | Reach | Notes |
|---|---|---|---|---|
| Chain-of-thought prompts: `thinking_instruction` on the task, `chain_of_thought_instructions` on a prompt; a two-message flow for non-reasoning models | `task.py:174-177`; `datamodel_enums.py:80-92`; docs "Reasoning & Chain of Thought" | Named (`PromptCreate` accepts it, `gateway/models.py:469-474`) | Yes | Not taught as a lever separate from thinking level. |
| Model or tools changed between turns of a multi-turn conversation | Kiln docs, Tasks page: "Change the model or tools in Options between turns. The prompt stays as it was on the first turn." Each turn is a `POST /run` with its own `run_config_properties`. | No | No | This is manual, per turn. It is not an automatic in-conversation switch, so it confirms overview 7.9. |
| Compaction | No code on main (`grep -ri compaction libs/core/kiln_ai` finds nothing) | — | — | Confirms overview 7.9. |
| Task default run config | `task.py:179-182` | Named | No (promotion is the orchestrator's) | |
