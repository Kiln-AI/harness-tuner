"""A provider key at its monthly limit must stop the run, not be classed as a
generic error while spending goes on; a timing pass that returns too few
pairs must not count. These pin that against the error shapes the loop
actually sees."""

from __future__ import annotations

import pytest
from conftest import fix_item, make_ctx, make_plan
from fake_dispatcher import ok
from fake_kiln import make_eval, make_run
from test_orchestrator import rc_ok, seed_active_eval, seed_baseline, seed_issue
from test_speed_objective import _project, _rc

from kiln_harness_tuner.kiln import KilnAPIError
from kiln_harness_tuner.latency import (
    compare_latencies,
    paired_floor,
    sample_inputs,
    split_inputs,
    timed_drives,
)
from kiln_harness_tuner.orchestrator import orchestrate
from kiln_harness_tuner.provider_errors import (
    ProviderQuotaError,
    classify_provider_message,
    quota_stop_line,
)

# Verbatim, from the run's `GET /api/jobs/{id}/errors` rows (the key id is
# replaced): the only place the provider's text reaches the loop.
KEY_LIMIT = (
    'litellm.APIError: APIError: OpenrouterException - {"error":{"message":'
    '"Key limit exceeded (monthly limit). Manage it using '
    'https://openrouter.ai/workspaces/default/keys/0000","code":403}}'
)
# What `POST …/run` answers for the same failure: the provider text is only
# in the server's log.
RUN_500_BODY = {
    "message": "An unexpected error occurred.",
    "error_type": "APIError",
    "trace": None,
}


class TestClassifier:
    @pytest.mark.parametrize(
        "message",
        [
            KEY_LIMIT,
            '{"error":{"message":"This request requires more credits, or fewer'
            ' max_tokens.","code":402}}',
            "OpenrouterException - Insufficient credits",
            "litellm.RateLimitError: OpenAIException - You exceeded your current"
            " quota, please check your plan and billing details.",
            'Error code: 429 - {"error": {"type": "insufficient_quota"}}',
            "litellm.BadRequestError: AnthropicException - Your credit balance is"
            " too low to access the Anthropic API.",
            "429 RESOURCE_EXHAUSTED Quota exceeded for metric generativelanguage"
            ".googleapis.com/generate_requests_per_model_per_day",
            "HTTP 402 Payment Required",
        ],
    )
    def test_quota_and_billing_failures_are_terminal(self, message):
        assert classify_provider_message(message) == "quota_exhausted"

    @pytest.mark.parametrize(
        "message",
        [
            "litellm.RateLimitError: VertexAIException - 429 RESOURCE_EXHAUSTED"
            " Resource has been exhausted (e.g. check quota).",
            "Rate limit reached for model x (TPM): Limit 250000",
        ],
    )
    def test_throttling_is_not(self, message):
        assert classify_provider_message(message) == "rate_limit"

    def test_the_generic_run_500_is_not_classified_as_quota(self):
        assert classify_provider_message(str(RUN_500_BODY)) == "provider_error"

    def test_the_stop_line_names_the_provider_and_the_remedy(self):
        assert quota_stop_line("quota_exhausted", KEY_LIMIT) == (
            "stopped: OpenRouter key limit exceeded (monthly limit) — raise it"
            " on the OpenRouter dashboard"
        )
        line = quota_stop_line(
            "quota_exhausted",
            "AnthropicException - Your credit balance is too low to access the"
            " Anthropic API.",
        )
        assert line.startswith("stopped: Anthropic your credit balance is too low")


def _failing_drives(ctx, *, fail_rc: str | None = None, fail_items=None, times=99):
    """`POST …/run` 500s (the real body) for `fail_rc` (every rc when None)
    on `fail_items` (every item when None), `times` times per (rc, item)."""
    seen: dict[tuple[str, str], int] = {}

    def handler(body: dict) -> dict:
        rc = body.get("task_run_config_id")
        text = body.get("plaintext_input") or ""
        key = (rc, text)
        hit = (fail_rc is None or rc == fail_rc) and (
            fail_items is None or text in fail_items
        )
        if hit and seen.get(key, 0) < times:
            seen[key] = seen.get(key, 0) + 1
            raise KilnAPIError("POST", "/run", 500, RUN_500_BODY)
        return {
            "id": "r",
            "usage": {"total_llm_latency_ms": 1000.0 + len(text), "cost": 0.001},
            "output": {"output": "{}"},
        }

    ctx.kiln.run_handler = handler


class TestQuotaStopsTheRun:
    async def test_a_key_limit_seen_through_drives_stops_the_run(self, tmp_path):
        """The limit began on the timing drives, which see only a generic 500.
        One failed item is re-run as a one-item eval job, whose errors carry
        the provider's text: a quota there stops the run before any agent."""
        ctx = make_ctx(gate_agent="auto")
        ctx.objective = "speed"
        ctx.measure_baseline_first = True
        ctx.target = ctx.target.model_copy(
            update={"project_path": str(_project(tmp_path))}
        )
        seed_baseline(ctx.kiln)
        seed_active_eval(ctx.kiln)
        ctx.kiln.run_configs = [_rc("rc_base")]
        # Stored baseline rows cover the split: setup goes straight to timing.
        ctx.kiln.results[("eval_1", "rc_base", "train")] = [
            make_run(f"train{n}", accuracy=1.0) for n in range(8)
        ]
        _failing_drives(ctx)
        ctx.kiln.job_behaviors[("eval_1", "rc_base", "train")] = ["errored_items"]
        ctx.kiln.job_errors[("eval_1", "rc_base", "train")] = [
            {"dataset_id": "train0", "error_message": KEY_LIMIT}
        ]
        result = await orchestrate(ctx, "g", timeout_mins=180)
        assert result == "quota_exhausted"
        assert not ctx.dispatch.calls_to("batch_plan")
        (stop,) = [f for k, f in ctx.log.events if k == "provider_quota"]
        assert stop["line"] == (
            "stopped: OpenRouter key limit exceeded (monthly limit) — raise it"
            " on the OpenRouter dashboard"
        )
        # Each drive tried at most twice, the diagnosis job once: never a
        # retry ladder on a quota error.
        assert len(ctx.kiln.run_posts) <= 2 * 8
        assert len(ctx.kiln.job_invocations) == 1

    async def test_a_key_limit_in_an_arms_eval_job_stops_on_the_first_attempt(
        self, tmp_path
    ):
        ctx = make_ctx(gate_agent="auto")
        seed_baseline(ctx.kiln)
        seed_active_eval(ctx.kiln)
        seed_issue(ctx.kiln)
        ctx.kiln.results[("eval_1", "rc_base", "train")] = [
            make_run("t1", accuracy=0.0)
        ]
        ctx.kiln.job_behaviors[("eval_1", "rc_cand", "train")] = ["errored_items"] * 3
        ctx.kiln.job_errors[("eval_1", "rc_cand", "train")] = [
            {"dataset_id": "t1", "error_message": KEY_LIMIT}
        ]
        ctx.dispatch.queue(
            "batch_plan",
            ok(make_plan(fix_item("w1-1"), fix_item("w1-2"))),
            ok(make_plan(loop_exit="done")),
        )
        ctx.dispatch.queue("create_experiment_rc", rc_ok("rc_cand"), rc_ok("rc_2"))
        result = await orchestrate(ctx, "g", timeout_mins=180)
        assert result == "quota_exhausted"
        assert ctx.kiln.job_invocations.count(("eval_1", "rc_cand", "train")) == 1
        assert len(ctx.dispatch.calls_to("batch_plan")) == 1  # no second wave
        assert not ctx.dispatch.calls_to("analyze_experiment_results")

    async def test_a_transient_drive_failure_is_diagnosed_and_not_fatal(self, tmp_path):
        ctx = make_ctx()
        ctx.target = ctx.target.model_copy(
            update={"project_path": str(_project(tmp_path))}
        )
        seed_active_eval(ctx.kiln)
        ctx.kiln.run_configs = [_rc("rc_base")]
        inputs = sample_inputs(split_inputs(ctx.target.project_path, "t1", "train"))
        _failing_drives(ctx, fail_items={inputs[0].plaintext})
        ctx.kiln.job_behaviors[("eval_1", "rc_base", "train")] = ["errored_items"]
        ctx.kiln.job_errors[("eval_1", "rc_base", "train")] = [
            {"dataset_id": inputs[0].id, "error_message": "upstream timed out"}
        ]
        drives = await timed_drives(ctx, ["rc_base"], inputs, "x")
        reason = drives.failures["rc_base"][inputs[0].id]
        assert "HTTP 500: An unexpected error occurred. (APIError)" in reason
        assert "after one retry" in reason and "upstream timed out" in reason


class TestShortSamples:
    async def test_a_failed_pair_is_retried_once(self, tmp_path):
        ctx = make_ctx()
        ctx.target = ctx.target.model_copy(
            update={"project_path": str(_project(tmp_path))}
        )
        ctx.kiln.run_configs = [_rc("rc_base")]
        _failing_drives(ctx, times=1)  # every first attempt fails
        inputs = sample_inputs(split_inputs(ctx.target.project_path, "t1", "train"))
        drives = await timed_drives(ctx, ["rc_base"], inputs, "x")
        assert len(drives.latencies["rc_base"]) == len(inputs)
        assert not drives.failures["rc_base"]
        assert len(ctx.kiln.run_posts) == 2 * len(inputs)
        assert ctx.kiln.job_invocations == []  # nothing left to diagnose

    def test_the_floor_is_two_thirds_of_the_plan(self):
        assert paired_floor(30) == 20
        assert paired_floor(8) == 6  # never below MIN_PAIRS
        assert paired_floor(3) == 6

    def test_a_short_sample_is_incomplete_never_a_verdict(self):
        base = {f"i{n}": 2000.0 for n in range(30)}
        cand = {f"i{n}": 500.0 for n in range(8)}  # 8 of 30 paired
        dropped = {f"i{n}": "HTTP 500 (after one retry)" for n in range(8, 30)}
        c = compare_latencies(
            base,
            cand,
            method="paired_drive",
            split="train",
            planned=30,
            dropped=dropped,
        )
        assert c.incomplete and not c.clearly_faster
        assert c.note == "speed incomplete: 8 of 30 pairs (22 errored)"
        assert c.planned_items == 30 and len(c.dropped_items) == 22
        full = {f"i{n}": 500.0 for n in range(20)}
        c20 = compare_latencies(
            base, full, method="paired_drive", split="train", planned=30
        )
        assert not c20.incomplete and c20.clearly_faster

    async def test_an_incomplete_timing_pass_stops_the_arm(self, tmp_path):
        from test_speed_objective import _speed_ctx

        ctx = _speed_ctx(tmp_path, cand_ms=300.0)
        # The candidate's drives fail on 3 of the 8 items (twice each), so its
        # 8 timing pairs are 5 < floor 6: the engine stops it on `not_better`
        # with the incomplete note before its first chunk is judged.
        quick = {
            i.plaintext
            for i in sample_inputs(
                split_inputs(ctx.target.project_path, "t1", "train"), 5
            )
        }
        rest = {
            i.plaintext for i in split_inputs(ctx.target.project_path, "t1", "train")
        } - quick
        inner = ctx.kiln.run_handler

        def handler(body):
            if body.get("task_run_config_id") == "rc_cand" and (
                body.get("plaintext_input") in rest
            ):
                raise KilnAPIError("POST", "/run", 500, RUN_500_BODY)
            return inner(body)

        ctx.kiln.run_handler = handler
        await orchestrate(ctx, "go fast", timeout_mins=180)
        (m,) = [f for k, f in ctx.log.events if k == "measurement"]
        assert m["stop_kind"] == "not_better"
        assert "speed incomplete: 5 of 8 pairs (3 errored)" in m["line"]
        # Judged nothing (the rc_cand jobs are the one-item drive-failure
        # diagnoses, not judging).
        assert m["line"].startswith("w1-1: 0 judged")
        assert not ctx.dispatch.calls_to("analyze_experiment_results")

    def test_an_incomplete_result_cannot_make_an_arm_successful(self):
        from kiln_harness_tuner.analysis import success_by_speed_rule
        from kiln_harness_tuner.schemas import AnalysisBundle

        c = compare_latencies(
            {f"i{n}": 2000.0 for n in range(30)},
            {f"i{n}": 500.0 for n in range(8)},
            method="paired_drive",
            split="train",
            planned=30,
        )
        bundle = AnalysisBundle(per_eval=[], spend_total_usd=0.0, errors=[], latency=c)
        verdict = success_by_speed_rule(bundle, 0.95)
        assert not verdict.ok and any("speed incomplete" in r for r in verdict.reasons)

    def test_an_incomplete_result_cannot_make_a_candidate_promotable(self):
        from test_speed_objective import _row, _table

        from kiln_harness_tuner.orchestrator import apply_speed_eligibility

        table = _table("rc_a", None, _row(0.0))
        table.latency = compare_latencies(
            {f"i{n}": 2000.0 for n in range(30)},
            {f"i{n}": 500.0 for n in range(8)},
            method="paired_drive",
            split="val",
            planned=30,
        )
        apply_speed_eligibility(table)
        assert table.ineligible
        assert any("speed incomplete" in e.detail for e in table.errors)


async def test_the_diagnosis_raises_on_a_quota_row(tmp_path):
    ctx = make_ctx()
    ctx.kiln.evals["eval_1"] = make_eval("eval_1", "ec_1")
    ctx.kiln.job_behaviors[("eval_1", "rc_x", "train")] = ["errored_items"]
    ctx.kiln.job_errors[("eval_1", "rc_x", "train")] = [
        {"dataset_id": "train0", "error_message": KEY_LIMIT}
    ]
    project = _project(tmp_path)
    with pytest.raises(ProviderQuotaError):
        await ctx.analyzer.diagnose_drive_failure(str(project), "rc_x", "train0")
    # An item no train split holds cannot be diagnosed: None, no job.
    assert (
        await ctx.analyzer.diagnose_drive_failure(str(project), "rc_x", "nope") is None
    )


class TestEchoedModelOutputNeverClassifies:
    """Run 260924-2032 stopped as "the provider rejected the API key": a
    schema error quoted the model's whole answer, and the answer (a batch
    plan) said "Recurring unauthorized charges"."""

    SCHEMA_ERROR = (
        "drive of rc 424242100001 on 424242200001: HTTP 500: This task requires"
        " a specific output schema. While the model produced JSON, that JSON"
        " didn't meet the schema. Search 'Troubleshooting Structured Data"
        " Issues' in our docs for more information. The error from the schema"
        " check was: Additional properties are not allowed. The JSON was: \n"
        "```json\n{'prompts': ['Billing service thread, 8 messages. Recurring"
        " unauthorized charges reported.', 'Key limit exceeded (monthly limit)"
        " in the customer's own words', 'invalid api key shown on screen,"
        ' "code": 401\']}\n```'
    )

    def test_a_schema_error_is_malformed_output(self):
        from kiln_harness_tuner.provider_errors import classify_provider_message

        assert classify_provider_message(self.SCHEMA_ERROR) == "malformed_output"

    def test_nothing_inside_the_echo_is_read(self):
        from kiln_harness_tuner.provider_errors import error_text_without_model_output

        head = error_text_without_model_output(self.SCHEMA_ERROR)
        for word in ("unauthorized", "Key limit", "invalid api key", "401"):
            assert word not in head.replace("424242", "")

    def test_a_real_key_limit_still_stops(self):
        from kiln_harness_tuner.provider_errors import classify_provider_message

        real = (
            'litellm.APIError: APIError: OpenrouterException - {"error":'
            '{"message":"Key limit exceeded (monthly limit). Manage it using'
            ' https://openrouter.ai/workspaces/default/keys/x","code":403}}'
        )
        assert classify_provider_message(real) == "quota_exhausted"
        assert (
            classify_provider_message("AuthenticationError: invalid api key") == "auth"
        )
