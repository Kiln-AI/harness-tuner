# Stream Chat

**POST /api/chat**

**Tags:** Copilot

Forward chat to Kiln Copilot and stream AI SDK events as Server-Sent Events.

## Request

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|messages|array of Messages|Yes||
|trace_id|one of: string or null|No||

## Responses

### 200 OK

Successful Response

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### ChatRequestMessage

|Field|Type|Required|Description|
|-|-|-|-|
|role|string|Yes||
|content|one of: string, array of object or null|No||

### Messages

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of ChatRequestMessage|Yes||

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
