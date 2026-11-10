# The start-up bridge: the code seams a Textual app plugs into

A read-only map of `main` at `656854f`. It covers the start-up flow, the hand-off to the run, and the output after the run. It is for the design where Textual runs on the main thread, `prepare_start` runs on a worker thread behind a new `Terminal`, the run's asyncio loop runs on a third thread, and a plain closing block goes to the scrollback when the app closes.

Every line number below was read from the file itself. Re-check before you rely on one.

**Drift in `cli_and_startup.md`.** Its `cli.py` and `console.py` line numbers still hold. Two sets do not:

| Earlier inventory | Now | Why |
|---|---|---|
| `flow.py:146-236` (`prepare_start`), `132-143` (`kiln_runner`), `113-121` (constants), `202-215` (summary, rerun), `228-236` (except) | `flow.py:105-195`, `91-102`, `71-80`, `161-174`, `187-195` | about 41 lines moved up |
| `error_screen.py:249-258`, `261-262`, `265-276`, `279-297`, `304-309`, `312-357`, imports `228-229` | `error_screen.py:38-47`, `50-51`, `54-65`, `68-86`, `93-98`, `101-146`, `17-18` | off by 211: the numbers of a file read after `summary.py` (211 lines) |

---

## 1. The `Terminal` protocol, exactly

### 1.1 Data types (`onboarding/terminal.py`, `onboarding/errors.py`)

| Name | Where | Definition |
|---|---|---|
| `T` | terminal.py:20 | `TypeVar("T")` |
| `StepStatus` | terminal.py:22 | `Literal["ok", "fail", "warn"]`. The flow in `src/` only ever passes `"ok"` (every `.step(` call; `demo.py:152-153` included). `"fail"` and `"warn"` occur only in tests. |
| `WHAT_NEXT` | terminal.py:24 | `"What next?"` |
| `Option[T]` | terminal.py:27-35 | frozen dataclass: `value: T`, `label: str`, `columns: tuple[str, ...] = ()` ("aligned across the rows"), `description: str \| None = None` ("shown under the highlighted row"), `disabled: str \| None = None` ("why it can't be picked, shown dimmed") |
| `ActionKind` | terminal.py:38-48 | `Enum`: `RETRY`, `OPEN`, `PICK_TASK`, `PICK_PROJECT`, `USE_DEMO`, `EXIT`, `START`, `CHANGE`, `KEEP`, `CUSTOMIZE` |
| `ENTER` | terminal.py:53 | `"enter"`. The key of an action that Enter picks, drawn `Enter)`. It may only lead a menu. |
| `Action` | terminal.py:56-63 | frozen dataclass: `key: str` ("one lower-case letter, unique in its menu, or ENTER"), `label: str`, `kind: ActionKind`, `url: str \| None = None` ("the page an OPEN action opens") |
| `_retry(label)` | terminal.py:121-122 | `Action("r", label, ActionKind.RETRY)` |
| `EXIT_ACTION` | terminal.py:125 | `Action("x", "Exit", ActionKind.EXIT)` |
| `Problem` | terminal.py:128-164 | frozen dataclass: `title: str`, `body: tuple[str, ...]`, `actions: tuple[Action, ...] = ()`, `retry_label: str = "Check again"`, `retry_first: bool = True`, `warnings: tuple[str, ...] = ()`. `menu(extra)` (145-151): `(retry, *actions, *extra, EXIT)` or, with `retry_first=False`, `(*actions, retry, *extra, EXIT)`. `error()` (153-164): an `OnboardingError(title, (*body, *links))`, where `links` is `"<label>: <url>"` for each OPEN action whose URL is not already in a body line. |
| `OnboardingError` | errors.py:13-24 | `__init__(title, body=())`. Fields `title`, `body` (a tuple), `shown = False`. `str()` is `plain_text(title, body)` (errors.py:8-10): the title, then each body line indented 2. |
| `OnboardingStopped` | errors.py:27-33 | `__init__(exit_code: int)`, field `exit_code`. `str()` is `"stopped (exit <code>)"`. |

### 1.2 The methods

The protocol is `class Terminal(Protocol)` at terminal.py:66-118. It has one attribute and ten methods. There are no others.

| Member | Signature (terminal.py) | Returns | Blocks for the user? | Raises (RichTerminal) |
|---|---|---|---|---|
| `interactive` | `interactive: bool` (67) | – | – | – |
| `welcome` | `(self, title: str, version: str, description: str) -> None` (69-72) | `None` | no | – |
| `step` | `(self, status: StepStatus, label: str, text: str) -> None` (74-76) | `None` | no | – |
| `block` | `(self, title: str, body: Sequence[str]) -> None` (78-80) | `None` | no | – |
| `say` | `(self, text: str) -> None` (82) | `None` | no | – |
| `warn` | `(self, text: str) -> None` (84) | `None` | no | – |
| `busy` | `(self, text: str) -> AbstractContextManager[object]` (86-89) | a context manager | no. It wraps a blocking Kiln call. "No prompt runs inside it." | – |
| `pick` | `(self, question: str, options: Sequence[Option[T]], *, hint: str \| None = None, default: T \| None = None) -> T` (91-103) | the picked option's `value` | **yes** | Ctrl-C → `OnboardingStopped(130)`; Ctrl-D → `OnboardingStopped(1)` |
| `act` | `(self, question: str, actions: Sequence[Action]) -> Action` (105) | the chosen `Action` object (identity: `actions[i]`) | **yes** | same |
| `text` | `(self, question: str, *, default: str = "", hint: str \| None = None, validate: Callable[[str], str \| None] \| None = None) -> str` (107-116) | the typed text, as typed (callers strip it) | **yes**, and again while `validate` returns an error | same |
| `open_url` | `(self, url: str) -> None` (118) | `None` | no | – |

`OnboardingError` is never raised by a `Terminal`. The flow raises it.

### 1.3 What `RichTerminal` does for each (`onboarding/console.py`)

Construction: `RichTerminal(prog, *, console=None, pt_input=None, pt_output=None)` (163-176). `prog` is not stored. The console is `Console(highlight=False)` on stdout. Every prompt gets `erase_when_done=True` (172), so it disappears after the answer. `interactive = True` (161).

| Method | Behaviour | Lines |
|---|---|---|
| `_print` | `console.print(text, soft_wrap=True, markup=False, highlight=False, emoji=False)`: no markup, no emoji codes, and no newline put inside a long line | 178-184 |
| `welcome` | A `Panel` with `box=ROUNDED`, `padding=(0, 1)`, `width=min(console.width, WELCOME_MAX_WIDTH)` (68), border `KILN_BLUE` `"#415CF5"`. First row: the title in `bold #415CF5` on the left, `v<version>` dim on the right. Then a blank row and the description. Then one blank line. | 186-202 |
| `step` | `✓` green / `✗` red / `!` yellow (`SYMBOLS`, 49-53), then `f" {label:<9} {text}"` (`LABEL_WIDTH = 9`, 46). Only the symbol is coloured. | 204-206 |
| `block` | a blank line, `f"✗ {title}"` in bold red, a blank line, each body line as `f"  {line}"`, a blank line | 208-214 |
| `say` | the text as is | 216-217 |
| `warn` | `! <text>`, `!` in yellow, **on stdout** (the comment says a stderr console would write past the spinner's redirect) | 219-224 |
| `busy` | `console.status(Text(text), spinner="dots")`. `Text`, not `str`, so Kiln's names are not read as markup. rich prints nothing when the console is not a terminal. | 226-228 |
| `_ask` | Merges an eager Ctrl-D binding (`_stop_on_ctrl_d`, 92-93, which exits with `EOFError`) and the caller's bindings over questionary's own, then `question.unsafe_ask()`. `KeyboardInterrupt` → `OnboardingStopped(130)`; `EOFError` → `OnboardingStopped(1)`. | 230-244 |
| `pick` | Rows are `aligned_rows(options)` (65-78): label and columns, each column padded to its widest cell (`cell_len`), joined by `COLUMN_GAP = "   "`, right-stripped. `questionary.select(question, choices, default=start, use_search_filter=True, use_jk_keys=False, instruction=hint or PICK_HINT)`. `PICK_HINT = "↑↓ move · type to filter · Enter pick"` (48). **A `hint` replaces `PICK_HINT`.** Each choice: `title=row`, `value=index`, `disabled=option.disabled`, `description=option.description`, `shortcut_key=False`. The filter matches the whole row text, so an ID matches. `default` is used only when an option with that value is enabled; else the cursor starts on the first enabled row (questionary's own choice). | 246-287 |
| `pick` keys (`_pick_bindings`) | Over questionary 2.1's own. **Filter keys** (`string.printable` and Backspace): add the character, then put the cursor on the first enabled row of the filtered list. **Enter** (`ControlM`): does nothing while the filter matches nothing, or on a disabled row; else picks the pointed row. **Down/Ctrl-N, Up/Ctrl-P**: move to the next enabled row of the *filtered* list, wrapping. It reads questionary internals (`InquirerControl`: `filtered_choices`, `found_in_search`, `search_filter`, `pointed_at`, `is_answered`, `add_search_character`). | 96-152 |
| `act` | Asserts that only `actions[0]` may have key `ENTER` ("Enter picks the first action"). Each row is `f"{key}) {label}"`, with `Enter` for `ENTER` (`_key_name`, 81-82). `instruction=" "` (an empty one gets questionary's "(Use arrow keys)"). One eager binding per non-Enter key exits with that action at once, with no Enter. Enter picks the row under the cursor, which starts on the first. Arrows move. A key no action has is ignored. `j`/`k` are off. | 289-316 |
| `text` | `questionary.text(question, default=default, instruction=hint, validate=verdict)`. `verdict` returns `True` or the validator's message. questionary shows the message, keeps the typed text, and waits for another Enter. The default is pre-filled; Enter accepts it. | 318-333 |
| `open_url` | When `graphical_browser_expected()` (56-62: `sys.platform` is `darwin` or `win32`, or `DISPLAY` or `WAYLAND_DISPLAY` is non-empty): prints `Opening <url>`, then `webbrowser.open(url)`. Else prints `Open this link in your browser: <url>` and starts nothing. | 335-340 |

`PlainTerminal(prog)` (343-397), for a start with no one to ask: `interactive = False`. `welcome` prints nothing. `step` prints `f"{label:<9} {text}"` with no symbol. `say` prints the text. `warn` prints `f"{prog}: {text}"` to **stderr**. `busy` is `nullcontext()`. `block`, `pick`, `act`, `text` and `open_url` raise `RuntimeError("not a guided start")`.

### 1.4 The one problem loop

`resolve_problem(check, terminal, *, busy, extra=())` (terminal.py:167-197):

1. `with terminal.busy(busy): problem = check()`.
2. No problem: return `None`.
3. `not terminal.interactive`: `terminal.warn(w)` for each warning, then `raise problem.error()`.
4. Guided: `terminal.block(title, body)`, then `terminal.warn(w)` for each warning, then `_ask_what_next` (200-209).
5. `_ask_what_next` loops on `terminal.act(WHAT_NEXT, menu)`. `EXIT` raises `OnboardingStopped(1)`. `OPEN` calls `terminal.open_url(url)` and asks again, with no re-check and no new block. Any other action returns.
6. `RETRY` goes back to step 1 (a new spinner, a new check, a new block if it still fails). Any other action is returned to the caller.

### 1.5 Every action menu the flow can show

| Menu | Keys in order | Where |
|---|---|---|
| Kiln not running; something else answering | `r` "Check again (Kiln is open now)" (not running only), `d` "Open the download page", `x` | kiln_check.py:28-30, 99-124 |
| Kiln too old | `r` "Check again (I've updated and reopened Kiln)", `u` "Open Kiln's update page", `d`, `x` | kiln_check.py:127-146 |
| No projects | `m` "Use the demo project", `o` "Open Kiln to create a project", `r`, `x` (`retry_first=False`) | choose.py:190-213 |
| No tasks | `o` "Open Kiln to create a task", `r`, [`p` "Pick a different project" if the project was picked], `x` | choose.py:216-232, 37, 377 |
| Not ready | `r` "Check again (I've fixed it in Kiln)", then `c` "Open Run configs in Kiln" and/or `e` "Open Evals in Kiln" (by what is missing), [`t` "Pick a different task" if the task was picked], `x` | readiness.py:134-170; flow.py:71, 315 |
| Missing API key | `r` `f"Check again (I've added the {keys})"`, `p` "Open AI Providers in Kiln", `x` | keys.py:314-329 |
| No agent CLI | `r` "Check again (I've installed one)", `x` | questions.py:285-296 |
| Advanced options | `Enter` "Keep these", `c` "Customize" | questions.py:588-591, 669-671 |
| Start the run? | `s` "Start the run", [`e` "Change an answer" if any question is changeable], `x` "Exit" | flow.py:72-74, 216, 220 |

Pickers: `Which project do you want to tune?`, `Which project with task <id>?`, `Which task in <project>?` (choose.py:33, 346, 384), `What should the tuner improve?`, `Which agent CLI should run the tuner's agents?` (questions.py:331, 394; this one has disabled rows `"not installed"`), `Which answer do you want to change?` (flow.py:75, 225-228). Text questions: questions.py:341-485.

### 1.6 Every place that relies on `interactive` or on a TTY

| Place | What it decides |
|---|---|
| terminal.py:188 | `resolve_problem`: raise the problem as an error, or show a block and a menu |
| choose.py:162 | `choose_one`: more than one row and not interactive is `OnboardingError(f"{flag} is needed when the start isn't guided", listing)` |
| choose.py:305 | `_no_projects(..., guided=terminal.interactive)`: the body line differs: `"the demo: a small moderation task with its evals and data already set up."` (guided) or `f"the demo: {prog} --demo-project"` |
| flow.py:176-184 | guided: the questions, the summary, the rerun command and "Start the run?"; not guided: `terminal.say(summary())` only |
| flow.py:188-190 | an `OnboardingError`: guided shows `terminal.block(title, body)` and sets `error.shown = True` |
| console.py:400-409 | `default_terminal`: `RichTerminal` when `sys.stdin.isatty()` and `sys.stdout.isatty()` and not `no_interaction`, else `PlainTerminal` |
| console.py:171, 228 | rich's own rules on the `Console`: colour off for `NO_COLOR` or a non-terminal; the spinner prints nothing on a non-terminal |
| cli.py:1300-1306 | the run display: `LiveUi` when `not args.no_progress and Console().is_terminal` (rich's test on stdout, which `FORCE_COLOR` and `TTY_COMPATIBLE` override) |
| error_screen.py:93-98, 112, 142 | `show_error`, `show_closing_line`: the rich or the plain form, by `stream.isatty()` alone (stderr by default) |

### 1.7 The contract a new `Terminal` must keep

`tests/onboarding_fakes.py:46-195` (`ScriptedTerminal`) states it as assertions. A new implementation must keep each:

- No prompt runs while a spinner runs, and no spinner runs inside a spinner (102, 115).
- No prompt and no `block` in a start that is not interactive (87, 112).
- `pick` returns an enabled option's value (137-138). `act` returns one of the given actions (145-148). A text answer passes `validate` (162-164).
- A `KeyboardInterrupt` raised from a prompt also gives exit 130: `prepare_start` turns it into `OnboardingStopped(130)` (flow.py:192-195; test_onboarding_flow.py:631-641).

---

## 2. How `prepare_start` picks a terminal and returns

### 2.1 The call

`main` (cli.py:1441-1447) calls:

```
prepare_start(args, raw_argv, prog=PROG, option_dests=parser.option_dests,
              policy_loader=load_model_policy)
```

It passes no `terminal`, no `runner`, no `home` and no `which`.

`prepare_start(args, argv, *, prog, option_dests, policy_loader, terminal=None, runner=None, home=None, which=shutil.which) -> argparse.Namespace` (flow.py:105-195):

| Parameter | Default and use |
|---|---|
| `terminal` | `None` → `default_terminal(prog, no_interaction=args.no_interaction)` (flow.py:142-143; console.py:404-409). This is the only place a terminal is built. |
| `runner` | `None` → `kiln_runner(host)` (flow.py:145, 91-102): each call runs `fn` in its own `asyncio.run` with a fresh `KilnClient(host)`. It works on any thread that has no running loop, so it works on a worker thread as is. Tests pass `runner_for(RouteKiln)` (onboarding_fakes.py:255-262), which is also an `asyncio.run`. |
| `home` | `Path.home()`: where the demo copies go |
| `which` | `shutil.which`: finds the agent CLIs |

**Guided or plain.** Guided exactly when stdin and stdout are both TTYs and `--no-interaction` is not given (console.py:407). `terminal=` overrides this.

### 2.2 What it returns

It returns **the same `args` object** it was given, changed in place:

- `args.project_id`, `args.task_id`, `args.project_path` (`str(selection.project_path)`) at flow.py:273-275;
- for the demo, the same three plus `args.demo_project = True` (flow.py:239-242);
- every `Answers` field (`objective`, `guidance`, `agent_cli`, `budget_usd`, `timeout_mins`, `model_policy`, `min_effect`, `max_concurrent_evals`, `max_concurrent_agents`, `noise_check`) by `answers.apply(args)` (flow.py:185; questions.py:180-183).

It does **not** put in `args`: the project name, the task name, Kiln's folder for the project as a `Path`, the baseline run config (`_KeyCheck.baseline`, flow.py:338), whether a row was picked, the summary text, or the rerun command.

### 2.3 What it prints, and through which method

| Output | Method | Guided | Plain | Line |
|---|---|---|---|---|
| welcome box | `welcome(WELCOME_TITLE, __version__, WELCOME_DESCRIPTION)` | box | nothing | flow.py:150; text at 76-80 |
| `Kiln` step, its warning | `step`, `warn` | yes | yes | flow.py:289-291 |
| demo, project, task steps | `step` (via demo.py:152, choose.py:172-178) | yes | yes | |
| `Ready` step, its warnings | `step`, `warn` | yes | yes | flow.py:321-323 |
| `API keys` step | `step` | yes | yes | flow.py:364 |
| question steps (`Improve`, `Guidance`, `Agents`, `Budget`, `Time`, `Advanced`) | `step` | yes | – | questions.py:337, 348, 369, 425, 437, 646 |
| the summary | `say(summary())` | yes, before each "Start the run?" | yes, once | flow.py:218 / 184 |
| the rerun command | `say(rerun())` | yes, before each "Start the run?" | **never** | flow.py:219 |
| an `OnboardingError` | `block(title, body)` | yes | – | flow.py:188-190 |

`say` is called **only** at flow.py:184, 218 and 219. So a `Terminal` that records its `say` texts holds exactly the summaries and the rerun commands. The last two `say` texts before `act(START_QUESTION, …)` returns `START` are the final summary and rerun command. Each later "Change an answer" says them again (flow.py:217-230).

### 2.4 The functions that make the summary and the rerun command

Both are closures inside `prepare_start` (flow.py:161-174). They are not returned.

```
summary() = "\n" + summary_text(summary_lines(selection, answers, keys.baseline, defaults))
rerun()   = f"\n{RERUN_HEADING}\n{rerun_command(prog, argv, option_dests=option_dests,
                                                selection=selection, answers=answers,
                                                indent='  ')}\n"
```

| Function | Where | Inputs it reads |
|---|---|---|
| `summary_lines(selection, answers, baseline, defaults) -> list[tuple[str, str]]` | summary.py:132-157 | `selection.project.name/.id`, `selection.task.name/.id`, `baseline.name/.model/.provider`, every answer, `defaults` |
| `summary_text(lines) -> str` | summary.py:160-162 | `SUMMARY_HEADING = "Ready to start"` (63), rows `f"  {label:<12} {text}"` |
| `rerun_command(prog, argv, *, option_dests, selection, answers, indent="") -> str` | summary.py:165-211 | `selection.project.id`, `selection.task.id`, `selection.project_path`; `answers`; `argv`; `option_dests` |
| `RERUN_HEADING` | summary.py:64 | `"To run this again with the same answers and no questions:"` |
| `parser_defaults(option_dests)` | summary.py:117-120 | – |

**Can the closing block rebuild them after the fact from the final `args`?**

- **The rerun command: yes, in effect.** It reads only ids, the folder and the answers. `args` has all of them, and `Answers.from_args(args)` (questions.py:176-178) gives the answers. But `rerun_command` takes a `Selection` (choose.py:57-64), so a caller must build one with placeholder names, plus `PROG`, `raw_argv` and `parser.option_dests`, which `main` has.
- **The summary: no.** It needs the project and task names and the baseline run config. None of them is in `args`.
- So the start part (functional spec 10.1) needs one of: the new `Terminal` records the last `say` texts; or `prepare_start` hands out `selection`, `keys.baseline` and the two texts. The app's title bar also needs the names (functional spec 12: "the task and project names"), and they also exist only in `selection`.
- **Plain start in the app (`--no-interaction` in a TTY):** today it prints the summary only, with no rerun command (flow.py:183-184). Spec 10.1 says "the summary and the rerun command, as the guided start prints them". This case has no rerun command today.

### 2.5 Exits from `prepare_start`

| Event | What leaves `prepare_start` |
|---|---|
| `x` at any menu, Ctrl-D at a prompt | `OnboardingStopped(1)` (terminal.py:204-205; flow.py:223-224; console.py:243-244) |
| Ctrl-C at a prompt | `OnboardingStopped(130)` (console.py:241-242) |
| Ctrl-C during a Kiln read | `KeyboardInterrupt` out of `asyncio.run`, turned into `OnboardingStopped(130)` (flow.py:192-195) |
| a problem with no one to ask, an unknown id, a bad path, a failed demo copy | `OnboardingError`, with `shown = True` when guided (flow.py:187-191) |
| a `KilnError` or `SetupPreconditionError` during a read | passes through untouched; `main` shows it with `SHOWN_ERRORS` |

**Spec and code disagree on one exit code.** Functional spec section 4 gives exit 0 when "the person declined 'Start the run?'". The code gives 1: `x) Exit` at "Start the run?" raises `OnboardingStopped(1)` (flow.py:223-224), and `test_exit_at_the_start_prompt_stops_with_1` (test_onboarding_flow.py:316-321) pins it.

---

## 3. The run hand-off and the exits

### 3.1 `main` (cli.py:1433-1483)

1. Resets `_RUN_PROGRESS = RunProgress()` (1434-1435).
2. `parse_args`, `validate_args` (1438-1439). Argument errors exit 2 here.
3. `args = prepare_start(...)` (1441-1447).
4. `result = asyncio.run(_run_with_signals(args))` (1448). One loop for the whole run, on the main thread.
5. The `except` arms and the finish line:

| Arm | Prints | Stream, form | Exit |
|---|---|---|---|
| `OnboardingStopped` | `harness-tuner: stopped.` | stderr, plain `print` | `stop.exit_code` (1 or 130) |
| `OnboardingError`, `shown` | `show_closing_line(PROG)`: `harness-tuner stopped with an error (exit 1)` | stderr; dim on a TTY | 1 |
| `OnboardingError`, not shown | `show_error(title, body, ending_lines(_RUN_PROGRESS), prog=PROG)` | stderr; rich or plain by stderr's TTY | 1 |
| `asyncio.CancelledError` (SIGTERM/SIGHUP) | `harness-tuner: interrupted (<SIGNAME>) — children killed, lock released`, then `_print_run_folder()` | stderr, plain | `128 + signum`; signum is `_stopped_by[0]`, else **SIGTERM** (1460) |
| `KeyboardInterrupt` (Ctrl-C mid-run) | `_print_run_folder()`, then **re-raise**: a traceback | stderr | 130 (Python's own) |
| `SHOWN_ERRORS` (1400-1413) | `show_error(title_for(exc), error_body(exc, host=args.kiln_host), ending_lines(_RUN_PROGRESS), prog=PROG)` | stderr | 1 |
| success | `harness-tuner: run finished — report: <result>` | **stdout**, plain `print` (1482) | 0 |

Not caught: `SystemExit(f"--agent-cli: {exc}")` (1179) and `SystemExit(f"{PROG}: {exc}")` for `InstallError` (1192). Python prints the text to stderr and exits 1. Any other exception is a traceback.

`_print_run_folder()` (1426-1430): `harness-tuner: run folder: <run_dir>` to stderr, only when `_RUN_PROGRESS.run_dir` is set.

### 3.2 `_run_with_signals` (cli.py:1331-1368)

`STOP_SIGNALS = (SIGTERM, SIGHUP)` (1331), `_stopped_by: list[int]` (1332). It installs `loop.add_signal_handler(sig, on_stop, sig)` for each, inside `contextlib.suppress(NotImplementedError, RuntimeError, ValueError)` (1358-1362). `on_stop` records the signal, removes both handlers (one shot), and cancels the main task (1347-1356).

**Off the main thread this installs nothing, silently.** `add_signal_handler` raises `RuntimeError: set_wakeup_fd only works in main thread of the main interpreter` (checked on Python 3.13), and the `suppress` swallows it. `tests/test_cli.py:1532-1533` says so: "Signal handlers install only on the main thread's loop". Textual 8.2.8 handles SIGTSTP, SIGCONT, SIGTTOU, SIGTTIN and SIGWINCH, and not SIGTERM or SIGHUP (read from `textual/drivers/linux_driver.py`). So with the run on a worker thread, a SIGTERM kills the process at once: no teardown, the lock's `finally` does not run, and the agent children (in their own sessions) keep running. The main thread must install the handlers and forward them with `run_loop.call_soon_threadsafe(...)`, and keep `_stopped_by` filled for the 143/129 codes.

### 3.3 `_run` (cli.py:1175-1322): what reaches the terminal, in order

| Step | Line | Output |
|---|---|---|
| `parse_agent_cli` | 1176-1179 | `SystemExit` text on failure |
| state folder | 1183-1186 | `StateHomeError` |
| `install_for_run` | 1189-1192 | `SystemExit` text on failure |
| `check_agent_cli` | 1198 | **nothing** on success, up to 3 minutes (`CLI_CHECK_TIMEOUT_SECS = 3 * 60`, claude_cli.py:174). Builds `AsyncioSubprocessRunner()` (claude_cli.py:752), whose `__init__` logs a WARNING when `ANTHROPIC_API_KEY` is withheld (claude_cli.py:322-339). With no handler on the root logger yet, `logging.lastResort` prints it to stderr. |
| `_RUN_PROGRESS.cli_checked = True` | 1199 | – |
| lock, run id, run dir | 1201-1208 | `_RUN_PROGRESS.run_dir = run_dir` |
| **the run line** | 1210 | `print(f"{PROG}: run {run_id} — files in {run_dir}/", flush=True)` to stdout. The comment: "Before the live display, which owns the terminal from then on." |
| `configure_run_logging(run_dir)` | 1215 | from here, logging goes to the file only |
| `build_ctx` | 1261-1289 | `_RUN_PROGRESS.spend = ctx.spend` (1290) |
| tracker | 1293-1299 | `RunStatusTracker(project_id, task_id, guidance, clock=ctx.clock, agent_cli=kind)`. A local; nothing outside `_run` can reach it. |
| **the display seam** | 1300-1306 | `console = Console()`; `LiveUi(tracker, console=console) if not args.no_progress and console.is_terminal else PlainUi(tracker, console=console)` |
| `with WaveLog(...) as wave_log, ui:` | 1307 | `LiveUi.__enter__` starts `rich.live.Live` |
| fan-out | 1310 | `FanoutLog(wave_log, tracker, ui, ctx.spend)`: in order, and with no `try` (wave_log.py:67-75). An exception in `ui.event` skips the spend meter. |
| report writer | 1314-1319 | `_RUN_PROGRESS.report_path = report_writer.report_path` |
| `orchestrate` | 1320 | returns the report path |

The app's run screen plugs in at 1300-1306 (an `EventLog` with `event(kind, **fields)`, and the context manager protocol). It also needs the `tracker`, which `_run` must hand out. The agent CLI check (1198) happens before the tracker and the UI exist, so a spinner for it (functional spec 4.3) needs a new signal out of `_run`.

### 3.4 The error screen (`onboarding/error_screen.py`)

| Name | Lines | What it does |
|---|---|---|
| `INDENT` | 22 | `"  "` |
| `Unbroken(str)` | 25-28 | a line never wrapped by the screen (a request, a path) |
| `RunProgress` | 38-47 | dataclass: `cli_checked: bool = False`, `run_dir: Path \| None = None`, `spend: SpendTotal \| None = None`, `report_path: Path \| None = None` |
| `closing_line(prog)` | 50-51 | `f"{prog} stopped with an error (exit 1)"` |
| `error_body(exc, *, host)` | 54-65 | `KilnAPIError`: `Unbroken(f"{method} {url_path}")`, `f"HTTP {status_code}: {kiln_message}"`. `KilnTransportError`: its lines, then `f"Is Kiln still running at {host}?"`. Else `str(exc).splitlines()`. Pure. |
| `ending_lines(progress)` | 68-86 | no run dir and no CLI check: `"Nothing was spent and no run folder was created."`; CLI check only: `"The only spend was the agent CLI's one test prompt. No run folder was created."`; a run dir: `f"The run stopped. Spent ${total:.2f} so far (agents and the task's model calls)."`, `Unbroken(f"Run folder: {run_dir}")`, and `Unbroken(f"Report: {report_path}")` **if the file exists at call time** |
| `show_error(title, body, ending, *, prog, stream=None)` | 101-135 | `stream` defaults to `sys.stderr`. Not a TTY: `f"{prog}: error: {title}"`, then each body and ending line (split on line breaks) as `f"  {part}"` (blank stays blank), then the closing line; one `write` and a `flush`. A TTY: a blank line, `✗ <title>` bold red, a blank line, the body wrapped inside the indent, a blank line, the ending, a blank line, the closing line dim. |
| `show_closing_line(prog, *, stream=None)` | 138-146 | the closing line alone; dim on a TTY |
| `title_for(exc)` | cli.py:1416-1423 | `exc.title` for an `OnboardingError`, else the first `ERROR_TITLES` match (cli.py:1375-1395), else `"The run stopped"` |

The plain form is built inline in `show_error` (112-119). The only way to get it on a terminal is to pass a stream whose `isatty()` is false, such as `io.StringIO()`. A `plain_error_text(...)` function would make the closing block's error part (spec 10.3) the same code.

### 3.5 What `PlainUi` and `LiveUi` print at the end (`ui.py`)

| Event | `PlainUi` (877-992) | `LiveUi` (734-874) |
|---|---|---|
| `exit` | `f"exit: {reason}{self._totals_suffix()}"` (989-990), where `_totals_suffix()` is `f" — total {totals_text(self._tracker.snapshot())}"` (914-918). Printed as `[HH:MM:SS] <text>` (910-912). | `f"{RULE}\n exit: {reason}\n TOTAL  {totals_text(status)}"` after flushing every wave (859-872) |
| `report_written` | `f"report: {path}"` (991-992) | `f" report: {path}"` (873-874) |

`totals_text(status)` (362-383): `f"{_fmt_usd(spend_total_usd)}{spend_split_text(status)} · {elapsed} elapsed"`, plus `f" · {left} left"` only when `time_remaining_known`. `spend_split_text` (394-417): `""` while nothing is spent, else `" (<agent lane> $x[ + Nk tokens unpriced] · Kiln $y[ · other $z])"`. Both are pure.

### 3.6 The texts the closing block's run part (spec 10.2) can reuse

| Part | Existing text | Where | Callable after the run? |
|---|---|---|---|
| exit line and totals | PlainUi's exit line | ui.py:989-990, 914-918 (inline, and `_totals_suffix` is private) | **No. Only at the `exit` event, on the loop thread.** `snapshot()` recomputes `elapsed_secs = clock() - run_started` at each call (status.py:203), so it keeps ticking after exit. The report writer's agent cost is a `spend` event after `exit` (ui.py:372-378, "Known exclusion"), which a later snapshot would add. The sink must build the line inside its `event("exit")` call. |
| totals alone | `totals_text(status)` | ui.py:362-383 | yes, but with the numbers of the moment it is called |
| exit reason | `fields["reason"]` of `exit`; also `RunStatus.exit_reason` (tracker `_on_exit`, status.py:624-625) | | yes; it does not change after exit |
| Best Confirmed | **no text exists.** PlainUi prints only `f"promotion: {winner or 'no winner'}"` per gate (968-973). The data is `snapshot().baseline_history` (status.py:329, 443, 622; schemas.py:2005). | | yes; nothing appends after exit |
| report path | `result`, the return of `orchestrate`; `report_written`'s `path`; `_RUN_PROGRESS.report_path` (set before the run; exists only once written) | cli.py:1448, 1482; report.py:931, 991 | yes. "No report" is the stop case: `orchestrate` writes none on cancel (orchestrator.py:513-517). |
| run folder | `f"{PROG}: run {run_id} — files in {run_dir}/"`; `_print_run_folder()`; `Unbroken(f"Run folder: {run_dir}")` | cli.py:1210 (inline), 1426-1430, error_screen.py:82 | yes, from `_RUN_PROGRESS.run_dir`, a module global |
| finish line | `f"{PROG}: run finished — report: {result}"` | cli.py:1482 (inline) | yes |
| interrupted line | `f"{PROG}: interrupted ({name}) — children killed, lock released"` | cli.py:1461-1465 (inline) | yes |

**"Stop the run" needs a new exit arm.** The natural cancel, `run_loop.call_soon_threadsafe(task.cancel)`, ends `asyncio.run` with `CancelledError`, not `KeyboardInterrupt`. `main`'s `CancelledError` arm then prints `interrupted (SIGTERM)` and returns 143, because `_stopped_by` is empty (cli.py:1460). The spec wants "Stopped by you", exit 130 and no traceback (functional spec 9).

---

## 4. Every other way the run reaches the terminal

### 4.1 What Textual 8.2.8 does with stray writes

Read from the 8.2.8 source in a throwaway environment, not from the repository:

- `App.run` runs `asyncio.run(run_app())`. While it processes messages it wraps them in `redirect_stdout(self._capture_stdout)` and `redirect_stderr(self._capture_stderr)`. That replaces `sys.stdout` and `sys.stderr` for **every thread** in the process.
- `_PrintCapture.isatty()` returns **`True`** and `fileno()` returns `-1`.
- `App._print` sends captured text only to devtools, to widgets that called `begin_capture_print`, or (headless only) to the original streams. **Otherwise the text is dropped.** It does not reach the screen, the scrollback or the run log.
- The Linux driver clears `ISIG` (unless `TEXTUAL_ALLOW_SIGNALS` is set), so Ctrl-C and Ctrl-Z are keys, not signals.

So a Python-level write does not tear the screen, but it is lost, and functional spec 11 wants stray output in the run log. A file-descriptor-level write (a child process that inherits fd 1 or 2) is not captured and does tear the screen. And any TTY test made while the app runs reads `True` for stdout and stderr.

### 4.2 The writers

| Writer | Where | Stream | When | With the app |
|---|---|---|---|---|
| the run line | cli.py:1210 | stdout, `print` | once the run dir exists | dropped. The plain path must keep it: `test_cli.py:630` and `tests/packaging/smoke_installed_wheel.py:415` assert it. |
| `logging.lastResort` | the root logger has no handler until cli.py:1215 | stderr, WARNING and up | the start-up thread and `check_agent_cli`: notably the `claude -p env: withholding …` warning (claude_cli.py:322-339) when `ANTHROPIC_API_KEY` is set; asyncio's "never retrieved" errors | dropped, and no run log exists yet |
| `configure_run_logging` | logbook.py:72-104 | a `FileHandler` on the root logger, append; httpx set to WARNING | from cli.py:1215 | safe. It adds nothing to stderr (logbook.py:15-17). |
| `warnings.warn` | nothing in `src/` calls it, and nothing calls `logging.captureWarnings` | stderr via `warnings.showwarning` | any library warning | dropped, never in the run log |
| `Console()` for the display | cli.py:1300; ui.py:747, 896 | stdout | the run | built while the app runs, `console.is_terminal` reads **True** from the capture, so the cli.py:1304 test would pick `LiveUi` |
| `RichTerminal` console, `PlainTerminal` `print` | console.py:171, 356-362 | stdout; stderr for plain `warn` | the start-up | replaced by the new `Terminal` |
| the error screen | error_screen.py:111, 141 | stderr by default | after the run | while the app runs, `sys.stderr.isatty()` is True, so the rich form would be chosen and dropped. Call it after the app has exited, or with an explicit stream. |
| `main`'s lines | cli.py:1430, 1450, 1461-1465, 1482 | stderr; stdout for 1482 | after `asyncio.run` | print them after the app exits |
| `SystemExit` texts | cli.py:1179, 1192 | stderr, by the interpreter | in `_run` | **on a worker thread `threading` swallows `SystemExit` silently** (checked). The thread wrapper must catch `BaseException` and hand it to `main`. |
| agent CLIs | claude_cli.py:349-359 | stdin, stdout and stderr all `PIPE`; `start_new_session=True` | each dispatch, the CLI check included | safe; Codex and OpenCode share this runner |
| `opencode export` | opencode_cli.py:350-357 | `capture_output=True` (stdout and stderr piped), **stdin inherited**, no new session | after each OpenCode session, blocking up to 60 s | output safe. It shares the terminal's stdin and process group; it is not known to read stdin. |
| the MCP servers | children of the agent CLIs, over stdio | – | – | safe |
| `webbrowser.open` | console.py:338 | the browser launcher (`xdg-open`, `gio`, `osascript`, …) is a `subprocess.Popen(cmdline)` or `os.popen` with **stdout and stderr inherited** (CPython `webbrowser`) | "Open a page" on a graphical system | **can tear the screen**: anything the launcher prints goes straight to the terminal. Redirect it, or launch the browser with `stdout`/`stderr` set to `DEVNULL`. |
| `check_agent_cli` | cli.py:168-186 | no output; raises `PreflightError` (shown by `main`); `logger.info` on success (claude_cli.py:1035) | before the run dir | the only stray is the runner's WARNING above |
| `print` elsewhere in `src/` | gateway/hook.py:78, 84; skill_installer/mcp_server.py:98; install_agents.py:351-353 | – | other processes or other console scripts | not in the run's process |

### 4.3 Other thread facts for the bridge

| Fact | Where | Effect |
|---|---|---|
| The tracker is thread-safe | status.py:184-194, 198-224 | a `threading.Lock` around every fold and every snapshot. The app can call `snapshot()` from the main thread. |
| `FanoutLog` calls each log in order, with no `try` | wave_log.py:67-75; cli.py:1310 | the app's sink runs on the loop thread, synchronously, and must never raise, or the spend meter is skipped |
| The loop blocks its own thread | opencode_cli.py:350-357 (60 s); trace copies in claude_cli.py:1490-1522 | the app on its own thread keeps ticking |
| Ctrl-C during a `busy` | flow.py:192-195 relies on SIGINT raising `KeyboardInterrupt` inside `asyncio.run` | on a worker thread no signal reaches the Kiln read. The app must stop at once without it (exit 130). If it abandons the thread, the demo copy's cleanup (`except BaseException: rmtree`, demo.py:178-182) does not run. Kiln reads time out at 60 s by default (kiln.py:125), the ping at 5 s (kiln_check.py:22). |

---

## 5. Removal inventory

This assumes the app replaces `RichTerminal` and `LiveUi` for every TTY case, and `PlainTerminal` and `PlainUi` stay.

### 5.1 `src/kiln_harness_tuner/onboarding/console.py`

| Symbol | Lines | After the change |
|---|---|---|
| `RichTerminal` | 155-340 | dead |
| `_key_name`, `_exit_with`, `_stop_on_ctrl_d`, `_inquirer_control`, `_pick_bindings` | 81-152 | dead. `_key_name`'s rule (`Enter)` for `ENTER`) moves to the new menu. |
| `WELCOME_MAX_WIDTH` | 45 | dead |
| `KILN_BLUE` | 44 | dead here; the same `#415CF5` is the app's brand blue (functional spec 8) |
| `PICK_HINT` | 48 | the string must survive (functional spec 5); move it |
| `SYMBOLS` | 49-53 | dead here; the glyphs `✓ ✗ !` survive in the step line (ui_design 4.7) |
| `aligned_rows`, `COLUMN_GAP` | 65-78, 47 | needed by the new picker ("Rows show aligned columns"); `test_pick_rows_are_aligned_across_options` pins them |
| `graphical_browser_expected` | 56-62 | needed by the new `open_url` (the rule is kept) |
| `LABEL_WIDTH` | 46 | **still needed** by `PlainTerminal.step` (356) |
| `PlainTerminal` | 343-397 | **kept** |
| `default_terminal`, `_is_tty` | 400-409 | change: `PlainTerminal` for no TTY, the app's `Terminal` otherwise. `test_onboarding_kiln_check.py:348-362` calls `prepare_start` with no `terminal` under capsys and expects `PlainTerminal`. |
| imports | 14, 15, 19-30 | `questionary`, `prompt_toolkit.*`, `questionary.prompts.common.InquirerControl`, `string`, and `rich.box/console/panel/table/text` become dead. `rich.cells.cell_len` stays with `aligned_rows`. `webbrowser`, `os`, `sys` stay with `open_url` and `PlainTerminal`. |
| docstrings | console.py:1-7; flow.py:1-10, 27 | "writes with rich and asks with questionary", "each prompt runs its own prompt_toolkit loop" become wrong |

### 5.2 `src/kiln_harness_tuner/ui.py`

| Symbol | Lines | Used by |
|---|---|---|
| `LiveUi`, `_ActiveView` | 722-874 | – |
| `style_line`, `style_block`, `wrap_line`, `wrap_block`, `_hanging_indent`, `_break_offsets`, `_RULE_MAX`, `_ITEM_ID`, `_DURATION`, `_SPINNER_GLYPH`, `_BAR`, `_WARNING`, `_FAILURE`, `_SOFT_FAILURE`, `_ITEM_ROW`, `_FINDING_COLUMN` | 593-719 | LiveUi only |
| `spinner_frame`, `_SPINNER_FRAMES`, `_SPINNER_FPS` | 69-73, 192-194 | LiveUi only |
| `render_header`, `render_wave_plan`, `render_wave_items`, `render_wave`, `_banners`, `render_active_wave`, `render_phase_line`, `render_totals_row`, `item_lines`, `item_line`, `progress_bar`, `RULE`, `_FINDING_GUTTER`, `_LABEL_WIDTH`, `_SPINNER` | 60-79, 197-359, 420-423 | LiveUi only (`render_wave` has no production caller at all, 322-326). Dead unless the app reuses them. |
| imports `re`, `time`, `Group`, `Live`, `Text` | 36, 37, 42-44 | LiveUi and its styling only |
| **kept** for `PlainUi` and report.py | | `outcome_cell` (also report.py:33), `change_lines` (and `_DETAIL_INDENT`, `_KIND_WIDTH`), `totals_text`, `spend_split_text`, `_fmt_tokens`, `_fmt_duration`, `_fmt_usd`, `_clip`, `baseline_found_line`, `baseline_seeded_line`, `setup_line`, `SETUP_LINE_EVENTS`, `own_line`, `janitor_line`, `phase_done_line`, `_PHASE_NOUNS`, `_count_of`, `retry_line`, `memory_write_failed_line`, `_ERROR_WIDTH`, `_FINDING_WIDTH`, `LIVE_STATE_TEXT`, `Console` |
| docstrings and comments | ui.py:1-32, 593-596; status.py:184-185; logbook.py:15-17; cli.py:4-11, 1209, 1213-1214, 1301 | name `rich.live` or the live display |

### 5.3 `cli.py`

`from kiln_harness_tuner.ui import LiveUi, PlainUi` (121) and the choice at 1300-1306 change. `from rich.console import Console` (31) stays for `PlainUi`. `_run`'s "Before the live display" print (1209-1210) changes for the app path only.

### 5.4 Dependencies

| Package | Today | After |
|---|---|---|
| `questionary>=2.1.1,<2.2` | pyproject.toml:36, with the comment at 28-35 on the internals `console.py` reads | nothing in `src/` uses it once `RichTerminal` goes. The plain path does not need it. `uv.lock`:1420, 1440, 3341-3347. |
| `prompt_toolkit` | imported directly (console.py:20-23) and by tests (test_onboarding_console.py:11-12), but **not declared**; it comes only through questionary (uv.lock:3345). Locked 3.0.53. | unused |
| `rich>=13.7` | pyproject.toml:37 | still needed: `PlainUi` (ui.py:42), cli.py:31, the error screen (error_screen.py:17-18) |
| `textual` | not a dependency, not in `uv.lock` | added (functional spec 13) |

### 5.5 Tests that cover the removed code

| File | What | Fate |
|---|---|---|
| tests/test_onboarding_console.py (493 lines) | every `RichTerminal` test, through `create_pipe_input`/`DummyOutput` (11-12, 58-76): pick 82-150, act 167-206 (206 monkeypatches `questionary.select`), text 212-230, Ctrl-C/D 235-250, output 256-376, `open_url` 404-424 | the behaviour tests port to the new `Terminal`. Keep `aligned_rows` 152-161, `graphical_browser_expected` 382-401, `PlainTerminal` 430-456, `default_terminal` 462-493 (rewritten for the new choice). |
| tests/test_ui.py | `LiveUi` 621-688, 690-711, 761-807; both-UIs loops 714-740, 743-758; `style_line` 939-960; `wrap_line` 962-974; `spinner_frame` 925-929; `render_*`, `item_line(s)`, `progress_bar` 178-260, 261-271, 441-519, 931-937, 976-987; imports 25-48 | the `LiveUi` and styling tests go; the renderer tests go with whatever the app does not reuse; `PlainUi` 810-901 stays |
| tests/test_setup_speed.py:219-235 | loops over `(PlainUi, LiveUi)` | drop `LiveUi` |
| tests/test_detectability.py:409-434 | loops over `(PlainUi, LiveUi)` | drop `LiveUi` |
| tests/test_cli.py:638-667 | monkeypatches `cli.LiveUi`, `cli.PlainUi` and `cli.Console` (a zero-argument callable) to check the display choice | rewrite for the new choice |

### 5.6 Docs that describe the old interface

| Doc | Lines | What |
|---|---|---|
| docs/install.md | 248-249, 264-265, 286-287, 483-484, 544-545, 766-767 | questionary-shaped renders: `? What next?`, ` » r) Check again …`, `? Start the run?` |
| docs/install.md | 612-708 | "Guided or not": the welcome box, the spinner, "The menu is erased after the choice", Ctrl-C/Ctrl-D, the stdout/stderr split (689-694), the flags table "live progress display" (699-708) |
| docs/install.md | 834-850 | "before the live display starts", the run line and the finish line |
| docs/your-own-task.md | 28-29, 385-450, 473-474, 559-560, 585-638 | the welcome box render, the picker render with `↑↓ move · type to filter · Enter pick` and `»`, "After the pick the list is erased", the summary and the start prompt render, "The command to run it again is printed here and nowhere else" (627) |
| docs/your-own-task.md | 669-682 | stopping: Ctrl-C, SIGTERM, SIGHUP. Also says "the report is written" on a signal; the code writes none (orchestrator.py:513-517). |
| docs/your-own-task.md | 692, 708 | the `--no-interaction` and `--no-progress` flag rows: "the live progress display" |
| docs/architecture.md | 24 | `rich live display ────► the person running it` |
| docs/architecture.md | 113-114, 850-857, 879-885 | "printed before the live display starts", "The live display (`ui.py`, built on `rich.live`) … freezes into scrollback", the terminal lines. 884-885 also names a `harness-tuner: report: <path>` line that the code does not print. |
| docs/costs.md:20, docs/agents.md:205 | | "the live display" in passing |
| README.md | – | nothing about the terminal interface |

---

## 6. Tests that pin the current behaviour

### 6.1 At the protocol level (they keep working with any `Terminal`)

All of them drive the flow through `ScriptedTerminal` and a fake Kiln, so they pin the strings, the order and the keys without a real terminal.

| File | Uses of `ScriptedTerminal(` | Pins |
|---|---|---|
| tests/test_onboarding_flow.py | 43 | the whole transcript in order (150-242: every `welcome`, `busy`, `step`, `block`, `act`, `pick`, `text`, `say`), the exact summary and rerun texts (220-241, 68-79), the menus' keys (242), the rerun round trip (245-256), Exit at "Start the run?" = 1 (316-321), Ctrl-C = 130 (631-641), the block only when guided and `shown` (807-814), the demo |
| tests/test_onboarding_choose.py | 39 | pickers, the only-one notes, the not-guided listings |
| tests/test_onboarding_questions.py | 24 | each question's text, hint, default, validator message and step line |
| tests/test_onboarding_kiln_check.py | 13 | the Kiln check's blocks and menus; 348-362 the default terminal without a TTY, through capsys |
| tests/test_onboarding_terminal.py | 10 | `resolve_problem`: retry, Open, extra actions, Exit/Ctrl-D = 1, Ctrl-C = 130, warnings' order, the plain error and its links |
| tests/test_onboarding_demo.py | 2 | the demo's spinners and step |
| tests/test_onboarding_summary.py | – | `summary_lines`, `summary_text`, `rerun_command` directly (94-292) |
| tests/test_onboarding_keys.py, tests/test_onboarding_readiness.py | – | the problems' titles, bodies and actions |

### 6.2 At the widget level (they pin `RichTerminal`)

tests/test_onboarding_console.py is the list a new `Terminal` must pass again, as Textual pilot tests: arrows (82-83); filter on a column, IDs too (86-88); skip a disabled row (105-107); Enter never picks a disabled row the filter leaves (110-111); the filter puts the cursor on its first enabled row, and Backspace re-filters (114-120); arrows skip disabled rows of the filtered list (123-133); Enter does nothing while the filter matches nothing (136-139); start on the default, or on the first enabled row for a disabled or unknown default (142-149); act by key with no Enter (167-169), by arrows and Enter (172-173), an unknown key is ignored (176-177), `Enter)` by Enter and others by key (186-189), labels `"Enter) Keep these"`, `"r) Check again"` (192-206); text default with Enter (212-213), edit after the default (216-217), a refused answer keeps the text (220-230); Ctrl-C → 130 and Ctrl-D → 1 at every prompt (235-250); step line format `"✓ Kiln      1.2.0 at …"` (256-269); names print as typed, markup and emoji codes too (349-355); `NO_COLOR` (313-330, 358-368); `open_url`'s two texts (404-424).

### 6.3 Around `main`

| Test | Pins |
|---|---|
| test_cli.py:81-88 | an autouse fixture swaps `prepare_start` for `lambda args, _argv, **_kwargs: args` |
| test_cli.py:345-373 | `fake_prepare_start(args, argv, *, prog, option_dests, policy_loader)`: **strict keywords.** If `main` passes `terminal=` or `runner=`, this test fails with a `TypeError`. It also pins that `_run` gets the object `prepare_start` returned (371). |
| test_cli.py:403-436 | the onboarding error forms: `harness-tuner: error: <title>`, `NOTHING_SPENT`, `CLOSING`; the closing line alone when `shown` |
| test_cli.py:591-601 | `harness-tuner: stopped.` and the stop code |
| test_cli.py:604-635 | stdout **starts with** the run line, and has the finish line |
| test_cli.py:1254-1290 | the error screen's ending after a run folder |
| test_cli.py:1292-1323 | `interrupted (SIGTERM)` and the run-folder line; Ctrl-C re-raises `KeyboardInterrupt` |
| test_cli.py:1507-1563 | SIGTERM → 143, SIGHUP → 129, teardown runs, on the main thread |
| test_error_screen.py:88-320 | `error_body`, `ending_lines`, both forms of `show_error` and `show_closing_line` |
| test_ui.py:810-901 | every `PlainUi` line, the exit line `exit: tripwire — total $1.50 (Claude Code $1.50 · Kiln $0.00) · 41m23s elapsed · 2h18m left` included (893-898) |
| tests/packaging/smoke_installed_wheel.py:390-428 | a piped `--no-progress` run: `files in <runs>/` on stdout, and `Kiln returned an error` and `  Run folder: ` on stderr |

### 6.4 How `tests/test_docs.py` ties flags to docs

`test_every_cli_flag_is_documented_exactly_once` (195-232) compares two sets. `parser_flags()` (141-146) is every `--` option of `cli.build_parser()._actions`, minus `--help`. `flag_entries()` (149-157) is every line of `docs/**/*.md` (not `README.md`) that matches `FLAG_ENTRY_RE = r"^\|\s*`(--[A-Za-z0-9][A-Za-z0-9_.-]*)`\s*\|"` (73): a table row whose first cell is the flag alone in backticks. A flag with no row, a row with no flag, or a flag with two rows fails. `--no-interaction` and `--no-progress` have their rows at docs/your-own-task.md:692 and 708. A new flag needs one new row under `docs/`; a row that names the old live display needs only new words.
