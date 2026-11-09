# OAuth Flow Status

**GET /api/git_sync/oauth/status/{state}**

**Tags:** Git Sync

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|state|string|Yes|The OAuth state parameter to check.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|complete|boolean|Whether the OAuth flow has completed.|
|oauth_token|one of: string or null|The OAuth token, if flow completed successfully.|
|error|one of: string or null|Error message, if flow failed.|

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
