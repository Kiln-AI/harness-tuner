"""The plain block the app prints to the scrollback when it closes
(functional spec section 10, architecture section 10).

A full-screen app erases what it drew. The scrollback held the start's
summary and its rerun command (printed nowhere else), and the run's last
lines. So the block prints the same texts the plain path prints at the same
moments, built by the same code:

1. The start: the summary and the rerun command, as the start last said them:
   from its report, or, when it stopped or failed after them, as it said them.
2. The run: `PlainUi`'s exit line, Best Confirmed, the report, the run folder.
3. An error: nothing here. `main`'s exception arms print it, as today.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from kiln_harness_tuner.onboarding import StartReport
from kiln_harness_tuner.tui.bridge import said_kind
from kiln_harness_tuner.tui.model.words import DONE_BEST, NO_REPORT


@dataclass
class ClosingBlock:
    start: StartReport | None = None
    # Set by `TuiSink` on the loop thread, at the moment `PlainUi` prints them.
    run_exit_line: str | None = None
    report_path: str | None = None
    # The Best Confirmed run config: its label in the app, and its id.
    best_confirmed: tuple[str, str] | None = None
    # The last summary and rerun command the start said, for a start that
    # ends with no report: Exit, Ctrl+C or an error after them.
    said_summary: str | None = None
    said_rerun: str | None = None

    def take_start(self, report: StartReport) -> None:
        """`prepare_start`'s `report`: called once, as the start ends well."""
        self.start = report

    def said(self, text: str) -> None:
        """The start's terminal's `on_say`, on the start-up thread."""
        kind = said_kind(text)
        if kind == "summary":
            self.said_summary = text
        elif kind == "rerun":
            self.said_rerun = text

    def start_lines(self) -> list[str]:
        """The summary, and the rerun command when the start was guided: the
        report's, or else the last ones the start said."""
        if self.start is not None:
            summary, rerun = self.start.summary, self.start.rerun
        else:
            summary, rerun = self.said_summary, self.said_rerun
        return [text for text in (summary, rerun) if text is not None]

    def run_lines(self, run_dir: Path | None) -> list[str]:
        """Nothing when the run never reached its `exit` event: the error
        screen then says how it ended."""
        if self.run_exit_line is None:
            return []
        lines = [self.run_exit_line]
        if self.best_confirmed is not None:
            label, rc_id = self.best_confirmed
            named = f"{label} ({rc_id})" if label != rc_id else rc_id
            lines.append(f"{DONE_BEST}: {named}")
        lines.append(f"report: {self.report_path}" if self.report_path else NO_REPORT)
        if run_dir is not None:
            lines.append(f"Run folder: {run_dir}")
        return lines


def display_stopped_line(error: BaseException) -> str:
    """After the app crashed while the run goes on (architecture 11)."""
    text = f"{type(error).__name__}: {error}" if str(error) else type(error).__name__
    return (
        f"The display stopped: {text}. The run goes on with plain lines;"
        " details in the run log."
    )


def stray_line(prog: str, path: Path) -> str:
    """Output that was written while the app was open, with no run log to
    put it in."""
    return f"{prog}: output written while the app was open: {path}"


def print_lines(lines: Iterable[str], stream: TextIO | None = None) -> None:
    """Print to the terminal the app gave back. A closed terminal (after a
    hang-up) raises `OSError`, which is dropped: nobody can read it."""
    out = stream if stream is not None else sys.stdout
    try:
        for line in lines:
            print(line, file=out)
        out.flush()
    except OSError:
        pass
