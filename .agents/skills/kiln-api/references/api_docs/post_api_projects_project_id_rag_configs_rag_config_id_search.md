# Search Rag Config

**POST /api/projects/{project_id}/rag_configs/{rag_config_id}/search**

**Tags:** Documents

Search the vector store associated with a RAG config.

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|
|rag_config_id|string|Yes|The unique identifier of the RAG configuration.|

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|query|string|Yes|The search query text|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|results|array of Results|The search results|

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### SearchResult

|Field|Type|Required|Description|
|-|-|-|-|
|document_id|string|Yes|The id of the Kiln document.|
|chunk_idx|integer|Yes|The index of the chunk.|
|chunk_text|string|Yes|The text of the chunk.|
|similarity|one of: number or null|Yes|The score of the chunk, which depends on the similarity metric used.|

### Results

The search results

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of SearchResult|Yes|The search results|

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
