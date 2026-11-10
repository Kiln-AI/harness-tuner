# Textual 8.2.8 at runtime: research for the new TUI

This file records how Textual 8.2.8 (with rich 15.0.0) behaves at runtime. The
evidence is the installed source and small experiments. It grounds the
architecture of the new terminal UI.

- Source citations are relative to `site-packages/`, for example
  `textual/app.py:454`.
- Experiments E1 to E11 ran on Linux in a pseudo-terminal harness
  (`pty.fork`, scripted keys and signals, all output captured). They ran on
  CPython 3.13.14. E2, E4, E10, E11 and the wheel test also ran on CPython
  3.11.18. The scripts were in the session scratchpad; the useful ones are
  copied to `experiments/` beside this file.
- "Exit" means the process exit code. "On screen" means bytes that reached the
  terminal while the alternate screen was on.

## Headline findings

1. `App.run()` must run on the main thread. Off the main thread it fails
   inside the driver and returns `None` with return code 1.
2. Textual does not handle SIGTERM or SIGHUP. Use `loop.add_signal_handler` on
   the app's own loop (main thread). The run thread's loop cannot do it.
3. A closed terminal (SIGHUP) can hang the stock driver forever. A driver
   subclass whose output ignores `OSError` fixes it (tested).
4. The driver draws on fd 2. Do not `dup2` fd 2 alone. Give the driver its
   own copy of the terminal fd, then point fds 1 and 2 at the run log (tested:
   no stray output reached the screen).
5. Textual drops Python-level writes to `sys.stdout` and `sys.stderr` while it
   runs. `begin_capture_print` can route them to the run log (tested).
6. `call_from_thread` blocks the caller. Textual thread workers block
   `App.run()` from returning. Use plain threads and a `queue.SimpleQueue`
   that a UI timer drains.
7. An uncaught exception in any handler, timer or render closes the app
   (return code 1). There is no per-widget error boundary. Guard the drain.
8. Textual does not detect light or dark. An OSC 11 query before `App.run()`
   works (tested, also under tmux 3.4). A late reply turns into key presses.
9. `App.open_url` calls `webbrowser.open`. It can block the UI and draw the
   browser's output on the screen. Over SSH it opens nothing. Use our own
   opener.
10. Textual 8.2.8 fits the locked dependency set. Raise the `rich` floor to
    `>=14.2`. Three packages are new.
11. `Static.update()` always repaints. Repainting 60 cards at 10 Hz on
    210×62 costs about 65% of one core and 750 KiB/s of terminal output.
    Skipping unchanged text brings it to about 5%.

## 1. Threads and signals

### Evidence

`App.run()` off the main thread fails. `LinuxDriver.__init__` calls
`signal.signal(SIGTSTP, ...)` (`textual/drivers/linux_driver.py:78-79`).
This raises `ValueError: signal only works in main thread`.
`_process_messages` catches it (`textual/app.py:3515`) and prints a
traceback. `run()` returns `None` with `return_code == 1`. It does not raise
(E1).

Handlers while the app runs (E1, before → during → after `run()`):

| Signal | While the app runs | Source | After `run()` returns |
|---|---|---|---|
| SIGINT | `asyncio.Runner._on_sigint` | `asyncio/runners.py:104-106` (3.13) | default |
| SIGTERM, SIGHUP, SIGQUIT | default (Textual sets none) | — | default |
| SIGWINCH | driver resize handler | `linux_driver.py:246-250` | default (`linux_driver.py:361`) |
| SIGTSTP, SIGCONT | driver suspend/resume | `linux_driver.py:78-97` | **still installed** |
| SIGTTOU, SIGTTIN | set only for a start-up check | `linux_driver.py:205-229` | default |

The tty flags ISIG, ICANON, ECHO and IEXTEN are off while the app runs
(`linux_driver.py:337-340`). ISIG stays on only if `TEXTUAL_ALLOW_SIGNALS` is
set.

A second asyncio loop on another thread works next to Textual (E4, all
scenarios). The run thread's loop spawned and reaped
`asyncio.create_subprocess_exec(..., start_new_session=True)` on 3.11.18 and
3.13.14 (E11).

A loop on a non-main thread cannot take signals (E1):

- `loop.add_signal_handler` raises `RuntimeError: set_wakeup_fd only works in
  main thread of the main interpreter`.
- `signal.signal` raises `ValueError`.

Today's `_run_with_signals` suppresses `RuntimeError` around
`loop.add_signal_handler` (`src/kiln_harness_tuner/cli.py:1361`). In a run
thread it would install nothing and say nothing.

The app's loop is on the main thread. `loop.add_signal_handler` works there
(E1).

`kill -INT` while the app runs: the asyncio runner cancels Textual's main
task. Textual swallows the `CancelledError` (`textual/app.py:3461-3464`). The
app closes, `run()` returns normally with `return_code None`, and no
`KeyboardInterrupt` is raised (E2). A run thread would go on with no UI.

Terminal closed (E4 D, E4b). The kernel sends SIGHUP and then SIGCONT.

- Textual's SIGCONT handler calls `resume_application_mode()`, which starts a
  second input thread and writer thread (stack dump: two `textual-input`
  threads).
- The writer thread dies with `OSError(5, 'Input/output error')`.
- Its queue holds 30 writes (`textual/drivers/_writer_thread.py:9,17`). When
  the queue is full, `Queue.put` blocks the main thread.
- E4 D (stock driver, UI waits 1.6 s for the run to stop): the run stopped
  cleanly, but the process never exited. The main thread was blocked in
  `WriterThread.stop` → `Queue.put` (`_writer_thread.py:67`), from
  `driver.close()` (`linux_driver.py:401`) in `App._shutdown`
  (`textual/app.py:3749`).
- With an output file that ignores `OSError` (section 4), E4b exited in 3.7 s
  and E4 D returned from `run()` with 129. The closing block's `print()` to
  the dead terminal then raised `OSError` (exit 1).

Ctrl+Z after `run()` returns (E10). The SIGTSTP and SIGCONT handlers are still
installed. On SIGCONT the driver calls `start_application_mode()`, which
raises `RuntimeError: no running event loop` in the main thread. The code
after `run()` crashed. Resetting both signals to `SIG_DFL` after `run()` fixed
it.

Ctrl+Z inside the app: raw mode makes it a key. The default bindings do not
use it. `action_suspend_process` (`textual/app.py:4770`) would stop the whole
process, the run thread too.

### Recommendation

- Run `App.run()` on the main thread.
- In `App.on_mount`, call `asyncio.get_running_loop().add_signal_handler` for
  SIGTERM, SIGHUP and SIGINT. Remove them in `on_unmount`.
- The handler records the signal number. Then it calls
  `run_loop.call_soon_threadsafe(run_task.cancel)`. It does not need the UI.
  Tested: E4 C (SIGTERM while the run loop blocked its own thread for 3 s:
  the cancel took effect when the block ended; exit 143) and E4 H
  (`kill -INT`: exit 130).
- Keep today's one-shot rule, but do not restore `SIG_DFL` while the app is in
  raw mode and the alternate screen. A default SIGTERM kills the process and
  leaves the terminal broken. On a second signal, close the UI at once and
  let `main` wait for the run thread with plain output.
- Use the safe driver of section 4. It is also the fix for the SIGHUP hang.
- After `run()` returns: reset SIGTSTP and SIGCONT to `SIG_DFL`. If the run
  thread is still alive, cancel it and join it (E4 F). Catch `OSError` when
  you print the closing block.
- Leave Ctrl+Z unbound.

## 2. Ctrl+C, Ctrl+D, Ctrl+Q

### Evidence

- ISIG is off, so Ctrl+C is the byte 0x03. The parser makes it the key
  `ctrl+c` (E2 log: `key:ctrl+c`). Textual turns on the kitty keyboard
  protocol (`linux_driver.py:285-292`). There Ctrl+C arrives as a `CSI u`
  sequence and maps to `ctrl+c` too.
- Default `App.BINDINGS` (`textual/app.py:454-464`):
  - `ctrl+q` → `quit`, `priority=True` → `self.exit()`
    (`textual/app.py:4460-4462`). Exit 0.
  - `ctrl+c` → `help_quit`, `system=True`, not priority →
    `action_help_quit` (`textual/app.py:3990-4001`). It only shows a
    notification. E2 log: `Press [b]ctrl+q[/b] to quit the app`.
  - Before the App binding, `Screen` binds `ctrl+c` to `screen.copy_text`
    (`textual/screen.py:272`). It raises `SkipAction` when nothing is
    selected (`textual/screen.py:985-991`). `Input` and `TextArea` bind
    `ctrl+c` to copy (`textual/widgets/_input.py:139`,
    `textual/widgets/_text_area.py:381`).
- `ctrl+d` has no App binding. `Input` binds it to `delete_right`
  (`textual/widgets/_input.py:120`). In E2 it did nothing.
- Priority bindings are checked from the App down, before the focused widget
  (`textual/app.py:3966-3987`).
- E2 "override": priority bindings on `ctrl+c`, `ctrl+q` and `ctrl+d` opened
  the confirm screen while an `Input` had the focus. A plain `q` binding (not
  priority) did not fire: the `Input` typed the `q`. `y` in the confirm
  screen called `app.exit("stopped", return_code=130)`. `run()` returned
  `'stopped'`, the process exited 130, and the closing block appeared after
  `\x1b[?1049l` (alternate screen off). Same result on 3.11.18.

### Recommendation

- On the App: `Binding("ctrl+c", "request_stop", priority=True, show=False)`.
  Bind `ctrl+q` the same way, so it does not quit without the confirm. Bind
  `ctrl+d` as the design says.
- Bind `q` without priority, on the run screen only. Text inputs keep the
  letter.
- Make `request_stop` do nothing while the confirm screen is open.
- Optional: if `self.screen.get_selected_text()` returns text, copy it with
  `app.copy_to_clipboard` (`textual/app.py:1770`) instead of opening the
  confirm. This keeps Textual's copy gesture.
- Set `ENABLE_COMMAND_PALETTE = False` (`textual/app.py:427`). Ctrl+P opens a
  command palette by default.

## 3. Cross-thread communication

### Evidence (E3)

| Mechanism | What happened |
|---|---|
| `post_message` from a thread | Thread-safe: it uses `loop.call_soon_threadsafe` when the caller is on another thread and the app loop is set (`textual/message_pump.py:882-887`). Each call took 0.01–0.1 ms while the UI slept 0.5 s per message. No back-pressure: the 5th message waited 2.0 s. |
| `post_message` before the loop exists | Falls through to `put_nowait` on an `asyncio.Queue` from the foreign thread (`message_pump.py:887`). Not thread-safe. |
| `post_message` after exit | Returns `False` (`message_pump.py:875-876`). |
| `call_from_thread` | Blocks the caller until the callback ends: `future.result()` with no timeout (`textual/app.py:1833-1838`). A 1.5 s UI callback blocked the caller 1.50 s. After exit it raises `RuntimeError: App is not running` (`textual/app.py:1818-1819`). In 40 exits with a thread calling it in a loop, no call hung. |
| `queue.SimpleQueue` + `set_interval` drain | 100,000 puts took 40 ms while the UI slept 1 s per drain. One drain took them all. The oldest item waited 106 ms. |
| `@work(thread=True)` / `run_worker(thread=True)` | Runs in the app loop's default executor (`textual/worker.py:324-326`). On exit, `workers.cancel_all()` (`textual/app.py:3465`) only sets a flag and cancels the asyncio task (`textual/worker.py:416-421`). The thread runs on. `asyncio.run` then waits for the executor (`asyncio/runners.py:73` on 3.11, no timeout; `runners.py:74` on 3.13, 300 s). Exit asked at 1.0 s; `App.run()` returned at 6.02 s, when the worker's 6 s wait ended. A worker that waits for an answer would hang the exit forever on 3.11. |
| Plain non-daemon `threading.Thread` | `App.run()` returned at once (0.52 s). The interpreter waited for the thread at exit (process ended at 3.28 s). A daemon thread dies at interpreter exit with no `finally`. |

### Recommendation

- Run → UI: the run thread puts events on a `queue.SimpleQueue`. The UI drains
  it with `set_interval` (0.1–0.25 s), folds the events into a model, and
  repaints only what changed. The run thread never calls
  `call_from_thread`.
- UI → run: only thread-safe calls, such as
  `run_loop.call_soon_threadsafe(run_task.cancel)`.
- Start-up flow: a plain `threading.Thread(daemon=True)`, not a Textual
  worker. For each question it puts `(question, concurrent.futures.Future)`
  on the queue and blocks on `future.result()`. The UI sets the result. On
  stop or exit, the UI sets an exception on any open future (for example
  `OnboardingStopped`), so the flow unwinds. E4 G: Ctrl+C during a question,
  then `y`: exit 130 after 1.7 s.
- Run: a plain non-daemon `threading.Thread` with its own `asyncio.run()`.
  The thread stores its loop and main task in a handle. `main` joins the
  thread after `App.run()` returns.

## 4. stdout and stderr while the app runs

### Evidence

- While it runs, Textual replaces `sys.stdout` and `sys.stderr` with
  `_PrintCapture` (`textual/app.py:3491-3492`; class at
  `textual/app.py:260-294`). This is process-wide, so it applies to every
  thread.
- `App._print` (`textual/app.py:2081-2105`) sends the text to devtools, to
  targets set with `begin_capture_print` (`textual/app.py:2107-2130`), and in
  headless mode to the real streams. Otherwise Textual drops it.
- `_PrintCapture.fileno()` returns -1 (`textual/app.py:291-293`). Code that
  passes `sys.stderr` to a subprocess while the app runs will fail.
- The driver draws on fd 2: `self._file = sys.__stderr__`
  (`linux_driver.py:58`). So `harness-tuner 2>err.log` sends the whole UI to
  `err.log`, and a plain `os.dup2(log, 2)` sends the UI to the log.

E5, a pty of 120×40. Each row is one kind of stray write:

| Stray write (from a thread) | Stock | fd 2 → log | fds 1, 2 → log and `begin_capture_print` |
|---|---|---|---|
| `print()` | dropped | dropped | log |
| `print(file=sys.stderr)` | dropped | dropped | log |
| `warnings.warn` | dropped | dropped | log |
| `logging.StreamHandler` made before `App.run()` | **on screen** | log | log |
| `logging` with no handler (`lastResort`) | dropped | dropped | log |
| `logging.StreamHandler` made while the app runs | dropped | dropped | log |
| `os.write(2, ...)` | **on screen** | log | log |
| `os.write(1, ...)` | **on screen** | **on screen** | log |
| child process writes to inherited fd 2 | **on screen** | log | log |
| child process writes to inherited fd 1 | **on screen** | **on screen** | log |

More findings from E5:

- Redirecting fd 1 breaks size detection unless the driver measures its own
  tty. `_get_terminal_size` uses `shutil.get_terminal_size`
  (`linux_driver.py:107-126`). That reads the `COLUMNS` and `LINES`
  variables first, then fd 1. With an override that calls
  `os.get_terminal_size(tty_fd)`, Textual saw 120×40.
- Open the run log with `O_APPEND`. Without it, writes through fd 2 and
  writes through a second Python file handle overwrote each other (the first
  E5 run lost lines).
- `print()` makes two writes (text, then newline), so `events.Print` arrives
  in pieces. Buffer to whole lines before you log.
- The crash traceback goes through `error_console = Console(stderr=True)`
  (`textual/app.py:636`). Rich resolves `sys.stderr` when it prints
  (`rich/console.py:757-763`), in `_shutdown` (`textual/app.py:3756`), after
  the redirect has ended. With fd 2 on the run log, the traceback goes to the
  log, not to the screen.

The repository's agent children already pipe stdout and stderr
(`src/kiln_harness_tuner/dispatch/claude_cli.py:349-358`). The fd redirect
covers everything else: libraries, warnings, early log lines, other children.

### Recommendation (tested in E5 and E4 with `E4_SAFE=1`)

```
before App.run():
    tty_fd = os.dup(2)                # or os.open("/dev/tty", O_RDWR | O_NOCTTY)
    saved_1, saved_2 = os.dup(1), os.dup(2)
    log_fd = os.open(run_log, O_WRONLY | O_CREAT | O_APPEND)
    os.dup2(log_fd, 1); os.dup2(log_fd, 2); os.close(log_fd)
    app = TunerApp(driver_class=TTYDriver)        # textual/app.py:574, 637

class TTYFile:                        # the driver's output
    write(s): if not dead: try self._f.write(s) except OSError: dead = True
    flush():  same guard
    fileno(), isatty() -> True

class TTYDriver(LinuxDriver):
    __init__: super().__init__(...); self._file = TTYFile(os.dup(tty_fd))
    _get_terminal_size: return os.get_terminal_size(tty_fd)

TunerApp.on_mount: self.begin_capture_print(self)
TunerApp.on_print(event): append event.text to the run log (line-buffered)

after App.run():
    flush sys.stdout, sys.stderr; os.dup2(saved_1, 1); os.dup2(saved_2, 2)
    reset SIGTSTP, SIGCONT to SIG_DFL
    print the closing block (catch OSError)
```

- Choose the TUI only when stdin, stdout and stderr are all TTYs, or open
  `/dev/tty` for the driver.
- For a UI crash, print a short line on the restored terminal ("the display
  failed; details in the run log"). Do not rely on Textual's traceback.

## 5. App exit, return codes and errors

### Evidence

- `App.exit(result=None, return_code=0, message=None)`
  (`textual/app.py:1270-1289`). `run()` returns `result`. `app.return_code`
  holds the code (`textual/app.py:976-990`). E2: `run()` returned
  `'stopped'`, `return_code` 130, `sys.exit(130)` gave exit 130.
- The terminal is restored before `run()` returns:
  - `driver.stop_application_mode()` runs in the `finally` of
    `_process_messages` (`textual/app.py:3514`). It writes alternate screen
    off, cursor on, mouse off (`linux_driver.py:375-396`).
  - `_shutdown` calls `driver.close()` (`textual/app.py:3748-3749`), which
    joins the writer thread (`_writer_thread.py:65-68`). So all bytes are out.
  - E2, E4: the closing block came after `\x1b[?1049l`, in the normal
    scrollback.
- Uncaught exceptions in message handlers (`textual/message_pump.py:607, 669,
  691, 704`), timers (`textual/timer.py:195`), render and compose
  (`textual/widget.py:4725`, `textual/screen.py:1383`) and thread workers
  (`exit_on_error=True`, `textual/worker.py:149, 382-384`) all go to
  `App._handle_exception` (`textual/app.py:3263-3283`). It:
  - sets `return_code = 1`,
  - keeps the first error in `app._exception` (private),
  - renders a traceback with `show_locals=True` (`textual/app.py:3291`) and
    prints it after the terminal is restored,
  - closes the app.
- `run()` does not raise. E4 F: `run()` returned after 2.13 s with
  `return_code=1` and `_exception=RuntimeError('bad refresh (uncaught)')`.
  `run_test` re-raises at the end (`textual/app.py:2216-2218`, E8).
- Textual 8.2.8 has no per-widget error boundary.

### Recommendation

- Exit with `self.exit(code, return_code=code)` for 0, 1, 129, 130 and 143.
  `main` returns `app.return_code`.
- Guard each event in the drain with `try/except Exception`. Log the error to
  the run log, mark the panel, and go on. E4 E: a guarded fault, then the run
  finished normally with exit 0.
- Keep `render()` and `compose()` trivial. They read the model only.
- Detect a UI crash with `app._exception is not None` (private but simple), or
  with your own flag set in an override of `_handle_exception`.
- `show_locals=True` can put local values in the scrollback. With fd 2 on the
  run log, they go to the log instead. Keep secrets out of UI locals.
- Spec decision: when the UI crashes and the run is alive, E4 F cancels and
  joins the run (exit 1). The other choice is to let the run go on with the
  plain-text display.

## 6. Light or dark background

### Evidence

- Textual does not query the terminal colours. There is no OSC 10 or OSC 11
  in `textual/`.
- The parser has no rule for OSC replies (`textual/_xterm_parser.py`). A reply
  that arrives while the app runs becomes key presses. Fed to
  `XTermParser`:
  - `\x1b]11;rgb:1e1e/1e1e/2e2e\x1b\\` → `circumflex_accent`,
    `right_square_bracket`, `1`, `1`, `semicolon`, `r`, `g`, `b`, `colon`,
    `1`, `e`, … `backslash`.
  - The DA1 reply `\x1b[?62;22c` → `[`, `?`, `6`, `2`, `;`, `2`, `2`, `c`.
  - The design binds `c`, `l` and `r` on the run screen.
- The default theme is `TEXTUAL_THEME` or `textual-dark`
  (`textual/constants.py:163`, `textual/app.py:560`). The themes `ansi-dark`
  and `ansi-light` paint with the terminal's own palette
  (`textual/theme.py:445-476`). They follow the terminal without a query, but
  they lose the designed colours.
- Colour depth: rich uses truecolor only when `COLORTERM` is `truecolor` or
  `24bit` (`rich/console.py:805-811`). Otherwise `TERM=…-256color` gives 256
  colours. E7 output used `38;5;253` (no `COLORTERM`). Over SSH `COLORTERM`
  is often not sent, and macOS Terminal has no truecolor. The theme's hex
  colours are then rounded to the 256-colour palette.
  `TEXTUAL_COLOR_SYSTEM` (`textual/constants.py:149`) overrides it.

The query function (`experiments/osc11.py`, tested):

```
def query_background(timeout=0.5) -> (r, g, b) in 0..1, or None:
    fd = os.open("/dev/tty", O_RDWR | O_NOCTTY)     # OSError -> None (no tty)
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd, TCSANOW)     # no echo, no line mode; ISIG stays on
    try:
        os.write(fd, b"\x1b]11;?\x1b\\" + b"\x1b[c")   # OSC 11, then DA1
        read with select() until the deadline, or until a DA1 reply
            (rb"\x1b\[\?[\d;]*c") arrives: terminals answer in order,
            so DA1 means "all answers are in"
        parse rb"\x1b\]11;rgba?:(h{1,4})/(h{1,4})/(h{1,4})(\x07|\x1b\\)"
            each channel = int(h, 16) / (16 ** len(h) - 1)
    finally:
        termios.tcflush(fd, TCIFLUSH)      # drop a partial reply
        termios.tcsetattr(fd, TCSANOW, old)
        os.close(fd)

dark = 0.2126 r + 0.7152 g + 0.0722 b < 0.5
```

E6 results:

| Case | Result | Time |
|---|---|---|
| No controlling tty (this agent's shell; `start_new_session`) | `None` → dark | 0 ms |
| pty, terminal never answers | `None` → dark | 150 ms (the timeout used) |
| pty, dark reply with ST | dark | fast |
| pty, light reply with BEL | light | fast |
| pty, only DA1 answered | `None` → dark | fast |
| pty, reply 0.4 s late (timeout 0.15 s) | `None` → dark; **the reply leaked into stdin after the flush** | 150 ms |
| tmux 3.4, outer terminal answers light | light (tmux answered from its own state) | 0 ms |
| tmux 3.4, outer terminal silent | `None` → dark (tmux answered DA1) | 1 ms |

tmux 3.4 sent its own OSC 10 and OSC 11 queries to the outer terminal at
start. Then it answered the inner query itself. GNU screen was not tested.

### Recommendation

- Run the query in `main`, before `App.run()` and before any thread reads
  stdin. Set `app.theme` before `run()`.
- Send DA1 after OSC 11 and stop at the DA1 reply. Then a long timeout (about
  0.5–1 s, for slow SSH links) costs nothing on real terminals.
- If no DA1 reply comes, fall back to dark. Flush the input. Keep the
  run-screen letter bindings off until the first screen is up.
- Give the user a way to force the theme (a flag or `TEXTUAL_THEME`).
  `COLORFGBG` can be a second hint (not tested).

## 7. Links

### Evidence (E7)

- `[link="https://…"]text[/link]` makes an OSC 8 hyperlink.
  `Strip.render_style` wraps the text in `\x1b]8;id=…;url\x1b\\ … \x1b]8;;\x1b\\`
  (`textual/strip.py:701-705`). The raw output had it.
- `[@click=app.go('b')]text[/]` makes no OSC 8. The text is underlined
  (link style). A click runs the action through `App._broker_event`
  (`textual/app.py:4291-4316`). E7: the click ran `go(b)`.
- Textual has mouse reporting on, so a click on an OSC 8 link comes to the
  app. The `Click` event has `event.style.link` set. Textual does nothing
  more. The terminal opens OSC 8 links only with its own modifier gesture
  (different per terminal; not tested).
- `App.open_url(url)` (`textual/app.py:4790-4798`) calls
  `Driver.open_url`, which calls `webbrowser.open(url)`
  (`textual/driver.py:195-206`). `new_tab` has no effect in a terminal.
- With `BROWSER` set to a test script that prints and sleeps 1.5 s:
  `open_url` blocked the UI for 1.51 s. The script's stdout and stderr were
  drawn on the app screen. (`webbrowser.GenericBrowser` does `Popen` and
  `wait()`.)
- With no `DISPLAY` and no console browser (this container),
  `webbrowser.open` returned `False`. Textual ignores the result, so nothing
  happens and nothing is shown.
- With no `DISPLAY` but `TERM` set, `webbrowser` registers `www-browser`,
  `links`, `lynx` and `w3m` (`webbrowser.py:549-563`, 3.13). A text browser
  would then fight Textual for the terminal.
- Over SSH, `webbrowser` runs on the remote host. It never opens the user's
  local browser.

### Recommendation

- Do not call `App.open_url`. Write our own opener.
- Local GUI (macOS, or Linux with `DISPLAY` or `WAYLAND_DISPLAY`, and no
  `SSH_CONNECTION` or `SSH_TTY`): start `open` or `xdg-open` with
  `subprocess.Popen(..., stdin/stdout/stderr=DEVNULL, start_new_session=True)`.
  Do not wait for it.
- Otherwise: show the URL as text with an OSC 8 `link` style. Offer a copy key
  that uses `app.copy_to_clipboard` (OSC 52, `textual/app.py:1770`). OSC 52
  works over SSH in terminals that allow it.
- Use `@click` actions for clickable text. In the action, call our opener.

## 8. Testing

### Evidence (E8: 5 tests, all passed in 0.87 s, pytest 9.2 and pytest-asyncio 1.4.1 with `asyncio_mode=auto` like the repository)

- `run_test()` defaults: `headless=True`, `size=(80, 24)`, `tooltips=False`,
  `notifications=False` (`textual/app.py:2136-2140`). With notifications off,
  `notify` shows nothing. Pass `notifications=True` to test toasts.
- Keys: `await pilot.press("ctrl+c")` fired the priority binding.
  `isinstance(app.screen, Confirm)` was true. `press("y")` gave
  `app.return_code == 130`.
- Another thread can put items on the `SimpleQueue` during `run_test`. The
  test waits with a small poll loop (`await pilot.pause(0.02)` until the
  model shows the item).
- `pilot.pause()` with no delay waits for pending messages only
  (`textual/pilot.py:535-547`). It does not run interval timers: the
  test with a 0.5 s drain timer saw nothing after `pause()`. Calling
  `app.drain()` from the test (same thread as the app) is deterministic.
- `app.export_screenshot(title=...)` (`textual/app.py:1855`) returns SVG text.
  No plugin is needed. The UI preview's `make_screenshots.py` already does
  this. `pytest-textual-snapshot` adds only a compare and update workflow
  (syrupy).
- An uncaught UI exception re-raises at the end of `run_test`
  (`textual/app.py:2216-2218`). `pytest.raises` caught it.
- Pilot cannot test raw mode, the alternate screen, signals or SIGHUP. The pty
  harness used for E1–E10 (about 120 lines, `pty.fork` with scripted keys and
  signals; `experiments/ptyharness.py`) tested all of them offline, each in a
  few seconds.

### Recommendation

- Test the app with `run_test(size=(210, 62))`. Drive the queue directly.
  Call the drain directly where timing matters.
- Inject the clock and turn off spinners in tests, so screenshots are stable.
- Make signal installation injectable. `run_test` runs in pytest's loop on the
  main thread, so `add_signal_handler` would install real handlers.
- Add a few pty tests (marked slow) for Ctrl+C → exit 130, SIGTERM → 143,
  SIGHUP → 129 with no hang, stray stderr not on screen, closing block after
  the alternate screen.
- Do not add `pytest-textual-snapshot` unless you want golden-file diffs.

## 9. Dependencies

Textual 8.2.8 metadata (`textual-8.2.8.dist-info/METADATA`):
`Requires-Python >=3.9,<4.0`, classifiers 3.9 to 3.14.

| Requirement | Range | Repository lock (`uv.lock`) | Result |
|---|---|---|---|
| rich | `>=14.2.0` | 15.0.0 | OK. `pyproject.toml` says `rich>=13.7`: raise it to `>=14.2`. |
| markdown-it-py[linkify] | `>=2.1.0` | 4.2.0 | OK |
| mdit-py-plugins | any | not locked | new: 0.6.1 (needs markdown-it-py `>=2,<5`) |
| linkify-it-py (from the extra) | — | not locked | new: 2.2.0, no dependencies |
| platformdirs | `>=3.6.0,<5` | 4.11.11 | OK |
| pygments | `>=2.19.2,<3.0.0` | 2.21.0 | OK |
| typing-extensions | `>=4.4.0,<5.0.0` | 4.16.0 | OK |
| prompt-toolkit, questionary | — | 3.0.53, 2.1.1 | No overlap with Textual |

- `uv pip compile` of the repository's runtime dependencies plus
  `textual==8.2.8` resolved on 3.11 and on 3.13. There was no conflict. A free
  resolve chose rich 14.3.4. With `rich>=15` it chose 15.0.0. Both satisfy
  Textual.
- Installed size: 5.9 MB. `import textual.app, textual.widgets` took about
  175 ms (3 runs, 3.13).
- E2, E4, E10, E11 and the wheel test passed on 3.11.18. The one 3.11
  difference found: `asyncio.run` waits for executor threads with no timeout
  (section 3).

Recommendation: add `textual>=8.2.8,<9` (or a pin, as for questionary) and
`rich>=14.2`. If the start-up flow leaves questionary, prompt-toolkit can go.

## 10. A `.tcss` file shipped as package data

### Evidence

- A relative `CSS_PATH` is resolved by `_make_path_object_relative`
  (`textual/_path.py:47-72`) against `inspect.getfile(obj.__class__)`: the
  file of the concrete class, unless `_BASE_PATH` is set. Textual reads it at
  start (`textual/app.py:3391-3392`). A CSS error goes to
  `_handle_exception`, and the app does not start (`textual/app.py:3410-3413`).
- Test: a small hatchling package with `packages = ["src/tcsspkg"]`, the same
  form as the repository. The wheel held `tcsspkg/tui/tuner.tcss` with no
  extra configuration. Installed in a new 3.11 venv and run from `/tmp`:
  `css_path` pointed into `site-packages`, and the rule
  `color: rgb(1,2,3)` applied.
- A subclass in another module looked for the file next to that module
  (`…/pkgtest/tuner.tcss`), did not find it, and the app did not start.
  `run()` returned `None`.

### Recommendation

- Use an absolute path: `CSS_PATH = Path(__file__).with_name("tuner.tcss")`.
  A subclass in a test then still finds the file.
- Add a check to `tests/packaging/smoke_installed_wheel.py` that the `.tcss`
  file is in the wheel.
- `ruff format` does not read `.tcss` files.

## 11. Other things the architect must know

### Update cost (E9: pty 210×62, 60 cards with borders in a grid, a 10 Hz timer, 5 s, 4-core container)

| Mode | CPU (% of one core) | Terminal output | Handler median | Longest timer interval |
|---|---|---|---|---|
| idle | 2.3% | 54 KiB/s (the first paint) | — | 100.6 ms |
| 2 cards change | 12.7% | 78 KiB/s | 0.32 ms | 101.6 ms |
| 60 cards, `Static.update()` | 64.3% | 748 KiB/s | 3.56 ms | 139 ms |
| 60 cards, `Static.update()` with the same text | 65.8% | 705 KiB/s | 3.38 ms | 224 ms |
| 60 cards, same text, the caller skips it | 4.5% | 54 KiB/s | 0.03 ms | 100.5 ms |
| 60 cards, `update(layout=False)` | 60.1% | 721 KiB/s | 3.58 ms | 170 ms |
| 60 widgets with a `reactive` that `render()` reads | 55.4% | 748 KiB/s | 3.02 ms | 141 ms |
| `RichLog`, 300 lines, +20 lines/s | 13.3% | 116 KiB/s | 0.70 ms | 101 ms |
| `RichLog`, 10,000 lines, +20 lines/s | 37.9% | 97 KiB/s | 0.71 ms | 505 ms (one stall) |

- `Static.update()` always repaints. It does not compare
  (`textual/widgets/_static.py:85-95`). A `reactive` skips equal values
  (`textual/reactive.py:350`) unless `always_update=True`.
- The cost is in the paint and the composite, not in the handler.
  `layout=False` saved little.
- `RichLog` keeps every line unless `max_lines` is set
  (`textual/widgets/_rich_log.py:61, 264`). Set it.
- Timers: a 0.1 s `set_interval` was within 1 ms when idle. By default it
  skips missed ticks (`skip=True`, `textual/timer.py:55, 162`). The screen
  repaints at most 60 times per second (`TEXTUAL_FPS`,
  `textual/constants.py:146`, `textual/screen.py:78`).

Recommendation: keep a model. Compare the new text of each widget with the
last text, and call `update()` only on a change. Drain at 4–10 Hz. Keep
spinners in one small widget. Over slow SSH links the terminal output rate
matters more than CPU.

### Other surprises

- `App.run()` sets `asyncio.eager_task_factory` on its own loop on 3.12+
  (`textual/app.py:2282-2283`). It does not touch the run thread's loop.
- Text selection is on by default (`ALLOW_SELECT`, `textual/app.py:403`).
  Mouse reporting is on, so the terminal's own selection needs its modifier
  key (often Shift, Option on macOS).
- At start the driver sends queries for synchronized output (`?2026$p`) and
  in-band resize (`?2048$p`) (`linux_driver.py:298-299, 315-324`). It also
  sends OSC 22 pointer-shape sequences (`textual/app.py:3931`). Textual parses
  their replies itself.
- `shutil.get_terminal_size` prefers exported `COLUMNS` and `LINES`. If a
  user exports them, the stock driver uses them and does not follow a resize.
  The `TTYDriver` override of section 4 measures the tty.
- Hangup also sends SIGCONT, and Textual then restarts application mode
  (section 1).
- `run(inline=True)` (`LinuxInlineDriver`) draws under the prompt with no
  alternate screen. It is an option if the design wants run lines to stay in
  the scrollback.

## Proposed shape, from the tested prototype (E4)

```
main thread
    bg = query_background()                     # section 6, before anything reads stdin
    redirect fds 1, 2 to the run log; TTYDriver gets a dup of the tty   # section 4
    app = TunerApp(theme from bg, driver_class=TTYDriver)
    app.run()                                    # blocks; UI on the main loop
    restore fds 1, 2; reset SIGTSTP, SIGCONT
    if the run thread is alive: handle.cancel(); thread.join()
    print the closing block (catch OSError)
    return app.return_code

TunerApp (main loop)
    on_mount: add_signal_handler(SIGTERM, SIGHUP, SIGINT -> request_stop(128 + sig))
              set_interval(0.1-0.25 s, drain)
              start the start-up thread (daemon)
    drain:    for each event from the SimpleQueue: try apply(event) except: log
              apply = update the model, then update() only the widgets whose text changed
    request_stop(code): once; show "Stopping..."
              in start-up: fail the open Future, exit(code)
              in the run:  run_loop.call_soon_threadsafe(run_task.cancel)
    "run ended" event -> exit(code, return_code=code)
    ctrl+c / ctrl+q / q -> confirm screen -> request_stop(130)

start-up thread (daemon)
    prepare_start(...) with a Terminal whose ask() puts (question, Future)
    on the queue and blocks on Future.result()
    then starts the run thread

run thread (non-daemon)
    asyncio.run(main): store loop and current_task in the handle; await _run(args)
    events -> SimpleQueue; the finally blocks run on cancel
    put "run ended" when done
```

Prototype results with this shape: normal end exit 0; Ctrl+C and confirm exit
130; SIGTERM during a 3 s self-block exit 143 after the block; `kill -INT`
exit 130; terminal closed exit 129 (with the safe driver); a guarded UI fault
did not stop the run; an unguarded UI fault exited 1 and the run was
cancelled and joined.
