# Jobs API

Run, monitor, and control background jobs, and stream their events.

|Endpoint|Method|Name|Description|API Details URL|
|-|-|-|-|-|
|`/api/jobs/events`|GET|Stream Job Events|Server-sent events for jobs. Emits an initial `snapshot`, then per-job `job` and `deleted` events. A...|get_api_jobs_events.md|
|`/api/jobs`|GET|List Jobs||get_api_jobs.md|
|`/api/jobs/evals/run`|POST|Run Eval Job|Kick off an eval as a background job and return immediately.  A typed, approval-gated entry point fo...|post_api_jobs_evals_run.md|
|`/api/jobs/wait`|POST|Wait For Jobs|Block until ALL the given jobs reach a terminal state, then return their records (order preserved). ...|post_api_jobs_wait.md|
|`/api/jobs/{type}`|POST|Create Job|Create a job of a registered job type.|post_api_jobs_type.md|
|`/api/jobs/{id}`|GET|Get Job||get_api_jobs_id.md|
|`/api/jobs/{id}/wait`|GET|Wait For Job|Block until the job reaches a terminal state, then return its record.|get_api_jobs_id_wait.md|
|`/api/jobs/{id}`|DELETE|Delete Job||delete_api_jobs_id.md|
|`/api/jobs/{id}/result`|GET|Get Job Result||get_api_jobs_id_result.md|
|`/api/jobs/{id}/errors`|GET|Get Job Errors||get_api_jobs_id_errors.md|
|`/api/jobs/{id}/pause`|POST|Pause Job||post_api_jobs_id_pause.md|
|`/api/jobs/{id}/resume`|POST|Resume Job||post_api_jobs_id_resume.md|
|`/api/jobs/{id}/cancel`|POST|Cancel Job||post_api_jobs_id_cancel.md|
