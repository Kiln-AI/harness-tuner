"""The OS sandbox around each agent's shell, and the platform that has none.

On macOS and Linux an agent CLI runs its shell commands inside an OS sandbox
(Seatbelt on macOS, bubblewrap on Linux). That sandbox is the part of the
read fence that a shell command cannot get around. Native Windows has no
such sandbox. The agent CLI's own permission rules still apply there, but
they cannot see into every command a shell runs. So a run on native Windows
cannot guarantee that no agent reads the withheld test set, and it starts
only when the operator agrees.
"""

from __future__ import annotations

import sys
from typing import TextIO

NO_SANDBOX_WARNING = (
    "Windows has no OS sandbox for the tuner's agents, so we can't guarantee"
    " they won't read your withheld test set. For that guarantee, run it in"
    " WSL (Windows Subsystem for Linux)."
)
CONTINUE_PROMPT = "Continue? [y/N] "
ALLOW_FLAG = "--allow-no-sandbox"
_YES = frozenset({"y", "yes"})


def has_os_sandbox() -> bool:
    """Whether this platform gives the agent CLIs an OS sandbox. A property
    of the platform, not of the run: an agent CLI may still use none
    (OpenCode fences by its own permission rules on every OS)."""
    return sys.platform != "win32"


class NoSandboxRefused(RuntimeError):
    """A run with no OS sandbox did not get the operator's agreement."""


def _is_terminal(stream: TextIO) -> bool:
    try:
        return stream.isatty()
    except ValueError:  # a closed stream
        return False


def confirm_no_sandbox(
    *,
    allow: bool,
    stdin: TextIO | None = None,
    stderr: TextIO | None = None,
) -> None:
    """On a platform with no OS sandbox, show the warning and get a yes.

    The answer defaults to No, and Ctrl-C is a No. With no terminal to
    answer on, only `allow` (`--allow-no-sandbox`) lets the run start. The
    prompt goes to stderr, so it stays on the screen when stdout is
    redirected. Raises NoSandboxRefused. Does nothing where the OS sandbox
    exists."""
    if has_os_sandbox():
        return
    stdin = sys.stdin if stdin is None else stdin
    stderr = sys.stderr if stderr is None else stderr
    if allow:
        print(f"{NO_SANDBOX_WARNING} Continuing: {ALLOW_FLAG}.", file=stderr)
        return
    if stdin is None or not _is_terminal(stdin):
        raise NoSandboxRefused(
            f"{NO_SANDBOX_WARNING}\n"
            "No terminal is attached to answer the question, so the run did not"
            " start.\n"
            f"fix: run it in WSL, or pass {ALLOW_FLAG} to start it without an OS"
            " sandbox."
        )
    print(f"{NO_SANDBOX_WARNING} {CONTINUE_PROMPT}", end="", file=stderr, flush=True)
    try:
        answer = stdin.readline()
    except KeyboardInterrupt:  # Ctrl-C at the question is a No
        print(file=stderr)
        answer = ""
    if answer.strip().lower() not in _YES:
        raise NoSandboxRefused(
            "the run did not start: it needs a yes to run without an OS sandbox.\n"
            f"fix: run it in WSL, or answer y, or pass {ALLOW_FLAG}."
        )
