# Send a user message into an auto-mode conversation

**POST /api/chat/auto/{run_id}/message**

**Tags:** Copilot

Inject a user message without disabling auto mode (Revision R1).

If a burst is active the message is queued and delivered at the next round
boundary; if the conversation is idle a new burst is started seeded with
the message. The echoed message and resulting events arrive on the run's
observer stream. 404 if the run is unknown or its flag is already off.

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|run_id|string|Yes|The auto run id to message.|

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|content|string|Yes||
|trace_id|one of: string or null|No||

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
