"""The repository's license, in the three places that have to agree.

`LICENSE` is the grant. `pyproject.toml` is what a built wheel tells a package
index about it. `.github/CONTRIBUTING.md` and `.github/CLA.md` are what a
contributor agrees to. A change to one that the others do not follow is a
licensing defect a reviewer reads past.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOLDER = "Chesterfield Laboratories Inc."


def _project_table() -> dict:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)["project"]


def test_license_file_is_mit_for_the_whole_repository():
    text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License\n")
    assert re.search(rf"^Copyright \(c\) \d{{4}} {re.escape(HOLDER)}$", text, re.M)
    assert "Permission is hereby granted, free of charge" in text
    # Kiln's own LICENSE files scope MIT to one directory. This one does not.
    assert "applies only to" not in text


def test_pyproject_declares_mit_and_ships_the_license_file():
    project = _project_table()
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert "License :: OSI Approved :: MIT License" in project["classifiers"]
    for name in project["license-files"]:
        assert (REPO_ROOT / name).is_file(), f"license-files names missing {name}"


def test_contributing_links_the_license_and_the_cla():
    # CONTRIBUTING is outside the docs link check (`tests/test_docs.py`), so
    # its two license links are resolved here.
    github = REPO_ROOT / ".github"
    contributing = (github / "CONTRIBUTING.md").read_text(encoding="utf-8")
    section = contributing.split("\n## Licence\n", 1)[1]
    for target in ("../LICENSE", "CLA.md"):
        assert f"]({target})" in section, f"the Licence section does not link {target}"
        assert (github / target).is_file(), f"{target} does not exist"
    cla = (github / "CLA.md").read_text(encoding="utf-8")
    assert cla.startswith("# Contributor License Agreement\n")
    assert HOLDER in cla
