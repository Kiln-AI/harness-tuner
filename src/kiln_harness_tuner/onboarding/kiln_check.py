"""The first start-up check: Kiln answers, and it has the APIs the tuner needs.

Kiln has no version endpoint, so "new enough" is read from its OpenAPI
document: each API the tuner needs is a path or a schema field there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.kiln import KilnAPIError, KilnError, KilnTransportError
from kiln_harness_tuner.onboarding.terminal import Problem

KILN_DOWNLOAD_URL = "https://kiln.tech"
PING_TIMEOUT_SECS = 5.0
OPENAPI_TIMEOUT_SECS = 10.0

UNREADABLE_API_LIST_WARNING = (
    "couldn't read Kiln's API list, so its version wasn't checked."
)


@dataclass(frozen=True)
class RequiredApi:
    """An API the tuner needs: present when `path` is in the document's
    `paths`, or when schema `schema` has the property `field`."""

    label: str
    path: str | None = None
    schema: str | None = None
    field: str | None = None


REQUIRED_APIS = (
    RequiredApi("the memory API", path="/api/projects/{project_id}/memories"),
    RequiredApi("the eval-job API", path="/api/jobs/evals/run"),
    RequiredApi(
        "run-config provenance", schema="CreateTaskRunConfigRequest", field="provenance"
    ),
)


@dataclass(frozen=True)
class KilnCheck:
    """The check's outcome. `warning` is set only when the check passed but
    could not read the API list."""

    problem: Problem | None = None
    warning: str | None = None


def _dict_at(node: Any, *keys: str) -> dict[str, Any]:
    """`node[k1][k2]…` when every level is a dict, else an empty dict."""
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else {}


def _is_present(api: RequiredApi, openapi: dict[str, Any]) -> bool:
    if api.path is not None:
        return api.path in _dict_at(openapi, "paths")
    assert api.schema is not None and api.field is not None
    schema_properties = _dict_at(
        openapi, "components", "schemas", api.schema, "properties"
    )
    return api.field in schema_properties


def missing_apis(openapi: dict[str, Any]) -> list[str]:
    """The labels of the `REQUIRED_APIS` the document lacks, in that order."""
    return [api.label for api in REQUIRED_APIS if not _is_present(api, openapi)]


def _version_note(openapi: dict[str, Any]) -> str:
    # Packaged Kiln builds can report `unknown`, which says nothing.
    version = _dict_at(openapi, "info").get("version")
    if not isinstance(version, str) or version.strip().lower() in ("", "unknown"):
        return ""
    return f" (version {version.strip()})"


def _kiln_problem(message: str) -> Problem:
    return Problem(message, open_url=KILN_DOWNLOAD_URL, open_label="kiln.tech")


def _not_running(host: str) -> Problem:
    return _kiln_problem(
        f"Kiln isn't running at {host}.\n"
        "Start the Kiln app. If you don't have it, download it from"
        f" {KILN_DOWNLOAD_URL}.\n"
        "If Kiln runs on another port, pass --kiln-host."
    )


def _not_kiln(host: str, answer: str) -> Problem:
    return _kiln_problem(
        f"Something answered at {host}, but it isn't Kiln"
        f" (GET /ping returned {answer}).\n"
        "Check --kiln-host, or start the Kiln app."
    )


def _too_old(
    host: str, openapi: dict[str, Any], missing: list[str], prog: str
) -> Problem:
    return _kiln_problem(
        f"Kiln at {host}{_version_note(openapi)} is too old for {prog}.\n"
        f"It's missing: {', '.join(missing)}.\n"
        "Update Kiln: in the app, open Settings → Check for update, or download"
        f" the latest from {KILN_DOWNLOAD_URL}.\n"
        "Then restart it."
    )


async def check_kiln(kiln: KilnApi, host: str, *, prog: str) -> KilnCheck:
    """Ping Kiln once, then check its API list for `REQUIRED_APIS`. An API
    list that can't be read is a warning, not a problem: a later call to a
    missing API fails with its own error. `prog` names the command in the
    too-old message."""
    try:
        pong = await kiln.call(
            "GET", "/ping", timeout_secs=PING_TIMEOUT_SECS, retries=False
        )
    except KilnTransportError:
        return KilnCheck(problem=_not_running(host))
    except KilnAPIError as exc:
        return KilnCheck(problem=_not_kiln(host, f"HTTP {exc.status_code}"))
    if pong != "pong":
        return KilnCheck(problem=_not_kiln(host, "an unexpected answer"))

    try:
        openapi = await kiln.call(
            "GET", "/openapi.json", timeout_secs=OPENAPI_TIMEOUT_SECS, retries=False
        )
    except KilnError:
        openapi = None
    if not isinstance(openapi, dict):
        return KilnCheck(warning=UNREADABLE_API_LIST_WARNING)
    missing = missing_apis(openapi)
    if missing:
        return KilnCheck(problem=_too_old(host, openapi, missing, prog))
    return KilnCheck()
