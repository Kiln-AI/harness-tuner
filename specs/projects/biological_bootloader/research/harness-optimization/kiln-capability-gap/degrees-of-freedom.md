# Degrees of freedom for classifying an agent

The overview (section 4) asks for the task-definition fields that classify an
agent. This file lists every field on Kiln main that changes what harness
levers apply. Source: `libs/core/kiln_ai/datamodel/task.py:130-205` and the
run config and eval models (Kiln `main`, commit `3e67b49a58b6`,
2026-11-10).

## Fields on the task (fixed for the task's life)

| Degree of freedom | Field | Values | What it changes | Visible to loop agents? |
|---|---|---|---|---|
| Turn mode | `Task.turn_mode` (`task.py:184-189`, frozen) | `single_turn`, `multiturn` | Multi-turn forbids input and output schemas (`task.py:191-205`). Multi-turn has no SDG, prompt optimizer or fine-tuning (docs, Tasks page). Evals need a synthetic user. The gateway drives multi-turn items as eval jobs (`gateway/ops.py:924-934`). | Not in `TaskView` (`gateway/models.py:65-73` has no `turn_mode`). The agent can only infer it from the eval shape or from the gateway's drive behaviour. A gap. |
| Output shape | `Task.output_json_schema` (`task.py:166-169`; object schema only) | none (free text) or a JSON object schema | Structured-output mode becomes a lever. Decision models (Jev) apply only with an enum, boolean, small-integer or 0–1 number schema. Exact-match and set-check judges become possible. | Yes (`TaskView.output_json_schema`) |
| Input shape | `Task.input_json_schema` (`task.py:170-173`; object or array) | none (plain text), object, array | Input templates can do field access on structured input, and on plain text that parses as JSON (docs, Input Templates page). A Kiln-task tool takes the sub-task's schema as its parameters (`kiln_task_tool.py:147-152`). | Yes |
| Chain-of-thought instruction | `Task.thinking_instruction` (`task.py:174-177`) | text or none | CoT prompt generators use it; a non-reasoning model then runs a two-message flow. | Yes (`TaskView.thinking_instruction`) |
| Requirements | `Task.requirements` | deprecated ("Use specs and prompts instead") | Little. | Yes |
| Default run config | `Task.default_run_config_id` | id | Which harness is "production". | Not exposed directly. |

## Fields on the harness (run config) that classify the agent

| Degree of freedom | Field | Values | Why it classifies |
|---|---|---|---|
| Harness type | `run_config_properties.type` (`run_config.py:113-130`) | `kiln_agent`, `mcp` | `mcp` means the agent lives outside Kiln and the tuner can only measure it. Harness Tuner refuses `mcp` (`gateway/ops.py:897-898`). |
| Tool use | `tools_config.tools` | none to many; kinds by prefix (code, MCP local or remote, Kiln task, RAG, skill, memory, world) | Tool-less tasks cannot use the tool levers; the lever ladder has a row for them (`lever-ladder.md:56`). |
| Sub-agents | any `kiln_task::` tool | yes or no | A sub-agent is a second tunable task. |
| Retrieval | any `kiln_tool::rag::` tool | yes or no | RAG parameters become levers. |
| Stateful environment | any `kiln_tool::world::` tool, or eval inputs with `world_reset` | yes or no | Stateful agents need world-backed evals; the gateway drives them as eval jobs. |
| Model class | provider and model | LLM, reasoning LLM, fine-tune (`kiln_fine_tune`), decision model (`typesafe` Jev) | The model class sets which other levers exist (Jev: no tools, no prompt-length trade-off in the same way). |

## Fields on the evals that classify the measurement

| Degree of freedom | Field | Values |
|---|---|---|
| Eval input type | `EvalInput.data` (`eval.py:604-670, 704-730`) | `single_turn` (a user message) or `multi_turn_synthetic` (persona, goal, behaviour guidance, user model, turns) |
| What the judge reads | `evaluation_data_type` (Harness Tuner `EvalCreate`, `gateway/models.py:520-526`) | `final_answer`, `full_trace`, `reference_answer` |
| Judge kind | `V2EvalType` (`eval.py:99-110`) | LLM judge (optionally G-Eval), exact match, pattern match, contains, set check, tool-call check, step-count check, code eval |
| Reference data | `EvalInput.reference` | keys a judge compares against |

## Suggested classification axes (inferred from the fields above)

1. Turn mode: single or multi.
2. Output: free text, structured, or structured-and-classifier-shaped (Jev-eligible).
3. Input: plain text, plain text that is JSON, structured object or array.
4. Tools: none, read-only, stateful (world).
5. Knowledge: none, in prompt, in skills, behind RAG.
6. Composition: single agent or with sub-agents.
7. Harness host: inside Kiln (`kiln_agent`) or external (`mcp`, not tunable).
8. Measurement: deterministic judges, LLM judges, synthetic-user drives.

Axis 2 has three values, not two: an enum or boolean output is a narrower
class than "structured", because it alone admits decision models and
exact-match evals.
