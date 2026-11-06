# Get Rating Options

**GET /api/projects/{project_id}/tasks/{task_id}/rating_options**

**Tags:** Tasks

Determines which rating options should be shown for a given dataset item.

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
|options|array of Options|The list of rating options.|

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### Priority

Priority levels, where P0 is highest priority.

|Field|Type|Required|Description|
|-|-|-|-|
|value|integer|Yes|Priority levels, where P0 is highest priority. One of: 0, 1, 2, 3|

### TaskOutputRatingType

Defines the types of rating systems available for task outputs.

|Field|Type|Required|Description|
|-|-|-|-|
|value|string|Yes|Defines the types of rating systems available for task outputs. One of: five_star, pass_fail, pass_fail_critical, custom|

### RatingOption

A rating requirement with display rules.

|Field|Type|Required|Description|
|-|-|-|-|
|requirement.id|one of: string or null|No|Unique identifier for this record.|
|requirement.name|string|Yes|The name of the task requirement. 1-32 chars|
|requirement.description|one of: string or null|No|Optional elaboration on the requirement's purpose.|
|requirement.instruction|string|Yes|Instructions for meeting the requirement. >=1 chars|
|requirement.priority|Priority|No|Priority levels, where P0 is highest priority. One of: 0, 1, 2, 3|
|requirement.type|TaskOutputRatingType|No|Defines the types of rating systems available for task outputs. One of: five_star, pass_fail, pass_fail_critical, custom|
|show_for_all|boolean|Yes|Whether this rating option is shown for all outputs.|
|show_for_tags|array of string|Yes|Tags for which this rating option is shown.|

### Options

The list of rating options.

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of RatingOption|Yes|The list of rating options.|

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
