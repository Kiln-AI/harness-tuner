"""Tests for KilnApiCallTool."""

import json
import re

import httpx
import pytest
import respx

from kiln_api_mcp.tool import ALLOWED_EXACT_PATHS, KilnApiCallTool


@pytest.fixture
def tool():
    return KilnApiCallTool(api_base_url="http://test-server:8757")


class TestKilnApiCallToolInit:
    def test_default_base_url(self):
        tool = KilnApiCallTool()
        assert tool._api_base_url == "http://localhost:8757"

    def test_custom_base_url(self):
        tool = KilnApiCallTool(api_base_url="http://custom:9999")
        assert tool._api_base_url == "http://custom:9999"

    @pytest.mark.asyncio
    async def test_tool_metadata(self, tool):
        assert await tool.name() == "call_kiln_api"
        assert "Kiln REST API" in await tool.description()
        assert await tool.id() == "kiln_tool::call_kiln_api"


class TestInputValidation:
    @pytest.mark.asyncio
    async def test_invalid_method(self, tool):
        with pytest.raises(ValueError, match="Invalid method 'INVALID'"):
            await tool.run(method="INVALID", url_path="/api/test")

    @pytest.mark.asyncio
    async def test_method_case_insensitive(self, tool):
        with respx.mock:
            respx.get("http://test-server:8757/api/test").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            result = await tool.run(method="get", url_path="/api/test")
            assert "status_code" in json.loads(result.output)

    @pytest.mark.asyncio
    async def test_url_path_missing_slash(self, tool):
        with pytest.raises(ValueError, match="url_path must start with '/'"):
            await tool.run(method="GET", url_path="no-slash")

    @pytest.mark.asyncio
    async def test_body_with_get(self, tool):
        with pytest.raises(ValueError, match="body parameter not allowed with GET"):
            await tool.run(method="GET", url_path="/api/test", body="data")

    @pytest.mark.asyncio
    async def test_body_with_delete(self, tool):
        with pytest.raises(ValueError, match="body parameter not allowed with DELETE"):
            await tool.run(method="DELETE", url_path="/api/test", body="data")


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_get_request(self, tool):
        with respx.mock:
            respx.get("http://test-server:8757/api/projects").mock(
                return_value=httpx.Response(200, json={"projects": ["p1", "p2"]})
            )
            result = await tool.run(method="GET", url_path="/api/projects")
            parsed = json.loads(result.output)
            assert parsed["status_code"] == 200
            assert "projects" in parsed["body"]

    @pytest.mark.asyncio
    async def test_post_request_with_body(self, tool):
        with respx.mock:
            route = respx.post("http://test-server:8757/api/projects").mock(
                return_value=httpx.Response(201, json={"id": "new-project"})
            )
            body = json.dumps({"name": "test-project"})
            result = await tool.run(method="POST", url_path="/api/projects", body=body)
            parsed = json.loads(result.output)
            assert parsed["status_code"] == 201
            # Verify the body was sent
            assert route.calls.last.request.content == body.encode()

    @pytest.mark.asyncio
    async def test_patch_request(self, tool):
        with respx.mock:
            route = respx.patch("http://test-server:8757/api/projects/123").mock(
                return_value=httpx.Response(200, json={"updated": True})
            )
            body = json.dumps({"name": "updated"})
            result = await tool.run(
                method="PATCH", url_path="/api/projects/123", body=body
            )
            parsed = json.loads(result.output)
            assert parsed["status_code"] == 200
            assert route.calls.last.request.content == body.encode()

    @pytest.mark.asyncio
    async def test_delete_request(self, tool):
        with respx.mock:
            respx.delete("http://test-server:8757/api/projects/123").mock(
                return_value=httpx.Response(204, text="")
            )
            result = await tool.run(method="DELETE", url_path="/api/projects/123")
            parsed = json.loads(result.output)
            assert parsed["status_code"] == 204


class TestJqFilter:
    @pytest.mark.asyncio
    async def test_jq_filter_on_success(self, tool):
        with respx.mock:
            respx.get("http://test-server:8757/api/test").mock(
                return_value=httpx.Response(200, json={"name": "test-value"})
            )
            result = await tool.run(
                method="GET", url_path="/api/test", jq_filter=".name"
            )
            parsed = json.loads(result.output)
            assert parsed["status_code"] == 200
            # jq .text() returns quoted strings
            assert parsed["body"] == '"test-value"'

    @pytest.mark.asyncio
    async def test_jq_filter_extracts_array(self, tool):
        with respx.mock:
            respx.get("http://test-server:8757/api/test").mock(
                return_value=httpx.Response(200, json={"items": [{"id": 1}, {"id": 2}]})
            )
            result = await tool.run(
                method="GET", url_path="/api/test", jq_filter=".items[] | .id"
            )
            parsed = json.loads(result.output)
            assert parsed["status_code"] == 200
            assert parsed["body"] == "1\n2"

    @pytest.mark.asyncio
    async def test_jq_filter_not_applied_on_error(self, tool):
        with respx.mock:
            respx.get("http://test-server:8757/api/test").mock(
                return_value=httpx.Response(404, json={"error": "not found"})
            )
            result = await tool.run(
                method="GET", url_path="/api/test", jq_filter=".name"
            )
            parsed = json.loads(result.output)
            assert parsed["status_code"] == 404
            # Filter should NOT be applied on 4xx
            assert "error" in parsed["body"]

    @pytest.mark.asyncio
    async def test_jq_filter_on_500(self, tool):
        with respx.mock:
            respx.get("http://test-server:8757/api/test").mock(
                return_value=httpx.Response(500, text="Internal Server Error")
            )
            result = await tool.run(
                method="GET", url_path="/api/test", jq_filter=".name"
            )
            parsed = json.loads(result.output)
            assert parsed["status_code"] == 500
            assert parsed["body"] == "Internal Server Error"


class TestJqErrors:
    @pytest.mark.asyncio
    async def test_invalid_jq_syntax(self, tool):
        with respx.mock:
            respx.get("http://test-server:8757/api/test").mock(
                return_value=httpx.Response(200, json={"name": "test"})
            )
            with pytest.raises(ValueError, match="jq filter error"):
                await tool.run(
                    method="GET", url_path="/api/test", jq_filter=".[invalid"
                )

    @pytest.mark.asyncio
    async def test_jq_runtime_error(self, tool):
        """Test jq runtime error (valid syntax that fails during execution)."""
        with respx.mock:
            respx.get("http://test-server:8757/api/test").mock(
                return_value=httpx.Response(200, json={"value": 10})
            )
            # Division by zero is valid jq syntax but fails at runtime
            with pytest.raises(ValueError, match="jq filter error"):
                await tool.run(
                    method="GET", url_path="/api/test", jq_filter=".value / 0"
                )

    @pytest.mark.asyncio
    async def test_jq_on_non_json_response(self, tool):
        with respx.mock:
            respx.get("http://test-server:8757/api/test").mock(
                return_value=httpx.Response(200, text="not json")
            )
            with pytest.raises(ValueError, match="Response is not valid JSON"):
                await tool.run(method="GET", url_path="/api/test", jq_filter=".name")


class TestHttpErrors:
    @pytest.mark.asyncio
    async def test_connection_refused(self, tool):
        with respx.mock:
            respx.get("http://test-server:8757/api/test").mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            with pytest.raises(
                ConnectionError, match=r"^Could not connect to server for /api/test$"
            ):
                await tool.run(method="GET", url_path="/api/test")

    @pytest.mark.asyncio
    async def test_timeout(self, tool):
        with respx.mock:
            respx.get("http://test-server:8757/api/test").mock(
                side_effect=httpx.TimeoutException("timeout")
            )
            with pytest.raises(
                TimeoutError, match="Request to /api/test timed out after 30.0s"
            ):
                await tool.run(method="GET", url_path="/api/test")

    @pytest.mark.asyncio
    async def test_timeout_uses_longer_timeout_for_post(self, tool):
        with respx.mock:
            respx.post("http://test-server:8757/api/test").mock(
                side_effect=httpx.TimeoutException("timeout")
            )
            with pytest.raises(TimeoutError, match="timed out after 300.0s"):
                await tool.run(method="POST", url_path="/api/test", body="{}")


class TestResponseConstruction:
    @pytest.mark.asyncio
    async def test_output_is_valid_json(self, tool):
        with respx.mock:
            respx.get("http://test-server:8757/api/test").mock(
                return_value=httpx.Response(200, json={"data": "value"})
            )
            result = await tool.run(method="GET", url_path="/api/test")
            # Should be valid JSON
            parsed = json.loads(result.output)
            assert "status_code" in parsed
            assert "body" in parsed

    @pytest.mark.asyncio
    async def test_status_code_is_integer(self, tool):
        with respx.mock:
            respx.get("http://test-server:8757/api/test").mock(
                return_value=httpx.Response(201, json={})
            )
            result = await tool.run(method="GET", url_path="/api/test")
            parsed = json.loads(result.output)
            assert isinstance(parsed["status_code"], int)
            assert parsed["status_code"] == 201


class TestBodyTypeHandling:
    @pytest.mark.asyncio
    async def test_body_dict_converted_to_json(self, tool):
        with respx.mock:
            request_body = None

            def capture_request(request):
                nonlocal request_body
                request_body = request.content
                return httpx.Response(201, json={"id": "new-id"})

            respx.post("http://test-server:8757/api/test").mock(
                side_effect=capture_request
            )

            await tool.run(method="POST", url_path="/api/test", body={"name": "test"})
            assert request_body == json.dumps({"name": "test"}).encode()

    @pytest.mark.asyncio
    async def test_body_list_converted_to_json(self, tool):
        with respx.mock:
            request_body = None

            def capture_request(request):
                nonlocal request_body
                request_body = request.content
                return httpx.Response(201, json={"id": "new-id"})

            respx.post("http://test-server:8757/api/test").mock(
                side_effect=capture_request
            )

            await tool.run(method="POST", url_path="/api/test", body=[1, 2, 3])
            assert request_body == json.dumps([1, 2, 3]).encode()

    @pytest.mark.asyncio
    async def test_body_string_passed_through(self, tool):
        with respx.mock:
            request_body = None

            def capture_request(request):
                nonlocal request_body
                request_body = request.content
                return httpx.Response(201, json={"id": "new-id"})

            respx.post("http://test-server:8757/api/test").mock(
                side_effect=capture_request
            )

            await tool.run(method="POST", url_path="/api/test", body='{"name": "test"}')
            assert request_body == b'{"name": "test"}'

    @pytest.mark.asyncio
    async def test_body_none_passed_through(self, tool):
        with respx.mock:
            request_body = None

            def capture_request(request):
                nonlocal request_body
                request_body = request.content
                return httpx.Response(200, json={"items": []})

            respx.get("http://test-server:8757/api/test").mock(
                side_effect=capture_request
            )

            await tool.run(method="GET", url_path="/api/test", body=None)
            assert request_body == b""

    @pytest.mark.asyncio
    async def test_body_empty_dict(self, tool):
        with respx.mock:
            request_body = None

            def capture_request(request):
                nonlocal request_body
                request_body = request.content
                return httpx.Response(201, json={"id": "new-id"})

            respx.post("http://test-server:8757/api/test").mock(
                side_effect=capture_request
            )

            await tool.run(method="POST", url_path="/api/test", body={})
            assert request_body == b"{}"

    @pytest.mark.asyncio
    async def test_body_empty_list(self, tool):
        with respx.mock:
            request_body = None

            def capture_request(request):
                nonlocal request_body
                request_body = request.content
                return httpx.Response(201, json={"id": "new-id"})

            respx.post("http://test-server:8757/api/test").mock(
                side_effect=capture_request
            )

            await tool.run(method="POST", url_path="/api/test", body=[])
            assert request_body == b"[]"


class TestPathAllowlist:
    """The tool reaches the API only.

    The base URL is the server root, which also serves the Kiln web app and
    the server's own doc routes. A mistyped path used to return a web page,
    and thousands of tokens of HTML landed in the agent's context.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url_path",
        [
            "/openapi.json",
            "/docs",
            "/redoc",
            "/scalar",
            "/",
            "/settings",
            "/projects/123/tasks",
            "/_app/immutable/entry/start.js",
            "/apifoo",
            "/api",
        ],
    )
    async def test_non_api_paths_rejected(self, url_path, tool):
        with pytest.raises(ValueError, match="url_path must start with '/api/'"):
            await tool.run(method="GET", url_path=url_path)

    @pytest.mark.asyncio
    async def test_rejection_happens_before_any_request(self, tool):
        with respx.mock:
            route = respx.get("http://test-server:8757/openapi.json").mock(
                return_value=httpx.Response(200, json={"openapi": "3.1.0"})
            )
            with pytest.raises(ValueError):
                await tool.run(method="GET", url_path="/openapi.json")
            assert not route.called

    @pytest.mark.asyncio
    async def test_ping_allowed(self, tool):
        with respx.mock:
            respx.get("http://test-server:8757/ping").mock(
                return_value=httpx.Response(200, json="pong")
            )
            result = await tool.run(method="GET", url_path="/ping")
            assert json.loads(result.output)["status_code"] == 200

    @pytest.mark.asyncio
    async def test_dropped_prefix_gets_a_suggestion(self, tool):
        with pytest.raises(ValueError, match=r"Did you mean '/api/projects/123'\?"):
            await tool.run(method="GET", url_path="/projects/123")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url_path", ["/openapi.json", "/docs", "/"])
    async def test_no_misleading_suggestion_for_non_api_routes(self, url_path, tool):
        with pytest.raises(ValueError) as exc_info:
            await tool.run(method="GET", url_path=url_path)
        assert "Did you mean" not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_missing_slash_error_still_wins(self, tool):
        """The more basic error is reported first."""
        with pytest.raises(ValueError, match="url_path must start with '/'"):
            await tool.run(method="GET", url_path="api/projects")

    @pytest.mark.asyncio
    async def test_path_checked_ignoring_query_string(self, tool):
        """A bad prefix is reported as such, not as a query string problem."""
        with pytest.raises(ValueError, match="url_path must start with '/api/'"):
            await tool.run(method="GET", url_path="/projects?id=1")


class TestQueryParams:
    """Kiln Harness Tuner agents were told to use query_params by the skill file, but
    this copy did not have the argument. They fell back to a '?' in url_path, which
    matches no route and returns the web app's 404 page."""

    @pytest.mark.asyncio
    async def test_query_params_are_sent(self, tool):
        with respx.mock:
            route = respx.get("http://test-server:8757/api/items").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            await tool.run(
                method="GET",
                url_path="/api/items",
                query_params={"page_size": "50"},
            )
            assert route.calls[0].request.url.params["page_size"] == "50"

    @pytest.mark.asyncio
    async def test_list_values_become_repeated_keys(self, tool):
        with respx.mock:
            route = respx.get("http://test-server:8757/api/items").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            await tool.run(
                method="GET",
                url_path="/api/items",
                query_params={"ids": ["a", "b"]},
            )
            assert route.calls[0].request.url.params.get_list("ids") == ["a", "b"]

    @pytest.mark.asyncio
    async def test_query_params_sent_on_post(self, tool):
        with respx.mock:
            route = respx.post("http://test-server:8757/api/items").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            await tool.run(
                method="POST",
                url_path="/api/items",
                body={"a": 1},
                query_params={"dry_run": "true"},
            )
            assert route.calls[0].request.url.params["dry_run"] == "true"

    @pytest.mark.asyncio
    async def test_query_string_in_url_path_rejected(self, tool):
        with pytest.raises(ValueError, match="must not contain a query string"):
            await tool.run(method="GET", url_path="/api/items?page_size=50")

    @pytest.mark.asyncio
    async def test_fragment_in_url_path_rejected(self, tool):
        with pytest.raises(ValueError, match="query string or fragment"):
            await tool.run(method="GET", url_path="/api/items#section")


class TestDotSegments:
    """A prefix check alone is not enough.

    The string validated is not the path sent. httpx resolves dot segments, so
    a path that starts with /api/ can leave as something else entirely.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url_path",
        [
            "/api/../docs",
            "/api/../openapi.json",
            "/api/a/../../openapi.json",
            "/api/./docs",
            # Percent-encoded forms. httpx leaves these encoded in the request
            # target, so whether they escape depends on who decodes them.
            "/api/%2e%2e/docs",
            "/api/%2E%2E/docs",
            "/api/%2e/docs",
            # An encoded slash used to hide a dot segment inside one segment.
            "/api/%2e%2e%2fdocs",
            "/api/%2e%2e%2Fopenapi.json",
        ],
    )
    async def test_dot_segments_rejected(self, url_path, tool):
        with pytest.raises(ValueError, match=re.escape("must not contain '.' or '..'")):
            await tool.run(method="GET", url_path=url_path)

    @pytest.mark.asyncio
    async def test_dot_segment_error_does_not_claim_a_missing_prefix(self, tool):
        """These paths do start with /api/. Saying otherwise reads as nonsense."""
        with pytest.raises(ValueError) as exc_info:
            await tool.run(method="GET", url_path="/api/../docs")
        assert "must start with '/api/'" not in str(exc_info.value)

    @pytest.mark.parametrize(
        "url_path, sent_path",
        [
            ("/api/../docs", "/docs"),
            ("/api/a/../../openapi.json", "/openapi.json"),
        ],
    )
    def test_httpx_really_would_have_sent_these_elsewhere(self, url_path, sent_path):
        """Pins the reason the rule exists, not just the rule.

        If httpx ever stopped resolving dot segments this would fail, and the
        guard above could be reconsidered rather than cargo-culted.
        """
        assert httpx.URL(f"http://test-server:8757{url_path}").path == sent_path

    @pytest.mark.asyncio
    async def test_encoded_slash_inside_a_segment_is_still_allowed(self, tool):
        """Decoding whole paths would split this into segments and over-reject.

        httpx keeps %2F encoded, so it never becomes a separator, and the path
        stays under /api/.
        """
        with respx.mock:
            respx.get("http://test-server:8757/api/%2Foutside").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            result = await tool.run(method="GET", url_path="/api/%2Foutside")
            assert json.loads(result.output)["status_code"] == 200

    @pytest.mark.asyncio
    async def test_dots_inside_a_segment_are_not_dot_segments(self, tool):
        """Only a whole segment of '.' or '..' is traversal."""
        with respx.mock:
            respx.get("http://test-server:8757/api/files/report..txt").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            result = await tool.run(method="GET", url_path="/api/files/report..txt")
            assert json.loads(result.output)["status_code"] == 200


class TestErrorMessageShape:
    @pytest.mark.asyncio
    async def test_prefix_error_names_only_the_rule_and_the_fix(self, tool):
        """No lecture about routes the caller was never asking for."""
        with pytest.raises(ValueError) as exc_info:
            await tool.run(method="GET", url_path="/projects/123")
        message = str(exc_info.value)
        assert "must start with '/api/'" in message
        assert "Did you mean '/api/projects/123'?" in message
        assert "openapi.json" not in message
        assert "/docs" not in message

    @pytest.mark.asyncio
    async def test_prefix_error_names_the_allowed_exceptions(self, tool):
        """The rule alone reads as '/ping is impossible'. Name what works."""
        with pytest.raises(ValueError) as exc_info:
            await tool.run(method="GET", url_path="/projects/123")
        message = str(exc_info.value)
        for allowed in ALLOWED_EXACT_PATHS:
            assert allowed in message

    @pytest.mark.asyncio
    async def test_no_suggestion_for_something_that_looks_like_a_file(self, tool):
        """A dot in the last segment means a filename, not an endpoint."""
        with pytest.raises(ValueError) as exc_info:
            await tool.run(method="GET", url_path="/_app/immutable/start.js")
        assert "Did you mean" not in str(exc_info.value)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url_path", ["/ping/", "/ping//"])
    async def test_near_miss_on_an_exact_path_is_corrected_to_that_path(
        self, url_path, tool
    ):
        """A trailing slash gets '/ping', not '/api/ping/'.

        Prefixing a near miss names a second wrong call rather than the fix.
        """
        with pytest.raises(ValueError) as exc_info:
            await tool.run(method="GET", url_path=url_path)
        message = str(exc_info.value)
        assert "Did you mean '/ping'?" in message
        assert "/api/ping" not in message

    @pytest.mark.asyncio
    async def test_a_genuinely_unknown_path_still_gets_the_prefix_hint(self, tool):
        """'/pings' is not a near miss, it is just wrong. Treat it normally."""
        with pytest.raises(ValueError) as exc_info:
            await tool.run(method="GET", url_path="/pings")
        assert "Did you mean '/api/pings'?" in str(exc_info.value)
