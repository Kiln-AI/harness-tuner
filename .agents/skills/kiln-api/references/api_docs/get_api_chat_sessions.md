# List chat sessions

**GET /api/chat/sessions**

**Tags:** Copilot

Proxy to Kiln Copilot ``GET /v1/chat/sessions``.

## Request

### Query Parameters

|Name|Type|Required|Default|Description|
|-|-|-|-|-|
|limit|integer|No|50|Maximum number of sessions to return|
|offset|integer|No|0|Number of sessions to skip|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|value|array of ChatSessionListItem||

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### ChatSessionListItem

|Field|Type|Required|Description|
|-|-|-|-|
|id|string|Yes||
|title|one of: string or null|No||
|updated_at|one of: string or null|No||
|auto_active|boolean|No||
|auto_run_id|one of: string or null|No||

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
