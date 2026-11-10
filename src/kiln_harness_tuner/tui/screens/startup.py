"""The start-up screen (ui_design section 8).

For now it shows the welcome card only, while the start runs behind it.
The step lines and the questions come with the app's own start-up terminal.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Static

from kiln_harness_tuner import __version__
from kiln_harness_tuner.onboarding.flow import WELCOME_DESCRIPTION, WELCOME_TITLE
from kiln_harness_tuner.tui.model import words
from kiln_harness_tuner.tui.widgets import Card

TITLE_MARKUP = f"[$error]◆[/] [b]{words.TITLE_MARK}[/]"


class StartupScreen(Screen[None]):
    def compose(self) -> ComposeResult:
        yield Static(TITLE_MARKUP, classes="title-bar")
        yield Card(
            Static(WELCOME_DESCRIPTION, markup=False),
            title=f"[b $text-primary]{WELCOME_TITLE}[/]",
            subtitle=f"[$text-muted]v{__version__}[/]",
            id="welcome",
        )
        yield Footer()
