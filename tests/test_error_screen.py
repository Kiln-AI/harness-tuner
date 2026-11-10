"""The error screen: Kiln's message out of an error body, the body and the
ending for each kind of stop, and the screen on a terminal and as plain
text."""

import io
import re

import pytest

from kiln_harness_tuner.kiln import KilnAPIError, KilnTransportError, kiln_error_text
from kiln_harness_tuner.onboarding.error_screen import (
    RunProgress,
    ending_lines,
    error_body,
    show_error,
)
from kiln_harness_tuner.orchestrator import SpendMeter

PROG = "kiln-harness-tuner"
HOST = "http://localhost:8757"
CONFIG_PATH = (
    "/api/projects/424242189194/tasks/424242920784/evals/424242136270"
    "/eval_config/424242913607"
)
JINJA_BODY = {
    "error_messages": [
        ": Value error, Invalid Jinja2 template: No filter named 'trace_overview'"
    ]
}


ANSI_CODE = re.compile(r"\x1b\[[0-9;]*m")


class TerminalStream(io.StringIO):
    def isatty(self) -> bool:
        return True

    def text(self) -> str:
        """What the terminal shows, without the colour codes."""
        return ANSI_CODE.sub("", self.getvalue())


@pytest.fixture
def terminal(monkeypatch) -> TerminalStream:
    """A colour terminal on stderr, 80 columns wide."""
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("TERM", "xterm-256color")
    for name in ("NO_COLOR", "FORCE_COLOR", "TTY_COMPATIBLE", "TTY_INTERACTIVE"):
        monkeypatch.delenv(name, raising=False)
    return TerminalStream()


# ---- kiln_error_text ----


@pytest.mark.parametrize(
    "body,text",
    [
        (JINJA_BODY, "Invalid Jinja2 template: No filter named 'trace_overview'"),
        ({"error_messages": ["one", "Value error, two"]}, "one; two"),
        ({"detail": "Eval not found"}, "Eval not found"),
        (
            {
                "detail": [
                    {"loc": ["body"], "msg": "Value error, bad name"},
                    {"loc": ["query"], "msg": "field required"},
                    "not an item",
                ]
            },
            "bad name; field required",
        ),
        ("Internal Server Error", "Internal Server Error"),
        ({"error_messages": [], "other": 1}, "{'error_messages': [], 'other': 1}"),
        (None, "None"),
    ],
    ids=[
        "error_messages",
        "several error_messages",
        "detail string",
        "detail list",
        "plain body",
        "no message",
        "empty body",
    ],
)
def test_kiln_error_text_reads_kilns_own_message(body, text):
    assert kiln_error_text(body) == text


def test_kiln_error_text_cuts_a_long_message_to_500_characters():
    text = kiln_error_text({"detail": "x" * 600})
    assert len(text) == 500
    assert text == "x" * 499 + "…"
    assert kiln_error_text("y" * 500) == "y" * 500


def test_a_kiln_api_error_carries_kilns_message():
    error = KilnAPIError("GET", CONFIG_PATH, 422, JINJA_BODY)
    assert error.kiln_message == (
        "Invalid Jinja2 template: No filter named 'trace_overview'"
    )


# ---- error_body ----


def test_a_kiln_api_errors_body_is_the_request_and_kilns_message():
    error = KilnAPIError("GET", CONFIG_PATH, 422, JINJA_BODY)
    assert error_body(error, host=HOST) == [
        f"GET {CONFIG_PATH}",
        "HTTP 422: Invalid Jinja2 template: No filter named 'trace_overview'",
    ]


def test_a_lost_connection_asks_whether_kiln_is_still_running():
    error = KilnTransportError("GET /ping failed after 4 attempts: ConnectError()")
    assert error_body(error, host=HOST) == [
        "GET /ping failed after 4 attempts: ConnectError()",
        f"Is Kiln still running at {HOST}?",
    ]


def test_any_other_errors_body_is_its_message_line_by_line():
    error = RuntimeError("the lock is held.\n\nfix: wait for that run to finish.")
    assert error_body(error, host=HOST) == [
        "the lock is held.",
        "",
        "fix: wait for that run to finish.",
    ]


# ---- ending_lines ----


def test_before_the_agent_cli_check_nothing_was_spent():
    assert ending_lines(RunProgress()) == [
        "Nothing was spent and no run folder was created."
    ]


def test_after_the_agent_cli_check_only_its_test_prompt_was_spent():
    assert ending_lines(RunProgress(cli_checked=True)) == [
        "The only spend was the agent CLI's one test prompt. No run folder was created."
    ]


def test_after_the_run_folder_the_ending_has_spend_folder_and_report(tmp_path):
    spend = SpendMeter()
    spend.event("spend", source="eval_job", cost_usd=3.204)
    report = tmp_path / "reports" / "kiln-harness-tuner-261107-1412.md"
    progress = RunProgress(
        cli_checked=True,
        run_dir=tmp_path / "runs" / "261107-1412",
        spend=spend,
        report_path=report,
    )
    without_report = [
        "The run stopped. Spent $3.20 so far on the task's model calls.",
        f"Run folder: {tmp_path / 'runs' / '261107-1412'}",
    ]
    # The report line only once the report file is there.
    assert ending_lines(progress) == without_report
    report.parent.mkdir()
    report.write_text("# report")
    assert ending_lines(progress) == [*without_report, f"Report: {report}"]


def test_a_run_folder_with_no_spend_meter_yet_spent_nothing(tmp_path):
    progress = RunProgress(cli_checked=True, run_dir=tmp_path)
    assert ending_lines(progress)[0] == (
        "The run stopped. Spent $0.00 so far on the task's model calls."
    )


# ---- show_error ----

BODY = [
    f"GET {CONFIG_PATH}",
    "HTTP 422: Invalid Jinja2 template: No filter named 'trace_overview'",
]
ENDING = ["Nothing was spent and no run folder was created."]


def test_on_a_terminal_the_screen_has_its_title_body_ending_and_closing_line(
    terminal, monkeypatch
):
    monkeypatch.setenv("COLUMNS", "100")
    show_error("Kiln returned an error", BODY, ENDING, prog=PROG, stream=terminal)
    assert terminal.text() == (
        "\n"
        "✗ Kiln returned an error\n"
        "\n"
        f"  GET {CONFIG_PATH}\n"
        "  HTTP 422: Invalid Jinja2 template: No filter named 'trace_overview'\n"
        "\n"
        "  Nothing was spent and no run folder was created.\n"
        "\n"
        "kiln-harness-tuner stopped with an error (exit 1)\n"
    )


def test_on_a_terminal_a_long_line_wraps_inside_the_indent(terminal):
    words = " ".join(["word"] * 30)
    show_error("Setup stopped the run", [words], ENDING, prog=PROG, stream=terminal)
    assert terminal.text().splitlines()[3:5] == [
        "  " + " ".join(["word"] * 15),
        "  " + " ".join(["word"] * 15),
    ]


def test_on_a_terminal_a_blank_body_line_is_empty(terminal):
    show_error(
        "The run stopped", ["one.", "", "fix: two."], ENDING, prog=PROG, stream=terminal
    )
    assert terminal.text().splitlines()[3:6] == ["  one.", "", "  fix: two."]


def test_on_a_terminal_a_request_or_a_path_is_never_broken(terminal, tmp_path):
    # At 80 columns: a request line, and a report path long enough that its
    # label and path don't fit on one line. The terminal wraps them, with
    # nothing put inside them, so they copy out whole.
    error = KilnAPIError("GET", CONFIG_PATH, 500, "Internal Server Error")
    run_dir = tmp_path / "firstname.lastname" / "projects" / "harness-tuner" / "runs"
    report = run_dir.parent / "reports" / "kiln-harness-tuner-261107-1412.md"
    report.parent.mkdir(parents=True)
    report.write_text("# report")
    ending = ending_lines(RunProgress(run_dir=run_dir, report_path=report))
    show_error(
        "Kiln returned an error",
        error_body(error, host=HOST),
        ending,
        prog=PROG,
        stream=terminal,
    )
    lines = terminal.text().splitlines()
    assert f"  GET {CONFIG_PATH}" in lines
    assert f"  Run folder: {run_dir}" in lines
    assert f"  Report: {report}" in lines
    assert len(f"  Report: {report}") > 80


def test_on_a_terminal_a_word_wider_than_the_line_is_kept_whole(terminal):
    path = (
        "/home/firstname.lastname/projects/harness-tuner/runs"
        "/.lock-424242748186-424242783061"
    )
    assert len(path) > 78
    line = f"another run holds the lock at {path} — wait for it to finish."
    show_error(
        "Another run is already using this task",
        [line],
        ENDING,
        prog=PROG,
        stream=terminal,
    )
    assert terminal.text().splitlines()[3:6] == [
        "  another run holds the lock at",
        f"  {path}",
        "  — wait for it to finish.",
    ]


def test_on_a_terminal_markup_and_emoji_codes_print_as_typed(terminal):
    body = ["eval [bold]Tone[/] :ok: failed"]
    show_error("The run stopped", body, ENDING, prog=PROG, stream=terminal)
    assert "  eval [bold]Tone[/] :ok: failed\n" in terminal.text()


def test_on_a_terminal_the_title_is_bold_red_and_the_closing_line_dim(terminal):
    show_error("Kiln returned an error", BODY, ENDING, prog=PROG, stream=terminal)
    text = terminal.getvalue()
    assert "\x1b[1;31m✗ Kiln returned an error\x1b[0m" in text
    assert "\x1b[2mkiln-harness-tuner stopped with an error (exit 1)\x1b[0m" in text


def test_without_a_terminal_the_screen_is_plain_lines(monkeypatch):
    # Whatever rich is told: a script reading stderr gets plain lines.
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("TTY_COMPATIBLE", "1")
    stream = io.StringIO()
    body = ["the lock is held.", "", "fix: wait.", "HTTP 400: one\ntwo"]
    show_error(
        "Another run is already using this task", body, ENDING, prog=PROG, stream=stream
    )
    assert stream.getvalue() == (
        "kiln-harness-tuner: error: Another run is already using this task\n"
        "  the lock is held.\n"
        "\n"
        "  fix: wait.\n"
        "  HTTP 400: one\n"
        "  two\n"
        "  Nothing was spent and no run folder was created.\n"
        "kiln-harness-tuner stopped with an error (exit 1)\n"
    )


def test_an_error_with_no_body_goes_straight_to_its_ending(terminal):
    show_error("Kiln isn't running", [], ENDING, prog=PROG, stream=terminal)
    assert terminal.text() == (
        "\n✗ Kiln isn't running\n\n"
        "  Nothing was spent and no run folder was created.\n\n"
        "kiln-harness-tuner stopped with an error (exit 1)\n"
    )


def test_the_agents_tokens_are_named_apart_from_the_spend(tmp_path):
    # The agents run on the agent CLI's account: their tokens follow the
    # spend line, for information, and are not in it.
    spend = SpendMeter()
    spend.event("spend", source="preflight", cost_usd=1.2, tokens=1_234_567)
    spend.event("spend", source="eval_jobs", cost_usd=2.004)
    progress = RunProgress(cli_checked=True, run_dir=tmp_path, spend=spend)
    assert ending_lines(progress)[:2] == [
        "The run stopped. Spent $2.00 so far on the task's model calls.",
        "The agents used about 1,234,567 tokens, which the budget does not count.",
    ]
