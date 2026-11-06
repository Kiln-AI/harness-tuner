"""`--demo-project`'s copy: the packaged demo, the folder it goes to, the
fresh project ID, the copy itself, and the import into Kiln."""

import json
import re
import shutil
from pathlib import Path

import pytest
from onboarding_fakes import (
    PROJECT_ID,
    RouteKiln,
    ScriptedTerminal,
    import_project_route,
    project,
    runner_for,
)

import kiln_harness_tuner
from kiln_harness_tuner.kiln import KilnAPIError, KilnTransportError
from kiln_harness_tuner.onboarding import demo
from kiln_harness_tuner.onboarding.demo import (
    DEMO_TASK_ID,
    IMPORT_PATH,
    DemoProject,
    claim_folder,
    demo_fence_root,
    demo_source,
    make_demo,
    new_project_id,
    write_copy,
)
from kiln_harness_tuner.onboarding.errors import OnboardingError

FRESH_ID = "731954460212"
NAME = "Forum Moderation"


def script_ids(monkeypatch, *ids: str) -> None:
    """The random IDs `new_project_id` draws, in order."""
    monkeypatch.setattr(demo, "_random_id", iter(ids).__next__)


def files_under(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def packaged_demo() -> Path:
    return Path(str(demo_source()))


def demo_kiln(*listed: dict) -> RouteKiln:
    """A Kiln that lists `listed` and imports like Kiln does."""
    kiln = RouteKiln({("GET", "/api/projects"): list(listed)})
    kiln.routes[("POST", IMPORT_PATH)] = import_project_route(kiln)
    return kiln


def demo_in(home: Path, kiln: RouteKiln, terminal=None) -> DemoProject:
    return make_demo(
        terminal=terminal or ScriptedTerminal(), run=runner_for(kiln), home=home
    )


def projects_folder(home: Path) -> Path:
    return home / "Kiln Projects"


def test_demo_source_is_the_packaged_demo():
    source = demo_source()
    project_file = json.loads((source / "project.kiln").read_text(encoding="utf-8"))
    assert (project_file["id"], project_file["name"]) == (PROJECT_ID, NAME)
    task_file = source / "tasks" / f"{DEMO_TASK_ID} - {NAME}" / "task.kiln"
    assert json.loads(task_file.read_text(encoding="utf-8"))["id"] == DEMO_TASK_ID


def test_the_fence_root_is_the_package_folder_that_holds_the_demo():
    package = Path(kiln_harness_tuner.__file__).parent
    assert demo_fence_root() == package / "demo_project"
    assert packaged_demo().parent == demo_fence_root()


def test_claim_folder_takes_the_name_when_it_is_free(tmp_path):
    parent = projects_folder(tmp_path)
    folder = claim_folder(parent, NAME)
    assert folder == parent / NAME
    assert folder.is_dir() and list(folder.iterdir()) == []


def test_claim_folder_adds_1_then_2(tmp_path):
    parent = projects_folder(tmp_path)
    (parent / NAME).mkdir(parents=True)
    assert claim_folder(parent, NAME) == parent / f"{NAME}-1"
    assert claim_folder(parent, NAME) == parent / f"{NAME}-2"


def test_an_existing_file_counts_as_taken(tmp_path):
    parent = projects_folder(tmp_path)
    parent.mkdir()
    (parent / NAME).write_text("mine")
    assert claim_folder(parent, NAME) == parent / f"{NAME}-1"
    assert (parent / NAME).read_text() == "mine"


def test_a_dangling_link_counts_as_taken(tmp_path):
    parent = projects_folder(tmp_path)
    parent.mkdir()
    (parent / NAME).symlink_to(tmp_path / "gone")
    assert claim_folder(parent, NAME) == parent / f"{NAME}-1"
    assert not (tmp_path / "gone").exists()


def test_new_project_id_avoids_taken_ids(monkeypatch):
    script_ids(monkeypatch, PROJECT_ID, "118230554411", FRESH_ID)
    assert new_project_id({PROJECT_ID, "118230554411"}) == FRESH_ID


def test_new_project_id_is_12_digits():
    assert re.fullmatch(r"\d{12}", new_project_id(set()))


def test_write_copy_copies_every_file_and_sets_the_id_and_name(tmp_path):
    dest = tmp_path / "copy"
    write_copy(demo_source(), dest, project_id=FRESH_ID, name=f"{NAME}-1")
    source = packaged_demo()
    assert files_under(dest) == files_under(source)
    for relative in files_under(source) - {Path("project.kiln")}:
        assert (dest / relative).read_bytes() == (source / relative).read_bytes()
    original = json.loads((source / "project.kiln").read_text(encoding="utf-8"))
    text = (dest / "project.kiln").read_text(encoding="utf-8")
    assert json.loads(text) == {**original, "id": FRESH_ID, "name": f"{NAME}-1"}
    assert text.endswith("}\n")


def test_write_copy_leaves_out_pycache(tmp_path):
    source = tmp_path / "source"
    config = source / "tasks" / "1 - t" / "evals" / "configs"
    (config / "__pycache__").mkdir(parents=True)
    (config / "__pycache__" / "scorer.cpython-313.pyc").write_bytes(b"\0")
    (config / "scorer.py").write_text("def score(): ...\n")
    (source / "project.kiln").write_text(json.dumps({"id": "1", "name": "p"}))
    dest = tmp_path / "copy"
    write_copy(source, dest, project_id=FRESH_ID, name=NAME)
    assert files_under(dest) == {
        Path("project.kiln"),
        Path("tasks/1 - t/evals/configs/scorer.py"),
    }


def test_make_demo_copies_imports_and_says_where(tmp_path, monkeypatch):
    script_ids(monkeypatch, FRESH_ID)
    kiln = demo_kiln(project())
    terminal = ScriptedTerminal()
    made = demo_in(tmp_path, kiln, terminal)
    folder = projects_folder(tmp_path) / NAME
    assert made == DemoProject(folder, FRESH_ID, DEMO_TASK_ID)
    assert kiln.paths() == ["/api/projects", IMPORT_PATH]
    imported = kiln.calls[-1]
    assert imported.method == "POST"
    assert imported.query == {
        "trusted": "true",
        "project_path": str(folder / "project.kiln"),
    }
    assert terminal.out == [
        f"Demo project copied to {folder}\n"
        f"and imported into Kiln as project {FRESH_ID} (task {DEMO_TASK_ID})."
    ]
    assert terminal.prompts == []
    copied = json.loads((folder / "project.kiln").read_text(encoding="utf-8"))
    assert (copied["id"], copied["name"]) == (FRESH_ID, NAME)


def test_a_second_demo_goes_to_dash_1_with_its_own_id_and_name(tmp_path):
    kiln = demo_kiln()
    first = demo_in(tmp_path, kiln)
    second = demo_in(tmp_path, kiln)
    assert first.folder.name == NAME
    assert second.folder == projects_folder(tmp_path) / f"{NAME}-1"
    assert second.project_id != first.project_id
    copied = json.loads((second.folder / "project.kiln").read_text(encoding="utf-8"))
    assert (copied["id"], copied["name"]) == (second.project_id, f"{NAME}-1")


def test_the_fresh_id_is_not_one_kiln_has(tmp_path, monkeypatch):
    script_ids(monkeypatch, PROJECT_ID, FRESH_ID)
    assert demo_in(tmp_path, demo_kiln(project())).project_id == FRESH_ID


def test_kilns_project_id_is_used_when_its_answer_has_one(tmp_path, monkeypatch):
    script_ids(monkeypatch, FRESH_ID)
    kiln = demo_kiln()
    kiln.routes[("POST", IMPORT_PATH)] = {"id": "118230554411"}
    assert demo_in(tmp_path, kiln).project_id == "118230554411"


@pytest.mark.parametrize("answer", [None, {}, {"id": ""}, "imported"])
def test_our_id_is_used_when_kilns_answer_has_none(tmp_path, monkeypatch, answer):
    script_ids(monkeypatch, FRESH_ID)
    kiln = demo_kiln()
    kiln.routes[("POST", IMPORT_PATH)] = lambda _query, _body: answer
    assert demo_in(tmp_path, kiln).project_id == FRESH_ID


def test_a_refused_import_removes_the_folder_and_says_why(tmp_path):
    kiln = demo_kiln()
    kiln.routes[("POST", IMPORT_PATH)] = KilnAPIError(
        "POST", IMPORT_PATH, 400, {"detail": "Failed to load project."}
    )
    with pytest.raises(OnboardingError) as error:
        demo_in(tmp_path, kiln)
    folder = projects_folder(tmp_path) / NAME
    assert str(error.value) == (
        "Kiln didn't import the demo project: Failed to load project.\n"
        f"The copy at {folder} was deleted."
    )
    assert not folder.exists()
    assert list(projects_folder(tmp_path).iterdir()) == []


def test_a_refusal_with_no_detail_names_the_error(tmp_path):
    kiln = demo_kiln()
    refusal = KilnAPIError("POST", IMPORT_PATH, 422, {"detail": [{"msg": "bad"}]})
    kiln.routes[("POST", IMPORT_PATH)] = refusal
    with pytest.raises(OnboardingError) as error:
        demo_in(tmp_path, kiln)
    assert str(error.value).splitlines()[0] == (
        f"Kiln didn't import the demo project: {refusal}"
    )


def test_an_import_that_never_answers_removes_the_folder(tmp_path):
    kiln = demo_kiln()
    kiln.routes[("POST", IMPORT_PATH)] = KilnTransportError(
        "POST /api/import_project failed after 1 attempts: ReadTimeout"
    )
    with pytest.raises(OnboardingError, match="ReadTimeout"):
        demo_in(tmp_path, kiln)
    assert not (projects_folder(tmp_path) / NAME).exists()


def test_a_failed_copy_removes_the_partial_folder(tmp_path, monkeypatch):
    def disk_full(_src, dest, **_kwargs):
        (Path(dest) / "project.kiln").write_text("{")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copytree", disk_full)
    kiln = demo_kiln()
    with pytest.raises(OnboardingError) as error:
        demo_in(tmp_path, kiln)
    folder = projects_folder(tmp_path) / NAME
    assert str(error.value) == (
        f"Couldn't copy the demo project to {folder}:"
        " [Errno 28] No space left on device\n"
        "The partial copy was deleted."
    )
    assert not folder.exists()
    assert IMPORT_PATH not in kiln.paths()


def test_ctrl_c_during_the_copy_removes_the_folder(tmp_path, monkeypatch):
    """Stopped after the files are in but before `project.kiln` is
    rewritten, the folder would be a whole project with the packaged demo's
    ID and name."""

    def copied_then_interrupted(src, dest, **_kwargs):
        shutil.copy(Path(src) / "project.kiln", Path(dest) / "project.kiln")
        raise KeyboardInterrupt

    monkeypatch.setattr(shutil, "copytree", copied_then_interrupted)
    kiln = demo_kiln()
    with pytest.raises(KeyboardInterrupt):
        demo_in(tmp_path, kiln)
    assert list(projects_folder(tmp_path).iterdir()) == []
    assert IMPORT_PATH not in kiln.paths()


def test_a_projects_folder_that_cant_be_made_is_an_error_that_names_it(tmp_path):
    projects_folder(tmp_path).write_text("not a folder")
    kiln = demo_kiln()
    with pytest.raises(OnboardingError) as error:
        demo_in(tmp_path, kiln)
    assert str(error.value).startswith(
        f"Couldn't make a folder for the demo project in {projects_folder(tmp_path)}:"
    )
    assert IMPORT_PATH not in kiln.paths()
