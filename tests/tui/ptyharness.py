"""Run a command in a pseudo-terminal, act on it step by step, and keep all
it writes (research: `specs/projects/new-tui/research/textual/experiments/`).

It answers the app's background query as a terminal with no OSC 11 does
(DA1 only), so the query does not wait for its timeout.

A step waits for its trigger, then acts. A trigger is a file to appear,
bytes to appear in what the command wrote since the step before, or seconds
to wait after the step before. An action is bytes (typed), a signal
name (sent), `CLOSE` (the terminal goes away, as when its window closes) or
None (nothing: a step that only waits).
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
import struct
import termios
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

CLOSE = "CLOSE"
BACKGROUND_QUERY = b"\x1b]11;?"
DA1_REPLY = b"\x1b[?62;22c"
ALT_ON = "\x1b[?1049h"
ALT_OFF = "\x1b[?1049l"

Trigger = Path | bytes | float
Action = bytes | str | None


@dataclass
class Result:
    out: bytes
    exit_code: int | None  # None: it did not end before the timeout

    @property
    def text(self) -> str:
        return self.out.decode(errors="replace")

    def after_app(self) -> str:
        """What the process wrote after it left the alternate screen."""
        text = self.text
        end = text.rfind(ALT_OFF)
        return text[end + len(ALT_OFF) :] if end >= 0 else ""

    def in_app(self) -> str:
        text = self.text
        return text[text.find(ALT_ON) : text.rfind(ALT_OFF)]


def run(
    argv: Sequence[str],
    steps: Sequence[tuple[Trigger, Action]] = (),
    *,
    timeout: float = 30.0,
    size: tuple[int, int] = (62, 210),
    env: dict[str, str] | None = None,
) -> Result:
    pid, fd = pty.fork()
    if pid == 0:  # the child
        child_env = {k: v for k, v in os.environ.items() if not k.startswith("TEXTUAL")}
        child_env.update(TERM="xterm-256color", **(env or {}))
        os.execve(argv[0], list(argv), child_env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", *size, 0, 0))
    out = bytearray()
    pending = list(steps)
    open_ = True
    answered = False
    since = time.monotonic()
    mark = 0  # where the output was at the step before
    deadline = since + timeout
    while True:
        if pending:
            trigger, action = pending[0]
            if isinstance(trigger, Path):
                due = trigger.exists()
            elif isinstance(trigger, bytes):
                due = trigger in out[mark:]
            else:
                due = time.monotonic() - since >= trigger
            if due:
                pending.pop(0)
                since = time.monotonic()
                mark = len(out)
                if action is None:
                    pass
                elif action == CLOSE:
                    os.close(fd)
                    open_ = False
                elif isinstance(action, bytes):
                    os.write(fd, action)
                else:
                    os.kill(pid, getattr(signal, action))
        if open_:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                try:
                    out += os.read(fd, 65536)
                except OSError:
                    pass
            if not answered and BACKGROUND_QUERY in out:
                os.write(fd, DA1_REPLY)
                answered = True
        else:
            time.sleep(0.05)
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            while open_:
                ready, _, _ = select.select([fd], [], [], 0.1)
                try:
                    chunk = os.read(fd, 65536) if ready else b""
                except OSError:
                    chunk = b""
                if not chunk:
                    break
                out += chunk
            if open_:
                os.close(fd)
            return Result(bytes(out), os.waitstatus_to_exitcode(status))
        if time.monotonic() > deadline:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            if open_:
                os.close(fd)
            return Result(bytes(out), None)
