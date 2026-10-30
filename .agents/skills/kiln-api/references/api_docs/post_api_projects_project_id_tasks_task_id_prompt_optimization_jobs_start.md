# Start Prompt Optimization Job

**POST /api/projects/{project_id}/tasks/{task_id}/prompt_optimization_jobs/start**

**Tags:** Prompt Optimization

Start a prompt optimization job by zipping the project and sending it to the Kiln server.
Creates and saves a PromptOptimizationJob datamodel to track the job.

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|
|task_id|string|Yes|The unique identifier of the task within the project.|

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|target_run_config_id|string|Yes||
|eval_ids|array of string|Yes||

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|v|integer|Schema version for migration support.|
|id|one of: string or null|Unique identifier for this record.|
|path|one of: string or null|File system path where the record is stored.|
|created_at|string|Timestamp when the model was created. Timezone-aware; stores the writer's local offset. Format: date-time|
|created_by|string|User ID of the creator.|
|name|string|The name of the prompt optimization job. 1-120 chars|
|description|one of: string or null|A description of the prompt optimization job for you and your team.|
|job_id|string|The ID of the job on the remote Kiln server.|
|target_run_config_id|string|The ID of the run configuration used for this job.|
|latest_status|string|The latest known status of this prompt optimization job (pending, running, succeeded, failed, cancelled). Not updated in real time.|
|optimized_prompt|one of: string or null|The optimized prompt result when the job succeeds.|
|created_prompt_id|one of: string or null|The ID of the prompt created from this job's result, if any.|
|created_run_config_id|one of: string or null|The ID of the run config created from this job's result, if any.|
|eval_ids|array of string|List of eval IDs used for this job.|
|model_type|string||

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
