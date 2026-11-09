# OAuth Callback

**GET /api/git_sync/oauth/callback**

**Tags:** Git Sync

## Request

### Query Parameters

|Name|Type|Required|Default|Description|
|-|-|-|-|-|
|state|string|No||OAuth state parameter linking the callback to a pending flow.|
|code|string|No||Authorization code from GitHub to exchange for an access token.|
|error|string|No||Error code returned by GitHub if authorization was denied.|
|error_description|string|No||Human-readable description of the error from GitHub.|

## Responses

### 200 OK

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
