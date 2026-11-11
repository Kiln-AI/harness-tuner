"""`harness-tuner`'s console script: it stops on native Windows, else it
starts `cli.main`.

`cli.py` imports modules that are Unix only (`fcntl`), so on native Windows
the import itself fails with a traceback. This module imports nothing from the
package before the check, so the user gets a message that says what to do.
WSL reports `sys.platform == "linux"`, so a run in WSL passes the check.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

NATIVE_WINDOWS_MESSAGE = """\
harness-tuner: native Windows is not supported.
Use WSL 2 (Windows Subsystem for Linux). Install harness-tuner in a WSL shell,
and run it there.
To install WSL: https://learn.microsoft.com/windows/wsl/install"""


def main(argv: Sequence[str] | None = None) -> int:
    if sys.platform == "win32":
        print(NATIVE_WINDOWS_MESSAGE, file=sys.stderr)
        return 1
    from kiln_harness_tuner.cli import main as cli_main

    return cli_main(argv)
