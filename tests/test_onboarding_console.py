"""The real terminals: `RichTerminal` driven through prompt_toolkit's piped
input, its output on a captured console, the browser rule, and
`PlainTerminal`."""

import io
import sys
from collections.abc import Iterator
from contextlib import ExitStack

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from kiln_harness_tuner.onboarding import console as console_module
from kiln_harness_tuner.onboarding.console import (
    PlainTerminal,
    RichTerminal,
    aligned_rows,
    default_terminal,
    graphical_browser_expected,
)
from kiln_harness_tuner.onboarding.errors import OnboardingStopped
from kiln_harness_tuner.onboarding.terminal import ENTER as ENTER_ACTION_KEY
from kiln_harness_tuner.onboarding.terminal import Action, ActionKind, Option

PROG = "kiln-harness-tuner"
DOWN = "\x1b[B"
UP = "\x1b[A"
ENTER = "\r"
CTRL_C = "\x03"
CTRL_D = "\x04"
BACKSPACE = "\x7f"

PROJECTS = [
    Option("p1", "Forum Moderation", ("424242975950", "~/Kiln Projects/Forum")),
    Option("p2", "Support Triage", ("118230554411", "~/Kiln Projects/Support")),
    Option("p3", "Spam", ("731954460212", "")),
]
ACTIONS = [
    Action("r", "Check again", ActionKind.RETRY),
    Action("d", "Open the download page", ActionKind.OPEN, url="https://k/dl"),
    Action("x", "Exit", ActionKind.EXIT),
]


def captured_console(**kwargs) -> Console:
    return Console(
        file=io.StringIO(), force_terminal=True, color_system=None, width=200, **kwargs
    )


def output(terminal: RichTerminal) -> str:
    return terminal.console.file.getvalue()


@pytest.fixture
def keys() -> Iterator:
    """`keys(text)`: a `RichTerminal` whose next prompt reads `text` as
    typed, then end of input. A prompt that wants more than `text` ends as
    Ctrl-D would, so a broken key fails the test instead of hanging it."""
    with ExitStack() as stack:

        def terminal(typed: str) -> RichTerminal:
            pipe = stack.enter_context(create_pipe_input())
            pipe.send_text(typed)
            pipe.close()
            return RichTerminal(
                PROG,
                console=captured_console(),
                pt_input=pipe,
                pt_output=DummyOutput(),
            )

        yield terminal


# ---- pick ----


def test_pick_by_arrow_keys(keys):
    assert keys(DOWN + DOWN + UP + ENTER).pick("Which?", PROJECTS) == "p2"


def test_pick_by_filter_text_on_a_column(keys):
    assert keys("1182" + ENTER).pick("Which?", PROJECTS) == "p2"
    assert keys("spa" + ENTER).pick("Which?", PROJECTS) == "p3"


CLIS = [
    Option("claude", "Claude Code"),
    Option("codex", "Codex", disabled="not installed"),
    Option("opencode", "OpenCode"),
]


def stops(terminal: RichTerminal, options) -> bool:
    """Whether the pick ran out of input (exit 1) without picking."""
    with pytest.raises(OnboardingStopped) as stopped:
        terminal.pick("Which?", options)
    return stopped.value.exit_code == 1


def test_pick_skips_a_disabled_option(keys):
    assert keys(DOWN + ENTER).pick("Which CLI?", CLIS) == "opencode"
    assert keys(UP + UP + ENTER).pick("Which CLI?", CLIS) == "claude"


def test_enter_never_picks_a_disabled_option_the_filter_leaves(keys):
    assert stops(keys("codex" + ENTER), CLIS)


def test_a_filter_puts_the_cursor_on_its_first_enabled_row(keys):
    disabled_first = [CLIS[1], CLIS[2], CLIS[0]]  # Codex, OpenCode, Claude Code
    # `co` matches all three; the cursor skips Codex.
    assert keys("co" + ENTER).pick("Which CLI?", disabled_first) == "opencode"
    # Backspace re-filters, and the cursor skips it again.
    typed = "cox" + BACKSPACE + ENTER
    assert keys(typed).pick("Which CLI?", disabled_first) == "opencode"


def test_the_arrows_skip_disabled_rows_of_the_filtered_list(keys):
    options = [
        Option("zed", "Zed"),
        Option("copilot", "Copilot"),
        Option("codex", "Codex", disabled="not installed"),
        Option("opencode", "OpenCode"),
    ]
    # `co` leaves Copilot, Codex and OpenCode, with the cursor on Copilot.
    # Down and Up skip Codex by its place in what is shown.
    assert keys("co" + DOWN + ENTER).pick("Which CLI?", options) == "opencode"
    assert keys("co" + UP + ENTER).pick("Which CLI?", options) == "opencode"


def test_enter_does_nothing_while_the_filter_matches_nothing(keys):
    assert stops(keys("zzz" + ENTER), PROJECTS)
    clear = BACKSPACE * 3
    assert keys("zzz" + ENTER + clear + DOWN + ENTER).pick("Which?", PROJECTS) == "p2"


def test_pick_starts_on_its_default(keys):
    assert keys(ENTER).pick("Which?", PROJECTS, default="p2") == "p2"
    assert keys(DOWN + ENTER).pick("Which?", PROJECTS, default="p2") == "p3"


def test_pick_starts_on_the_first_enabled_row_for_a_disabled_default(keys):
    assert keys(ENTER).pick("Which CLI?", CLIS, default="codex") == "claude"
    assert keys(ENTER).pick("Which CLI?", CLIS, default="nothing") == "claude"


def test_pick_rows_are_aligned_across_options():
    assert aligned_rows(PROJECTS) == [
        "Forum Moderation   424242975950   ~/Kiln Projects/Forum",
        "Support Triage     118230554411   ~/Kiln Projects/Support",
        "Spam               731954460212",
    ]
    assert aligned_rows([Option(1, "a"), Option(2, "bb", ("x",))]) == [
        "a",
        "bb   x",
    ]


# ---- act ----


def test_act_by_its_key_with_no_enter(keys):
    # Nothing follows the `d`: the key acts at once.
    assert keys("d").act("What next?", ACTIONS) is ACTIONS[1]


def test_act_by_arrows_and_enter(keys):
    assert keys(DOWN + DOWN + ENTER).act("What next?", ACTIONS) is ACTIONS[2]


def test_act_ignores_a_key_no_action_has(keys):
    assert keys("q" + ENTER).act("What next?", ACTIONS) is ACTIONS[0]


ADVANCED = [
    Action(ENTER_ACTION_KEY, "Keep these", ActionKind.KEEP),
    Action("c", "Customize", ActionKind.CUSTOMIZE),
]


def test_an_enter_action_is_picked_by_enter_and_the_others_by_their_keys(keys):
    assert keys(ENTER).act("Advanced options", ADVANCED) is ADVANCED[0]
    assert keys("c").act("Advanced options", ADVANCED) is ADVANCED[1]
    assert keys(DOWN + ENTER).act("Advanced options", ADVANCED) is ADVANCED[1]


def test_act_draws_each_key_before_its_label(keys, monkeypatch):
    drawn = []
    select = console_module.questionary.select

    def drawing(question, choices, **kwargs):
        drawn.append([choice.title for choice in choices])
        return select(question, choices=choices, **kwargs)

    monkeypatch.setattr(console_module.questionary, "select", drawing)
    keys(ENTER).act("Advanced options", ADVANCED)
    keys(ENTER).act("What next?", ACTIONS)
    assert drawn == [
        ["Enter) Keep these", "c) Customize"],
        ["r) Check again", "d) Open the download page", "x) Exit"],
    ]


# ---- text ----


def test_text_enter_accepts_the_default(keys):
    assert keys(ENTER).text("Budget?", default="50", hint="USD") == "50"


def test_text_typed_after_the_default(keys):
    assert keys(BACKSPACE * 2 + "12.5" + ENTER).text("Budget?", default="50") == "12.5"


def test_text_rejects_an_invalid_answer_and_keeps_what_was_typed(keys):
    seen = []

    def validate(text):
        seen.append(text)
        return None if text.isdigit() else "Enter a number"

    # `50x` is refused; one backspace fixes what is still in the field.
    terminal = keys("x" + ENTER + BACKSPACE + ENTER)
    assert terminal.text("Budget?", default="50", validate=validate) == "50"
    assert "50x" in seen


# ---- stopping ----

PROMPTS = {
    "pick": lambda t: t.pick("Which?", PROJECTS),
    "act": lambda t: t.act("What next?", ACTIONS),
    "text": lambda t: t.text("Budget?", default="50"),
}


@pytest.mark.parametrize("prompt", PROMPTS)
@pytest.mark.parametrize(
    "key,exit_code", [(CTRL_C, 130), (CTRL_D, 1)], ids=["ctrl-c", "ctrl-d"]
)
def test_ctrl_c_and_ctrl_d_stop_every_prompt(keys, prompt, key, exit_code):
    # The Enter after the key would answer a prompt that ignored it.
    with pytest.raises(OnboardingStopped) as stopped:
        PROMPTS[prompt](keys(key + ENTER))
    assert stopped.value.exit_code == exit_code


# ---- output ----


def test_header_step_warn_and_say():
    terminal = RichTerminal(PROG, console=captured_console())
    terminal.header("Kiln Harness Tuner 0.1.0")
    terminal.step("ok", "Kiln", "1.2.0 at http://localhost:8757")
    terminal.step("fail", "API keys", "missing")
    terminal.step("warn", "Ready", "maybe")
    terminal.warn("Couldn't read Kiln's API list, so its version wasn't checked.")
    terminal.say("A [bold] word stays as typed.")
    assert output(terminal) == (
        "Kiln Harness Tuner 0.1.0\n"
        "\n"
        "✓ Kiln      1.2.0 at http://localhost:8757\n"
        "✗ API keys  missing\n"
        "! Ready     maybe\n"
        "! Couldn't read Kiln's API list, so its version wasn't checked.\n"
        "A [bold] word stays as typed.\n"
    )


def test_block_is_set_off_by_blank_lines_and_indented():
    terminal = RichTerminal(PROG, console=captured_console())
    terminal.block("Kiln isn't running", ["Open it.", "  1. Install it."])
    assert output(terminal) == (
        "\n✗ Kiln isn't running\n\n  Open it.\n    1. Install it.\n\n"
    )


def test_a_long_line_is_not_broken_by_the_console():
    console = Console(file=io.StringIO(), force_terminal=True, width=20)
    terminal = RichTerminal(PROG, console=console)
    command = "kiln-harness-tuner --project-id 424242975950 --task-id 4242"
    terminal.say(command)
    assert console.file.getvalue() == command + "\n"


def test_names_print_as_typed_in_lines_and_spinners():
    terminal = RichTerminal(PROG, console=captured_console())
    # A spinner's str is rich markup: a stray closing tag would raise.
    with terminal.busy("Reading Moderation [/old]'s tasks…"):
        pass
    terminal.say("kiln-harness-tuner --guidance 'Keep :ok: posts'")
    assert output(terminal).endswith("--guidance 'Keep :ok: posts'\n")


def test_colour_marks_the_symbols_and_no_color_turns_it_off(monkeypatch):
    def coloured(**env) -> str:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        console = Console(file=io.StringIO(), force_terminal=True)
        RichTerminal(PROG, console=console).step("ok", "Kiln", "up")
        return console.file.getvalue()

    monkeypatch.delenv("NO_COLOR", raising=False)
    assert "\x1b[32m✓" in coloured()
    assert "\x1b[3" not in coloured(NO_COLOR="1")


def test_busy_prints_nothing_on_a_console_that_isnt_a_terminal():
    console = Console(file=io.StringIO())
    terminal = RichTerminal(PROG, console=console)
    with terminal.busy("Checking Kiln…"):
        pass
    assert console.file.getvalue() == ""


# ---- opening a page ----


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


def test_open_url_prints_the_link_then_opens_the_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(console_module, "graphical_browser_expected", lambda: True)
    monkeypatch.setattr(console_module.webbrowser, "open", opened.append)
    terminal = RichTerminal(PROG, console=captured_console())
    terminal.open_url("https://kiln.tech/download")
    assert output(terminal) == "Opening https://kiln.tech/download\n"
    assert opened == ["https://kiln.tech/download"]


def test_open_url_with_no_graphical_browser_only_prints_the_link(monkeypatch):
    def no_browser(url):
        raise AssertionError("a browser started on a headless shell")

    monkeypatch.setattr(console_module, "graphical_browser_expected", lambda: False)
    monkeypatch.setattr(console_module.webbrowser, "open", no_browser)
    terminal = RichTerminal(PROG, console=captured_console())
    terminal.open_url("https://kiln.tech/download")
    assert output(terminal) == (
        "Open this link in your browser: https://kiln.tech/download\n"
    )


# ---- PlainTerminal ----


def test_plain_terminal_prints_plain_lines(capsys):
    terminal = PlainTerminal(PROG)
    assert terminal.interactive is False
    terminal.header("Kiln Harness Tuner 0.1.0")
    with terminal.busy("Checking Kiln…"):
        terminal.step("ok", "Kiln", "1.2.0 at http://localhost:8757")
    terminal.say("hello")
    terminal.warn("Couldn't read Kiln's API list.")
    captured = capsys.readouterr()
    assert captured.out == "Kiln      1.2.0 at http://localhost:8757\nhello\n"
    assert captured.err == "kiln-harness-tuner: Couldn't read Kiln's API list.\n"


@pytest.mark.parametrize(
    "call",
    [
        lambda t: t.pick("Which?", PROJECTS),
        lambda t: t.act("What next?", ACTIONS),
        lambda t: t.text("Budget?"),
        lambda t: t.block("Title", ["body"]),
        lambda t: t.open_url("https://kiln.tech"),
    ],
    ids=["pick", "act", "text", "block", "open_url"],
)
def test_plain_terminal_never_asks(call):
    with pytest.raises(RuntimeError, match="not a guided start"):
        call(PlainTerminal(PROG))


# ---- the guided rule ----


class Stream(io.StringIO):
    def __init__(self, tty: bool):
        super().__init__()
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


@pytest.mark.parametrize(
    "stdin_tty,stdout_tty,no_interaction,guided",
    [
        (True, True, False, True),
        (True, True, True, False),
        (True, False, False, False),
        (False, True, False, False),
    ],
)
def test_the_start_is_guided_only_with_two_terminals_and_no_flag(
    monkeypatch, stdin_tty, stdout_tty, no_interaction, guided
):
    monkeypatch.setattr("sys.stdin", Stream(stdin_tty))
    monkeypatch.setattr("sys.stdout", Stream(stdout_tty))
    terminal = default_terminal(PROG, no_interaction=no_interaction)
    assert isinstance(terminal, RichTerminal if guided else PlainTerminal)
    assert terminal.interactive is guided


def test_the_start_isnt_guided_without_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", None)
    monkeypatch.setattr("sys.stdout", Stream(True))
    assert isinstance(default_terminal(PROG, no_interaction=False), PlainTerminal)
