"""Provider-failure classification for the eval-job lane.

Kiln serves job errors as free text (`GET /api/jobs/{id}/errors` — no
structured kind), and it converts provider 429s into HTTP 500s, so the class
of a failure is only recoverable from the message body. The corpus is
extremely regular — every observed failure family carries a stable litellm/
provider phrase — so the lane classifies by regex and routes retry policy on
the class instead of treating a transient 429, a terminal 402 and a malformed
judge identically (a flat 2×60s ladder).

Classes (priority order — a message matching several takes the first):

- ``quota_exhausted`` — out of credits / hard spend cap (OpenRouter 402 …).
  Terminal and operator-actionable: retrying burns wall-clock, never succeeds.
- ``auth`` — invalid/unauthorized key. Same policy as quota.
- ``rate_limit`` — throttling. The MOST retryable class in the corpus (Groq
  retry-afters were milliseconds); gets extra attempts + exponential backoff
  honoring the provider's own retry-after hint.
- ``malformed_output`` — the judge/model emitted unusable output (bad JSON,
  empty completion, hallucinated tool name). Provider-side sleeping is pure
  waste here (an unconditional 60s per retry) — retried without backoff.
- ``provider_error`` — everything else; keeps the pre-existing ladder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from kiln_harness_tuner.schemas import ErrorInfo

# Message families this matches (shapes seen in wave logs / job errors):
#   "This request requires more credits, or fewer max_tokens. …"
#   '"code":402,"metadata":{"limit_source":"openrouter_key_limit",…}'
#   A key limit can also arrive as an HTTP 403, which would otherwise be
#   classed provider_error and retried: '… {"error":{"message":"Key limit
#   exceeded (monthly limit). …","code":403}}'
# Other providers' documented terminal texts: OpenAI 429 "insufficient_quota"
# / "You exceeded your current quota"; Anthropic "Your credit balance is too
# low"; Gemini "RESOURCE_EXHAUSTED" only when it names a daily or billing
# quota (a per-minute RESOURCE_EXHAUSTED is throttling, rate_limit below).
_QUOTA_RE = re.compile(
    r"requires more credits|insufficient[_ ]credits|out of credits"
    r"|exceeded your (?:current )?quota|billing hard limit|insufficient_quota"
    r"|key[_ ]limit(?: exceeded)?|limit exceeded \((?:daily|weekly|monthly)"
    r"|credit balance is too low|payment required"
    r"|resource_exhausted.{0,400}?(?:per[ _]?day|daily|billing|free[_ ]tier)"
    r"|(?:per[ _]?day|daily|billing|free[_ ]tier).{0,400}?resource_exhausted"
    r"|\"code\"\s*:\s*402",
    re.IGNORECASE | re.DOTALL,
)
_AUTH_RE = re.compile(
    r"invalid api key|incorrect api key|api key (?:is )?invalid|unauthorized"
    r"|authentication[_ ]error|no auth credentials|\"code\"\s*:\s*401",
    re.IGNORECASE,
)
# A bare status number is evidence only in a job-error body, which is a
# provider response and nothing else. An agent's free-text `result` can
# quote a 401 from anything it probed, so the CLI lane
# classifies with `status_codes=False`.
_QUOTA_CODE_RE = re.compile(r"\b402\b")
_AUTH_CODE_RE = re.compile(r"\b401\b")
#   "litellm.RateLimitError: … Fireworks_aiException … rate limit exceeded"
#   "Rate limit reached for model … (TPM): Limit 250000 …"
_RATE_LIMIT_RE = re.compile(
    r"ratelimiterror|rate[\s_-]?limit|too many requests|\b429\b"
    r"|resource_exhausted",
    re.IGNORECASE,
)
#   "This task requires JSON output but the model didn't return valid JSON"
#   "Model returned an assistant message, but no content or tool calls."
#   "A tool named 'call_palice_api' was invoked by a model, but was not available."
#   "Score key mismatch: got ['__skipped__'] …"
_MALFORMED_RE = re.compile(
    r"didn't return valid json|did not return valid json"
    r"|didn't meet the schema|did not meet the schema"
    r"|requires a specific output schema"
    r"|no content or tool calls"
    r"|was invoked by a model, but was not available"
    r"|failed to validate arguments for tool"
    r"|score key mismatch",
    re.IGNORECASE,
)

#   "… please try again in 17.04ms" / "try again in 2.5s" / "retry after 30"
_RETRY_AFTER_RE = re.compile(
    r"try again in\s+(\d+(?:\.\d+)?)\s*(ms|s\b|seconds?)"
    r"|retry[\s_-]?after[:\s]+(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

# Priority: a quota message often also contains rate-limit vocabulary, so the
# terminal classes are checked first.
_CLASSIFIERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("quota_exhausted", _QUOTA_RE),
    ("auth", _AUTH_RE),
    ("rate_limit", _RATE_LIMIT_RE),
    ("malformed_output", _MALFORMED_RE),
)

_PRIORITY = ("quota_exhausted", "auth", "rate_limit", "malformed_output")


# Where an error message starts quoting what the MODEL wrote. Kiln's schema
# error ends with "The JSON was: ```json {…the whole answer…}```". A batch
# plan in that echo that says "Recurring unauthorized charges" must not read as
# "the provider rejected the API key". Nothing the
# model wrote may classify an error, so the echo is cut before any pattern runs.
_ECHO_MARKERS = re.compile(
    r"```|the json was\s*:|the output was\s*:|model output\s*:|raw output\s*:"
    r"|the response was\s*:",
    re.IGNORECASE,
)


def error_text_without_model_output(message: str) -> str:
    """`message` cut at the first place it starts quoting model output."""
    match = _ECHO_MARKERS.search(message)
    return message[: match.start()] if match else message


def classify_provider_message(message: str, *, status_codes: bool = True) -> str:
    """One free-text provider/job error message → its failure class.
    `status_codes=False` ignores a bare `401`/`402` number (see the code
    patterns above). Echoed model output is never read (see _ECHO_MARKERS)."""
    message = error_text_without_model_output(message)
    for name, pattern in _CLASSIFIERS:
        if pattern.search(message):
            return name
        if (
            status_codes
            and name == "quota_exhausted"
            and _QUOTA_CODE_RE.search(message)
        ):
            return name
        if status_codes and name == "auth" and _AUTH_CODE_RE.search(message):
            return name
    return "provider_error"


def parse_retry_after_secs(message: str) -> float | None:
    """The provider's own retry-after hint, in seconds, when the message
    carries one (Groq embeds "try again in 17.04ms" in its 429 text)."""
    match = _RETRY_AFTER_RE.search(message)
    if match is None:
        return None
    if match.group(1) is not None:
        value = float(match.group(1))
        unit = match.group(2).lower()
        return value / 1000.0 if unit == "ms" else value
    return float(match.group(3))


@dataclass(frozen=True)
class JobErrorDiagnosis:
    """The classification of one attempt's error messages, as a set."""

    error_class: str  # highest-priority class across the messages
    retry_after_secs: float | None  # max provider hint among rate-limit rows
    # the first message of the winning class — the detail a human acts on
    # (quota messages carry the provider's own remedy URL verbatim)
    exemplar: str


def diagnose_job_errors(messages: list[str]) -> JobErrorDiagnosis:
    """Classify one attempt's job-error messages as a set. The winning class
    is the highest-priority one present; ``retry_after_secs`` is the largest
    provider hint found on any rate-limited row (a batch throttles as one)."""
    by_class: dict[str, str] = {}
    retry_after: float | None = None
    for message in messages:
        cls = classify_provider_message(message)
        by_class.setdefault(cls, message)
        if cls == "rate_limit":
            hint = parse_retry_after_secs(message)
            if hint is not None:
                retry_after = max(retry_after or 0.0, hint)
    for cls in _PRIORITY:
        if cls in by_class:
            return JobErrorDiagnosis(cls, retry_after, by_class[cls])
    exemplar = messages[0] if messages else ""
    return JobErrorDiagnosis("provider_error", None, exemplar)


class ProviderQuotaError(Exception):
    """A terminal, operator-actionable provider condition (quota exhausted /
    auth failure) — the run must STOP SPENDING now, not retry (the
    circuit breaker). Carries the provider's own message verbatim: quota errors
    embed the remedy ("Raise or remove this API key's usage limit at <url>") and
    the run's exit surfaces it."""

    def __init__(self, error: ErrorInfo, usage: Any = None):
        self.error = error
        # The spend of the session(s) that hit the wall: it keeps the round's
        # `total_cost_usd` and every earlier rung's, so the accounting holds on
        # exactly the run where it matters. Typed
        # loosely to keep this module free of the schemas' Usage import
        # order; the dispatcher and the retry ladder put a `Usage` here.
        self.usage = usage
        super().__init__(error.detail)


# Provider names by the vocabulary their errors carry (litellm prefixes an
# exception with the provider: "OpenrouterException", "AnthropicException"…).
_PROVIDERS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("OpenRouter", re.compile(r"openrouter", re.I), "the OpenRouter dashboard"),
    ("Anthropic", re.compile(r"anthropic", re.I), "the Anthropic console (billing)"),
    (
        "Google",
        re.compile(r"gemini|vertex|resource_exhausted|generativelanguage", re.I),
        "the Google AI Studio / Cloud console (quotas and billing)",
    ),
    ("Fireworks", re.compile(r"fireworks", re.I), "the Fireworks dashboard"),
    ("Together", re.compile(r"together", re.I), "the Together dashboard"),
    ("Cerebras", re.compile(r"cerebras", re.I), "the Cerebras dashboard"),
    ("Groq", re.compile(r"groq", re.I), "the Groq console"),
    (
        "OpenAI",
        re.compile(r"openai|insufficient_quota", re.I),
        "the OpenAI platform (billing / usage limits)",
    ),
)
_INNER_MESSAGE_RE = re.compile(r'"message"\s*:\s*"([^"]{1,300})"')


def provider_of(message: str) -> tuple[str, str]:
    """(provider name, where its limit is raised) for an error message, or
    ("the provider", "the provider's dashboard") when it names none."""
    for name, pattern, where in _PROVIDERS:
        if pattern.search(message):
            return name, where
    return "the provider", "the provider's dashboard"


def quota_stop_line(error_class: str, message: str) -> str:
    """One plain line for the operator, e.g. "stopped: OpenRouter key limit
    exceeded (monthly limit) — raise it on the OpenRouter dashboard"."""
    provider, where = provider_of(message)
    inner = _INNER_MESSAGE_RE.search(message)
    text = inner.group(1) if inner else message.strip().splitlines()[0][:200]
    # litellm's wrappers ("litellm.BadRequestError: AnthropicException - …")
    # name the class, not the problem; the provider is named separately.
    text = re.sub(
        r"^(?:[\w.]+(?:Error|Exception)\s*[:\-]\s*)+", "", text.strip()
    ).strip()
    text = re.sub(r"\.?\s*Manage it using \S+", "", text).strip().rstrip(".")
    if error_class == "auth":
        return (
            f"stopped: {provider} rejected the API key — {text[:200]} — fix the"
            f" key on {where}"
        )
    head = text[0].lower() + text[1:] if text else "quota exhausted"
    return f"stopped: {provider} {head} — raise it on {where}"
