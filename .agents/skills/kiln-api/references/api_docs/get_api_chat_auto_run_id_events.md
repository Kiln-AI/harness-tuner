# Stream auto run events

**GET /api/chat/auto/{run_id}/events**

**Tags:** Copilot

Pure-observer SSE stream of a run's chat events (404 if unknown/GC'd).

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|run_id|string|Yes|The auto run id to observe.|

## Responses

### 200 OK

Successful Response

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

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
