# Get Available Tools

**GET /api/projects/{project_id}/available_tools**

**Tags:** Tools & MCP

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|value|array of ToolSetApiDescription||

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### ToolSetType

|Field|Type|Required|Description|
|-|-|-|-|
|value|string|Yes|One of: search, mcp, kiln_task, demo, skill, builtin, code|

### ToolApiDescription

|Field|Type|Required|Description|
|-|-|-|-|
|id|string|Yes||
|name|string|Yes||
|description|one of: string or null|Yes||
|function_name|one of: string or null|No||

### Tools

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of ToolApiDescription|Yes||

### ToolSetApiDescription

|Field|Type|Required|Description|
|-|-|-|-|
|type|ToolSetType|Yes|One of: search, mcp, kiln_task, demo, skill, builtin, code|
|set_name|string|Yes||
|tools|array of Tools|Yes||

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
