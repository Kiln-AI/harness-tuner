# Execute approved client tools and continue chat stream

**POST /api/chat/execute-tools**

**Tags:** Copilot

Tool calls that require user approval are streamed to the client for approval, along with the
other toolcalls part of the same turn. The user must approve / reject all the approval-requiring
toolcalls in the UI, then send back the decisions through this endpoint, which will execute
the toolcalls and continue the chat stream.

## Request

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|trace_id|string|Yes||
|tool_calls|array of ToolCalls|Yes||
|decisions|object|Yes||

## Responses

### 200 OK

Successful Response

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

### ToolCalls

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
