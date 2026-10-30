# Select Kiln File

**GET /api/select_kiln_file**

**Tags:** Settings & Utilities

## Request

### Query Parameters

|Name|Type|Required|Default|Description|
|-|-|-|-|-|
|title|string|No|Select Kiln File|The title for the file dialog window.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|file_path|one of: string or null||

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
