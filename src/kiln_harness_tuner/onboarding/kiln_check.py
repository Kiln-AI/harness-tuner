"""The first start-up check: Kiln answers, and it has the APIs the tuner needs.

Kiln has no version endpoint, so "new enough" is read from its OpenAPI
document: each API the tuner needs is a path or a schema field there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.kiln import KilnAPIError, KilnError, KilnTransportError
from kiln_harness_tuner.onboarding import kiln_urls
from kiln_harness_tuner.onboarding.terminal import (
    Action,
    ActionKind,
    Problem,
    english_list,
)

PING_TIMEOUT_SECS = 5.0
OPENAPI_TIMEOUT_SECS = 10.0

UNREADABLE_API_LIST_WARNING = (
    "Couldn't read Kiln's API list, so its version wasn't checked."
)
DOWNLOAD_ACTION = Action(
    "d", "Open the download page", ActionKind.OPEN, url=kiln_urls.KILN_DOWNLOAD_URL
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
    could not read the API list. `version` is what Kiln reports, when it
    says something."""

    problem: Problem | None = None
    warning: str | None = None
    version: str | None = None


def kiln_passed_text(version: str | None, host: str) -> str:
    """The step line's text for a Kiln that passed."""
    return f"{version} at {host}" if version else f"running at {host}"


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


def _version(openapi: dict[str, Any]) -> str | None:
    # Packaged Kiln builds can report `unknown`, which says nothing.
    version = _dict_at(openapi, "info").get("version")
    if not isinstance(version, str) or version.strip().lower() in ("", "unknown"):
        return None
    return version.strip()


def _not_running(host: str) -> Problem:
    return Problem(
        f"Kiln isn't running at {host}",
        (
            "The tuner works through the Kiln app, so Kiln has to be open on this"
            " computer.",
            f"  1. Install Kiln if you don't have it: {kiln_urls.KILN_DOWNLOAD_URL}",
            "  2. Open the Kiln app and wait for its window to appear.",
            "Kiln on another port? Exit and pass --kiln-host http://localhost:<port>.",
        ),
        actions=(DOWNLOAD_ACTION,),
        retry_label="Check again (Kiln is open now)",
    )


def _not_kiln(host: str, answer: str) -> Problem:
    return Problem(
        f"Something other than Kiln is answering at {host}",
        (
            f"GET /ping returned {answer}, so this isn't the Kiln app.",
            "  1. If Kiln is running on another port, exit and pass --kiln-host"
            " with its address.",
            "  2. Otherwise close the program using this port, then open the Kiln app.",
        ),
        actions=(DOWNLOAD_ACTION,),
    )


def _too_old(host: str, version: str | None, missing: list[str]) -> Problem:
    kiln = f"Kiln {version}" if version else "This Kiln"
    return Problem(
        f"{kiln} is too old for the tuner",
        (
            f"The tuner needs {english_list(missing)}, which this Kiln doesn't have.",
            "  1. Update Kiln: in the app, open Settings → Check for update,",
            f"     or download the latest version from {kiln_urls.KILN_DOWNLOAD_URL}.",
            "  2. Quit and reopen Kiln so the update takes effect.",
        ),
        actions=(
            Action(
                "u",
                "Open Kiln's update page",
                ActionKind.OPEN,
                url=kiln_urls.check_for_update(host),
            ),
            DOWNLOAD_ACTION,
        ),
        retry_label="Check again (I've updated and reopened Kiln)",
    )


async def check_kiln(kiln: KilnApi, host: str) -> KilnCheck:
    """Ping Kiln once, then check its API list for `REQUIRED_APIS`. An API
    list that can't be read is a warning, not a problem: a later call to a
    missing API fails with its own error."""
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
    version = _version(openapi)
    missing = missing_apis(openapi)
    if missing:
        return KilnCheck(problem=_too_old(host, version, missing), version=version)
    return KilnCheck(version=version)
