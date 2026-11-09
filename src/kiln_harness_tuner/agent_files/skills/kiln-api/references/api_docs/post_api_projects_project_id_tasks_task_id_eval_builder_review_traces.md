# Review Traces

**POST /api/projects/{project_id}/tasks/{task_id}/eval_builder/review_traces**

**Tags:** Eval Builder

Per-trace `judge → claim builder` over single-turn I/O pairs,
fanned out (local) and streamed.

Emits one SSE event per trace as it completes:
  - `trace_reviewed` { trace_index, raw_input, raw_output,
                       judge_score, judge_reasoning, claims,
                       final_judgement }
  - `trace_error`    { trace_index, code, message }   (batch continues)
Bracketed by `{ "type": "batch_started", "total" }` and `data: complete`.

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
|traces|array of Traces|Yes||
|spec_name|string|Yes|The spec's name. The review judge scores under the same output-score identity the saved eval will use, so the prompt the user calibrates here is byte-identical to the one that ships. 1-32 chars|
|judge.prompt|string|Yes||
|judge.model_name|string|Yes||
|judge.model_provider|string|Yes||

## Responses

### 200 OK

Successful Response

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### TraceInput

One single-turn example to review: the task's raw I/O pair.

Multi-turn conversations never ride this request — they are driven,
judged, and distilled server-side by the review pipeline, which reads
the runner's real trace directly. Structured traces therefore have no
wire shape here at all.

|Field|Type|Required|Description|
|-|-|-|-|
|raw_input|string|Yes|The task's raw input.|
|raw_output|string|Yes|The task's raw output.|

### Traces

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of TraceInput|Yes||

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
