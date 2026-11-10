"""The terminal while the app runs (architecture section 11).

- The driver draws on a terminal fd of its own (`TTYFile`), which drops
  writes once the terminal is gone. Textual's own driver draws on fd 2.
- fds 1 and 2 point at a stray-output file for the app's lifetime, so a
  write below Python (`os.write`, a child process, a stream handler made
  earlier) never reaches the screen the app draws.
- Python-level writes (`print`, warnings, log records with no handler) are
  captured by Textual; the app sends them to the same file, whole lines only.
- After the app, the file's content goes to the run log, or its path is
  printed when no run folder exists.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Protocol

from textual.app import App
from textual.drivers.linux_driver import LinuxDriver

from kiln_harness_tuner.tui import theme


class TTYFile:
    """The driver's output: the terminal, on an fd of its own.

    Once a write fails (EIO after the terminal closed) the file is dead and
    drops every write. A raise would kill Textual's writer thread, and its
    full queue would then block the app's exit forever (research section 1:
    the SIGHUP hang)."""

    def __init__(self, fd: int, *, encoding: str = "utf-8"):
        self._file = open(fd, "w", encoding=encoding, errors="replace")
        self.dead = False

    def write(self, text: str) -> int:
        if not self.dead:
            try:
                self._file.write(text)
            except OSError:
                self.dead = True
        return len(text)

    def flush(self) -> None:
        if not self.dead:
            try:
                self._file.flush()
            except OSError:
                self.dead = True

    def fileno(self) -> int:
        return self._file.fileno()

    def isatty(self) -> bool:
        return True

    def close(self) -> None:
        with contextlib.suppress(OSError, ValueError):
            self._file.close()


def open_tty() -> int:
    """A terminal fd for the driver alone: the controlling terminal, else a
    copy of fd 1. Call it before `redirect_fds`. Not fd 2, which a person
    may redirect (`2>err.log`) and which the app points at the stray file."""
    try:
        return os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return os.dup(1)


class TTYDriver(LinuxDriver):
    """Textual's Linux and macOS driver, drawing on `tty`, and measuring the
    terminal through it: with fd 1 redirected, the stock size check reads
    `COLUMNS` and `LINES` or falls back to 80×25."""

    tty: ClassVar[TTYFile]

    @classmethod
    def on(cls, tty: TTYFile) -> type[TTYDriver]:
        """This driver class, drawing on `tty`. Textual makes the driver
        itself, from the class."""
        return type(cls.__name__, (cls,), {"tty": tty})

    def __init__(
        self,
        app: App[Any],
        *,
        debug: bool = False,
        mouse: bool = True,
        size: tuple[int, int] | None = None,
    ):
        super().__init__(app, debug=debug, mouse=mouse, size=size)
        self._file = self.tty

    def _get_terminal_size(self) -> tuple[int, int]:
        try:
            size = os.get_terminal_size(self.tty.fileno())
        except (OSError, ValueError):
            return super()._get_terminal_size()
        return size.columns or 80, size.lines or 25


def _flush_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.flush()


@contextlib.contextmanager
def redirect_fds(path: Path) -> Iterator[None]:
    """Point fds 1 and 2 at `path` for the block, then restore them.
    Append mode: a write through fd 2 and a write through another handle
    on the same file never overwrite each other."""
    _flush_streams()
    saved = os.dup(1), os.dup(2)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.dup2(fd, 1)
        os.dup2(fd, 2)
    finally:
        os.close(fd)
    try:
        yield
    finally:
        _flush_streams()
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        os.close(saved[0])
        os.close(saved[1])


STRAY_HEADING = "Output written to the terminal while the app was open:"


class StrayOutput:
    """The file that takes the output written to stdout and stderr while
    the app runs. It is made before the app starts, outside the state
    folder, whose checks run later (in `_run`)."""

    def __init__(self, path: Path):
        self.path = path
        self._fd: int | None = None
        self._partial = ""
        self._lock = threading.Lock()

    @classmethod
    def create(cls) -> StrayOutput:
        fd, name = tempfile.mkstemp(prefix="harness-tuner-", suffix=".log")
        os.close(fd)
        return cls(Path(name))

    def write(self, text: str) -> None:
        """A Python-level write the app captured. `print` writes its text
        and its newline apart, so only whole lines go to the file."""
        with self._lock:
            whole, newline, self._partial = (self._partial + text).rpartition("\n")
            if newline:
                self._append(whole + newline)

    def flush(self) -> None:
        """Write the last line, also when it has no newline yet."""
        with self._lock:
            if self._partial:
                self._append(self._partial + "\n")
                self._partial = ""

    def _append(self, text: str) -> None:
        with contextlib.suppress(OSError):
            if self._fd is None:
                flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
                self._fd = os.open(self.path, flags, 0o600)
            os.write(self._fd, text.encode("utf-8", "replace"))

    def close(self) -> None:
        self.flush()
        with self._lock:
            if self._fd is not None:
                with contextlib.suppress(OSError):
                    os.close(self._fd)
                self._fd = None

    def settle(self, run_log: Path | None) -> Path | None:
        """Once nothing writes to the file any more. Empty: it is removed.
        With a run log: its content is appended there, and it is removed.
        Else it is kept, and its path is returned for the person."""
        self.close()
        try:
            content = self.path.read_bytes()
        except OSError:
            return None
        if not content:
            self.path.unlink(missing_ok=True)
            return None
        if run_log is None:
            return self.path
        stamp = datetime.now().isoformat(timespec="seconds")
        try:
            with run_log.open("ab") as log:
                log.write(f"{stamp} {STRAY_HEADING}\n".encode())
                log.write(content)
        except OSError:
            return self.path
        self.path.unlink(missing_ok=True)
        return None


# The signals the app takes while it runs. Textual sets none for SIGTERM
# and SIGHUP, and its SIGINT handling closes the app with no word to the run.
APP_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
# Installs `handler(signum)` for `APP_SIGNALS` on the app's loop, and returns
# what removes them.
SignalHooks = Callable[
    [asyncio.AbstractEventLoop, Callable[[int], None]], Callable[[], None]
]


def loop_signals(
    loop: asyncio.AbstractEventLoop, handler: Callable[[int], None]
) -> Callable[[], None]:
    """On the app's loop, which is the main thread's: a loop on another
    thread can't take signals."""
    for signum in APP_SIGNALS:
        loop.add_signal_handler(signum, handler, signum)

    def remove() -> None:
        for signum in APP_SIGNALS:
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(signum)

    return remove


class Takeover(Protocol):
    """What the app takes over from the process while it runs: the
    terminal and the stop signals. Tests use a headless one."""

    signals: SignalHooks | None

    def theme(self) -> str:
        """The theme's name. Asked before the app starts."""
        ...

    def print_sink(self, text: str) -> None:
        """Where the app sends the Python-level writes it captures."""
        ...

    def run(self, app: App[Any]) -> None:
        """Run the app to its end. The terminal is the shell's again when
        this returns."""
        ...

    def settle(self, run_log: Path | None) -> Path | None:
        """The stray output, once the run is over (`StrayOutput.settle`)."""
        ...


class TerminalTakeover:
    """The real terminal."""

    signals: SignalHooks | None = staticmethod(loop_signals)

    def __init__(self) -> None:
        self.stray = StrayOutput.create()

    def theme(self) -> str:
        return theme.pick_theme(theme.query_background())

    def print_sink(self, text: str) -> None:
        self.stray.write(text)

    def run(self, app: App[Any]) -> None:
        encoding = getattr(sys.__stdout__, "encoding", None) or "utf-8"
        tty = TTYFile(open_tty(), encoding=encoding)
        try:
            with redirect_fds(self.stray.path):
                app.driver_class = TTYDriver.on(tty)
                app.run()
        finally:
            self.stray.flush()
            tty.close()
            # Textual leaves its SIGTSTP and SIGCONT handlers in place. After
            # a Ctrl+Z they would restart application mode with no running
            # loop (research section 1).
            for signum in (signal.SIGTSTP, signal.SIGCONT):
                signal.signal(signum, signal.SIG_DFL)

    def settle(self, run_log: Path | None) -> Path | None:
        return self.stray.settle(run_log)
