# Submit Question Answers

**POST /api/copilot/refine_spec_with_question_answers**

**Tags:** Copilot

## Request

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|task_prompt|string|Yes|The task's prompt|
|specification.spec_fields|object|Yes|Dictionary mapping field names to their descriptions/purposes|
|specification.spec_field_current_values|object|Yes|Dictionary mapping field names to their current values|
|questions_and_answers|array of QuestionsAndAnswers|Yes|Questions about the specification with user-provided answers|

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

### AnswerOptionWithSelection

An answer option with user selection state.

|Field|Type|Required|Description|
|-|-|-|-|
|answer_title|string|Yes|A short title describing this answer option|
|answer_description|string|Yes|A description of this answer|
|selected|boolean|Yes|Whether the user selected this answer option|

### AnswerOptions

Possible answers the user was asked to select from

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of AnswerOptionWithSelection|Yes|Possible answers the user was asked to select from|

### QuestionWithAnswer

A question with user-provided answer.

|Field|Type|Required|Description|
|-|-|-|-|
|question_title|string|Yes|A short title for this question|
|question_body|string|Yes|The full question text|
|answer_options|array of AnswerOptions|Yes|Possible answers the user was asked to select from|
|custom_answer|one of: string or null|No|User-provided text feedback when predefined answer options don't fit|

### QuestionsAndAnswers

Questions about the specification with user-provided answers

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of QuestionWithAnswer|Yes|Questions about the specification with user-provided answers|

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
