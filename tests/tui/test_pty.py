"""The app on a real terminal (architecture section 15, the pty tests):
`pty_app.py` runs `run_app` in a pseudo-terminal, with a fake start and a
fake run. What pilot can't test: raw mode, the alternate screen, signals,
a closed terminal and writes below Python."""

import sys
from pathlib import Path

import pytest
from pty_app import RERUN, STRAY, SUMMARY
from ptyharness import CLOSE, run

pytestmark = pytest.mark.slow

CHILD = str(Path(__file__).with_name("pty_app.py"))


def start(work: Path, behavior: str, steps, **kwargs):
    return run([sys.executable, CHILD, str(work), behavior], steps, **kwargs)


def scrollback(text: str) -> str:
    return text.replace("\r\n", "\n")


def test_sigterm_stops_the_run_with_its_teardown(tmp_path):
    result = start(tmp_path, "wait", [(tmp_path / "started", "SIGTERM")])
    assert result.exit_code == 143
    assert (tmp_path / "teardown").exists()
    after = scrollback(result.after_app())
    assert after.index(SUMMARY) < after.index("exit: cancelled")
    assert "harness-tuner: interrupted (SIGTERM) — children killed" in after


def test_a_closed_terminal_ends_the_process_with_no_hang(tmp_path):
    result = start(tmp_path, "wait", [(tmp_path / "started", CLOSE)], timeout=20)
    # It ended, after the run's teardown. Its last lines had no terminal to
    # go to, so the exit code is not checked.
    assert result.exit_code is not None
    assert (tmp_path / "teardown").exists()


def test_ctrl_c_then_stop_the_run_exits_130(tmp_path):
    steps = [
        # Ctrl+C: a key, not a signal, in raw mode.
        (tmp_path / "started", b"\x03"),
        # The dialog is drawn once it is mounted, with the focus on "Keep
        # running": Tab moves it to "Stop the run", and Enter presses it.
        (b"Stop the run?", b"\t\r"),
    ]
    result = start(tmp_path, "wait", steps)
    assert result.exit_code == 130
    assert "Stop the run?" in result.in_app()
    assert "harness-tuner: interrupted (SIGINT)" in result.after_app()


# Done's footer: `q` closes the app only once Done is up.
CLOSE_ON_DONE = [(b"Close", b"q")]


def test_stray_output_never_reaches_the_terminal_and_lands_in_the_run_log(tmp_path):
    result = start(tmp_path, "finish", CLOSE_ON_DONE)
    assert result.exit_code == 0
    for marker in STRAY:
        assert marker not in result.text
    run_log = (tmp_path / "run" / "kiln-harness-tuner.log").read_text()
    for marker in STRAY:
        assert marker in run_log
    # The run line, which `_run` prints before its display starts.
    assert "harness-tuner: run 424242-0001 — files in" in run_log


def test_the_closing_block_prints_after_the_app_in_the_scrollback(tmp_path):
    result = start(tmp_path, "finish", CLOSE_ON_DONE)
    assert result.exit_code == 0
    after = scrollback(result.after_app())
    report = tmp_path / "report.md"
    expected = [
        SUMMARY,
        RERUN,
        "exit: time_limit — total",
        f"report: {report}",
        f"Run folder: {tmp_path / 'run'}",
        f"harness-tuner: run finished — report: {report}",
    ]
    places = [after.index(text) for text in expected]
    assert places == sorted(places)
    assert SUMMARY not in result.text[: result.text.find("\x1b[?1049h")]
