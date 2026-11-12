"""The properties of the documentation a reviewer cannot hold by reading.

The flags have their own guard: `test_cli_reference.py` holds `docs/cli.md`
to the argparse definitions of the public commands.

**Every internal link resolves.** A document that links to a file which does
not exist is broken, and the mistake this repository is most likely to make is
linking forward to a document a later phase writes. The link check covers
`docs/` **and the documents at the repository root**: `README.md` is almost
entirely links into `docs/`, and a misspelling there is invisible to a check
that reads `docs/` alone and broken for every reader.

**The install sample names every console script.** `docs/install.md` shows
the line `uv tool install .` prints. A script added to `[project.scripts]`
and not to that line is a stale document.

**No retired command or install form comes back.** The commands lost their
`kiln-` prefix, a run stopped needing a checkout, and the loop agents'
instructions left `.agents/`. A document that still names an old form tells
the reader to type something that no longer works, and a reader copies
commands. This check reads every document a user or a contributor meets.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from kiln_harness_tuner.knowledge.load import resolve_link

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPO_ROOT / "docs"

# Inline markdown links. Reference-style links are not used in this tree; if
# one is ever added, it is invisible here rather than wrongly reported, which
# is why `test_docs_tree_is_not_empty` guards the whole file.
LINK_RE = re.compile(r"\]\(([^)\s]+)\)")

EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "//")

# The documents outside `docs/` that readers and agents meet first. Named, not
# globbed: a glob over the root would silently pick up whatever a future phase
# drops there, and this list is a claim about which files are load-bearing.
ROOT_DOCS = (
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "src/kiln_harness_tuner/agent_files/AGENTS.md",
)


# The maintenance skill of the knowledge system, and the backlog it adds to.
# Globbed, because a template or a reference file added to the skill is a
# document a coding agent follows.
MAINTENANCE_GLOB = ".agents/skills/knowledge-maintenance/**/*.md"
BACKLOG = "specs/backlog.md"

# A template links documents of a knowledge tree by a placeholder id, such as
# `../levers/{{LEVER_ID}}.md`. Such a link names no file until an author fills
# it, and the template test (`test_knowledge.py`) checks the filled document.
PLACEHOLDER = "{{"


# The user-facing documents outside the link-checked set. The retired-form
# check reads these as well.
USER_DOCS = (
    ".github/CONTRIBUTING.md",
    "examples/moderation/README.md",
)

# Forms the documents must not name any more, each with what replaced it.
# The tuner's own file names keep the old prefix (`kiln-harness-tuner.log`,
# `kiln-harness-tuner-<run_id>.md`), and so do the skill names
# (`kiln-harness-tuner-gateway`), so the bare name is matched only where no
# `-` and no `.log` follows it.
RETIRED_FORMS = {
    "the old command and repository name `kiln-harness-tuner` (now"
    " `harness-tuner`)": re.compile(r"kiln-harness-tuner(?![\w-]|\.log\b)"),
    "an old `kiln-harness-tuner-…` command (now `harness-tuner-…`)": re.compile(
        r"kiln-harness-tuner-(?:install-agents|gateway-mcp|gateway-hook"
        r"|skill-installer-mcp)\b"
    ),
    "`uv run --project` (a run starts its MCP servers as `<python> -P -m`)": (
        re.compile(r"uv run --project")
    ),
    "`uv tool install .` (now `uv tool install harness-tuner`)": re.compile(
        r"uv tool install \.(?![\w/])"
    ),
    "`.agents/AGENTS.md` (now `src/kiln_harness_tuner/agent_files/AGENTS.md`)": (
        re.compile(r"\.agents/AGENTS\.md")
    ),
}


def doc_files() -> list[Path]:
    """The documentation tree."""
    return sorted(DOCS_ROOT.rglob("*.md"))


def maintenance_files() -> list[Path]:
    """The maintenance skill and the backlog."""
    return sorted(REPO_ROOT.glob(MAINTENANCE_GLOB)) + [REPO_ROOT / BACKLOG]


def linked_files() -> list[Path]:
    """Every document whose links are checked: the tree, plus ROOT_DOCS, the
    maintenance skill and the backlog."""
    return [REPO_ROOT / name for name in ROOT_DOCS] + maintenance_files() + doc_files()


def user_docs() -> list[Path]:
    """Every document the retired-form check reads: the link-checked set,
    plus USER_DOCS."""
    return linked_files() + [REPO_ROOT / name for name in USER_DOCS]


def test_docs_tree_is_not_empty():
    """The guard that stops the other checks passing vacuously.

    A docs test over an empty tree reports success forever: the link check
    finds no broken link in no file."""
    assert DOCS_ROOT.is_dir(), f"{DOCS_ROOT} does not exist"
    assert len(doc_files()) >= 8, (
        "the docs tree has fewer files than the reference set this test was"
        f" written for: {[p.name for p in doc_files()]}"
    )


def test_the_root_documents_are_link_checked():
    """The link check's own coverage, asserted rather than assumed.

    `test_every_internal_link_resolves` is parametrised over `linked_files()`.
    If that helper ever narrows back to the docs tree, every root-document
    parameter disappears and the suite goes green with fewer cases — a
    coverage loss that reads exactly like a passing run."""
    # Resolved paths, never basenames: a future `docs/README.md` would satisfy
    # a basename comparison with the root pair dropped from the set, which is
    # the exact coverage loss this test exists to catch.
    checked = {path.resolve() for path in linked_files()}
    missing = [
        name for name in ROOT_DOCS if (REPO_ROOT / name).resolve() not in checked
    ]
    assert not missing, (
        f"root documents outside the link check: {missing}. README.md is"
        " almost entirely links into docs/, and nothing else reads them."
    )
    for name in ROOT_DOCS:
        assert (REPO_ROOT / name).is_file(), f"{name} does not exist"
    for name in (
        ".agents/skills/knowledge-maintenance/SKILL.md",
        ".agents/skills/knowledge-maintenance/templates/technique.md",
        BACKLOG,
    ):
        assert (REPO_ROOT / name).is_file(), f"{name} does not exist"
        assert (REPO_ROOT / name).resolve() in checked, f"{name} is not link-checked"


def resolves_inside_the_repository(base: Path, target: str) -> bool:
    """True when `target`, read from a file in `base`, names an existing path
    inside this repository, spelled EXACTLY as it is on disk.

    Two failures, both of which are green for the author and broken for every
    reader.

    `Path.exists()` asks the filesystem, and this one answers
    case-insensitively — so `](ARCHITECTURE.md)` passes on the machine the
    document was written on and 404s on a case-sensitive one. Each component
    is checked against its parent's real directory listing instead, which is
    the only spelling a web renderer or a Linux checkout will accept.

    And a target that climbs OUT of the repository resolves here and exists
    for nobody else. A `../..` link to a file next to the checkout works for
    its author and for no other reader."""
    # The knowledge checks resolve their links with the same rule.
    return resolve_link(base, target, REPO_ROOT) is not None


@pytest.mark.parametrize(
    "path", linked_files(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix()
)
def test_every_internal_link_resolves(path: Path):
    """Relative links point at files that exist, spelled as they are on disk.

    An `#anchor` suffix is stripped before resolution: this checks that the
    file is there, not that the heading is."""
    broken: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for target in LINK_RE.findall(line):
            if target.startswith(EXTERNAL_PREFIXES) or target.startswith("#"):
                continue
            if PLACEHOLDER in target:
                continue
            bare = target.split("#", 1)[0]
            if not resolves_inside_the_repository(path.parent, bare):
                broken.append(
                    f"line {lineno}: {target} -> "
                    f"{(path.parent / bare).resolve()} (not found, spelled"
                    " differently on disk, or outside this repository)"
                )
    assert not broken, f"{path.relative_to(REPO_ROOT)} has broken links:\n" + "\n".join(
        broken
    )


def test_the_retired_form_check_reads_every_user_document():
    """The scan's coverage, asserted for the same reason as the link check's:
    a narrowed set passes with fewer cases and reads as green."""
    scanned = {path.resolve() for path in user_docs()}
    for name in (
        *ROOT_DOCS,
        *USER_DOCS,
        ".github/CONTRIBUTING.md",
        "examples/moderation/README.md",
    ):
        assert (REPO_ROOT / name).is_file(), f"{name} does not exist"
        assert (REPO_ROOT / name).resolve() in scanned, f"{name} is not scanned"


@pytest.mark.parametrize(
    "path", user_docs(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix()
)
def test_docs_name_no_retired_command(path: Path):
    hits = [
        f"line {lineno}: {what}: {line.strip()}"
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for what, pattern in RETIRED_FORMS.items()
        if pattern.search(line)
    ]
    assert not hits, f"{path.relative_to(REPO_ROOT)} names retired forms:\n" + (
        "\n".join(hits)
    )


# The summary line `uv tool install .` prints, as `docs/install.md` shows it.
INSTALLED_LINE_RE = re.compile(r"^Installed (\d+) executables: (.+)$", re.MULTILINE)


def test_install_doc_lists_every_console_script():
    """`docs/install.md` shows the line `uv tool install .` prints. `uv` names
    every `[project.scripts]` entry on it, sorted, so a script added to
    `pyproject.toml` and not to the sample is a stale document."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    scripts = tomllib.loads(pyproject)["project"]["scripts"]
    install = (DOCS_ROOT / "install.md").read_text(encoding="utf-8")
    lines = INSTALLED_LINE_RE.findall(install)
    assert len(lines) == 1, f"expected one `Installed N executables:` line: {lines}"
    count, names = lines[0]
    assert names.split(", ") == sorted(scripts)
    assert int(count) == len(scripts)
