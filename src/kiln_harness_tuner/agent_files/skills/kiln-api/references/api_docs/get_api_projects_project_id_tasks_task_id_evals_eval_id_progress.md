# Get Eval Progress

**GET /api/projects/{project_id}/tasks/{task_id}/evals/{eval_id}/progress**

**Tags:** Evals

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|
|task_id|string|Yes|The unique identifier of the task within the project.|
|eval_id|string|Yes|The unique identifier of the eval.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|dataset_size|integer|The total size of the eval dataset.|
|golden_dataset_size|integer|The total size of the golden dataset.|
|golden_dataset_not_rated_count|integer|Number of unrated golden dataset items.|
|golden_dataset_partially_rated_count|integer|Number of partially rated golden dataset items.|
|golden_dataset_fully_rated_count|integer|Number of fully rated golden dataset items.|
|train_dataset_size|integer|The total size of the train dataset.|
|current_eval_method|one of: EvalConfig or null|The currently selected eval config.|

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### EvalConfigType

The type of eval configuration, determining how scores are generated.

|Field|Type|Required|Description|
|-|-|-|-|
|value|string|Yes|The type of eval configuration, determining how scores are generated. One of: g_eval, llm_as_judge, v2|

### KilnArtifactProvenance

Why this artifact exists and what it was derived from.

Written once at creation; immutable thereafter (enforced at the API layer).
Compile-time metadata for future agent sessions and humans — never shown to
runtime models (not part of any tool/prompt surface).

|Field|Type|Required|Description|
|-|-|-|-|
|notes|one of: string or null|No|Why this artifact exists: the problem/hypothesis it addresses, what changed vs. the derived_from_ids parents, what validation/evidence supports it (cite eval/run_config/trace IDs inline), and known limits. First line = one-sentence summary. Record observations with conditions, never universal rules. Max ~2000 chars.|
|derived_from_ids|array of one of: string or null|No|IDs of same-type sibling artifacts this one was derived from. Ordered: first = primary parent (the artifact this replaces or is a new version of); further entries = additional sources merged in. Empty = not derived. IDs resolve among siblings in the same parent scope only.|
|origin|one of: string or null|No|Whose judgment created this artifact. 'human': a person authored it directly OR an agent created it fulfilling a direct human request. 'agent': an agent created it autonomously. None: unknown/legacy. Required when this provenance is created; consumers must tolerate unknown values.|

### EvalConfig

A configuration for running an eval. This includes anything needed to run the eval on a dataset like the prompt, model, thresholds, etc.

A eval might have many configs, example running the same eval with 2 different models. Comparing eval results is only valid within the scope of the same config.

|Field|Type|Required|Description|
|-|-|-|-|
|v|integer|No|Schema version for migration support.|
|id|one of: string or null|No|Unique identifier for this record.|
|path|one of: string or null|No|File system path where the record is stored.|
|created_at|string|No|Timestamp when the model was created. Timezone-aware; stores the writer's local offset. Format: date-time|
|created_by|string|No|User ID of the creator.|
|name|string|Yes|The name of the eval config. 1-120 chars|
|model_name|one of: string or null|No|The name of the model to use for this eval config. Required for legacy configs, None for V2.|
|model_provider|one of: string or null|No|The provider of the model to use for this eval config. Required for legacy configs, None for V2.|
|config_type|EvalConfigType|No|The type of eval configuration, determining how scores are generated. One of: g_eval, llm_as_judge, v2|
|properties|one of: one of: object, object, object, object, object, object, object, object, object or null|No|Properties to be used to execute the eval config. Legacy configs use a dict; V2 configs use typed properties.|
|provenance|one of: KilnArtifactProvenance or null|No|Why this artifact exists and what it was derived from.|
|model_type|string|Yes||

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
