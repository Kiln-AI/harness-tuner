# Create Spec With Copilot

**POST /api/projects/{project_id}/tasks/{task_id}/spec_with_copilot**

**Tags:** Copilot

Create a spec using Kiln Copilot.

This endpoint uses Kiln Copilot to create:
1. An Eval for the spec with the appropriate template
2. A judge EvalConfig (LLM-as-judge)
3. Single-turn only: batch examples via copilot API, split into the
   eval + train datasets and persisted as TaskRuns; the golden
   dataset is the request's human-reviewed examples
4. The Spec itself
Plus, for multi-turn: tag existing chain leaves with the golden/train
filter tags and mint one EvalInput per driven case — the eval slice
the runner re-drives per run config at eval time.

If you don't need copilot, use POST /spec instead.

All models are validated before any saves occur. If validation fails,
no data is persisted.

## Request

### Path Parameters

|Name|Type|Required|Description|
|-|-|-|-|
|project_id|string|Yes|The unique identifier of the project.|
|task_id|string|Yes|The unique identifier of the task within the project.|

### Request Body

**Content Type:** `application/json`

|Field|Type|Required|Description|
|-|-|-|-|
|name|string|Yes|1-32 chars|
|definition|string|Yes|The spec definition string, built by client using buildSpecDefinition()|
|properties|one of: DesiredBehaviourProperties, IssueProperties, ToneProperties, FormattingProperties, LocalizationProperties, AppropriateToolUseProperties, ReferenceAnswerAccuracyProperties, FactualCorrectnessProperties, HallucinationsProperties, CompletenessProperties, ToxicityProperties, BiasProperties, MaliciousnessProperties, NsfwProperties, TabooProperties, JailbreakProperties, PromptLeakageProperties|Yes|The spec properties object, pre-built by client with spec_type included|
|evaluate_full_trace|boolean|No||
|reviewed_examples|array of ReviewedExamples|No||
|judge_info.prompt|string|Yes||
|judge_info.model_name|string|Yes||
|judge_info.model_provider|string|Yes||
|sdg_session_config|one of: SyntheticDataGenerationSessionConfigApi or null|No||
|multi_turn|one of: MultiTurnSaveInfo or null|No||
|task_prompt_with_example|string|No||
|task_sample|one of: TaskSample or null|No||

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
|name|string|The name of the spec. 1-120 chars|
|definition|string|A detailed definition of the spec. >=1 chars|
|properties|one of: DesiredBehaviourProperties, IssueProperties, ToneProperties, FormattingProperties, LocalizationProperties, AppropriateToolUseProperties, ReferenceAnswerAccuracyProperties, FactualCorrectnessProperties, HallucinationsProperties, CompletenessProperties, ToxicityProperties, BiasProperties, MaliciousnessProperties, NsfwProperties, TabooProperties, JailbreakProperties, PromptLeakageProperties|The properties of the spec.|
|priority|Priority|Priority levels, where P0 is highest priority. One of: 0, 1, 2, 3|
|status|ModelProviderName|Defines the status of a spec. One of: active, future, deprecated, archived|
|tags|array of string|The tags of the spec.|
|eval_id|one of: string or null|The id of the eval to use for this spec.|
|task_sample|one of: TaskSample or null|An example task input/output pair used to demonstrate expected behavior for this spec.|
|synthetic_data_generation_session_config|one of: SyntheticDataGenerationSessionConfig or null|Config for synthetic data generation session.|
|model_type|string||

### 422 Validation Error

Validation Error

|Field|Type|Description|
|-|-|-|
|detail|array of Detail||

## Schemas

### DesiredBehaviourProperties

|Field|Type|Required|Description|
|-|-|-|-|
|spec_type|string|Yes||
|desired_behaviour_description|string|Yes||
|correct_behaviour_examples|string|No||
|incorrect_behaviour_examples|string|No||

### IssueProperties

|Field|Type|Required|Description|
|-|-|-|-|
|spec_type|string|Yes||
|issue_description|string|Yes||
|issue_examples|string|No||
|non_issue_examples|string|No||

### ToneProperties

|Field|Type|Required|Description|
|-|-|-|-|
|spec_type|string|Yes||
|core_requirement|string|Yes||
|tone_description|string|Yes||
|acceptable_examples|string|No||
|unacceptable_examples|string|No||

### FormattingProperties

|Field|Type|Required|Description|
|-|-|-|-|
|spec_type|string|Yes||
|core_requirement|string|Yes||
|formatting_requirements|string|Yes||
|proper_formatting_examples|string|No||
|improper_formatting_examples|string|No||

### LocalizationProperties

|Field|Type|Required|Description|
|-|-|-|-|
|spec_type|string|Yes||
|core_requirement|string|Yes||
|localization_requirements|string|Yes||
|violation_examples|string|Yes||

### AppropriateToolUseProperties

|Field|Type|Required|Description|
|-|-|-|-|
|spec_type|string|Yes||
|core_requirement|string|Yes||
|tool_id|string|Yes||
|tool_function_name|string|Yes||
|tool_use_guidelines|string|Yes||
|appropriate_tool_use_examples|string|Yes||
|inappropriate_tool_use_examples|string|Yes||

### ReferenceAnswerAccuracyProperties

|Field|Type|Required|Description|
|-|-|-|-|
|spec_type|string|Yes||
|core_requirement|string|Yes||
|reference_answer_accuracy_description|string|Yes||
|accurate_examples|string|Yes||
|inaccurate_examples|string|Yes||

### FactualCorrectnessProperties

|Field|Type|Required|Description|
|-|-|-|-|
|spec_type|string|Yes||
|core_requirement|string|Yes||
|factually_inaccurate_examples|string|Yes||

### HallucinationsProperties

|Field|Type|Required|Description|
|-|-|-|-|
|spec_type|string|Yes||
|core_requirement|string|Yes||
|hallucinations_examples|string|Yes||

### CompletenessProperties

|Field|Type|Required|Description|
|-|-|-|-|
|spec_type|string|Yes||
|core_requirement|string|Yes||
|complete_examples|string|Yes||
|incomplete_examples|string|Yes||

### ToxicityProperties

|Field|Type|Required|Description|
|-|-|-|-|
|spec_type|string|Yes||
|core_requirement|string|Yes||
|toxicity_examples|string|Yes||

### BiasProperties

|Field|Type|Required|Description|
|-|-|-|-|
|spec_type|string|Yes||
|core_requirement|string|Yes||
|bias_examples|string|Yes||

### MaliciousnessProperties

|Field|Type|Required|Description|
|-|-|-|-|
|spec_type|string|Yes||
|core_requirement|string|Yes||
|malicious_examples|string|Yes||

### NsfwProperties

|Field|Type|Required|Description|
|-|-|-|-|
|spec_type|string|Yes||
|core_requirement|string|Yes||
|nsfw_examples|string|Yes||

### TabooProperties

|Field|Type|Required|Description|
|-|-|-|-|
|spec_type|string|Yes||
|core_requirement|string|Yes||
|taboo_examples|string|Yes||

### JailbreakProperties

|Field|Type|Required|Description|
|-|-|-|-|
|spec_type|string|Yes||
|core_requirement|string|Yes||
|jailbroken_examples|string|Yes||

### PromptLeakageProperties

|Field|Type|Required|Description|
|-|-|-|-|
|spec_type|string|Yes||
|core_requirement|string|Yes||
|leakage_examples|string|Yes||

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

### ClaimReviewApi

The reviewer's grades on one trace's claim/evidence distillation.

Mirrors the persisted ClaimReview shape (judge verdict + per-claim
agree/disagree with optional whys) so the save path can write it onto
the golden TaskRun and judge refinement can consume it later.

|Field|Type|Required|Description|
|-|-|-|-|
|judge_score|string|Yes|One of: pass, fail|
|judge_reasoning|string|Yes||
|claims|array of Claims|Yes||
|final_judgement.claim|string|Yes|The claim that was graded.|
|final_judgement.evidence|string|Yes|The one-sentence evidence backing the claim.|
|final_judgement.expected_result|string|Yes|The verdict an AGREE on this claim supports. One of: pass, fail|
|final_judgement.human_grade|string|Yes|The human's grade on this claim. One of: agree, disagree|
|final_judgement.human_feedback|one of: string or null|No|Optional plaintext reason for the grade.|

### ReviewedExample

A reviewed example from the spec review process.

Extends SampleApi with review-specific fields for tracking
model and user judgments on spec compliance.

|Field|Type|Required|Description|
|-|-|-|-|
|input|string|Yes||
|output|string|Yes||
|model_says_meets_spec|boolean|Yes||
|user_says_meets_spec|boolean|Yes||
|feedback|string|Yes||
|claim_review|one of: ClaimReviewApi or null|No|Per-claim grades from the claim/evidence review, when the example was reviewed that way (v2 builder).|

### ReviewedExamples

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of ReviewedExample|Yes||

### ModelProviderName

Enumeration of supported AI model providers.

|Field|Type|Required|Description|
|-|-|-|-|
|value|string|Yes|Enumeration of supported AI model providers. One of: openai, groq, amazon_bedrock, ollama, openrouter, fireworks_ai, kiln_fine_tune, kiln_custom_registry, openai_compatible, anthropic, gemini_api, azure_openai, huggingface, vertex, together_ai, siliconflow_cn, cerebras, docker_model_runner|

### SyntheticDataGenerationSessionConfigApi

Configuration for a synthetic data generation session

|Field|Type|Required|Description|
|-|-|-|-|
|topic_generation_config.task_metadata.model_name|string|Yes|The name of the AI model used.|
|topic_generation_config.task_metadata.model_provider_name|ModelProviderName|Yes|Enumeration of supported AI model providers. One of: openai, groq, amazon_bedrock, ollama, openrouter, fireworks_ai, kiln_fine_tune, kiln_custom_registry, openai_compatible, anthropic, gemini_api, azure_openai, huggingface, vertex, together_ai, siliconflow_cn, cerebras, docker_model_runner|
|topic_generation_config.prompt|string|Yes||
|input_generation_config.task_metadata.model_name|string|Yes|The name of the AI model used.|
|input_generation_config.task_metadata.model_provider_name|ModelProviderName|Yes|Enumeration of supported AI model providers. One of: openai, groq, amazon_bedrock, ollama, openrouter, fireworks_ai, kiln_fine_tune, kiln_custom_registry, openai_compatible, anthropic, gemini_api, azure_openai, huggingface, vertex, together_ai, siliconflow_cn, cerebras, docker_model_runner|
|input_generation_config.prompt|string|Yes||
|output_generation_config.task_metadata.model_name|string|Yes|The name of the AI model used.|
|output_generation_config.task_metadata.model_provider_name|ModelProviderName|Yes|Enumeration of supported AI model providers. One of: openai, groq, amazon_bedrock, ollama, openrouter, fireworks_ai, kiln_fine_tune, kiln_custom_registry, openai_compatible, anthropic, gemini_api, azure_openai, huggingface, vertex, together_ai, siliconflow_cn, cerebras, docker_model_runner|
|output_generation_config.prompt|string|Yes||

### ReviewedChainApi

A reviewer's verdict on one multi-turn chain, keyed by its leaf run.

The leaf TaskRun id is the durable identity that rides from the drive
batch through review to save — the save path writes the golden rating
(and the claim review) onto that leaf.

|Field|Type|Required|Description|
|-|-|-|-|
|leaf_run_id|string|Yes||
|user_says_meets_spec|boolean|Yes||
|feedback|string|No||
|claim_review|one of: ClaimReviewApi or null|No||

### ReviewedChains

The human's review verdicts, one per reviewed chain keyed by leaf TaskRun id. Each becomes a golden RequirementRating on the chain leaf (plus Feedback / per-claim grades when present).

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of ReviewedChainApi|Yes|The human's review verdicts, one per reviewed chain keyed by leaf TaskRun id. Each becomes a golden RequirementRating on the chain leaf (plus Feedback / per-claim grades when present).|

### DrivenSyntheticCaseApi

One driven synthetic-user case from the builder session.

The save path mints an EvalInput from each — the re-drivable input the
eval runner regenerates a conversation from, per run config.

|Field|Type|Required|Description|
|-|-|-|-|
|seed_prompt|string|Yes|The opening user-side message of the conversation. >=1 chars|
|synthetic_user_info|string|Yes|The XML-tagged persona blob as generated (persona/goal/behavior_guidance). Wire format only: the save path parses it into the structured submodel before anything persists. >=1 chars|
|scenario_index|one of: integer or null|No|Zero-based index into the builder's user-approved scenario plan identifying the scenario this case was generated from. Recorded on the minted EvalInput as a `scenario:{index}` provenance tag; omit when the case has no plan scenario.|

### Cases

The driven synthetic-user cases of this batch. Each is minted as an EvalInput — the eval slice the runner re-drives per run config at eval time.

|Field|Type|Required|Description|
|-|-|-|-|
|value|array of DrivenSyntheticCaseApi|Yes|The driven synthetic-user cases of this batch. Each is minted as an EvalInput — the eval slice the runner re-drives per run config at eval time.|

### MultiTurnSaveInfo

Identifies an existing multi-turn synthetic-user batch to turn into an Eval.

The endpoint splits the chains tagged with this batch_tag into golden and
train slices, and mints the eval slice as EvalInput items from `cases` —
the re-drivable inputs the eval runner regenerates conversations from,
per run config, using `drive_config` as the synthetic user.

|Field|Type|Required|Description|
|-|-|-|-|
|batch_tag|string|Yes|The batch_tag emitted by the multi-turn synthetic-user runner (see kiln_ai.synthetic_user.runner). Identifies the set of conversation chains already persisted to disk that this Eval should evaluate.|
|reviewed_chains|array of ReviewedChains|No|The human's review verdicts, one per reviewed chain keyed by leaf TaskRun id. Each becomes a golden RequirementRating on the chain leaf (plus Feedback / per-claim grades when present).|
|cases|array of Cases|Yes|The driven synthetic-user cases of this batch. Each is minted as an EvalInput — the eval slice the runner re-drives per run config at eval time.|
|drive_config.model_name|string|Yes|The model that plays the synthetic user during re-drives.|
|drive_config.model_provider|string|Yes|The provider of the synthetic-user model.|
|drive_config.turns|integer|Yes|Exact number of assistant turns per re-driven conversation (the drive loop has no early termination). 1.0-20.0|

### TaskSample

An example task input/output pair used to demonstrate expected behavior.

|Field|Type|Required|Description|
|-|-|-|-|
|input|string|Yes|The example input for the task.|
|output|string|Yes|The expected output for the task.|

### Priority

Priority levels, where P0 is highest priority.

|Field|Type|Required|Description|
|-|-|-|-|
|value|integer|Yes|Priority levels, where P0 is highest priority. One of: 0, 1, 2, 3|

### SyntheticDataGenerationSessionConfig

Configuration for a synthetic data generation session.

|Field|Type|Required|Description|
|-|-|-|-|
|topic_generation_config.model_name|string|Yes|The model used for generation.|
|topic_generation_config.provider_name|string|Yes|The provider of the model used for generation.|
|topic_generation_config.prompt|string|Yes|The prompt used for generation.|
|input_generation_config.model_name|string|Yes|The model used for generation.|
|input_generation_config.provider_name|string|Yes|The provider of the model used for generation.|
|input_generation_config.prompt|string|Yes|The prompt used for generation.|
|output_generation_config.model_name|string|Yes|The model used for generation.|
|output_generation_config.provider_name|string|Yes|The provider of the model used for generation.|
|output_generation_config.prompt|string|Yes|The prompt used for generation.|

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
