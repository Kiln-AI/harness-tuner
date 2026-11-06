"""The start on native Windows, which has no OS sandbox for the agents: the
warning, the question that defaults to No, the refusal when no terminal can
answer, and the report's note. Driven on any OS by setting `sys.platform`."""

from __future__ import annotations

import io
import sys

import pytest
from test_cli import ARGS, _redirect_roots

import kiln_harness_tuner.cli
from kiln_harness_tuner import os_sandbox
from kiln_harness_tuner.cli import main
from kiln_harness_tuner.os_sandbox import (
    NO_SANDBOX_WARNING,
    NoSandboxRefused,
    confirm_no_sandbox,
)
from kiln_harness_tuner.report import NO_SANDBOX_NOTE, ReportWriter
from kiln_harness_tuner.status import RunStatusTracker

# The question as the operator reads it.
QUESTION = (
    "Windows has no OS sandbox for the tuner's agents, so we can't guarantee"
    " they won't read your withheld test set. For that guarantee, run it in WSL"
    " (Windows Subsystem for Linux). Continue? [y/N] "
)


class Terminal(io.StringIO):
    """An operator at a terminal, with what they type."""

    def isatty(self) -> bool:
        return True


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")


def test_macos_and_linux_ask_nothing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    stderr = io.StringIO()
    confirm_no_sandbox(allow=False, stdin=io.StringIO(), stderr=stderr)
    assert stderr.getvalue() == ""


@pytest.mark.parametrize("answer", ["y\n", "Y\n", "yes\n", " yes \n"])
def test_windows_asks_and_a_yes_starts_the_run(windows, answer):
    stderr = io.StringIO()
    confirm_no_sandbox(allow=False, stdin=Terminal(answer), stderr=stderr)
    assert stderr.getvalue() == QUESTION


@pytest.mark.parametrize("answer", ["\n", "n\n", "no\n", "sure\n", ""])
def test_windows_defaults_to_no(windows, answer):
    with pytest.raises(NoSandboxRefused, match="needs a yes") as raised:
        confirm_no_sandbox(allow=False, stdin=Terminal(answer), stderr=io.StringIO())
    assert "\nfix: run it in WSL" in str(raised.value)


class CtrlC(Terminal):
    """An operator who presses Ctrl-C at the question."""

    def readline(self, size: int = -1) -> str:
        raise KeyboardInterrupt


def test_ctrl_c_at_the_question_is_a_no(windows):
    stderr = io.StringIO()
    with pytest.raises(NoSandboxRefused, match="needs a yes"):
        confirm_no_sandbox(allow=False, stdin=CtrlC(), stderr=stderr)
    assert stderr.getvalue() == QUESTION + "\n"  # the next line starts clean


def test_windows_with_no_terminal_refuses_and_names_the_flag(windows):
    stderr = io.StringIO()
    with pytest.raises(NoSandboxRefused) as raised:
        confirm_no_sandbox(allow=False, stdin=io.StringIO("y\n"), stderr=stderr)
    message = str(raised.value)
    assert message.startswith(NO_SANDBOX_WARNING)
    assert "\nfix: run it in WSL, or pass --allow-no-sandbox" in message
    assert stderr.getvalue() == ""  # nothing was asked


def test_windows_with_the_flag_warns_and_starts(windows):
    stderr = io.StringIO()
    stdin = Terminal("n\n")
    confirm_no_sandbox(allow=True, stdin=stdin, stderr=stderr)
    assert stderr.getvalue().startswith(NO_SANDBOX_WARNING)
    assert "--allow-no-sandbox" in stderr.getvalue()
    assert stdin.read() == "n\n"  # not asked


def _real_confirmation(monkeypatch):
    # The suite's conftest answers the question for every other test.
    monkeypatch.setattr(
        kiln_harness_tuner.cli, "confirm_no_sandbox", os_sandbox.confirm_no_sandbox
    )


def test_a_windows_start_with_no_terminal_stops_before_any_agent(
    windows, monkeypatch, capsys
):
    _real_confirmation(monkeypatch)
    started: list[object] = []

    async def no_run(args):
        started.append(args)
        return "unreachable"

    monkeypatch.setattr(kiln_harness_tuner.cli, "_run", no_run)
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    assert main(ARGS) == 1
    assert started == []  # no agent CLI check, no run directory, no lock
    err = capsys.readouterr().err
    assert err.startswith(f"kiln-harness-tuner: {NO_SANDBOX_WARNING}")
    assert "--allow-no-sandbox" in err


def test_a_windows_start_with_the_flag_runs(windows, monkeypatch, capsys):
    _real_confirmation(monkeypatch)

    async def runs(args):
        return "reports/kiln-harness-tuner-x.md"

    monkeypatch.setattr(kiln_harness_tuner.cli, "_run", runs)
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    assert main([*ARGS, "--allow-no-sandbox"]) == 0
    captured = capsys.readouterr()
    assert captured.err.startswith(NO_SANDBOX_WARNING)
    assert "run finished" in captured.out


def _report(tmp_path, *, os_sandbox: bool) -> str:
    tracker = RunStatusTracker(
        project_id="p", task_id="t", guidance="", os_sandbox=os_sandbox
    )
    writer = ReportWriter(tracker, "260101-0000", tmp_path)
    path = writer._write("# Run report\n", tracker.snapshot())
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_the_report_of_a_run_with_no_os_sandbox_says_so_first(tmp_path):
    report = _report(tmp_path, os_sandbox=False)
    assert report.startswith(NO_SANDBOX_NOTE + "\n\n# Run report\n")


def test_a_sandboxed_run_report_has_no_such_note(tmp_path):
    assert NO_SANDBOX_NOTE not in _report(tmp_path, os_sandbox=True)


def test_the_cli_records_the_missing_sandbox_in_the_report(monkeypatch, tmp_path):
    _redirect_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(kiln_harness_tuner.cli, "has_os_sandbox", lambda: False)

    async def fatal_exit(ctx, guidance, timeout_mins):
        return await ctx.write_fallback(ctx, "fatal")

    monkeypatch.setattr(kiln_harness_tuner.cli, "orchestrate", fatal_exit)
    assert main(ARGS) == 0
    (report,) = (tmp_path / "repo" / "reports").iterdir()
    assert report.read_text(encoding="utf-8").startswith(NO_SANDBOX_NOTE)
