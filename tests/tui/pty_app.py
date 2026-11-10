"""A child process for `test_pty.py`: `run_app` in `app` mode on a real
terminal, with a fake start and a fake run.

    python pty_app.py <work folder> <wait | finish | ask>

The fake run writes to stdout and stderr in each way a stray writer does,
then makes the file `started`. With `wait` it runs until it is cancelled;
with `finish` it ends at once and the app shows Done. Its teardown makes
the file `teardown`. With `ask` the process is `cli.main` itself, which
picks the app for a terminal, and the fake start asks "Start the run?" in
the app before it reports.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import warnings
from pathlib import Path

from kiln_harness_tuner import cli
from kiln_harness_tuner.logbook import configure_run_logging
from kiln_harness_tuner.onboarding import OnboardingStopped, StartReport
from kiln_harness_tuner.onboarding.flow import START, START_QUESTION
from kiln_harness_tuner.onboarding.terminal import EXIT_ACTION
from kiln_harness_tuner.status import RunStatusTracker
from kiln_harness_tuner.tui.entry import run_app
from kiln_harness_tuner.wave_log import FanoutLog

# Set by `main` from the command line; `test_pty.py` imports the texts only.
WORK = Path()
BEHAVIOR = ""
SUMMARY = "\nReady to start\n  Project      Forum Moderation (424242975950)"
RERUN = (
    "\nTo run this again with the same answers and no questions:\n  harness-tuner …\n"
)
STRAY = ("STRAY-FD1", "STRAY-FD2", "STRAY-PRINT", "STRAY-STDERR", "STRAY-WARN")


def fake_prepare_start(args, _argv, *, report, terminal, **_kwargs):
    if BEHAVIOR == "ask":
        menu = [START, EXIT_ACTION]
        if terminal.act(START_QUESTION, menu) is EXIT_ACTION:
            raise OnboardingStopped(1)
    report(
        StartReport(
            project_name="Forum Moderation",
            task_name="Moderate posts",
            baseline_rc_id="424242000001",
            baseline_name="Baseline",
            summary=SUMMARY,
            rerun=RERUN,
        )
    )
    return args


async def fake_run(args, *, display=None):
    run_dir = WORK / "run"
    cli._RUN_PROGRESS.cli_checked = True
    cli._RUN_PROGRESS.run_dir = run_dir
    configure_run_logging(run_dir)
    print(f"{cli.PROG}: run 424242-0001 — files in {run_dir}/", flush=True)
    tracker = RunStatusTracker(
        project_id=args.project_id,
        task_id=args.task_id,
        guidance="",
        clock=time.monotonic,
    )
    log = FanoutLog(tracker, display(tracker, None))
    try:
        log.event(
            "run_start",
            timeout_mins=60,
            deadline=time.monotonic() + 3600,
            deadline_iso="2026-11-10T13:00:00",
            budget_usd=None,
            objective="quality",
            model_policy=None,
        )
        os.write(1, b"STRAY-FD1\n")
        os.write(2, b"STRAY-FD2\n")
        print("STRAY-PRINT")
        print("STRAY-STDERR", file=sys.stderr)
        warnings.warn("STRAY-WARN", stacklevel=1)
        (WORK / "started").write_text("")
        if BEHAVIOR == "wait":
            await asyncio.sleep(3600)
        log.event("exit", reason="time_limit")
        report = WORK / "report.md"
        report.write_text("# Report\n")
        log.event("report_written", path=str(report))
        return str(report)
    except asyncio.CancelledError:
        log.event("exit", reason="cancelled")
        raise
    finally:
        (WORK / "teardown").write_text("")


def main() -> int:
    global WORK, BEHAVIOR
    WORK, BEHAVIOR = Path(sys.argv[1]), sys.argv[2]
    cli.prepare_start = fake_prepare_start
    cli._run = fake_run
    argv = ["--project-path", str(WORK), "--project-id", "424242975950"]
    argv += ["--task-id", "424242783061"]
    if BEHAVIOR == "ask":
        return cli.main(argv)
    parser = cli.build_parser()
    args = parser.parse_args(argv)
    cli.validate_args(parser, args)
    return run_app(
        args,
        argv,
        parser,
        "app",
        finish=lambda body: cli._finish(body, kiln_host=args.kiln_host),
    )


if __name__ == "__main__":
    sys.exit(main())
