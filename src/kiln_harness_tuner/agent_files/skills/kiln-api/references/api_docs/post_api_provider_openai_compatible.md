# Add OpenAI Compatible Provider

**POST /api/provider/openai_compatible**

**Tags:** Providers & Models

## Request

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|name|string|Yes|Name for the OpenAI compatible provider.|
|base_url|string|Yes|Base URL for the OpenAI compatible API.|
|api_key|string|Yes|API key for authentication.|

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
