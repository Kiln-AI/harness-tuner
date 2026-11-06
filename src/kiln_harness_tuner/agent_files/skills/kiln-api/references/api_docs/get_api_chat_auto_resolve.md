# Resolve a trace id to an active auto run

**GET /api/chat/auto/resolve**

**Tags:** Copilot

Resolve a (possibly stale) trace id to the active auto run for its
conversation, returning the run id and the run's CURRENT leaf trace id.

Used by the web UI to resync after a hard refresh: the stored trace id is
the leaf the tab last saw, but the server-owned run advances the leaf each
round while the tab is gone. The registry's whole-chain trace index (every
seen trace id → run) matches the stale id anyway, and the returned
``current_trace_id`` lets the client hydrate the rounds it missed before
attaching to the live events stream. 404 if no active run owns the
trace.

## Request

### Query Parameters

|Name|Type|Required|Default|Description|
|-|-|-|-|-|
|trace_id|string|Yes||A trace id from the conversation (may be stale).|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|run_id|string||
|current_trace_id|string||
|status|AutoRunStatus|One of: running, idle, stopped, disabled|

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### AutoRunStatus

|Field|Type|Required|Description|
|-|-|-|-|
|value|string|Yes|One of: running, idle, stopped, disabled|

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
