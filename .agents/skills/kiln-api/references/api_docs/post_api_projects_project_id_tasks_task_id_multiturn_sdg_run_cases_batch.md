# Run Multi-Turn SU Cases Batch

**POST /api/projects/{project_id}/tasks/{task_id}/multiturn_sdg/run_cases_batch**

**Tags:** Multiturn SDG

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|ID of the project containing the target task.|
|task_id|string|Yes|ID of the target task. Must be a multi-turn task.|

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|cases|array of object|Yes|Cases as returned by /generate_cases, optionally edited. A SyntheticUserCase. Shape: {seed_prompt: str, synthetic_user_info: str, scenario_index?: int | null}. The synthetic_user_info value is an XML-tagged blob: <persona>...</persona><goal>...</goal><behavior_guidance>...</behavior_guidance>. Parsed client-side by kiln_ai.synthetic_user.parser. scenario_index is set only on scenario batches (generate_cases with case_prompts) and maps the case back to its plan prompt.|
|turns|integer|No|Exact number of assistant turns to produce per case. The drive loop has no early termination. 1.0-20.0|
|target_run_config|one of: one of: object, object or null|No|Inline run config for the target task, used verbatim — the same full properties shape a manual run sends, tools included. For driving a config that isn't worth saving (ad-hoc experiments, scripting). Must be a Kiln agent config. Exactly one of target_run_config / target_run_config_id is required.|
|target_run_config_id|one of: string or null|No|ID of one of the target task's saved run configs. The drive uses the saved config verbatim — model, prompt, sampling, and tools — so the agent under test behaves exactly like a manual run, and driven runs attribute back to the config. Exactly one of target_run_config / target_run_config_id is required.|
|su_driver.model_name|string|Yes|>=1 chars|
|su_driver.model_provider|ModelProviderName|Yes|Enumeration of supported AI model providers. One of: openai, groq, amazon_bedrock, ollama, openrouter, fireworks_ai, kiln_fine_tune, kiln_custom_registry, openai_compatible, anthropic, gemini_api, azure_openai, huggingface, vertex, together_ai, siliconflow_cn, cerebras, docker_model_runner|
|batch_tag|one of: string or null|No|Optional user-supplied batch label. Constrained to [A-Za-z0-9_-]{1,64} so it can safely be used as a tag on leaf TaskRuns. Auto-generated if not provided.|

## Responses

### 200 OK

Successful Response

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### ModelProviderName

Enumeration of supported AI model providers.

|Field|Type|Required|Description|
|-|-|-|-|
|value|string|Yes|Enumeration of supported AI model providers. One of: openai, groq, amazon_bedrock, ollama, openrouter, fireworks_ai, kiln_fine_tune, kiln_custom_registry, openai_compatible, anthropic, gemini_api, azure_openai, huggingface, vertex, together_ai, siliconflow_cn, cerebras, docker_model_runner|

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
