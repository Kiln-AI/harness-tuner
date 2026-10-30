"""MCP Server wrapper for KilnApiCallTool."""

import argparse
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from kiln_api_mcp.tool import KilnApiCallTool


def create_server(api_base_url: str = "http://localhost:8757") -> FastMCP:
    """Create an MCP server exposing the Kiln API tool.

    Args:
        api_base_url: Base URL for the Kiln API server.

    Returns:
        FastMCP server instance.
    """
    mcp = FastMCP("Kiln API")
    tool_instance = KilnApiCallTool(api_base_url=api_base_url)

    @mcp.tool(
        name="call_kiln_api",
        description=KilnApiCallTool._build_description(),
    )
    async def call_kiln_api(
        method: Annotated[
            str, Field(description="HTTP method: GET, POST, PATCH, or DELETE.")
        ],
        url_path: Annotated[
            str,
            Field(
                description="API path with no query string — pass query args via query_params. Correct paths are in the endpoint documentation."
            ),
        ],
        body: Annotated[
            str | dict | list | None,
            Field(
                description="Request body for POST/PATCH. Schema is in the endpoint doc."
            ),
        ] = None,
        query_params: Annotated[
            dict[str, str | list[str]] | None,
            Field(
                description="Query string params. Values are strings or arrays of strings (arrays become repeated keys, e.g. ?ids=a&ids=b). Required and optional params are listed in the endpoint doc."
            ),
        ] = None,
        jq_filter: Annotated[
            str | None,
            Field(
                description="Optional jq expression applied to successful (2xx) responses."
            ),
        ] = None,
    ) -> str:
        """Call the Kiln API server."""
        result = await tool_instance.run(
            method=method,
            url_path=url_path,
            body=body,
            query_params=query_params,
            jq_filter=jq_filter,
        )
        return result.output

    return mcp


def main():
    """CLI entry point for the MCP server."""
    parser = argparse.ArgumentParser(description="Kiln API MCP Server")
    parser.add_argument(
        "--api-base-url",
        default="http://localhost:8757",
        help="Base URL for the Kiln API (default: http://localhost:8757)",
    )
    args = parser.parse_args()

    server = create_server(api_base_url=args.api_base_url)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
