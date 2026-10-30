# List Memories

**GET /api/projects/{project_id}/memories**

**Tags:** Memory

List memory summaries newest-first. content_length 0 means the overview
is the whole memory. Truncation fields nudge how to narrow the results.

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|

### Query Parameters

|Name|Type|Required|Default|Description|
|-|-|-|-|-|
|scope|one of: string or null|No||Exact-match scope filter. Omit for all scopes.|
|tags|one of: array of string or null|No||Memory must have ALL of these tags (AND). Repeat the param for multiple tags; omit for no tag filter.|
|content_match|one of: string or null|No||Case-insensitive regex over overview + content.|
|limit|integer|No|50|Max rows to return. >=1|
|offset|integer|No|0|Rows to skip. >=0|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|listings|array of Listings||
|matched|integer||
|remaining|integer||
|remaining_tag_counts|object||

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### MemoryListing

A single row in a list_memories result. Carries content_length, not content.

|Field|Type|Required|Description|
|-|-|-|-|
|id|string|Yes||
|overview|string|Yes||
|tags|array of string|Yes||
|scope|string|Yes||
|content_length|integer|Yes||
|created_at|string|Yes|Format: date-time|
|created_by|string|Yes||

### Listings

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of MemoryListing|Yes||

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
