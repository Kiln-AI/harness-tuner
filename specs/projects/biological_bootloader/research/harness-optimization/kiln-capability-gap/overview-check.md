# The project overview against the code

Each claim from `specs/projects/biological_bootloader/project_overview.md`
that this subtopic can test, checked against Kiln `main` (commit
`3e67b49a58b6`, 2026-11-10) and the Harness Tuner tree on the same date.

## Confirms

| Overview claim | Evidence |
|---|---|
| 6.2: Skills are `SKILL.md` plus resource files. | `datamodel/skill.py:13-95`: `references/` and `assets/`, read as text. |
| 6.3: Kiln code tools can wrap other tools. | `datamodel/code_tool.py:77-80` (`tool_allowlist`); nested calls through `kiln.tools` (`code-tool-authoring/SKILL.md:113-130`). |
| 6.4: Settings are temperature, top-p, structured-output mode, thinking level. | `run_config.py:63-78`. These four, plus the prompt, tools and input transform, are the whole sampling surface. |
| 7.2: Sub-agents are Kiln tasks attached as tools; one turn only; no state between calls. | `external_tool_server.py:55-60`; `tools/kiln_task_tool.py:64-116` (fresh run, no parent run id). |
| 7.2: A sub-agent is a Kiln task, so it can be tuned with its own evals. | It is a task with its own run configs. But the pinned `run_config_id` in the tool server means the parent must be re-wired to a new task tool to use a tuned sub-agent run config. |
| 7.3: Input transforms are Jinja templates applied before the user message reaches the model. | `datamodel/input_transform.py`; docs page "Input Templates & Feature Engineering". |
| 7.1: RAG configs have chunking, embedding model, and vector, full-text or hybrid search. | `chunk.py:36-55`; `embedding.py:15-38`; `vector_store.py:14-45`. |
| 7.1: Extraction needs a model, usually Gemini. | Docs RAG page: suggested configs use Gemini 2.5 Pro or Flash. |
| 7.6: Existing fine-tunes are candidate models. | `kiln_fine_tune` provider in `/api/available_models` (`provider_api.py:2299`). |
| 7.8: Multi-turn evals use synthetic users. | `eval.py:605-670` (`SyntheticUserInfo`, `MultiTurnDriveConfig`). |
| 7.9: No compaction; no automatic mid-conversation model switch. | No compaction code on main. Per-turn model change exists only by hand (docs, Tasks page). |
| 4: One request with many tool calls is still single-turn. | Kiln docs, Tasks page: "One turn can include many tool calls, so an agent that handles each request on its own is still single-turn." |

## Contradicts or corrects

| Overview claim | What the code shows |
|---|---|
| 6.2: "possibly code files; check whether Kiln supports those". | Kiln skills do not run code. Resources are read as UTF-8 text from `references/` or `assets/` only (`tools/skill_tool.py:10`; `skill.py:74-95`). |
| 7.4: Decision models: "More uses are coming to the Kiln harness but are not shipped." | Jev 1.13 is shipped on main as a model (provider `typesafe`, `adapters/model_adapters/jev_adapter.py`; Kiln spec `jev_provider` status complete). A run config can use it today for a compatible single-turn task. What is unshipped (other uses) I could not find in code. |
| 7.4: "Luna Decisions, Cloudflare Clef, Kev". | Only Jev (TypeSafe AI) has a Kiln provider on main. The others are not in `ModelProviderName` (`datamodel_enums.py:112-137`). |
| 7.7: OpenEnv "not all of it is merged to main". | The `World` data model, world tool ids, `/worlds` routes and the eval runner's episode handling are on main (`datamodel/world.py`; `tool_id.py:22-24`). I could not find which part is still unmerged. |
| 7.8: "If there are no evals, the tuner does not write them." | Harness Tuner has an `eval_build` role with write operations (`create_eval`, `add_eval_items`, `create_eval_config`, `test_judge`; `gateway/ops.py:1418-1433`). It is off by default (`eval_building: bool = False`, `orchestrator.py:436`). The overview's rule holds only while that flag is off. |
| 7.6: "The tuner does not recommend or start a fine-tune." | Batch selection allows a fine-tune arm when the kickoff guidance asks for one (`batch-selection/SKILL.md:58-61`). No operation can start one, so it cannot actually be built. |

## Misses (capabilities the overview does not mention)

1. **The `llm` built-in tool inside code tools** (`tools/built_in_tools/llm_tools.py`). A wrapper can call a model to summarise, extract or classify a large result before it reaches the agent. It is a cheaper pattern than a sub-agent for "a cheap reader" (overview 7.2). No loop skill mentions it.
2. **Prompt generators** (`few_shot_…`, `multi_shot_…`, `…_chain_of_thought_…`, `repairs_prompt_builder`; `prompt_id.py:11-17`). Few-shot examples come from rated or repaired task runs. A lever for classifiers, and a possible split leak (`adapters/prompt_builders.py:204-233`).
3. **Chain-of-thought instructions and the two-message flow** for non-reasoning models (`Task.thinking_instruction`, `Prompt.chain_of_thought_instructions`; docs "Reasoning & Chain of Thought"). A lever separate from `thinking_level`.
4. **MCP run configs** (`type: "mcp"`): the agent runs outside Kiln. Harness Tuner can measure but not tune it, and refuses to create one. The classification step should detect this.
5. **Agent memory tools** (`kiln_tool::memory::*`) as a subject-agent capability. They share the project memory store the tuner uses.
6. **RAG search-tool name, description and document-tag filter** (`rag.py:30-63`). These are cheaper levers than chunking or embedding.
7. **Reranker** (`reranker.py`, `cohere_compatible`, `top_n`) and **semantic chunking** (`chunk.py:43-50`). The overview names chunker, embedding and search type, not these.
8. **Eval-side settings that look like harness settings**: the synthetic user's model and turn count (`MultiTurnDriveConfig`), the judge's own run config (`LlmJudgeRunConfig`, `eval.py:113-140`). The tuner must not change these as levers.
9. **World operating limits** that cause eval failures for environment reasons: concurrency below 25, and an env that changes without a version bump reuses old traces (`world.py:12-25`). These are code-checkable red flags for overview 7.7 and 9.
10. **Fine-tune metadata**: `task_filter` and `model_specific_run_config` on a fine-tune model are dropped by Harness Tuner's model list (`model_policy.py:241-271`). A fine-tune for another task could be offered.
11. **Structured-output modes are nine, not three** (`datamodel_enums.py:36-58`), including two function-calling variants and `json_custom_instructions`.
12. **Turn mode is not visible to loop agents**: `TaskView` omits `turn_mode` (`gateway/models.py:65-73`). The first classification axis in overview section 4 cannot be read directly.
