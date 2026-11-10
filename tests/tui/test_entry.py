"""`run_app` end to end, headless (architecture sections 2, 6, 10, 11): a
fake start and a fake `_run` behind the real threads, the real app and
`main`'s own `_finish`."""

import asyncio
import os
import signal
import threading
import time
from pathlib import Path

import httpx
import pytest
from app_fakes import HeadlessTakeover, prompted, until
from model_events import Run, fix, row, table, val_row
from rich.console import Console

from kiln_harness_tuner import cli
from kiln_harness_tuner.kiln import KilnAPIError, KilnClient
from kiln_harness_tuner.logbook import configure_run_logging, log_path
from kiln_harness_tuner.onboarding import (
    OnboardingError,
    OnboardingStopped,
    StartReport,
)
from kiln_harness_tuner.onboarding.error_screen import RunProgress
from kiln_harness_tuner.onboarding.flow import START, START_QUESTION
from kiln_harness_tuner.onboarding.terminal import EXIT_ACTION
from kiln_harness_tuner.status import RunStatusTracker
from kiln_harness_tuner.tui import entry
from kiln_harness_tuner.tui.bridge import AppTerminal, CancellableRunner
from kiln_harness_tuner.tui.entry import _AppRun, run_app, run_config_rows, run_model
from kiln_harness_tuner.tui.handles import RunHandle
from kiln_harness_tuner.tui.screens.stop import StopDialog
from kiln_harness_tuner.tui.sink import TuiSink
from kiln_harness_tuner.wave_log import FanoutLog

ARGS = ["--project-path", "/tmp/forum", "--project-id", "424242975950"]
ARGS += ["--task-id", "424242783061"]
SUMMARY = "\nReady to start\n  Project      Forum Moderation (424242975950)"
RERUN = (
    "\nTo run this again with the same answers and no questions:\n  harness-tuner …\n"
)
REPORT = StartReport(
    project_name="Forum Moderation",
    task_name="Moderate posts",
    baseline_rc_id="424242000001",
    baseline_name="Baseline",
    summary=SUMMARY,
    rerun=RERUN,
)


@pytest.fixture(autouse=True)
def no_kiln(monkeypatch):
    """The app looks the run config names up in Kiln; here Kiln knows none."""

    async def no_names(_info):
        return []

    monkeypatch.setattr(entry, "run_config_rows", no_names)


class FakeStart:
    """`cli.prepare_start`: it reports and returns `args`, or raises. With
    `ask`, it first asks "Start the run?" on the terminal it is given, and
    stops as the flow does on `x`; with `say` too, it says the summary and
    the rerun command before it asks, as the flow does."""

    def __init__(self, *, error=None, ask=False, say=False):
        self.error = error
        self.ask = ask
        self.say = say
        self.kwargs = {}

    def __call__(self, args, _argv, **kwargs):
        self.kwargs = kwargs
        if self.say:
            kwargs["terminal"].say(SUMMARY)
            kwargs["terminal"].say(RERUN)
        if self.ask:
            menu = [START, EXIT_ACTION]
            if kwargs["terminal"].act(START_QUESTION, menu) is EXIT_ACTION:
                raise OnboardingStopped(1)
        if self.error is not None:
            raise self.error
        kwargs["report"](REPORT)
        return args


class FakeLoop:
    """`cli._run`: it shows events on the display it is given, as the loop
    does, and ends as told. `ends`: "finish", "wait" (until cancelled),
    "plain" (finish once the app has closed and the sink shows plain lines),
    "plain_sigterm" (then send this process a SIGTERM, and wait), or an
    exception to raise. With `run_log`, it sets up the run log first. With
    `display_after`, it waits that long before it builds its display, as
    `_run` does through the agent CLI's test prompt; `entered` is set before
    the wait."""

    def __init__(
        self,
        run_dir: Path,
        *,
        ends="finish",
        events=(),
        teardown=0.0,
        run_log=False,
        display_after=0.0,
    ):
        self.run_dir = run_dir
        self.ends = ends
        self.events = list(events)
        self.teardown = teardown
        self.run_log = run_log
        self.display_after = display_after
        self.entered = threading.Event()
        self.running = threading.Event()
        self.display = "unset"
        self.sink = None
        self.thread = None
        self.torn_down = False

    async def __call__(self, args, *, display=None):
        self.display, self.thread = display, threading.current_thread()
        cli._RUN_PROGRESS.cli_checked = True
        cli._RUN_PROGRESS.run_dir = self.run_dir
        if display is None:
            print("plain run output")
            return "/reports/plain.md"
        if self.run_log:
            configure_run_logging(self.run_dir)
        self.entered.set()
        await asyncio.sleep(self.display_after)
        tracker = RunStatusTracker(
            project_id=args.project_id, task_id=args.task_id, guidance=""
        )
        sink = self.sink = display(tracker, Console())
        log = FanoutLog(tracker, sink)
        try:
            log.event("run_start", timeout_mins=60, deadline=time.monotonic() + 3600)
            for stamped in self.events:
                log.event(stamped.kind, **stamped.fields)
            self.running.set()
            if self.ends == "wait":
                await asyncio.sleep(30)
            if self.ends in ("plain", "plain_sigterm"):
                # Bounded, as `FakeRun` is: a failing test must not hang.
                deadline = time.monotonic() + 10
                while not sink.forwarding and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)
                self.sigterm_while_plain = signal.getsignal(signal.SIGTERM)
            if self.ends == "plain_sigterm":
                os.kill(os.getpid(), signal.SIGTERM)
                await asyncio.sleep(30)
            if isinstance(self.ends, BaseException):
                raise self.ends
            log.event("exit", reason="time_limit")
            log.event("report_written", path="/reports/kiln-harness-tuner-1.md")
            return "/reports/kiln-harness-tuner-1.md"
        except asyncio.CancelledError:
            await asyncio.sleep(self.teardown)
            log.event("exit", reason="cancelled")
            raise
        finally:
            self.torn_down = True


@pytest.fixture(autouse=True)
def fresh_cli_state(monkeypatch):
    monkeypatch.setattr(cli, "_RUN_PROGRESS", RunProgress())
    cli._stopped_by.clear()
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)}
    yield
    cli._stopped_by.clear()
    for sig, handler in saved.items():
        assert signal.getsignal(sig) == handler, "a stop signal handler was left"


def run_with(
    monkeypatch, start, loop, script=None, *, mode="app", takeover=None, argv=ARGS
):
    monkeypatch.setattr(cli, "prepare_start", start)
    if loop is not None:
        monkeypatch.setattr(cli, "_run", loop)
    parser = cli.build_parser()
    args = parser.parse_args(argv)
    cli.validate_args(parser, args)
    takeover = takeover if takeover is not None else HeadlessTakeover(script)
    code = run_app(
        args,
        argv,
        parser,
        mode,
        finish=lambda body: cli._finish(body, kiln_host=args.kiln_host),
        takeover=takeover,
    )
    return code, takeover


async def close_on_done(pilot):
    await until(pilot, lambda: pilot.app.phase == "done")
    await pilot.press("q")


def run_dir_of(tmp_path: Path) -> Path:
    run_dir = tmp_path / "runs" / "261110-0912"
    run_dir.mkdir(parents=True)
    return run_dir


# ---- app mode ----


def test_the_app_runs_the_start_and_the_run_then_prints_the_closing_block(
    monkeypatch, capsys, tmp_path
):
    start = FakeStart()
    loop = FakeLoop(run_dir_of(tmp_path))
    code, takeover = run_with(monkeypatch, start, loop, close_on_done)
    assert code == 0
    out = capsys.readouterr().out
    assert out.index(SUMMARY) < out.index(RERUN) < out.index("exit: time_limit")
    assert out.endswith(
        "report: /reports/kiln-harness-tuner-1.md\n"
        f"Run folder: {loop.run_dir}\n"
        "harness-tuner: run finished — report: /reports/kiln-harness-tuner-1.md\n"
    )
    # The start ran behind the app's terminal and its runner, and reported.
    terminal = start.kwargs["terminal"]
    assert isinstance(terminal, AppTerminal) and terminal.interactive
    assert isinstance(start.kwargs["runner"], CancellableRunner)
    assert callable(start.kwargs["report"])
    # The run ran on its own thread, on the app's display.
    assert isinstance(loop.sink, TuiSink)
    assert loop.thread is not threading.main_thread()
    assert takeover.settled == [log_path(loop.run_dir)]


def test_the_closing_block_names_best_confirmed(monkeypatch, capsys, tmp_path):
    run = Run().standard_start()
    run.wave_start(1, fix("w1-1", "e1"))
    run.success("w1-1", "rc_a", row("e1", 0.6, 0.7))
    run.gate(1, table("rc_a", val_row("e1", 0.62, 0.70)), winner="rc_a")
    loop = FakeLoop(run_dir_of(tmp_path), events=run.events)
    code, _ = run_with(monkeypatch, FakeStart(), loop, close_on_done)
    assert code == 0
    assert "Best Confirmed: w1-1 (rc_a)\n" in capsys.readouterr().out


def test_stop_the_run_exits_130_with_the_interrupted_line(
    monkeypatch, capsys, tmp_path
):
    loop = FakeLoop(run_dir_of(tmp_path), ends="wait")

    async def stop(pilot):
        await until(pilot, loop.running.is_set)
        await pilot.press("ctrl+c")
        await until(pilot, lambda: isinstance(pilot.app.screen, StopDialog))
        await pilot.press("tab", "enter")

    code, _ = run_with(monkeypatch, FakeStart(), loop, stop)
    assert code == 130
    assert cli._stopped_by == [signal.SIGINT]
    assert loop.torn_down
    out, err = capsys.readouterr()
    assert "exit: cancelled" in out
    assert "no report\n" in out
    assert "harness-tuner: interrupted (SIGINT) — children killed, lock released" in err


def test_sigterm_during_the_run_exits_143(monkeypatch, capsys, tmp_path):
    loop = FakeLoop(run_dir_of(tmp_path), ends="wait")

    async def terminate(pilot):
        await until(pilot, loop.running.is_set)
        pilot.app.on_signal(signal.SIGTERM)

    code, _ = run_with(monkeypatch, FakeStart(), loop, terminate)
    assert code == 143
    assert loop.torn_down
    assert "interrupted (SIGTERM)" in capsys.readouterr().err


def test_a_second_signal_closes_the_app_and_the_teardown_shows_plain_lines(
    monkeypatch, capsys, tmp_path
):
    loop = FakeLoop(run_dir_of(tmp_path), ends="wait", teardown=0.5)

    async def twice(pilot):
        await until(pilot, loop.running.is_set)
        pilot.app.on_signal(signal.SIGTERM)
        pilot.app.on_signal(signal.SIGTERM)

    code, _ = run_with(monkeypatch, FakeStart(), loop, twice)
    assert code == 143
    assert loop.torn_down
    out = capsys.readouterr().out
    # PlainUi's own line, with its timestamp, then the closing block's.
    assert "] exit: cancelled — total" in out
    assert "\nexit: cancelled — total" in out


def test_a_ctrl_c_while_waiting_for_the_run_still_settles_and_closes(
    monkeypatch, capsys, tmp_path
):
    # After a second stop the default SIGINT handler is back, so a Ctrl+C
    # raises in the main thread while it waits for the teardown.
    loop = FakeLoop(run_dir_of(tmp_path), ends="wait", teardown=0.3)
    real_join = RunHandle.join

    def interrupted(handle, timeout=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(RunHandle, "join", interrupted)

    async def twice(pilot):
        await until(pilot, loop.running.is_set)
        pilot.app.on_signal(signal.SIGTERM)
        pilot.app.on_signal(signal.SIGTERM)

    takeover = HeadlessTakeover(twice)
    with pytest.raises(KeyboardInterrupt):
        run_with(monkeypatch, FakeStart(), loop, takeover=takeover)
    assert takeover.settled == [log_path(loop.run_dir)]
    out, err = capsys.readouterr()
    # The run had not reached its `exit` yet: the start part only, then
    # `main`'s Ctrl+C arm names the run folder.
    assert out.endswith(RERUN + "\n")
    assert f"harness-tuner: run folder: {loop.run_dir}" in err
    assert real_join(takeover.app.session.run, 5)


DISPLAY_STOPPED = (
    "The display stopped: RuntimeError: bad refresh. The run goes on with plain"
    " lines; details in the run log."
)


def crash_when(event: threading.Event):
    async def crash(pilot):
        await until(pilot, event.is_set)

        def bad_refresh():
            raise RuntimeError("bad refresh")

        pilot.app.call_later(bad_refresh)

    return crash


def test_a_crash_while_the_run_goes_on_switches_to_plain_lines(
    monkeypatch, capsys, tmp_path
):
    loop = FakeLoop(run_dir_of(tmp_path), ends="plain", run_log=True)
    code, takeover = run_with(monkeypatch, FakeStart(), loop, crash_when(loop.running))
    assert code == 0
    out, err = capsys.readouterr()
    assert err.count(DISPLAY_STOPPED) == 1
    # In the run log once, with its traceback; not with the stray output.
    run_log = log_path(loop.run_dir).read_text()
    assert run_log.count(DISPLAY_STOPPED) == 1
    assert "RuntimeError: bad refresh" in run_log.split(DISPLAY_STOPPED, 1)[1]
    assert not any(DISPLAY_STOPPED in text for text in takeover.prints)
    assert "] exit: time_limit — total" in out
    assert "] report: /reports/kiln-harness-tuner-1.md" in out
    assert out.endswith("run finished — report: /reports/kiln-harness-tuner-1.md\n")
    # While the main thread waited, a SIGTERM would have stopped the run.
    # The handlers are put back after (the fixture checks it).
    assert loop.sigterm_while_plain not in (signal.SIG_DFL, signal.SIG_IGN, None)


def test_a_crash_before_the_run_built_its_display_still_gives_plain_lines(
    monkeypatch, capsys, tmp_path
):
    loop = FakeLoop(run_dir_of(tmp_path), ends="plain", display_after=0.5)
    code, takeover = run_with(monkeypatch, FakeStart(), loop, crash_when(loop.entered))
    assert code == 0
    out, err = capsys.readouterr()
    assert loop.sink.forwarding
    assert "] exit: time_limit — total" in out
    assert out.endswith("run finished — report: /reports/kiln-harness-tuner-1.md\n")
    # No run log yet: the line goes with the stray output, so it reaches the
    # run log if the run makes one, and the terminal shows it only once.
    assert err.count(DISPLAY_STOPPED) == 1
    assert f"{DISPLAY_STOPPED}\n" in takeover.prints


def test_a_display_built_after_the_app_closed_shows_plain_lines_at_once():
    # The same as above, with no timing: `_join_plainly` has already run.
    parser = cli.build_parser()
    app_run = _AppRun(parser.parse_args(ARGS), ARGS, parser, "app", HeadlessTakeover())
    app_run._plain = True
    tracker = RunStatusTracker(project_id="p", task_id="t", guidance="")
    sink = app_run._display(tracker, Console())
    assert sink is app_run.sink
    assert sink.forwarding
    sink.event("wave_start", wave=1)
    assert app_run.queue.empty()


def test_after_a_crash_a_sigterm_still_stops_the_run(monkeypatch, capsys, tmp_path):
    loop = FakeLoop(run_dir_of(tmp_path), ends="plain_sigterm")

    async def crash(pilot):
        await until(pilot, loop.running.is_set)
        pilot.app.call_later(lambda: 1 / 0)

    code, _ = run_with(monkeypatch, FakeStart(), loop, crash)
    assert code == 143
    assert loop.torn_down
    assert cli._stopped_by == [signal.SIGTERM]
    assert "interrupted (SIGTERM)" in capsys.readouterr().err


def test_an_app_that_closes_by_itself_during_the_run_goes_on_with_plain_lines(
    monkeypatch, capsys, tmp_path
):
    loop = FakeLoop(run_dir_of(tmp_path), ends="plain")

    async def close(pilot):
        await until(pilot, loop.running.is_set)
        pilot.app.exit()

    code, _ = run_with(monkeypatch, FakeStart(), loop, close)
    assert code == 0
    out, err = capsys.readouterr()
    assert (
        "The display stopped: DisplayClosed: the display closed. The run goes on"
        " with plain lines; details in the run log." in err
    )
    assert "] exit: time_limit — total" in out


def test_a_run_error_closes_the_app_and_shows_the_error_screen(
    monkeypatch, capsys, tmp_path
):
    error = cli.RunLockError("run 261110-0800 (pid 4242) holds the lock")
    loop = FakeLoop(run_dir_of(tmp_path), ends=error)
    code, _ = run_with(monkeypatch, FakeStart(), loop)
    assert code == 1
    err = capsys.readouterr().err
    assert "harness-tuner: error: Another run is already using this task" in err
    assert "run 261110-0800 (pid 4242) holds the lock" in err


def test_system_exit_from_the_run_reaches_the_main_thread(monkeypatch, tmp_path):
    loop = FakeLoop(run_dir_of(tmp_path), ends=SystemExit("--agent-cli: bad"))
    with pytest.raises(SystemExit, match="--agent-cli: bad"):
        run_with(monkeypatch, FakeStart(), loop)


# ---- the start ----


async def asked(pilot):
    await prompted(pilot)


@pytest.mark.parametrize("key,code", [("ctrl+c", 130), ("ctrl+d", 1), ("x", 1)])
def test_a_key_at_a_start_up_question_stops_with_its_code(
    monkeypatch, capsys, key, code
):
    start = FakeStart(ask=True)

    async def press(pilot):
        await asked(pilot)
        await pilot.press(key)

    exit_code, takeover = run_with(monkeypatch, start, None, press)
    assert exit_code == code
    out, err = capsys.readouterr()
    assert err == "harness-tuner: stopped.\n"
    assert out == ""
    # The start unwound from its question: the app did not wait it out.
    assert takeover.app.session.start.outcome is not None


@pytest.mark.parametrize("key,code", [("x", 1), ("ctrl+c", 130)])
def test_leaving_at_start_the_run_keeps_the_summary_and_rerun_command(
    monkeypatch, capsys, key, code
):
    async def press(pilot):
        await asked(pilot)
        await pilot.press(key)

    exit_code, _ = run_with(monkeypatch, FakeStart(ask=True, say=True), None, press)
    assert exit_code == code
    out, err = capsys.readouterr()
    # The scrollback gets them, as it did before the app: the rerun command
    # is printed nowhere else.
    assert out == f"{SUMMARY}\n{RERUN}\n"
    assert err == "harness-tuner: stopped.\n"


def test_a_guided_start_answered_in_the_app_goes_on_to_the_run(
    monkeypatch, capsys, tmp_path
):
    loop = FakeLoop(run_dir_of(tmp_path))

    async def start_then_close(pilot):
        await asked(pilot)
        await pilot.press("s")
        await close_on_done(pilot)

    code, _ = run_with(monkeypatch, FakeStart(ask=True), loop, start_then_close)
    assert code == 0
    assert "run finished" in capsys.readouterr().out


def test_with_no_interaction_the_start_asks_nothing(monkeypatch, tmp_path):
    start = FakeStart()
    loop = FakeLoop(run_dir_of(tmp_path))
    code, _ = run_with(
        monkeypatch, start, loop, close_on_done, argv=[*ARGS, "--no-interaction"]
    )
    assert code == 0
    assert start.kwargs["terminal"].interactive is False


def test_a_signal_during_the_start_exits_with_its_code(monkeypatch, capsys):
    async def terminate(pilot):
        await asked(pilot)
        pilot.app.on_signal(signal.SIGTERM)

    code, _ = run_with(monkeypatch, FakeStart(ask=True), None, terminate)
    assert code == 143
    assert "harness-tuner: stopped." in capsys.readouterr().err


def test_a_crash_during_the_start_releases_its_question(monkeypatch, capsys):
    start = FakeStart(ask=True)

    async def crash(pilot):
        await asked(pilot)
        pilot.app.call_later(lambda: 1 / 0)

    takeover = HeadlessTakeover(crash)
    with pytest.raises(ZeroDivisionError):
        run_with(monkeypatch, start, None, takeover=takeover)
    # Nobody answers the question now: it raised, and the start ended.
    start_handle = takeover.app.session.start
    assert start_handle.join(5)
    assert isinstance(start_handle.outcome.error, OnboardingStopped)


def test_a_start_error_prints_the_whole_error_screen(monkeypatch, capsys):
    # A guided start showed it as a block in the app, which is gone now.
    error = OnboardingError("Kiln isn't running", ("Nothing answered at :8757.",))
    code, _ = run_with(monkeypatch, FakeStart(error=error), None)
    assert code == 1
    err = capsys.readouterr().err
    assert "harness-tuner: error: Kiln isn't running" in err
    assert "  Nothing answered at :8757." in err


def test_stray_output_with_no_run_folder_is_named(monkeypatch, capsys, tmp_path):
    stray = tmp_path / "harness-tuner-x.log"
    takeover = HeadlessTakeover(stray=stray)
    error = OnboardingError("Kiln isn't running")
    code, _ = run_with(monkeypatch, FakeStart(error=error), None, takeover=takeover)
    assert code == 1
    assert takeover.settled == [None]
    assert (
        f"harness-tuner: output written while the app was open: {stray}"
        in capsys.readouterr().err
    )


# ---- app_startup mode ----


def test_with_no_progress_the_plain_run_follows_the_start_part(
    monkeypatch, capsys, tmp_path
):
    loop = FakeLoop(run_dir_of(tmp_path))
    code, _ = run_with(monkeypatch, FakeStart(), loop, mode="app_startup")
    assert code == 0
    out = capsys.readouterr().out
    assert out.index(RERUN) < out.index("plain run output")
    assert out.endswith("harness-tuner: run finished — report: /reports/plain.md\n")
    # Today's plain path: its own display, on the main thread.
    assert loop.display is None
    assert loop.thread is threading.main_thread()


# ---- the run config names ----


def kiln_answering(body, requests: list):
    """A `KilnClient` maker over a mock transport that answers `body`."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if body is None:
            return httpx.Response(500)
        return httpx.Response(200, json=body)

    def connect(host: str) -> KilnClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return KilnClient(host, client=client, retry_backoff_secs=(0.0,))

    return connect


@pytest.mark.parametrize(
    "body",
    [
        [{"id": "rc_1", "name": "Hold fields"}, "junk"],
        {"run_configs": [{"id": "rc_1", "name": "Hold fields"}]},
    ],
)
def test_run_config_rows_reads_the_task_run_configs(body):
    requests: list[httpx.Request] = []
    info = run_model(cli.build_parser().parse_args(ARGS), REPORT).info
    rows = asyncio.run(run_config_rows(info, connect=kiln_answering(body, requests)))
    assert rows == [{"id": "rc_1", "name": "Hold fields"}]
    (request,) = requests
    assert request.method == "GET"
    assert (
        request.url.path == "/api/projects/424242975950/tasks/424242783061/run_configs"
    )


def test_run_config_rows_does_not_retry_a_failed_read():
    requests: list[httpx.Request] = []
    info = run_model(cli.build_parser().parse_args(ARGS), REPORT).info
    with pytest.raises(KilnAPIError):
        asyncio.run(run_config_rows(info, connect=kiln_answering(None, requests)))
    assert len(requests) == 1
