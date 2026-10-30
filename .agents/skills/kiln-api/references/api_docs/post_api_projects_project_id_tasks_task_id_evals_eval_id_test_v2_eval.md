# Test V2 Eval Config

**POST /api/projects/{project_id}/tasks/{task_id}/evals/{eval_id}/test_v2_eval**

**Tags:** Evals

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|
|task_id|string|Yes|The unique identifier of the task within the project.|
|eval_id|string|Yes|The unique identifier of the eval.|

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|properties|one of: one of: object, object, object, object, object, object, object, object or null|No|The V2 eval config properties to test. Required unless llm_judge_builder_input is set.|
|eval_input.final_message|string|Yes|The final model output (task output text).|
|eval_input.trace|one of: array of object or null|No|The full conversation trace, if available.|
|eval_input.reference_data|one of: object or null|No|Reference/ground-truth data from EvalInput.reference.|
|eval_input.task_input|one of: string or null|No|The original task input text.|
|llm_judge_builder_input|one of: LlmJudgeBuilderInput or null|No|Builder input for llm_judge; when set, the server bakes the full properties from the eval's output_scores.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|scores|object||
|skipped_reason|one of: string or null||
|skipped_detail|one of: string or null||
|score_range_errors|one of: array of string or null||
|intermediate_outputs|one of: object or null||

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

### LlmJudgeBuilderInput

Shared fields for llm_judge: model, provider, g_eval.

|Field|Type|Required|Description|
|-|-|-|-|
|model_name|string|Yes|The LLM model to use as judge.|
|provider|ModelProviderName|Yes|Enumeration of supported AI model providers. One of: openai, groq, amazon_bedrock, ollama, openrouter, fireworks_ai, kiln_fine_tune, kiln_custom_registry, openai_compatible, anthropic, gemini_api, azure_openai, huggingface, vertex, together_ai, siliconflow_cn, cerebras, docker_model_runner|
|g_eval|boolean|Yes|Whether to use G-Eval logprob scoring.|
|judge_prompt|one of: string or null|No|Override the judge prompt template. If unset, the server assembles a rich default from the eval's task and spec.|
|system_prompt|one of: string or null|No|Override the judge system prompt. Defaults to 'You are an evaluator.'|

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
