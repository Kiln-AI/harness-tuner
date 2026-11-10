---
status: complete
---

# Architecture: New TUI

This document sets how the new TUI is built. `functional_spec.md` (with its section 16) sets what it does, and `ui_design.md` (with its section 14) sets how it looks. The research behind each decision:

- `research/codebase/startup_bridge.md`: the start-up flow, the hand-off, the exits, the stray writers, the removal inventory.
- `research/codebase/data_contract_audit.md`: every screen element against the events, and the exact emit points and schemas of the loop changes (its section 2).
- `research/textual/summary.md`: Textual 8.2.8 at runtime, with a tested prototype (`research/textual/experiments/e4_arch.py`) and a pty test harness (`experiments/ptyharness.py`).

Scope rule from the user: this is TUI work. The loop changes only by emitting more data. Anything that exists today (signals, cancel, exit lines) is reused, not redesigned.

## 1. Shape

Three threads in one process, only when the app runs:

```
main thread     Textual App.run(). The UI and its asyncio loop. Owns the terminal.
                Catches SIGTERM, SIGHUP, SIGINT and forwards them.
start-up thread daemon. Runs prepare_start() with AppTerminal. Blocks on the UI
                for each question.
run thread      non-daemon. asyncio.run(_run_with_signals(args, ...)). The loop,
                unchanged. Its events go to TuiSink, which puts them on a queue.
```

Data flows one way per direction:

- run → UI: `TuiSink.event()` stamps the event and puts it on a `queue.SimpleQueue`. A UI timer drains the queue at 4 Hz, folds the events into a model, and repaints only the widgets whose text changed.
- UI → run: only `run_loop.call_soon_threadsafe(run_task.cancel)`.
- start-up ↔ UI: each question is a request object with a `concurrent.futures.Future`. The start-up thread waits on the future. The UI sets its result or its exception.

Nothing on the run thread waits for the UI. Nothing on the UI thread waits for the run, except at exit (a join).

## 2. Entry and modes

`cli.main` picks a mode after `validate_args`, before anything reads stdin:

| Mode | When | What runs |
|---|---|---|
| `plain` | stdin or stdout is not a TTY, or both `--no-interaction` and `--no-progress` | Today's code path, unchanged: `prepare_start` with `PlainTerminal`, `asyncio.run` on the main thread, `PlainUi` |
| `app_startup` | TTYs and `--no-progress` | The app runs the start-up only. When `prepare_start` returns, the app closes, the start part of the closing block prints, then today's path runs on the main thread with `PlainUi` |
| `app` | TTYs, no `--no-progress` | The app runs the start-up and the run |

`--no-interaction` in `app` mode: the app shows the start-up's step lines with no questions (`AppTerminal(interactive=False)`), then the run screen.

The mode test is `sys.stdin.isatty() and sys.stdout.isatty()`, read in `main` before the app starts. While the app runs, Textual's stream capture answers `isatty()` with True, so nothing inside the app may test TTYs. `prepare_start` therefore always gets `terminal=` from `main` in the app modes.

`main`'s body is split so both paths share the exception arms:

```
def main(argv):
    parse, validate
    mode = display_mode(args)
    if mode == "plain":
        return _finish(lambda: _plain_body(args, raw_argv, parser))   # today's code
    return tui.entry.run_app(args, raw_argv, parser, mode, finish=_finish)

def _finish(body) -> int:
    try:
        result = body()
    except OnboardingStopped / OnboardingError / CancelledError / KeyboardInterrupt / SHOWN_ERRORS:
        (today's arms, unchanged)
    print finish line; return 0
```

In app modes, `run_app` re-raises the start-up thread's or the run thread's exception on the main thread after the app has closed, inside `_finish`. So every exit line and exit code is today's. The one extra: a `BaseException` from a worker thread (a `SystemExit` from `_run`, cli.py:1179, 1192) is caught by the thread wrapper and re-raised on the main thread, where Python prints it as today.

## 3. Package layout

New package `src/kiln_harness_tuner/tui/`. Only `cli.py` imports it, and only inside `run_app`'s mode, so a plain run never imports Textual.

```
tui/
  entry.py        run_app(): threads, fd redirect, theme query, App.run(), joins,
                  closing block, re-raise. The only module cli.py calls.
  handles.py      RunHandle (loop, task, thread), StartHandle; thread wrappers that
                  catch BaseException
  terminal_io.py  TTYFile, TTYDriver, redirect_fds() context manager, stray-output file
  theme.py        KILN_DARK, KILN_LIGHT (textual Theme), pick_theme(), query_background()
  opener.py       open_link(url) -> bool; graphical_browser_expected() (moved from console.py)
  app.py          TunerApp(App): themes, bindings, signal handlers, drain timer, screen switch
  bridge.py       AppTerminal (the Terminal protocol), request types, cancellable_runner
  sink.py         TuiSink: the run's display (EventLog + context manager)
  closing.py      ClosingBlock: collects the start part, the run part, the error part
  model/          pure Python, no Textual imports
    events.py     Stamped(kind, fields, mono, wall)
    tracker.py    UiTracker(RunStatusTracker): stamped clock, extra handlers
    state.py      the view model dataclasses (section 7)
    fold.py       RunModel.apply(stamped): the reducer
    results.py    concerns and the three tiers
    cards.py      item → CardView; verdict words
    details.py    item key → DetailsView (the details panel); the picker's groups
    timeline.py   segments, projection, wave estimate
    log.py        event → LogLine
    words.py      every user-facing string of the run screen
    links.py      compare_url(), lineage_url()
    names.py      RcNames: the run config name dictionary
  screens/
    startup.py    StartupScreen, ProblemCard, SummaryCard
    prompts.py    ActionMenu, Picker, TextAnswer
    run.py        RunScreen and its sections
    stop.py       StopDialog
    details.py    RunModal, DetailsPanel, ExperimentPicker (functional spec 7.9)
  widgets.py      TitleBar, Section, SectionHeader, Card, TierBar, Gauge, Steps,
                  JudgedStrip, Timeline, RunLog, KeyLine, Tag, Link, Spinner (from
                  ui_preview/widgets.py)
  tuner.tcss      the stylesheet (package data); CSS_PATH = Path(__file__).with_name(...)
```

`specs/projects/new-tui/ui_preview/` is the starting point for `widgets.py`, `tuner.tcss` and the screens. The preview stays as design material.

## 4. The start-up in the app

### 4.1 AppTerminal (bridge.py)

`AppTerminal` implements the `Terminal` protocol (`onboarding/terminal.py:66-118`) for the start-up thread. Each method builds a request and hands it to the app:

```
@dataclass
class Request:            # one per call
    kind: "welcome" | "step" | "block" | "say" | "warn" | "busy_on" | "busy_off"
          | "pick" | "act" | "text" | "open"
    payload: the call's arguments
    future: Future | None # only pick, act, text

AppTerminal.pick(question, options, hint, default):
    future = Future(); queue.put(Request("pick", ..., future))
    return future.result()     # value; or raises OnboardingStopped(130 | 1)
```

- Requests go on the same `SimpleQueue` that the run uses, as `Stamped("ui_request", ...)`. One drain handles both.
- `welcome`, `step`, `block`, `say`, `warn` do not wait.
- `busy(text)` is a context manager that puts `busy_on` and `busy_off`. It keeps the protocol's rule: no prompt inside a spinner.
- `interactive` is set at construction. With `interactive=False`, `pick`, `act`, `text`, `block` and `open_url` raise `RuntimeError("not a guided start")`, as `PlainTerminal` does (console.py:343-397).
- `open_url(url)`: `opener.open_link(url)`. When it returns True, the app shows "Opening <url>"; else "Open this link in your browser: <url>". Both texts are today's (console.py:335-340). The link is clickable.
- `say` texts also go to `ClosingBlock` (an `on_say` hook), which keeps the last summary and the last rerun command, by their first lines (section 10).

The app answers a request on the UI thread. Ctrl+C sets `OnboardingStopped(130)` on the open future, Ctrl+D sets `OnboardingStopped(1)`. If no question is open (a busy check is running), Ctrl+C calls `cancellable_runner.cancel()` (4.2) and the app exits as soon as the start-up thread ends.

### 4.2 The cancellable Kiln runner

`prepare_start` already takes `runner=` (flow.py:105-145). `cancellable_runner(host)` does what `kiln_runner` does (an `asyncio.run` per call with a fresh `KilnClient`), and it records the running loop and task while a call runs. `cancel()` calls `loop.call_soon_threadsafe(task.cancel)`. The runner turns the `CancelledError` into `KeyboardInterrupt`, which `prepare_start` already maps to `OnboardingStopped(130)` (flow.py:192-195). So the demo copy's cleanup runs (demo.py:178-182).

### 4.3 The start report

`prepare_start` gets one new keyword, `report: Callable[[StartReport], None] | None = None`. It calls it once, just before it returns. Default None, so every caller and test is unchanged.

```
@dataclass(frozen=True)
class StartReport:
    project_name: str; task_name: str
    baseline_rc_id: str | None; baseline_name: str | None
    summary: str            # summary() as said last
    rerun: str | None       # rerun() as said last; None when not guided
```

The title bar takes the names from it, `RcNames` (section 8) is seeded with the baseline's name, and `ClosingBlock` takes the start part.

`tests/test_cli.py:345-373` pins `prepare_start`'s keywords strictly. That test is updated for the app modes only; the plain path still calls `prepare_start` as today.

### 4.4 Widgets

`ActionMenu` (an `OptionList` with one binding per action key), `Picker` (an `Input` filter over an `OptionList`), `TextAnswer` (an `Input` with the validator), `ProblemCard`, step lines and the spinner line. They keep every behavior in functional spec 5 ("Interaction behaviors to keep"). The rules that `RichTerminal` implements today move with them: `aligned_rows` and `COLUMN_GAP`, `PICK_HINT`, `_key_name` ("Enter)" for `ENTER`), the first-enabled-row rule, the filter on the whole row text. `aligned_rows`, `PICK_HINT` and `graphical_browser_expected` move out of `console.py` before it is cut down (section 12).

## 5. The run in the app

### 5.1 Seams in cli.py (additive)

- `_run(args, *, display=None)` and `_run_with_signals(args, *, display=None, on_started=None)`. `display` is a factory `(tracker, console) -> EventLog & context manager`. None keeps today's choice (cli.py:1300-1306), so the plain path is unchanged.
- `on_started(loop, task)` is called at the top of `_run_with_signals`. `RunHandle` keeps both. The signal handlers that `_run_with_signals` tries to install fail silently off the main thread (cli.py:1358-1362). That stays as it is; the app forwards the signals (section 6).
- The run line `print(f"{PROG}: run {run_id} — files in {run_dir}/")` (cli.py:1210) stays. In app mode it lands in the stray-output file; the closing block names the run folder.
- `_RUN_PROGRESS.cli_checked` (set at cli.py:1199) is read by the app's timer to end the "Agent CLI" step. No new event.

### 5.2 TuiSink (sink.py)

Built by the `display` factory, so it sits where `ui` is in `FanoutLog(wave_log, tracker, ui, ctx.spend)` (cli.py:1310), after the loop's tracker.

```
class TuiSink:
    def __init__(self, tracker, queue, closing): ...
    def event(self, kind, **fields):
        try:
            if kind == "exit":
                closing.run_exit_line = plain_exit_line(fields, tracker.snapshot())
            elif kind == "report_written":
                closing.report_path = fields.get("path")
            queue.put(Stamped(kind, fields, time.monotonic(), datetime.now()))
        except Exception:
            logger.exception("tui sink")     # never raises into the loop
    __enter__/__exit__: no-ops
```

- `plain_exit_line` is today's `PlainUi` exit line (ui.py:989-990, 914-918) moved to a pure function that `PlainUi` also calls. So the closing block's run part is the same text as the plain path, built at the same moment (the audit's point: totals keep ticking after `exit`).
- Payloads are passed as they are. The audit found no emitted object that the loop changes after the emit (section 3.2). The UI treats every payload as read-only. The new events (section 9) send fresh dumps.
- The stamps give the UI the loop's own timings (audit 3.3, 5.2).

### 5.3 After the run

When `_run_with_signals` returns or raises, the run thread wrapper stores the result or the exception in `RunHandle` and puts `Stamped("run_ended", ...)`. The app switches to Done (or closes, on a stop) when it drains that event.

## 6. Signals, stop and exit

- `TunerApp.on_mount` adds `loop.add_signal_handler` for SIGTERM, SIGHUP and SIGINT on its own loop (the main thread). `on_unmount` removes them. Installation is injectable, so tests do not install real handlers.
- A signal during the run: append the number to `cli._stopped_by` (today's list, read by `main`'s `CancelledError` arm), then `run_handle.cancel()`. The app shows "Stopping…". Today's teardown runs on the run thread: agent children killed, lock released, `exit` event, no report.
- A signal during the start-up: fail the open future (or cancel the runner) with `OnboardingStopped(128 + signum)`, and close the app.
- A second signal: close the app at once. `run_app` then joins the run thread with plain output (section 11).
- `q` or Ctrl+C on the run screen opens `StopDialog`. "Stop tuning" does what a SIGINT does: `_stopped_by.append(SIGINT)`, cancel. `main` then prints today's "interrupted (SIGINT) — children killed, lock released" and exits 130. No new exit code and no new exit line (decided).
- Bindings on the App, all `priority=True`: `ctrl+c` (stop or stop dialog), `ctrl+d` (start-up only: exit 1), `ctrl+q` (same as `ctrl+c`, so it never quits without the dialog). `e`, `q`, `c`, `g`, `r` are plain bindings on the run screen. The command palette is off. Ctrl+Z is not bound.
- The details panel and the picker are `RunModal` screens pushed over the run screen (`TunerApp.show_item`, `show_picker`; a card's click, `e`). Each has its own `q` (the stop dialog, as on the run screen; nothing on Done) and Esc. The stop dialog pushes over them: it is always the top screen while open, so the `isinstance(self.screen, StopDialog)` checks of the end and of a signal still hold, and its answer runs after its pop (Textual's `call_next`). A run that ends on a stop or a failure closes the app whatever is open; a normal end pops the stop dialog only, and an open panel stays over Done.
- After `App.run()` returns, `run_app` resets SIGTSTP and SIGCONT to `SIG_DFL` (Textual leaves its handlers, research 1).

## 7. The run model (model/, pure)

### 7.1 State

```
RunModel
  tracker: UiTracker                 # RunStatusTracker subclass, clock = event stamp
  info: RunInfo                      # CLI facts: run id, names, host, agent CLI, slots, objective
  goal: GoalView                     # fixed map of the objective + split line (16.2, 16.4)
  clock: ClockView                   # elapsed, limit, spend, lanes, burn, wave N of ~M, last wave
  timeline: list[Segment]            # kind, wave, start_mono, end_mono | None, mark
  concerns: list[Concern]            # eval_id, score_key, name, score_type, direction
  tiers: dict[concern_key, Tiers]    # start, confirmed, unconfirmed (+ holder rc, item id)
  items: dict[item_id, ItemModel]    # details (C5), state, chunk cells (C3), arm results (C1), verdict
  waves: list[WaveModel]             # item order, parent rc, probe phase, planning steps, screens
  gate: GateModel | None             # candidates, fills (C4), tables (C2), decision
  rc_items: dict[rc_id, item_id]
  log: list[LogLine]
  done: DoneView | None
  version: int                       # bumps on every change; widgets compare their own text
```

`UiTracker` adds handlers only in the subclass. The loop's own tracker, which the report reads, is not changed (audit section 2 rules).

### 7.2 Key rules

- **Concerns** come from the `detectability` event's rows, in order, one per (eval, score) with `promotion_driving`, then cost, then latency. An eval first seen later (built mid-run) is added when a C1b or C2 row names it.
- **Start**: pass rates from the detectability rows ((n − room) ÷ n). Rating, cost and latency from the first C1b row's `baseline_score` and passive values for that concern; until then "not measured yet". After the first gate, Start is the first gate's incumbent row on val.
- **Best Confirmed**: the rc of the last confirmed promotion (rule below). Its values are its winning C2 table's `candidate_score`. Its change against Start is colored by sign only.
- **Promotion confirmed**: a `promotion` with a winner counts only when the gate's `phase_end promotion_gate` arrives with no `promotion_blocked` for that rc in between (audit finding 11).
- **Best Unconfirmed**, per concern: among items since the last confirmed promotion, with a successful verdict and a C1b row for that concern, the best `parent value + delta`, shown only where it beats Best Confirmed. Screen survivors count for cost and latency (`screening_round.results`). It clears at each confirmed promotion and drops an rc rejected at val. Dim when the row's interval crosses zero.
- **In flight**, per eval concern: live items whose C5 `targets` include that eval. None for cost and latency.
- **Cards**: one per `wave_start.items` entry, in order. Lines from C5 details, `item_state`, C3 cells, `item_detail`, the verdict. Verdict words are a table in `words.py` (functional spec 7.6 plus 16.10, with the card short forms of ui_design 7.5). Items named by `deadline_cancel` or `fatal_cancel` with no outcome get "Cut off: tuning ended".
- **Timeline**: segments from phase, wave, screening and gate events by their stamps (audit 1.4). The projection uses the mean of finished main-wave cycles (planning start to planning start).
- **Wave N of ~M** (priority 3): `M = N + min(floor((time_left − WRAP_UP_WINDOW_SECS) / mean_cycle), floor(budget_left / mean_cycle_cost))`, never below N, and only after wave 1 ends. The two constants are imported from `orchestrator.py` (lines 181, 187). If this proves awkward, show "wave N" alone.
- **Log lines**: `log.py` maps each event in functional spec 7.7 to a glyph and plain words. `memory_written` (C7) lines show the record kind and its overview, for kinds lesson, dead end and issue.

### 7.3 The drain

```
def drain(self):                      # set_interval(0.25) on the app
    batch = pop everything from the queue
    for stamped in batch:
        try: model.apply(stamped)
        except Exception: logger.exception(...); model.note_ui_error()
    if model.version changed: screen.refresh_from(model)
```

`refresh_from` asks each section for its new text and calls `update()` only where the text changed (research 11: 65% CPU otherwise). Then each open `RunModal` on the screen stack gets the model too (`show`), so a panel updates live under a stop dialog as well. A panel updates its rows in place while their shape holds, and mounts them again at the same scroll position when a field or section comes or goes. The panel and the picker find an item by its key, `(index of its wave_start in RunModel.waves, item id)`: item ids are only wave-unique, and `RunModel.items` (by id) holds the newest item of each id, the one the loop's events name. `RichLog` gets `max_lines=2000` and only the new lines. Spinners and the clock live in two small widgets on their own 0.1 s timer.

## 8. Run config names (model/names.py)

The user asked for real names. `RcNames` keeps `rc_id → (name, description)`.

- Seeded from `StartReport` (the baseline's name).
- When the model sees an rc id it does not know, it marks the dictionary stale. The app runs one async lookup at a time on its own loop (an async Textual worker, not a thread worker): `GET /api/projects/{p}/tasks/{t}/run_configs` with its own `KilnClient`, at most once every 10 s. It is the route the loop already uses (probes.py:221). Rows give `id`, `name`, `description`.
- On a failed lookup it logs, keeps the ids, and tries again on the next unknown id.
- Display: the name; the item id beside it when the rc was built this run ("Hold fields wrapper · w3-4"). Before the name is known, the item id or the rc id.

This read is in the app, not the loop. It adds one small Kiln GET per new rc, at most every 10 s.

## 9. The loop changes (additive)

The schemas, emit points and risks are in `data_contract_audit.md` section 2. The rules there apply to all: new event kinds only (never `eval_progress`, whose handler raises without its fields), plain JSON-safe dumps, `PlainUi` and the loop's tracker unchanged.

| Change | Kind | Emit point | Delta from the audit |
|---|---|---|---|
| C1a | `arm_estimates` | `items.py` `_measure_arm`, at the `measurement` event | as proposed |
| C1b | `arm_results` | `items.py` `_execute_plan_item`, after the bundle's latency and cost are set | as proposed |
| C2 | `gate_val_tables` | `orchestrator.py` `_run_promotion_gate`, after the tables are final (quality: after `_record_gate_blocks`; speed and cost: after the second `write_val_summary`) | as proposed |
| C3 | new fields on `measure_chunk` | `measure.py` `_measure` | plus `cells: [[eval_id, item_id, parent_pass, cand_pass]]` for the rows judged in this chunk (decision 16.1) |
| C4 | `gate_fills_planned`, `gate_fill` | `orchestrator.py` `_assemble_val_tables` | as proposed |
| C5 | new keys in `wave_start.items[]` | `status.item_summary` | as proposed; copies only. `predicted_effect` (fixes) and `reason` (eval builds, measures, re-measures) added for the details panel (TUI iteration 2) |
| C6 | `open_questions` | after each `write_open_unknowns` | as proposed |
| C7 | gateway file `gateway_memory.jsonl` + `memory_written` | `gateway/ops.py` `memory_create`, `memory_update`; forwarded from `ClaudeCliDispatcher._run` through an `on_session_end` hook, and once before the first `exit` | as proposed, but the kind field is `record_kind` (`kind` is the event's own name); the file joins `RUN_DIR_ENTRIES` |

Each change gets a unit test that the event appears with its fields, and a test that `PlainUi` prints nothing new.

## 10. The closing block (closing.py)

`ClosingBlock` collects three parts and prints them after the app has closed and the fds are restored:

1. **Start**: `StartReport.summary` and `StartReport.rerun`, as said (today's texts). A start that ends with no report (Exit, Ctrl+C or Ctrl+D at "Start tuning?", or an error after the summary) gives the last summary and rerun command it said instead (section 4.1). A report, when there is one, wins. In `app_startup` mode this is all it prints, before the plain run starts.
2. **Run**: `run_exit_line` and `report: <path>` (both built by `TuiSink`, section 5.2), then "Run folder: <run_dir>" from `_RUN_PROGRESS`.
3. **Error**: nothing new. `main`'s arms print it as today, because `run_app` re-raises inside `_finish`.

Printing catches `OSError` (a closed terminal).

## 11. The terminal (terminal_io.py)

- **The driver's own tty.** `TTYDriver(LinuxDriver)` writes to `TTYFile(os.open("/dev/tty"))`, which ignores `OSError` (this fixes the SIGHUP hang, research 1). `_get_terminal_size` reads that fd.
- **fds 1 and 2.** For the app's lifetime, `redirect_fds()` points fds 1 and 2 at a stray-output file (`O_APPEND`), made with `tempfile.mkstemp(prefix="harness-tuner-", suffix=".log")` before the app starts. It is not in the state folder, whose checks run later in `_run`. At exit the fds are restored. If the file is not empty, its content is appended to the run log when a run folder exists, else its path is printed with the closing block. Then it is removed.
- **Python-level writes.** `begin_capture_print` sends them to the app, which writes whole lines to the same file.
- **Links.** `opener.open_link` starts `open` or `xdg-open` with stdin, stdout and stderr on `DEVNULL` and `start_new_session=True`, and does not wait. It returns False with no graphical browser or over SSH (`SSH_CONNECTION`, `SSH_TTY`). `App.open_url` is never used (it blocks and draws the browser's output).
- **A crash of the app** while the run is alive: `run_app` restores the fds, prints "The display stopped: <error>. The run goes on with plain lines; details in the run log.", switches `TuiSink` to forward to a `PlainUi`, installs plain `signal.signal` handlers that cancel the run, and joins the run thread. The run is not changed (functional spec 11).

## 12. Theme (theme.py)

- `KILN_DARK` and `KILN_LIGHT` are `textual.theme.Theme` objects with the tokens of ui_design section 2 (hex only; computed colors are not valid in borders). They are registered in `TunerApp.__init__`.
- `query_background()`: the OSC 11 query with a DA1 end marker, from `research/textual/experiments/osc11.py`, 0.5 s timeout, input flushed after. It runs in `run_app` before the app or any thread reads stdin.
- `pick_theme()`: `TEXTUAL_THEME` when it names one of the two themes; else light when the query says the background is light; else dark. No new flag.

## 13. Dependencies and removals

- Add `textual>=8.2.8,<9`. Raise `rich` to `>=14.2`. Remove `questionary` (and so `prompt_toolkit`).
- Remove `RichTerminal` and its helpers, and `LiveUi` and its renderers, as `startup_bridge.md` section 5 lists. Keep `PlainTerminal`, `PlainUi` and the shared helpers it names.
- With no `terminal`, `prepare_start` uses `PlainTerminal`; `default_terminal` is removed (`test_onboarding_kiln_check.py`, `test_with_no_terminal_the_start_is_plain`). App modes always pass `terminal=`.
- `tuner.tcss` ships as package data. The wheel smoke test checks it is in the wheel and that `import kiln_harness_tuner.tui.app` works from the installed wheel.

## 14. Error handling

| Where | Error | What happens |
|---|---|---|
| `TuiSink.event` | any | Logged; the loop goes on. Never raises. |
| The drain | an event the model can't fold | Logged; that event is skipped; the screen goes on |
| A widget refresh | any | Logged; the section keeps its last text |
| Name lookup | Kiln error | Logged; ids shown; retried on the next unknown id |
| The app itself | a crash | Section 11: plain lines for the rest of the run |
| The start-up thread | `OnboardingStopped`, `OnboardingError`, Kiln errors | Stored; re-raised in `main`'s arms after the app closes |
| The run thread | any `BaseException` | Stored; re-raised in `main`'s arms after the app closes |

Logging: the run log from `configure_run_logging` (unchanged). The app's own logger is `kiln_harness_tuner.tui`.

## 15. Testing

All offline, in `uv run pytest`.

- **Model** (`tests/tui/test_model_*.py`): fold recorded event sequences (built with the real emitters' field shapes) and assert the views: tiers and their reset, promotion confirmation and block, Best Unconfirmed rules, cut-off items, verdict words, timeline segments, wave estimate, log lines, compare and lineage URLs (2 to 10 columns), names.
- **Loop changes**: one test per change for the event and its fields, and that `PlainUi` output is unchanged.
- **Start-up widgets** (pilot, `run_test(size=(210, 62))`): the behaviors in `tests/test_onboarding_console.py` (startup_bridge 6.2), ported: keys, filter, disabled rows, defaults, validator, Ctrl+C 130, Ctrl+D 1, `open_url` texts. The flow tests with `ScriptedTerminal` stay as they are.
- **Bridge**: `prepare_start` on a thread with `AppTerminal` and a fake Kiln, driven by pilot; the start report; the cancellable runner during a busy check.
- **Run screen** (pilot): feed `Stamped` events into the queue, call `drain()` directly, assert widget text for each mode and the stop dialog.
- **Entry**: `run_app` with a fake `_run` (an async function that emits events), for each mode, the closing block, the re-raised exceptions and `_stopped_by`.
- **pty tests** (`@pytest.mark.slow`, with `ptyharness.py`): SIGTERM cancels the run and tears down; SIGHUP exits with no hang; stray stderr never reaches the screen; the closing block prints after the alternate screen.
- **Docs**: `tests/test_docs.py` stays green; no new flags.

Signal installation, the theme query and the drain interval are injectable, so tests are deterministic.
