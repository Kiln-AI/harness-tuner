# Classify Spec Description

**POST /api/copilot/classify_spec_description**

**Tags:** Copilot

Stub for spec classification — kiln_server classifier hasn't
shipped. Returns 501 so callers can fall back to manual selection.

## Request

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|description|string|Yes|Free-text description of what the eval should check.|
|task_prompt|one of: string or null|No|Optional task prompt for context (improves classification accuracy when the spec relates to a specific task).|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|spec_type|SpecType|Defines the type of spec. One of: desired_behaviour, issue, tone, formatting, localization, appropriate_tool_use, reference_answer_accuracy, factual_correctness, hallucinations, completeness, toxicity, bias, maliciousness, nsfw, taboo, jailbreak, prompt_leakage|
|suggested_name|string|A filename-safe name for the new spec, derived from the description.|
|property_values|object|Pre-filled property values for the chosen spec_type. Keys correspond to the field_configs of that spec_type.|

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### SpecType

Defines the type of spec.

|Field|Type|Required|Description|
|-|-|-|-|
|value|string|Yes|Defines the type of spec. One of: desired_behaviour, issue, tone, formatting, localization, appropriate_tool_use, reference_answer_accuracy, factual_correctness, hallucinations, completeness, toxicity, bias, maliciousness, nsfw, taboo, jailbreak, prompt_leakage|

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
