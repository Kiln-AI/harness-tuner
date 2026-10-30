"""The path model: one repository root, derived once.

Every `__file__`-relative walk in this tree must land inside the repository.
A walk calibrated for a package that lives deeper resolves outside it —
`cli.REPO_ROOT` would resolve to `$HOME`. These tests stop that: a walk
that leaves the package may exist only where a console script needs it, and the
two that do exist must agree on the answer.

**What the scan cannot see**, stated because a guard that overclaims is the
defect this project keeps finding. It is single-statement and syntactic: it
reads the source segment of each statement naming `__file__` and counts the
ways of going up one directory that appear in that same segment. A derivation
through an intermediate variable — `_HERE = Path(__file__).resolve()` on one
line, `_ROOT = _HERE.parent.parent.parent` on the next — is never discovered,
and a spelling it does not know, such as
`os.sep.join(__file__.split(os.sep)[:-3])`, scores 0. It covers the shapes
this tree writes (`parents[n]`, `.parent` chains, nested `os.path.dirname`)
and is a guard against drift, not against someone working around it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import kiln_harness_tuner
from kiln_harness_tuner.cli import REPO_ROOT

PACKAGE_ROOT = Path(kiln_harness_tuner.__file__).resolve().parent

# The only module allowed to walk out of the package: `kiln-harness-tuner`'s
# composition root (cli.py), which resolves the roots everything below it is
# handed. Every other module receives the paths it needs.
# `dispatch/claude_cli.py` also reads `__file__`, but for a package-internal
# resource (`agents/`), which never leaves the package and is not an exception
# to anything.
ROOT_DERIVING_MODULES = {"cli.py", "install_agents.py"}

_PARENTS_INDEX = re.compile(r"\.parents\[(\d+)\]")
# One step up, in each spelling that reaches a parent directory. `dirname` is
# here because `pathlib` is a convention, not a fence: three nested
# `os.path.dirname(...)` calls around `__file__` climb out of the package just
# as surely, and a guard that only knows `pathlib` reports success on them.
_UP_ONE = re.compile(r"\.parent(?!s)|\bdirname\s*\(")


def _walk_up(segment: str) -> int:
    """How many directories above its own the source segment reaches.

    A chain of k single steps is k - 1, because the first one only reaches the
    file's own directory; `parents[n]` is n on top of any steps beside it."""
    steps = len(_UP_ONE.findall(segment))
    index = _PARENTS_INDEX.search(segment)
    if index:
        return int(index.group(1)) + steps
    return max(steps - 1, 0)


def _module_paths() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _names_file(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Name) and child.id == "__file__"
        for child in ast.walk(node)
    )


def _innermost_statements(tree: ast.AST) -> list[ast.stmt]:
    """The statements that name `__file__` and contain no statement that does.

    An enclosing `FunctionDef`'s source segment contains `__file__` too, so
    without this a derivation inside a function is counted twice — once at its
    own statement and once at every statement wrapping it."""
    carriers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.stmt) and _names_file(node)
    ]
    return [
        node
        for node in carriers
        if not any(
            other is not node and isinstance(other, ast.stmt) and _names_file(other)
            for other in ast.walk(node)
        )
    ]


def _file_derivations() -> dict[str, list[tuple[str, int]]]:
    """Every statement in the package that derives a path from `__file__`,
    keyed by the module's package-relative path, with how far it walks up."""
    out: dict[str, list[tuple[str, int]]] = {}
    for path in _module_paths():
        source = path.read_text()
        if "__file__" not in source:
            continue
        for node in _innermost_statements(ast.parse(source)):
            segment = ast.get_source_segment(source, node) or ""
            key = path.relative_to(PACKAGE_ROOT).as_posix()
            out.setdefault(key, []).append((segment, _walk_up(segment)))
    return out


def _escapes_the_package(module: str, walk_up: int) -> bool:
    """A walk escapes when it reaches above the package's own directory."""
    depth_below_package = len(Path(module).parts) - 1
    return walk_up > depth_below_package


def test_the_package_derives_a_path_from_file_somewhere():
    # The guard below is a set comparison, and a set comparison over an empty
    # discovery passes for the wrong reason.
    derivations = _file_derivations()
    assert derivations, "no __file__ derivation found — the discovery is broken"


def test_only_a_declared_root_module_walks_out_of_the_package():
    escaping = {
        module
        for module, found in _file_derivations().items()
        for _, walk_up in found
        if _escapes_the_package(module, walk_up)
    }
    assert escaping == ROOT_DERIVING_MODULES, (
        "a module outside the declared set walks above the package, or a"
        " declared one stopped: every other module is given its"
        " paths rather than letting it compute one"
    )


def test_a_package_internal_derivation_is_not_an_exception():
    # The module that reads `__file__` for a resource inside the package must
    # stay inside it — `agents/` is package data, and a walk one step further
    # reaches `src/`.
    derivations = _file_derivations()
    for module in ("dispatch/claude_cli.py",):
        found = derivations.get(module)
        # `.get`, not `[...]`: a subscript raises KeyError before the message
        # below can say which module stopped deriving one, which is the whole
        # information this assertion exists to carry.
        assert found, f"{module} no longer derives a path from __file__"
        for segment, walk_up in found:
            assert not _escapes_the_package(module, walk_up), segment


def test_repo_root_is_the_repository_root():
    assert (REPO_ROOT / "pyproject.toml").is_file()
    assert (REPO_ROOT / ".agents" / "claude" / ".mcp.json").is_file()
    assert (REPO_ROOT / ".agents" / "skills").is_dir()
    # `src/kiln_harness_tuner` sits under it, which is the walk cli.py performs.
    assert PACKAGE_ROOT.parent.parent == REPO_ROOT
