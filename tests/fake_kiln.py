"""Fake KilnClient for the measurement-lane tests (the decision tree
runs against a fake KilnClient — no live server).

Routes the call_kiln_api-shaped surface analysis.py uses: eval defs, the
EvalRun results cache (per (eval, rc, split)), the eval-job lifecycle with
scripted per-invocation behaviors, and canned `POST /api/statistics`
responses. Everything is recorded for assertions.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import Any

from kiln_harness_tuner.kiln import KilnAPIError, KilnTransportError

_KILN_FORBIDDEN = re.compile(r"[/\\?%*:|\"<>.,;=\n]")


def _kiln_name_ok(name: str) -> bool:
    """Kiln's rule: a name its `string_to_valid_name` leaves unchanged."""
    cleaned = unicodedata.normalize("NFKD", name)
    cleaned = _KILN_FORBIDDEN.sub(" ", cleaned)
    cleaned = re.sub(r"[\x00-\x1F]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_").strip()
    return 1 <= len(name) <= 120 and cleaned == name


_RESULTS_PATH = re.compile(
    r"^/api/projects/(?P<pid>[^/]+)/tasks/(?P<tid>[^/]+)/evals/(?P<eid>[^/]+)"
    r"/eval_config/(?P<ecid>[^/]+)/run_config/(?P<rcid>[^/]+)/results$"
)
_EVAL_PATH = re.compile(
    r"^/api/projects/(?P<pid>[^/]+)/tasks/(?P<tid>[^/]+)/evals/(?P<eid>[^/]+)$"
)
_EVAL_CONFIG_PATH = re.compile(
    r"^/api/projects/(?P<pid>[^/]+)/tasks/(?P<tid>[^/]+)/evals/(?P<eid>[^/]+)"
    r"/eval_config/(?P<ecid>[^/]+)$"
)
_TASK_PATH = re.compile(r"^/api/projects/(?P<pid>[^/]+)/tasks/(?P<tid>[^/]+)$")
# The noise check: a new, non-default eval config.
_CREATE_EVAL_CONFIG_PATH = re.compile(
    r"^/api/projects/(?P<pid>[^/]+)/tasks/(?P<tid>[^/]+)/evals/(?P<eid>[^/]+)"
    r"/create_eval_config$"
)
# Saved prompts, listed as the live server lists them (`id` is
# the PromptId, `id::<id>`), and any prompt id rendered by `gen_prompt`.
_PROMPTS_PATH = re.compile(r"^/api/projects/[^/]+/tasks/[^/]+/prompts$")
_GEN_PROMPT_PATH = re.compile(
    r"^/api/projects/[^/]+/tasks/[^/]+/gen_prompt/(?P<pid>.+)$"
)
_RUN_CONFIGS_PATH = re.compile(
    r"^/api/projects/(?P<pid>[^/]+)/tasks/(?P<tid>[^/]+)/run_configs$"
)
_EVALS_LIST_PATH = re.compile(
    r"^/api/projects/(?P<pid>[^/]+)/tasks/(?P<tid>[^/]+)/evals$"
)
_SPECS_LIST_PATH = re.compile(
    r"^/api/projects/(?P<pid>[^/]+)/tasks/(?P<tid>[^/]+)/specs$"
)
_JOB_ERRORS_PATH = re.compile(r"^/api/jobs/(?P<jid>[^/]+)/errors$")
_JOB_CANCEL_PATH = re.compile(r"^/api/jobs/(?P<jid>[^/]+)/cancel$")
_JOB_GET_PATH = re.compile(r"^/api/jobs/(?P<jid>job_[^/]+)$")
_MEMORIES_PATH = re.compile(r"^/api/projects/(?P<pid>[^/]+)/memories$")
# Code probes: the routes `kiln_harness_tuner.probes` drives.
_RUN_PATH = re.compile(r"^/api/projects/(?P<pid>[^/]+)/tasks/(?P<tid>[^/]+)/run$")
_TASK_RUN_PATH = re.compile(
    r"^/api/projects/(?P<pid>[^/]+)/tasks/(?P<tid>[^/]+)/runs/(?P<rid>[^/]+)$"
)
_CODE_TOOLS_PATH = re.compile(r"^/api/projects/(?P<pid>[^/]+)/code_tools$")
_SKILLS_PATH = re.compile(r"^/api/projects/(?P<pid>[^/]+)/skills$")
_CODE_TRUST_PATH = re.compile(r"^/api/projects/(?P<pid>[^/]+)/add_code_trust$")
_MEMORIES_BY_IDS_PATH = re.compile(r"^/api/projects/(?P<pid>[^/]+)/memories/by_ids$")
_MEMORY_PATH = re.compile(r"^/api/projects/(?P<pid>[^/]+)/memories/(?P<mid>[^/]+)$")


def make_score(
    name: str = "Accuracy",
    type: str = "pass_fail",
    direction: str | None = "higher_is_better",
) -> dict[str, Any]:
    score: dict[str, Any] = {"name": name, "type": type}
    if direction is not None:
        score["direction"] = direction
    return score


def make_eval(
    eval_id: str = "eval_1",
    config_id: str | None = "ec_1",
    scores: list[dict[str, Any]] | None = None,
    name: str | None = None,
    train_filter: str | None = "tag::train",
    val_filter: str | None = "tag::val",
    test_filter: str | None = "tag::test",
    split_source: str = "eval_input",
    legacy_split_fields: bool = False,
) -> dict[str, Any]:
    """One `GET …/evals` row. Split filter ids are on the eval itself
    (verified against a live project tree) — the setup phase's
    readiness audit reads them from here, never by probing.

    They live under `splits` by default, which is where a current server puts
    them. `legacy_split_fields=True` emits the flat `*_set_filter_id` shape an
    older server serves instead; note that shape has no home for `val`, so the
    row it builds genuinely has no val split.
    """
    row: dict[str, Any] = {
        "id": eval_id,
        "name": name if name is not None else f"Eval {eval_id}",
        "description": None,
        "current_config_id": config_id,
        "output_scores": scores if scores is not None else [make_score()],
    }
    if legacy_split_fields:
        row["eval_set_filter_id"] = test_filter
        row["train_set_filter_id"] = train_filter
        return row
    row["splits"] = {
        name_: {"source": split_source, "filter_id": filter_id}
        for name_, filter_id in (
            ("test", test_filter),
            ("train", train_filter),
            ("val", val_filter),
        )
        if filter_id is not None
    }
    return row


def make_eval_config(config_id: str = "ec_1", **properties: Any) -> dict[str, Any]:
    """One `GET …/evals/{eid}/eval_config/{ecid}` body. `properties` is the
    only field setup reads; pass the live shape under test (`type="code_eval"`
    + `code=…`, `type="llm_judge"` + `prompt_template=…`, or the legacy
    g_eval's `eval_steps=[…]` with no `type` key at all)."""
    return {"id": config_id, "name": f"Config {config_id}", "properties": properties}


def make_spec(
    spec_id: str = "424242331708",
    eval_id: str = "424242966189",
    status: str = "active",
    priority: int = 1,
    definition: str = "## Core Requirement\nend replies with a handoff block",
    **properties: Any,
) -> dict[str, Any]:
    """One `GET …/specs` row, trimmed to what setup reads (verified against
    live specs on disk)."""
    return {
        "id": spec_id,
        "name": f"Spec {spec_id}",
        "eval_id": eval_id,
        "status": status,
        "priority": priority,
        "definition": definition,
        "properties": properties,
    }


def make_probe_run(
    *,
    tool_calls: int = 0,
    skill_reads: int = 0,
    reasoning: str = "",
    output: str = "{}",
    cost: float = 0.001,
) -> dict[str, Any]:
    """One `POST …/run` reply as `kiln_harness_tuner.probes.count_metric` reads it: an
    OpenAI-format trace with the requested tool calls / skill reads on the
    assistant message, the output string, and a usage cost."""
    calls = [
        {"id": f"call_{i}", "function": {"name": "probe_checklist", "arguments": "{}"}}
        for i in range(tool_calls)
    ] + [
        {"id": f"skill_{i}", "function": {"name": "skill", "arguments": "{}"}}
        for i in range(skill_reads)
    ]
    assistant: dict[str, Any] = {"role": "assistant", "content": output}
    if calls:
        assistant["tool_calls"] = calls
    if reasoning:
        assistant["reasoning_content"] = reasoning
    return {
        "id": "run_x",
        "trace": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        + [assistant],
        "output": {"output": output},
        "intermediate_outputs": {"reasoning": reasoning} if reasoning else None,
        "usage": {"cost": cost},
    }


def make_run_config(
    rc_id: str = "rc_1",
    created_at: str | None = "2026-07-30T12:00:00-04:00",
) -> dict[str, Any]:
    """One `GET …/tasks/{id}/run_configs` row, trimmed to what the setup
    phase reads. `created_at` is offset-aware on the live server (verified
    against a live project tree); `None` omits the key, the
    shape `_most_recent` must survive."""
    row: dict[str, Any] = {"id": rc_id, "name": f"Run config {rc_id}"}
    if created_at is not None:
        row["created_at"] = created_at
    return row


def make_run(
    item_id: str, cost: float = 0.0, eval_cost: float = 0.0, **scores: float
) -> dict[str, Any]:
    """One EvalRun score record, flat — the shape `Analyzer.stored_results`
    hands its callers. `FakeKiln` wraps it on the way out of `/results`, the
    way the live route does."""
    return {
        "dataset_id": item_id,
        "scores": dict(scores),
        "task_run_usage": {"cost": cost},
        "eval_usage": {"cost": eval_cost},
    }


def make_job_error(dataset_id: str, message: str) -> dict[str, Any]:
    """One `GET /api/jobs/{id}/errors` row EXACTLY as the live server serves
    it: free-text message, no structured kind."""
    return {
        "error_message": message,
        "dataset_id": dataset_id,
        "run_config_id": "rc_x",
    }


class FakeKiln:
    """Scripted stand-in for KilnClient.call.

    - `evals[eval_id]` — eval definitions served by GET.
    - `results[(eval_id, rc_id, split)]` — the stored EvalRun cache.
    - `pending[(eval_id, rc_id, split)]` — published into `results` when an
      eval job for that combo succeeds (simulates measurement).
    - `job_behaviors[(eval_id, rc_id, split)]` — per-invocation queue:
      "ok" (default) | "fail" | "errored_items" | "hang" | "http504" |
      "http422" (raised at job creation). "errored_items" publishes
      `partial_results[key]` (if scripted) into the results cache — the
      errored job stored SOME EvalRuns — and reports progress totals
      (`{success, error, total}`).
    - `job_errors[(eval_id, rc_id, split)]` — rows served by
      `GET /api/jobs/{id}/errors` for that combo's jobs (see
      `make_job_error`); `job_error_behaviors[key]` — per-GET queue,
      "http500" raises.
    - `job_progress[(eval_id, rc_id, split)]` — per-wait queue of progress
      dicts overriding the computed one (e.g. subset-scoped retry totals).
    - `results_behaviors[(eval_id, rc_id, split)]` — per-GET queue for the
      results route: "http500" raises, anything else serves normally.
    - `stats_responses` — FIFO queue; falls back to `stats_response`.
    """

    def __init__(self) -> None:
        # `GET /api/available_models`: provider entries, each
        # `{"provider_id", "provider_name", "models": [{"id", "name"}]}`.
        self.available_models: list[dict[str, Any]] = []
        # evals/specs/eval_configs are typed loosely (Any) for the same
        # reason task/run_configs below are: the setup phase's shape guards
        # are only testable by scripting a DRIFTED response.
        self.evals: Any = {}
        # `GET …/evals` wraps its rows since Kiln 38e07d4ff; the fake serves
        # that shape by default so the suite exercises what the live server
        # actually returns. Set False for the older bare-list server, which
        # kiln-harness-tuner still reads.
        self.evals_envelope: bool = True
        # `/results` rows are wrapped since Kiln split the trace off the score
        # record; False serves the older bare-EvalRun rows, which kiln-harness-tuner
        # still reads.
        self.results_wrapped: bool = True
        self.eval_load_error_count: int = 0
        # `eval_configs[config_id]` — served by
        # `GET …/evals/{eid}/eval_config/{ecid}`, the only config the setup
        # phase reads (the one `current_config_id` names).
        self.eval_configs: dict[str, Any] = {}
        self.specs: Any = []
        # The setup phase reads the task record (for
        # `default_run_config_id`) and its run configs (bare array, the shape
        # `/evals` already serves). No default key by default — a task that
        # never had one set.
        # Typed loosely (Any) so a test can script a DRIFTED response shape —
        # the setup phase's shape guards are only testable that way.
        self.task: Any = {"id": "t1", "name": "Fake task"}
        # Saved prompts by their bare id -> {"prompt", "chain_of_thought_...}.
        self.saved_prompts: dict[str, dict[str, Any]] = {}
        self.gen_prompt_fails = False  # the render route answers 500
        self.run_configs: Any = []
        # Code probes: `POST …/run_configs` appends a row here
        # (and to `run_configs` when it is a list) and returns it;
        # `POST …/run` records the body in `run_posts` and serves the next
        # `run_responses` entry ("http500" raises), or a default 3-message
        # trace with an empty-object output when the queue is empty.
        self.created_run_configs: list[dict[str, Any]] = []
        self.fail_run_config_create = False
        self.run_posts: list[dict[str, Any]] = []
        self.run_responses: list[Any] = []
        # When set, `POST …/run` answers with this function of the
        # body — the latency drives interleave arms, so a queue cannot say
        # which answer belongs to which arm.
        self.run_handler: Any = None
        # Drives through eval jobs (`kiln_harness_tuner.job_drives`):
        # `judge_free_jobs` — the server's OpenAPI schema shows the `judge`
        # field of `EvalJobParams`; False serves an older server's schema.
        # `drive_handler(rc_id, item_id, split)` — what a judge-free job's
        # drive returns: a task-run dict (stored under `task_runs`, served by
        # `GET …/runs/{id}`), or "error:<text>" (a job error row for the
        # item). `job_drives` records each judge-free (rc, item, split) in
        # the order the jobs ran.
        self.judge_free_jobs: bool = True
        self.drive_handler: Any = None
        self.task_runs: dict[str, dict[str, Any]] = {}
        self.job_drives: list[tuple[str, str, str]] = []
        self.code_tools: list[dict[str, Any]] = []
        self.skills: list[dict[str, Any]] = []
        self.code_trust_posts: int = 0
        self.results: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        self.pending: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        self.partial_results: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        self.job_behaviors: dict[tuple[str, str, str], list[str]] = {}
        self.job_errors: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        self.job_error_behaviors: dict[tuple[str, str, str], list[str]] = {}
        self.job_progress: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        # Per-wait queue of JobError objects stamped onto a "fail" behavior's
        # record (`{"error": "<summary>", "detail": …}` — the job-LEVEL
        # failure summary, distinct from the per-item error log above).
        self.job_record_errors: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        self.job_error_fetches: list[str] = []
        self.job_cancels: list[str] = []  # POST /api/jobs/{id}/cancel, in order
        self.job_progress: dict[str, dict[str, Any]] = {}  # GET /api/jobs/{id}
        self.results_behaviors: dict[tuple[str, str, str], list[str]] = {}
        # `stats_response` overrides BOTH operations when a test sets it;
        # left None, the route serves the real per-operation shape below.
        self.stats_response: dict[str, Any] | None = None
        # The REAL mcnemar_paired shape (live-verified): the p
        # lives in "p_exact" — no "p_value", no stderr key at all. The
        # server's own "significant" is deliberately contradictory here
        # (False beside p_exact=0.03): code must compute significance
        # and never read this field.
        self.mcnemar_response: dict[str, Any] = {
            "operation": "mcnemar_paired",
            "n_pairs": 8,
            "table": {"n11": 5, "n10": 0, "n01": 2, "n00": 1},
            "discordant_hurt_b": 0,
            "discordant_helped_c": 2,
            "p_a": 0.625,
            "p_b": 0.875,
            "p_a_pct": 62.5,
            "p_b_pct": 87.5,
            "delta": 0.25,
            "delta_pct": 25.0,
            "p_exact": 0.03,
            "chi2_cc": 0.5,
            "p_chi2_cc": 0.4795,
            "ci_method": "newcombe_paired",
            "ci_low": -0.0757,
            "ci_high": 0.6503,
            "ci_low_pct": -7.6,
            "ci_high_pct": 65.0,
            "significant": False,
            "confidence": 0.95,
            "pooling_warning": "…",
            "interpretation": "…",
        }
        # The REAL compare_paired shape, copied from the server source
        # (kiln_server/statistics_api.py `_op_compare_paired`):
        # the p is "wilcoxon_p" (None below five non-zero paired differences),
        # the CI is a paired bootstrap, and there is no "p_value". Serving the
        # mcnemar shape for this operation would leave the code's
        # p_value/p_exact read untested.
        self.compare_paired_response: dict[str, Any] = {
            "operation": "compare_paired",
            "n_pairs": 8,
            "n_pairs_used": 8,
            "mean_a": 3.0,
            "mean_b": 3.5,
            "mean_delta": 0.5,
            "method": "paired_bootstrap + wilcoxon_signed_rank",
            "ci_low": 0.125,
            "ci_high": 0.875,
            "wilcoxon_p": 0.0391,
            "wilcoxon_note": None,
            "significant": True,
            "confidence": 0.95,
            "interpretation": "…",
        }
        # Eval configs created through `create_eval_config`.
        # Their rows are keyed apart from the default config's: a key's rc
        # part reads `<rc>@<config>` (e.g. `("eval_1", "rc_base@ec_copy_1",
        # "train")`), so a test scripts a copy config's pending rows beside
        # the default config's stored ones.
        self.created_eval_configs: list[dict[str, Any]] = []
        self.extra_config_ids: set[str] = set()
        self.stats_responses: list[dict[str, Any]] = []
        self.stats_bodies: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str, dict | None, Any]] = []
        self.job_invocations: list[tuple[str, str, str]] = []
        self.results_queries: list[tuple[str, str, str, dict | None]] = []
        self.wait_requests: list[dict[str, Any]] = []
        self._jobs: dict[str, dict[str, Any]] = {}
        self._job_seq = 0
        # ---- memory store ----
        self.memories: dict[str, dict[str, Any]] = {}
        self.memory_posts: list[dict[str, Any]] = []
        self.memory_patches: list[tuple[str, dict[str, Any]]] = []
        self.memory_deletes: list[str] = []
        # per-memory-id queues of "http500" behaviors consumed on PATCH/DELETE
        self.memory_patch_behaviors: dict[str, list[str]] = {}
        self.memory_patch_retries: list[bool] = []
        self.memory_delete_behaviors: dict[str, list[str]] = {}
        # POSTs have no id to key on yet, so their queues are keyed by a TAG
        # in the posted body — scoping a failure to the record under test
        # (e.g. the `val` record's first-promotion CREATE) rather than to
        # whichever POST happens to land first.
        self.memory_post_behaviors: dict[str, list[str]] = {}
        self._memory_seq = 0
        # `GET /ping` health route (the live server's):
        # response served verbatim — scripts assert it equals "pong".
        self.ping_response: Any = "pong"

    # ---- memory helpers ----

    def add_memory(
        self,
        overview: str,
        scope: str,
        tags: list[str],
        content: str | None = None,
        memory_id: str | None = None,
    ) -> str:
        if memory_id is None:
            self._memory_seq += 1
            memory_id = f"mem_{self._memory_seq}"
        self.memories[memory_id] = {
            "id": memory_id,
            "overview": overview,
            "scope": scope,
            "tags": list(tags),
            "content": content,
        }
        return memory_id

    @staticmethod
    def _memory_summary_row(m: dict[str, Any]) -> dict[str, Any]:
        """A listing row EXACTLY as the live server serves it:
        a summary — `content_length`, never `content`."""
        return {
            "id": m["id"],
            "overview": m["overview"],
            "tags": list(m["tags"]),
            "scope": m["scope"],
            "content_length": len(m["content"] or ""),
            "created_at": "2026-07-30T00:00:00",
            "created_by": "fake",
        }

    @staticmethod
    def _memory_full_record(m: dict[str, Any]) -> dict[str, Any]:
        """A `/by_ids` row EXACTLY as the live server serves it:
        the full record, `content` present (null when empty)."""
        return {
            "v": 1,
            "id": m["id"],
            "path": f"/fake/memories/{m['id']}.kiln",
            "created_at": "2026-07-30T00:00:00",
            "created_by": "fake",
            "overview": m["overview"],
            "content": m["content"],
            "tags": list(m["tags"]),
            "scope": m["scope"],
            "model_type": "memory",
        }

    @staticmethod
    def _reject_multiline_overview(method: str, url_path: str, body: Any) -> None:
        """The API's overview contract ("New one-line summary. No newlines.")
        — enforced here so the suite catches any write path that lets an
        agent-authored string reach it uncollapsed. The expensive place to
        discover that is cold-start setup, after the describe fan-out has
        already been paid for."""
        overview = (body or {}).get("overview")
        if isinstance(overview, str) and "\n" in overview:
            raise KilnAPIError(
                method,
                url_path,
                422,
                {
                    "detail": [
                        {"type": "string_pattern_mismatch", "loc": ["body", "overview"]}
                    ]
                },
            )

    @staticmethod
    def _reject_over_cap_writes(method: str, url_path: str, body: Any) -> None:
        """The REAL write-side caps (enforced server-side): the server 422s
        any memory POST or PATCH whose overview exceeds 280 chars OR whose
        content exceeds 4000 — served here so the suite catches any future
        unclamped write path. The server validates the overview as well as
        the content on write."""
        detail = [
            {
                "type": "string_too_long",
                "loc": ["body", field],
                "msg": f"String should have at most {cap} characters",
                "ctx": {"max_length": str(cap)},
            }
            for field, cap in (("overview", 280), ("content", 4000))
            if isinstance((body or {}).get(field), str)
            and len((body or {})[field]) > cap
        ]
        if detail:
            raise KilnAPIError(method, url_path, 422, {"detail": detail})

    def _list_memories(self, query_params: dict | None) -> dict[str, Any]:
        params = query_params or {}
        scope = params.get("scope")
        tags = params.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        rows = [
            m
            for m in self.memories.values()
            if (scope is None or m["scope"] == scope)
            and all(t in m["tags"] for t in tags)
        ]
        offset = int(params.get("offset") or 0)
        limit = int(params.get("limit") or 50)
        page = rows[offset : offset + limit]
        # The real list envelope: summaries under
        # "listings" + matched/remaining paging — NEVER full records.
        return {
            "listings": [self._memory_summary_row(m) for m in page],
            "matched": len(rows),
            "remaining": max(0, len(rows) - offset - len(page)),
            "remaining_tag_counts": {},
        }

    async def call(
        self,
        method: str,
        url_path: str,
        query_params: dict[str, Any] | None = None,
        body: Any | None = None,
        *,
        timeout_secs: float | None = None,
        retries: bool = True,
    ) -> Any:
        self.calls.append((method, url_path, query_params, body))

        if url_path == "/ping":
            return self.ping_response
        if url_path == "/api/available_models":
            # The planner's `allowed_models` read.
            return list(self.available_models)
        if url_path == "/openapi.json":
            properties: dict[str, Any] = {"split": {}, "item_ids": {}}
            if self.judge_free_jobs:
                properties["judge"] = {"type": "boolean", "default": True}
            return {
                "components": {"schemas": {"EvalJobParams": {"properties": properties}}}
            }
        if url_path == "/api/jobs/evals/run":
            return self._create_job(body)
        if url_path == "/api/jobs/wait":
            self.wait_requests.append(
                {"body": body, "timeout_secs": timeout_secs, "retries": retries}
            )
            return await self._wait_job(body)
        if url_path == "/api/statistics":
            self.stats_bodies.append(body)
            if self.stats_responses:
                return self.stats_responses.pop(0)
            if self.stats_response is not None:
                return dict(self.stats_response)
            if (body or {}).get("operation") == "compare_paired":
                return dict(self.compare_paired_response)
            return dict(self.mcnemar_response)

        match = _JOB_GET_PATH.match(url_path)
        if match and method.upper() == "GET":
            # A running job's record, for the setup progress
            # lines; `job_progress` scripts it per job id.
            if match["jid"] not in self._jobs:
                raise KilnAPIError(method, url_path, 404, {"detail": "no such job"})
            return {
                "id": match["jid"],
                "status": "running",
                "progress": dict(self.job_progress.get(match["jid"], {})),
            }

        match = _JOB_CANCEL_PATH.match(url_path)
        if match and method.upper() == "POST":
            if match["jid"] not in self._jobs:
                raise KilnAPIError(method, url_path, 404, {"detail": "no such job"})
            self.job_cancels.append(match["jid"])
            return None

        match = _JOB_ERRORS_PATH.match(url_path)
        if match:
            job = self._jobs.get(match["jid"])
            if job is None:
                raise KilnAPIError(method, url_path, 404, {"detail": "no such job"})
            key = job["key"]
            self.job_error_fetches.append(match["jid"])
            if "drive_errors" in job:
                return [dict(row) for row in job["drive_errors"]]
            queue = self.job_error_behaviors.get(key)
            if queue and queue.pop(0) == "http500":
                raise KilnAPIError(method, url_path, 500, {"detail": "flaky errors"})
            return [dict(row) for row in self.job_errors.get(key, [])]

        match = _RESULTS_PATH.match(url_path)
        if match:
            key = (
                match["eid"],
                self._rc_key(match["rcid"], match["ecid"]),
                (query_params or {}).get("split", "test"),
            )
            self.results_queries.append((*key, query_params))
            queue = self.results_behaviors.get(key)
            if queue and queue.pop(0) == "http500":
                raise KilnAPIError(method, url_path, 500, {"detail": "flaky results"})
            rows = list(self.results.get(key, []))
            if self.results_wrapped:
                # The live route wraps each score record and resolves the trace
                # (and its input/output/usage) onto the wrapper.
                rows = [
                    {
                        "eval_run": row,
                        "input": row.get("input"),
                        "output": row.get("output"),
                        "task_run_trace": row.get("task_run_trace"),
                        "task_run_usage": row.get("task_run_usage"),
                    }
                    for row in rows
                ]
            return {"results": rows}

        match = _CREATE_EVAL_CONFIG_PATH.match(url_path)
        if match and method.upper() == "POST":
            config_id = f"ec_copy_{len(self.created_eval_configs) + 1}"
            row = {
                "id": config_id,
                "name": (body or {}).get("name"),
                "config_type": (body or {}).get("type"),
                "properties": (body or {}).get("properties"),
                "model_name": (body or {}).get("model_name"),
                "model_provider": (body or {}).get("provider"),
                "eval_id": match["eid"],
                "provenance": (body or {}).get("provenance"),
            }
            self.created_eval_configs.append(row)
            self.extra_config_ids.add(config_id)
            self.eval_configs[config_id] = dict(row)
            return dict(row)

        match = _EVAL_CONFIG_PATH.match(url_path)
        if match:
            if match["ecid"] not in self.eval_configs:
                raise KilnAPIError(
                    method, url_path, 404, {"detail": "no such eval config"}
                )
            return self.eval_configs[match["ecid"]]

        match = _EVAL_PATH.match(url_path)
        if match:
            eval_def = self.evals.get(match["eid"])
            if eval_def is None:
                raise KilnAPIError(method, url_path, 404, {"detail": "no such eval"})
            return eval_def

        if _EVALS_LIST_PATH.match(url_path):
            # A drifted shape a test scripted (a str/dict body) is served
            # verbatim — that's the point of scripting it.
            if not isinstance(self.evals, dict):
                return self.evals
            rows = list(self.evals.values())
            if not self.evals_envelope:
                return rows
            return {"evals": rows, "load_error_count": self.eval_load_error_count}
        if _SPECS_LIST_PATH.match(url_path):
            return list(self.specs) if isinstance(self.specs, list) else self.specs
        if _RUN_CONFIGS_PATH.match(url_path) and method.upper() == "POST":
            # The real server refuses a name its own clean-up would change;
            # so does this one.
            name = (body or {}).get("name")
            if not isinstance(name, str) or not _kiln_name_ok(name):
                raise KilnAPIError(
                    method, url_path, 422, {"message": f"Name is invalid: {name!r}"}
                )
            if self.fail_run_config_create:
                raise KilnAPIError(method, url_path, 500, {"message": "boom"})
            row = {
                "id": f"rc_probe_{len(self.created_run_configs) + 1}",
                "name": (body or {}).get("name"),
                "description": (body or {}).get("description"),
                "run_config_properties": (body or {}).get("run_config_properties"),
                "provenance": (body or {}).get("provenance"),
            }
            self.created_run_configs.append(row)
            if isinstance(self.run_configs, list):
                self.run_configs.append(dict(row))
            return dict(row)
        match = _TASK_RUN_PATH.match(url_path)
        if match and method.upper() == "GET":
            run = self.task_runs.get(match["rid"])
            if run is None:
                raise KilnAPIError(method, url_path, 404, {"detail": "Run not found"})
            return dict(run)
        if _RUN_PATH.match(url_path) and method.upper() == "POST":
            self.run_posts.append(dict(body or {}))
            if self.run_handler is not None:
                return self.run_handler(dict(body or {}))
            if self.run_responses:
                response = self.run_responses.pop(0)
                if response == "http500":
                    raise KilnAPIError(method, url_path, 500, {"detail": "drive 500"})
                return response
            return make_probe_run()
        if _CODE_TOOLS_PATH.match(url_path):
            if method.upper() == "POST":
                row = {**(body or {}), "id": f"tool_{len(self.code_tools) + 1}"}
                self.code_tools.append(row)
                return dict(row)
            return [dict(r) for r in self.code_tools]
        if _SKILLS_PATH.match(url_path):
            if method.upper() == "POST":
                row = {**(body or {}), "id": f"skill_{len(self.skills) + 1}"}
                self.skills.append(row)
                return dict(row)
            return [dict(r) for r in self.skills]
        if _CODE_TRUST_PATH.match(url_path) and method.upper() == "POST":
            self.code_trust_posts += 1
            return {"trusted": True}
        if _RUN_CONFIGS_PATH.match(url_path):
            # Rows are copied so a caller can't mutate fake state; a drifted
            # shape a test scripted (None, an envelope dict, a str body) is
            # served verbatim — that's the point of scripting it.
            if isinstance(self.run_configs, list):
                rows = [
                    dict(rc) if isinstance(rc, dict) else rc for rc in self.run_configs
                ]
                # A real server lists every run config it holds rows for:
                # a scripted list names the ones a test
                # shapes; any other rc with stored or pending rows is listed
                # bare, as a run config with no instructions of its own.
                listed = {str(r.get("id")) for r in rows if isinstance(r, dict)}
                held = {
                    str(rc)
                    for (_e, rc, _split) in [*self.results, *self.pending]
                    if str(rc) not in listed
                }
                rows += [{"id": rc, "name": rc} for rc in sorted(held)]
                return rows
            return self.run_configs
        if _PROMPTS_PATH.match(url_path) and method.upper() == "GET":
            return {
                "generators": [{"id": "simple_prompt_builder", "name": "Basic"}],
                "prompts": [
                    {"id": f"id::{pid}", "name": pid, "type": "Custom", **row}
                    for pid, row in self.saved_prompts.items()
                ],
            }
        if (m := _GEN_PROMPT_PATH.match(url_path)) and method.upper() == "GET":
            if self.gen_prompt_fails:
                raise KilnAPIError(method, url_path, 500, {"detail": "render failed"})
            prompt_id = m["pid"]
            if prompt_id.startswith("id::") and prompt_id[4:] in self.saved_prompts:
                row = self.saved_prompts[prompt_id[4:]]
                return {"prompt_id": prompt_id, **row}
            if prompt_id == "simple_prompt_builder":
                task = self.task if isinstance(self.task, dict) else {}
                return {
                    "prompt_id": prompt_id,
                    "prompt": str(task.get("instruction") or ""),
                    "chain_of_thought_instructions": None,
                }
            raise KilnAPIError(method, url_path, 400, {"detail": "unknown prompt id"})
        if _TASK_PATH.match(url_path):
            return dict(self.task) if isinstance(self.task, dict) else self.task

        if _MEMORIES_PATH.match(url_path):
            if method.upper() == "POST":
                lost_reply = False
                for tag in (body or {}).get("tags") or []:
                    queue = self.memory_post_behaviors.get(tag)
                    behavior = queue.pop(0) if queue else None
                    if behavior == "http500":
                        raise KilnAPIError(
                            method, url_path, 500, {"detail": "flaky post"}
                        )
                    if behavior == "lost_reply":
                        # The write LANDS, the reply does not (read timeout,
                        # proxy reset) — the case where a replayed POST would fork it.
                        lost_reply = True
                self._reject_multiline_overview(method, url_path, body)
                self._reject_over_cap_writes(method, url_path, body)
                self.memory_posts.append(body)
                memory_id = self.add_memory(
                    overview=body["overview"],
                    scope=body["scope"],
                    tags=body.get("tags") or [],
                    content=body.get("content"),
                )
                if lost_reply:
                    raise KilnTransportError("POST reply lost after the write landed")
                return self._memory_full_record(self.memories[memory_id])
            return self._list_memories(query_params)
        if _MEMORIES_BY_IDS_PATH.match(url_path):
            ids = (query_params or {}).get("ids") or []
            if isinstance(ids, str):
                ids = [ids]
            # Bare array of full records.
            return [
                self._memory_full_record(self.memories[i])
                for i in ids
                if i in self.memories
            ]
        match = _MEMORY_PATH.match(url_path)
        if match and method.upper() == "DELETE":
            # Hard delete (memory skill: `DELETE .../memories/{id}`).
            memory_id = match["mid"]
            queue = self.memory_delete_behaviors.get(memory_id)
            if queue and queue.pop(0) == "http500":
                raise KilnAPIError(method, url_path, 500, {"detail": "flaky delete"})
            if memory_id not in self.memories:
                raise KilnAPIError(method, url_path, 404, {"detail": "no such memory"})
            self.memory_deletes.append(memory_id)
            del self.memories[memory_id]
            return None
        if match and method.upper() == "PATCH":
            memory_id = match["mid"]
            queue = self.memory_patch_behaviors.get(memory_id)
            if queue and queue.pop(0) == "http500":
                raise KilnAPIError(method, url_path, 500, {"detail": "flaky write"})
            if memory_id not in self.memories:
                raise KilnAPIError(method, url_path, 404, {"detail": "no such memory"})
            self._reject_multiline_overview(method, url_path, body)
            self._reject_over_cap_writes(method, url_path, body)
            self.memory_patches.append((memory_id, body))
            self.memory_patch_retries.append(retries)
            for key, value in (body or {}).items():
                self.memories[memory_id][key] = value
            return self._memory_full_record(self.memories[memory_id])

        raise KilnAPIError(method, url_path, 404, {"detail": "unrouted path"})

    def _rc_key(self, rc_id: str, config_id: str | None) -> str:
        """The rc part of a results key: `<rc>@<config>` for a created,
        non-default eval config, the bare rc otherwise."""
        if config_id and config_id in self.extra_config_ids:
            return f"{rc_id}@{config_id}"
        return rc_id

    def _create_job(self, body: dict[str, Any]) -> dict[str, Any]:
        key = (
            body["eval_id"],
            self._rc_key(body["run_config_id"], body.get("eval_config_id")),
            body["split"],
        )
        self.job_invocations.append(key)
        if body.get("judge") is False:
            # A judge-free job takes no scripted behavior: those
            # belong to the judged jobs of the same key.
            if not self.judge_free_jobs:
                raise KilnAPIError(
                    "POST", "/api/jobs/evals/run", 422, {"detail": "extra field"}
                )
            self._job_seq += 1
            job_id = f"job_{self._job_seq}"
            self._jobs[job_id] = {"key": key, "behavior": "drive", "body": dict(body)}
            return {"job_id": job_id, "status": "pending"}
        queue = self.job_behaviors.get(key)
        behavior = queue.pop(0) if queue else "ok"
        if behavior == "http422":
            raise KilnAPIError(
                "POST",
                "/api/jobs/evals/run",
                422,
                {"detail": "split not set on eval"},
            )
        self._job_seq += 1
        job_id = f"job_{self._job_seq}"
        self._jobs[job_id] = {
            "key": key,
            "behavior": behavior,
            # A trial job names the split items it wants; the
            # fake publishes only those pending rows (the rest stay pending
            # for a later full job, as the live server would skip them).
            "item_ids": body.get("item_ids"),
        }
        return {"job_id": job_id, "status": "pending"}

    async def _wait_job(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        job = self._jobs[body["ids"][0]]
        behavior = job["behavior"]
        if behavior == "hang":
            await asyncio.Event().wait()  # forever — only wait_for cancels it
        if behavior == "http504":
            raise KilnAPIError("POST", "/api/jobs/wait", 504, {"detail": "timeout"})
        if behavior == "drive":
            return [{"id": body["ids"][0], **self._drive_job(job)}]
        key = job["key"]
        if behavior == "fail":
            record: dict[str, Any] = {"status": "failed", "progress": {"error": 0}}
            record_error_queue = self.job_record_errors.get(key)
            if record_error_queue:
                record["error"] = record_error_queue.pop(0)
        elif behavior == "errored_items":
            # An errored job stored SOME EvalRuns — publish the scripted
            # partial rows; the record carries progress totals (the live
            # record's `error` count matches the errors-route rows).
            published = self.partial_results.get(key)
            if published is not None:
                self.results[key] = list(published)
            errored = len(self.job_errors.get(key, [])) or 2
            success = len(published or [])
            record = {
                "status": "succeeded",
                "progress": {
                    "success": success,
                    "error": errored,
                    "total": success + errored,
                },
            }
        else:  # "ok"
            item_ids = job.get("item_ids")
            # Like the live runner: a scored item is skipped, never stored
            # twice; a job that names item_ids takes only those pending rows.
            stored = {r.get("dataset_id") for r in self.results.get(key, [])}
            if key in self.pending and item_ids:
                wanted = set(item_ids)
                taken = [
                    r
                    for r in self.pending[key]
                    if r.get("dataset_id") in wanted
                    and r.get("dataset_id") not in stored
                ]
                self.pending[key] = [
                    r for r in self.pending[key] if r.get("dataset_id") not in wanted
                ]
                self.results[key] = [*self.results.get(key, []), *taken]
                record = {
                    "status": "succeeded",
                    "progress": {
                        "success": len(taken),
                        "error": 0,
                        "total": len(taken),
                    },
                }
            else:
                if key in self.pending:
                    fresh = [
                        r
                        for r in self.pending.pop(key)
                        if r.get("dataset_id") not in stored
                    ]
                    self.results[key] = [*self.results.get(key, []), *fresh]
                record = {"status": "succeeded", "progress": {"error": 0}}
        progress_queue = self.job_progress.get(key)
        if progress_queue:
            record["progress"] = progress_queue.pop(0)
        return [{"id": body["ids"][0], **record}]

    def _drive_job(self, job: dict[str, Any]) -> dict[str, Any]:
        """A judge-free job: each named item is driven by `drive_handler`,
        never judged; the record's result names the task runs."""
        body = job["body"]
        driven: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for item_id in body.get("item_ids") or []:
            rc_id, split = body["run_config_id"], body["split"]
            self.job_drives.append((rc_id, item_id, split))
            run = (
                self.drive_handler(rc_id, item_id, split)
                if self.drive_handler is not None
                else make_probe_run()
            )
            if isinstance(run, str) and run.startswith("error:"):
                errors.append(make_job_error(item_id, run.removeprefix("error:")))
                continue
            run = dict(run)
            run["id"] = f"tr_{len(self.task_runs) + 1}"
            self.task_runs[run["id"]] = run
            driven.append(
                {
                    "item_source": "eval_input",
                    "item_id": item_id,
                    "task_run_id": run["id"],
                }
            )
        job["drive_errors"] = errors
        return {
            "status": "succeeded",
            "progress": {
                "success": len(driven),
                "error": len(errors),
                "total": len(driven) + len(errors),
            },
            "result": {
                "total": len(driven) + len(errors),
                "success": len(driven),
                "error": len(errors),
                "driven_runs": driven,
            },
        }
