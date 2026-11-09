# Save Git Sync Config

**POST /api/git_sync/save_config**

**Tags:** Git Sync

## Request

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|Unique project identifier.|
|project_path|string|Yes|Relative path to the project.kiln file within the clone.|
|git_url|string|Yes|The git remote URL.|
|clone_path|string|Yes|Local filesystem path of the clone.|
|branch|string|Yes|Branch name to sync.|
|remote_name|string|No|Git remote name.|
|pat_token|one of: string or null|No|Optional personal access token for authentication.|
|oauth_token|one of: string or null|No|Optional OAuth token for authentication.|
|auth_mode|string|No|Auth mode detected during setup: 'system_keys', 'pat_token', or 'github_oauth'. One of: system_keys, pat_token, github_oauth|
|sync_mode|string|No|Sync mode: 'auto' or 'manual'. One of: auto, manual|
|remove_conflicting_id|boolean|No|When true and a duplicate project ID conflict is detected, remove the existing project registration before saving.|
|trusted|boolean|No|Must be true to confirm trust before importing. Kiln projects can contain code that runs on your machine.|

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
