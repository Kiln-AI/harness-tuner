# Test Git Remote Access

**POST /api/git_sync/test_access**

**Tags:** Git Sync

## Request

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|git_url|string|Yes|The git remote URL to test access against.|
|pat_token|one of: string or null|No|Optional personal access token for authentication.|
|oauth_token|one of: string or null|No|Optional OAuth token for authentication.|
|auth_mode|string|No|Auth mode: 'system_keys', 'pat_token', or 'github_oauth'. One of: system_keys, pat_token, github_oauth|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|success|boolean|Whether access to the remote succeeded.|
|message|string|Human-readable result message.|
|auth_required|boolean|True when the failure is due to missing authentication.|
|write_denied|boolean|True when the user authenticated but the remote rejected the push due to insufficient write permissions.|
|auth_method|one of: string or null|Auth method that succeeded: 'system_keys', 'pat_token', or 'github_oauth'. Null on failure.|

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
