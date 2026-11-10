"""Opening a page from the terminal: when a browser can show one, and how
the app starts it."""

import subprocess
import sys

import pytest

from kiln_harness_tuner.tui.opener import graphical_browser_expected, open_link


@pytest.mark.parametrize(
    "platform,env,expected",
    [
        ("darwin", {}, True),
        ("win32", {}, True),
        ("linux", {"DISPLAY": ":0"}, True),
        ("linux", {"WAYLAND_DISPLAY": "wayland-0"}, True),
        ("linux", {}, False),
        ("linux", {"DISPLAY": ""}, False),
    ],
)
def test_a_graphical_browser_is_expected_only_where_one_can_show(
    monkeypatch, platform, env, expected
):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert graphical_browser_expected() is expected


class FakePopen:
    started: list[tuple[list[str], dict]] = []

    def __init__(self, command, **kwargs):
        FakePopen.started.append((command, kwargs))

    def wait(self):
        return 0


@pytest.fixture
def popen(monkeypatch):
    FakePopen.started = []
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "SSH_CONNECTION", "SSH_TTY"):
        monkeypatch.delenv(name, raising=False)
    return FakePopen.started


@pytest.mark.parametrize(
    "platform,command", [("linux", "xdg-open"), ("darwin", "open")]
)
def test_a_link_opens_with_the_system_opener_detached(
    monkeypatch, popen, platform, command
):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("DISPLAY", ":0")
    assert open_link("http://localhost:8757/optimize") is True
    ((argv, kwargs),) = popen
    assert argv == [command, "http://localhost:8757/optimize"]
    # Nothing it prints reaches the app's screen, and it never waits.
    assert kwargs == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "start_new_session": True,
    }


@pytest.mark.parametrize(
    "env", [{}, {"DISPLAY": ":0", "SSH_CONNECTION": "1"}, {"SSH_TTY": "/dev/pts/1"}]
)
def test_no_link_opens_with_no_screen_to_show_it_on(monkeypatch, popen, env):
    monkeypatch.setattr(sys, "platform", "linux")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert open_link("http://localhost:8757") is False
    assert popen == []


def test_an_opener_that_does_not_start_gives_false(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("SSH_TTY", raising=False)

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("xdg-open")

    monkeypatch.setattr(subprocess, "Popen", missing)
    assert open_link("http://localhost:8757") is False
