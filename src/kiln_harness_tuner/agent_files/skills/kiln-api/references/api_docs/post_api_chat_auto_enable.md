# Enable auto mode for a chat

**POST /api/chat/auto/enable**

**Tags:** Copilot

Start a server-owned auto run that continues the chat autonomously.

Begins a fresh upstream continuation from the seed's ``trace_id`` (resolving
the accepted ``enable_auto_mode`` call as enabled). The run is supervised by
the registry and survives client disconnects.

## Request

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|trace_id|one of: string or null|No||
|enable_tool_call_id|one of: string or null|No||
|pending_tool_calls|array of PendingToolCalls|No||
|extra_messages|array of object|No||
|reason|one of: string or null|No||

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|run_id|string||

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### ToolCallInfo

|Field|Type|Required|Description|
|-|-|-|-|
|toolCallId|string|Yes||
|toolName|string|Yes||
|input|object|Yes||
|requiresApproval|boolean|Yes||

### PendingToolCalls

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of ToolCallInfo|Yes||

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
