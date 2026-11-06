"""Links into the Kiln web UI, for the fix lines the start-up checks print.

The routes come from Kiln's web UI source, not from its API, so a Kiln
rename breaks a link here and nothing else. `host` is the Kiln host with no
trailing slash.
"""

from __future__ import annotations


def create_project(host: str) -> str:
    return f"{host}/settings/create_project"


def create_task(host: str, project_id: str) -> str:
    return f"{host}/settings/create_task/{project_id}"


def run_configs(host: str, project_id: str, task_id: str) -> str:
    return f"{host}/optimize/{project_id}/{task_id}"


def evals(host: str, project_id: str, task_id: str) -> str:
    return f"{host}/specs/{project_id}/{task_id}"


def providers(host: str) -> str:
    return f"{host}/settings/providers"
