"""provider_errors tests the free-text
job-error classifier that routes the eval-job lane's retry policy. Message
fixtures are verbatim from stored wave logs / job errors."""

import pytest

from kiln_harness_tuner.provider_errors import (
    JobErrorDiagnosis,
    ProviderQuotaError,
    classify_provider_message,
    diagnose_job_errors,
    parse_retry_after_secs,
)
from kiln_harness_tuner.schemas import ErrorInfo

FIREWORKS_429 = (
    "litellm.RateLimitError: RateLimitError: Fireworks_aiException - "
    '{"error":{"object":"error","type":"invalid_request_error",'
    '"message":"rate limit exceeded, please try again later"}}'
)
GROQ_429 = (
    "litellm.RateLimitError: GroqException - Rate limit reached for model "
    "'openai/gpt-oss-120b' in organization 'org' service tier 'on_demand' on "
    "tokens per minute (TPM): Limit 250000, Used 248034, Requested 3271. "
    "Please try again in 17.04ms."
)
OPENROUTER_402 = (
    "This request requires more credits, or fewer max_tokens. You requested "
    "up to 65536 tokens, but can only afford 37517. To increase, visit "
    "https://openrouter.ai/workspaces/default/keys/ — "
    '"code":402,"metadata":{"limit_source":"openrouter_key_limit"}'
)
JUDGE_JSON = "This task requires JSON output but the model didn't return valid JSON"
EMPTY_COMPLETION = (
    "Model returned an assistant message, but no content or tool calls. "
    "This is not supported."
)
HALLUCINATED_TOOL = (
    "A tool named 'call_palice_api' was invoked by a model, but was not available."
)
KILN_500_RATE_LIMIT = (
    '{"message":"Rate limit exceeded. Wait a moment and try again.",'
    '"error_type":"RateLimitError"}'
)


class TestClassify:
    @pytest.mark.parametrize(
        "message", [FIREWORKS_429, GROQ_429, KILN_500_RATE_LIMIT, "HTTP 429"]
    )
    def test_rate_limit_family(self, message):
        assert classify_provider_message(message) == "rate_limit"

    def test_quota_beats_rate_limit_vocabulary(self):
        # OpenRouter's 402 message is a quota condition even when limit
        # vocabulary appears alongside — quota is checked first.
        assert classify_provider_message(OPENROUTER_402) == "quota_exhausted"

    @pytest.mark.parametrize(
        "message",
        ["Invalid API key provided", "401 authentication_error: no auth credentials"],
    )
    def test_auth_family(self, message):
        assert classify_provider_message(message) == "auth"

    @pytest.mark.parametrize(
        "message",
        [
            JUDGE_JSON,
            EMPTY_COMPLETION,
            HALLUCINATED_TOOL,
            "Failed to validate arguments for tool 'skill': 'name' is required",
            "Score key mismatch: got ['__skipped__'], expected ['x']",
        ],
    )
    def test_malformed_output_family(self, message):
        assert classify_provider_message(message) == "malformed_output"

    def test_everything_else_is_provider_error(self):
        assert classify_provider_message("upstream exploded") == "provider_error"
        # Bare id digits must not read as an HTTP status.
        assert classify_provider_message("run mem_4021 missing") == "provider_error"


class TestRetryAfter:
    def test_milliseconds(self):
        assert parse_retry_after_secs(GROQ_429) == pytest.approx(0.01704)

    def test_seconds(self):
        assert parse_retry_after_secs("try again in 2.5s") == pytest.approx(2.5)
        assert parse_retry_after_secs("retry after 30") == pytest.approx(30.0)

    def test_absent(self):
        assert parse_retry_after_secs(FIREWORKS_429) is None


class TestDiagnose:
    def test_priority_and_exemplar(self):
        diagnosis = diagnose_job_errors([JUDGE_JSON, GROQ_429, OPENROUTER_402])
        assert diagnosis.error_class == "quota_exhausted"
        assert diagnosis.exemplar == OPENROUTER_402

    def test_rate_limit_hint_is_the_max_across_rows(self):
        diagnosis = diagnose_job_errors(
            [GROQ_429, "rate limit — try again in 3s", FIREWORKS_429]
        )
        assert diagnosis.error_class == "rate_limit"
        assert diagnosis.retry_after_secs == pytest.approx(3.0)

    def test_empty_set(self):
        assert diagnose_job_errors([]) == JobErrorDiagnosis("provider_error", None, "")


def test_quota_error_carries_the_error_info():
    error = ErrorInfo(error_class="quota_exhausted", detail=OPENROUTER_402)
    exc = ProviderQuotaError(error)
    assert exc.error is error
    assert "requires more credits" in str(exc)
