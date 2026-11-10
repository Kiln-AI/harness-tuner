"""The plain block printed when the app closes (functional spec section 10)."""

import io
from pathlib import Path

from kiln_harness_tuner.onboarding import StartReport
from kiln_harness_tuner.tui.closing import (
    ClosingBlock,
    display_stopped_line,
    print_lines,
    stray_line,
)

SUMMARY = "\nReady to start\n  Project      Forum Moderation (424242975950)"
RERUN = (
    "\nTo run this again with the same answers and no questions:\n"
    "  harness-tuner --project-id 424242975950\n"
)


def report(rerun: str | None = RERUN) -> StartReport:
    return StartReport(
        project_name="Forum Moderation",
        task_name="Moderate posts",
        baseline_rc_id="424242000001",
        baseline_name="Baseline",
        summary=SUMMARY,
        rerun=rerun,
    )


def test_the_start_part_is_the_summary_and_the_rerun_command_as_said():
    closing = ClosingBlock()
    assert closing.start_lines() == []
    closing.take_start(report())
    assert closing.start_lines() == [SUMMARY, RERUN]


def test_a_start_that_was_not_guided_has_no_rerun_command():
    closing = ClosingBlock()
    closing.take_start(report(rerun=None))
    assert closing.start_lines() == [SUMMARY]


def test_a_start_with_no_report_keeps_the_last_summary_and_rerun_it_said():
    closing = ClosingBlock()
    changed = SUMMARY.replace("Forum Moderation", "Support Triage")
    for text in (SUMMARY, RERUN, "Opening https://kiln.tech/download", changed):
        closing.said(text)
    # Exit at "Start the run?" after a changed answer: the last of each.
    assert closing.start_lines() == [changed, RERUN]


def test_the_report_wins_over_what_was_said():
    closing = ClosingBlock()
    closing.said(SUMMARY.replace("Forum Moderation", "Support Triage"))
    closing.take_start(report(rerun=None))
    assert closing.start_lines() == [SUMMARY]


def test_the_run_part_names_the_result_the_report_and_the_folder():
    closing = ClosingBlock(
        run_exit_line="exit: time_limit — total $3.10 · 2h58m elapsed",
        report_path="/reports/kiln-harness-tuner-261110.md",
        best_confirmed=("Hold fields wrapper · w3-4", "424242000009"),
    )
    assert closing.run_lines(Path("/state/runs/261110-0912")) == [
        "exit: time_limit — total $3.10 · 2h58m elapsed",
        "Best Confirmed: Hold fields wrapper · w3-4 (424242000009)",
        "report: /reports/kiln-harness-tuner-261110.md",
        "Run folder: /state/runs/261110-0912",
    ]


def test_a_stopped_run_has_no_report():
    closing = ClosingBlock(
        run_exit_line="exit: cancelled — total $0.40 · 3m elapsed",
        best_confirmed=("424242000009", "424242000009"),
    )
    assert closing.run_lines(None) == [
        "exit: cancelled — total $0.40 · 3m elapsed",
        "Best Confirmed: 424242000009",
        "no report",
    ]


def test_a_run_that_never_reached_its_exit_prints_no_run_part():
    # The error screen says how it ended.
    assert ClosingBlock(report_path="/r.md").run_lines(Path("/runs/x")) == []


def test_printing_to_a_closed_terminal_is_dropped():
    out = io.StringIO()
    print_lines(["one", "two"], out)
    assert out.getvalue() == "one\ntwo\n"

    class Closed(io.StringIO):
        def write(self, _text):
            raise OSError(5, "Input/output error")

    print_lines(["lost"], Closed())


def test_the_display_stopped_and_stray_output_lines():
    assert display_stopped_line(RuntimeError("bad refresh")) == (
        "The display stopped: RuntimeError: bad refresh. The run goes on with"
        " plain lines; details in the run log."
    )
    assert display_stopped_line(ZeroDivisionError()).startswith(
        "The display stopped: ZeroDivisionError. "
    )
    assert stray_line("harness-tuner", Path("/tmp/harness-tuner-x.log")) == (
        "harness-tuner: output written while the app was open: /tmp/harness-tuner-x.log"
    )
