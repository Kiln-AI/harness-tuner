"""The model preflight: one test call per model the run may
use, before any spend on the baseline measurement, the waves or the screens.

Origin: in one run a provider had no deployment for an allowed model, and
26 or more eval jobs failed with HTTP 400 "no deployments". Another provider
refused prompts longer than the model's context window (172k tokens against
131k). Four jobs got HTTP 503. Each failure cost spend and time that one
check at start-up would have saved.

**The models.** The baseline's own model first. Then the models the run
can move to: what the planner receives as `allowed_models` — the server's
`GET /api/available_models`, filtered by the run's policy. Under
`--models keep` the policy is the baseline's one pair, so only the
baseline's model is checked. When more than `MAX_CHECKED_MODELS` models are
allowed (a policy of wildcards over a large provider), only the pairs the
policy names exactly — an exception, or an allow rule with no wildcard — are
checked, and the rest are counted as untested.

**The test call.** `POST …/tasks/{task_id}/run`, the cheapest model call
Kiln has: Kiln has no connectivity check for a model. The call is a fork of
the baseline's properties on the model under check, with
`simple_prompt_builder` in place of the baseline's prompt, no tools, the
lowest thinking level the server offers for the model, and — for a model
other than the baseline's — the structured output mode Kiln's model list
suggests for it. The input is the shortest train item, sent as plaintext
when the task has no input schema. So one call costs one short prompt and
one short answer, and the preflight makes at most two calls per model. The
runs carry `MODEL_PREFLIGHT_RUN_TAG`, and their cost is booked as `spend`,
source `model_preflight`.

**The context check.** The baseline's prompt and its code-tool definitions,
in characters / 4 (no tokenizer), against the model's context window when
Kiln's model list gives one (`CONTEXT_WINDOW_KEYS`). A model whose window is
smaller is flagged, and gets no test call.

**The failure classes.** Kiln answers a failed model call with HTTP 500
and an `ErrorWithTrace` body: `message`, Kiln's `format_error_message` text
(a fixed sentence per litellm error class, "An unexpected error occurred."
for the rest), and `error_type`, the class name of the original exception.
`classify_failure` reads both:

- **transient** — a timeout, a connection failure, a 429 or a 5xx
  (`TRANSIENT_ERROR_TYPES`, or Kiln's sentences for them). One retry.
- **hard** — the failure names the model or the provider: a bad request,
  not found, no deployment, context too long, quota or auth
  (`HARD_ERROR_TYPES`, or the provider's own text).
- **output** — the model answered, and its output could not be read. That
  proves the provider serves the model.
- **unclear** — anything else, a structured output mode the model does not
  support included.

**The outcome.** Only a hard failure flags a model, and it is never silently
dropped: a log line, an entry in `RunStatus.model_preflight`, and an entry
in `Ctx.flagged_models`, which keeps it out of `allowed_models`, out of
every screening round and out of every measurement this run. A hard failure
on the baseline's own model stops the start (`ModelPreflightError`). Every
other failure — still transient after the retry, output, unclear — leaves
the model `untested`, with a warning and the reason, and excludes nothing.
`--skip-model-preflight` turns the whole check off.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from kiln_harness_tuner.job_drives import error_text
from kiln_harness_tuner.kiln import KilnAPIError, KilnError, KilnTransportError
from kiln_harness_tuner.latency import run_cost, split_inputs
from kiln_harness_tuner.model_policy import (
    MAX_CHECKED_MODELS,
    ModelPolicy,
    allowed_models,
)
from kiln_harness_tuner.probes import ProbeSetupError, TrainInput, rc_props
from kiln_harness_tuner.prompt_text import CODE_TOOL_DESCRIPTION, rc_texts
from kiln_harness_tuner.provider_errors import (
    classify_provider_message,
    error_text_without_model_output,
)
from kiln_harness_tuner.schemas import PROVIDER_LANE, ModelCheck, ModelPreflightResult
from kiln_harness_tuner.setup import SetupError

if TYPE_CHECKING:
    from kiln_harness_tuner.orchestrator import Ctx

logger = logging.getLogger(__name__)

MODEL_PREFLIGHT_RUN_TAG = "kiln_harness_tuner_model_preflight"
SPEND_SOURCE = "model_preflight"
# The prompt generator Kiln builds from the task's own instruction: shorter
# than a tuned prompt, and present on every task.
TEST_PROMPT_ID = "simple_prompt_builder"
MAX_CONCURRENT_CHECKS = 4
TEST_CALL_TIMEOUT_SECS = 180.0
RETRY_SLEEP_SECS = 5.0
CHARS_PER_TOKEN = 4
# The keys a model row may give its context window under, in tokens. Kiln's
# model list has none of them today; the check runs when a server adds one.
CONTEXT_WINDOW_KEYS = (
    "context_window",
    "context_length",
    "max_context_tokens",
    "max_input_tokens",
)
# The `error_type` values of a failed run (the litellm class names Kiln's
# `format_error_message` maps), by failure class (module doc).
TRANSIENT_ERROR_TYPES = frozenset(
    {
        "RateLimitError",
        "ServiceUnavailableError",
        "InternalServerError",
        "BadGatewayError",
        "Timeout",
        "APIConnectionError",
    }
)
HARD_ERROR_TYPES = frozenset(
    {
        "BadRequestError",
        "NotFoundError",
        "AuthenticationError",
        "PermissionDeniedError",
        "ContextWindowExceededError",
    }
)
OUTPUT_ERROR_TYPES = frozenset(
    {"StructuredOutputParseError", "JSONSchemaValidationError", "JSONDecodeError"}
)
# Kiln's own sentences for the transient classes, and the provider texts
# that come through a server older than `error_type`.
_TRANSIENT_RE = re.compile(
    r"rate limit exceeded|currently unavailable|took too long to respond"
    r"|could not connect to the model provider"
    r"|\b(?:429|502|503|504)\b|timed? ?out|overloaded|service unavailable"
    r"|temporarily unavailable|bad gateway",
    re.IGNORECASE,
)
_HARD_RE = re.compile(
    r"no deployments?\b|model not found|does not exist|unknown model"
    r"|invalid model|context (?:length|window)|maximum context|prompt is too long",
    re.IGNORECASE,
)
_OUTPUT_RE = re.compile(r"didn't match the expected format", re.IGNORECASE)
_MODE_RE = re.compile(
    r"structured output|response_format|json_schema|json mode|function calling"
    r"|tool_choice",
    re.IGNORECASE,
)
# A structured output mode that names no real mode.
_NO_MODE = ("default", "unknown")
# Why a failure that is not hard leaves the model untested, by class.
_UNTESTED_WHY = {
    "transient": "still failing after one retry",
    "output": "the provider answered, but the output could not be read",
    "unclear": "the error names neither the model nor the provider",
}

Pair = tuple[str, str]


class ModelPreflightError(SetupError):
    """The baseline's own model failed the model preflight. Raised before
    any wave spend, with the fix line."""


def models_to_check(
    baseline: Pair,
    policy: ModelPolicy | None,
    offered: list[dict[str, Any]],
    *,
    keep_model: bool = False,
) -> tuple[list[Pair], int]:
    """(the pairs to check, baseline first; how many allowed pairs are left
    untested). The module doc, **The models**. `keep_model` with no policy
    is `--models keep` on a baseline whose pair was not pinned."""
    if policy is None and keep_model:
        return [baseline], 0
    allowed = [
        p
        for p in dict.fromkeys(
            (row["provider"], row["model"]) for row in allowed_models(policy, offered)
        )
        if p != baseline
    ]
    chosen = allowed
    if len(allowed) > MAX_CHECKED_MODELS:
        rules = (*policy.exceptions, *policy.allow) if policy is not None else ()
        exact = {(rule.provider, rule.model) for rule in rules if rule.exact}
        chosen = [p for p in allowed if p in exact][:MAX_CHECKED_MODELS]
    return [baseline, *chosen], len(allowed) - len(chosen)


def _model_row(
    offered: Iterable[dict[str, Any]], provider: str, model: str
) -> dict[str, Any] | None:
    for entry in offered:
        if not isinstance(entry, dict) or entry.get("provider_id") != provider:
            continue
        for row in entry.get("models") or []:
            if isinstance(row, dict) and row.get("id") == model:
                return row
    return None


def context_window(row: dict[str, Any] | None) -> int | None:
    """The model's context window in tokens, when its row gives one."""
    for key in CONTEXT_WINDOW_KEYS:
        value = (row or {}).get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def thinking_levels(row: dict[str, Any] | None) -> list[str]:
    """The levels the server offers for the model, lowest first."""
    levels = (row or {}).get("available_thinking_levels")
    return [str(v) for v in levels.values()] if isinstance(levels, dict) else []


async def prompt_tokens(ctx: Ctx, rc_id: str) -> int | None:
    """The run config's prompt and code-tool definitions, in estimated
    tokens. None when its prompt cannot be read."""
    text = (await rc_texts(ctx, [rc_id]))[rc_id]
    if text.prompt is None:
        return None
    chars = len(text.prompt) + sum(
        len(tool.files.get(CODE_TOOL_DESCRIPTION, ""))
        for tool in (text.code_tools or {}).values()
    )
    return math.ceil(chars / CHARS_PER_TOKEN)


async def task_input_is_structured(ctx: Ctx) -> bool | None:
    """Does the task have an input schema? Kiln reads `structured_input`
    then, and `plaintext_input` otherwise. None when the task is unreadable."""
    base = f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}"
    try:
        task = await ctx.kiln.call("GET", base)
    except KilnError:
        return None
    if not isinstance(task, dict):
        return None
    return bool(task.get("input_json_schema"))


def test_input(
    inputs: list[TrainInput], structured: bool | None
) -> dict[str, Any] | None:
    """The request's input field for the shortest train item, or None when
    there is no item to send. A task with no input schema gets plaintext,
    even when the item's text is JSON; a task with one gets an item that
    parses as JSON. `structured` None (the task is unreadable): the item's
    own shape decides."""
    if structured:
        inputs = [i for i in inputs if i.structured is not None]
    inp = min(inputs, key=lambda i: (i.size, i.id), default=None)
    if inp is None:
        return None
    if structured is False or inp.structured is None:
        text = (
            inp.plaintext if inp.plaintext is not None else json.dumps(inp.structured)
        )
        return {"plaintext_input": text}
    return {"structured_input": inp.structured}


async def shortest_train_input(ctx: Ctx) -> dict[str, Any] | None:
    try:
        inputs = split_inputs(ctx.target.project_path, ctx.target.task_id, "train")
    except Exception as exc:  # the test calls are then skipped, with the reason
        logger.warning("model preflight: train inputs unreadable: %r", exc)
        return None
    return test_input(inputs, await task_input_is_structured(ctx))


def test_call_body(
    props: dict[str, Any],
    pair: Pair,
    row: dict[str, Any] | None,
    inp: dict[str, Any],
    *,
    baseline: bool,
) -> dict[str, Any]:
    """The baseline's properties on `pair`, made as cheap as Kiln allows
    (the module doc, **The test call**). `row`: the model's row in Kiln's
    model list, or None; `inp`: the input field (`test_input`)."""
    test = json.loads(json.dumps(props))
    test["model_provider_name"], test["model_name"] = pair
    test["prompt_id"] = TEST_PROMPT_ID
    test.pop("tools_config", None)
    levels = thinking_levels(row)
    if levels:
        test["thinking_level"] = levels[0]
    else:
        test.pop("thinking_level", None)
    # The baseline's mode may be one another provider does not support; the
    # mode Kiln suggests for the model is one it does.
    mode = (row or {}).get("structured_output_mode")
    if not baseline and isinstance(mode, str) and mode not in _NO_MODE:
        test["structured_output_mode"] = mode
    return {"run_config_properties": test, "tags": [MODEL_PREFLIGHT_RUN_TAG], **inp}


def _message_and_type(exc: KilnError) -> tuple[str, str | None]:
    """The server's message, without the HTTP 500 Kiln puts on every failed
    model call, and the body's `error_type` when it has one."""
    message = re.sub(r"^HTTP 500: ", "", error_text(exc))
    body = getattr(exc, "body", None)
    error_type = body.get("error_type") if isinstance(body, dict) else None
    return message, error_type if isinstance(error_type, str) else None


def failure_reason(exc: KilnError) -> str:
    """The message, with the `error_type` after it: a provider's 400 reaches
    the client only as "An unexpected error occurred." (BadRequestError)."""
    message, error_type = _message_and_type(exc)
    return f"{message} ({error_type})" if error_type else message


def classify_failure(exc: KilnError) -> str:
    """ "transient", "hard", "output" or "unclear" (the module doc, **The
    failure classes**). The order matters: a quota or auth text is hard
    whatever else it says, and a timeout's text is not a context error."""
    if isinstance(exc, KilnTransportError):
        return "transient"
    message, error_type = _message_and_type(exc)
    text = error_text_without_model_output(message)
    provider_class = classify_provider_message(text)
    if provider_class in ("quota_exhausted", "auth") or error_type in (
        "AuthenticationError",
        "PermissionDeniedError",
    ):
        return "hard"
    if (
        error_type in TRANSIENT_ERROR_TYPES
        or (isinstance(exc, KilnAPIError) and exc.status_code in (429, 502, 503, 504))
        or provider_class == "rate_limit"
        or _TRANSIENT_RE.search(text)
    ):
        return "transient"
    if (
        error_type in OUTPUT_ERROR_TYPES
        or provider_class == "malformed_output"
        or _OUTPUT_RE.search(text)
    ):
        return "output"
    if _MODE_RE.search(text) or error_type == "UnsupportedParamsError":
        return "unclear"
    if error_type in HARD_ERROR_TYPES or _HARD_RE.search(text):
        return "hard"
    return "unclear"


async def _test_call(
    ctx: Ctx, check: ModelCheck, body: dict[str, Any], sem: asyncio.Semaphore
) -> None:
    path = f"/api/projects/{ctx.target.project_id}/tasks/{ctx.target.task_id}/run"
    for attempt in (1, 2):
        check.attempts = attempt
        try:
            async with sem:
                run = await ctx.kiln.call(
                    "POST",
                    path,
                    body=body,
                    timeout_secs=TEST_CALL_TIMEOUT_SECS,
                    retries=False,
                )
        except KilnError as exc:
            kind = classify_failure(exc)
            reason = failure_reason(exc)[:300]
            if attempt == 1 and kind == "transient":
                logger.info(
                    "model preflight: %s/%s failed (%s); one retry",
                    check.provider,
                    check.model,
                    reason[:200],
                )
                await ctx.sleep(RETRY_SLEEP_SECS)
                continue
            if kind == "hard":
                retried = " after one retry" if attempt == 2 else ""
                check.outcome = "flagged"
                check.reason = f"the test call failed{retried}: {reason}"
            else:
                check.outcome = "untested"
                check.reason = f"{_UNTESTED_WHY[kind]}: {reason}"
            return
        check.cost_usd += run_cost(run if isinstance(run, dict) else {})
        return


def flag_line(check: ModelCheck) -> str:
    who = " (the baseline's model)" if check.baseline else ""
    state = "untested, " if check.outcome == "untested" else ""
    return (
        f"⚠ model preflight: {check.provider}/{check.model}{who} —"
        f" {state}{check.reason}"
    )


def refusal(check: ModelCheck, rc_id: str) -> str:
    return (
        f"model preflight: the baseline {rc_id} runs on"
        f" {check.provider}/{check.model}, and {check.reason}\n"
        "fix: connect the provider in Kiln and make sure it serves this model"
        " (a test run in the Kiln app shows the error), or set a baseline on a"
        " model that answers. To start without the check, pass"
        " --skip-model-preflight."
    )


async def run_model_preflight(ctx: Ctx, rc_id: str) -> ModelPreflightResult:
    """The whole check (module doc). Records the flagged models in
    `ctx.flagged_models` and the result in the `model_preflight` event.
    Raises `ModelPreflightError` when the baseline's own model fails."""
    result = ModelPreflightResult(baseline_rc_id=rc_id)

    def skip(why: str) -> ModelPreflightResult:
        result.skipped = why[:300]
        line = f"model preflight: skipped — {result.skipped}"
        ctx.log.event("model_preflight", line=line, result=result)
        return result

    try:
        props = await rc_props(ctx, rc_id)
    except (ProbeSetupError, KilnError) as exc:
        return skip(f"the baseline's run config is unreadable: {exc}")
    provider, model = props.get("model_provider_name"), props.get("model_name")
    if not isinstance(provider, str) or not isinstance(model, str):
        return skip("the baseline names no model provider and model")
    try:
        offered = await ctx.kiln.call("GET", "/api/available_models")
    except KilnError:
        offered = []
    offered = offered if isinstance(offered, list) else []
    pairs, result.untested = models_to_check(
        (provider, model), ctx.model_policy, offered, keep_model=ctx.keep_model
    )
    result.estimated_prompt_tokens = await prompt_tokens(ctx, rc_id)
    inp = await shortest_train_input(ctx)
    if inp is None:
        result.skipped = "no train input to send; only the context check ran"
    sem = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)
    calls = []
    for pair in pairs:
        row = _model_row(offered, *pair)
        check = ModelCheck(
            provider=pair[0],
            model=pair[1],
            baseline=pair == (provider, model),
            context_window=context_window(row),
        )
        result.checks.append(check)
        needed = result.estimated_prompt_tokens
        if check.context_window and needed and check.context_window < needed:
            check.outcome = "flagged"
            check.reason = (
                f"its context window ({check.context_window} tokens) is smaller"
                f" than the baseline's prompt and tools (about {needed} tokens)"
            )
        elif inp is not None:
            body = test_call_body(props, pair, row, inp, baseline=check.baseline)
            calls.append(_test_call(ctx, check, body, sem))
    await asyncio.gather(*calls)
    result.spend_usd = sum(c.cost_usd for c in result.checks)
    ctx.log.event(
        "spend", source=SPEND_SOURCE, lane=PROVIDER_LANE, cost_usd=result.spend_usd
    )
    for check in result.flagged():
        ctx.flagged_models[(check.provider, check.model)] = check.reason or ""
        ctx.log.event(
            "model_preflight_flag",
            line=flag_line(check),
            provider=check.provider,
            model=check.model,
            reason=check.reason,
        )
        logger.warning("%s", flag_line(check))
    for check in result.inconclusive():
        # Not flagged: nothing is excluded, and the start goes on.
        ctx.log.event(
            "model_preflight_untested",
            line=flag_line(check),
            provider=check.provider,
            model=check.model,
            reason=check.reason,
        )
        logger.warning("%s", flag_line(check))
    untested = f", {result.untested} allowed model(s) not tested"
    summary = (
        f"model preflight: {len(result.checks)} model(s) checked,"
        f" {len(result.flagged())} flagged, {len(result.inconclusive())} untested"
        + (untested if result.untested else "")
        + (f" ({result.skipped})" if result.skipped else "")
        + f" — ${result.spend_usd:.4f}"
    )
    ctx.log.event("model_preflight", line=summary, result=result)
    logger.info("%s", summary)
    for check in result.flagged():
        if check.baseline:
            raise ModelPreflightError(refusal(check, rc_id))
    return result


def flagged_reason(ctx: Ctx, provider: Any, model: Any) -> str | None:
    """Why the model preflight flagged (provider, model), or None."""
    return getattr(ctx, "flagged_models", {}).get((provider, model))
