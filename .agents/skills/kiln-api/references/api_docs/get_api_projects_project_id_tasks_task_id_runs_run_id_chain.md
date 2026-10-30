# Get Run Chain

**GET /api/projects/{project_id}/tasks/{task_id}/runs/{run_id}/chain**

**Tags:** Runs

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|
|task_id|string|Yes|The unique identifier of the task within the project.|
|run_id|string|Yes|The unique identifier of the task run whose chain to return.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|chain|array of Chain|Ordered root-to-leaf, includes the requested run itself as the final entry. If chain_broken is true, the list contains only the intact suffix from the leaf back to (and excluding) the break point.|
|chain_broken|boolean|True if while walking parents we encountered a parent_task_run_id that could not be loaded, a cycle, the depth guard, or the chain length exceeded the leaf trace's user-message count.|
|has_children|boolean|True if at least one other TaskRun in the task references the requested run via parent_task_run_id (i.e. the requested run is an intermediate node in the chain, not a leaf). Used by the UI to warn that sending a new message from this run will create a new branch rather than extending an existing one.|

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### RunChainEntry

A single entry in a multi-turn run's conversation chain.

|Field|Type|Required|Description|
|-|-|-|-|
|run_id|one of: string or null|Yes|The TaskRun id at this turn position in the chain.|
|turn_index|integer|Yes|1-based turn index in the leaf's conversation (turn 1 = root, turn N = leaf). Derived from the leaf trace's user-message count.|

### Chain

Ordered root-to-leaf, includes the requested run itself as the final entry. If chain_broken is true, the list contains only the intact suffix from the leaf back to (and excluding) the break point.

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of RunChainEntry|Yes|Ordered root-to-leaf, includes the requested run itself as the final entry. If chain_broken is true, the list contains only the intact suffix from the leaf back to (and excluding) the break point.|

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
