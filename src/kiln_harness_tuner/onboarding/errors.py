"""The two ways the onboarding stops a start before the run begins."""

from __future__ import annotations


class OnboardingError(Exception):
    """A start-up problem the user must fix. The message is complete; `main`
    prints `<prog>: <message>` to stderr and exits 1."""


class OnboardingStopped(Exception):
    """The user stopped at a prompt: `x` or end of input (exit 1), or Ctrl-C
    (exit 130). `main` prints `<prog>: stopped.` and exits with `exit_code`."""

    def __init__(self, exit_code: int):
        self.exit_code = exit_code
        super().__init__(f"stopped (exit {exit_code})")
