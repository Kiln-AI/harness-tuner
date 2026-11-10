"""The building blocks of ui_design section 4, as Textual widgets.

`specs/projects/new-tui/ui_preview/widgets.py` is the design's version of
each. This module grows with the screens that use them.
"""

from __future__ import annotations

from textual.containers import Vertical
from textual.widget import Widget


class Card(Vertical):
    """4.3: a rounded container. The border title says what the card is; the
    border subtitle carries muted meta. The border is blue while live."""

    def __init__(
        self,
        *children: Widget,
        title: str = "",
        subtitle: str = "",
        live: bool = False,
        id: str | None = None,
        classes: str | None = None,
    ):
        super().__init__(*children, id=id, classes=classes)
        self.border_title = title
        self.border_subtitle = subtitle
        self.set_class(live, "-live")
