"""Opening a page in the person's browser from the terminal."""

from __future__ import annotations

import os
import sys


def graphical_browser_expected() -> bool:
    """Whether a browser opened from here shows a window. Elsewhere, such as
    an SSH session, Python's `webbrowser` can start a console browser in the
    foreground, which hides the address until it is quit."""
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
