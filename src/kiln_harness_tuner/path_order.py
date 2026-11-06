"""One order for file paths on every OS.

`sorted()` on `Path` objects ignores case on Windows and compares code
points on macOS and Linux, so a listing of `Top` and `examples/` comes out in
two orders. `path_key` sorts every path by its parts as text, case included,
which is the macOS and Linux order, on every OS.
"""

from __future__ import annotations

from pathlib import PurePath


def path_key(path: PurePath) -> tuple[str, ...]:
    """The sort key for a path: its parts, compared as case-sensitive text."""
    return path.parts
