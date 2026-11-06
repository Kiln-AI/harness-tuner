# Get chat session

**GET /api/chat/sessions/{session_id}**

**Tags:** Copilot

Proxy to Kiln Copilot ``GET /v1/chat/sessions/{session_id}``.

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|session_id|string|Yes|Chat session id (same as trace id for continuation).|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|id|string||
|task_run.trace|one of: array of object or null||
|context_usage|one of: ContextUsage or null||

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### ContextUsage

Proxy mirror of the kiln_server ``ContextUsage`` value object.

Carries only the gauge numbers and the ``compacted`` flag — never any trace
content — so it is safe to surface to the web UI. Every field is optional so
an older upstream that doesn't emit ``context_usage`` (or emits a partial
object) never 500s the proxy; the web UI hides the gauge when it's absent.

|Field|Type|Required|Description|
|-|-|-|-|
|context_tokens|one of: integer or null|No||
|context_limit|one of: integer or null|No||
|context_percent|one of: number or null|No||
|compacted|one of: boolean or null|No||

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
