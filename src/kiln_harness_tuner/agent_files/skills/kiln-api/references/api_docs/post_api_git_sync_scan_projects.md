# Scan for Kiln Projects

**POST /api/git_sync/scan_projects**

**Tags:** Git Sync

## Request

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|clone_path|string|Yes|Local filesystem path of the cloned repo.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|projects|array of Projects|List of discovered projects.|

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### ProjectInfo

Metadata about a discovered Kiln project.

|Field|Type|Required|Description|
|-|-|-|-|
|path|string|Yes|Relative path to the project.kiln file.|
|name|string|Yes|Project name from the project file.|
|description|string|Yes|Project description from the project file.|
|id|string|No|Unique project identifier.|

### Projects

List of discovered projects.

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of ProjectInfo|Yes|List of discovered projects.|

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
