# Update Eval

**PATCH /api/projects/{project_id}/tasks/{task_id}/evals/{eval_id}**

**Tags:** Evals

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|
|task_id|string|Yes|The unique identifier of the task within the project.|
|eval_id|string|Yes|The unique identifier of the eval.|

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|name|one of: string or null|No|The updated name.|
|description|one of: string or null|No|The updated description.|
|train_set_filter_id|one of: string or null|No|The updated train set filter ID.|

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
|name|string|The name of the eval. 1-120 chars|
|description|one of: string or null|The description of the eval|
|template|one of: EvalTemplateId or null|The template selected when creating this eval. Useful for suggesting eval steps and output scores.|
|current_config_id|one of: string or null|The id of the current config to use for this eval. This can be changed over time to run the same eval with different configs.|
|eval_set_filter_id|one of: string or null|The id of the dataset filter which defines which dataset items are included when running this eval (V1 TaskRun-typed). This is the eval's test set; the 'eval set' name is legacy.|
|eval_configs_filter_id|one of: string or null|The id of the dataset filter which defines which dataset items are included when comparing the quality of the eval configs under this eval. Should consist of dataset items with ratings.|
|train_set_filter_id|one of: string or null|The id of the dataset filter which defines which dataset items are included in the training set for fine-tuning.|
|val_set_filter_id|one of: string or null|The id of the dataset filter which defines which dataset items are included in the validation set.|
|eval_input_filter_id|one of: string or null|Filter ID for EvalInput-backed datasets (V2). Mutually exclusive with eval_set_filter_id.|
|output_scores|array of OutputScores|The scores this evaluator should produce.|
|favourite|boolean|Whether this eval is a favourite of the user. Rendered as a star icon in the UI.|
|template_properties|one of: object or null|Properties to be used to execute the eval. This is template_type specific and should serialize to a json dict.|
|evaluation_data_type|one of: EvalTemplateId or null|The output of the task run to evaluate. Can be final answer, full trace, or None for V2 evals.|
|multi_turn_drive_config|one of: MultiTurnDriveConfig or null|How to re-drive multi-turn synthetic eval inputs at eval time (synthetic-user model + turn count). Required to execute multi-turn EvalInput items; None for single-turn and stored-trace evals.|
|model_type|string||

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### EvalTemplateId

An eval template is a pre-defined eval that can be used as a starting point for a new eval.

|Field|Type|Required|Description|
|-|-|-|-|
|value|string|Yes|An eval template is a pre-defined eval that can be used as a starting point for a new eval. One of: kiln_requirements, desired_behaviour, kiln_issue, tool_call, toxicity, bias, maliciousness, factual_correctness, jailbreak, rag|

### EvalOutputScore

A definition of a score that an evaluator will produce.

Very similar to TaskRequirement, but conceptually different keeping in a separate models.

|Field|Type|Required|Description|
|-|-|-|-|
|name|string|Yes|The name of the score. Will be provided to the model so use a descriptive name. Should align to the model's TaskRequirement name if you want to use human evals to evaluate the evaluator's performance. 1-32 chars|
|instruction|one of: string or null|No|A description of the score, used to help the model understand the goal of the score. Will be provided to evaluator models, so should be written for the model, not the team/user.|
|type|EvalTemplateId|Yes|Defines the types of rating systems available for task outputs. One of: five_star, pass_fail, pass_fail_critical, custom|
|direction|EvalTemplateId|No|The direction of improvement for an eval output score.

Tells consumers how to interpret a change in the score's value: 'higher_is_better'
means an increase is an improvement, 'lower_is_better' means a decrease is an
improvement, and 'informational' scores carry context only and should never drive
decisions in either direction. One of: higher_is_better, lower_is_better, informational|

### OutputScores

The scores this evaluator should produce.

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of EvalOutputScore|Yes|The scores this evaluator should produce.|

### MultiTurnDriveConfig

Per-eval settings for re-driving multi-turn synthetic inputs at eval time.

A multi-turn eval run regenerates each conversation: the agent under test
comes from the run config being evaluated, while the synthetic user
(customer) defined here is held constant across run configs — so a
comparison varies only the agent. Stored per-eval so re-drives use the
same synthetic-user model and turn count the builder used when driving
the conversations the judge was calibrated on, keeping the judge scoring
the same conversation distribution.

|Field|Type|Required|Description|
|-|-|-|-|
|model_name|string|Yes|The model that plays the synthetic user during re-drives.|
|model_provider|string|Yes|The provider of the synthetic-user model.|
|turns|integer|Yes|Exact number of assistant turns per re-driven conversation (the drive loop has no early termination). 1.0-20.0|

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
