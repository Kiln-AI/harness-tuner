# Eval Builder API

Eval builder V2 review pipeline: judge and claim/evidence generation over alignment traces.

|Endpoint|Method|Name|Description|API Details URL|
|-|-|-|-|-|
|`/api/projects/{project_id}/tasks/{task_id}/eval_builder/review_pipeline`|POST|Run Multi-Turn Review Pipeline|The merged multi-turn stream: [drive → judge → claims] per case.  Emits (all frames `type`-discrimin...|post_api_projects_project_id_tasks_task_id_eval_builder_review_pipeline.md|
|`/api/projects/{project_id}/tasks/{task_id}/eval_builder/review_traces`|POST|Review Traces|Per-trace `judge → claim builder` over single-turn I/O pairs, fanned out (local) and streamed.  Emit...|post_api_projects_project_id_tasks_task_id_eval_builder_review_traces.md|
|`/api/projects/{project_id}/tasks/{task_id}/eval_builder/build_claims`|POST|Build Claims|Claims-only primitive: build claims for one trace given a known verdict.  Used by the refine loop (r...|post_api_projects_project_id_tasks_task_id_eval_builder_build_claims.md|
|`/api/projects/{project_id}/tasks/{task_id}/eval_builder/refine_judge`|POST|Refine Judge|Propose a judge-prompt revision from the human's per-claim grades.  The refined prompt is a PROPOSAL...|post_api_projects_project_id_tasks_task_id_eval_builder_refine_judge.md|
