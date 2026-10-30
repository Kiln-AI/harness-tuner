# Get Prompt Optimization Job Result

**GET /api/prompt_optimization_jobs/{job_id}/result**

**Tags:** Prompt Optimization

Get the result of a prompt optimization job (includes status and output if completed).

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|job_id|string|Yes|The unique identifier of the job.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|optimized_prompt|string||

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
