"""Every pre-flight failure names the command or the document that fixes it.

The first user of this repository has none of the runtime
prerequisites set up — no studio server, no installed console scripts, maybe no
`.mcp.json` on the path the loop looks at — so the error that meets them has to
carry the next step and not only the diagnosis. The marker is a line beginning
`fix: `, which is mechanical enough to check here and readable enough to be the
last line a user sees.

The check is static, over the pre-flight modules, because a
message is text and every one of these raise sites is an inline literal. Two
live raises are exercised underneath it, so the contract is held on what a user
actually receives rather than only on what the source says.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import kiln_harness_tuner
from kiln_harness_tuner.cli import RunLockError, _acquire_run_lock

PACKAGE_ROOT = Path(kiln_harness_tuner.__file__).resolve().parent

# The modules that fail before any paid work starts, and the errors they raise
# to do it. `DispatchTimeoutError` and `ProviderQuotaError` are not here: they
# happen mid-run, against a machine that was already proven working, and there
# is no operator command that resolves them.
PREFLIGHT_MODULES = (
    "cli.py",
    "dispatch/claude_cli.py",
    "quarantine.py",
    "gateway/trainset.py",
)
PREFLIGHT_ERRORS = {
    "ConfigRenderError",
    "RunLockError",
    "PreflightError",
    "QuarantineError",
    "TrainSetError",
}

FIX_MARKER = "\nfix: "

# Measured from the tree, and stated so the guard cannot narrow in silence: a
# raise site that is deleted or renamed out of the set fails here instead of
# quietly leaving this test asserting less than it did.
EXPECTED_SITES = 21


def _message_literals(source: str) -> list[tuple[str, str]]:
    """`(error class, the literal text of its message)` for every pre-flight
    raise in `source`.

    Only the literal parts are recovered: an f-string's placeholders are
    values, not the text of the contract. That is what makes the marker
    checkable — a fix line whose whole body is a placeholder would leave
    nothing here to assert against, so the messages carry literal text after
    the marker."""
    found: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        func = node.exc.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name not in PREFLIGHT_ERRORS or not node.exc.args:
            continue
        found.append((name, _literal_text(node.exc.args[0])))
    return found


def _literal_text(node: ast.expr) -> str:
    """The literal characters of a string expression, placeholders dropped.

    Adjacent string literals are folded by the parser into one `Constant` when
    they are all plain, and into one `JoinedStr` when any is an f-string, so
    these two cases cover every message in those modules."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return ""


def _sites() -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for module in PREFLIGHT_MODULES:
        source = (PACKAGE_ROOT / module).read_text()
        out += [(module, error, text) for error, text in _message_literals(source)]
    return out


SYNTHETIC = """
class ConfigRenderError(RuntimeError):
    pass


def f(x):
    if x:
        raise ConfigRenderError(
            f"something is wrong with {x}.\\n"
            "fix: do the thing that fixes it."
        )
    raise ValueError("not a preflight error")
"""


def test_the_discovery_reads_a_synthetic_module():
    # A discovery that found nothing would make every assertion below vacuous,
    # and a discovery that found the wrong node would make them wrong. One
    # source with one known answer settles both.
    found = _message_literals(SYNTHETIC)
    assert found == [
        (
            "ConfigRenderError",
            "something is wrong with .\nfix: do the thing that fixes it.",
        )
    ]


def test_the_discovery_finds_every_site():
    found = _sites()
    assert len(found) == EXPECTED_SITES, (
        f"{len(found)} pre-flight raise sites, not {EXPECTED_SITES}:"
        f" {[(module, error) for module, error, _ in found]}. Update"
        " EXPECTED_SITES with the new message's fix line, not without it."
    )
    assert {error for _, error, _ in found} == PREFLIGHT_ERRORS


@pytest.mark.parametrize("module,error,text", _sites())
def test_every_preflight_message_carries_a_fix_line(module, error, text):
    assert FIX_MARKER in text, (
        f"{module}: the {error} message has no `fix:` line — a pre-flight message"
        " must carry the command or the document that resolves it"
    )
    remedy = text.split(FIX_MARKER, 1)[1].strip()
    assert remedy, f"{module}: the {error} message's fix line is empty"


def test_the_run_lock_message_carries_its_fix_line(tmp_path):
    # The live message, not the source: a second run against the same task is
    # the failure a user meets when they forget one is running.
    lock = _acquire_run_lock(tmp_path, task_id="42", project_id="9")
    try:
        with pytest.raises(RunLockError) as raised:
            _acquire_run_lock(tmp_path, task_id="42", project_id="9")
    finally:
        lock.release()
    message = str(raised.value)
    assert FIX_MARKER in message and str(tmp_path) in message


def test_an_unclear_train_boundary_names_the_fix(tmp_path):
    from kiln_harness_tuner.gateway.trainset import TrainSetError, compute

    task = tmp_path / "project" / "tasks" / "42 - T" / "evals" / "e1 - E"
    task.mkdir(parents=True)
    (task / "eval.kiln").write_text(
        json.dumps(
            {
                "id": "e1",
                "splits": {"train": {"source": "eval_input", "filter_id": "all"}},
            }
        )
    )
    with pytest.raises(TrainSetError) as raised:
        compute(tmp_path / "project", "42")
    assert FIX_MARKER in str(raised.value)
