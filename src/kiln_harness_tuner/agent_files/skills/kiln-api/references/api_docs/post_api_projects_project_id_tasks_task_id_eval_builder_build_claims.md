# Build Claims

**POST /api/projects/{project_id}/tasks/{task_id}/eval_builder/build_claims**

**Tags:** Eval Builder

Claims-only primitive: build claims for one trace given a known verdict.

Used by the refine loop (regenerate claims without re-running the judge).

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|
|task_id|string|Yes|The unique identifier of the task.|

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|raw_input|string|Yes||
|raw_output|string|Yes||
|eval_rubric|string|Yes||
|judge_reasoning|string|Yes||
|judge_score|string|Yes|One of: pass, fail|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|claims|array of Claims||
|final_judgement.claim|string||
|final_judgement.expected_result|string|One of: pass, fail|
|final_judgement.evidence|string||
|final_judgement.citations|array of Citations||

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### CitationApi

A start+end anchor into the trace; the UI highlights from `from` to `to`.

`from` is a Python keyword, so the field is `from_` with an alias — the
serialized key MUST stay `from` (the UI greps that literal JSON key).

|Field|Type|Required|Description|
|-|-|-|-|
|marker|integer|Yes||
|source|string|Yes|One of: input, output|
|from|string|Yes||
|to|string|Yes||

### Citations

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of CitationApi|Yes||

### ClaimApi

One atomic claim + its one-sentence evidence with [n] citation markers.

`expected_result` is the verdict a reviewer's AGREE on this claim supports —
a direction bit, not a re-judging: claims pointing opposite the judge's
verdict are counter-evidence the reviewer can use to catch a bad judge.

|Field|Type|Required|Description|
|-|-|-|-|
|claim|string|Yes||
|expected_result|string|Yes|One of: pass, fail|
|evidence|string|Yes||
|citations|array of Citations|Yes||

### Claims

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of ClaimApi|Yes||

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
