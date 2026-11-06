"""The two properties of the documentation a reviewer cannot hold by reading.

**Every CLI flag is documented exactly once.** A flag nobody documents is a
flag nobody uses, and a flag documented twice is two descriptions that will
disagree. The set of flag reference entries in the docs tree must equal the set
of options `cli.build_parser()` defines.

**Every internal link resolves.** A document that links to a file which does
not exist is broken, and the mistake this repository is most likely to make is
linking forward to a document a later phase writes.

The two checks read different sets of files, on purpose. The link check covers
`docs/` **and the two documents at the repository root**: `README.md` is almost
entirely links into `docs/`, and a misspelling there is invisible to a check
that reads `docs/` alone and broken for every reader.

The flag check stays over `docs/` alone, and the reason is the documentation
plan, not the entry pattern: the flag reference lives in `docs/`, one row per
flag, and `README.md` is an entry document that points at it. Widening the scan
to the root would not *catch* anything a reader wants caught — it would make a
root flag table legal, and the design says there should not be one. (It would
also be enforceable in the wrong direction: a root table row is a perfectly good
flag entry as far as `FLAG_ENTRY_RE` is concerned, so the scan cannot tell a
second home from a first.)

A **flag reference entry** is a markdown table row whose FIRST cell is exactly
one backticked long flag:

    | `--budget-usd` | A cost ceiling for the run… |

Prose mentions and example command lines are deliberately NOT entries. That is
what lets a quickstart print a whole command without claiming to document its
flags, and it is what lets this file say `--no-recon` in a sentence.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

import pytest

from kiln_harness_tuner.cli import build_parser

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPO_ROOT / "docs"

# The first cell of a markdown table row, when that cell is nothing but one
# backticked long flag. Anchored at the row's leading pipe so a flag appearing
# in a LATER cell — a "see also `--gate-agent`" in a description — is not an
# entry.
#
# The character class is every character argparse allows in a long option, not
# every character today's flags happen to use: with `[a-z0-9-]` alone, a flag
# named `--dry_run` could never be documented, because the row documenting it
# would not parse as an entry and the test would demand the row that was
# already there. `FLAG_SPELLING_RE` below is the same set, and it turns a flag
# this pattern cannot express into a message that says so instead of an
# unsatisfiable failure.
FLAG_ENTRY_RE = re.compile(r"^\|\s*`(--[A-Za-z0-9][A-Za-z0-9_.-]*)`\s*\|")
FLAG_SPELLING_RE = re.compile(r"^--[A-Za-z0-9][A-Za-z0-9_.-]*$")

# Inline markdown links. Reference-style links are not used in this tree; if
# one is ever added, it is invisible here rather than wrongly reported, which
# is why `test_docs_tree_is_not_empty` guards the whole file.
LINK_RE = re.compile(r"\]\(([^)\s]+)\)")

EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "//")

# The documents outside `docs/` that readers and agents meet first. Named, not
# globbed: a glob over the root would silently pick up whatever a future phase
# drops there, and this list is a claim about which files are load-bearing.
ROOT_DOCS = ("README.md", "src/kiln_harness_tuner/agent_files/AGENTS.md")


def doc_files() -> list[Path]:
    """The documentation tree — the surface the FLAG check owns."""
    return sorted(DOCS_ROOT.rglob("*.md"))


def linked_files() -> list[Path]:
    """Every document whose links are checked: the tree, plus ROOT_DOCS."""
    return [REPO_ROOT / name for name in ROOT_DOCS] + doc_files()


def parser_flags() -> set[str]:
    """Every long option `harness-tuner` accepts, minus argparse's own help."""
    flags: set[str] = set()
    for action in build_parser()._actions:
        flags.update(opt for opt in action.option_strings if opt.startswith("--"))
    return flags - {"--help"}


def flag_entries() -> list[tuple[str, Path, int]]:
    """`(flag, file, line number)` for every flag reference entry, in order."""
    found: list[tuple[str, Path, int]] = []
    for path in doc_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = FLAG_ENTRY_RE.match(line)
            if match:
                found.append((match.group(1), path, lineno))
    return found


def test_docs_tree_is_not_empty():
    """The guard that stops the other three passing vacuously.

    A docs test over an empty tree reports success forever. Both the flag
    check and the link check are set comparisons, and an empty left side
    satisfies one of them for free."""
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


def test_every_cli_flag_is_documented_exactly_once():
    entries = flag_entries()
    documented = [flag for flag, _, _ in entries]
    expected = parser_flags()

    # A flag the entry pattern cannot express could never be documented, and
    # the failure would read as a missing row that is in fact already there.
    # Say which it is, and say that the pattern is the thing to widen.
    inexpressible = sorted(f for f in expected if not FLAG_SPELLING_RE.match(f))
    assert not inexpressible, (
        f"CLI flags this test cannot recognise in a table row: {inexpressible}."
        " Widen FLAG_ENTRY_RE and FLAG_SPELLING_RE to cover them — as written,"
        " no document can satisfy this test for such a flag."
    )

    missing = sorted(expected - set(documented))
    unknown = sorted(set(documented) - expected)
    duplicated = sorted({f for f in documented if documented.count(f) > 1})

    where = {
        flag: [
            f"{path.relative_to(REPO_ROOT)}:{lineno}"
            for f, path, lineno in entries
            if f == flag
        ]
        for flag in duplicated
    }
    assert not missing, (
        f"undocumented CLI flags: {missing}. Add one table row per flag to a"
        " document under docs/, with the flag alone in the first cell."
    )
    assert not unknown, (
        f"documented flags `cli.build_parser()` does not define: {unknown}."
        " A renamed or deleted flag leaves its row behind."
    )
    assert not duplicated, (
        f"CLI flags documented more than once: {where}. Exactly one document"
        " describes each flag."
    )


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
    current = base.resolve()
    for part in PurePosixPath(target).parts:
        if part == ".":
            continue
        if part == "..":
            current = current.parent
            continue
        if part.startswith("/"):
            return False  # an absolute link cannot resolve from a document
        try:
            names = os.listdir(current)
        except OSError:
            return False
        if part not in names:
            return False
        current = current / part
    return current.is_relative_to(REPO_ROOT)


@pytest.mark.parametrize("path", linked_files(), ids=lambda p: p.name)
def test_every_internal_link_resolves(path: Path):
    """Relative links point at files that exist, spelled as they are on disk.

    An `#anchor` suffix is stripped before resolution: this checks that the
    file is there, not that the heading is."""
    broken: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for target in LINK_RE.findall(line):
            if target.startswith(EXTERNAL_PREFIXES) or target.startswith("#"):
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
