# Decline auto mode and resume interactive chat

**POST /api/chat/auto/decline**

**Tags:** Copilot

Resolve the ``enable_auto_mode`` call as declined and resume the normal
interactive chat stream. Any sibling tool calls are resolved as denied.

## Request

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|trace_id|string|Yes||
|enable_tool_call_id|string|Yes||
|siblings|array of Siblings|No||

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

### Siblings

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
