# Stop an auto run

**POST /api/chat/auto/{run_id}/stop**

**Tags:** Copilot

Cooperatively cancel the run. Idempotent — stopping an unknown or
already-terminal run is a no-op.

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|run_id|string|Yes|The auto run id to stop.|

## Responses

### 202 Accepted

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
