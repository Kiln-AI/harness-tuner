# Refine Judge

**POST /api/projects/{project_id}/tasks/{task_id}/eval_builder/refine_judge**

**Tags:** Eval Builder

Propose a judge-prompt revision from the human's per-claim grades.

The refined prompt is a PROPOSAL — the UI validates it and shows the
changes for approval; it is never auto-applied.

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|
|task_id|string|Yes|The unique identifier of the task.|

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|judge_prompt|string|Yes|>=1 chars|
|graded_traces|array of GradedTraces|Yes||

## Responses

### 200 OK

Successful Response

|Field|Type|Description|
|-|-|-|
|refined_judge_prompt|string||
|changes|array of Changes||
|not_incorporated_feedback|one of: string or null||

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### GradedClaim

One claim/evidence pair with a human grade on it.

`expected_result` is the verdict an AGREE on the claim supports, so a
grade is meaningful relative to a judge's verdict: agreeing with a claim
that points opposite the judge is evidence the judge was wrong.

|Field|Type|Required|Description|
|-|-|-|-|
|claim|string|Yes|The claim that was graded.|
|evidence|string|Yes|The one-sentence evidence backing the claim.|
|expected_result|string|Yes|The verdict an AGREE on this claim supports. One of: pass, fail|
|human_grade|string|Yes|The human's grade on this claim. One of: agree, disagree|
|human_feedback|one of: string or null|No|Optional plaintext reason for the grade.|

### Claims

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of GradedClaim|Yes||

### GradedTraceApi

One human-reviewed trace's grades, shaped to feed judge refinement.

Mirrors the persisted ClaimReview (judge verdict + per-claim
agree/disagree with optional whys) plus a `trace_label` the refine model
cites in its change rationales. Only the claims the reviewer actually
graded appear — an absent claim is "not reviewed", never agreement.

|Field|Type|Required|Description|
|-|-|-|-|
|trace_label|string|Yes|A label for the trace the refine model cites in its rationales; derived UI-side from the run id (often opaque).|
|judge_score|string|Yes|One of: pass, fail|
|judge_reasoning|string|Yes||
|claims|array of Claims|Yes||
|final_judgement.claim|string|Yes|The claim that was graded.|
|final_judgement.evidence|string|Yes|The one-sentence evidence backing the claim.|
|final_judgement.expected_result|string|Yes|The verdict an AGREE on this claim supports. One of: pass, fail|
|final_judgement.human_grade|string|Yes|The human's grade on this claim. One of: agree, disagree|
|final_judgement.human_feedback|one of: string or null|No|Optional plaintext reason for the grade.|

### GradedTraces

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of GradedTraceApi|Yes||

### RefineJudgeChangeApi

One edit the refine model made to the judge prompt, with its rationale.

|Field|Type|Required|Description|
|-|-|-|-|
|change|string|Yes||
|rationale|string|Yes||

### Changes

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of RefineJudgeChangeApi|Yes||

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
