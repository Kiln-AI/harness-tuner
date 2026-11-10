"""The modal pattern of ui_design 4.11 and 4.13: a card over the dimmed run
screen. A click on the dimmed area outside the card does what Esc does in
that modal; a click inside the card never closes it."""

from __future__ import annotations

from textual import events
from textual.screen import ModalScreen, ScreenResultType


class CardModal(ModalScreen[ScreenResultType]):
    """A modal screen whose outside closes it, as its Esc does."""

    async def on_click(self, event: events.Click) -> None:
        # Outside the card, the screen itself is under the mouse. A click in
        # the card, or on the footer, lands on one of its widgets.
        if event.widget is not self:
            return
        # A double click on an experiment card: the first click opens the
        # panel, and the second (chain 2) lands on its dimmed area. It is
        # not a click outside. A first click outside always has chain 1.
        if event.chain > 1:
            return
        escape = self.active_bindings.get("escape")
        if escape is not None and escape.enabled:
            await self.app.run_action(escape.binding.action, escape.node)
