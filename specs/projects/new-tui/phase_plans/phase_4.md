---
status: complete
---

# Phase 4: The app shell and the terminal

## Overview

This phase builds the Textual app around the run, without its real screens: the threads and the queue between them, the terminal take-over (the driver's own tty, fds 1 and 2 on a stray-output file, the background query), the signals, the stop dialog, the drain into the phase 3 model, the closing block and `entry.run_app` for the app modes (architecture sections 1, 2, 5.2, 5.3, 6, 10, 11, 12, 14). The start-up and run screens are placeholders; phases 5 and 6 replace them. `main` does not call `run_app` yet (phase 5 wires it), so `harness-tuner` behaves as before, and the plain path still never imports Textual.

The start-up thread runs `prepare_start` with a stand-in terminal, `PlainTerminal`, until phase 5 brings `AppTerminal`. `TuiSink` (listed under phase 6) comes forward to this phase: the drain and the closing block's run part need it, and it does not depend on the run screen.

## Steps

1. **Dependencies** (`pyproject.toml`, `uv.lock`): add `textual>=8.2.8,<9` with a comment, raise `rich` to `>=14.2`. Register a `slow` pytest marker.

2. **`tui/theme.py`**:
   - `KILN_DARK`, `KILN_LIGHT` (`textual.theme.Theme`, the tokens of ui_design section 2, from `ui_preview/preview_app.py`), `THEMES`, `CUSTOM_VARIABLES`, `DARK`/`LIGHT` names.
   - `query_background(timeout=0.5, *, tty_path="/dev/tty") -> RGB | None`: OSC 11 then DA1, stop at the DA1 reply, flush input after (research `osc11.py`). Never raises.
   - `parse_background(reply: bytes) -> RGB | None`, `is_light(rgb) -> bool`.
   - `pick_theme(background, env=os.environ) -> str`: `TEXTUAL_THEME` when it names one of the two; else light for a light background; else dark.

3. **`tui/opener.py`**: `open_link(url) -> bool`. False with no graphical browser or over SSH (`SSH_CONNECTION`, `SSH_TTY`); else `open` (macOS) or `xdg-open`, all three streams on `DEVNULL`, `start_new_session=True`, reaped by a daemon thread, no wait. False when it can't start.

4. **`tui/terminal_io.py`**:
   - `TTYFile(fd)`: the driver's output; `write`/`flush` drop `OSError` once the terminal is gone (`dead`), `fileno`, `isatty`, `close`.
   - `open_tty() -> int`: `/dev/tty`, else a dup of fd 1.
   - `TTYDriver(LinuxDriver)` with `tty: ClassVar[TTYFile]`; `TTYDriver.on(tty) -> type[TTYDriver]`; `__init__` sets `self._file = tty`; `_get_terminal_size` reads the tty fd.
   - `redirect_fds(path)`: a context manager that points fds 1 and 2 at `path` (`O_APPEND`) and restores them.
   - `StrayOutput`: `create()` (`mkstemp(prefix="harness-tuner-", suffix=".log")`), `write(text)` (whole lines only, through its own `O_APPEND` fd), `close()`, `settle(run_log: Path | None) -> Path | None` (empty: removed; a run log: appended there, removed; else kept, its path returned).
   - `SignalHooks = Callable[[AbstractEventLoop, Callable[[int], None]], Callable[[], None]]`; `APP_SIGNALS = (SIGTERM, SIGHUP, SIGINT)`; `loop_signals` (add on the loop, return the remover).
   - `Takeover` protocol: `theme()`, `signals`, `print_sink`, `run(app)`, `settle(run_log)`. `TerminalTakeover`: the real one. `run(app)` opens the tty, redirects the fds, runs the app on `TTYDriver.on(tty)`, restores the fds, resets SIGTSTP and SIGCONT to `SIG_DFL`.

5. **`tui/model/events.py`**: `Stamped.now(kind, fields)`, stamped with `time.monotonic()` and `datetime.now()`.

6. **`tui/handles.py`**:
   - `Outcome[T]` (`value` or `error`, `unwrap()`).
   - `Worker[T]`: a plain thread (daemon or not) whose outcome, any `BaseException` included, is kept; then it puts `Stamped(<ended kind>)` on the queue. `begin()`, `alive`, `began`, `join(timeout)`, `outcome`.
   - `StartHandle(Worker[Namespace])`, daemon, kind `start_ended`; `stop(code)` keeps the first `OnboardingStopped(code)`, which wins over the thread's outcome; `result()`.
   - `RunHandle(Worker[str])`, non-daemon, kind `run_ended`; `started(loop, task)` (the `on_started` hook); `stop(signum) -> bool`: once, appends the signal to `stopped_by` and cancels the task through `call_soon_threadsafe`; a stop before `started` is done in `started`, after `_run_with_signals` has cleared the list. `stopping`, `result()`.

7. **`tui/sink.py`**: `TuiSink(tracker, queue, closing)`, architecture 5.2. `event()` builds the exit line at `exit` and keeps the report path, then puts `Stamped.now`; it never raises. `forward_to(plain)`: from then on events go to a `PlainUi` (section 11). Context manager no-ops.

8. **`tui/closing.py`**: `ClosingBlock` with `take_start(report)`, `run_exit_line`, `report_path`, `best_confirmed`. `start_lines()`: the summary and the rerun command as said. `run_lines(run_dir)`: the exit line, `Best Confirmed: …` when a promotion was confirmed, `report: <path>` or `no report`, `Run folder: <dir>`; nothing when the run never reached `exit`. `print_lines(lines, stream)` catches `OSError`. `stray_line(path)`.

9. **`tui/model/words.py`**: the stop dialog's title and buttons, the display-stopped line, the closing block's words.

10. **`tui/widgets.py`**: `Card` (ui_design 4.3) only; phase 6 adds the rest. **`tui/screens/stop.py`**: `StopDialog(message)`, a `ModalScreen[bool]` (ui_design 4.11, 9): "Keep running" has the focus, Esc keeps running, its own `Footer`. **`tui/screens/startup.py`** and **`tui/screens/run.py`**: placeholders. The run screen shows the title, the Experiments header (or "Stopping…") and the newest log lines as plain text, and updates only when the text changed; `q` stops, `q` or Enter closes on Done. **`tui/tuner.tcss`**: the styles these use.

11. **`tui/app.py`**:
    - `Session` (frozen dataclass): `mode` (`app_startup` | `app`), `queue`, `start`, `run`, `make_model`, `progress`.
    - `TunerApp(session, *, theme, signals=loop_signals, print_sink=None, drain_interval=0.25)`: `CSS_PATH = Path(__file__).with_name("tuner.tcss")`, the command palette off, both themes registered in `__init__`.
    - `phase`: `startup`, `run`, `done`. `closed_on_purpose`, `crash` (Textual's first unhandled error).
    - `on_mount`: signals, print capture, the start-up screen, the drain timer, `start.begin()`. `on_unmount`: removes the signals.
    - `drain()`: everything on the queue; `start_ended` and `run_ended` handled by the app, every other event folded into the model, each in `try`/`except` (logged, `note_ui_error`); the run id and the agent CLI check read from `progress`; the run screen refreshed only when `model.version` moved.
    - The hand-off: on `start_ended`, a stop or an error closes the app; `app_startup` closes it; `app` makes the model, switches to the run screen, `run.begin()`. On `run_ended`: closes after a stop or an error, else Done.
    - Keys (all `priority=True` on the App): `ctrl+c` and `ctrl+q` stop the start-up (130) or ask to stop the run; `ctrl+d` exits the start-up (1). `check_action` by phase. `ask_stop`: on Done closes; while stopping closes at once; else the stop dialog (once). "Stop the run" is `run.stop(SIGINT)`.
    - `on_signal(signum)`: start-up: stop with `128 + signum`; run: the first stops the run, the second closes the app at once; Done: closes.

12. **`tui/entry.py`**: `run_app(args, raw_argv, parser, mode, *, finish, takeover=None) -> int` returns `finish(body)`. The body:
    - asks for the theme, builds the queue, `ClosingBlock`, the two handles (the start: `cli.prepare_start(..., terminal=PlainTerminal(PROG), report=closing.take_start)`; the run: `asyncio.run(cli._run_with_signals(args, display=<TuiSink factory>, on_started=run.started))`), the model factory (`RunInfo` from `args` and the start report, `RcNames` seeded with the baseline), and the app; `takeover.run(app)`.
    - Then: the start part prints. A run thread still alive (a crash, or a second stop) gets plain output: the display-stopped line after a crash (also logged), `TuiSink.forward_to(PlainUi)`, one-shot `signal.signal` handlers that stop the run (when it is not stopping yet), a join. The stray output settles into the run log. The run part prints, with Best Confirmed from the model when the app saw the end.
    - Re-raises on the main thread, inside `finish`: a start-up stop, a crash before the run, the start's error (an `OnboardingError` with `shown = False`: the block it showed is gone with the app), or the run's outcome. In `app_startup` mode a good start goes on to today's plain run on the main thread.

## Tests

- `tests/tui/test_theme.py`: replies with ST and BEL, 2- and 4-digit channels, `rgba`; garbage gives None; `is_light`; `pick_theme` for `TEXTUAL_THEME` (ours, another's), light, dark, no answer; `query_background` on a pty answered by a fake terminal (light reply, DA1 only, silence until the timeout, no tty).
- `tests/tui/test_opener.py`: `open_link` starts `xdg-open` or `open` detached with `DEVNULL`; False with no display, over SSH, and when the command can't start.
- `tests/tui/test_terminal_io.py`: `TTYFile` drops writes after a broken pipe; `TTYDriver` writes to its tty and measures the tty's size (a pty); `redirect_fds` sends fd-level writes to the file and restores; `StrayOutput` keeps whole lines and settles three ways; `loop_signals` installs and removes.
- `tests/tui/test_handles.py`: a worker keeps a value, an exception, `SystemExit` and `CancelledError`, and puts its ended event; `RunHandle.stop` before and after `started`, once only, after the loop closed; `StartHandle.stop` wins over a later success.
- `tests/tui/test_sink.py`: stamped events on the queue; the exit line is `plain_exit_line` at `exit`; the report path; a failing put does not raise; `forward_to` sends events to the plain display.
- `tests/tui/test_closing.py`: the start part as said (with and without the rerun); the run part with and without a report, Best Confirmed, the run folder; nothing before `exit`; `OSError` is swallowed.
- `tests/tui/test_app.py` (pilot, 210×62, fake handles): the start-up keys (Ctrl+C 130, Ctrl+D 1, Ctrl+Q 130) and their footer; the hand-off to the run screen; `q` and Ctrl+C open the stop dialog once, its message names what is in flight, Esc and "Keep running" keep running, "Stop the run" stops with SIGINT and shows "Stopping…"; signals in each phase, the second closes; Done keys; a bad event is skipped and noted; unchanged text is not updated again; the app's themes.
- `tests/tui/test_entry.py` (headless takeover, fake `prepare_start` and `_run`): each mode end to end with `cli._finish`; the closing block's order; Stop the run gives 130 and `interrupted (SIGINT)`; SIGTERM 143; a second signal closes at once and the run is joined with plain lines; a start-up stop gives `stopped.` and 130 or 1; a start error prints the full error screen; a run error closes at once; `SystemExit` from the run; a crash while the run is alive goes on with plain lines; `app_startup` runs the plain run on the main thread after the start part.
- `tests/tui/test_pty.py` (`slow`, a pty and `tests/tui/pty_app.py`): SIGTERM cancels the run and tears down (143); SIGHUP exits with no hang; Ctrl+C then "Stop the run" gives 130; stray writes (fd 1, fd 2, `print`, `warnings`) never reach the terminal and land in the run log; the closing block prints after the alternate screen.
