"""A command the operator typed (`--agent-cli`), read the same on every OS.

On Windows a path's backslashes are separators, not escapes, and an
executable carries an extension (`claude.exe`, npm's `claude.cmd`). Quotes
group words on every OS: `"C:\\Program Files\\nodejs\\claude.cmd" -p`.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import PurePosixPath, PureWindowsPath

_WINDOWS_EXECUTABLE_SUFFIXES = frozenset({".exe", ".cmd", ".bat", ".com"})


def split_command(text: str) -> list[str]:
    """`text` split into words as a shell would. Raises ValueError on an
    unbalanced quote."""
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    if sys.platform == "win32":
        lexer.escape = ""
    return list(lexer)


def executable_name(word: str) -> str:
    """The program a command's first word names: its last path part. On
    Windows, whose names ignore case, in lower case and without the
    executable's extension."""
    if sys.platform != "win32":
        return PurePosixPath(word).name
    path = PureWindowsPath(word)
    suffix = path.suffix.lower()
    return (path.stem if suffix in _WINDOWS_EXECUTABLE_SUFFIXES else path.name).lower()
