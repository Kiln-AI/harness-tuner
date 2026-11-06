"""The shared retry prompt (`resolve_problem`) and the real terminal."""

import io

import pytest
from onboarding_fakes import ScriptedTerminal

from kiln_harness_tuner.onboarding import terminal as terminal_module
from kiln_harness_tuner.onboarding.errors import OnboardingError, OnboardingStopped
from kiln_harness_tuner.onboarding.terminal import (
    Problem,
    Resolution,
    StdTerminal,
    resolve_problem,
)

PROBLEM = Problem(
    "Kiln isn't running.", open_url="https://kiln.tech", open_label="kiln.tech"
)


class Check:
    """A check that reports `problems` in turn, then passes."""

    def __init__(self, *problems: Problem):
        self.problems = list(problems)
        self.runs = 0

    def __call__(self) -> Problem | None:
        self.runs += 1
        return self.problems.pop(0) if self.problems else None


def test_resolve_problem_returns_ok_when_the_check_passes():
    terminal = ScriptedTerminal()
    assert resolve_problem(Check(), terminal) is Resolution.OK
    assert terminal.out == [] and terminal.prompts == []


def test_retry_runs_the_check_again_until_it_passes():
    check = Check(PROBLEM, PROBLEM)
    terminal = ScriptedTerminal(["r", "r"])
    assert resolve_problem(check, terminal) is Resolution.OK
    assert check.runs == 3
    # The message is printed again after each failed retry.
    assert terminal.out == [PROBLEM.message, PROBLEM.message]
    assert len(terminal.prompts) == 2


def test_enter_is_retry():
    check = Check(PROBLEM)
    assert resolve_problem(check, ScriptedTerminal([""])) is Resolution.OK
    assert check.runs == 2


def test_open_opens_the_url_and_asks_again_without_rechecking():
    check = Check(PROBLEM)
    terminal = ScriptedTerminal(["o", "r"])
    assert resolve_problem(check, terminal) is Resolution.OK
    assert terminal.opened == ["https://kiln.tech"]
    assert check.runs == 2
    assert terminal.out == [PROBLEM.message]
    assert len(terminal.prompts) == 2


def test_the_prompt_shows_only_the_options_that_apply():
    def prompt_for(problem, **kwargs):
        terminal = ScriptedTerminal(["r"])
        resolve_problem(Check(problem), terminal, **kwargs)
        return terminal.prompts[0]

    assert prompt_for(PROBLEM) == "[r]etry  [o]pen kiln.tech  e[x]it: "
    assert prompt_for(Problem("No URL.")) == "[r]etry  e[x]it: "
    assert (
        prompt_for(PROBLEM, offer_pick_task=True)
        == "[r]etry  [o]pen kiln.tech  [t]ask: pick another  e[x]it: "
    )
    # With no label, the URL itself names what opens.
    assert (
        prompt_for(Problem("Unlabelled.", open_url="http://h/x"))
        == "[r]etry  [o]pen http://h/x  e[x]it: "
    )


def test_open_is_not_an_option_without_a_url():
    check = Check(Problem("No URL."))
    terminal = ScriptedTerminal(["o", "r"])
    assert resolve_problem(check, terminal) is Resolution.OK
    assert terminal.opened == []
    assert len(terminal.prompts) == 2


def test_pick_task_only_when_offered():
    offered = ScriptedTerminal(["t"])
    resolution = resolve_problem(Check(PROBLEM), offered, offer_pick_task=True)
    assert resolution is Resolution.PICK_TASK

    check = Check(PROBLEM)
    not_offered = ScriptedTerminal(["t", "r"])
    assert resolve_problem(check, not_offered) is Resolution.OK
    assert len(not_offered.prompts) == 2 and check.runs == 2


def test_unknown_answers_ask_again_and_answers_are_normalised():
    check = Check(PROBLEM)
    terminal = ScriptedTerminal(["zz", "retry", " R "])
    assert resolve_problem(check, terminal) is Resolution.OK
    assert len(terminal.prompts) == 3
    assert check.runs == 2


@pytest.mark.parametrize("inputs", [["x"], [" X "], []], ids=["x", "X", "eof"])
def test_exit_and_end_of_input_stop_with_1(inputs):
    with pytest.raises(OnboardingStopped) as stopped:
        resolve_problem(Check(PROBLEM), ScriptedTerminal(inputs))
    assert stopped.value.exit_code == 1


def test_not_interactive_raises_the_message_without_asking():
    check = Check(PROBLEM)
    terminal = ScriptedTerminal(["r"], interactive=False)
    with pytest.raises(OnboardingError) as error:
        resolve_problem(check, terminal)
    assert str(error.value) == PROBLEM.message
    assert terminal.prompts == [] and terminal.out == []
    assert check.runs == 1


class FakeStream(io.StringIO):
    def __init__(self, tty: bool):
        super().__init__()
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


@pytest.mark.parametrize(
    "stdin_tty,stdout_tty,interactive",
    [(True, True, True), (True, False, False), (False, True, False)],
)
def test_std_terminal_is_interactive_only_when_both_streams_are_terminals(
    monkeypatch, stdin_tty, stdout_tty, interactive
):
    monkeypatch.setattr("sys.stdin", FakeStream(stdin_tty))
    monkeypatch.setattr("sys.stdout", FakeStream(stdout_tty))
    assert StdTerminal("prog").interactive is interactive


def test_std_terminal_is_not_interactive_without_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", None)
    monkeypatch.setattr("sys.stdout", FakeStream(True))
    assert StdTerminal("prog").interactive is False


def test_std_terminal_says_to_stdout_and_warns_to_stderr_with_the_prefix(capsys):
    terminal = StdTerminal("prog")
    terminal.say("hello")
    terminal.warn("careful")
    captured = capsys.readouterr()
    assert captured.out == "hello\n"
    assert captured.err == "prog: careful\n"


def test_std_terminal_ask_returns_the_line(monkeypatch):
    prompts = []

    def fake_input(prompt):
        prompts.append(prompt)
        return "r"

    monkeypatch.setattr("builtins.input", fake_input)
    assert StdTerminal("prog").ask("Go? ") == "r"
    assert prompts == ["Go? "]


@pytest.mark.parametrize(
    "raised,exit_code", [(EOFError, 1), (KeyboardInterrupt, 130)], ids=["eof", "ctrlc"]
)
def test_std_terminal_ask_stops_on_eof_and_ctrl_c(
    monkeypatch, capsys, raised, exit_code
):
    def fake_input(_prompt):
        raise raised

    monkeypatch.setattr("builtins.input", fake_input)
    with pytest.raises(OnboardingStopped) as stopped:
        StdTerminal("prog").ask("Go? ")
    assert stopped.value.exit_code == exit_code
    # A newline, so the next line does not start after the prompt.
    assert capsys.readouterr().out == "\n"


def test_std_terminal_open_url_opens_the_browser_and_prints_the_url(
    monkeypatch, capsys
):
    opened = []
    monkeypatch.setattr(terminal_module.webbrowser, "open", opened.append)
    StdTerminal("prog").open_url("https://kiln.tech")
    assert opened == ["https://kiln.tech"]
    assert capsys.readouterr().out == "https://kiln.tech\n"
