# Check Eval

**GET /api/projects/{project_id}/tasks/{task_id}/prompt_optimization_jobs/check_eval**

**Tags:** Prompt Optimization

Check if an eval is valid for a Prompt Optimization job.
Validates that the eval has a default config and that the model is supported.

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|
|task_id|string|Yes|The unique identifier of the task within the project.|

### Query Parameters

|Name|Type|Required|Default|Description|
|-|-|-|-|-|
|eval_id|string|Yes||The unique identifier of the evaluation.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|has_default_config|boolean||
|has_train_set|boolean||
|model_is_supported|boolean||

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
