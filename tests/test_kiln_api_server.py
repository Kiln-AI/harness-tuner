"""Tests for MCP server wrapper."""

import json
import re
from unittest.mock import patch

import httpx
import pytest
import respx

from kiln_api_mcp.server import create_server, main


class TestCreateServer:
    def test_returns_fastmcp_instance(self):
        server = create_server()
        assert server is not None
        assert hasattr(server, "run")

    @pytest.mark.asyncio
    async def test_default_api_base_url(self):
        """Verify default API base URL is used by checking the request target."""
        with respx.mock:
            route = respx.get("http://localhost:8757/api/test").mock(
                return_value=httpx.Response(200, json={})
            )
            server = create_server()
            tool_handler = server._tool_manager._tools["call_kiln_api"].fn
            await tool_handler(method="GET", url_path="/api/test")
            assert route.called

    @pytest.mark.asyncio
    async def test_custom_api_base_url(self):
        """Verify custom API base URL is used by checking the request target."""
        with respx.mock:
            route = respx.get("http://custom:9999/api/test").mock(
                return_value=httpx.Response(200, json={})
            )
            server = create_server(api_base_url="http://custom:9999")
            tool_handler = server._tool_manager._tools["call_kiln_api"].fn
            await tool_handler(method="GET", url_path="/api/test")
            assert route.called


class TestToolRegistration:
    @pytest.mark.asyncio
    async def test_tool_is_registered(self):
        server = create_server()
        # FastMCP stores tools internally
        # Check that the tool handler exists
        tools = server._tool_manager._tools
        assert "call_kiln_api" in tools

    @pytest.mark.asyncio
    async def test_tool_delegates_to_kiln_api_tool(self):
        with respx.mock:
            respx.get("http://localhost:8757/api/projects").mock(
                return_value=httpx.Response(200, json={"projects": []})
            )

            server = create_server()
            # Get the tool handler
            tool_handler = server._tool_manager._tools["call_kiln_api"].fn

            result = await tool_handler(method="GET", url_path="/api/projects")
            parsed = json.loads(result)
            assert parsed["status_code"] == 200


class TestCliArguments:
    def test_default_arguments(self):
        with patch("sys.argv", ["server.py"]):
            with patch("kiln_api_mcp.server.create_server") as mock_create:
                with patch.object(mock_create.return_value, "run"):
                    main()
                    mock_create.assert_called_once_with(
                        api_base_url="http://localhost:8757"
                    )

    def test_custom_api_base_url(self):
        with patch("sys.argv", ["server.py", "--api-base-url", "http://custom:9999"]):
            with patch("kiln_api_mcp.server.create_server") as mock_create:
                with patch.object(mock_create.return_value, "run"):
                    main()
                    mock_create.assert_called_once_with(
                        api_base_url="http://custom:9999"
                    )


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_full_request_flow(self):
        """Test that the MCP tool correctly delegates to KilnApiCallTool."""
        with respx.mock:
            respx.post("http://localhost:8757/api/projects").mock(
                return_value=httpx.Response(201, json={"id": "new-id"})
            )

            server = create_server()
            tool_handler = server._tool_manager._tools["call_kiln_api"].fn

            result = await tool_handler(
                method="POST",
                url_path="/api/projects",
                body=json.dumps({"name": "test"}),
            )
            parsed = json.loads(result)
            assert parsed["status_code"] == 201
            assert "new-id" in parsed["body"]

    @pytest.mark.asyncio
    async def test_jq_filter_through_mcp(self):
        """Test that jq filter works through MCP layer."""
        with respx.mock:
            respx.get("http://localhost:8757/api/test").mock(
                return_value=httpx.Response(200, json={"items": [{"id": 1}, {"id": 2}]})
            )

            server = create_server()
            tool_handler = server._tool_manager._tools["call_kiln_api"].fn

            result = await tool_handler(
                method="GET",
                url_path="/api/test",
                jq_filter=".items | length",
            )
            parsed = json.loads(result)
            assert parsed["status_code"] == 200
            assert parsed["body"] == "2"


class TestBodyTypeHandling:
    @pytest.mark.asyncio
    async def test_body_dict_converted_to_json(self):
        with respx.mock:
            request_body = None

            def capture_request(request):
                nonlocal request_body
                request_body = request.content
                return httpx.Response(201, json={"id": "new-id"})

            respx.post("http://localhost:8757/api/test").mock(
                side_effect=capture_request
            )

            server = create_server()
            tool_handler = server._tool_manager._tools["call_kiln_api"].fn

            await tool_handler(
                method="POST", url_path="/api/test", body={"name": "test"}
            )
            assert request_body == json.dumps({"name": "test"}).encode()

    @pytest.mark.asyncio
    async def test_body_list_converted_to_json(self):
        with respx.mock:
            request_body = None

            def capture_request(request):
                nonlocal request_body
                request_body = request.content
                return httpx.Response(201, json={"id": "new-id"})

            respx.post("http://localhost:8757/api/test").mock(
                side_effect=capture_request
            )

            server = create_server()
            tool_handler = server._tool_manager._tools["call_kiln_api"].fn

            await tool_handler(method="POST", url_path="/api/test", body=[1, 2, 3])
            assert request_body == json.dumps([1, 2, 3]).encode()

    @pytest.mark.asyncio
    async def test_body_string_passed_through(self):
        with respx.mock:
            request_body = None

            def capture_request(request):
                nonlocal request_body
                request_body = request.content
                return httpx.Response(201, json={"id": "new-id"})

            respx.post("http://localhost:8757/api/test").mock(
                side_effect=capture_request
            )

            server = create_server()
            tool_handler = server._tool_manager._tools["call_kiln_api"].fn

            await tool_handler(
                method="POST", url_path="/api/test", body='{"name": "test"}'
            )
            assert request_body == b'{"name": "test"}'

    @pytest.mark.asyncio
    async def test_body_none_passed_through(self):
        with respx.mock:
            request_body = None

            def capture_request(request):
                nonlocal request_body
                request_body = request.content
                return httpx.Response(200, json={"items": []})

            respx.get("http://localhost:8757/api/test").mock(
                side_effect=capture_request
            )

            server = create_server()
            tool_handler = server._tool_manager._tools["call_kiln_api"].fn

            await tool_handler(method="GET", url_path="/api/test", body=None)
            assert request_body == b""

    @pytest.mark.asyncio
    async def test_body_empty_dict(self):
        with respx.mock:
            request_body = None

            def capture_request(request):
                nonlocal request_body
                request_body = request.content
                return httpx.Response(201, json={"id": "new-id"})

            respx.post("http://localhost:8757/api/test").mock(
                side_effect=capture_request
            )

            server = create_server()
            tool_handler = server._tool_manager._tools["call_kiln_api"].fn

            await tool_handler(method="POST", url_path="/api/test", body={})
            assert request_body == b"{}"

    @pytest.mark.asyncio
    async def test_body_empty_list(self):
        with respx.mock:
            request_body = None

            def capture_request(request):
                nonlocal request_body
                request_body = request.content
                return httpx.Response(201, json={"id": "new-id"})

            respx.post("http://localhost:8757/api/test").mock(
                side_effect=capture_request
            )

            server = create_server()
            tool_handler = server._tool_manager._tools["call_kiln_api"].fn

            await tool_handler(method="POST", url_path="/api/test", body=[])
            assert request_body == b"[]"


class TestQueryParamsThroughMcp:
    """The argument must be reachable from MCP, not just from the tool.

    The Kiln Harness Tuner skill file tells agents to pass query_params. Before this
    change the MCP surface had no such argument, so agents fell back to a '?' in
    url_path and got the web app's 404 page instead of an API response.
    """

    @pytest.mark.asyncio
    async def test_query_params_argument_is_exposed(self):
        server = create_server()
        tool = server._tool_manager._tools["call_kiln_api"]
        assert "query_params" in tool.parameters["properties"]

    @pytest.mark.asyncio
    async def test_query_params_reach_the_request(self):
        with respx.mock:
            route = respx.get("http://localhost:8757/api/items").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )

            server = create_server()
            tool_handler = server._tool_manager._tools["call_kiln_api"].fn

            await tool_handler(
                method="GET",
                url_path="/api/items",
                query_params={"page_size": "50"},
            )
            assert route.calls[0].request.url.params["page_size"] == "50"

    @pytest.mark.asyncio
    async def test_non_api_path_rejected_through_mcp(self):
        server = create_server()
        tool_handler = server._tool_manager._tools["call_kiln_api"].fn

        with pytest.raises(ValueError, match="url_path must start with '/api/'"):
            await tool_handler(method="GET", url_path="/openapi.json")

    @pytest.mark.asyncio
    async def test_dot_segment_rejected_through_mcp(self):
        """The guard that matters most, checked at the surface agents call."""
        server = create_server()
        tool_handler = server._tool_manager._tools["call_kiln_api"].fn

        with pytest.raises(ValueError, match=re.escape("must not contain '.' or '..'")):
            await tool_handler(method="GET", url_path="/api/a/../../openapi.json")
