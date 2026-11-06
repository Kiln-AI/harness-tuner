# Generate Batch

**POST /api/copilot/generate_batch**

**Tags:** Copilot

## Request

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|target_task_info.task_prompt|string|Yes|The task's prompt.|
|target_task_info.task_input_schema|string|Yes|The task's input JSON schema.|
|target_task_info.task_output_schema|string|Yes|The task's output JSON schema.|
|target_specification|string|Yes||
|num_samples_per_topic|integer|Yes||
|num_topics|integer|Yes||
|sdg_session_config.topic_generation_config.task_metadata|TaskMetadataApi|Yes|Metadata about the model used for a task.|
|sdg_session_config.topic_generation_config.prompt|string|Yes||
|sdg_session_config.input_generation_config.task_metadata|TaskMetadataApi|Yes|Metadata about the model used for a task.|
|sdg_session_config.input_generation_config.prompt|string|Yes||
|sdg_session_config.output_generation_config.task_metadata|TaskMetadataApi|Yes|Metadata about the model used for a task.|
|sdg_session_config.output_generation_config.prompt|string|Yes||

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|data_by_topic|object||

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

### TaskMetadataApi

Metadata about the model used for a task.

|Field|Type|Required|Description|
|-|-|-|-|
|model_name|string|Yes|The name of the AI model used.|
|model_provider_name|ModelProviderName|Yes|Enumeration of supported AI model providers. One of: openai, groq, amazon_bedrock, ollama, openrouter, fireworks_ai, kiln_fine_tune, kiln_custom_registry, openai_compatible, anthropic, gemini_api, azure_openai, huggingface, vertex, together_ai, siliconflow_cn, cerebras, docker_model_runner|

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
