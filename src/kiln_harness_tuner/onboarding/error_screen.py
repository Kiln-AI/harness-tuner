"""The error screen: how every error that stops the start or the run is
shown, and what the stop left behind.

On a terminal the screen is a red `✗` title, the error's own lines, the
ending, and a dimmed closing line. Without a terminal the same lines are
plain text, the first one `<prog>: error: <title>`.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO

from rich.console import Console
from rich.text import Text

from kiln_harness_tuner.kiln import KilnAPIError, KilnTransportError

INDENT = "  "


class Unbroken(str):
    """A screen line printed whole, never wrapped by the screen: a request
    or a path, which must copy out intact. A long one is wrapped by the
    terminal, with nothing put inside it."""


class SpendTotal(Protocol):
    """The run's spend meter, as the ending reads it."""

    @property
    def total(self) -> float: ...


@dataclass
class RunProgress:
    """How far the start got, for the ending: whether the agent CLI's test
    prompt ran, and the run folder, spend meter and report once they
    exist."""

    cli_checked: bool = False
    run_dir: Path | None = None
    spend: SpendTotal | None = None
    report_path: Path | None = None


def closing_line(prog: str) -> str:
    return f"{prog} stopped with an error (exit 1)"


def error_body(exc: BaseException, *, host: str) -> list[str]:
    """The lines that explain `exc`: a Kiln error's request and Kiln's own
    message, a lost connection's message with the question to check, or
    the error's own message."""
    if isinstance(exc, KilnAPIError):
        return [
            Unbroken(f"{exc.method} {exc.url_path}"),
            f"HTTP {exc.status_code}: {exc.kiln_message}",
        ]
    if isinstance(exc, KilnTransportError):
        return [*str(exc).splitlines(), f"Is Kiln still running at {host}?"]
    return str(exc).splitlines()


def ending_lines(progress: RunProgress) -> list[str]:
    """What the stop left behind: nothing, the agent CLI's test prompt, or a
    run folder with its spend so far and its report."""
    if progress.run_dir is None:
        if progress.cli_checked:
            return [
                "The only spend was the agent CLI's one test prompt."
                " No run folder was created."
            ]
        return ["Nothing was spent and no run folder was created."]
    total = progress.spend.total if progress.spend is not None else 0.0
    lines = [
        f"The run stopped. Spent ${total:.2f} so far"
        " (agents and the task's model calls).",
        Unbroken(f"Run folder: {progress.run_dir}"),
    ]
    if progress.report_path is not None and progress.report_path.exists():
        lines.append(Unbroken(f"Report: {progress.report_path}"))
    return lines


def _console(stream: TextIO) -> Console:
    return Console(file=stream, highlight=False, soft_wrap=False, emoji=False)


def _is_terminal(stream: TextIO) -> bool:
    """Whether `stream` is a terminal, by the stream alone. rich's own test
    also honours `FORCE_COLOR` and `TTY_COMPATIBLE`, which CI and agent
    harnesses set, and a script reading stderr needs the plain form."""
    isatty = getattr(stream, "isatty", None)
    return bool(isatty is not None and isatty())


def show_error(
    title: str,
    body: Sequence[str],
    ending: Sequence[str],
    *,
    prog: str,
    stream: TextIO | None = None,
) -> None:
    """Show one error: the title, the body and the ending, then the
    closing line. `stream` is stderr by default."""
    out: TextIO = sys.stderr if stream is None else stream
    if not _is_terminal(out):
        lines = [f"{prog}: error: {title}"]
        for line in (*body, *ending):
            lines += [f"{INDENT}{part}" if part else "" for part in _parts(line)]
        lines.append(closing_line(prog))
        out.write("\n".join(lines) + "\n")
        out.flush()
        return
    console = _console(out)
    console.print()
    console.print(Text(f"✗ {title}", style="bold red"))
    for section in (body, ending):
        if not section:
            continue
        console.print()
        for line in section:
            for part in _wrapped(console, line):
                # Soft-wrapped: a part wider than the line, a long path, is
                # wrapped by the terminal, with no indent put inside it. A
                # blank line, a paragraph break, gets no indent.
                indented = Text(INDENT) + part if part.plain else Text()
                console.print(indented, soft_wrap=True)
    console.print()
    console.print(Text(closing_line(prog), style="dim"))


def show_closing_line(prog: str, *, stream: TextIO | None = None) -> None:
    """The closing line alone, after an error the guided start has already
    shown."""
    out: TextIO = sys.stderr if stream is None else stream
    if not _is_terminal(out):
        out.write(closing_line(prog) + "\n")
        out.flush()
        return
    _console(out).print(Text(closing_line(prog), style="dim"))


def _wrapped(console: Console, line: str) -> list[Text]:
    """`line` as the screen prints it after the indent. An `Unbroken` line
    is one part. Any other line is wrapped at its spaces to fit, and a word
    wider than the line, such as a path, is kept whole in its own part.
    Each part is without the trailing spaces a wrap leaves."""
    if isinstance(line, Unbroken):
        return [Text(line)]
    width = max(console.width - len(INDENT), 1)
    parts = Text(line, overflow="ignore").wrap(console, width)
    for part in parts:
        part.rstrip()
    return list(parts)


def _parts(line: str) -> list[str]:
    """A body line's own lines: a Kiln message can hold line breaks."""
    return line.splitlines() or [""]
