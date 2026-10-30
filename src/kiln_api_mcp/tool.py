"""Kiln API Call Tool - makes HTTP requests to the Kiln API server."""

import json
from typing import Any
from urllib.parse import unquote

import httpx
import jq
from kiln_ai.tools.base_tool import KilnTool, ToolCallResult

# The base URL is the server root, and that server also hosts the web app and
# FastAPI's own doc routes. So a mistyped path does not 404 as JSON — it returns
# a web page, and thousands of tokens of HTML land in the model's context. Every
# Kiln API route lives under /api/ apart from /ping, so hold the tool to that.
API_PATH_PREFIX = "/api/"
ALLOWED_EXACT_PATHS = frozenset({"/ping"})
# Paths that a "did you mean /api<path>?" hint would only mislead on.
_NON_API_PATHS = frozenset({"/", "/docs", "/redoc", "/scalar"})


def _path_without_query(url_path: str) -> str:
    """The path portion alone.

    The query string is ignored by the path checks: a url_path carrying one is
    rejected by its own check, and stripping it first keeps those errors about
    the path.
    """
    return url_path.split("?", 1)[0].split("#", 1)[0]


def _has_dot_segment(path_only: str) -> bool:
    """True when any segment is '.' or '..', encoded or not.

    A prefix check alone is not enough, because the string we validate is not
    the path that gets sent. httpx resolves dot segments, so '/api/../docs'
    leaves as '/docs' and '/api/a/../../openapi.json' leaves as
    '/openapi.json' — both past a guard that only looked at the '/api/' at the
    front.

    Segments are decoded one at a time rather than decoding the whole path,
    then split again on any slash the decoding revealed. That catches an
    encoded '%2e%2e', and an encoded slash used to hide one ('%2e%2e%2f'),
    without treating an encoded slash as a separator in its own right — httpx
    leaves those encoded, so '/api/x%2Fy' is one segment and stays under
    '/api/'.
    """
    return any(
        part in {".", ".."}
        for segment in path_only.split("/")
        for part in unquote(segment).split("/")
    )


def _is_allowed_path(url_path: str) -> bool:
    """True when url_path addresses the Kiln REST API rather than the web app."""
    path_only = _path_without_query(url_path)
    if _has_dot_segment(path_only):
        return False
    return path_only in ALLOWED_EXACT_PATHS or path_only.startswith(API_PATH_PREFIX)


def _disallowed_path_message(url_path: str) -> str:
    path_only = _path_without_query(url_path)
    if _has_dot_segment(path_only):
        # A separate message: "must start with /api/" reads as nonsense for a
        # path that plainly does start with it.
        return (
            "url_path must not contain '.' or '..' path segments, encoded or "
            f"not — they resolve elsewhere before the request is sent. Got: "
            f"'{url_path}'. Write the full path. Correct paths are in the "
            "endpoint documentation."
        )

    # A near miss on an exact allowed path — a trailing slash, say — is
    # corrected to that path. Prefixing it instead would name a second wrong
    # call: '/api/ping/' is no more a route than '/ping/' is.
    near_miss = path_only.rstrip("/")
    if near_miss in ALLOWED_EXACT_PATHS:
        hint = f" Did you mean '{near_miss}'?"
    else:
        # Suggest the prefixed form only where it could plausibly be the fix.
        # A dot in the final segment means a filename, not an endpoint, and
        # "/api/openapi.json" is no more callable than "/openapi.json".
        looks_like_a_file = "." in path_only.rsplit("/", 1)[-1]
        suggest = (
            not path_only.startswith("/api")
            and path_only not in _NON_API_PATHS
            and not looks_like_a_file
        )
        hint = f" Did you mean '/api{path_only}'?" if suggest else ""
    # Name the exceptions. The rule alone reads as "/ping is impossible",
    # which would stop a caller retrying the one path that does work.
    exceptions = ", ".join(f"'{path}'" for path in sorted(ALLOWED_EXACT_PATHS))
    return (
        f"url_path must start with '{API_PATH_PREFIX}', or be exactly "
        f"{exceptions}. Got: '{url_path}'.{hint} "
        "Correct paths are in the endpoint documentation."
    )


class KilnApiCallTool(KilnTool):
    """Tool for making HTTP requests to the Kiln API server."""

    def __init__(self, api_base_url: str = "http://localhost:8757"):
        self._api_base_url = api_base_url
        super().__init__(
            tool_id="kiln_tool::call_kiln_api",  # type: ignore[arg-type]
            name="call_kiln_api",
            description=self._build_description(),
            parameters_schema=self._build_parameters_schema(),
        )

    @staticmethod
    def _build_description() -> str:
        return """Call the Kiln REST API. Makes an HTTP request and returns JSON with status_code and body.

Endpoint paths, request schemas, response fields, and jq filters are defined in per-endpoint documentation — not here. Load the endpoint doc before calling."""

    @staticmethod
    def _build_parameters_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PATCH", "DELETE"],
                    "description": "HTTP method for the API call",
                },
                "url_path": {
                    "type": "string",
                    "description": "API path with no query string — pass query args via query_params. Correct paths are in the endpoint documentation.",
                },
                "query_params": {
                    "type": "object",
                    "description": "Query string params. Values are strings or arrays of strings (arrays become repeated keys, e.g. ?ids=a&ids=b). Required and optional params are listed in the endpoint doc.",
                    "additionalProperties": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                    },
                },
                "body": {
                    "description": "Request body for POST/PATCH. JSON string, object, or array — auto-serialized. Schema is in the endpoint doc.",
                },
                "jq_filter": {
                    "type": "string",
                    "description": "Optional jq expression applied to successful (2xx) responses.",
                },
            },
            "required": ["method", "url_path"],
        }

    async def run(  # type: ignore[override]
        self,
        method: str,
        url_path: str,
        body: str | dict | list | None = None,
        query_params: dict[str, str | list[str]] | None = None,
        jq_filter: str | None = None,
        context=None,
    ) -> ToolCallResult:
        body_str: str | None = None
        if isinstance(body, (dict, list)):
            body_str = json.dumps(body)
        elif isinstance(body, str):
            body_str = body

        # 1. Validate inputs
        method = method.upper()
        allowed_methods = {"GET", "POST", "PATCH", "DELETE"}
        if method not in allowed_methods:
            raise ValueError(
                f"Invalid method '{method}'. Must be one of: {', '.join(sorted(allowed_methods))}"
            )

        if not url_path.startswith("/"):
            raise ValueError(f"url_path must start with '/', got: {url_path}")

        if not _is_allowed_path(url_path):
            raise ValueError(_disallowed_path_message(url_path))

        if "?" in url_path or "#" in url_path:
            raise ValueError(
                "url_path must not contain a query string or fragment ('?' or '#'). "
                "Pass query args via query_params."
            )

        if body_str is not None and method in {"GET", "DELETE"}:
            raise ValueError(f"body parameter not allowed with {method} method")

        # 2. Build full URL
        full_url = f"{self._api_base_url}{url_path}"

        # 3. Make HTTP request
        headers = {"Content-Type": "application/json"}
        # GET/DELETE: 30s, POST/PATCH: 5 minutes (may upload large data)
        timeout_seconds = 30.0 if method in {"GET", "DELETE"} else 300.0
        timeout = httpx.Timeout(timeout_seconds)

        async with httpx.AsyncClient(timeout=timeout) as client:
            request_funcs = {
                "GET": lambda: client.get(
                    full_url, headers=headers, params=query_params
                ),
                "POST": lambda: client.post(
                    full_url, headers=headers, content=body_str, params=query_params
                ),
                "PATCH": lambda: client.patch(
                    full_url, headers=headers, content=body_str, params=query_params
                ),
                "DELETE": lambda: client.delete(
                    full_url, headers=headers, params=query_params
                ),
            }
            try:
                response = await request_funcs[method]()
            except httpx.TimeoutException as exc:
                raise TimeoutError(
                    f"Request to {url_path} timed out after {timeout_seconds}s"
                ) from exc
            except httpx.ConnectError as exc:
                raise ConnectionError(
                    f"Could not connect to server for {url_path}"
                ) from exc

        # 4. Build response
        status_code = response.status_code
        response_text = response.text

        if jq_filter and 200 <= status_code < 300:
            # Apply jq filter on successful responses
            try:
                parsed_json = json.loads(response_text)
            except json.JSONDecodeError as e:
                raise ValueError(f"Response is not valid JSON: {e}") from e

            try:
                compiled = jq.compile(jq_filter)
                filtered = compiled.input_value(parsed_json).text()
                response_body = filtered if filtered is not None else ""
            except Exception as e:
                raise ValueError(f"jq filter error: {e}") from e
        else:
            # Return raw body for non-2xx or when no filter
            response_body = response_text

        result = {"status_code": status_code, "body": response_body}
        return ToolCallResult(output=json.dumps(result))
