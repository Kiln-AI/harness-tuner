# Get Prompt Optimization Job Status

**GET /api/prompt_optimization_jobs/{job_id}/status**

**Tags:** Prompt Optimization

Get the status of a Prompt Optimization job.

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
|job_id|string||
|status|JobStatus|One of: cancelled, failed, pending, running, succeeded|

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### JobStatus

|Field|Type|Required|Description|
|-|-|-|-|
|value|string|Yes|One of: cancelled, failed, pending, running, succeeded|

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
