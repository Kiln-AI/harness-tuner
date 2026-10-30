# Disconnect Provider API Key

**POST /api/provider/disconnect_api_key**

**Tags:** Providers & Models

## Request

### Query Parameters

|Name|Type|Required|Default|Description|
|-|-|-|-|-|
|provider_id|string|Yes||The provider identifier to disconnect.|

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
