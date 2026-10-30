# Get Run

**GET /api/projects/{project_id}/tasks/{task_id}/runs/{run_id}**

**Tags:** Runs

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|
|task_id|string|Yes|The unique identifier of the task within the project.|
|run_id|string|Yes|The unique identifier of the task run.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|v|integer|Schema version for migration support.|
|id|one of: string or null|Unique identifier for this record.|
|path|one of: string or null|File system path where the record is stored.|
|created_at|string|Timestamp when the model was created. Timezone-aware; stores the writer's local offset. Format: date-time|
|created_by|string|User ID of the creator.|
|input|string|The inputs to the task. JSON formatted for structured input, plaintext for unstructured input.|
|input_source|one of: DataSource or null|The source of the input: human or synthetic.|
|output.v|integer|Schema version for migration support.|
|output.id|one of: string or null|Unique identifier for this record.|
|output.path|one of: string or null|File system path where the record is stored.|
|output.created_at|string|Timestamp when the model was created. Timezone-aware; stores the writer's local offset. Format: date-time|
|output.created_by|string|User ID of the creator.|
|output.output|string|The output of the task. JSON formatted for structured output, plaintext for unstructured output.|
|output.source|one of: DataSource or null|The source of the output: human or synthetic.|
|output.rating|one of: TaskOutputRating-Output or null|The rating of the output|
|output.model_type|string||
|repair_instructions|one of: string or null|Instructions for fixing the output. Should define what is wrong, and how to fix it. Will be used by models for both generating a fixed output, and evaluating future models.|
|repaired_output|one of: TaskOutput-Output or null|An version of the output with issues fixed. This must be a 'fixed' version of the existing output, and not an entirely new output. If you wish to generate an ideal curatorial output for this task unrelated to this output, generate a new TaskOutput with type 'human' instead of using this field.|
|intermediate_outputs|one of: object or null|Intermediate outputs from the task run. Keys are the names of the intermediate output steps (cot=chain of thought, etc), values are the output data.|
|tags|array of string|Tags for the task run. Tags are used to categorize task runs for filtering and reporting.|
|usage|one of: Usage or null|Usage information for the task run. This includes the number of input tokens, output tokens, and total tokens used.|
|cumulative_usage|one of: MessageUsage or null|Sum of per-message token usage and cost across the entire trace, including any seeded prior trace. None on records created before this field existed. For a fresh (non-seeded) run, the token / cost fields equal those of `usage`.|
|trace|one of: array of one of: object, object, object, object, object, object or null|The trace of the task run in OpenAI format. This is the list of messages that were sent to/from the model.|
|parent_task_run_id|one of: string or null|The ID of the parent task run. This is the ID of the task run that contains this task run.|
|model_type|string||

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### DataSourceType

The source type of a piece of data.

Human: a human created the data
Synthetic: a model created the data

|Field|Type|Required|Description|
|-|-|-|-|
|value|string|Yes|The source type of a piece of data.

Human: a human created the data
Synthetic: a model created the data. One of: human, synthetic, file_import, tool_call|

### DataSource

Represents the origin of data, either human, synthetic, file import, or tool call, with associated properties.

Properties vary based on the source type - for synthetic/tool_call sources this includes
model information, for human sources this includes creator information, for file imports
this includes file information.

|Field|Type|Required|Description|
|-|-|-|-|
|type|DataSourceType|Yes|The source type of a piece of data.

Human: a human created the data
Synthetic: a model created the data. One of: human, synthetic, file_import, tool_call|
|properties|object|No|Properties describing the data source. For synthetic things like model. For human: the human's name. For file_import: file information.|
|run_config_id|one of: string or null|No|The ID of the saved TaskRunConfig used to produce this data, if any. Only present when the run was initiated from a saved TaskRunConfig (e.g. via the saved-config dropdown or a tool call); runs configured ad-hoc from the run page leave this unset. Not validated against the file system: a TaskRunConfig may be deleted without invalidating historical runs that reference it.|
|run_config|one of: one of: object, object or null|No|The run config used to generate the data, if generated by a running a model in Kiln (only true for type=synthetic).|

### TaskOutputRating-Output

A rating for a task output, including an overall rating and ratings for each requirement.

Supports:
- five_star: 1-5 star ratings
- pass_fail: boolean pass/fail (1.0 = pass, 0.0 = fail)
- pass_fail_critical: tri-state (1.0 = pass, 0.0 = fail, -1.0 = critical fail)

|Field|Type|Required|Description|
|-|-|-|-|
|v|integer|No|Schema version for migration support.|
|id|one of: string or null|No|Unique identifier for this record.|
|path|one of: string or null|No|File system path where the record is stored.|
|created_at|string|No|Timestamp when the model was created. Timezone-aware; stores the writer's local offset. Format: date-time|
|created_by|string|No|User ID of the creator.|
|type|DataSourceType|No|Defines the types of rating systems available for task outputs. One of: five_star, pass_fail, pass_fail_critical, custom|
|value|one of: number or null|No|The rating value. Interpretation depends on rating type:
- five_star: 1-5 stars
- pass_fail: 1.0 (pass) or 0.0 (fail)
- pass_fail_critical: 1.0 (pass), 0.0 (fail), or -1.0 (critical fail)|
|requirement_ratings|object|No|The ratings of the requirements of the task. The ID can be either a task_requirement_id or a named rating for an eval_output_score name (in format 'named::<name>').|
|model_type|string|Yes||

### TaskOutput-Output

An output for a specific task run.

Contains the actual output content, its source (human or synthetic),
and optional rating information.

|Field|Type|Required|Description|
|-|-|-|-|
|v|integer|No|Schema version for migration support.|
|id|one of: string or null|No|Unique identifier for this record.|
|path|one of: string or null|No|File system path where the record is stored.|
|created_at|string|No|Timestamp when the model was created. Timezone-aware; stores the writer's local offset. Format: date-time|
|created_by|string|No|User ID of the creator.|
|output|string|Yes|The output of the task. JSON formatted for structured output, plaintext for unstructured output.|
|source|one of: DataSource or null|No|The source of the output: human or synthetic.|
|rating|one of: TaskOutputRating-Output or null|No|The rating of the output|
|model_type|string|Yes||

### Usage

Token usage, cost, and aggregate LLM latency for a per-run accumulator.

Extends :class:`MessageUsage` with ``total_llm_latency_ms``, which is
only meaningful while a single run is in flight (its model calls run
sequentially in real time). For per-message records and full-trace
sums use :class:`MessageUsage` — those values would mix latencies
from different points in time, so the field doesn't apply.

|Field|Type|Required|Description|
|-|-|-|-|
|input_tokens|one of: integer or null|No|The number of input tokens used.|
|output_tokens|one of: integer or null|No|The number of output tokens used.|
|total_tokens|one of: integer or null|No|The total number of tokens used.|
|cost|one of: number or null|No|The cost in US dollars, saved at runtime (prices can change over time).|
|cached_tokens|one of: integer or null|No|Number of tokens served from prompt cache. None if not reported.|
|total_llm_latency_ms|one of: integer or null|No|Total time spent waiting on LLM API calls in milliseconds. Sum of per-call latencies, excludes tool execution time.|

### MessageUsage

Token usage and cost for a single LLM call or a multi-message sum.

Carries only the fields that are meaningfully aggregatable across
messages: token counts and cost. Per-call latency lives on the
individual message's ``latency_ms`` field; aggregating it across the
full trace would mix latencies from different points in time, so
``MessageUsage`` does NOT carry ``total_llm_latency_ms``.

The :class:`Usage` subclass adds ``total_llm_latency_ms`` for the
in-flight per-run accumulator that tracks how long this run spent
waiting on LLM calls.

|Field|Type|Required|Description|
|-|-|-|-|
|input_tokens|one of: integer or null|No|The number of input tokens used.|
|output_tokens|one of: integer or null|No|The number of output tokens used.|
|total_tokens|one of: integer or null|No|The total number of tokens used.|
|cost|one of: number or null|No|The cost in US dollars, saved at runtime (prices can change over time).|
|cached_tokens|one of: integer or null|No|Number of tokens served from prompt cache. None if not reported.|

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
