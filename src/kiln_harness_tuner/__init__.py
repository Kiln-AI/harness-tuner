"""kiln-harness-tuner — an agentic optimize loop for Kiln tasks.

`cli.py` is the entry point and the composition root; `orchestrator.py` is the
wave loop everything else hangs off.
"""

from importlib.metadata import PackageNotFoundError, version

# `pyproject.toml` is the one place the version is set. The package reads it
# from the installed distribution's metadata, which every install uv makes
# (the editable one of `uv sync` included) has.
try:
    __version__ = version("harness-tuner")
except PackageNotFoundError:
    # Imported from a source tree that is not installed.
    __version__ = "0+unknown"
