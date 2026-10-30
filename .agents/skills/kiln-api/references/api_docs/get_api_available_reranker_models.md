# List Available Reranker Models

**GET /api/available_reranker_models**

**Tags:** Providers & Models

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|value|array of RerankerProvider||

## Schemas

### RerankerModelDetails

|Field|Type|Required|Description|
|-|-|-|-|
|id|string|Yes||
|name|string|Yes||
|deprecated|boolean|No||

### Models

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of RerankerModelDetails|Yes||

### RerankerProvider

|Field|Type|Required|Description|
|-|-|-|-|
|provider_name|string|Yes||
|provider_id|string|Yes||
|models|array of Models|Yes||
