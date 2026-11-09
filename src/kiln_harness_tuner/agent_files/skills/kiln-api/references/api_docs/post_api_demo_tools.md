# Set Demo Tools

**POST /api/demo_tools**

**Tags:** Tools & MCP

## Request

### Query Parameters

|Name|Type|Required|Default|Description|
|-|-|-|-|-|
|enable_demo_tools|boolean|Yes||Whether to enable demo tools.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|value|boolean||

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
