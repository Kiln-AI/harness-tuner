"""The terminal while the app runs (architecture section 11): the driver's
own tty, fds 1 and 2 on the stray-output file, and the stop signals."""

import asyncio
import fcntl
import os
import pty
import signal
import struct
import tempfile
import termios
from pathlib import Path

import pytest
from textual.app import App

from kiln_harness_tuner.tui.terminal_io import (
    APP_SIGNALS,
    STRAY_HEADING,
    StrayOutput,
    TTYDriver,
    TTYFile,
    loop_signals,
    redirect_fds,
)


def test_a_tty_file_drops_writes_once_the_terminal_is_gone():
    read_end, write_end = os.pipe()
    tty = TTYFile(write_end)
    tty.write("drawn")
    tty.flush()
    assert os.read(read_end, 100) == b"drawn"
    assert tty.isatty()
    # The far end goes, as a closed terminal window does.
    os.close(read_end)
    tty.write("x" * 100_000)
    tty.flush()
    assert tty.dead
    tty.write("more")
    tty.flush()
    tty.close()


@pytest.fixture
def keep_job_control_signals():
    """Textual's driver sets SIGTSTP and SIGCONT handlers when it is made."""
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTSTP, signal.SIGCONT)}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


async def test_the_driver_draws_on_its_own_tty_and_measures_it(
    keep_job_control_signals,
):
    near, far = pty.openpty()
    fcntl.ioctl(far, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 150, 0, 0))
    tty = TTYFile(far)
    try:
        driver_class = TTYDriver.on(tty)
        assert issubclass(driver_class, TTYDriver)
        driver = driver_class(App())
        assert driver._file is tty
        assert driver._get_terminal_size() == (150, 50)
    finally:
        tty.close()
        os.close(near)


def test_redirect_fds_sends_low_level_writes_to_the_file(tmp_path, capfd):
    path = tmp_path / "stray.log"
    with redirect_fds(path):
        os.write(1, b"one\n")
        os.write(2, b"two\n")
    os.write(1, b"after\n")
    assert path.read_text() == "one\ntwo\n"
    out, err = capfd.readouterr()
    assert out == "after\n"
    assert err == ""


def test_stray_output_is_made_outside_the_state_folder():
    stray = StrayOutput.create()
    try:
        assert stray.path.parent == Path(tempfile.gettempdir())
        assert stray.path.name.startswith("harness-tuner-")
        assert stray.path.suffix == ".log"
    finally:
        stray.path.unlink()


def test_stray_output_keeps_whole_lines(tmp_path):
    stray = StrayOutput(tmp_path / "stray.log")
    # `print` writes the text and the newline apart.
    stray.write("a warning")
    stray.write("\n")
    stray.write("half")
    assert stray.path.read_text() == "a warning\n"
    stray.flush()
    assert stray.path.read_text() == "a warning\nhalf\n"


def test_empty_stray_output_is_removed(tmp_path):
    stray = StrayOutput(tmp_path / "stray.log")
    stray.path.write_text("")
    assert stray.settle(tmp_path / "run.log") is None
    assert not stray.path.exists()
    assert not (tmp_path / "run.log").exists()


def test_stray_output_goes_to_the_run_log(tmp_path):
    run_log = tmp_path / "kiln-harness-tuner.log"
    run_log.write_text("2026-11-10T09:00:00 INFO run starting\n")
    stray = StrayOutput(tmp_path / "stray.log")
    stray.write("UserWarning: something\n")
    assert stray.settle(run_log) is None
    assert not stray.path.exists()
    lines = run_log.read_text().splitlines()
    assert lines[0] == "2026-11-10T09:00:00 INFO run starting"
    assert lines[1].endswith(STRAY_HEADING)
    assert lines[2] == "UserWarning: something"


def test_stray_output_with_no_run_log_is_kept_and_named(tmp_path):
    stray = StrayOutput(tmp_path / "stray.log")
    stray.write("WARNING claude -p env: withholding ANTHROPIC_API_KEY\n")
    assert stray.settle(None) == stray.path
    assert stray.path.read_text().startswith("WARNING claude -p env")


@pytest.fixture
def keep_stop_signals():
    saved = {sig: signal.getsignal(sig) for sig in APP_SIGNALS}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


async def test_the_app_takes_the_stop_signals_on_its_loop(keep_stop_signals):
    got: list[int] = []
    remove = loop_signals(asyncio.get_running_loop(), got.append)
    os.kill(os.getpid(), signal.SIGHUP)
    for _ in range(100):
        if got:
            break
        await asyncio.sleep(0.01)
    assert got == [signal.SIGHUP]
    remove()
    assert signal.getsignal(signal.SIGHUP) == signal.SIG_DFL
    assert signal.getsignal(signal.SIGINT) == signal.default_int_handler
