"""The model preflight (`kiln_harness_tuner.model_preflight`): one test
call per model the run may use, the context-window check, the retry, the
flag and its exclusion from screening, and the refused start. Offline:
FakeKiln only."""

from __future__ import annotations

import pytest
from conftest import make_ctx, make_plan, make_tracker
from fake_dispatcher import ok
from fake_kiln import make_probe_run
from test_orchestrator import seed_active_eval, seed_baseline
from test_speed_objective import _project

from kiln_harness_tuner.analysis import ModelPolicyRefused
from kiln_harness_tuner.kiln import KilnAPIError, KilnTransportError
from kiln_harness_tuner.model_policy import ModelPolicy
from kiln_harness_tuner.model_preflight import (
    MAX_CHECKED_MODELS,
    MODEL_PREFLIGHT_RUN_TAG,
    TEST_PROMPT_ID,
    ModelPreflightError,
    models_to_check,
    run_model_preflight,
)
from kiln_harness_tuner.orchestrator import _allowed_models, orchestrate, require_policy
from kiln_harness_tuner.report import render_model_preflight_appendix
from kiln_harness_tuner.schemas import ScreenCandidate, lane_for_source
from kiln_harness_tuner.viability import _make_candidate_rc

RUN_PATH = "/api/projects/p1/tasks/t1/run"
BASE = ("openai", "base_model")
SMALL = ("fireworks_ai", "small_model")
LARGE = ("together_ai", "large_model")
POLICY = {
    "allow": [
        {"provider": "openai", "model": "*"},
        {"provider": "fireworks_ai", "model": "small_model"},
        {"provider": "together_ai", "model": "large_model"},
    ]
}


def _offered(*rows: tuple[tuple[str, str], dict]) -> list[dict]:
    by_provider: dict[str, list[dict]] = {}
    for (provider, model), extra in rows:
        by_provider.setdefault(provider, []).append(
            {"id": model, "name": model, **extra}
        )
    return [
        {"provider_id": p, "provider_name": p, "models": models}
        for p, models in by_provider.items()
    ]


def _ctx(tmp_path, *, small: dict | None = None, policy: dict | None = POLICY):
    ctx = make_ctx(gate_agent="auto")
    ctx.target = ctx.target.model_copy(
        update={"project_path": str(_project(tmp_path, train_n=3))}
    )
    ctx.model_policy = ModelPolicy.from_json(policy) if policy else None
    ctx.kiln.saved_prompts["p1"] = {"prompt": "x" * 4000}  # about 1000 tokens
    ctx.kiln.run_configs = [
        {
            "id": "rc_base",
            "name": "rc_base",
            "run_config_properties": {
                "type": "kiln_agent",
                "model_provider_name": BASE[0],
                "model_name": BASE[1],
                "prompt_id": "id::p1",
                "structured_output_mode": "json_schema",
                "thinking_level": "high",
                "tools_config": {"tools": ["kiln_tool::code::tool_1"]},
            },
        }
    ]
    ctx.kiln.available_models = _offered(
        (BASE, {"available_thinking_levels": {"low": "low", "high": "high"}}),
        (SMALL, small or {}),
        (LARGE, {}),
    )
    return ctx


def _handler(fails: dict[str, list]):
    """`POST …/run` by model: each model's queue of failures (an exception
    to raise) is served first, then a good run."""

    def handle(body: dict) -> dict:
        queue = fails.get(body["run_config_properties"]["model_name"], [])
        if queue:
            raise queue.pop(0)
        return make_probe_run(cost=0.001)

    return handle


# A failed run as Kiln answers it: HTTP 500 with an `ErrorWithTrace` body,
# whose `message` is `format_error_message`'s text and whose `error_type` is
# the original exception's class name (kiln_ai/adapters/errors.py,
# kiln_server/custom_errors.py).
UNAVAILABLE = "The model provider is currently unavailable. Try again in a moment."
TOO_SLOW = "The model provider took too long to respond. Try again in a moment."
NO_CONNECTION = (
    "Could not connect to the model provider. Check your network connection."
)
RATE_LIMITED = "Rate limit exceeded. Wait a moment and try again."
GENERIC = "An unexpected error occurred."


def _run_error(message: str, error_type: str) -> KilnAPIError:
    body = {"message": message, "error_type": error_type, "trace": None}
    return KilnAPIError("POST", RUN_PATH, 500, body)


def _no_deployment() -> KilnAPIError:
    # A provider's 400 ("no deployments") reaches the client only this way.
    return _run_error(GENERIC, "BadRequestError")


def _posts_for(ctx, model: str) -> list[dict]:
    return [
        b
        for b in ctx.kiln.run_posts
        if b["run_config_properties"]["model_name"] == model
    ]


async def test_every_model_passes_with_one_cheap_call_each(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.kiln.run_handler = _handler({})
    result = await run_model_preflight(ctx, "rc_base")
    assert [(c.provider, c.model) for c in result.checks] == [BASE, SMALL, LARGE]
    assert not result.flagged() and ctx.flagged_models == {}
    assert result.estimated_prompt_tokens == 1000
    # The cheapest call: the task's simple prompt, no tools, the lowest level
    # the server offers, the shortest train item, and the preflight's tag.
    (base,) = _posts_for(ctx, "base_model")
    props = base["run_config_properties"]
    assert props["prompt_id"] == TEST_PROMPT_ID
    assert "tools_config" not in props
    assert props["thinking_level"] == "low"
    assert props["structured_output_mode"] == "json_schema"
    assert base["plaintext_input"] == "train input 0"
    assert base["tags"] == [MODEL_PREFLIGHT_RUN_TAG]
    # A model with no levels runs at its provider default.
    (small,) = _posts_for(ctx, "small_model")
    assert "thinking_level" not in small["run_config_properties"]
    # The spend is booked on the provider lane.
    (spend,) = [f for k, f in ctx.log.events if k == "spend"]
    assert spend["source"] == "model_preflight"
    assert spend["cost_usd"] == pytest.approx(0.003)
    assert lane_for_source("model_preflight") == "provider"
    (done,) = [f for k, f in ctx.log.events if k == "model_preflight"]
    assert done["line"].startswith("model preflight: 3 model(s) checked, 0 flagged")


async def test_a_failed_model_is_flagged_and_left_out_of_screening(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.kiln.run_handler = _handler({"small_model": [_no_deployment()]})
    tracker = make_tracker()
    result = await run_model_preflight(ctx, "rc_base")
    for kind, fields in ctx.log.events:
        tracker.event(kind, **fields)
    (flag,) = result.flagged()
    assert (flag.provider, flag.model, flag.attempts) == (*SMALL, 1)
    # The error type follows Kiln's generic message, so the reason says what
    # went wrong.
    assert flag.reason == f"the test call failed: {GENERIC} (BadRequestError)"
    assert ctx.flagged_models == {SMALL: flag.reason}
    # Logged, and in RunStatus for the report.
    (line,) = [f["line"] for k, f in ctx.log.events if k == "model_preflight_flag"]
    assert "fireworks_ai/small_model" in line and "BadRequestError" in line
    status = tracker.snapshot()
    assert status.model_preflight is not None
    assert [c.model for c in status.model_preflight.flagged()] == ["small_model"]
    assert "flagged: the test call failed" in render_model_preflight_appendix(status)
    # Out of the planner's list, and refused by the screen — as an error,
    # not a dead end (nothing goes to memory).
    assert [
        (r["provider"], r["model"]) for r in await _allowed_models(ctx, "rc_base")
    ] == [
        BASE,
        LARGE,
    ]
    swap = {"model_provider_name": SMALL[0], "model_name": SMALL[1]}
    cand = ScreenCandidate(id="s1", overrides=swap)
    rc_id, reason, dead = await _make_candidate_rc(ctx, cand, "rc_base")
    assert rc_id is None and dead is False
    assert reason.startswith("the model preflight flagged its model")
    assert ctx.kiln.created_run_configs == []


@pytest.mark.parametrize(
    "error",
    [
        _run_error(UNAVAILABLE, "ServiceUnavailableError"),
        _run_error(UNAVAILABLE, "InternalServerError"),
        _run_error(UNAVAILABLE, "BadGatewayError"),
        _run_error(TOO_SLOW, "Timeout"),
        _run_error(NO_CONNECTION, "APIConnectionError"),
        _run_error(RATE_LIMITED, "RateLimitError"),
        # A server older than `error_type`: Kiln's sentence alone.
        KilnAPIError("POST", RUN_PATH, 500, {"message": UNAVAILABLE}),
        KilnAPIError("POST", RUN_PATH, 500, {"message": TOO_SLOW}),
        KilnAPIError("POST", RUN_PATH, 500, {"message": NO_CONNECTION}),
        KilnTransportError("timed out"),
    ],
)
async def test_a_transient_failure_gets_one_retry(tmp_path, error):
    ctx = _ctx(tmp_path)
    ctx.kiln.run_handler = _handler({"small_model": [error]})
    result = await run_model_preflight(ctx, "rc_base")
    check = {c.model: c for c in result.checks}["small_model"]
    assert (check.outcome, check.attempts) == ("ok", 2)
    assert len(_posts_for(ctx, "small_model")) == 2
    assert ctx.sleeps == [5.0]


async def test_still_transient_after_the_retry_is_untested_not_flagged(tmp_path):
    ctx = _ctx(tmp_path)
    busy = _run_error(UNAVAILABLE, "ServiceUnavailableError")
    ctx.kiln.run_handler = _handler(
        {
            "large_model": [busy, busy],
            # On the baseline's own model too: a warning, never a refusal.
            "base_model": [KilnTransportError("timed out"), KilnTransportError("x")],
        }
    )
    result = await run_model_preflight(ctx, "rc_base")
    assert result.flagged() == [] and ctx.flagged_models == {}
    by_model = {c.model: c for c in result.inconclusive()}
    assert set(by_model) == {"large_model", "base_model"}
    large = by_model["large_model"]
    assert large.attempts == 2  # never a third call
    assert large.reason == (
        f"still failing after one retry: {UNAVAILABLE} (ServiceUnavailableError)"
    )
    lines = [f["line"] for k, f in ctx.log.events if k == "model_preflight_untested"]
    assert len(lines) == 2 and all("untested, still failing" in x for x in lines)
    (done,) = [f for k, f in ctx.log.events if k == "model_preflight"]
    assert "0 flagged, 2 untested" in done["line"]
    tracker = make_tracker()
    for kind, fields in ctx.log.events:
        tracker.event(kind, **fields)
    appendix = render_model_preflight_appendix(tracker.snapshot())
    assert "| untested: still failing after one retry" in appendix
    # Nothing is excluded.
    assert LARGE in [
        (r["provider"], r["model"]) for r in await _allowed_models(ctx, "rc_base")
    ]


@pytest.mark.parametrize(
    ("error", "why"),
    [
        # The model answered: the provider serves it.
        (
            _run_error(
                "Expecting value: line 1 column 1 (char 0)",
                "StructuredOutputParseError",
            ),
            "the provider answered",
        ),
        (
            _run_error(
                "The model's output didn't match the expected format.",
                "JSONSchemaValidationError",
            ),
            "the provider answered",
        ),
        # Kiln's own refusal of the request (no error type): not the model's.
        (
            KilnAPIError(
                "POST",
                RUN_PATH,
                400,
                {"message": "No input provided. Ensure you provided the proper format"},
            ),
            "names neither",
        ),
        # A structured output mode the model does not support.
        (
            _run_error(
                "litellm.UnsupportedParamsError: response_format is not supported",
                "UnsupportedParamsError",
            ),
            "names neither",
        ),
    ],
)
async def test_a_failure_that_proves_nothing_leaves_the_model_untested(
    tmp_path, error, why
):
    ctx = _ctx(tmp_path)
    ctx.kiln.run_handler = _handler({"base_model": [error]})
    result = await run_model_preflight(ctx, "rc_base")  # no refusal
    (check,) = result.inconclusive()
    assert check.model == "base_model" and check.attempts == 1
    assert why in (check.reason or "")
    assert result.flagged() == [] and ctx.flagged_models == {}


@pytest.mark.parametrize(
    "error",
    [
        _run_error(GENERIC, "BadRequestError"),
        _run_error(GENERIC, "NotFoundError"),
        _run_error(GENERIC, "ContextWindowExceededError"),
        _run_error(
            "Authentication with the model provider failed. Check your API key.",
            "AuthenticationError",
        ),
        # A server older than `error_type`, with the provider's own text.
        KilnAPIError("POST", RUN_PATH, 500, {"message": "400 no deployments"}),
    ],
)
async def test_a_hard_failure_flags_the_model(tmp_path, error):
    ctx = _ctx(tmp_path)
    ctx.kiln.run_handler = _handler({"small_model": [error]})
    result = await run_model_preflight(ctx, "rc_base")
    (flag,) = result.flagged()
    assert flag.model == "small_model" and flag.attempts == 1
    assert SMALL in ctx.flagged_models


async def test_a_non_transient_failure_is_not_retried(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.kiln.run_handler = _handler({"small_model": [_no_deployment()]})
    await run_model_preflight(ctx, "rc_base")
    assert len(_posts_for(ctx, "small_model")) == 1
    assert ctx.sleeps == []


async def test_a_failed_baseline_model_refuses_the_start_with_a_fix_line(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.kiln.run_handler = _handler({"base_model": [_no_deployment()]})
    with pytest.raises(ModelPreflightError) as raised:
        await run_model_preflight(ctx, "rc_base")
    message = str(raised.value)
    assert "the baseline rc_base runs on openai/base_model" in message
    assert "BadRequestError" in message
    assert "\nfix: " in message and "--skip-model-preflight" in message


async def test_a_context_window_smaller_than_the_baseline_is_flagged(tmp_path):
    ctx = _ctx(tmp_path, small={"context_window": 500})
    ctx.kiln.run_handler = _handler({})
    result = await run_model_preflight(ctx, "rc_base")
    (flag,) = result.flagged()
    assert (flag.model, flag.context_window, flag.attempts) == ("small_model", 500, 0)
    assert "context window (500 tokens)" in (flag.reason or "")
    assert "about 1000 tokens" in (flag.reason or "")
    assert _posts_for(ctx, "small_model") == []  # no call on a flagged model
    assert SMALL in ctx.flagged_models


async def test_another_model_gets_the_output_mode_kiln_suggests_for_it(tmp_path):
    ctx = _ctx(tmp_path, small={"structured_output_mode": "json_instructions"})
    ctx.kiln.available_models[0]["models"][0]["structured_output_mode"] = "json_mode"
    ctx.kiln.run_handler = _handler({})
    await run_model_preflight(ctx, "rc_base")
    mode = {
        m: _posts_for(ctx, m)[0]["run_config_properties"]["structured_output_mode"]
        for m in ("base_model", "small_model", "large_model")
    }
    # The baseline keeps its own; a row with no mode keeps the baseline's.
    assert mode == {
        "base_model": "json_schema",
        "small_model": "json_instructions",
        "large_model": "json_schema",
    }


async def test_a_plaintext_task_gets_plaintext_even_for_json_text(tmp_path):
    ctx = _ctx(tmp_path)
    item = tmp_path / "proj" / "tasks" / "t1 - T" / "eval_inputs" / "train0"
    (item / "eval_input.kiln").write_text(
        '{"id": "train0", "tags": ["train"], "data": {"type": "single_turn",'
        ' "user_message": {"text": "{\\"a\\": 1}"}}}'
    )
    ctx.kiln.run_handler = _handler({})
    await run_model_preflight(ctx, "rc_base")  # the task has no input schema
    (base,) = _posts_for(ctx, "base_model")
    assert base["plaintext_input"] == '{"a": 1}'
    assert "structured_input" not in base
    # A task with an input schema gets the item that parses as JSON.
    ctx = _ctx(tmp_path / "s")
    ctx.kiln.task = {"id": "t1", "name": "T", "input_json_schema": '{"type": "object"}'}
    item = tmp_path / "s" / "proj" / "tasks" / "t1 - T" / "eval_inputs" / "train2"
    (item / "eval_input.kiln").write_text(
        '{"id": "train2", "tags": ["train"], "data": {"type": "single_turn",'
        ' "user_message": {"text": "{\\"b\\": 2}"}}}'
    )
    ctx.kiln.run_handler = _handler({})
    await run_model_preflight(ctx, "rc_base")
    (base,) = _posts_for(ctx, "base_model")
    assert base["structured_input"] == {"b": 2}
    assert "plaintext_input" not in base


async def test_measurement_refuses_an_rc_on_a_flagged_model(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.kiln.run_configs.append(
        {
            "id": "rc_agent",
            "name": "rc_agent",
            "run_config_properties": {
                "type": "kiln_agent",
                "model_provider_name": SMALL[0],
                "model_name": SMALL[1],
            },
        }
    )
    ctx.kiln.run_handler = _handler({"small_model": [_no_deployment()]})
    await run_model_preflight(ctx, "rc_base")
    with pytest.raises(ModelPolicyRefused) as raised:
        await require_policy(ctx, "rc_agent")
    assert "the model preflight flagged its model fireworks_ai/small_model" in str(
        raised.value
    )
    assert "BadRequestError" in str(raised.value)
    (event,) = [f for k, f in ctx.log.events if k == "model_policy_refused"]
    assert event["rc_id"] == "rc_agent"
    # The baseline chain stays exempt, and other models are measured.
    ctx.policy_exempt_rcs.add("rc_agent")
    await require_policy(ctx, "rc_agent")
    await require_policy(ctx, "rc_base")


def test_the_model_set():
    offered = _offered((BASE, {}), (SMALL, {}), (LARGE, {}))
    # No policy under quality: the planner gets no model list.
    assert models_to_check(BASE, None, "quality", offered) == ([BASE], 0)
    policy = ModelPolicy.from_json(POLICY)
    assert models_to_check(BASE, policy, "quality", offered) == (
        [BASE, SMALL, LARGE],
        0,
    )
    # More allowed models than are checked: the exact entries only.
    many = _offered(
        *(((("openai", f"m{i}"), {})) for i in range(MAX_CHECKED_MODELS + 1)),
        (SMALL, {}),
        (LARGE, {}),
    )
    pairs, untested = models_to_check(BASE, policy, "quality", many)
    assert pairs == [BASE, SMALL, LARGE]
    assert untested == MAX_CHECKED_MODELS + 1


async def test_the_run_goes_on_past_a_flagged_model(tmp_path):
    ctx = _ctx(tmp_path)
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    ctx.model_preflight = True
    ctx.kiln.run_handler = _handler({"large_model": [_no_deployment()]})
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    assert await orchestrate(ctx, "g", timeout_mins=180) == "done"
    (plan_input,) = ctx.dispatch.calls_to("batch_plan")
    assert LARGE not in [(r["provider"], r["model"]) for r in plan_input.allowed_models]
    (flagged,) = plan_input.flagged_models
    assert flagged.startswith("together_ai/large_model: the test call failed")


async def test_a_failed_baseline_model_stops_the_run_before_the_planner(tmp_path):
    ctx = _ctx(tmp_path)
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    ctx.model_preflight = True
    ctx.kiln.run_handler = _handler({"base_model": [_no_deployment()]})
    with pytest.raises(ModelPreflightError):
        await orchestrate(ctx, "g", timeout_mins=180)
    assert ctx.dispatch.calls_to("batch_plan") == []


async def test_the_preflight_is_off_without_the_flag(tmp_path):
    ctx = _ctx(tmp_path)
    seed_baseline(ctx.kiln)
    seed_active_eval(ctx.kiln)
    ctx.dispatch.queue("batch_plan", ok(make_plan(loop_exit="done")))
    assert await orchestrate(ctx, "g", timeout_mins=180) == "done"
    assert ctx.kiln.run_posts == []
    assert not [k for k, _f in ctx.log.events if k == "model_preflight"]


def test_the_cli_flag_turns_the_preflight_off(tmp_path):
    from test_cli import ARGS

    from kiln_harness_tuner.cli import build_ctx, build_parser, target_from_args
    from kiln_harness_tuner.kiln import KilnClient

    assert build_parser().parse_args(ARGS).skip_model_preflight is False
    args = build_parser().parse_args([*ARGS, "--skip-model-preflight"])
    assert args.skip_model_preflight is True
    run_dir = tmp_path / "runs" / "r"

    def ctx_with(**kwargs):
        return build_ctx(
            target_from_args(args),
            KilnClient("http://localhost:1"),
            run_id="r",
            run_dir=run_dir,
            mcp_config_path=run_dir / "mcp_config.json",
            settings_path=run_dir / "settings.json",
            **kwargs,
        )

    assert ctx_with().model_preflight is True
    assert (
        ctx_with(model_preflight=not args.skip_model_preflight).model_preflight is False
    )


async def test_without_a_policy_the_planner_gets_the_baselines_model(tmp_path):
    # Under `quality` with no policy the full list is not sent, but the list
    # is never empty: the planner plans arms only on listed models, and an
    # empty list read as "no arm at all" ended a run after one wave.
    ctx = _ctx(tmp_path, policy=None)
    rows = await _allowed_models(ctx, "rc_base")
    assert [(r["provider"], r["model"]) for r in rows] == [BASE]
    # A baseline that cannot be read leaves the list empty rather than failing.
    assert await _allowed_models(ctx, "rc_missing") == []
