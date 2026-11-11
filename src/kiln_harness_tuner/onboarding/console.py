"""`PlainTerminal`: the start's terminal when no one is there to ask.

A guided start runs in the terminal app, behind its own terminal
(`kiln_harness_tuner.tui.bridge.AppTerminal`). This one is for a start that
isn't guided: plain lines, and never a prompt.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import TypeVar

from kiln_harness_tuner.onboarding.terminal import Action, Option, StepStatus

T = TypeVar("T")

LABEL_WIDTH = 9


class PlainTerminal:
    """A start with no one to ask: plain lines on stdout, warnings on
    stderr, no colour, no spinners, and never a prompt."""

    interactive = False

    def __init__(self, prog: str):
        self.prog = prog

    def welcome(self, title: str, version: str, description: str) -> None:
        pass

    def step(self, status: StepStatus, label: str, text: str) -> None:
        print(f"{label:<{LABEL_WIDTH}} {text}")

    def say(self, text: str) -> None:
        print(text)

    def warn(self, text: str) -> None:
        print(f"{self.prog}: {text}", file=sys.stderr)

    def busy(self, text: str) -> AbstractContextManager[object]:
        return nullcontext()

    # A start that isn't guided fails each problem with its message
    # (`resolve_problem`), so nothing ever asks or shows a menu here.

    def block(self, title: str, body: Sequence[str]) -> None:
        raise RuntimeError("not a guided start")

    def pick(
        self,
        question: str,
        options: Sequence[Option[T]],
        *,
        hint: str | None = None,
        default: T | None = None,
        back: bool = False,
    ) -> T:
        raise RuntimeError("not a guided start")

    def pick_many(
        self,
        question: str,
        options: Sequence[Option[T]],
        *,
        hint: str | None = None,
        default: Sequence[T] = (),
        warn_unmarked: Mapping[T, str] | None = None,
        limit: int | None = None,
        back: bool = False,
    ) -> list[T]:
        raise RuntimeError("not a guided start")

    def act(
        self, question: str, actions: Sequence[Action], *, back: bool = False
    ) -> Action:
        raise RuntimeError("not a guided start")

    def text(
        self,
        question: str,
        *,
        default: str = "",
        hint: str | None = None,
        validate: Callable[[str], str | None] | None = None,
        back: bool = False,
    ) -> str:
        raise RuntimeError("not a guided start")

    def open_url(self, url: str) -> None:
        raise RuntimeError("not a guided start")

    # Lines once printed stay printed, and nothing asks, so nothing goes back.

    def mark(self) -> object:
        return None

    def rewind(self, mark: object) -> None:
        pass
