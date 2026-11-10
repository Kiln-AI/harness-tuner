"""E6: query the terminal background colour with OSC 11 before App.run().

query_background() returns (r, g, b) floats in 0..1, or None (no tty, no
reply within the timeout, or an unparsable reply). It never raises.
"""

from __future__ import annotations

import os
import re
import select
import sys
import termios
import time
import tty

_REPLY = re.compile(
    rb"\x1b\]11;rgba?:([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})(?:/[0-9a-fA-F]{1,4})?(?:\x07|\x1b\\)"
)


def _scale(h: bytes) -> float:
    return int(h, 16) / (16 ** len(h) - 1)


def query_background(timeout: float = 0.15) -> tuple[float, float, float] | None:
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return None  # no controlling terminal (CI, cron, pipes)
    try:
        if not os.isatty(fd):
            return None
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd, termios.TCSANOW)  # no echo, no line buffering; ISIG kept
            # Query OSC 11, then DA1 (CSI c). Almost every terminal answers DA1,
            # so its reply marks "the terminal is done answering": we can stop
            # waiting early on terminals that ignore OSC 11.
            # (tmux 3.3+ answers OSC 11 itself from the pane's colours, which
            # follow the outer terminal only if tmux learned them; older tmux
            # and GNU screen may not answer at all -> the DA1 reply ends the wait.)
            query = b"\x1b]11;?\x1b\\" + b"\x1b[c"
            os.write(fd, query)
            buf = b""
            deadline = time.monotonic() + timeout
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                r, _, _ = select.select([fd], [], [], left)
                if not r:
                    break
                chunk = os.read(fd, 1024)
                if not chunk:
                    break
                buf += chunk
                if re.search(rb"\x1b\[\?[\d;]*c", buf):  # DA1 reply: all answers are in
                    break
            return _parse(buf)
        finally:
            # Drop anything still unread (a late or partial reply) so it never
            # reaches the app as key presses, then restore the mode.
            termios.tcflush(fd, termios.TCIFLUSH)
            termios.tcsetattr(fd, termios.TCSANOW, old)
    except (OSError, termios.error):
        return None
    finally:
        os.close(fd)


def _parse(buf: bytes):
    m = _REPLY.search(buf)
    if not m:
        return None
    return tuple(_scale(g) for g in m.groups())


def is_dark(rgb) -> bool:
    r, g, b = rgb
    # relative luminance (sRGB, approximate)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) < 0.5


if __name__ == "__main__":
    a = time.monotonic()
    rgb = query_background(float(sys.argv[1]) if len(sys.argv) > 1 else 0.15)
    took = time.monotonic() - a
    verdict = "fallback dark" if rgb is None else ("dark" if is_dark(rgb) else "light")
    print(f"OSC11 result={rgb} theme={verdict} took={took * 1000:.0f}ms")
