"""The `harness-tuner` console script stops on native Windows with a message
that recommends WSL, before it imports `cli.py`, and starts `cli.main`
everywhere else (WSL reports `linux`)."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

from kiln_harness_tuner import cli, launch

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_the_console_script_starts_at_launch():
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))
    assert pyproject["project"]["scripts"]["harness-tuner"] == (
        "kiln_harness_tuner.launch:main"
    )


def test_native_windows_stops_with_a_wsl_message_before_cli_imports(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(sys, "platform", "win32")
    # A `None` entry makes `import kiln_harness_tuner.cli` raise, as the
    # `fcntl` import does on native Windows.
    monkeypatch.setitem(sys.modules, "kiln_harness_tuner.cli", None)

    assert launch.main(["--help"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == launch.NATIVE_WINDOWS_MESSAGE + "\n"


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_other_platforms_start_cli_main(monkeypatch: pytest.MonkeyPatch, platform: str):
    monkeypatch.setattr(sys, "platform", platform)
    calls: list[object] = []

    def fake_main(argv: object = None) -> int:
        calls.append(argv)
        return 7

    monkeypatch.setattr(cli, "main", fake_main)

    assert launch.main(["--demo-project"]) == 7
    assert calls == [["--demo-project"]]
