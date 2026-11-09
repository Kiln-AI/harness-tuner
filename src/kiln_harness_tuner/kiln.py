"""KilnClient — the orchestrator's Kiln REST access.

Deliberately shaped like the agents' `call_kiln_api` tool — one method taking
`(method, url_path, query_params, body)` — so another transport can replace
it without touching its callers (treat this as an interface with one
implementation, like AgentDispatcher). No `kiln_ai` imports, ever
(REST-only; docs/architecture.md section 1).

Retry policy is the "plain REST" row of docs/architecture.md section 10:
3 quick retries with backoff on transient failures (5xx, transport errors);
4xx raises immediately — e.g. the 422 missing-split error must surface
unchanged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

DEFAULT_KILN_HOST = "http://localhost:8757"

# 3 quick retries after the initial attempt, with backoff
RETRY_BACKOFF_SECS: tuple[float, ...] = (2.0, 5.0, 10.0)

# The longest Kiln message an error screen or a warning quotes.
KILN_MESSAGE_MAX_CHARS = 500
# What pydantic puts before a validator's own message, as Kiln passes it on.
_MESSAGE_PREFIXES = (": ", "Value error, ")


def _clean_message(message: object) -> str:
    text = str(message)
    for prefix in _MESSAGE_PREFIXES:
        text = text.removeprefix(prefix)
    return text


def _messages(body: Any) -> list[str]:
    """Kiln's own messages in an error body: its `error_messages`, else
    FastAPI's `detail` (a string, or a validation error's list of items)."""
    if not isinstance(body, dict):
        return []
    error_messages = body.get("error_messages")
    if isinstance(error_messages, list) and error_messages:
        return [_clean_message(message) for message in error_messages]
    detail = body.get("detail")
    if isinstance(detail, str):
        return [_clean_message(detail)]
    if isinstance(detail, list):
        return [
            _clean_message(item["msg"])
            for item in detail
            if isinstance(item, dict) and "msg" in item
        ]
    return []


def kiln_error_text(body: Any) -> str:
    """The message in a Kiln error body, for a person to read: Kiln's own
    messages joined with `; `, else the body as text, cut to
    `KILN_MESSAGE_MAX_CHARS`."""
    messages = _messages(body)
    if messages:
        text = "; ".join(messages)
    elif isinstance(body, str):
        text = _clean_message(body)
    else:
        text = repr(body)
    if len(text) > KILN_MESSAGE_MAX_CHARS:
        text = text[: KILN_MESSAGE_MAX_CHARS - 1] + "…"
    return text


class KilnError(Exception):
    """Base class for KilnClient failures."""


class KilnAPIError(KilnError):
    """A non-2xx HTTP response from the Kiln API."""

    def __init__(self, method: str, url_path: str, status_code: int, body: Any):
        self.method = method
        self.url_path = url_path
        self.status_code = status_code
        self.body = body
        super().__init__(f"{method} {url_path} -> HTTP {status_code}: {body!r}")

    @property
    def kiln_message(self) -> str:
        return kiln_error_text(self.body)


class KilnTransportError(KilnError):
    """The request never produced an HTTP response (after retries)."""


def _parse_body(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


class KilnClient:
    """call_kiln_api-shaped async REST client for the running Kiln app.

    `client` and `sleep` are injectable for tests (httpx.MockTransport / no-op
    sleep); production callers pass nothing.
    """

    def __init__(
        self,
        kiln_host: str = DEFAULT_KILN_HOST,
        *,
        client: httpx.AsyncClient | None = None,
        retry_backoff_secs: tuple[float, ...] = RETRY_BACKOFF_SECS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.kiln_host = kiln_host.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        self._retry_backoff_secs = retry_backoff_secs
        self._sleep = sleep

    async def call(
        self,
        method: str,
        url_path: str,
        query_params: dict[str, Any] | None = None,
        body: Any | None = None,
        *,
        timeout_secs: float | None = None,
        retries: bool | None = None,
    ) -> Any:
        """Perform one Kiln API call; returns the parsed JSON body (None when
        the response is empty). Raises KilnAPIError on 4xx immediately and on
        5xx / transport errors after the retry ladder is exhausted.

        `timeout_secs` overrides the client's default (60s) per request —
        required for long-polls like `POST /api/jobs/wait`, whose server-side
        hold outlives the default read timeout. `retries=False` disables the
        internal 5xx/transport ladder for calls that manage their own retry
        policy (the eval-job ladder — re-waiting inside call() would
        multiply its attempts)."""
        url = f"{self.kiln_host}{url_path}"
        # Retries default to GET only: a POST/PATCH whose reply was lost
        # (read timeout, proxy reset) had already been applied, and replaying
        # it forked the `baseline`/`val` memory singletons. Writers
        # that ARE idempotent (eval jobs, the pure statistics route) opt in
        # with `retries=True`; the memory writers recover a lost reply by
        # re-listing instead.
        if retries is None:
            retries = method.upper() == "GET"
        backoffs = self._retry_backoff_secs if retries else ()
        timeout = timeout_secs if timeout_secs is not None else httpx.USE_CLIENT_DEFAULT
        attempt = 0
        while True:
            try:
                response = await self._client.request(
                    method.upper(), url, params=query_params, json=body, timeout=timeout
                )
            except httpx.HTTPError as exc:
                if attempt < len(backoffs):
                    await self._sleep(backoffs[attempt])
                    attempt += 1
                    continue
                raise KilnTransportError(
                    f"{method} {url_path} failed after {attempt + 1} attempts: {exc!r}"
                ) from exc

            if response.status_code < 400:
                return _parse_body(response)
            if response.status_code < 500:
                # Client errors are real answers (422 missing split, 404, …) —
                # never retried, never softened.
                raise KilnAPIError(
                    method, url_path, response.status_code, _parse_body(response)
                )
            if attempt < len(backoffs):
                await self._sleep(backoffs[attempt])
                attempt += 1
                continue
            raise KilnAPIError(
                method, url_path, response.status_code, _parse_body(response)
            )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> KilnClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
