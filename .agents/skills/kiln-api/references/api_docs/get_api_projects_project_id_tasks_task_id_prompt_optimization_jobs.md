# List Prompt Optimization Jobs

**GET /api/projects/{project_id}/tasks/{task_id}/prompt_optimization_jobs**

**Tags:** Prompt Optimization

List all Prompt Optimization jobs for a task.
Optionally update the status of non-final jobs from the remote server.

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|
|task_id|string|Yes|The unique identifier of the task within the project.|

### Query Parameters

|Name|Type|Required|Default|Description|
|-|-|-|-|-|
|update_status|boolean|No|False|Whether to update the status of non-final jobs from the remote server.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|value|array of PromptOptimizationJob||

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### PromptOptimizationJob

The Kiln prompt optimization job datamodel.

|Field|Type|Required|Description|
|-|-|-|-|
|v|integer|No|Schema version for migration support.|
|id|one of: string or null|No|Unique identifier for this record.|
|path|one of: string or null|No|File system path where the record is stored.|
|created_at|string|No|Timestamp when the model was created. Timezone-aware; stores the writer's local offset. Format: date-time|
|created_by|string|No|User ID of the creator.|
|name|string|Yes|The name of the prompt optimization job. 1-120 chars|
|description|one of: string or null|No|A description of the prompt optimization job for you and your team.|
|job_id|string|Yes|The ID of the job on the remote Kiln server.|
|target_run_config_id|string|Yes|The ID of the run configuration used for this job.|
|latest_status|string|No|The latest known status of this prompt optimization job (pending, running, succeeded, failed, cancelled). Not updated in real time.|
|optimized_prompt|one of: string or null|No|The optimized prompt result when the job succeeds.|
|created_prompt_id|one of: string or null|No|The ID of the prompt created from this job's result, if any.|
|created_run_config_id|one of: string or null|No|The ID of the run config created from this job's result, if any.|
|eval_ids|array of string|No|List of eval IDs used for this job.|
|model_type|string|Yes||

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
