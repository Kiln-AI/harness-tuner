# Generate Inputs Batch Status

**GET /api/projects/{project_id}/tasks/{task_id}/generate_inputs_batch/{job_id}**

**Tags:** Synthetic Data

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|
|task_id|string|Yes|The unique identifier of the task within the project.|
|job_id|string|Yes|The batch job identifier.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|status|string|One of: running, complete, error|
|total|integer||
|completed|integer||
|errors|integer||
|model_name|string||
|model_provider|string||
|results|array of Results||
|error_message|one of: string or null||

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### InputsBatchResultItem

|Field|Type|Required|Description|
|-|-|-|-|
|index|integer|Yes||
|input|one of: string, object or null|No||
|error|one of: string or null|No||

### Results

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of InputsBatchResultItem|Yes||

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
