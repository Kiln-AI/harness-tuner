"""What the tuner does differently on native Windows, checked on any OS.

CI runs the whole suite on Linux, macOS and Windows. These tests also drive
the Windows branches on Linux and macOS, by setting `sys.platform`, and they
hold the source to what Windows needs: no Unix-only API at import time, and
an explicit encoding on every text file it opens."""

from __future__ import annotations

import ast
import io
import json
import os
import signal
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest
from platform_marks import posix_only

import kiln_harness_tuner
from kiln_harness_tuner import cli
from kiln_harness_tuner.dispatch import claude_cli
from kiln_harness_tuner.dispatch.claude_cli import spawn_argv
from kiln_harness_tuner.dispatch.run_fence import read_rule, rule_path
from kiln_harness_tuner.gateway import hook
from kiln_harness_tuner.path_order import path_key

PACKAGE_ROOT = Path(kiln_harness_tuner.__file__).resolve().parent
SOURCE_ROOT = PACKAGE_ROOT.parent

# A fresh interpreter without what native Windows does not have: the
# Unix-only modules cannot be imported, and the Unix-only functions and
# signals are gone. Then every module of both packages is imported and
# `--help` prints. On Windows itself the removals change nothing.
WITHOUT_UNIX = r"""
import importlib, importlib.abc, os, pkgutil, signal, sys

UNIX_ONLY_MODULES = {"fcntl", "termios", "tty", "pty", "pwd", "grp", "resource"}


class RefuseUnixOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name in UNIX_ONLY_MODULES:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None


for name in UNIX_ONLY_MODULES:
    sys.modules.pop(name, None)
sys.meta_path.insert(0, RefuseUnixOnly())
for name in ("killpg", "getpgid", "setsid", "getuid", "geteuid", "fork"):
    if hasattr(os, name):
        delattr(os, name)
for name in ("SIGHUP", "SIGKILL", "SIGQUIT", "SIGUSR1", "SIGALRM", "SIGCHLD"):
    if hasattr(signal, name):
        delattr(signal, name)

import kiln_api_mcp
import kiln_harness_tuner

for package in (kiln_harness_tuner, kiln_api_mcp):
    for module in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        importlib.import_module(module.name)

from kiln_harness_tuner.cli import main

try:
    main(["--help"])
except SystemExit as stop:
    sys.exit(stop.code)
"""


def test_every_module_imports_and_help_prints_without_unix_only_apis():
    proc = subprocess.run(
        [sys.executable, "-c", WITHOUT_UNIX],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("usage: kiln-harness-tuner")


# The subprocess calls that decode a child's output when given `text=True`.
SUBPROCESS_CALLS = {"run", "Popen", "check_output", "check_call", "call"}


def _binary_mode(call: ast.Call) -> bool:
    """Whether an `open` call names a binary mode. The mode is the second
    argument of `open(file, mode)` and the first of `Path.open(mode)`."""
    position = 0 if isinstance(call.func, ast.Attribute) else 1
    mode = call.args[position] if len(call.args) > position else None
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    return isinstance(mode, ast.Constant) and "b" in str(mode.value)


def _text_io_without_an_encoding(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        keywords = {keyword.arg for keyword in node.keywords}
        if "encoding" in keywords:
            continue
        is_os_open = isinstance(func, ast.Attribute) and ast.unparse(func) == "os.open"
        if (
            name in ("read_text", "write_text")
            or (name == "open" and not is_os_open and not _binary_mode(node))
            or (name in SUBPROCESS_CALLS and keywords & {"text", "universal_newlines"})
        ):
            lines.append(node.lineno)
    return lines


def test_every_text_file_is_opened_with_an_explicit_encoding():
    # Windows reads and writes text in the locale's code page (cp1252), not
    # UTF-8: an agent template's em dash reached the prompt as `â€”`.
    found = [
        f"{path.relative_to(SOURCE_ROOT).as_posix()}:{line}"
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        for line in _text_io_without_an_encoding(
            ast.parse(path.read_text(encoding="utf-8"))
        )
    ]
    assert not found, f"text I/O without encoding=: {found}"


# The calls that list a folder: their order is the OS's unless sorted by
# `path_key`.
LISTING_CALLS = {"iterdir", "glob", "rglob"}


def _listings_sorted_by_the_os(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "sorted"
            and node.args
            and not any(keyword.arg == "key" for keyword in node.keywords)
        ):
            continue
        listed = {
            call.func.attr
            for call in ast.walk(node.args[0])
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        }
        if listed & LISTING_CALLS:
            lines.append(node.lineno)
    return lines


class TestPathOrder:
    def test_windows_paths_sort_as_macos_and_linux_paths_do(self):
        # `Path` comparison ignores case on Windows: `examples` came first.
        windows = [
            PureWindowsPath(r"C:\r\examples\Nested"),
            PureWindowsPath(r"C:\r\Top"),
        ]
        posix = [PurePosixPath("/r/examples/Nested"), PurePosixPath("/r/Top")]
        assert sorted(windows) == windows  # the order this key replaces
        assert [p.name for p in sorted(windows, key=path_key)] == ["Top", "Nested"]
        assert [p.name for p in sorted(posix, key=path_key)] == ["Top", "Nested"]

    def test_every_sorted_folder_listing_uses_path_key(self):
        # It sees `sorted()` over an `iterdir`, `glob` or `rglob` call in the
        # same expression, not a listing stored in a variable first.
        found = [
            f"{path.relative_to(SOURCE_ROOT).as_posix()}:{line}"
            for path in sorted(SOURCE_ROOT.rglob("*.py"), key=path_key)
            for line in _listings_sorted_by_the_os(
                ast.parse(path.read_text(encoding="utf-8"))
            )
        ]
        assert not found, f"sorted() of a folder listing without key=path_key: {found}"


class TestPermissionRulePaths:
    def test_a_posix_path_keeps_its_form(self):
        assert rule_path(PurePosixPath("/home/u/repo")) == "//home/u/repo"

    def test_a_windows_path_is_written_in_posix_form(self):
        # Claude Code matches `C:\Users\alice` as `/c/Users/alice`.
        path = PureWindowsPath(r"C:\Users\alice\repo\runs")
        assert rule_path(path) == "//c/Users/alice/repo/runs"

    def test_the_read_rule_uses_that_form(self, tmp_path):
        (tmp_path / "file.json").write_text("{}", encoding="utf-8")
        body = rule_path(tmp_path)
        assert read_rule(tmp_path) == f"Read({body}/**)"
        assert read_rule(tmp_path / "file.json") == f"Read({body}/file.json)"
        assert "\\" not in body and "///" not in body


class TestTheGatewayHook:
    def test_a_shell_command_on_macos_and_linux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        entry = hook.hook_entry("probe", timeout=10)
        assert entry == {
            "type": "command",
            "command": hook.hook_command("probe"),
            "timeout": 10,
        }

    def test_the_exec_form_on_windows(self, monkeypatch):
        # No shell: Git Bash and PowerShell quote a Windows path differently.
        monkeypatch.setattr(sys, "platform", "win32")
        entry = hook.hook_entry("probe", timeout=10)
        assert entry == {
            "type": "command",
            "command": sys.executable,
            "args": ["-m", "kiln_harness_tuner.gateway.hook", "--role", "probe"],
            "timeout": 10,
        }

    def test_the_hook_reads_its_event_as_utf8_bytes(self, monkeypatch, capsys):
        # A piped stdin on Windows decodes in cp1252, where the byte 0x9D of
        # a `”` is undefined: the event read as unreadable and a legitimate
        # call was blocked.
        event = {"tool_name": "mcp__x__y", "tool_input": {"text": "“quoted”"}}
        stdin = io.TextIOWrapper(
            io.BytesIO(json.dumps(event, ensure_ascii=False).encode("utf-8")),
            encoding="cp1252",
        )
        monkeypatch.setattr(sys, "stdin", stdin)
        assert hook.main(["--role", "probe"]) == hook.BLOCK_EXIT_CODE
        assert "not one of the probe role's tools" in capsys.readouterr().err


class TestLaunchingAnAgentCli:
    def test_windows_finds_the_cmd_shim_on_path(self, monkeypatch):
        # npm installs `claude` as `claude.cmd`, which CreateProcess cannot
        # find by the bare name.
        shim = r"C:\Users\steve\AppData\Roaming\npm\claude.CMD"
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(claude_cli.shutil, "which", lambda name: shim)
        assert spawn_argv(["claude", "-p"]) == [shim, "-p"]

    def test_windows_keeps_a_name_it_cannot_find(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(claude_cli.shutil, "which", lambda name: None)
        assert spawn_argv(["claude", "-p"]) == ["claude", "-p"]

    def test_macos_and_linux_leave_the_lookup_to_exec(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(claude_cli.shutil, "which", pytest.fail)
        assert spawn_argv(["claude", "-p"]) == ["claude", "-p"]


class TestKillingTheProcessTree:
    def test_windows_kills_the_tree_with_taskkill(self, monkeypatch):
        calls: list[list[str]] = []

        def run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0)

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(claude_cli.subprocess, "run", run)
        claude_cli._kill_tree(4242)
        assert calls == [["taskkill", "/F", "/T", "/PID", "4242"]]

    @posix_only("os.killpg and SIGKILL exist on macOS and Linux only")
    def test_macos_and_linux_kill_the_process_group(self, monkeypatch):
        sent: list[tuple[int, int]] = []
        monkeypatch.setattr(claude_cli.os, "killpg", lambda *a: sent.append(a))
        claude_cli._kill_tree(4242)
        assert sent == [(4242, signal.SIGKILL)]

    async def test_a_taskkill_that_hangs_does_not_stop_the_reap(self, monkeypatch):
        class Proc:
            pid, returncode, killed = 4242, None, False

            def kill(self):
                self.killed = True

            async def wait(self):
                return 1

        def hangs(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(claude_cli.subprocess, "run", hangs)
        proc = Proc()
        await claude_cli._kill_and_reap(proc)  # type: ignore[arg-type]
        assert proc.killed  # the fallback still ran


class TestRedirectedOutputOnWindows:
    def _cp932_stdout(self, monkeypatch) -> io.BytesIO:
        # A redirected stdout on Japanese Windows: cp932 has no em dash.
        raw = io.BytesIO()
        monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp932"))
        return raw

    def test_a_character_the_code_page_lacks_prints_as_a_question_mark(
        self, monkeypatch
    ):
        raw = self._cp932_stdout(monkeypatch)
        monkeypatch.setattr(sys, "platform", "win32")
        cli._replace_unencodable_output()
        print("Kiln Harness Tuner — an agentic optimize loop")
        sys.stdout.flush()
        # Windows' text streams end a line with `\r\n`.
        assert raw.getvalue() == (
            b"Kiln Harness Tuner ? an agentic optimize loop" + os.linesep.encode()
        )

    def test_macos_and_linux_streams_are_left_alone(self, monkeypatch):
        self._cp932_stdout(monkeypatch)
        monkeypatch.setattr(sys, "platform", "linux")
        cli._replace_unencodable_output()
        assert sys.stdout.errors == "strict"
