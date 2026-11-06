"""The agent-dispatch seam (docs/architecture.md section 2): the loop talks to
`AgentDispatcher`, and exactly one module knows which implementation it got.

`ClaudeCliDispatcher` is the prototype implementation — a `claude -p`
subprocess per call. It is the piece a different agent CLI's dispatcher
replaces, and the replacement is only possible while nothing but the
composition root names it.
The orchestrator, the analyzer and the agents must reach it through
`dispatch.base.AgentDispatcher` and never import the concrete class, or
`docs/agent-cli.md` promises something the code does not hold.
"""

from __future__ import annotations

import ast
from pathlib import Path

import kiln_harness_tuner

PACKAGE_ROOT = Path(kiln_harness_tuner.__file__).resolve().parent
DISPATCH_DIR = PACKAGE_ROOT / "dispatch"

# `cli.py` builds the `Ctx` — it constructs the dispatcher and hands it down,
# which is what a composition root is for. There is no second one.
COMPOSITION_ROOT = "cli.py"
# One dispatcher per agent CLI, the same rule for each.
AGENT_CLI_DISPATCHERS = frozenset(
    {"ClaudeCliDispatcher", "CodexCliDispatcher", "OpenCodeCliDispatcher"}
)
CONCRETE_MODULES = frozenset(
    {
        "kiln_harness_tuner.dispatch.claude_cli",
        "kiln_harness_tuner.dispatch.codex_cli",
        "kiln_harness_tuner.dispatch.opencode_cli",
    }
)


def _modules_outside_dispatch() -> list[Path]:
    return sorted(
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if DISPATCH_DIR not in path.parents and path != DISPATCH_DIR
    )


def _label(path: Path) -> str:
    return path.relative_to(PACKAGE_ROOT).as_posix()


def _imported_names(path: Path) -> dict[str, set[str]]:
    """`{module: {names imported from it}}` for every import in the file.

    Read off the AST rather than by substring, so a mention in a comment or a
    docstring is not an import and an import split over lines still is one."""
    out: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.setdefault(node.module, set()).update(
                alias.name for alias in node.names
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.setdefault(alias.name, set())
    return out


def test_the_scan_sees_the_modules_it_is_meant_to():
    # A boundary test over an empty file list is a boundary test that passes
    # whatever the code does.
    found = {_label(path) for path in _modules_outside_dispatch()}
    assert {"cli.py", "orchestrator.py", "gateway/server.py"} <= found


def test_only_the_composition_root_names_an_agent_cli_dispatcher():
    for dispatcher in sorted(AGENT_CLI_DISPATCHERS):
        naming = {
            _label(path)
            for path in _modules_outside_dispatch()
            if any(
                dispatcher in names
                for module, names in _imported_names(path).items()
                if module.startswith("kiln_harness_tuner.dispatch")
            )
        }
        assert naming == {COMPOSITION_ROOT}, (
            f"{dispatcher} is a CLI-specific implementation;"
            f" only {COMPOSITION_ROOT} may construct it"
        )


def test_every_other_module_reaches_the_seam_through_the_protocol():
    offenders = {
        _label(path)
        for path in _modules_outside_dispatch()
        if _label(path) != COMPOSITION_ROOT
        and CONCRETE_MODULES & set(_imported_names(path))
    }
    assert not offenders, (
        f"{sorted(offenders)} import a dispatcher module directly — the seam is"
        " kiln_harness_tuner.dispatch.base.AgentDispatcher"
    )


def test_the_orchestrator_holds_the_protocol_and_not_the_implementation():
    # The one module the rule above exists for, asserted by name: it is the
    # loop itself, and a scan that silently stopped covering it would still
    # pass the set comparisons above.
    imported = _imported_names(PACKAGE_ROOT / "orchestrator.py")
    assert "AgentDispatcher" in imported["kiln_harness_tuner.dispatch.base"]
    assert not CONCRETE_MODULES & set(imported)
