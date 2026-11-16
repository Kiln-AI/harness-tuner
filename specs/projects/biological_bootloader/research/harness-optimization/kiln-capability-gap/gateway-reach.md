# What the loop agents can reach

Read 2026-11-10 from `src/kiln_harness_tuner/gateway/` and
`src/kiln_harness_tuner/skill_installer/`.

## The surface

Loop agents have no generic Kiln API. They reach Kiln through:

1. **The gateway**: 47 typed operations (`gateway/ops.py:1252-1340`), given
   per role (`ROLE_OPS`, `gateway/ops.py:1353-1443`). A call outside the
   role's list is refused (`dispatch`, `gateway/ops.py:1471-1485`).
2. **The skill installer**: `install_skill_bundle` and `export_skill_bundle`
   (`skill_installer/mcp_server.py:34-75`), for the builder and the probe
   (`docs/architecture.md:946`).

Every operation is scoped to the run's one project and one task
(`State.base`, `State.project`, `gateway/ops.py:123-130`).

## Write operations, by object

| Object | Create | Change | Notes |
|---|---|---|---|
| Run config | `create_run_config` | `rename_run_config` only | `run_config_properties` is a free dict; the only checks are `type == kiln_agent` and the model policy (`gateway/ops.py:896-911`). |
| Prompt | `create_prompt` (`prompt`, `chain_of_thought_instructions`) | — | |
| Code tool | `create_code_tool`, `test_code_tool`, `trust_code_tools` | `archive_code_tool` | |
| Skill | `install_skill_bundle` | `archive_skill` | Bundle = `SKILL.md` + `references/` + `assets/`. |
| Eval, eval items, judge, spec | `create_eval`, `add_eval_items`, `create_eval_config`, `set_current_eval_config`, `create_spec` | — | Eval-build role only. Items are v1 tagged runs. |
| Memory | `memory_create` | `memory_update` | The tuner's own memory, not the subject's. |

## Not reachable (no operation)

- MCP tool servers: connect local, connect remote, edit, archive.
- Kiln task tools (sub-agents): create, edit; and any run config or prompt
  on a task other than the run's task.
- Documents, extractor, chunker, embedding, vector store, reranker and RAG
  configs; RAG search (`rag_configs/{id}/search`) for a test query.
- Worlds: list, get, list tools.
- Eval inputs v2 (`eval_inputs`), multi-turn synthetic items, world resets.
- Input-template validation (`validate_input_transform_template`).
- Fine-tunes, prompt optimization jobs, synthetic data generation, eval
  builder (out of scope by design for the first two; see the overview).
- Model capability flags (`supports_function_calling` and others): dropped
  by `list_available_models` (`gateway/models.py:114-119`).
- Task `turn_mode`: dropped by `get_task` (`gateway/models.py:65-73`).

## Reached only by pass-through

These work because `create_run_config` forwards the dict. No operation names
them, validates them, or lists their options:

- `input_transform` (Jinja template).
- `structured_output_mode` (not in `SCREEN_ONLY_OVERRIDES`,
  `schemas.py:682-688`, so not screenable; a probe may override it, as the
  example at `batch-selection/SKILL.md:866-868` shows).
- Tool ids of kinds the agent cannot create: `kiln_task::…`,
  `kiln_tool::rag::…`, `mcp::…`, `kiln_tool::world::…`,
  `kiln_tool::memory::…`. `list_available_tools` lists most of them, but not
  world tools or memory tools (Kiln's `available_tools` builds sets for
  search, MCP, Kiln task, skill, demo, code and sandbox-code only;
  `app/desktop/studio_server/tool_api.py:191-205, 300-480`).
- Decision-model and fine-tune providers, as models, subject to policy.

## The vendored API reference is behind main

`agent_files/skills/kiln-api/` was generated from spec version `1.0.4`
(`kiln-api/SKILL.md:6-9`). Comparing its endpoint files against route
strings in the Kiln main source (a text grep, so approximate) finds these
real routes missing from it:

- `/api/projects/{project_id}/worlds`, `/worlds/{world_id}`,
  `/worlds/{world_id}/tools`
- `/tasks/{task_id}/eval_inputs`, `/eval_inputs/{eval_input_id}`
- `/eval_builder/single_turn_pipeline`, `/multi_turn_pipeline`,
  `/author_judge`, `/judge_traces`, `/preflight_model`
- `/tasks/{task_id}/eval_default_judge_types`,
  `/tasks/{task_id}/run_configs/eval_scores`,
  `/evals/…/eval_run/{eval_run_id}/judge_trace`,
  `/tasks/{task_id}/available_spec_name`

This matters for any new gateway operation built from the vendored docs:
read the Kiln source, not only the reference.
