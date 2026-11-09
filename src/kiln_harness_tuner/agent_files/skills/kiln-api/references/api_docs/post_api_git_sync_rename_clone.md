# Rename Clone to Final Path

**POST /api/git_sync/rename_clone**

**Tags:** Git Sync

## Request

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|clone_path|string|Yes|Current filesystem path of the cloned repo (typically in .tmp/).|
|project_name|string|Yes|Human-readable project name for the final directory.|
|project_id|string|Yes|Unique project identifier used in the final path.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|new_clone_path|string|The new filesystem path of the renamed clone.|
|success|boolean|Whether the rename succeeded.|
|message|string|Human-readable result message.|

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
