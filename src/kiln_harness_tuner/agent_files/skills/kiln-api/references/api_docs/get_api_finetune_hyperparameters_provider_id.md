# List Finetune Hyperparameters

**GET /api/finetune/hyperparameters/{provider_id}**

**Tags:** Fine-tuning

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|provider_id|string|Yes|The unique identifier of the finetune provider.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|value|array of FineTuneParameter||

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### FineTuneParameter

A parameter for a fine-tune. Hyperparameters, etc.

|Field|Type|Required|Description|
|-|-|-|-|
|name|string|Yes||
|type|string|Yes|One of: string, int, float, bool|
|description|string|Yes||
|optional|boolean|No||

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
