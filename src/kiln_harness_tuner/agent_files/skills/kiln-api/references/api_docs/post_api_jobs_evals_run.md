# Run Eval Job

**POST /api/jobs/evals/run**

**Tags:** Jobs

Kick off an eval as a background job and return immediately.

A typed, approval-gated entry point for agents. Unlike the UI's SSE
run endpoints, this does not stream — the job runs in the background.
Poll `GET /api/jobs/{id}` (or `/api/jobs/wait`) for progress and the
result.

## Request

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|Id of the project the eval belongs to.|
|task_id|string|Yes|Id of the task the eval belongs to.|
|eval_id|string|Yes|Id of the eval to run.|
|eval_config_id|string|Yes|Id of the eval config (judge) to evaluate the run's output with.|
|run_config_id|string|Yes|Id of the task run config whose outputs are being evaluated.|
|concurrency|one of: integer or null|No|Max dataset items evaluated in parallel by the runner. Leave null to use the runner's default (25).|
|split|one of: string or null|No|Which of the eval's dataset splits to run: train, val, or test. Fails with 422 if the eval has no filter configured for the split. Leave null to run the eval set (the test set — today's default behavior).|
|item_ids|array of string \| null|Optional. Restrict the job to these items of the split (EvalInput ids for an EvalInput-backed split, TaskRun ids otherwise). Ids outside the split are ignored; progress and completion are measured over the subset; stored rows are reused by a later full run. Null = the whole split. (Server-version caveat: this parameter is newer than the released server — check that your studio_server accepts it before relying on it. It is the Kiln Harness Tuner residual trial's lane.)|

## Responses

### 201 Created

Successful Response

|Field|Type|Description|
|-|-|-|
|job_id|string|The id of the newly created job.|
|status|BackgroundJobStatus|One of: pending, running, paused, succeeded, failed, cancelled|

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### BackgroundJobStatus

|Field|Type|Required|Description|
|-|-|-|-|
|value|string|Yes|One of: pending, running, paused, succeeded, failed, cancelled|

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
