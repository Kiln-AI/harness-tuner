# Get Eval Run Results

**GET /api/projects/{project_id}/tasks/{task_id}/evals/{eval_id}/eval_config/{eval_config_id}/run_config/{run_config_id}/results**

**Tags:** Evals

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|
|task_id|string|Yes|The unique identifier of the task within the project.|
|eval_id|string|Yes|The unique identifier of the eval.|
|eval_config_id|string|Yes|The unique identifier of the eval configuration.|
|run_config_id|string|Yes|The unique identifier of the run configuration.|

### Query Parameters

|Name|Type|Required|Default|Description|
|-|-|-|-|-|
|split|one of: string or null|No||Only return results for dataset items in this split of the eval (train, val, or test). 422 if the eval has no filter configured for the split. Omit to return all results (no split filtering).|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|results|array of Results|The individual eval run results.|
|eval.v|integer|Schema version for migration support.|
|eval.id|one of: string or null|Unique identifier for this record.|
|eval.path|one of: string or null|File system path where the record is stored.|
|eval.created_at|string|Timestamp when the model was created. Timezone-aware; stores the writer's local offset. Format: date-time|
|eval.created_by|string|User ID of the creator.|
|eval.name|string|The name of the eval. 1-120 chars|
|eval.description|one of: string or null|The description of the eval|
|eval.template|one of: EvalTemplateId or null|The template selected when creating this eval. Useful for suggesting eval steps and output scores.|
|eval.current_config_id|one of: string or null|The id of the current config to use for this eval. This can be changed over time to run the same eval with different configs.|
|eval.eval_set_filter_id|one of: string or null|The id of the dataset filter which defines which dataset items are included when running this eval (V1 TaskRun-typed). This is the eval's test set; the 'eval set' name is legacy.|
|eval.eval_configs_filter_id|one of: string or null|The id of the dataset filter which defines which dataset items are included when comparing the quality of the eval configs under this eval. Should consist of dataset items with ratings.|
|eval.train_set_filter_id|one of: string or null|The id of the dataset filter which defines which dataset items are included in the training set for fine-tuning.|
|eval.val_set_filter_id|one of: string or null|The id of the dataset filter which defines which dataset items are included in the validation set.|
|eval.eval_input_filter_id|one of: string or null|Filter ID for EvalInput-backed datasets (V2). Mutually exclusive with eval_set_filter_id.|
|eval.output_scores|array of OutputScores|The scores this evaluator should produce.|
|eval.favourite|boolean|Whether this eval is a favourite of the user. Rendered as a star icon in the UI.|
|eval.template_properties|one of: object or null|Properties to be used to execute the eval. This is template_type specific and should serialize to a json dict.|
|eval.evaluation_data_type|one of: EvalTemplateId or null|The output of the task run to evaluate. Can be final answer, full trace, or None for V2 evals.|
|eval.multi_turn_drive_config|one of: MultiTurnDriveConfig or null|How to re-drive multi-turn synthetic eval inputs at eval time (synthetic-user model + turn count). Required to execute multi-turn EvalInput items; None for single-turn and stored-trace evals.|
|eval.model_type|string||
|eval_config.v|integer|Schema version for migration support.|
|eval_config.id|one of: string or null|Unique identifier for this record.|
|eval_config.path|one of: string or null|File system path where the record is stored.|
|eval_config.created_at|string|Timestamp when the model was created. Timezone-aware; stores the writer's local offset. Format: date-time|
|eval_config.created_by|string|User ID of the creator.|
|eval_config.name|string|The name of the eval config. 1-120 chars|
|eval_config.model_name|one of: string or null|The name of the model to use for this eval config. Required for legacy configs, None for V2.|
|eval_config.model_provider|one of: string or null|The provider of the model to use for this eval config. Required for legacy configs, None for V2.|
|eval_config.config_type|EvalTemplateId|The type of eval configuration, determining how scores are generated. One of: g_eval, llm_as_judge, v2|
|eval_config.properties|one of: one of: object, object, object, object, object, object, object, object, object or null|Properties to be used to execute the eval config. Legacy configs use a dict; V2 configs use typed properties.|
|eval_config.provenance|one of: KilnArtifactProvenance or null|Why this artifact exists and what it was derived from.|
|eval_config.model_type|string||
|run_config.v|integer|Schema version for migration support.|
|run_config.id|one of: string or null|Unique identifier for this record.|
|run_config.path|one of: string or null|File system path where the record is stored.|
|run_config.created_at|string|Timestamp when the model was created. Timezone-aware; stores the writer's local offset. Format: date-time|
|run_config.created_by|string|User ID of the creator.|
|run_config.name|string|The name of the task run config. 1-120 chars|
|run_config.description|one of: string or null|The description of the task run config.|
|run_config.run_config_properties|one of: KilnAgentRunConfigProperties, McpRunConfigProperties|The run config properties to use for this task run.|
|run_config.prompt|one of: BasePrompt or null|A prompt to use for run config.|
|run_config.starred|boolean|Whether this run config is starred/favourited by the user.|
|run_config.provenance|one of: KilnArtifactProvenance or null|Why this artifact exists and what it was derived from.|
|run_config.model_type|string||

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### Usage

Token usage, cost, and aggregate LLM latency for a per-run accumulator.

Extends :class:`MessageUsage` with ``total_llm_latency_ms``, which is
only meaningful while a single run is in flight (its model calls run
sequentially in real time). For per-message records and full-trace
sums use :class:`MessageUsage` — those values would mix latencies
from different points in time, so the field doesn't apply.

|Field|Type|Required|Description|
|-|-|-|-|
|input_tokens|one of: integer or null|No|The number of input tokens used.|
|output_tokens|one of: integer or null|No|The number of output tokens used.|
|total_tokens|one of: integer or null|No|The total number of tokens used.|
|cost|one of: number or null|No|The cost in US dollars, saved at runtime (prices can change over time).|
|cached_tokens|one of: integer or null|No|Number of tokens served from prompt cache. None if not reported.|
|total_llm_latency_ms|one of: integer or null|No|Total time spent waiting on LLM API calls in milliseconds. Sum of per-call latencies, excludes tool execution time.|

### EvalRun

The results of running an eval on a single dataset item.

This is a child of an EvalConfig, which specifies how the scores were generated.

Eval runs can be one of 2 types:
1) eval_config_eval=False: we were evaluating a task run (a method of running the task). We get the task input from the dataset_id.input, run the task with the task_run_config, then ran the evaluator on that output. task_run_config_id must be set. The output saved in this model is the output of the task run.
2) eval_config_eval=True: we were evaluating an eval config (a method of evaluating the task). We used the existing dataset item input/output, and ran the evaluator on it. task_run_config_id must be None. The input/output saved in this model is the input/output of the dataset item.

|Field|Type|Required|Description|
|-|-|-|-|
|v|integer|No|Schema version for migration support.|
|id|one of: string or null|No|Unique identifier for this record.|
|path|one of: string or null|No|File system path where the record is stored.|
|created_at|string|No|Timestamp when the model was created. Timezone-aware; stores the writer's local offset. Format: date-time|
|created_by|string|No|User ID of the creator.|
|dataset_id|one of: string or null|No|The ID of the dataset item (TaskRun) that was used for this run. Mutually exclusive with eval_input_id.|
|task_run_config_id|one of: string or null|Yes|The ID of the TaskRunConfig that was run, if this eval run was based on a task run. Must belong to the same Task as this eval. Can be None if this eval run is based on an eval config.|
|eval_config_eval|boolean|No|Whether this eval run to evaluate the parent eval config (evaluating the config using an existing dataset item). If true, task_run_config_id must be None, as we're not running the task.|
|input|string|Yes|The input to the task. JSON formatted for structured input, plaintext for unstructured input.|
|output|one of: string or null|No|The output of the task. None for skipped-before-execution runs.|
|reference_answer|one of: string or null|No|The reference answer for the input. JSON formatted for structured reference answer, plaintext for unstructured reference answer. Used for reference answer evals.|
|intermediate_outputs|one of: object or null|No|The intermediate outputs of the task (example, eval thinking).|
|task_run_trace|one of: string or null|No|The JSON formatted trace of the task run that produced the output.|
|scores|object|No|The output scores of the evaluator (aligning to those required by the grand-parent Eval this object is a child of).|
|task_run_usage|one of: Usage or null|No|The usage of the task run that produced this eval run output (not the usage by the evaluation model).|
|eval_usage|one of: Usage or null|No|The usage of the evaluation model (judge) that produced this eval run's scores, aggregated across every LLM call the judgment made. Distinct from task_run_usage, which is the evaluated task run's usage. None for non-LLM evals (e.g. code evals) and for records that predate this field.|
|eval_input_id|one of: string or null|No|ID of the EvalInput used for this run (V2 evals). Mutually exclusive with dataset_id.|
|reference_data|one of: object or null|No|Structured reference data from EvalInput.reference, used by V2 eval types.|
|skipped_reason|one of: string or null|No|If set, this run was skipped. Stored as str for back/forward-compat; conventionally a SkippedReason value.|
|skipped_detail|one of: string or null|No|Case-specific detail for skipped runs (e.g. missing key name).|
|drive_fingerprint|one of: string or null|No|Identity of the drive that produced task_run_trace for multi-turn synthetic (EvalInput) runs: a versioned hash of drive config + run config properties + scenario content. Enables reuse of the stored conversation by other eval configs; None for non-driven records.|
|model_type|string|Yes||

### Results

The individual eval run results.

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of EvalRun|Yes|The individual eval run results.|

### EvalTemplateId

An eval template is a pre-defined eval that can be used as a starting point for a new eval.

|Field|Type|Required|Description|
|-|-|-|-|
|value|string|Yes|An eval template is a pre-defined eval that can be used as a starting point for a new eval. One of: kiln_requirements, desired_behaviour, kiln_issue, tool_call, toxicity, bias, maliciousness, factual_correctness, jailbreak, rag|

### EvalOutputScore

A definition of a score that an evaluator will produce.

Very similar to TaskRequirement, but conceptually different keeping in a separate models.

|Field|Type|Required|Description|
|-|-|-|-|
|name|string|Yes|The name of the score. Will be provided to the model so use a descriptive name. Should align to the model's TaskRequirement name if you want to use human evals to evaluate the evaluator's performance. 1-32 chars|
|instruction|one of: string or null|No|A description of the score, used to help the model understand the goal of the score. Will be provided to evaluator models, so should be written for the model, not the team/user.|
|type|EvalTemplateId|Yes|Defines the types of rating systems available for task outputs. One of: five_star, pass_fail, pass_fail_critical, custom|
|direction|EvalTemplateId|No|The direction of improvement for an eval output score.

Tells consumers how to interpret a change in the score's value: 'higher_is_better'
means an increase is an improvement, 'lower_is_better' means a decrease is an
improvement, and 'informational' scores carry context only and should never drive
decisions in either direction. One of: higher_is_better, lower_is_better, informational|

### OutputScores

The scores this evaluator should produce.

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of EvalOutputScore|Yes|The scores this evaluator should produce.|

### MultiTurnDriveConfig

Per-eval settings for re-driving multi-turn synthetic inputs at eval time.

A multi-turn eval run regenerates each conversation: the agent under test
comes from the run config being evaluated, while the synthetic user
(customer) defined here is held constant across run configs — so a
comparison varies only the agent. Stored per-eval so re-drives use the
same synthetic-user model and turn count the builder used when driving
the conversations the judge was calibrated on, keeping the judge scoring
the same conversation distribution.

|Field|Type|Required|Description|
|-|-|-|-|
|model_name|string|Yes|The model that plays the synthetic user during re-drives.|
|model_provider|string|Yes|The provider of the synthetic-user model.|
|turns|integer|Yes|Exact number of assistant turns per re-driven conversation (the drive loop has no early termination). 1.0-20.0|

### KilnArtifactProvenance

Why this artifact exists and what it was derived from.

Written once at creation; immutable thereafter (enforced at the API layer).
Compile-time metadata for future agent sessions and humans — never shown to
runtime models (not part of any tool/prompt surface).

|Field|Type|Required|Description|
|-|-|-|-|
|notes|one of: string or null|No|Why this artifact exists: the problem/hypothesis it addresses, what changed vs. the derived_from_ids parents, what validation/evidence supports it (cite eval/run_config/trace IDs inline), and known limits. First line = one-sentence summary. Record observations with conditions, never universal rules. Max ~2000 chars.|
|derived_from_ids|array of one of: string or null|No|IDs of same-type sibling artifacts this one was derived from. Ordered: first = primary parent (the artifact this replaces or is a new version of); further entries = additional sources merged in. Empty = not derived. IDs resolve among siblings in the same parent scope only.|
|origin|one of: string or null|No|Whose judgment created this artifact. 'human': a person authored it directly OR an agent created it fulfilling a direct human request. 'agent': an agent created it autonomously. None: unknown/legacy. Required when this provenance is created; consumers must tolerate unknown values.|

### ToolsRunConfig

A config describing which tools are available to a task.

|Field|Type|Required|Description|
|-|-|-|-|
|tools|array of string|Yes|The IDs of the tools available to the task.|

### KilnAgentRunConfigProperties

A configuration for running a task using a Kiln AI agent.

This includes everything needed to run a task, except the input and task ID. Running the same RunConfig with the same input should make identical calls to the model (output may vary as models are non-deterministic).

|Field|Type|Required|Description|
|-|-|-|-|
|type|string|No||
|model_name|string|Yes|The model to use for this run config.|
|model_provider_name|EvalTemplateId|Yes|Enumeration of supported AI model providers. One of: openai, groq, amazon_bedrock, ollama, openrouter, fireworks_ai, kiln_fine_tune, kiln_custom_registry, openai_compatible, anthropic, gemini_api, azure_openai, huggingface, vertex, together_ai, siliconflow_cn, cerebras, docker_model_runner|
|prompt_id|string|Yes|The prompt to use for this run config. Defaults to building a simple prompt from the task if not provided.|
|top_p|number|No|The top-p value to use for this run config. Defaults to 1.0.|
|temperature|number|No|The temperature to use for this run config. Defaults to 1.0.|
|structured_output_mode|EvalTemplateId|Yes|Enumeration of supported structured output modes.

- json_schema: request json using API capabilities for json_schema
- function_calling: request json using API capabilities for function calling
- json_mode: request json using API's JSON mode, which should return valid JSON, but isn't checking/passing the schema
- json_instructions: append instructions to the prompt to request json matching the schema. No API capabilities are used. You should have a custom parser on these models as they will be returning strings.
- json_instruction_and_object: append instructions to the prompt to request json matching the schema. Also request the response as json_mode via API capabilities (returning dictionaries).
- json_custom_instructions: The model should output JSON, but custom instructions are already included in the system prompt. Don't append additional JSON instructions.
- default: let the adapter decide (legacy, do not use for new use cases)
- unknown: used for cases where the structured output mode is not known (on old models where it wasn't saved). Should lookup best option at runtime. One of: default, json_schema, function_calling_weak, function_calling, json_mode, json_instructions, json_instruction_and_object, json_custom_instructions, unknown|
|thinking_level|one of: string or null|No|The thinking level to use for this run config. If None, defaults may apply.|
|tools_config|one of: ToolsRunConfig or null|No|The tools config to use for this run config, defining which tools are available to the model.|
|input_transform|one of: one of: object or null|No|Optional transform applied to the task input at run time, producing the first user message sent to the model. Default None preserves the identity path.|

### McpRunConfigProperties

A configuration for running a task via an MCP tool.

|Field|Type|Required|Description|
|-|-|-|-|
|type|string|No||
|tool_reference.tool_id|string|Yes|The MCP tool ID to call (mcp::local|remote::<server_id>::<tool_name>).|
|tool_reference.tool_server_id|one of: string or null|No|The MCP tool server ID.|
|tool_reference.tool_name|one of: string or null|No|The MCP tool name.|
|tool_reference.input_schema|one of: object or null|No|Snapshot of the MCP tool input schema.|
|tool_reference.output_schema|one of: object or null|No|Snapshot of the MCP tool output schema.|

### BasePrompt

A prompt for a task. This is the basic data storage format which can be used throughout a project.

The "Prompt" model name is reserved for the custom prompts parented by a task.

|Field|Type|Required|Description|
|-|-|-|-|
|name|string|Yes|The name of the prompt. 1-120 chars|
|description|one of: string or null|No|A more detailed description of the prompt.|
|generator_id|one of: string or null|No|The id of the generator that created this prompt.|
|prompt|string|Yes|The prompt for the task. >=1 chars|
|chain_of_thought_instructions|one of: string or null|No|Instructions for the model 'thinking' about the requirement prior to answering. Used for chain of thought style prompting. COT will not be used unless this is provided.|

### ValidationError

|Field|Type|Required|Description|
|-|-|-|-|
|loc|array of one of: string, integer|Yes||
|msg|string|Yes||
|type|string|Yes||

### Detail

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of ValidationError|Yes||
