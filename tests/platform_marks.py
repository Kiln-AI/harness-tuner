"""Marks for the tests that cannot run on every OS, each with its reason.

CI runs the suite on Linux, macOS and Windows. A test that needs what one OS
does not have is marked here, never skipped by file."""

from __future__ import annotations

import os
import sys
import tempfile

import pytest


def posix_only(reason: str) -> pytest.MarkDecorator:
    """Skip on native Windows, with the reason in the skip message."""
    return pytest.mark.skipif(sys.platform == "win32", reason=reason)


def _can_symlink() -> bool:
    with tempfile.TemporaryDirectory() as folder:
        try:
            os.symlink(folder, os.path.join(folder, "link"), target_is_directory=True)
        except (OSError, NotImplementedError):
            return False
    return True


requires_symlinks = pytest.mark.skipif(
    not _can_symlink(),
    reason="this account cannot create symlinks (on Windows that needs"
    " Developer Mode or an administrator)",
)
