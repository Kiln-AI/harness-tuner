"""The two ways the onboarding stops a start before the run begins."""

from __future__ import annotations

from collections.abc import Sequence


def plain_text(title: str, body: Sequence[str]) -> str:
    """A titled message as plain lines: the title, then the body indented."""
    return "\n".join([title, *(f"  {line}" for line in body)])


class OnboardingError(Exception):
    """A start-up problem the user must fix, as a title and the lines that
    explain it. `str()` is the plain message. `main` shows the title and the
    lines on the error screen (`error_screen.show_error`) before exiting 1. A
    guided start shows the error as a ✗ block instead, and sets `shown` so
    `main` prints only the screen's closing line."""

    def __init__(self, title: str, body: Sequence[str] = ()):
        self.title = title
        self.body = tuple(body)
        self.shown = False
        super().__init__(plain_text(title, self.body))


class OnboardingStopped(Exception):
    """The user stopped at a prompt: Exit or Ctrl-D (exit 1), or Ctrl-C
    (exit 130). `main` prints `<prog>: stopped.` and exits with `exit_code`."""

    def __init__(self, exit_code: int):
        self.exit_code = exit_code
        super().__init__(f"stopped (exit {exit_code})")
