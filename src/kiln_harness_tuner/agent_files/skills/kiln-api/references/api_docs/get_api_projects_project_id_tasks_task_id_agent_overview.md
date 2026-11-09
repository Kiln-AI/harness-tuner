# Agent Overview

**GET /api/projects/{project_id}/tasks/{task_id}/agent_overview**

**Tags:** Agent

An overview of a task and all it's components, optimized for agents.

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|
|task_id|string|Yes|The unique identifier of the task within the project.|

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|project.id|one of: string or null||
|project.name|string||
|project.description|one of: string or null||
|task.id|one of: string or null||
|task.name|string||
|task.description|one of: string or null||
|task.instruction|string|Task instruction, truncated with sentinel if over 70 words.|
|task.input_json_schema|one of: any or null|JSON Schema for task input, or null if plain text.|
|task.output_json_schema|one of: any or null|JSON Schema for task output, or null if plain text.|
|task.default_run_config_id|one of: string or null||
|dataset.total_count|integer||
|dataset.by_tag|object||
|dataset.by_source|object|Counts keyed by DataSourceType.|
|dataset.by_rating|object|Counts keyed by star rating (1-5) or 'unrated'.|
|docs.doc_count|integer||
|docs.by_tag|object||
|docs.by_kind|object||
|search_tools.items|array of Items||
|search_tools.archived_search_tool_count|integer||
|prompts.total|integer||
|prompts.showing|string|Human-readable 'N of M' summary.|
|prompts.generators_from_task_instruction_count|integer|Number of built-in prompt generators that auto-generate from the task instruction.|
|prompts.items|array of ItemsV2||
|specs.items|array of ItemsV3||
|specs.archived_spec_count|integer||
|evals|array of Evals||
|tool_servers.items|array of ItemsV4||
|tool_servers.archived_tool_server_count|integer||
|run_configs.default_run_config_id|one of: string or null||
|run_configs.total|integer||
|run_configs.showing|string|Human-readable 'N of M' summary.|
|run_configs.items|array of ItemsV5||
|fine_tunes.total_count|integer||
|prompt_optimization_jobs.total_count|integer||
|skills.items|array of ItemsV6||
|skills.archived_skill_count|integer||
|connected_providers|object||

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### AgentOverviewSearchTool

|Field|Type|Required|Description|
|-|-|-|-|
|id|one of: string or null|Yes||
|name|string|Yes||
|tool_name|string|Yes||
|tool_description|string|Yes||
|description|one of: string or null|Yes||
|tags|one of: array of string or null|Yes||

### Items

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of AgentOverviewSearchTool|Yes||

### AgentOverviewPrompt

A prompt available for use in run configs.

|Field|Type|Required|Description|
|-|-|-|-|
|id|string|Yes|The prompt ID (e.g. 'id::...' or 'task_run_config::...').|
|name|string|Yes||
|type|string|Yes|Type label (e.g. 'Custom', 'Frozen', 'Fine-Tune').|

### ItemsV2

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of AgentOverviewPrompt|Yes||

### AgentOverviewSpec

A non-archived eval spec with its priority and status.

|Field|Type|Required|Description|
|-|-|-|-|
|eval_id|one of: string or null|Yes|The eval this spec belongs to.|
|name|string|Yes||
|spec_type|string|Yes||
|priority|string|Yes|Priority level name (e.g. 'p0', 'p1', 'p2').|
|status|string|Yes||
|tags|array of string|Yes||

### ItemsV3

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of AgentOverviewSpec|Yes||

### AgentOverviewOutputScore

|Field|Type|Required|Description|
|-|-|-|-|
|name|string|Yes||
|type|string|Yes||

### OutputScores

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of AgentOverviewOutputScore|Yes||

### AgentOverviewEval

An eval definition with its scoring configuration.

|Field|Type|Required|Description|
|-|-|-|-|
|eval_id|one of: string or null|Yes||
|name|string|Yes||
|description|one of: string or null|Yes||
|template|one of: string or null|Yes|Eval template type, if template-based.|
|default_judge_config_id|one of: string or null|Yes|ID of the default judge run config for this eval.|
|output_scores|array of OutputScores|Yes||
|favourite|boolean|Yes||

### Evals

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of AgentOverviewEval|Yes||

### AgentOverviewToolServer

|Field|Type|Required|Description|
|-|-|-|-|
|id|one of: string or null|Yes||
|name|string|Yes||
|type|string|Yes||
|description|one of: string or null|Yes||

### ItemsV4

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of AgentOverviewToolServer|Yes||

### AgentOverviewRunConfig

A run configuration defining model, prompt, and tool selections.

|Field|Type|Required|Description|
|-|-|-|-|
|id|one of: string or null|Yes||
|name|string|Yes||
|description|one of: string or null|Yes||
|type|string|Yes|Run config type (e.g. 'agent', 'mcp').|
|model_name|one of: string or null|Yes||
|model_provider|one of: string or null|Yes||
|prompt_id|one of: string or null|Yes|Prompt ID used by this config, matching AgentOverviewPrompt.id format.|
|tool_ids|array of string|Yes|Tool IDs attached to this config, including skills (prefixed with 'kiln_tool::skill::').|
|starred|boolean|Yes||

### ItemsV5

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of AgentOverviewRunConfig|Yes||

### AgentOverviewSkill

|Field|Type|Required|Description|
|-|-|-|-|
|id|one of: string or null|Yes||
|name|string|Yes||
|description|string|Yes||

### ItemsV6

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of AgentOverviewSkill|Yes||

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
