"""Run a script under a pseudo-terminal, feed it keys/signals on a schedule,
capture everything it writes, and report the exit status.

Usage from another script:
    from ptyharness import run
    res = run(["script.py", "arg"], steps=[(1.0, b"q"), (2.0, "SIGTERM")], timeout=10)
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time

PY = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "tvenv", "bin", "python"
)
HERE = os.path.dirname(os.path.abspath(__file__))


class Result:
    def __init__(self, out: bytes, status: int | None, elapsed: float, timed_out: bool):
        self.out = out
        self.status = status
        self.elapsed = elapsed
        self.timed_out = timed_out

    @property
    def text(self) -> str:
        return self.out.decode(errors="replace")

    def exit_code(self):
        if self.status is None:
            return None
        return os.waitstatus_to_exitcode(self.status)

    def segments(self):
        """Split output into (before alt screen, inside alt screen, after)."""
        t = self.text
        a = t.find("\x1b[?1049h")
        b = t.rfind("\x1b[?1049l")
        if a < 0:
            return t, "", ""
        if b < 0:
            return t[:a], t[a:], ""
        return t[:a], t[a:b], t[b:]


def run(
    argv,
    steps=(),
    timeout=15.0,
    size=(62, 210),
    env=None,
    python=PY,
    close_master_at=None,
):
    """steps: list of (time_offset_s, action). action is bytes (written to the
    pty), a signal name like "SIGTERM" (sent to the child), or a callable(fd, pid)."""
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(HERE)
        e = dict(os.environ)
        e.update(TERM="xterm-256color", COLUMNS=str(size[1]), LINES=str(size[0]))
        e.pop("TEXTUAL", None)
        if env:
            e.update(env)
        os.execve(python, [python, *argv], e)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", size[0], size[1], 0, 0))
    out = bytearray()
    start = time.time()
    pending = sorted(steps, key=lambda s: s[0])
    status = None
    master_open = True
    while True:
        now = time.time() - start
        while pending and pending[0][0] <= now:
            _, action = pending.pop(0)
            if isinstance(action, bytes):
                if master_open:
                    os.write(fd, action)
            elif isinstance(action, str):
                try:
                    os.kill(pid, getattr(signal, action))
                except ProcessLookupError:
                    pass
            else:
                action(fd, pid)
        if close_master_at is not None and master_open and now >= close_master_at:
            os.close(fd)
            master_open = False
        if master_open:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    data = b""
                if data:
                    out += data
        else:
            time.sleep(0.05)
        wpid, st = os.waitpid(pid, os.WNOHANG)
        if wpid:
            status = st
            # drain
            if master_open:
                while True:
                    r, _, _ = select.select([fd], [], [], 0.1)
                    if not r:
                        break
                    try:
                        data = os.read(fd, 65536)
                    except OSError:
                        break
                    if not data:
                        break
                    out += data
            break
        if now > timeout:
            os.kill(pid, signal.SIGKILL)
            _, st = os.waitpid(pid, 0)
            if master_open:
                os.close(fd)
            return Result(bytes(out), None, now, True)
    if master_open:
        os.close(fd)
    return Result(bytes(out), status, time.time() - start, False)


if __name__ == "__main__":
    res = run(sys.argv[1:], timeout=10)
    print(res.exit_code(), res.text[-2000:])
