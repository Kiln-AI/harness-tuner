"""KilnClient tests — httpx.MockTransport only, no live server

Covers the call_kiln_api request shape and the plain-REST
retry policy: 4xx immediate, 5xx/transport retried 3x with backoff.
"""

import httpx
import pytest

from kiln_harness_tuner.kiln import KilnAPIError, KilnClient, KilnTransportError


class Recorder:
    """Scripted MockTransport handler recording every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = (
            self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        )
        if isinstance(response, Exception):
            raise response
        return response


def make_client(handler, **kwargs):
    sleeps: list[float] = []

    async def fake_sleep(secs: float) -> None:
        sleeps.append(secs)

    client = KilnClient(
        "http://localhost:8757",
        # Timeout pinned so assertions don't ride httpx's library default.
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=httpx.Timeout(5.0)
        ),
        sleep=fake_sleep,
        **kwargs,
    )
    return client, sleeps


async def test_get_sends_method_path_and_query_params():
    handler = Recorder([httpx.Response(200, json={"ok": True})])
    client, _ = make_client(handler)
    result = await client.call(
        "GET",
        "/api/projects/424242748186/memories",
        query_params={"tags": "baseline"},
    )
    assert result == {"ok": True}
    request = handler.requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/projects/424242748186/memories"
    assert request.url.params["tags"] == "baseline"


async def test_post_serializes_json_body():
    handler = Recorder([httpx.Response(200, json={"id": "job_1"})])
    client, _ = make_client(handler)
    body = {"eval_id": "eval_1", "split": "train"}
    result = await client.call("POST", "/api/jobs/evals/run", body=body)
    assert result == {"id": "job_1"}
    request = handler.requests[0]
    assert request.method == "POST"
    assert request.headers["content-type"] == "application/json"
    assert (
        b'"split": "train"' in request.content or b'"split":"train"' in request.content
    )


async def test_empty_body_returns_none():
    handler = Recorder([httpx.Response(204)])
    client, _ = make_client(handler)
    assert await client.call("DELETE", "/api/thing/1") is None


async def test_4xx_raises_immediately_without_retry():
    handler = Recorder([httpx.Response(422, json={"detail": "val split unset"})])
    client, sleeps = make_client(handler)
    with pytest.raises(KilnAPIError) as excinfo:
        await client.call("POST", "/api/jobs/evals/run", body={"split": "val"})
    assert excinfo.value.status_code == 422
    assert excinfo.value.body == {"detail": "val split unset"}
    assert len(handler.requests) == 1  # no retries: 422 must surface
    assert sleeps == []


async def test_5xx_retried_then_succeeds():
    handler = Recorder(
        [
            httpx.Response(500, text="boom"),
            httpx.Response(502, text="bad gateway"),
            httpx.Response(200, json={"recovered": True}),
        ]
    )
    client, sleeps = make_client(handler)
    result = await client.call("GET", "/api/jobs")
    assert result == {"recovered": True}
    assert len(handler.requests) == 3
    assert sleeps == [2.0, 5.0]  # backoff recorded per retry


async def test_5xx_exhausts_retries_and_raises():
    handler = Recorder([httpx.Response(500, text="down")])
    client, sleeps = make_client(handler)
    with pytest.raises(KilnAPIError) as excinfo:
        await client.call("GET", "/api/jobs")
    assert excinfo.value.status_code == 500
    assert len(handler.requests) == 4  # initial + 3 quick retries
    assert sleeps == [2.0, 5.0, 10.0]


async def test_transport_error_retried_like_5xx():
    handler = Recorder(
        [
            httpx.ConnectError("refused"),
            httpx.Response(200, json={"up": True}),
        ]
    )
    client, sleeps = make_client(handler)
    assert await client.call("GET", "/api/jobs") == {"up": True}
    assert sleeps == [2.0]


async def test_persistent_transport_error_raises_transport_error():
    handler = Recorder([httpx.ConnectError("refused")])
    client, _ = make_client(handler)
    with pytest.raises(KilnTransportError):
        await client.call("GET", "/api/jobs")
    assert len(handler.requests) == 4


async def test_per_request_timeout_override_reaches_the_transport():
    handler = Recorder([httpx.Response(200, json=[])])
    client, _ = make_client(handler)
    await client.call("POST", "/api/jobs/wait", body={"ids": []}, timeout_secs=3630.0)
    # httpx carries the resolved per-request timeout in request extensions —
    # the long-poll must outlive the client default (60s).
    timeout = handler.requests[0].extensions["timeout"]
    assert timeout["read"] == 3630.0


async def test_default_timeout_used_when_not_overridden():
    handler = Recorder([httpx.Response(200, json=[])])
    client, _ = make_client(handler)
    await client.call("GET", "/api/jobs")
    # No override → the injected client's own timeout applies (pinned to 5s
    # in make_client; the client KilnClient builds itself uses 60s).
    assert handler.requests[0].extensions["timeout"]["read"] == 5.0


async def test_retries_false_raises_5xx_immediately():
    handler = Recorder([httpx.Response(500, json={"detail": "boom"})])
    client, sleeps = make_client(handler)
    with pytest.raises(KilnAPIError) as excinfo:
        await client.call("POST", "/api/jobs/wait", body={"ids": []}, retries=False)
    assert excinfo.value.status_code == 500
    assert len(handler.requests) == 1  # no internal retry ladder
    assert sleeps == []


async def test_retries_false_raises_transport_error_immediately():
    handler = Recorder([httpx.ReadTimeout("read timed out")])
    client, sleeps = make_client(handler)
    with pytest.raises(KilnTransportError):
        await client.call("POST", "/api/jobs/wait", body={"ids": []}, retries=False)
    assert len(handler.requests) == 1
    assert sleeps == []


async def test_post_5xx_is_not_retried_by_default():
    # A write whose reply was lost may already have been applied, and replaying
    # it forks the memory singletons. Writes opt in to retries explicitly.
    handler = Recorder([httpx.Response(500, text="boom")])
    client, sleeps = make_client(handler)
    with pytest.raises(KilnAPIError):
        await client.call("POST", "/api/projects/p/memories", body={"overview": "x"})
    assert len(handler.requests) == 1
    assert sleeps == []


async def test_post_transport_error_is_not_retried_by_default():
    handler = Recorder([httpx.ConnectError("reset")])
    client, _ = make_client(handler)
    with pytest.raises(KilnTransportError):
        await client.call("POST", "/api/projects/p/memories", body={"overview": "x"})
    assert len(handler.requests) == 1


async def test_post_with_retries_true_still_walks_the_ladder():
    # Idempotent writers (eval jobs, the pure statistics route) opt in.
    handler = Recorder(
        [httpx.Response(500, text="boom"), httpx.Response(200, json={"id": "j1"})]
    )
    client, sleeps = make_client(handler)
    result = await client.call(
        "POST", "/api/jobs/evals/run", body={"split": "train"}, retries=True
    )
    assert result == {"id": "j1"}
    assert sleeps == [2.0]
