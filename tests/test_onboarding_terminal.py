"""The shared problem loop (`resolve_problem`), `Problem`'s menu and plain
error, the picker's row rule, and the display helpers."""

from pathlib import Path

import pytest
from onboarding_fakes import ScriptedTerminal

from kiln_harness_tuner.onboarding.errors import OnboardingError, OnboardingStopped
from kiln_harness_tuner.onboarding.terminal import (
    Action,
    ActionKind,
    Option,
    Problem,
    aligned_rows,
    english_list,
    plural,
    resolve_problem,
    tilde,
)

DOWNLOAD = Action("d", "Open the download page", ActionKind.OPEN, url="https://k/dl")
DEMO = Action("m", "Use the demo project", ActionKind.USE_DEMO)
PROBLEM = Problem(
    "Kiln isn't running",
    ("Open the Kiln app.", "  1. Install it."),
    actions=(DOWNLOAD,),
    retry_label="Check again (Kiln is open now)",
)
BUSY = "Checking Kiln…"


class Check:
    """A check that reports `problems` in turn, then passes."""

    def __init__(self, *problems: Problem):
        self.problems = list(problems)
        self.runs = 0

    def __call__(self) -> Problem | None:
        self.runs += 1
        return self.problems.pop(0) if self.problems else None


def resolve(check, terminal, **kwargs):
    return resolve_problem(check, terminal, busy=BUSY, **kwargs)


def test_a_passing_check_returns_none_after_its_spinner():
    terminal = ScriptedTerminal()
    assert resolve(Check(), terminal) is None
    assert terminal.busy_texts == [BUSY]
    assert terminal.blocks == [] and terminal.asked == []


def test_check_again_runs_the_check_again_and_shows_the_block_each_time():
    check = Check(PROBLEM, PROBLEM)
    terminal = ScriptedTerminal(["r", "r"])
    assert resolve(check, terminal) is None
    assert check.runs == 3
    assert terminal.busy_texts == [BUSY] * 3
    assert terminal.blocks == [(PROBLEM.title, PROBLEM.body)] * 2
    assert terminal.questions == ["What next?"] * 2


def test_open_opens_the_page_and_asks_again_without_rechecking():
    check = Check(PROBLEM)
    terminal = ScriptedTerminal(["d", "r"])
    assert resolve(check, terminal) is None
    assert terminal.opened == ["https://k/dl"]
    assert check.runs == 2
    assert len(terminal.blocks) == 1
    assert terminal.menus == [("r", "d", "x")] * 2


def test_extra_actions_come_before_exit_and_are_returned():
    pick_task = Action("t", "Pick a different task", ActionKind.PICK_TASK)
    terminal = ScriptedTerminal(["t"])
    assert resolve(Check(PROBLEM), terminal, extra=(pick_task,)) is pick_task
    assert terminal.menus == [("r", "d", "t", "x")]


def test_a_problems_own_action_is_returned():
    problem = Problem("No projects", (), actions=(DEMO,), retry_first=False)
    terminal = ScriptedTerminal(["m"])
    assert resolve(Check(problem), terminal) is DEMO


@pytest.mark.parametrize("answers", [["x"], []], ids=["exit", "ctrl-d"])
def test_exit_and_ctrl_d_stop_with_1(answers):
    with pytest.raises(OnboardingStopped) as stopped:
        resolve(Check(PROBLEM), ScriptedTerminal(answers))
    assert stopped.value.exit_code == 1


def test_ctrl_c_at_the_menu_stops_with_130():
    with pytest.raises(OnboardingStopped) as stopped:
        resolve(Check(PROBLEM), ScriptedTerminal([OnboardingStopped(130)]))
    assert stopped.value.exit_code == 130


def test_a_problems_warnings_follow_its_block_or_precede_its_error():
    problem = Problem("Not ready", ("Add an eval.",), warnings=("1 eval failed",))
    guided = ScriptedTerminal(["r"])
    resolve(Check(problem), guided)
    assert guided.transcript[1:4] == [
        "block Not ready",
        "warn 1 eval failed",
        "act What next?",
    ]
    plain = ScriptedTerminal(interactive=False)
    with pytest.raises(OnboardingError):
        resolve(Check(problem), plain)
    assert plain.err == ["1 eval failed"]


def test_not_guided_raises_the_plain_problem_without_a_block_or_a_menu():
    check = Check(PROBLEM)
    terminal = ScriptedTerminal(interactive=False)
    with pytest.raises(OnboardingError) as error:
        resolve(check, terminal)
    assert (error.value.title, error.value.body) == (
        "Kiln isn't running",
        (
            "Open the Kiln app.",
            "  1. Install it.",
            "Open the download page: https://k/dl",
        ),
    )
    assert str(error.value) == (
        "Kiln isn't running\n"
        "  Open the Kiln app.\n"
        "    1. Install it.\n"
        "  Open the download page: https://k/dl"
    )
    assert check.runs == 1


def test_the_menu_puts_check_again_first_unless_the_problem_leads():
    extra = (Action("p", "Pick a different project", ActionKind.PICK_PROJECT),)
    leads = Problem("No tasks", (), actions=(DOWNLOAD,), retry_first=False)
    assert [a.key for a in PROBLEM.menu(extra)] == ["r", "d", "p", "x"]
    assert [a.key for a in leads.menu(extra)] == ["d", "r", "p", "x"]
    retry, *_rest, exit_action = PROBLEM.menu()
    assert (retry.key, retry.label, retry.kind) == (
        "r",
        "Check again (Kiln is open now)",
        ActionKind.RETRY,
    )
    assert (exit_action.key, exit_action.label, exit_action.kind) == (
        "x",
        "Exit",
        ActionKind.EXIT,
    )


def test_the_plain_error_leaves_out_a_link_the_body_already_gives():
    problem = Problem("Old", ("Download it from https://k/dl.",), actions=(DOWNLOAD,))
    assert problem.error().body == ("Download it from https://k/dl.",)
    # Actions that open nothing add nothing.
    assert Problem("None", (), actions=(DEMO,)).error().body == ()


def test_pick_rows_are_aligned_across_options():
    projects = [
        Option("p1", "Forum Moderation", ("424242975950", "~/Kiln Projects/Forum")),
        Option("p2", "Support Triage", ("118230554411", "~/Kiln Projects/Support")),
        Option("p3", "Spam", ("731954460212", "")),
    ]
    assert aligned_rows(projects) == [
        "Forum Moderation   424242975950   ~/Kiln Projects/Forum",
        "Support Triage     118230554411   ~/Kiln Projects/Support",
        "Spam               731954460212",
    ]
    assert aligned_rows([Option(1, "a"), Option(2, "bb", ("x",))]) == [
        "a",
        "bb   x",
    ]


def test_tilde_shows_the_home_folder_as_a_tilde():
    home = Path("/home/you")
    assert tilde(home / "Kiln Projects" / "Demo", home) == "~/Kiln Projects/Demo"
    assert tilde(home, home) == "~"
    assert tilde("/kiln-projects/Demo", home) == "/kiln-projects/Demo"
    assert tilde(Path.home() / "x") == "~/x"


def test_english_list_and_plural():
    assert english_list([]) == ""
    assert english_list(["a"]) == "a"
    assert english_list(["a", "b"]) == "a and b"
    assert english_list(["a", "b", "c"]) == "a, b and c"
    assert plural(1, "eval") == "1 eval"
    assert plural(3, "eval") == "3 evals"
    assert plural(2, "key", "keys") == "2 keys"
