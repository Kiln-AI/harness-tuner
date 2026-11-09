# List Remote Branches

**POST /api/git_sync/list_branches**

**Tags:** Git Sync

## Request

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|git_url|string|Yes|The git remote URL to list branches from.|
|pat_token|one of: string or null|No|Optional personal access token for authentication.|
|oauth_token|one of: string or null|No|Optional OAuth token for authentication.|
|auth_mode|string|No|Auth mode: 'system_keys', 'pat_token', or 'github_oauth'. One of: system_keys, pat_token, github_oauth|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|branches|array of string|Branch names available on the remote.|
|default_branch|one of: string or null|The HEAD branch of the remote, if detected.|

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
