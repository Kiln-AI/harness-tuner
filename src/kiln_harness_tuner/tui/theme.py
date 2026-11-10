"""The app's two themes, and which one a terminal gets (ui_design section 2).

Textual does not ask the terminal for its colors. `query_background` asks
with OSC 11 before the app starts, and `pick_theme` turns the answer into
one of the two themes. A light background gets `kiln-light`; anything else,
or no answer, gets `kiln-dark`.
"""

from __future__ import annotations

import os
import re
import select
import termios
import time
import tty
from collections.abc import Mapping

from textual.theme import Theme

DARK = "kiln-dark"
LIGHT = "kiln-light"

KILN_DARK = Theme(
    name=DARK,
    primary="#415CF5",
    secondary="#415CF5",
    accent="#7B90FF",
    warning="#F4B544",
    error="#E74D31",
    success="#4CC38A",
    foreground="#E6E8F0",
    background="#0F1117",
    surface="#151822",
    panel="#1C2030",
    dark=True,
    variables={
        "text-primary": "#7B90FF",
        "text-warning": "#F4B544",
        "text-error": "#E74D31",
        "text-success": "#4CC38A",
        "text-muted": "#8C92A6",
        "text-disabled": "#5C6276",
        "border-muted": "#363C4F",
        "faint": "#4C5266",
        "held": "#2C7D57",
        "failing": "#9C3D2C",
        "tag-text": "#0F1117",
        "block-cursor-foreground": "#FFFFFF",
        "footer-key-foreground": "#7B90FF",
        "footer-background": "#1C2030",
        "input-selection-background": "#415CF5 40%",
    },
)

KILN_LIGHT = Theme(
    name=LIGHT,
    primary="#415CF5",
    secondary="#415CF5",
    accent="#415CF5",
    warning="#F4B544",
    error="#D1401F",
    success="#1E8A57",
    foreground="#1B1E27",
    background="#F4F5F9",
    surface="#FFFFFF",
    panel="#E3E6F0",
    dark=False,
    variables={
        "text-primary": "#415CF5",
        "text-warning": "#9A6A00",
        "text-error": "#D1401F",
        "text-success": "#1E8A57",
        "text-muted": "#636A7E",
        "text-disabled": "#9CA1B0",
        "border-muted": "#C4C9D6",
        "faint": "#B3B8C6",
        "held": "#86C9A6",
        "failing": "#EBA595",
        "tag-text": "#1B1E27",
        "block-cursor-foreground": "#FFFFFF",
        "footer-key-foreground": "#415CF5",
        "footer-background": "#E3E6F0",
        "input-selection-background": "#415CF5 30%",
    },
)

THEMES = (KILN_DARK, KILN_LIGHT)
# The variables the themes add to Textual's own. The app gives their dark
# values as defaults, so the stylesheet parses before a theme is set.
CUSTOM_VARIABLES = ("border-muted", "faint", "held", "failing", "tag-text")

RGB = tuple[float, float, float]

# An OSC 11 reply: `ESC ] 11 ; rgb:RRRR/GGGG/BBBB` (or `rgba:…/AAAA`), ended
# by BEL or ST. Each channel has 1 to 4 hex digits.
_REPLY = re.compile(
    rb"\x1b\]11;rgba?:([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})"
    rb"(?:/[0-9a-fA-F]{1,4})?(?:\x07|\x1b\\)"
)
# The reply to DA1 (`ESC [ c`). Terminals answer in order, so it comes after
# any OSC 11 reply: once it is in, every answer is in.
_DA1_REPLY = re.compile(rb"\x1b\[\?[\d;]*c")
_QUERY = b"\x1b]11;?\x1b\\" + b"\x1b[c"
QUERY_TIMEOUT_SECS = 0.5


def parse_background(reply: bytes) -> RGB | None:
    """The background color in an OSC 11 reply, each channel from 0 to 1."""
    match = _REPLY.search(reply)
    if match is None:
        return None
    red, green, blue = (int(h, 16) / (16 ** len(h) - 1) for h in match.groups())
    return red, green, blue


def is_light(rgb: RGB) -> bool:
    red, green, blue = rgb
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue >= 0.5


def query_background(
    timeout: float = QUERY_TIMEOUT_SECS, *, tty_path: str = "/dev/tty"
) -> RGB | None:
    """Ask the terminal for its background color. None when there is no
    terminal, no answer before `timeout`, or no color in the answer. Never
    raises.

    Call it before the app starts and before any thread reads stdin: a
    reply that arrives while the app runs becomes key presses. A late or
    partial reply is flushed from the input before this returns."""
    try:
        fd = os.open(tty_path, os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return None
    try:
        if not os.isatty(fd):
            return None
        before = termios.tcgetattr(fd)
        try:
            # No echo and no line mode; Ctrl+C still works.
            tty.setcbreak(fd, termios.TCSANOW)
            os.write(fd, _QUERY)
            reply = b""
            deadline = time.monotonic() + timeout
            while (left := deadline - time.monotonic()) > 0:
                ready, _, _ = select.select([fd], [], [], left)
                if not ready:
                    break
                chunk = os.read(fd, 1024)
                if not chunk:
                    break
                reply += chunk
                if _DA1_REPLY.search(reply):
                    break
            return parse_background(reply)
        finally:
            termios.tcflush(fd, termios.TCIFLUSH)
            termios.tcsetattr(fd, termios.TCSANOW, before)
    except (OSError, termios.error):
        return None
    finally:
        os.close(fd)


def pick_theme(background: RGB | None, env: Mapping[str, str] = os.environ) -> str:
    """`TEXTUAL_THEME` when it names one of the two themes; else light for a
    light background; else dark."""
    forced = env.get("TEXTUAL_THEME", "")
    if forced in (DARK, LIGHT):
        return forced
    return LIGHT if background is not None and is_light(background) else DARK
