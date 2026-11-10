"""The two themes, and which one a terminal gets (ui_design section 2)."""

import os
import pty
import threading
import time

import pytest

from kiln_harness_tuner.tui import theme

DA1 = b"\x1b[?62;22c"


@pytest.mark.parametrize(
    "reply,expected",
    [
        (b"\x1b]11;rgb:ffff/ffff/ffff\x1b\\", (1.0, 1.0, 1.0)),
        (b"\x1b]11;rgb:ff/00/ff\x07", (1.0, 0.0, 1.0)),
        (b"\x1b]11;rgba:0000/ffff/0000/ffff\x1b\\", (0.0, 1.0, 0.0)),
        (b"\x1b]11;rgb:f/0/0\x07" + DA1, (1.0, 0.0, 0.0)),
    ],
)
def test_the_background_is_read_from_each_reply_form(reply, expected):
    assert theme.parse_background(reply) == pytest.approx(expected)


@pytest.mark.parametrize("reply", [b"", DA1, b"\x1b]11;rgb:zz/00/00\x07", b"abc"])
def test_a_reply_with_no_color_gives_none(reply):
    assert theme.parse_background(reply) is None


def test_light_is_by_luminance():
    assert theme.is_light((1.0, 1.0, 1.0))
    assert theme.is_light((0.9, 0.9, 0.85))
    assert not theme.is_light((0.12, 0.12, 0.18))
    # Pure blue is dark: the eye weighs green most.
    assert not theme.is_light((0.0, 0.0, 1.0))


@pytest.mark.parametrize(
    "env,background,expected",
    [
        ({"TEXTUAL_THEME": "kiln-light"}, None, theme.LIGHT),
        ({"TEXTUAL_THEME": "kiln-dark"}, (1.0, 1.0, 1.0), theme.DARK),
        # Another theme's name is not ours to use: the background decides.
        ({"TEXTUAL_THEME": "nord"}, (1.0, 1.0, 1.0), theme.LIGHT),
        ({}, (0.95, 0.95, 0.95), theme.LIGHT),
        ({}, (0.1, 0.1, 0.1), theme.DARK),
        ({}, None, theme.DARK),
    ],
)
def test_the_theme_follows_the_terminal(env, background, expected):
    assert theme.pick_theme(background, env) == expected


def test_both_themes_have_the_custom_variables():
    for kiln_theme in theme.THEMES:
        assert set(theme.CUSTOM_VARIABLES) <= set(kiln_theme.variables)


@pytest.fixture
def terminal():
    """A pseudo-terminal: the query opens its far end by path, and the test
    answers on the near end as the terminal would."""
    near, far = pty.openpty()
    yield near, os.ttyname(far)
    os.close(near)
    os.close(far)


def answer(near: int, reply: bytes) -> threading.Thread:
    def run() -> None:
        asked = b""
        while b"\x1b[c" not in asked:
            asked += os.read(near, 1024)
        os.write(near, reply)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_the_query_reads_the_terminal_answer(terminal):
    near, path = terminal
    answer(near, b"\x1b]11;rgb:ffff/ffff/ffff\x1b\\" + DA1)
    assert theme.query_background(5.0, tty_path=path) == (1.0, 1.0, 1.0)


def test_a_terminal_that_answers_only_da1_gives_none_at_once(terminal):
    near, path = terminal
    answer(near, DA1)
    began = time.monotonic()
    assert theme.query_background(5.0, tty_path=path) is None
    assert time.monotonic() - began < 2.0


def test_a_silent_terminal_gives_none_after_the_timeout(terminal):
    _near, path = terminal
    began = time.monotonic()
    assert theme.query_background(0.2, tty_path=path) is None
    assert 0.15 <= time.monotonic() - began < 2.0


def test_no_terminal_gives_none(tmp_path):
    plain_file = tmp_path / "not-a-tty"
    plain_file.write_text("")
    assert theme.query_background(0.1, tty_path=str(plain_file)) is None
    assert theme.query_background(0.1, tty_path=str(tmp_path / "missing")) is None
