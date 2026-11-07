---
status: complete
---

# Phase 6: The terminal layer and the messages (guided start, part 1)

## Overview

Phases 1–5 talk through `StdTerminal`: `print`, `input()` and a one-line `[r]etry  [o]pen …  e[x]it:` prompt. This phase replaces that layer with the guided start's terminal (FS §3, architecture §18–§19) and rewrites every start-up message to FS §4–§9. After it, a guided start shows a header, one step line per check, spinners while Kiln is called, ✗ blocks for failed checks, arrow-key pickers with a filter, and single-key action menus. A start that isn't guided prints plain text and fails fast, as before.

The check logic doesn't change: the same Kiln reads, the same rules, the same providers. The questions, the summary and the new rerun command are phase 7; the phase-2 rerun line stays where it is.

The terminal is chosen by the guided rule: both stdin and stdout are terminals, and `--no-interaction` (new in this phase) isn't given.

This closes backlog item 1: opening a page prints the address first and starts a browser only where a graphical one can be expected.

## Steps

1. **Dependency.** `questionary>=2.1.1,<2.2` in `pyproject.toml`, with a comment on the pin. Regenerate `uv.lock` with `uv lock`.

2. **`onboarding/errors.py`.** `OnboardingError(title, body=())` carries the title and the body lines, so a guided start can show it as a ✗ block. `str(error)` is the plain form: the title, then each body line indented two spaces. `shown` (False) is set by `prepare_start` once the block has been printed, so `main` doesn't print it again.

   ```python
   def plain_text(title: str, body: Sequence[str]) -> str

   class OnboardingError(Exception):
       def __init__(self, title: str, body: Sequence[str] = ()): ...
       title: str
       body: tuple[str, ...]
       shown: bool
   ```

3. **`onboarding/terminal.py`** (architecture §18, §19). It loses `StdTerminal` and `Resolution`, and holds:
   - `StepStatus = Literal["ok", "fail", "warn"]`.
   - `Option[T]` (value, label, columns, description, disabled), `ActionKind` (the ten kinds), `Action` (key, label, kind, url).
   - The `Terminal` protocol: `interactive`, `header`, `step`, `block`, `say`, `warn`, `busy`, `pick`, `act`, `text`, `open_url`.
   - `Problem(title, body, actions=(), retry_label="Check again", retry_first=True)`, with:
     - `menu(extra) -> tuple[Action, ...]`: `r`, the problem's actions, `extra`, `x` when `retry_first`. Otherwise the problem's actions, then `r`, `extra` and `x`: the no-projects and no-tasks menus of FS §5.2 lead with what to do in Kiln.
     - `error() -> OnboardingError`: the title and the body, plus `"<label>: <url>"` for each Open action whose address the body doesn't already give, because a start that isn't guided has no menu to open it from.
   - `resolve_problem(check, terminal, *, busy, extra=()) -> Action | None`:
     1. `check()` runs inside `terminal.busy(busy)`.
     2. On a problem, a start that isn't guided raises `problem.error()`.
     3. A guided start prints `terminal.block(title, body)` and asks `terminal.act("What next?", problem.menu(extra))`:
        - OPEN opens the page and asks again without re-checking;
        - RETRY runs the check again;
        - EXIT raises `OnboardingStopped(1)`;
        - any other kind is returned.
     4. It returns `None` when the check passes.
   - `tilde(path, home=None) -> str`: a path under the home folder shown as `~/…`.

4. **`onboarding/console.py`** (new):
   - `graphical_browser_expected()`: true on macOS and Windows, or when `DISPLAY` or `WAYLAND_DISPLAY` is set.
   - **`RichTerminal(prog, *, console=None, pt_input=None, pt_output=None)`**, the guided terminal (`interactive = True`).
     - **Output** goes through one `rich.console.Console(highlight=False)`, every line with `soft_wrap=True`, so a long command isn't broken by inserted newlines:
       - `header`: bold text, then a blank line.
       - `step`: `✓` green, `✗` red or `!` yellow, then the label padded to 9, then the text.
       - `block`: a blank line, the bold red `✗ title`, a blank line, each body line indented two, then a blank line.
       - `warn`: `! text`, with the `!` in yellow.
       - `say`: the text as it is.
     - **Warnings go to stdout**, beside the step lines (FS §13 puts the guided start on stdout). A second console on stderr would write past rich's live-display redirect and break the spinner's line.
     - **`busy`** is `console.status(text, spinner="dots")`.
     - **`pick`**:
       - `questionary.select(…, use_search_filter=True, use_jk_keys=False, instruction=hint or "↑↓ move · type to filter · Enter pick", erase_when_done=True)`.
       - Each row is the label and the columns, each padded to its widest cell (`rich.cells.cell_len`) and joined by three spaces.
       - The choices carry the description and the disabled reason, and no shortcut keys.
     - **`act`**:
       - `questionary.select(…, use_shortcuts=True, use_jk_keys=False, erase_when_done=True)`, with each action's key as its shortcut.
       - One eager binding per key, merged into `question.application.key_bindings`, exits with that action at once.
     - **`text`**: `questionary.text(…, default=…, instruction=hint, validate=…, erase_when_done=True)`. The validate callback's error string becomes questionary's message.
     - **Every prompt:**
       - It gets an eager Ctrl-D binding that raises `EOFError`: questionary's select swallows Ctrl-D, and its text deletes a character with it.
       - `KeyboardInterrupt` becomes `OnboardingStopped(130)`, and `EOFError` becomes `OnboardingStopped(1)`.
     - **`open_url`**: `Opening <url>`, then `webbrowser.open(url)` when `graphical_browser_expected()`. Otherwise it prints `Open this link in your browser: <url>`.
   - **`PlainTerminal(prog)`**, the start that isn't guided (`interactive = False`):
     - `header` and `busy` do nothing;
     - `step` prints `<label padded to 9> <text>` to stdout;
     - `say` prints to stdout, and `warn` prints `<prog>: text` to stderr;
     - `block`, `pick`, `act`, `text` and `open_url` raise `RuntimeError("not a guided start")`.
   - `default_terminal(prog, *, no_interaction) -> Terminal`: `RichTerminal` when stdin and stdout are terminals and `no_interaction` is false, else `PlainTerminal`.

5. **`onboarding/kiln_urls.py`.** `KILN_DOWNLOAD_URL = "https://kiln.tech/download"` (moved here from `kiln_check.py`) and `check_for_update(host)` → `<host>/settings/check_for_update`.

6. **`onboarding/kiln_check.py`** (FS §4):
   - `check_kiln(kiln, host) -> KilnCheck(problem, warning, version)`; `prog` goes, since the messages say "the tuner".
   - `kiln_passed_text(version, host)`: `1.2.0 at <host>`, or `running at <host>` with no version.
   - The not-running, not-Kiln (HTTP status or "an unexpected answer") and too-old problems carry the FS titles, bodies, retry labels and actions:
     - `d) Open the download page` on each;
     - `u) Open Kiln's update page` too when Kiln is too old.
   - The too-old body names only the missing APIs, joined as an English list.
   - The warning reads `Couldn't read Kiln's API list, so its version wasn't checked.`

7. **`onboarding/choose.py`** (FS §5):
   - `TaskRow` gains `description: str | None = None`, read from Kiln.
   - **`choose_one(rows, *, label, question, heading, flag, where, terminal)`:**
     - one row: the step line `<name> (<id>) · the only <noun> <where>`;
     - several rows and guided: `terminal.pick`, with option values the row IDs. Projects show the ID and Kiln's folder (`tilde`); tasks show the ID and the description. Then the step line `<name> (<id>)`;
     - several rows and not guided: `OnboardingError("<flag> is needed when the start isn't guided", listing)`.
   - **A project or task given by a flag** also gets its step line.
   - **Spinners:**
     - `Reading projects…`;
     - `Reading <project>'s tasks…`;
     - `Reading every project's tasks…` for the task-ID search.
   - **No projects:** a `Problem` through `resolve_problem`, whose check re-reads the list.
     - Actions: `m) Use the demo project` (USE_DEMO) and `o) Open Kiln to create a project`, with `retry_first=False`.
     - The body names `<prog> --demo-project` when the start isn't guided.
   - **No tasks:** a `Problem` whose check re-reads the tasks.
     - Its action is `o) Open Kiln to create a task`, with `retry_first=False`.
     - `p) Pick a different project` (PICK_PROJECT) is extra, only when the project was picked from a list. `select` handles it by going back to the project picker.
   - **`select`** returns `Selection | Literal[ActionKind.USE_DEMO]`. The flow makes the demo.
   - **The other errors of FS §5.3** keep their substance as titled `OnboardingError`s, with their listings as the body.

8. **`onboarding/readiness.py`** (FS §6). `readiness_problem` returns `Readiness(problem, warnings, run_configs, measurable)`:
   - `step_text`: `2 run configs · 3 evals the tuner can measure`.
   - The problem's title is `<task> isn't ready to tune yet`, with the FS body.
   - One action per line: `c) Open Run configs in Kiln`, `e) Open Evals in Kiln`. The `e` action appears once.
   - The retry label is `Check again (I've fixed it in Kiln)`.
   - The no-default-judge line names the evals as an English list, with `has` or `have`.
   - The warning reads `Kiln couldn't load 1 eval of this task, so the run can't measure it.`

9. **`onboarding/keys.py`** (FS §7):
   - `key_problem` builds the FS problem:
     - the title singular or plural;
     - `• <name>: used by …` lines;
     - `, or remove it from the model policy` for a policy-only provider;
     - `p) Open AI Providers in Kiln`;
     - the retry label `Check again (I've added the key[s])`.
   - `keys_passed_text(needs)`: the display names of the providers that take a key, in first-seen order, or `none needed (local models only)`.

10. **`onboarding/demo.py`** (FS §9):
    - `make_demo` reads the taken IDs, claims the folder and copies inside `busy("Copying the demo project…")`, then imports inside `busy("Importing it into Kiln…")`.
    - Then the step line `Demo  <~/folder>, imported as project <id>`.
    - Its three errors become titled `OnboardingError`s.

11. **`onboarding/flow.py`:**
    - `prepare_start` picks the terminal with `default_terminal(prog, no_interaction=args.no_interaction)` when none is given, then prints the header `Kiln Harness Tuner <__version__>`.
    - **Kiln check:** `resolve_problem(…, busy="Checking Kiln at <host>…")`, then `✓ Kiln`, then the warning, if any.
    - **Selection loop:**
      - `select`, or USE_DEMO → `_use_demo` (the same `make_demo` as `--demo-project`, which also sets `args.demo_project`), then select again;
      - the rerun line, unchanged;
      - readiness with `busy="Checking the task is ready…"` and the extra `t) Pick a different task` when the task was picked, then `✓ Ready`.
    - **Key check:** `busy="Checking API keys…"`, then `✓ API keys`.
    - **Errors:** an `OnboardingError` in a guided start is printed with `terminal.block` and marked `shown` before it propagates.

12. **`cli.py`:**
    - `--no-interaction` (`store_true`), with the architecture's help text.
    - In `main`, `OnboardingError` gets its own `except`, which prints nothing when `shown` and returns 1.
    - **Docs:** a `--no-interaction` row in the flag table of `docs/your-own-task.md`.

13. **`backlog.md`:** item 1 marked closed.

## Tests

- **`tests/onboarding_fakes.py`:**
  - `ScriptedTerminal(answers, interactive=True)` answers `pick` with a value, `act` with a key and `text` with a string or a callable that gets `validate`. A callable answer for `pick` or `act` is called first, so a test can change Kiln while the prompt waits.
  - It records `headers`, `steps`, `blocks`, `out`, `err`, `busy`, `opened`, `questions` and `menus` (the keys of each action menu).
  - It fails the test on:
    - a prompt in a start that isn't guided;
    - a key that isn't in the menu;
    - a value that isn't a pickable option;
    - a prompt while a spinner runs;
    - a nested spinner.
  - Running out of answers stops with exit 1, like Ctrl-D.
  - `flags()` gains `no_interaction`.
- **`tests/test_onboarding_terminal.py`:**
  - `resolve_problem`: a pass returns None with no block; a retry re-checks inside the spinner and prints the block again; Open opens and asks again without re-checking; `extra` actions are returned; Exit and running out give 1, and Ctrl-C gives 130.
  - Not guided, it raises the plain error with no block, with an Open action's address added unless the body has it.
  - `menu` order with `retry_first` true and false.
  - `Problem.error()` text.
  - `tilde`.
- **`tests/test_onboarding_console.py`** (new):
  - Through `create_pipe_input()` and `DummyOutput()`:
    - a pick by arrow keys;
    - a pick by filter text, on an ID column;
    - a disabled option skipped;
    - aligned rows;
    - an action by its key with no Enter;
    - an action by arrows and Enter;
    - a text default accepted;
    - an invalid text, then a valid one;
    - Ctrl-C → 130 and Ctrl-D → 1, for each prompt kind.
  - Step, block, header, warn and say text on a `Console(file=StringIO(), force_terminal=True, color_system=None)`. No colour codes with `NO_COLOR`. `busy` prints nothing on a console that isn't a terminal.
  - `open_url` with and without `DISPLAY`, and on macOS. `graphical_browser_expected`.
  - `PlainTerminal`:
    - its step, say and warn output;
    - header and busy print nothing;
    - every prompt, `block` and `open_url` raise.
  - `default_terminal` by tty and `--no-interaction`.
- **`tests/test_onboarding_kiln_check.py`:**
  - The new titles, bodies, retry labels and menus, with `d` and `u` opening their links.
  - The passed step line with and without a version.
  - The warning after the step line.
  - Not guided: the plain message.
  - The spinner text.
- **`tests/test_onboarding_choose.py`:**
  - The pickers' questions, options and columns, and the step lines, including the only-one suffixes and flag-given rows.
  - No projects: `m` returns USE_DEMO, `o` opens create_project, and `r` re-reads. Not guided, the plain text names `--demo-project`.
  - No tasks: `o` opens create_task; `p` is offered only after a project pick, and goes back to the project picker.
  - The FS §5.3 errors as titled errors.
  - The task description read from Kiln.
- **`tests/test_onboarding_readiness.py`:**
  - The FS title and body;
  - one action per link, and `e` once;
  - the English list of evals with no judge;
  - `step_text` counts and plurals;
  - the warning text.
- **`tests/test_onboarding_keys.py`:**
  - The FS message, singular and plural;
  - the policy suffix;
  - the `p` action and the retry label;
  - `keys_passed_text`.
- **`tests/test_onboarding_demo.py`:**
  - The step line with `~`;
  - the two spinner texts;
  - the titled errors.
- **`tests/test_onboarding_flow.py`:**
  - No flags, end to end: header, step lines in order, both pickers, the rerun line, a key retry by `r`, and the returned flags.
  - A Kiln that has no projects → `m` → the demo is made and used.
  - No tasks → `p` → the other project.
  - `t` back to the task picker.
  - Readiness and key retries.
  - A guided `OnboardingError` shown as a block and marked `shown`.
  - Not guided: no prompt, the plain errors, and the plain step lines.
  - Ctrl-C → 130.
- **`tests/test_cli.py`:**
  - `--no-interaction` parses and defaults off.
  - `main` prints nothing for a shown `OnboardingError` and still exits 1.
