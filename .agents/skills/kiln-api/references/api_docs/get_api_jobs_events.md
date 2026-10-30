# Stream Job Events

**GET /api/jobs/events**

**Tags:** Jobs

Server-sent events for jobs. Emits an initial `snapshot`, then per-job
`job` and `deleted` events. A pure observer: disconnecting never stops a job.

## Request

### Query Parameters

|Name|Type|Required|Default|Description|
|-|-|-|-|-|
|job_id|one of: string or null|No||Only stream events for this job id.|
|type|one of: string or null|No||Only stream events for this job type.|
|project_id|one of: string or null|No||Only stream events for this project id.|

## Responses

### 200 OK

Successful Response

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
