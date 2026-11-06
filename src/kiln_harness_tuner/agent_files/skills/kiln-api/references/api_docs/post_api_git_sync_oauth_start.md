# Start GitHub OAuth Flow

**POST /api/git_sync/oauth/start**

**Tags:** Git Sync

## Request

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|git_url|string|Yes|The git remote URL to authenticate against.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|authorize_url|string|GitHub OAuth authorization URL to open in the browser.|
|install_url|string|GitHub App installation URL (used if app not yet installed on repo).|
|state|string|OAuth state parameter for polling.|
|owner_name|string|Parsed owner name from git URL.|
|repo_name|string|Parsed repo name from git URL.|
|owner_pre_selected|boolean|Whether the owner was pre-selected in the install URL.|
|repo_pre_selected|boolean|Whether the repo was pre-selected in the install URL.|

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
