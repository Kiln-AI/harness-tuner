"""`PlainTerminal`: the start's terminal when no one is there to ask. The
guided start's prompts are tested in the app (`tests/tui/test_startup.py`)."""

import pytest

from kiln_harness_tuner.onboarding.console import PlainTerminal
from kiln_harness_tuner.onboarding.flow import WELCOME_DESCRIPTION
from kiln_harness_tuner.onboarding.terminal import Action, ActionKind, Option

PROG = "kiln-harness-tuner"

PROJECTS = [
    Option("p1", "Forum Moderation", ("424242975950", "~/Kiln Projects/Forum")),
    Option("p2", "Support Triage", ("118230554411", "~/Kiln Projects/Support")),
]
ACTIONS = [
    Action("r", "Check again", ActionKind.RETRY),
    Action("x", "Exit", ActionKind.EXIT),
]


# ---- PlainTerminal ----


def test_plain_terminal_prints_plain_lines(capsys):
    terminal = PlainTerminal(PROG)
    assert terminal.interactive is False
    terminal.welcome("Kiln Harness Tuner", "0.1.0", WELCOME_DESCRIPTION)
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
