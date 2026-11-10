---
status: complete
---

# Phase 5: The start-up screen

## Overview

This phase puts the start-up flow in the app (architecture section 4) and makes the app reachable. `prepare_start` runs on the start-up thread behind `AppTerminal`, which turns each `Terminal` call into a request on the app's queue. The start-up screen draws the requests as a transcript that grows down (ui_design section 8), with the action menu, the picker and the text answer as Textual widgets that keep every behavior of functional spec section 5. Kiln reads go through a runner that the app can cancel, so Ctrl+C during a busy check unwinds the start. `main` then sends a terminal invocation to `run_app`. `RichTerminal`, questionary and their tests go; `PlainTerminal` stays for the plain path. The run screen stays the phase 4 placeholder until phase 6, and `LiveUi` stays with it.

## Steps

1. **`tui/bridge.py`** (no Textual imports).
   - The requests, one frozen dataclass each, on the queue as `Stamped(UI_REQUEST, {"request": request})`: `Welcome(title, version, description)`, `Step(status, label, text)`, `Block(title, body)`, `Say(text)`, `Warn(text)`, `Busy(text)`, `BusyEnd()`, `Opened(url, opened)`. The three prompts carry `answer: concurrent.futures.Future`: `Pick(question, options, hint, default, answer)`, `Act(question, actions, answer)`, `Text(question, default, hint, validate, answer)`.
   - `opened_text(url, opened) -> str`: "Opening <url>" or "Open this link in your browser: <url>" (today's texts).
   - `AppTerminal(queue, *, interactive, opener=open_link)`, the `Terminal` protocol. `busy` is a context manager that puts `Busy` and, in `finally`, `BusyEnd`. A prompt puts its request and waits on its future. `act` keeps `RichTerminal`'s rule that only the first action may have the key `ENTER`. With `interactive=False`, `block`, `pick`, `act`, `text` and `open_url` raise `RuntimeError("not a guided start")`. `open_url` calls the opener on the start-up thread and puts `Opened`.
   - `AppTerminal.abandon(stop: OnboardingStopped)`: the first stop is kept. The open prompt's future fails with it, and every later prompt raises it at once.
   - `CancellableRunner(connect)`: a `KilnRunner`. Each call is `asyncio.run` over `async with connect() as kiln: await fn(kiln)`, and it records its loop and task. `cancel()`: from then on every call raises `KeyboardInterrupt` (which `prepare_start` turns into `OnboardingStopped(130)`); a call in flight is cancelled through `call_soon_threadsafe`, and its `CancelledError` becomes `KeyboardInterrupt`. `cancellable_runner(host)` connects with `KilnClient(host)`.

2. **`tui/handles.py`**: `StartHandle(target, queue, *, on_stop=None)`. `stop(code)` keeps the first `OnboardingStopped(code)` and calls `on_stop` with it, once.

3. **`tui/widgets.py`**: add `TitleBar` (4.1: the mark on the left with the task name once known, muted meta on the right; `TITLE_MARKUP` moves here from `screens/startup.py`), `StepLine` (4.7: glyph, 9-character label, text; `Content`, so names show as typed), `BusyLine` (a blue spinner on its own 0.1 s timer in place of the glyph), and `link(url)` / `linked(text)`: a URL as a clickable link (`$primary`, underlined, "↗"), which runs `app.open_link`.

4. **`tui/screens/prompts.py`**: the three prompt widgets. Each holds its request and posts `Answered(request, value)` once, or `Failed(request, error)`.
   - `ActionMenu`: the question, then an `OptionList` of "key) label" rows ("Enter) …" for `ENTER`). A letter that one action has acts at once with no Enter; arrows and Enter work; any other key is ignored.
   - `Picker`: the question, a filter `Input` (it has the focus), the rows as `aligned_rows` in an `OptionList` that can't take the focus, the highlighted row's description, and the hint (`hint or PICK_HINT`). The filter matches the whole row text, case-insensitively. A disabled row shows its reason and can't be highlighted; the arrows skip it and wrap. The cursor starts on the default row when it is enabled, else on the first enabled row, and goes to the first enabled row after each filter change. Enter picks the highlighted row, and does nothing when the filter matches nothing.
   - `TextAnswer`: the question, an `Input` with the default pre-filled (not selected), the validator's message in red under it, and the hint. Enter runs the validator on the typed text: a message shows and the text stays; none answers with the text as typed. Editing clears the message. A validator that raises fails the prompt with its error.

5. **`tui/screens/startup.py`**: `StartupScreen`: `TitleBar`, a `VerticalScroll#transcript` that can't take the focus, `Footer`. `show(request)` (requests that come before the screen is mounted wait in a backlog):
   - `Welcome`: a card titled with the title in bold `$text-primary`, the description, the version as the subtitle.
   - `Step`: a step line. A `Task` step (`choose.py`'s label) puts the task name in the title bar (`task_name(text)`, the inverse of `choose._step`'s format).
   - `Block`: a problem card: red border, "✗ <title>" as its title, the body lines. `Warn` right after it goes in the card as a `!` line; elsewhere a `Warn` is a `!` step line.
   - `Say`: a text whose first line is `SUMMARY_HEADING` is a card titled with it; a text whose first line is `RERUN_HEADING` goes in that card in muted text (or alone in muted text); any other text is a line.
   - `Busy`/`BusyEnd`: the busy line, then gone.
   - `Opened`: a line with `opened_text`, its URL a link.
   - Prompts: the widget mounts and takes the focus. On `Answered` the widget is removed and the future gets the value; on `Failed` it gets the error. The transcript scrolls to its end after each part.
   - URLs in step, warning, body and open lines are links.

6. **`tui/app.py`**:
   - The drain hands `UI_REQUEST` events to the start-up screen.
   - `action_open_link(url)`: `opener.open_link`.
   - `action_stop_start(code)`: `start.stop(code)` (the bridge fails the open prompt and cancels the runner). The app closes when the start thread has ended (its `start_ended`), or after `stop_grace` seconds (default 2), or at once on a second stop or when the thread is no longer alive.
   - `close_app` acts once.

7. **`tui/entry.py`**: `_AppRun` builds `AppTerminal(queue, interactive=not args.no_interaction)` and `cancellable_runner(host)`, and passes both to `prepare_start` (`terminal=`, `runner=`). The start handle's `on_stop` abandons the terminal and cancels the runner. When the app closes with the start-up thread still alive, the thread is released the same way, so it does not wait on a prompt nobody answers.

8. **`cli.py`**: `main` calls `display_mode(args)`. `plain` runs `_finish(_plain_body)` as today. The app modes import `tui.entry` there and return `run_app(args, raw_argv, parser, mode, finish=...)`, so the plain path still never imports Textual.

9. **Removals.**
   - `onboarding/console.py`: `RichTerminal` and its helpers (`_key_name`, `_exit_with`, `_stop_on_ctrl_d`, `_inquirer_control`, `_pick_bindings`, `KILN_BLUE`, `WELCOME_MAX_WIDTH`, `SYMBOLS`), `default_terminal` and `_is_tty`, and the imports they used. `PlainTerminal` and `LABEL_WIDTH` stay. The docstring says what is left.
   - `onboarding/flow.py`: with no `terminal`, `prepare_start` uses `PlainTerminal(prog)` (the old rule picked it in every plain-mode case). The module and function docstrings lose questionary and prompt_toolkit.
   - `pyproject.toml`: `questionary` and its comment; `uv.lock` again.
   - `tests/test_onboarding_console.py`: the `RichTerminal` and `default_terminal` tests (ported in step 10); the `PlainTerminal` tests stay.

10. **`tuner.tcss`**: the start-up styles of `ui_preview/preview.tcss` (transcript, welcome, start-up cards, problem border, prompts, the menu `OptionList`, `Input`) and the link style.

## Tests

- `tests/tui/test_bridge.py`:
  - `test_each_call_puts_its_request_in_order`: welcome, step, block, say, warn, busy (on and off, also when the body raises), open on the queue as requests.
  - `test_a_prompt_waits_for_its_answer`: `pick`, `act`, `text` on a thread return the value the future gets; a failed future raises its error.
  - `test_a_start_that_isnt_guided_never_asks`: the five guided calls raise `not a guided start`.
  - `test_act_lets_only_the_first_action_be_enter`.
  - `test_abandon_fails_the_open_prompt_and_every_later_one`; the first stop wins.
  - `test_open_url_says_whether_the_page_opened` (both texts).
  - `test_the_runner_returns_what_the_call_returns_and_closes_the_client`.
  - `test_cancel_stops_the_call_in_flight_with_keyboard_interrupt`, and every later call; a cancel with nothing in flight stops the next call; the client is closed.
- `tests/tui/test_startup.py` (pilot, 210×62, `AppTerminal` on the start-up thread, ported from `test_onboarding_console.py`):
  - pick: by arrows; by filter text on a column (ids too); skips a disabled row; Enter never picks a disabled row the filter leaves; the filter puts the cursor on its first enabled row and Backspace re-filters; the arrows skip disabled rows of the filtered list; Enter does nothing while the filter matches nothing; starts on its default, or on the first enabled row for a disabled or unknown default; the description of the highlighted row; the hint replaces `PICK_HINT`; rows aligned; disabled rows show their reason.
  - act: by its key with no Enter; by arrows and Enter; ignores a key no action has; `Enter)` by Enter and others by their keys; rows read "Enter) Keep these", "r) Check again".
  - text: Enter accepts the default; typed after the default; a refused answer shows the message and keeps the text; editing clears the message; a validator that raises fails the prompt.
  - Ctrl+C (130) and Ctrl+D (1) at every prompt, and during a busy check (the runner's call is cancelled); a second Ctrl+C closes at once; the footer shows `^c Stop` and `^d Exit` at every prompt.
  - output: the step line format; names, markup and emoji codes show as typed; the welcome card; the problem card with its warnings; the summary card with the rerun command; the busy line comes and goes; `open_url`'s two texts and a clickable link; the task name in the title bar; the newest part in view.
- `tests/tui/test_startup_flow.py`: the real `prepare_start` with `AppTerminal`, `CancellableRunner` and a fake Kiln, driven by pilot: a guided start to "Start the run?" and `s`, with its report; `x` at "Start the run?" gives exit 1; a not-ready task's problem card, `r` re-checks; `--no-interaction` shows the step lines and asks nothing.
- `tests/tui/test_app.py`, `test_entry.py`: the fakes take `on_stop`; the entry test checks that the start gets `AppTerminal` and `CancellableRunner`, and that a stop while the start waits on a prompt exits with its code.
- `tests/tui/test_pty.py` (`slow`): `main` on a real terminal goes to the app; `x` at "Start the run?" exits 1 with `stopped.`, and Ctrl+C at it exits 130.
- `tests/test_cli.py`: `main` sends the app modes to `run_app` with `_finish`, and the plain mode to the plain body; the existing tests stay plain.
- `tests/test_onboarding_kiln_check.py`: the default terminal is the plain one (rename only).
