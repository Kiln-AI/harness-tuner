# List Finetune Providers

**GET /api/finetune_providers**

**Tags:** Fine-tuning

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|value|array of FinetuneProvider||

## Schemas

### ChatStrategy

Strategy for how a chat is structured.

|Field|Type|Required|Description|
|-|-|-|-|
|value|string|Yes|Strategy for how a chat is structured. One of: final_only, final_and_intermediate, two_message_cot, final_and_intermediate_r1_compatible|

### DataStrategiesSupported

The data strategies supported by this model.

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of ChatStrategy|Yes|The data strategies supported by this model.|

### FinetuneProviderModel

A model available for fine-tuning from a provider.

|Field|Type|Required|Description|
|-|-|-|-|
|name|string|Yes|The display name of the model.|
|id|string|Yes|The provider's model identifier.|
|data_strategies_supported|array of DataStrategiesSupported|No|The data strategies supported by this model.|
|supports_function_calling|boolean|No|Whether the model supports function calling.|

### Models

The models available for fine-tuning.

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of FinetuneProviderModel|Yes|The models available for fine-tuning.|

### FinetuneProvider

A provider that offers fine-tuning.

|Field|Type|Required|Description|
|-|-|-|-|
|name|string|Yes|The display name of the provider.|
|id|string|Yes|The unique provider identifier used in API calls.|
|enabled|boolean|Yes|Whether this provider is enabled.|
|models|array of Models|Yes|The models available for fine-tuning.|
