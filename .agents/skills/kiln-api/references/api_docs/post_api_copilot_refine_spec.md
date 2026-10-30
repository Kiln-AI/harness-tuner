# Refine Spec

**POST /api/copilot/refine_spec**

**Tags:** Copilot

## Request

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|target_task_info.task_prompt|string|Yes|The task's prompt.|
|target_task_info.task_input_schema|string|Yes|The task's input JSON schema.|
|target_task_info.task_output_schema|string|Yes|The task's output JSON schema.|
|target_specification.spec_fields|object|Yes||
|target_specification.spec_field_current_values|object|Yes||
|examples_with_feedback|array of ExamplesWithFeedback|Yes||

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|new_proposed_spec_edits|array of NewProposedSpecEdits||
|not_incorporated_feedback|one of: string or null||

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### ExampleWithFeedbackApi

An example with user feedback for spec refinement.

|Field|Type|Required|Description|
|-|-|-|-|
|user_agrees_with_judge|boolean|Yes||
|input|string|Yes||
|output|string|Yes||
|fails_specification|boolean|Yes||
|user_feedback|one of: string or null|No||

### ExamplesWithFeedback

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of ExampleWithFeedbackApi|Yes||

### NewProposedSpecEditApi

A proposed edit to a spec field.

|Field|Type|Required|Description|
|-|-|-|-|
|spec_field_name|string|Yes||
|proposed_edit|string|Yes||
|reason_for_edit|string|Yes||

### NewProposedSpecEdits

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of NewProposedSpecEditApi|Yes||

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
