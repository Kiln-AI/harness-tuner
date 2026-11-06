# Question Spec

**POST /api/copilot/question_spec**

**Tags:** Copilot

## Request

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|target_task_info.task_prompt|string|Yes|The task's prompt.|
|target_task_info.task_input_schema|string|Yes|The task's input JSON schema.|
|target_task_info.task_output_schema|string|Yes|The task's output JSON schema.|
|target_specification|string|Yes|The specification to analyze|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|questions|array of Questions|A set of questions to ask about the specification|

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### AnswerOption

|Field|Type|Required|Description|
|-|-|-|-|
|answer_title|string|Yes|A short title describing this answer option|
|answer_description|string|Yes|A description of this answer|

### AnswerOptions

A list of possible answers to this question for the user to select from

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of AnswerOption|Yes|A list of possible answers to this question for the user to select from|

### Question

|Field|Type|Required|Description|
|-|-|-|-|
|question_title|string|Yes|A short title for this question|
|question_body|string|Yes|The full question text|
|answer_options|array of AnswerOptions|Yes|A list of possible answers to this question for the user to select from|

### Questions

A set of questions to ask about the specification

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of Question|Yes|A set of questions to ask about the specification|

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
