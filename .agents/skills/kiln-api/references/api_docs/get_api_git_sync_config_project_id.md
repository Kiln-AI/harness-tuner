# Get Git Sync Config

**GET /api/git_sync/config/{project_id}**

**Tags:** Git Sync

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
|sync_mode|string|Sync mode: 'auto' or 'manual'. One of: auto, manual|
|auth_mode|string|Auth mode: 'system_keys', 'pat_token', or 'github_oauth'. One of: system_keys, pat_token, github_oauth|
|remote_name|string|Git remote name.|
|branch|string|Branch name being synced.|
|clone_path|one of: string or null|Local filesystem path of the clone.|
|git_url|one of: string or null|The git remote URL.|
|has_pat_token|boolean|Whether a PAT token is configured (token not shown).|
|has_oauth_token|boolean|Whether an OAuth token is configured (token not shown).|

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
