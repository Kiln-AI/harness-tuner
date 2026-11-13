"""The two GETs of the catalogue, once for each run, at the start.

Each is a plain GET of a public file. It sends nothing about the run, the
task or the user: no header beyond httpx's defaults. A fetch never raises. A
source that fails is recorded with a short reason, and the run goes on.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from kiln_harness_tuner.model_catalog.build import build_catalog
from kiln_harness_tuner.model_catalog.schema import Catalog, Source

KILN_URL = "https://remote-config.getkiln.ai/kiln_config_v2.json"
MODELS_DEV_URL = "https://models.dev/api.json"
FETCH_TIMEOUT_SECS = 10.0
MAX_BYTES = 50 * 1024 * 1024
TURNED_OFF = "turned off"


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _get_json(client: httpx.AsyncClient, url: str) -> tuple[Any, str]:
    """(the parsed body, "") or (None, the reason)."""
    body = bytearray()
    try:
        # The client's timeout bounds each read. This bounds the whole fetch,
        # so a slow trickle cannot hold the start of the run.
        async with asyncio.timeout(FETCH_TIMEOUT_SECS):
            async with client.stream("GET", url) as response:
                if not response.is_success:
                    return None, f"HTTP {response.status_code}"
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        return None, f"body over {MAX_BYTES // (1024 * 1024)} MiB"
    except (TimeoutError, httpx.TimeoutException):
        return None, f"timeout after {FETCH_TIMEOUT_SECS:g} s"
    except httpx.HTTPError as exc:
        return None, f"transport error: {type(exc).__name__}"
    except Exception as exc:  # a fetch failure never stops the run
        return None, f"fetch failed: {type(exc).__name__}"
    try:
        return json.loads(body), ""
    except (ValueError, RecursionError):
        return None, "not valid JSON"


def _source(reason: str) -> Source:
    return Source(state="none", reason=reason) if reason else Source(state="ok")


async def fetch_catalog(
    *,
    enabled: bool = True,
    client: httpx.AsyncClient | None = None,
    now: Callable[[], datetime] = _utc_now,
) -> Catalog:
    """Fetch Kiln's published model file and models.dev at the same time, and
    build the catalogue. `enabled=False` (`--no-model-catalog`) sends no
    request."""
    if not enabled:
        return Catalog.unavailable(TURNED_OFF)
    owned = client is None
    http = client or httpx.AsyncClient(
        timeout=FETCH_TIMEOUT_SECS, follow_redirects=True
    )
    try:
        (kiln_raw, kiln_reason), (md_raw, md_reason) = await asyncio.gather(
            _get_json(http, KILN_URL), _get_json(http, MODELS_DEV_URL)
        )
    finally:
        if owned:
            await http.aclose()
    return build_catalog(
        kiln_raw,
        md_raw,
        fetched_at=now().astimezone(UTC).isoformat(timespec="seconds"),
        kiln=_source(kiln_reason),
        models_dev=_source(md_reason),
    )
