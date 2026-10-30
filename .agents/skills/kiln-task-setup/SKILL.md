---
name: kiln-task-setup
description: Create and wire a Kiln task end-to-end via the local studio_server REST API — single-turn or multi-turn, versioned prompt, run config with tools (built-in / MCP / skill / code tool), project-level skill with progressive-discovery knowledge files, and turn-by-turn conversation driving. Use when "create a Kiln task", "port an assistant/prompt into Kiln", "set up a run config with tools", "attach a skill", "drive a multi-turn conversation via API".
---

# kiln-task-setup

How to build a Kiln task over the API: task → prompt → run config → tools → (optionally) conversations. Every endpoint and payload below was verified against a running studio_server while building a task this way.

## Prerequisites

- The local studio_server running (README setup names the port) with provider
  keys configured. Reach it through `call_kiln_api` — pass a method and a path,
  never a host.
- Verify the model pair first: `GET /api/available_models` — the `(models[].id, provider_id)` pair must appear; require `supports_function_calling: true` for tool-bearing tasks.

## API reference

| Step | Endpoint | Body essentials |
|---|---|---|
| Create task | `POST /api/projects/{pid}/tasks` | raw Task dict: `{name, instruction, turn_mode?, description?, input_json_schema?, output_json_schema?}` |
| Create prompt | `POST .../tasks/{tid}/prompts` | `{name, prompt, description?, chain_of_thought_instructions?}` → returns prefixed `id::<pid>` (use as-is) |
| Create run config | `POST .../tasks/{tid}/run_configs` | `{name?, description?, run_config_properties}` (schema below) |
| Set default config | `PATCH .../tasks/{tid}` | `{"default_run_config_id": "<rcid>"}` |
| Create skill | `POST /api/projects/{pid}/skills` | `{name (kebab-case), description (≤1024 — what the model reads to decide relevance), body (SKILL.md markdown)}` |
| Register local MCP | `POST /api/projects/{pid}/connect_local_mcp` | `{name, command, args[], env_vars{}, secret_env_var_keys[], is_archived: false}` — validates connectivity on create |
| Execute a run | `POST .../tasks/{tid}/run` | `{run_config_properties, plaintext_input \| structured_input, tags?, task_run_config_id?, parent_task_run_id?}` |
| Inspect chain | `GET .../tasks/{tid}/runs/{run_id}/chain` | multiturn only |
| Edit a run | `PATCH .../tasks/{tid}/runs/{run_id}` | raw dict, deep-merged; `{"trace": [...]}` replaces the trace wholesale (fixture seeding) |

**`run_config_properties` (`type: "kiln_agent"`)**: `model_name`*, `model_provider_name`*, `prompt_id`* (`id::<id>` saved / generator name — generators get frozen into the config and `prompt_id` rewritten to `task_run_config::…`), `structured_output_mode`* (`default` for plaintext tasks), `top_p`, `temperature`, `thinking_level`, `tools_config: {tools: [ToolId]} | null`, `input_transform: {type: "jinja", template} | null` (template sees `input`; compiled at save; invalid → 422).

**Tool id forms**: `kiln_tool::call_kiln_api` (built-in REST tool) · `mcp::local|remote::<server_id>::<tool_name>` · `kiln_tool::skill::<skill_id>` · `kiln_tool::code::<code_tool_id>` · `kiln_task::<tool_server_id>`.

## Workflow

1. **Task.** Set `turn_mode` at create: `"single_turn"` (default) or `"multiturn"`. Multiturn tasks MUST have null input/output schemas (plaintext only; validator rejects otherwise). For schema'd single-turn tasks, strip constraints the target provider rejects and expect the schema to be immutable post-create — that rule is inherited and was not re-verified here.
2. **Prompt.** One saved prompt per version; reference by `id::<id>` in run configs.
3. **Skill (optional, for assistant-style tasks).** The API writes `skill.kiln` + `SKILL.md` + empty `references/`/`assets/`. **`references/*` files have NO API — write them to disk** under `<project>/skills/<id> - <name>/references/` (also: `PATCH /skills/{sid}` can only toggle `is_archived`; SKILL.md body/description are API-immutable post-create). ⚠️ In Kiln Harness Tuner experiment-build sessions the disk-write workaround is unavailable (the project tree is write-denied) — author the full bundle in scratch and use the `install_skill_bundle` MCP tool instead (see `kiln-harness-tuner-experiment-build`). Strip external doc references from ported knowledge files; inline the API schemas the model will need. At runtime the model reads them with `skill(name="<skill-name>", resource="references/…")`.
4. **Run config.** Attach tools via `tools_config.tools`. Set as task default.
5. **Drive conversations (multiturn).** Turn 1 = `POST /run` without `parent_task_run_id`; turn N = pass the previous run's id (parent must have a `trace`). Response carries `output.output`, `trace` (OpenAI-format incl. tool_calls), `usage`, `cumulative_usage`. Tag runs at creation (no spaces in tags) — tags drive eval dataset filters later. `GET /runs` lists leaves only for multiturn tasks.

## Hard rules & known bugs

- **`turn_mode` is frozen**: PATCH rejects changes (explicit 400). For a pre-existing task, edit `task.kiln` on disk — the server reads external file edits live, no restart. (Not in a Kiln Harness Tuner session: there the project tree is read- and write-denied.)
- **`PATCH /run_configs/{id}` silently ignores `run_config_properties`** (only name/starred/prompt_name update; 200 with the change dropped). Properties are immutable post-create — create a new config and re-point `default_run_config_id`.
- **The built-in `kiln_tool::call_kiln_api` is hidden from UI tool pickers** (intentional, `tool_api.py`): UI conversations silently drop it while API/eval runs keep it. For UI-driven tasks, attach the tool via a Local MCP server instead (`command: "kiln_api_mcp"`, the server vendored in this repository → `mcp::local::<server_id>::call_kiln_api`). MCP tools ARE UI-visible.
- **Name validation** (422 traps): names reject `. , ; : = / \ ? % * | < >`, consecutive/leading/trailing whitespace/underscores, newlines. "GPT-4.1" → "GPT4p1".
- Batch-creating 3+ run configs: use a code tool wrapper rather than looping API calls.

## Verification checklist

- `GET .../tasks/{tid}` round-trips `turn_mode` and `instruction`.
- A driven conversation's trace shows skill loads BEFORE dependent API calls (progressive discovery working).
- `chain` endpoint returns all turns, `chain_broken: false`; `cumulative_usage.cost` grows per turn.
- Every tool in `tools_config` actually fires in at least one trace.

## Related

`kiln-api` (every endpoint above, documented) · `kiln-harness-tuner-experiment-build` (the build discipline that loads this skill).
