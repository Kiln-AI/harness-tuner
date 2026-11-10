"""Opening a page from the terminal: when a browser can show one."""

import sys

import pytest

from kiln_harness_tuner.tui.opener import graphical_browser_expected


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
