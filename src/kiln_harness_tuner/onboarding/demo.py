"""`--demo-project`: a fresh copy of the demo project, imported into Kiln.

The demo is the moderation example's Kiln project. It ships inside the
package, so it always matches the tuner's version, and a clone and an
installed wheel read the same files. Each start copies it into a folder of
its own under Kiln's default projects folder, gives the copy a project ID no
project in Kiln has, and imports it.
"""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Collection
from dataclasses import dataclass
from importlib.resources import as_file, files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kiln_harness_tuner.analysis import KilnApi
from kiln_harness_tuner.kiln import KilnAPIError, KilnError
from kiln_harness_tuner.onboarding.choose import fetch_projects
from kiln_harness_tuner.onboarding.errors import OnboardingError
from kiln_harness_tuner.onboarding.terminal import Terminal

if TYPE_CHECKING:  # flow.py imports this module; a runtime import would cycle.
    from kiln_harness_tuner.onboarding.flow import KilnRunner

DEMO_DIR = "demo_project"
DEMO_PROJECT = "Forum Moderation"
DEMO_TASK_ID = "424242783061"
# The folder Kiln's own "create project" uses: `default_project_path()` in
# kiln_server's project_api.py, `~/Kiln Projects`.
KILN_PROJECTS_DIR = "Kiln Projects"
IMPORT_PATH = "/api/import_project"
PROJECT_FILE = "project.kiln"


@dataclass(frozen=True)
class DemoProject:
    folder: Path
    project_id: str
    task_id: str


def demo_source() -> Traversable:
    """The packaged demo project."""
    return files("kiln_harness_tuner") / DEMO_DIR / DEMO_PROJECT


def demo_fence_root() -> Path:
    """The packaged demo's folder, for the read fence: it holds the eval
    inputs and labels of every split, test included."""
    return Path(str(files("kiln_harness_tuner") / DEMO_DIR))


def claim_folder(parent: Path, name: str) -> Path:
    """A new, empty folder in `parent`: `name`, else `name-1`, `name-2`, …

    A name is claimed by creating its folder, so two starts at once can't
    share one, and nothing that exists is ever overwritten. A file, a folder
    or a dangling link of that name all count as taken."""
    parent.mkdir(parents=True, exist_ok=True)
    n = 0
    while True:
        candidate = parent / (name if n == 0 else f"{name}-{n}")
        try:
            candidate.mkdir()
        except FileExistsError:
            n += 1
            continue
        return candidate


def _random_id() -> str:
    """Kiln's own ID scheme: `generate_model_id` in kiln_ai's basemodel."""
    return str(uuid.uuid4().int)[:12]


def new_project_id(taken: Collection[str]) -> str:
    """A project ID that isn't in `taken`."""
    while True:
        candidate = _random_id()
        if candidate not in taken:
            return candidate


def write_copy(source: Traversable, dest: Path, *, project_id: str, name: str) -> None:
    """Copy the project at `source` into `dest`, then give the copy's
    `project.kiln` its own ID and name."""
    with as_file(source) as src:
        # An installer that byte-compiles the wheel leaves a `__pycache__`
        # beside the eval's scorer. It is no part of the project.
        shutil.copytree(
            src,
            dest,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    project_file = dest / PROJECT_FILE
    project = json.loads(project_file.read_text(encoding="utf-8"))
    project["id"] = project_id
    project["name"] = name
    project_file.write_text(
        json.dumps(project, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _kiln_says(exc: KilnError) -> str:
    """Kiln's own message for a refusal: the `detail` of its answer, else
    the error as the client words it."""
    if isinstance(exc, KilnAPIError) and isinstance(exc.body, dict):
        detail = exc.body.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return str(exc)


async def _import(kiln: KilnApi, folder: Path) -> Any:
    # `trusted=true`: the demo's eval runs code, the scorer that ships with it.
    return await kiln.call(
        "POST",
        IMPORT_PATH,
        {"trusted": "true", "project_path": str(folder / PROJECT_FILE)},
    )


def make_demo(*, terminal: Terminal, run: KilnRunner, home: Path) -> DemoProject:
    """Copy the packaged demo into a new folder under `home`'s Kiln
    projects folder, and import the copy into Kiln. Raises
    `OnboardingError` when the copy or the import fails, after deleting the
    folder this call made: it holds nothing but the copy."""
    taken = {project.id for project in run(fetch_projects)}
    parent = home / KILN_PROJECTS_DIR
    try:
        folder = claim_folder(parent, DEMO_PROJECT)
    except OSError as exc:
        raise OnboardingError(
            f"Couldn't make a folder for the demo project in {parent}: {exc}"
        ) from exc
    project_id = new_project_id(taken)
    try:
        write_copy(demo_source(), folder, project_id=project_id, name=folder.name)
    except OSError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise OnboardingError(
            f"Couldn't copy the demo project to {folder}: {exc}\n"
            "The partial copy was deleted."
        ) from exc
    except BaseException:
        # Ctrl-C mid-copy. A copy stopped before `project.kiln` is rewritten
        # is a whole project that still has the packaged demo's ID and name.
        shutil.rmtree(folder, ignore_errors=True)
        raise
    try:
        imported = run(lambda kiln: _import(kiln, folder))
    except KilnError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise OnboardingError(
            f"Kiln didn't import the demo project: {_kiln_says(exc)}\n"
            f"The copy at {folder} was deleted."
        ) from exc
    kiln_id = imported.get("id") if isinstance(imported, dict) else None
    if isinstance(kiln_id, str) and kiln_id.strip():
        project_id = kiln_id.strip()
    terminal.say(
        f"Demo project copied to {folder}\n"
        f"and imported into Kiln as project {project_id} (task {DEMO_TASK_ID})."
    )
    return DemoProject(folder=folder, project_id=project_id, task_id=DEMO_TASK_ID)
