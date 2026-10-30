# Generate Multi-Turn SU Cases

**POST /api/projects/{project_id}/tasks/{task_id}/multiturn_sdg/generate_cases**

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
|target_specification|string|Yes|>=1 chars|
|num_cases|integer|Yes|1.0-10.0|
|case_prompts|one of: array of string or null|No|Optional per-case scenario prompts (e.g. from an approved batch plan). When provided, the batch is generated in ONE upstream call with case i designed around prompt i; each returned case carries scenario_index. Under the upstream salvage contract a flaky case is dropped rather than failing the batch, so the response may hold fewer cases than prompts — scenario_index, not position, maps a case to its prompt. Length must equal num_cases.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|cases|array of object|A SyntheticUserCase. Shape: {seed_prompt: str, synthetic_user_info: str, scenario_index?: int | null}. The synthetic_user_info value is an XML-tagged blob: <persona>...</persona><goal>...</goal><behavior_guidance>...</behavior_guidance>. Parsed client-side by kiln_ai.synthetic_user.parser. scenario_index is set only on scenario batches (generate_cases with case_prompts) and maps the case back to its plan prompt.|

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

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
