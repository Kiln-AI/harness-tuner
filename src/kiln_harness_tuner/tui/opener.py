"""Opening a page in the person's browser from the terminal.

The plain path imports this module, so it must not import Textual.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading


def graphical_browser_expected() -> bool:
    """Whether a browser opened from here shows a window. Elsewhere, such as
    an SSH session, Python's `webbrowser` can start a console browser in the
    foreground, which hides the address until it is quit."""
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def over_ssh() -> bool:
    """A browser started here would open on the remote host, not on the
    person's own screen."""
    return bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))


def open_link(url: str) -> bool:
    """Start the system's opener for `url`, and do not wait for it. False
    when no browser window can show here, or the opener does not start; the
    caller then shows the link as text.

    Not `webbrowser`: it waits for the browser command and lets it write on
    the terminal, which the app owns, and it can start a console browser
    that fights the app for the terminal."""
    if not graphical_browser_expected() or over_ssh():
        return False
    command = ["open", url] if sys.platform == "darwin" else ["xdg-open", url]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    # Reaped off the caller's thread, so it does not stay a zombie.
    threading.Thread(target=process.wait, name="open-link", daemon=True).start()
    return True
