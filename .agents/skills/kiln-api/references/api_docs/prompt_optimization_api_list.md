# Prompt Optimization API

Run and monitor prompt optimization jobs.

|Endpoint|Method|Name|Description|API Details URL|
|-|-|-|-|-|
|`/api/projects/{project_id}/tasks/{task_id}/prompt_optimization_jobs/check_run_config`|GET|Check Run Config|Check if a run config is valid for a Prompt Optimization job by validating the model is supported.|get_api_projects_project_id_tasks_task_id_prompt_optimization_jobs_check_run_config.md|
|`/api/projects/{project_id}/tasks/{task_id}/prompt_optimization_jobs/check_eval`|GET|Check Eval|Check if an eval is valid for a Prompt Optimization job. Validates that the eval has a default confi...|get_api_projects_project_id_tasks_task_id_prompt_optimization_jobs_check_eval.md|
|`/api/projects/{project_id}/tasks/{task_id}/prompt_optimization_jobs/start`|POST|Start Prompt Optimization Job|Start a prompt optimization job by zipping the project and sending it to the Kiln server. Creates an...|post_api_projects_project_id_tasks_task_id_prompt_optimization_jobs_start.md|
|`/api/projects/{project_id}/tasks/{task_id}/prompt_optimization_jobs`|GET|List Prompt Optimization Jobs|List all Prompt Optimization jobs for a task. Optionally update the status of non-final jobs from th...|get_api_projects_project_id_tasks_task_id_prompt_optimization_jobs.md|
|`/api/projects/{project_id}/tasks/{task_id}/prompt_optimization_jobs/{prompt_optimization_job_id}`|GET|Get Prompt Optimization Job|Get a specific Prompt Optimization job and update its status from the remote server. If the job has ...|get_api_projects_project_id_tasks_task_id_prompt_optimization_jobs_prompt_optimization_job_id.md|
|`/api/prompt_optimization_jobs/{job_id}/status`|GET|Get Prompt Optimization Job Status|Get the status of a Prompt Optimization job.|get_api_prompt_optimization_jobs_job_id_status.md|
|`/api/prompt_optimization_jobs/{job_id}/result`|GET|Get Prompt Optimization Job Result|Get the result of a prompt optimization job (includes status and output if completed).|get_api_prompt_optimization_jobs_job_id_result.md|
