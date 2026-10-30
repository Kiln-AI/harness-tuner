# Open Skill Enclosing Folder

**POST /api/projects/{project_id}/skills/{skill_id}/open_enclosing_folder**

**Tags:** Skills

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|
|skill_id|string|Yes|The unique identifier of the skill.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|path|string|The filesystem path to the file.|

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
