# List active auto runs

**GET /api/chat/auto/sessions**

**Tags:** Copilot

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|value|array of AutoSessionItem||

## Schemas

### AutoRunStatus

|Field|Type|Required|Description|
|-|-|-|-|
|value|string|Yes|One of: running, idle, stopped, disabled|

### AutoSessionItem

|Field|Type|Required|Description|
|-|-|-|-|
|run_id|string|Yes||
|current_trace_id|one of: string or null|No||
|status|AutoRunStatus|Yes|One of: running, idle, stopped, disabled|
|reason|one of: string or null|No||
