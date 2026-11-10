"""`run_app`: an interactive invocation in the app (architecture sections 1,
2, 10 and 11). The only function `cli.main` calls in this package.

The app runs on the main thread. The start runs on a daemon thread, the run
on a thread of its own. After the app has closed, `run_app` prints the
closing block and re-raises how the start or the run ended, on the main
thread, inside `main`'s exception arms. So every exit line and exit code is
the plain path's.

It reads `cli`'s seams at call time (`prepare_start`, `_run_with_signals`,
`_stopped_by`, `_RUN_PROGRESS`), as `main` does.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
import threading
import time
from collections.abc import Callable, Mapping
from queue import SimpleQueue
from types import FrameType
from typing import Any

from rich.console import Console

from kiln_harness_tuner import cli
from kiln_harness_tuner.dispatch.agent_cli import parse_agent_cli
from kiln_harness_tuner.kiln import KilnClient
from kiln_harness_tuner.logbook import log_path, run_log_configured
from kiln_harness_tuner.onboarding import (
    OnboardingError,
    OnboardingStopped,
    StartReport,
)
from kiln_harness_tuner.status import RunStatusTracker
from kiln_harness_tuner.tui.app import AppMode, Session, TunerApp
from kiln_harness_tuner.tui.bridge import AppTerminal, cancellable_runner
from kiln_harness_tuner.tui.closing import (
    ClosingBlock,
    display_stopped_line,
    print_lines,
    stray_line,
)
from kiln_harness_tuner.tui.handles import RunHandle, StartHandle
from kiln_harness_tuner.tui.model.events import Stamped
from kiln_harness_tuner.tui.model.fold import RunModel
from kiln_harness_tuner.tui.model.names import RcNames
from kiln_harness_tuner.tui.model.state import RunInfo
from kiln_harness_tuner.tui.sink import TuiSink
from kiln_harness_tuner.tui.terminal_io import APP_SIGNALS, Takeover, TerminalTakeover
from kiln_harness_tuner.ui import PlainUi

logger = logging.getLogger("kiln_harness_tuner.tui")

# The name lookup is a nicety: it waits this long, once, with no retries.
NAMES_TIMEOUT_SECS = 15.0


class DisplayClosed(RuntimeError):
    """The app closed with no stop, no end and no error of its own: its
    input or output failed (the stray-output file has Textual's account)."""


def run_app(
    args: argparse.Namespace,
    raw_argv: list[str],
    parser: cli._Parser,
    mode: AppMode,
    *,
    finish: Callable[[Callable[[], str]], int],
    takeover: Takeover | None = None,
) -> int:
    """The start (and, in `app` mode, the run) in the app. `finish` is
    `main`'s `_finish`, which turns the body's result or exception into the
    last line and the exit code."""
    taken = takeover if takeover is not None else TerminalTakeover()
    return finish(lambda: _AppRun(args, raw_argv, parser, mode, taken).body())


async def run_config_rows(
    info: RunInfo, *, connect: Callable[[str], KilnClient] = KilnClient
) -> list[Mapping[str, Any]]:
    """The task's run configs, as Kiln lists them: the route the loop reads
    too (`probes.task_run_configs`). Its own client, on the app's loop."""
    async with connect(info.kiln_host) as kiln:
        response = await kiln.call(
            "GET",
            f"/api/projects/{info.project_id}/tasks/{info.task_id}/run_configs",
            timeout_secs=NAMES_TIMEOUT_SECS,
            retries=False,
        )
    if isinstance(response, Mapping):
        response = response.get("run_configs")
    if not isinstance(response, list):
        return []
    return [row for row in response if isinstance(row, Mapping)]


def run_model(args: argparse.Namespace, report: StartReport | None) -> RunModel:
    """The run screen's model, at the hand-off: `prepare_start` has filled
    in `args`, and its report has the names."""
    try:
        agent_cli = parse_agent_cli(args.agent_cli)
        kind, text = agent_cli.kind, agent_cli.text
    except ValueError:
        # `_run` stops on it at once, with its own message.
        kind, text = "claude", str(args.agent_cli)
    info = RunInfo(
        project_id=str(args.project_id or ""),
        task_id=str(args.task_id or ""),
        kiln_host=args.kiln_host,
        objective=args.objective or "quality",
        agent_cli=kind,
        agent_cli_text=text,
        agent_slots=args.max_concurrent_agents,
        eval_slots=args.max_concurrent_evals,
        project_name=report.project_name if report else "",
        task_name=report.task_name if report else "",
    )
    seed: dict[str, str] = {}
    if report is not None and report.baseline_rc_id and report.baseline_name:
        seed[report.baseline_rc_id] = report.baseline_name
    return RunModel(info, started=time.monotonic(), names=RcNames(seed))


class _AppRun:
    def __init__(
        self,
        args: argparse.Namespace,
        raw_argv: list[str],
        parser: cli._Parser,
        mode: AppMode,
        takeover: Takeover,
    ):
        self.args = args
        self.raw_argv = raw_argv
        self.parser = parser
        self.mode = mode
        self.takeover = takeover
        self.queue: SimpleQueue[Stamped] = SimpleQueue()
        self.closing = ClosingBlock()
        self.sink: TuiSink | None = None
        # Set once the app has closed while the run goes on. The run may
        # build its display after that (`_run` builds it only after the
        # agent CLI's test prompt), so the lock makes the two meet.
        self._plain = False
        self._sink_lock = threading.Lock()
        self.terminal = AppTerminal(
            self.queue,
            interactive=not args.no_interaction,
            on_say=self.closing.said,
        )
        self.runner = cancellable_runner(args.kiln_host.rstrip("/"))
        self.start = StartHandle(self._start, self.queue, on_stop=self._unwind_start)
        self.run = RunHandle(self._run, self.queue, cli._stopped_by)

    # ---- the two threads' targets ----

    def _start(self) -> argparse.Namespace:
        return cli.prepare_start(
            self.args,
            self.raw_argv,
            prog=cli.PROG,
            option_dests=self.parser.option_dests,
            policy_loader=cli.load_model_policy,
            terminal=self.terminal,
            runner=self.runner,
            report=self.closing.take_start,
        )

    def _unwind_start(self, stop: OnboardingStopped) -> None:
        """Make the start-up thread end: its open prompt and its Kiln read
        in flight raise, and so does every later one."""
        self.terminal.abandon(stop)
        self.runner.cancel()

    def _display(self, tracker: RunStatusTracker, console: Console) -> TuiSink:
        with self._sink_lock:
            self.sink = TuiSink(tracker, self.queue, self.closing)
            if self._plain:
                self.sink.forward_to(PlainUi(tracker, console=console))
            return self.sink

    def _run(self) -> str:
        # The run begins only after a start that ended well (`_start_ended`).
        args = self.start.result()
        return asyncio.run(
            cli._run_with_signals(
                args, display=self._display, on_started=self.run.started
            )
        )

    # ---- the main thread ----

    def body(self) -> str:
        theme = self.takeover.theme()
        session = Session(
            mode=self.mode,
            queue=self.queue,
            start=self.start,
            run=self.run,
            make_model=lambda: run_model(self.start.result(), self.closing.start),
            progress=lambda: cli._RUN_PROGRESS,
            uninterruptible=lambda: self.terminal.copying_demo,
            lookup_names=run_config_rows,
        )
        app = TunerApp(
            session,
            theme=theme,
            signals=self.takeover.signals,
            print_sink=self.takeover.print_sink,
        )
        self.takeover.run(app)
        if self.start.began and self.start.outcome is None:
            # The app closed under the start (a crash, or its terminal went
            # away): nobody answers its prompt now.
            self._unwind_start(OnboardingStopped(1))
        print_lines(self.closing.start_lines())
        failure = _failure(app)
        if not self.run.began:
            return self._after_start(failure)
        return self._after_run(app, failure)

    def _after_start(self, failure: BaseException | None) -> str:
        """The app closed before the run began: a stop, an error, a crash,
        or the end of the start in `app_startup` mode."""
        stray = self._settle_stray()
        if stray is not None:
            print_lines([stray], sys.stderr)
        if self.start.stopped is not None:
            raise self.start.stopped
        if failure is not None:
            raise failure
        try:
            args = self.start.result()
        except OnboardingError as error:
            # A guided start showed it as a block, which went with the app.
            # So the scrollback gets the whole error screen.
            error.shown = False
            raise
        # `app_startup`: today's plain run, on this thread.
        return asyncio.run(cli._run_with_signals(args))

    def _after_run(self, app: TunerApp, failure: BaseException | None) -> str:
        try:
            if self.run.alive:
                self._join_plainly(failure)
        finally:
            # Also after a Ctrl+C during the wait, which goes on to `main`.
            model = app.model
            if model is not None and model.run_ended and model.confirmed_rc:
                rc_id = model.confirmed_rc
                self.closing.best_confirmed = (model.rc_label(rc_id), rc_id)
            run_dir = cli._RUN_PROGRESS.run_dir
            stray = self._settle_stray()
            print_lines(self.closing.run_lines(run_dir))
            if stray is not None:
                print_lines([stray], sys.stderr)
        return self.run.result()

    def _join_plainly(self, failure: BaseException | None) -> None:
        """The app has closed while the run goes on: after a crash, or a
        second stop. The rest of the run shows as plain lines, and the main
        thread waits for it (architecture section 11)."""
        if failure is not None:
            self._report_failure(failure)
        # A run that is stopping already keeps today's one-shot rule: the
        # next signal has its default effect.
        restore = None if self.run.stopping else _plain_stop_signals(self.run)
        with self._sink_lock:
            self._plain = True
            if self.sink is not None:
                self.sink.forward_to(PlainUi(self.sink.tracker, console=Console()))
        try:
            self.run.join()
        finally:
            if restore is not None:
                restore()

    def _report_failure(self, failure: BaseException) -> None:
        """The display-stopped line, on the terminal and in the run log.
        Logged only once the run log is set up: with no handler, `logging`
        would print it to the terminal a second time. Before that, the line
        goes with the stray output, which reaches the run log if the run
        makes one."""
        line = display_stopped_line(failure)
        print_lines([line], sys.stderr)
        if run_log_configured():
            logger.error("%s", line, exc_info=failure)
        else:
            self.takeover.print_sink(f"{line}\n")

    def _settle_stray(self) -> str | None:
        """The output written while the app was open goes to the run log; a
        line names its file when there is no run folder."""
        run_dir = cli._RUN_PROGRESS.run_dir
        kept = self.takeover.settle(log_path(run_dir) if run_dir else None)
        return stray_line(cli.PROG, kept) if kept is not None else None


def _failure(app: TunerApp) -> BaseException | None:
    """Why the app closed, when it did not close itself."""
    if app.crash is not None:
        return app.crash
    if not app.closed_on_purpose:
        return DisplayClosed("the display closed")
    return None


def _plain_stop_signals(run: RunHandle) -> Callable[[], None]:
    """While the main thread waits for the run with no app: the first
    SIGTERM, SIGHUP or SIGINT stops the run, as in a plain run, and puts
    back the handlers that were there. Returns what puts them back."""
    before = {signum: signal.getsignal(signum) for signum in APP_SIGNALS}

    def restore() -> None:
        for signum, handler in before.items():
            # None: a handler set outside Python, which can't be put back.
            with contextlib.suppress(OSError, ValueError, TypeError):
                signal.signal(signum, signal.SIG_DFL if handler is None else handler)

    def on_stop(signum: int, _frame: FrameType | None) -> None:
        restore()
        run.stop(signum)

    for signum in APP_SIGNALS:
        signal.signal(signum, on_stop)
    return restore
