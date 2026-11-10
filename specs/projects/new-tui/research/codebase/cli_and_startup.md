# harness-tuner: CLI flags, start-up flow, hand-off and exit (inventory for the Textual TUI spec)

Repo state: `main` at `2a9f72c` ("Spec: new TUI overview…"). Package: `src/kiln_harness_tuner/` (plus the vendored `src/kiln_api_mcp/`). The command is `harness-tuner` (`[project.scripts] harness-tuner = "kiln_harness_tuner.cli:main"`, pyproject.toml), and the name in every message comes from `PROG = "harness-tuner"` (cli.py:128).

Note on the spec vs the code: `specs/projects/cli-onboarding/functional_spec.md` was written when the command was `kiln-harness-tuner`. Every string below is quoted from the code, which is the truth. Where the spec and the code differ, section 8 lists it.

Verified by running: `--help`, a non-guided start with Kiln down, `--no-ui`, `--demo-project --task-id`, a guided start in a pty, rich's TTY detection, the pre-logging warning, and an uncaught Ctrl-C. I installed the locked dependencies into a scratchpad venv (`UV_PROJECT_ENVIRONMENT`, `--no-install-project`) and ran with `PYTHONDONTWRITEBYTECODE=1`. Nothing in the repo was modified.

---

## 1. Every CLI flag

The parser is `build_parser()` (cli.py:243-464). It is a custom `_Parser(argparse.ArgumentParser)` (cli.py:209-240) that:

- records each long option's dest, whether it takes a value, and its default in `option_dests` (cli.py:216-228). The start-up uses this to find which flags were given and to build the rerun command.
- refuses removed flags with a custom message before parsing (cli.py:230-240; `REMOVED_FLAGS` at cli.py:191-206).

Other parser facts:

- **No positional arguments. No subcommands.** The only short option is `-h/--help`.
- `formatter_class=RawDescriptionHelpFormatter`. The description is "Kiln Harness Tuner\n\nAutomatically improves an AI agent…" (cli.py:244-257).
- argparse's default `allow_abbrev=True` is in effect, so prefixes like `--no-prog` work. `summary._long_option` copies this rule when it reads argv (summary.py:78-86).
- Cross-flag rule (cli.py:470-478): `--demo-project` with any of `--project-id`/`--task-id`/`--project-path` calls `parser.error("--demo-project makes its own project: drop --project-id, --task-id and --project-path")`. Exit 2 (verified).

"Display?" in the table below means the flag changes what the terminal shows. Every flag that answers a start-up question also removes that question from a guided start (`given_dests`, summary.py:109-114; `changeable`, questions.py:649-652). A flag counts as given even when its value equals the default.

| Flag (cli.py line) | Type / metavar | Default | Effect | Display? |
|---|---|---|---|---|
| `-h`, `--help` | flag | – | argparse help, exit 0 | yes (help text) |
| `--project-path` (258) | str | `None` → Kiln's folder for the project | On-disk project folder. Must equal Kiln's folder (choose.py:424-449). Given alone, it finds the project (choose.py:273-284). Skips the project picker. | yes: no project picker; adds error screens |
| `--project-id` (264) | str | `None` → the only project, or picked | Project id. An unknown id is an error that lists the projects (choose.py:263-272). Skips the project picker. | yes |
| `--task-id` (269) | str | `None` → the only task, or picked | Task id. Given alone, it finds the project(s) that have it (choose.py:321-353). Skips the task picker. | yes |
| `--demo-project` (273) | store_true | False | Copies the packaged demo to `~/Kiln Projects/Forum Moderation[-N]`, imports it, and runs on it (demo.py:133-183). Not combinable with the three project flags (exit 2). | yes: "Demo" step and spinners, no pickers, pre-filled guidance |
| `--no-interaction` (280) | store_true | False | Never asks or waits: a non-guided start (console.py:404-409) | yes: plain start, no welcome box, no spinners, no questions |
| `--kiln-host` (286) | str | `DEFAULT_KILN_HOST` = `http://localhost:8757` | Kiln server. `prepare_start` strips a trailing `/` (flow.py:185). The run uses it unstripped (cli.py:547-553). | yes: in the Kiln step, the spinner, problems and links |
| `--guidance` (291) | str | `""` | Kickoff guidance for every planner. Skips the guidance question. | yes: summary; LiveUi header line ` guidance: "…"` (ui.py:254-255) |
| `--timeout-mins` (296) | int | `720` (`DEFAULT_TIMEOUT_MINS`, cli.py:130) | Monotonic deadline. Skips the time question. | yes: summary "Limits"; "left" in the live totals row |
| `--no-progress` (303) | store_true | False | `PlainUi` instead of `LiveUi` during the run (cli.py:1301-1306). Does **not** affect the start. | **yes**: the whole run display |
| `--budget-usd` (308) | `_budget` (cli.py:481-487 → `parse_budget`), metavar `USD` | `50.0` (`DEFAULT_BUDGET_USD`, cli.py:132); `none` = no cap | Cost ceiling. Skips the budget question. A bad value exits 2 with `'<text>': Enter an amount in USD, like 50, or none` | yes: summary |
| `--coverage-floor` (317) | float | `DEFAULT_COVERAGE_FLOOR` (0.95) | Promotion-gate val coverage floor. Never asked. | no |
| `--max-concurrent-evals` (324) | int | `4` (cli.py:138); 0 = unlimited | Eval jobs in flight run-wide. Skips its advanced question. | yes: Advanced text in the summary |
| `--agent-cli` (331) | str, metavar `COMMAND` | `DEFAULT_AGENT_CLI` = `'claude -p'` (dispatch/agent_cli.py:34) | Agent CLI command (`claude -p`, `codex exec`, `opencode run` + options). Skips the agent CLI **and** model questions. A test prompt runs before the run (cli.py:168-186). | yes: summary "Agents"; the lane label in the spend split |
| `--max-concurrent-agents` (343) | int | `4` (cli.py:139); 0 = unlimited | Agent sessions in flight. Skips its advanced question. (The help still says "`claude -p` sessions".) | yes: summary |
| `--agent-model` (350) | append, `ROLE=MODEL` | `[]` | Per-role model override. Validated in `_run` (cli.py:510-529), i.e. **after** the guided start. Never asked. | no (an error screen on a bad value) |
| `--agent-effort` (362) | append, `ROLE=EFFORT` | `[]` | Per-role effort override. Same validation, same timing. | no |
| `--no-baseline-audit` (370) | store_true | False | Skips the baseline audit. | indirectly (fewer run lines) |
| `--reference-check` (378) | choices `warn`/`refuse`/`off` (`REFERENCE_CHECK_MODES`) | `warn` | The build reference check | no |
| `--no-recon` (387) | store_true | False | Skips the recon battery (wave 0) | indirectly (no wave-0 block) |
| `--gate-agent` (395) | choices `auto`/`always` | `auto` | Who decides a one-candidate gate | no |
| `--max-turns` (403) | int | `None` | Turn cap per session. A warning goes to the log file only when the CLI has no equivalent (cli.py:1227-1230). | no |
| `--objective` (411) | choices `OBJECTIVES` = `quality`/`speed`/`cost` (cli.py:467) | `quality` | What to optimise. Skips the objective question. (The help describes only quality and speed.) | yes: summary; LiveUi header adds ` · objective: <x>` when not quality (ui.py:251-252) |
| `--min-effect` (420) | `_fraction` (cli.py:490-496), metavar `FRACTION` | `None` | Detectability threshold. Skips its advanced question. | yes: summary; detectability lines |
| `--allow-underpowered` (430) | store_true | False | The run goes on despite a detectability refusal | indirectly |
| `--noise-check` (437) | `_positive_int` (cli.py:499-507, ≥1), metavar `N` | `0` | Paid noise check before wave 1. Skips its advanced question. Note: the flag needs ≥1, but the question accepts 0 (`parse_count`). | yes: summary; noise lines |
| `--model-policy` (446) | str, metavar `PATH` | `None` | Model policy JSON, loaded by `load_model_policy` (cli.py:532-544). Skips its advanced question. The key check covers its providers. | yes: summary; key-check lines |
| `--skip-model-preflight` (455) | store_true | False | Skips the per-model test calls | indirectly |

**Removed flags** (exit 2 with `harness-tuner: error: <msg>` after the usage; verified for `--no-ui`):

- `--max-request-cost` → `--max-request-cost was removed: --model-policy keeps costly models out, and the cost of each arm per request is still reported`
- `--max-request-latency` → `--max-request-latency was removed: use --objective speed when latency is the goal; the latency of each arm per request is still reported`
- `--no-ui` → `--no-ui was renamed --no-progress: plain log lines instead of the live progress display`

**Flags that do not exist:** no `--quiet`, `--verbose`, `--dry-run`, `--preflight-only` or `--version`. The only verbosity knob is the env var `KILN_HARNESS_TUNER_LOG_LEVEL`, and it changes the log file only (logbook.py:33-58).

**Environment variables that change behaviour or display:**

- `KILN_HARNESS_TUNER_HOME`: the state folder (paths.py:29-77).
- `KILN_HARNESS_TUNER_LOG_LEVEL`: the file log level.
- `KILN_HARNESS_TUNER_CHILD_ENV_KEEP`: names env vars passed through to agent sessions (claude_cli.py:265).
- `NO_COLOR`, `FORCE_COLOR`, `TTY_COMPATIBLE`, `TTY_INTERACTIVE`, `COLUMNS`: rich's own rules.
- `DISPLAY`/`WAYLAND_DISPLAY`: the browser rule (console.py:56-62).

**Value parsers shared by flags and questions** (questions.py):

| Parser (line) | Accepts | Error message |
|---|---|---|
| `parse_budget` (62) | `none` (any case), or a number >0 with an optional leading `$` | `BUDGET_ERROR = "Enter an amount in USD, like 50, or none"` |
| `parse_duration` (74) | minutes, `Nm`, `Nh`, `1.5h`; rounded to the nearest minute; ≥1 | `"Enter a time like 90m or 12h"` |
| `parse_fraction` (88) | a number >0 and ≤1 | `"Enter a fraction above 0 and at most 1, like 0.1 for 10%"` |
| `parse_count` (96) | digits only | `"Enter a whole number, 0 or more"` |
| model name (279) | one word, no spaces | `MODEL_ERROR = "Enter one model name, with no spaces"` |

Other console scripts, which a TUI must not touch: `harness-tuner-install-agents` (flags `--agent-cli`, `--dest`; install_agents.py:315-355), `harness-tuner-gateway-mcp`, `harness-tuner-skill-installer-mcp` and `kiln_api_mcp`. The last three are stdio MCP servers that the agents start; their stdout is the MCP channel.

---

## 2. How the display mode is chosen today

There are **three independent TTY tests**, plus rich's own rules inside each console.

1. **Start-up: guided or not.** `default_terminal(prog, no_interaction=…)` (console.py:404-409) returns `RichTerminal` only when `sys.stdin.isatty()` **and** `sys.stdout.isatty()` **and** not `--no-interaction` (`_is_tty`, console.py:400-401). Otherwise it returns `PlainTerminal`. It is called from `prepare_start` (flow.py:183-184). The `terminal=` parameter overrides it (tests use this).
   - `RichTerminal` (console.py:155-340): rich output, questionary input, spinners via `console.status`.
   - `PlainTerminal` (console.py:343-397): plain `print` to stdout, warnings to stderr, `busy` is a `nullcontext`, and every ask method raises `RuntimeError("not a guided start")`.
2. **Run display: live or plain.** cli.py:1300-1306:
   ```
   console = Console()
   # --no-progress (and CI / non-TTY) falls back to plain log lines.
   ui = (
       LiveUi(tracker, console=console)
       if not args.no_progress and console.is_terminal
       else PlainUi(tracker, console=console)
   )
   ```
   - `Console()` writes to stdout. `console.is_terminal` is rich's test: stdout `isatty()`, but **`FORCE_COLOR` (any value) or `TTY_COMPATIBLE=1` force it True**, and `TTY_COMPATIBLE=0` forces it False. I verified this with rich 15.0.0: with stdout piped, `FORCE_COLOR=1` and `TTY_COMPATIBLE=1` both give `is_terminal=True`. So in CI with `FORCE_COLOR` set and no `--no-progress`, today's code picks `LiveUi`.
   - This test looks at **stdout only**, not stdin. `harness-tuner < /dev/null` in a terminal gives a non-guided start followed by the live display.
   - `LiveUi` (ui.py:734-874): `rich.live.Live(_ActiveView(self), console=…, refresh_per_second=10, transient=True)` (ui.py:757-762). It starts in `__enter__` (ui.py:764-766) and stops in `__exit__`. The live region is redrawn from a tracker snapshot by rich's refresh thread. Static lines go through `console.print`, which Live puts above the live region.
   - `PlainUi` (ui.py:877-992): one `[HH:MM:SS] <text>` line per event, through `Console.print(markup=False, highlight=False)`. **Not** `soft_wrap`, so on a non-TTY rich wraps each line at 80 columns (or `COLUMNS`); I verified this.
3. **Error screen.** `show_error`/`show_closing_line` (error_screen.py:312-357) use the raw `stream.isatty()` of **stderr** (`_is_terminal`, error_screen.py:304-309, which deliberately ignores `FORCE_COLOR`/`TTY_COMPATIBLE`). A TTY gets the rich ✗ screen (red bold title, dim closing line). Otherwise plain text with the first line `harness-tuner: error: <title>`.

Inside `RichTerminal`, colour follows rich's rules on stdout (`NO_COLOR`). `RichTerminal` is built with `Console(highlight=False)` (console.py:171), and every print uses `soft_wrap=True, markup=False, highlight=False, emoji=False` (console.py:178-184).

**The four combinations, as documented in docs/install.md:697-708:**

| Situation | Start | Run display |
|---|---|---|
| Terminal, no flags | guided | live |
| Terminal, `--no-interaction` | non-guided | live |
| Terminal, `--no-progress` | guided | plain |
| No terminal | non-guided | plain (unless `FORCE_COLOR`/`TTY_COMPATIBLE` say otherwise, see above) |

---

## 3. The interactive start-up flow, in order

**Entry.** `main` (cli.py:1433-1483):

1. `parser.parse_args(raw_argv)` (1438), then `validate_args` (1439).
2. `prepare_start(args, raw_argv, prog=PROG, option_dests=parser.option_dests, policy_loader=load_model_policy)` (cli.py:1441-1447; flow.py:146-236).
3. `asyncio.run(_run_with_signals(args))` (1448).

**`prepare_start` is synchronous on purpose** (flow.py:1-51, 146-236):

- Each prompt runs its own prompt_toolkit loop while no asyncio loop is running.
- Each batch of Kiln reads runs in its own short `asyncio.run` through a `KilnRunner` (`kiln_runner`, flow.py:132-143), which opens a fresh `KilnClient` for that batch.
- The reason, from the docstring: "`asyncio.run` installs its own SIGINT handler, and while the event loop's thread waits at a prompt that handler only cancels the main task: the first Ctrl-C at a prompt would do nothing visible."

**Libraries:**

- Output: **rich** (Panel, Table.grid, Text, `console.status` spinner "dots").
- Input: **questionary 2.1.1** on prompt_toolkit 3.0.53. `questionary.select` serves both pickers and action menus; `questionary.text` serves typed answers.
- Every prompt is created with `erase_when_done=True` (console.py:172), so the prompt disappears after the answer and the step line takes its place.
- Ctrl-C at any prompt raises `OnboardingStopped(130)`, and Ctrl-D raises `OnboardingStopped(1)` (console.py:230-244). Ctrl-D is an eager binding on every prompt (console.py:92-93, 234-235).
- No `input()` anywhere. No numbered lists.

**The seams** (`Terminal` protocol, terminal.py:66-118):

- `welcome`, `step`, `block`, `say`, `warn`.
- `busy(text)`: a context manager around a blocking Kiln call.
- `pick(question, options, *, hint, default)`, `act(question, actions)`, `text(question, *, default, hint, validate)`, `open_url`.
- `interactive: bool`.

**The problem loop.** Every failed check is a `Problem` (terminal.py:128-164) that goes through one loop, `resolve_problem` (terminal.py:167-197):

1. Spinner `busy`, then run the check.
2. Guided: on a problem, `block(title, body)`, then the warnings, then `_ask_what_next` (terminal.py:200-209) with the question **`What next?`** (`WHAT_NEXT`, terminal.py:24).
3. `r` re-runs the check, with its spinner. Nothing polls. If the check still fails, the block is printed again.
4. An Open action opens its page (or prints the link), then asks `What next?` again, without re-checking and without reprinting the block.
5. `x` raises `OnboardingStopped(1)`. Any other action is returned to the caller.
6. Non-guided: the first problem's warnings go to stderr, then `raise problem.error()`. `Problem.error()` (terminal.py:153-164) appends `"<label>: <url>"` for each Open action whose URL is not already in the body.

**Menu order** (`Problem.menu`, terminal.py:145-151):

- `retry_first=True`: (retry, own actions…, extra…, `x) Exit`).
- `retry_first=False`: (own actions…, retry, extra…, `x) Exit`).

`EXIT_ACTION = Action("x", "Exit", ActionKind.EXIT)` (terminal.py:125).

**How the widgets look (guided, questionary defaults).** Verified in a pty:

- **Action menu** (`act`, console.py:289-316):
  - The question has questionary's default qmark `? `.
  - `instruction=" "`.
  - Each row is titled `"<key>) <label>"`, or `"Enter) <label>"` for the `ENTER` key, which may only lead the menu (terminal.py:51-53; console.py:81-82, 293-299).
  - The pointer is `»` and starts on the first row.
  - Each letter acts at once with no Enter (eager bindings, console.py:312-316). Arrows plus Enter also work. `j`/`k` are off. A letter no action has is ignored.
  - Rendered:
    ```
    ? What next?
     » r) Check again (Kiln is open now)
       d) Open the download page
       x) Exit
    ```
- **Picker** (`pick`, console.py:246-287):
  - `questionary.select(..., use_search_filter=True, use_jk_keys=False, instruction=hint or PICK_HINT)`, where `PICK_HINT = "↑↓ move · type to filter · Enter pick"` (console.py:48). Note: when a question passes a `hint`, the hint **replaces** `PICK_HINT` as the instruction.
  - Rows are label plus aligned columns, separated by `COLUMN_GAP = "   "` (console.py:47, 65-78).
  - Typing filters on the whole row text, so IDs work.
  - A `description` shows under the highlighted row. A disabled row shows dimmed with its reason and can't be picked.
  - Custom Enter/arrow/filter bindings fix questionary 2.1 quirks (console.py:105-152). They reach into questionary's `InquirerControl` internals.
  - The cursor starts on `default` when it is enabled, else on the first enabled row.
- **Text** (`text`, console.py:318-333):
  - `questionary.text(question, default=…, instruction=hint, validate=…)`.
  - The default is pre-filled, and Enter accepts it.
  - An invalid answer shows the validator's message and keeps what was typed.
- **Spinner** (`busy`, console.py:226-228): `console.status(Text(text), spinner="dots")`.
- **Step line** (console.py:204-206): `f"{symbol} {label:<9} {text}"`, with `LABEL_WIDTH = 9`. Symbols: `✓` green, `✗` red, `!` yellow (console.py:49-53). Non-guided: `print(f"{label:<9} {text}")` with no symbol (console.py:355-356).
- **Block** (console.py:208-214): a blank line, `✗ <title>` in bold red, a blank line, each body line indented 2, then a blank line. On stdout.
- **Warn** (console.py:219-224): `! <text>` on **stdout** (guided). Non-guided: `harness-tuner: <text>` on stderr (console.py:361-362).
- **Open URL** (console.py:335-340):
  - When `graphical_browser_expected()` (macOS, Windows, or `DISPLAY`/`WAYLAND_DISPLAY` set): prints `Opening <url>`, then `webbrowser.open(url)`.
  - Otherwise prints `Open this link in your browser: <url>`.

### Step 0 – Argument errors

argparse prints usage and `harness-tuner: error: …` to stderr, exit 2. This includes the removed flags, `--demo-project` with a project flag, and type errors such as `--budget-usd foo` → `argument --budget-usd: 'foo': Enter an amount in USD, like 50, or none`.

### Step 1 – Welcome box (guided only)

`terminal.welcome(WELCOME_TITLE, __version__, WELCOME_DESCRIPTION)` (flow.py:191; constants at flow.py:117-121; rendering at console.py:186-202).

- `WELCOME_TITLE = "Kiln Harness Tuner"`, bold `#415CF5` (`KILN_BLUE`, console.py:44).
- `v0.1.0` (from `__version__`, `__init__.py:7`), dim and right-aligned on the title row.
- A blank line, then `WELCOME_DESCRIPTION = "Automatically improves an AI agent by tuning its harness: the prompt, model, tools, skills and settings around it."`
- `Panel(box=ROUNDED, padding=(0,1), width=min(console.width, 68), border_style=KILN_BLUE)`, followed by a blank line.
- `PlainTerminal.welcome` prints nothing (console.py:352-353).

### Step 2 – Kiln check

`_check_kiln` (flow.py:321-332), `check_kiln` (kiln_check.py:150-177).

- Spinner: `Checking Kiln at {host}…` (flow.py:329).
- Reads: `GET /ping` (5 s, no retries; the answer must be `"pong"`), then `GET /openapi.json` (10 s), which is checked for `REQUIRED_APIS` (kiln_check.py:44-50): the memory API, the eval-job API and run-config provenance.
- **Passed:** `✓ Kiln      <version> at <host>`, or `running at <host>` when there is no version or it is `unknown` (kiln_check.py:64-66, 91-96).
- **API list unreadable** (a warning; the start goes on): `! Couldn't read Kiln's API list, so its version wasn't checked.` (kiln_check.py:25-27).

**Not running** (kiln_check.py:99-111). Title: `Kiln isn't running at {host}`. Body:

```
The tuner works through the Kiln app, so Kiln has to be open on this computer.
  1. Install Kiln if you don't have it: https://kiln.tech/download
  2. Open the Kiln app and wait for its window to appear.
Kiln on another port? Exit and pass --kiln-host http://localhost:<port>.
```

Menu: `r) Check again (Kiln is open now)` / `d) Open the download page` (opens `https://kiln.tech/download`, kiln_urls.py:11) / `x) Exit`.

**Not Kiln** (kiln_check.py:114-124). Title: `Something other than Kiln is answering at {host}`. Body:

```
GET /ping returned {HTTP <status>|an unexpected answer}, so this isn't the Kiln app.
  1. If Kiln is running on another port, exit and pass --kiln-host with its address.
  2. Otherwise close the program using this port, then open the Kiln app.
```

Menu: `r) Check again` / `d) Open the download page` / `x) Exit`.

**Too old** (kiln_check.py:127-147). Title: `Kiln {version} is too old for the tuner`, or `This Kiln is too old for the tuner`. Body:

```
The tuner needs {english_list(missing)}, which this Kiln doesn't have.
  1. Update Kiln: in the app, open Settings → Check for update,
     or download the latest version from https://kiln.tech/download.
  2. Quit and reopen Kiln so the update takes effect.
```

Menu: `r) Check again (I've updated and reopened Kiln)` / `u) Open Kiln's update page` (`{host}/settings/check_for_update`) / `d) Open the download page` / `x) Exit`.

**Non-guided output** (verified, stderr, exit 1):

```
harness-tuner: error: Kiln isn't running at http://127.0.0.1:59999
  The tuner works through the Kiln app, so Kiln has to be open on this computer.
    1. Install Kiln if you don't have it: https://kiln.tech/download
    2. Open the Kiln app and wait for its window to appear.
  Kiln on another port? Exit and pass --kiln-host http://localhost:<port>.
  Nothing was spent and no run folder was created.
harness-tuner stopped with an error (exit 1)
```

### Step 3 – Demo copy and import (only with `--demo-project`, or the `m` action)

`_use_demo` (flow.py:274-283) and `make_demo` (demo.py:133-155).

1. Spinner `Copying the demo project…`. Inside it: `fetch_projects` (to avoid taken ids), `claim_folder(~/Kiln Projects, "Forum Moderation")` (→ `-1`, `-2`, …), and `write_copy`.
2. Spinner `Importing it into Kiln…`: `POST /api/import_project` with `trusted=true` and `project_path`.
3. Step line `✓ Demo      {~/Kiln Projects/Forum Moderation[-N]}, imported as project {id}` (demo.py:152-154).
4. `args.project_id/task_id/project_path` are set and `args.demo_project=True`. The task id is always `424242783061` (demo.py:33).

Errors are `OnboardingError`, so there is no menu:

- `Kiln didn't import the demo project` + (Kiln's `detail` or the error text, `The copy at {folder} was deleted.`) (demo.py:143-148)
- `Couldn't make a folder for the demo project in {parent}` + (str(exc)) (demo.py:165-168)
- `Couldn't copy the demo project to {folder}` + (str(exc), `The partial copy was deleted.`) (demo.py:172-177)

A Ctrl-C mid-copy deletes the folder and re-raises (demo.py:178-182).

### Step 4 – Project, task and folder

`_choose_a_ready_task` (flow.py:286-318), which calls `select` (choose.py:452-503). It loops until the readiness check passes. The `USE_DEMO` result triggers Step 3, then selects again.

**Which flags decide what** (`_choose_project`, choose.py:253-290; first match wins):

1. `--project-id` given: spinner `Reading projects…`, then find it. Error: `Kiln has no project {id}` + the listing.
2. `--project-path` given: same spinner, then match Kiln's folder. Error: `--project-path {wanted} isn't a project in Kiln` + the listing + `Import it into Kiln first.`
3. `--task-id` only: `Reading projects…`, then `Reading every project's tasks…` (one GET per project). Error: `No project in Kiln has task {id}` + the listing. Several projects have it: picker `Which project with task {task_id}?`.
4. Nothing given: `_pick_project` (choose.py:293-318) through `resolve_problem` with busy `Reading projects…`.

The listing is `"<heading>:"` followed by rows `"  <id>  <name>"` (choose.py:124-125). With no projects, the listing is instead `Kiln has no projects yet. Create one in Kiln: {host}/settings/create_project` and `Or start with the demo: harness-tuner --demo-project` (choose.py:181-187).

**Project picker** (guided, 2+ projects; `choose_one`, choose.py:142-169):

- Question: `Which project do you want to tune?` (choose.py:33).
- Hint: none, so `↑↓ move · type to filter · Enter pick` shows.
- Row: `name` + columns `(id, ~/folder)` (choose.py:133-139).
- Step after the pick: `✓ Project   {name} ({id})`.
- Exactly one project: no question, and the step is `✓ Project   {name} ({id}) · the only project in Kiln`. This applies to both kinds of start.
- Non-guided with 2+: `OnboardingError("--project-id is needed when the start isn't guided", listing)` (choose.py:162-165).
- Named by a flag: step without a note.

**No projects** (`_no_projects`, choose.py:190-213). Title: `Kiln has no projects yet`. Body:

```
The tuner works on a task in a Kiln project. Create one in Kiln, or start with
the demo: a small moderation task with its evals and data already set up.
```

Non-guided, the second line is `the demo: harness-tuner --demo-project`.

Menu (`retry_first=False`): `m) Use the demo project` (returns `USE_DEMO`) / `o) Open Kiln to create a project` (`{host}/settings/create_project`) / `r) Check again` / `x) Exit`.

**The folder** (`_project_path`, choose.py:424-449). This check runs before the task is chosen.

- Kiln has no folder: `Kiln has no folder for project {name} ({id})` + `Pass --project-path with the project's folder.`
- Mismatch: `--project-path isn't Kiln's folder for project {name}` + the lines `--project-path is {given},`, `but Kiln has project {id} at {theirs}.`, `Drop --project-path, or pass {shlex.quote(theirs)}.`

**Task** (`_choose_task`, choose.py:356-389):

- Spinner: `Reading {project.name}'s tasks…` (choose.py:392-393).
- Picker question: `Which task in {project.name}?`. Row: `name` + `(id)`, with the task's description under the highlighted row.
- Step: `✓ Task      {name} ({id})`, or `… · the only task in this project`.
- Non-guided with 2+: `--task-id is needed when the start isn't guided` + `Tasks in {name}:` listing.
- Given `--task-id` not in the project (`_given_task`, choose.py:396-421): `{project.name} has no task {id}` + the listing, or `It has no tasks yet. Create one in Kiln: {host}/settings/create_task/{project_id}`.

**No tasks** (`_no_tasks`, choose.py:216-232). Title: `{project.name} has no tasks yet`. Body:

```
The tuner works on one task of a Kiln project, and this project has none.
Create one in Kiln.
```

Menu: `o) Open Kiln to create a task` / `r) Check again` / `p) Pick a different project` (only when the project was picked from a list; choose.py:37, 376-380) / `x) Exit`.

### Step 5 – Readiness check

`_check_ready` (flow.py:335-365), `readiness_problem` (readiness.py:278-361).

- Spinner: `Checking the task is ready…`.
- Reads `run_configs`, `evals` and `specs` in parallel, then each measurable eval's default config.
- **Passed:** `✓ Ready     {N run config(s)} · {M eval(s)} the tuner can measure`, with ` ({K} left out)` when some evals can't be loaded (readiness.py:238-244). Then one warning per item.

**Warnings** (they do not fail the check):

- `! Kiln couldn't load {N eval(s)} of this task, so the run can't measure {it|them}.` (readiness.py:247-250)
- An eval Kiln can't load: a 3-line warning (setup.py:819-833; readiness.py:304-313). The link is the eval page, `{host}/specs/{p}/{t}/{spec}/{eval}`, or the evals list `{host}/specs/{p}/{t}`.
  ```
  Kiln can't load eval "{name}": {reason without trailing .}.
    The run leaves it out. To use it, fix its judge in Kiln, or update Kiln if
    the eval was made with a newer version: {link}
  ```

**Not ready.** Title: `{task_name} isn't ready to tune yet`. Body: the `WHY` lines (readiness.py:219-222), `The tuner starts from one of the task's run configs and measures every change` and `with its evals. This task is missing:`, then any of:

- `  • A run config: the model and prompt the tuner starts from.`
- `  • An eval it can measure: Kiln can't load any of this task's evals (see the warnings).`
- `  • A default judge on its active evals: "A" and "B" have none set.` (`has` for one name) (readiness.py:272-275)
- `  • An eval it can measure: an active eval with a default judge set.`

Menu: `r) Check again (I've fixed it in Kiln)` / `c) Open Run configs in Kiln` (`{host}/optimize/{p}/{t}`, only when run configs are missing) / `e) Open Evals in Kiln` (`{host}/specs/{p}/{t}`, only when evals are missing) / `t) Pick a different task` (only when the task was picked from a list; flow.py:112, 356) / `x) Exit`. The `t` action goes back to the task picker in the same project (flow.py:318).

### Step 6 – API-key check

`_KeyCheck.check` (flow.py:381-406), `needed_providers` (keys.py:103-143), `key_problem` (keys.py:284-330).

- Spinner: `Checking API keys…`.
- Every try reloads the policy through `policy_loader`. A bad `--model-policy` here raises `ConfigRenderError`, which is not an `OnboardingError`, so it reaches `main`'s error screen: `A start-up option needs fixing`, with the message `--model-policy: <exc>.\nfix: correct the file (examples/model-policy.json shows the shape), or drop --model-policy to allow every model.` (cli.py:532-544).
- **Passed:** `✓ API keys  {Provider, Provider}`, or `none needed (local models only)` (keys.py:275-281). Display names come from `PROVIDER_NAMES` (keys.py:58-75).

**Missing, one provider:** title `Kiln is missing an API key this run needs`. Body:

```
The tuner calls this provider through Kiln, which needs a key for it:
  • {Name}: used by {…}[, or remove it from the model policy]
Add the key in Kiln under Settings → AI Providers.
```

**Missing, several:** title `Kiln is missing {N} API keys this run needs`. The intro reads `The tuner calls these providers through Kiln, which needs a key for each:` and the last line reads `Add the keys …`.

The `used by` texts:

- `the task's run config "{name}"`
- `eval "{name}"'s judge`
- `the simulated user in the task's eval inputs`
- `--model-policy`

Menu: `r) Check again (I've added the key|keys)` / `p) Open AI Providers in Kiln` (`{host}/settings/providers`) / `x) Exit`.

The check also records the `Baseline` (name, model, provider) for the summary.

### Step 7 – The questions (guided only)

`_ask_until_started` → `ask_all` (flow.py:239-271; questions.py:655-676). Each question is skipped when its flag was given (`changeable(given)`, questions.py:649-652). With `--demo-project`, the guidance is pre-filled with `DEMO_GUIDANCE = "Improve the moderation decisions. Keep the output format."` (demo.py:41; questions.py:662-663).

The questions, in order (`QUESTIONS`, questions.py:498-584):

**1. Objective.** `_ask_objective` (questions.py:325-337). Picker.

- Question: `What should the tuner improve?`
- Rows: `Quality: higher eval scores` / `Speed: lower latency per request, without losing quality` / `Cost: lower cost per request, without losing quality` (questions.py:186-190).
- Hint, which replaces the pick hint: `One objective per run. Speed and cost only promote a change that keeps quality equal or better.`
- Default: the current objective (`quality`).
- Step: `✓ Improve   Quality|Speed|Cost`.

**2. Guidance.** `_ask_guidance` (questions.py:340-348). Text.

- Question: `Guidance for the tuner's planner (optional)`
- Default: `""`, or the demo text.
- Hint: `What to try first and what not to touch, in your own words. For example: "Keep the output format. Don't switch models." Enter to skip.`
- No validation. The answer is stripped.
- Step: `✓ Guidance  "{text}"` or `✓ Guidance  none`.

**3. Agent CLI.** `_choose_agent_cli` (questions.py:372-400).

- Spinner `Looking for agent CLIs…`, through `resolve_problem`; detection is `shutil.which` for `claude`/`codex`/`opencode`.
- **None installed** (questions.py:285-296). Title: `No agent CLI is installed`. Body:
  ```
  The tuner's agents run through an agent CLI, signed in with its own account:
  Claude Code (claude), Codex (codex) or OpenCode (opencode).
  docs/install.md section 4 has the install and sign-in steps for each.
  ```
  Menu: `r) Check again (I've installed one)` / `x) Exit`.
- **One installed:** no question. The note `the only agent CLI installed` is appended to the Agents step.
- **Several installed:** picker.
  - Question: `Which agent CLI should run the tuner's agents?`
  - Rows: `Claude Code: claude -p` / `Codex: codex exec` / `OpenCode: opencode run`. A row not on PATH is disabled with `not installed`.
  - Hint, which replaces the pick hint: `The tuner's own agents (planner, builders, reviewers) run through this CLI and are billed to its account, not to your Kiln provider keys.`
  - Default: the current kind, if installed.

**4. Model.** `_ask_agents` (questions.py:351-369). Text. Asked whenever question 3 runs, including when detection skips the picker.

- Question: `Model for the agents`
- Default: `opus` for Claude Code, `""` for Codex and OpenCode. When asked again, the model it already has.
- Hint for Claude Code: `Enter keeps opus. sonnet or haiku makes the run cheaper, with weaker plans.`
- Hint for the others: `Enter uses the CLI's own default model. OpenCode models are PROVIDER/MODEL.` (questions.py:222-237)
- Validation: `Enter one model name, with no spaces`.
- The answer becomes `--agent-cli` through `agent_command` (questions.py:248-256): `claude -p --model X`, `codex exec -m X`, `opencode run -m X`. The model is omitted when it is the CLI's default.
- Step: `✓ Agents    {Name} ({base cmd}) · {model|default model}[ · effort E][ · the only agent CLI installed]` (`agents_text`, questions.py:266-276).

**5. Budget.** `_ask_budget` (questions.py:403-425). Text.

- Question: `Budget for this run`
- Default: `50`, or `none`.
- Hint: `The most this run may spend, in USD: the tuner's agents and the task's model calls together. No wave starts past it. Type none for no cap.` For Codex (`reports_cost=False`) it adds ` Codex doesn't report its cost, so this caps the task's model calls only.`
- Validation: `parse_budget`.
- Step: `✓ Budget    $50.00` (format `${:,.2f}`) or `no cap`.

**6. Time limit.** `_ask_time` (questions.py:428-437). Text.

- Question: `Time limit`
- Default: `12h` (`duration_default`: `Nh` when divisible by 60, else `Nm`).
- Hint: `No wave starts after this, and work still running at the limit is cancelled, so leave room for a whole wave.`
- Validation: `parse_duration`.
- Step: `✓ Time      12 hours` / `90 minutes` / `1 hour`.

**7. Advanced menu.** Action menu, only if at least one advanced flag was not given (questions.py:667-675).

- Question: `Advanced options: {advanced_values}`, e.g. `Advanced options: any model · 4 evals and 4 agents at once · no minimum effect · no noise check` (questions.py:619-629). Variants: `model policy {path}`, `any number of evals`, `minimum effect 10%`, `noise check on N item(s) per eval`.
- Menu: `Enter) Keep these` / `c) Customize` (questions.py:588-591).
- `c` asks each advanced question that was not given, in order:

  | Question | Default | Hint | Validation |
  |---|---|---|---|
  | `Model policy file` | `""` | `Which models the tuner may move the task to. examples/model-policy.json shows the format.` | `policy_loader`; the error text is the loader's message (questions.py:440-458) |
  | `Smallest improvement worth acting on` | `""` | `The run stops before spending when no eval can show this change on val.` | optional `parse_fraction` (questions.py:461-469) |
  | `Eval jobs at once` | `4` | `Lower these if your provider rate-limits you.` | `parse_count` |
  | `Agent sessions at once` | `4` | same hint | `parse_count` |
  | `Noise check: items per eval re-run before wave 1` | `0` | `Measures how often the same config scores differently. Costs extra; it is estimated first.` | `parse_count` |

- Step: `✓ Advanced  defaults`, or the changes from the parser defaults, e.g. `model policy ./p.json · 2 evals at once` (`advanced_changes`, questions.py:632-642).

### Step 8 – The key check again

`keys.check_if_changed(answers.model_policy)` (flow.py:255, 271, 408-411). It re-runs Step 6, with its spinner and step line, only when the policy path differs from the one last checked.

### Step 9 – Summary, rerun command and start prompt

**Summary.** `summary_lines`/`summary_text` (summary.py:132-162). It is printed as `terminal.say("\n" + summary_text)` (flow.py:202-204, 259). `SUMMARY_LABEL_WIDTH = 12`, indent 2.

```
Ready to start
  Task         {task} ({id}) in project {project} ({id})
  Starts from  run config "{name}"[: {model}][ on {Provider}]      (omitted when no baseline found)
  Improve      Quality
  Guidance     "…" | none
  Agents       Claude Code (claude -p) · opus
  Limits       $50.00 · 12 hours
  Advanced     defaults | <changes>
```

**Rerun command.** It is printed only here, as `terminal.say(f"\n{RERUN_HEADING}\n{command}\n")` (flow.py:206-215, 260).

- `RERUN_HEADING = "To run this again with the same answers and no questions:"` (summary.py:64).
- `rerun_command` (summary.py:165-211), with indent `"  "`. Lines, joined with ` \`:
  1. `harness-tuner`
  2. `--project-id P --task-id T`
  3. `--project-path '<path>'`
  4. `--objective X`
  5. `--guidance '…'`
  6. `--agent-cli '…'`
  7. `--budget-usd 50|none --timeout-mins 720`
  8. `[advanced flags that differ from the defaults]`
  9. `[flags the start never sets, kept as typed]`
  10. `--no-interaction`
- Every value goes through `shlex.quote`.
- `SET_BY_START` (summary.py:43-61) lists what is dropped from argv and rebuilt.

**Start prompt.** `terminal.act("Start the run?", menu)` (flow.py:115, 261). Menu: `s) Start the run` / `e) Change an answer` (only if some question is changeable) / `x) Exit` (flow.py:113-114, 257).

- `s` returns.
- `x` raises `OnboardingStopped(1)`.
- `e` opens the picker `Which answer do you want to change?` (flow.py:116, 266-269). Rows: `Option(q.name, q.title, (q.show(answers),))`, so each row is a title column plus the current answer. Titles: `What to improve`, `Guidance`, `Agent CLI and model`, `Budget`, `Time limit`, `Model policy`, `Minimum effect`, `Eval jobs at once`, `Agent sessions at once`, `Noise check`. Then `ask_one` asks that question again (an advanced one also reprints the Advanced step), runs the key check if the policy changed, and **prints the summary and rerun command again**. The old ones stay in scrollback.

**Non-guided.** Only `terminal.say(summary())` is printed, to stdout (flow.py:224-225). No rerun command, no prompt. Then `answers.apply(args)` and return (flow.py:226-227).

### Step 10 – Errors during the start

- **An `OnboardingError` in a guided start** (an unknown id, a bad path, a demo failure): `terminal.block(title, body)` on stdout, `error.shown = True` (flow.py:228-232). Then `main` prints only the closing line `harness-tuner stopped with an error (exit 1)` (dim on a TTY) to **stderr** (cli.py:1452-1458; error_screen.py:261-262, 349-357). Exit 1.
- **Non-guided:** `show_error(title, body, ending_lines(progress))` (error_screen.py:312-346).
- **A `KilnError` or `SetupPreconditionError` during a start-up read** (5xx, a changed response shape, a lost connection): not an `OnboardingError`. It reaches `main`'s `SHOWN_ERRORS` branch and shows the full error screen, with the title from `ERROR_TITLES` (cli.py:1375-1395), e.g. `Lost the connection to Kiln` or `Kiln returned an error`. The body comes from `error_body` (error_screen.py:265-276): `METHOD path`, `HTTP <status>: <kiln message>`; or the message plus `Is Kiln still running at {host}?`. The ending reads `Nothing was spent and no run folder was created.`
- **Stopping:** `x`, Exit or Ctrl-D give exit 1; Ctrl-C at a prompt or during a Kiln read gives exit 130. Both print `harness-tuner: stopped.` to stderr (cli.py:1449-1451). `prepare_start`'s whole body is wrapped in `except KeyboardInterrupt: raise OnboardingStopped(130)` (flow.py:233-236).
- **Not validated during the start** (they fail only after `s) Start the run`, in `_run`):
  - `--agent-cli` parse errors: `SystemExit(f"--agent-cli: {exc}")`, a plain stderr line, exit 1, **no error screen** (cli.py:1176-1179).
  - `--agent-model`/`--agent-effort` `ROLE=VALUE` errors: `ConfigRenderError` → `A start-up option needs fixing`.
  - An agent CLI that is missing from PATH or fails its test prompt: `PreflightError` → `The tuner's agents couldn't start`.
  - A malformed `--kiln-host` such as `http://[::1`: a raw `httpx.InvalidURL` traceback (open backlog item, specs/projects/cli-onboarding/backlog.md:6).

---

## 4. Hand-off to the run

**Where the start ends.** `prepare_start` returns `args` at flow.py:227. `main` then calls `asyncio.run(_run_with_signals(args))` at cli.py:1448. This is the **one** event loop for the whole run; the start-up used only short throw-away loops. There are no threads, except rich's Live refresh thread and spinner thread.

**`_run_with_signals`** (cli.py:1335-1368): installs `loop.add_signal_handler` for `SIGTERM` and `SIGHUP` (`STOP_SIGNALS`, cli.py:1331). They are one-shot: the handler cancels the main task, removes itself, and records the signal in `_stopped_by`. Then it awaits `_run`.

**`_run`** (cli.py:1175-1322), in order. Nothing is printed until step 9.

1. `parse_agent_cli(args.agent_cli)` (1176-1179).
2. State folder: `state_home()` → `check_no_overlap` → `prepare_state` (1183-1186). Raises `StateHomeError`.
3. `install_for_run(agent_cli.kind, state.agents, package_version())` (1189-1192). On `InstallError`: `SystemExit("harness-tuner: …")`.
4. `target_from_args`; `parse_role_overrides` for models and efforts (1193-1195).
5. **`await check_agent_cli(...)`** (1198; cli.py:168-186; `check_cli` at claude_cli.py:996-1048). It sends one prompt with a **3-minute** timeout (`CLI_CHECK_TIMEOUT_SECS`, claude_cli.py:174). **No spinner and no message: a silent gap of up to 3 minutes after "Start the run".** Then `_RUN_PROGRESS.cli_checked = True`.
6. `load_model_policy` (1200); `_acquire_run_lock` (1201; `RunLockError`).
7. `run_started_at`, `_unique_run_id` (`YYMMDD-HHMM[-N]`, status.py:57-60), `run_dir = state.runs / run_id` (1205-1208).
8. (No output yet.)
9. **`print(f"{PROG}: run {run_id} — files in {run_dir}/", flush=True)`** (1210), to stdout. The comment says "Before the live display, which owns the terminal from then on."
10. `configure_run_logging(run_dir)` (1215). From here on, the root logger writes to `runs/<id>/kiln-harness-tuner.log` **only**.
11. Log-only `logger.info`/`warning` lines (1216-1238); policy copy (1239-1246); `render_run_configs` (1247-1254); `sweep_staging` (1257-1259).
12. `async with KilnClient(target.kiln_host) as kiln:` (1260); `ctx = build_ctx(...)` (1261-1289); `_RUN_PROGRESS.spend = ctx.spend`.
13. `tracker = RunStatusTracker(project_id, task_id, guidance, clock=ctx.clock, agent_cli=agent_cli.kind)` (1293-1299). The tracker must share `ctx.clock`, because the deadline is monotonic.
14. **The UI is created**: `console = Console()` plus the `LiveUi`/`PlainUi` choice (1300-1306).
15. `with WaveLog(run_dir / "wave_log.jsonl") as wave_log, ui:` (1307). `LiveUi.__enter__` starts `rich.live.Live`.
16. **The UI is attached to the event log**: `ctx.log = FanoutLog(wave_log, tracker, ui, ctx.spend)` (1310; `FanoutLog`, wave_log.py:67-75; the order matters: "Tracker before UI"). Then `ctx.analyzer.log = ctx.log` (1313).
17. `ReportWriter(tracker, run_id, state.reports)` (1314). `_RUN_PROGRESS.report_path` is set, plus `ctx.write_report` and `ctx.write_fallback` (1315-1319).
18. **`return await orchestrate(ctx, args.guidance, args.timeout_mins)`** (1320). `orchestrate` (orchestrator.py:416-432) wraps `ctx.log` in `_ReconcileBeforeExit` (orchestrator.py:435-456). `_orchestrate` (orchestrator.py:458-526) emits **`run_start`** (orchestrator.py:462-474), which is the loop's first event. `LiveUi` prints the header `Kiln Harness Tuner · {project_id} / {task_id} · started HH:MM[ · objective: X]` plus an optional ` guidance: "…"` line and a 78-char `─` rule (ui.py:246-256). `PlainUi` prints `[HH:MM:SS] harness-tuner: run started (timeout N min)`.
19. Then `_run_loop` (orchestrator.py:529-…). In order: the quarantine; the MCP-lane preflight phase `Preflight: verifying the Kiln Harness Tuner gateway MCP lane…`; setup `Setup: syncing the baseline and issue memories with the task's evals…`; janitor; contract check; model preflight; baseline; audit; detectability; noise check; recon (wave 0); `run_opening_screen`; waves.
    - Note: `opening_screen=True` (cli.py:1030) is **not a UI screen**. It is the speed/cost objective's first screening round (orchestrator.py:1071-1108).
    - All of these steps reach the screen **only as events** through `ctx.log.event(...)`. There are no prints.

**What the screen shows between the start and the loop** (guided):

1. The last summary block and the rerun command. The `Start the run?` menu is erased.
2. Silence while the agent CLI check runs (up to 3 min).
3. `harness-tuner: run <id> — files in <dir>/`
4. The live region starts, with the TOTAL row alone at first.
5. On `run_start`, the header.

If `ANTHROPIC_API_KEY` (or similar) is in the environment, a stray warning line can also appear before line 3 (section 7, item 4).

---

## 5. Exit and interruption today

**Clean finish.**

- `orchestrate` returns the report path. On the way: the `exit` event (`LiveUi` freezes the remaining waves, sets `_exited`, and prints `RULE\n exit: <reason>\n TOTAL  <totals>`, ui.py:860-872), the report phase, and `report_written` (` report: <path>`).
- `with` exits, and `Live.stop()` erases the transient live region.
- `main` prints `harness-tuner: run finished — report: <path>` to stdout, exit 0 (cli.py:1482-1483).
- Exit reasons that all end in exit 0: `timeout`, `budget_exhausted`, `planner_failed`, `empty_plan`/`plan.loop_exit`, `tripwire`, `wrap_up`, and `quota_exhausted`. On `quota_exhausted`, the circuit breaker writes the **fallback** report and still returns normally (orchestrator.py:478-501).

**Fatal error inside the run** (orchestrator.py:502-511):

- An `exit` event with `fatal:<Type>`.
- The fallback report, only if a wave has started.
- Then re-raise.
- If the error is in `SHOWN_ERRORS` (cli.py:1400-1413): the error screen on stderr (cli.py:1474-1481) with the ending `The run stopped. Spent $X.XX so far (agents and the task's model calls).`, `Run folder: <dir>`, and `Report: <path>` when the file exists (error_screen.py:279-297). Then `harness-tuner stopped with an error (exit 1)`. Exit 1.
- Any other exception: a raw Python traceback, exit 1, and no run-folder line.

**Ctrl-C during the run.**

- It is SIGINT. `asyncio.run`'s own handler cancels the main task. The cancellation unwinds every `await`:
  - each agent subprocess is SIGKILLed **by process group** (`_kill_and_reap`, claude_cli.py:292-318, called from the runner's `except BaseException`, claude_cli.py:369-380);
  - in-flight Kiln eval jobs get a shielded `POST /api/jobs/{id}/cancel` (analysis.py:1355-1390, 1441-1452);
  - `orchestrate` emits `exit` with `reason="interrupted"` and **writes no report**: "the operator asked us to stop NOW" (orchestrator.py:512-516);
  - the UI context exits, the `KilnClient` closes, and the lock is released (cli.py:1321-1322).
- Then `asyncio.run` raises `KeyboardInterrupt`. `main` prints `harness-tuner: run folder: <dir>` to stderr (cli.py:1426-1430, 1468-1473) and **re-raises**. I verified that the user then sees **a full Python traceback** ending in `KeyboardInterrupt`, and the shell sees exit 130.
- A **second** Ctrl-C during teardown makes asyncio's Runner raise `KeyboardInterrupt` immediately, wherever the code is (stdlib behaviour). That can cut the cleanup short.
- Ctrl-C during the agent CLI check (before the run folder) gives the same traceback and no run-folder line.

**SIGTERM / SIGHUP during the run.**

- The same cancellation, through `_run_with_signals`.
- `main` catches `asyncio.CancelledError` and prints `harness-tuner: interrupted (SIGTERM|SIGHUP) — children killed, lock released` and the run-folder line to stderr. Exit `128+signum`: 143 or 129 (cli.py:1459-1467).
- A second signal hits the default disposition, because the handler is one-shot (cli.py:1347-1356).
- **During `prepare_start` there are no handlers**: SIGTERM or SIGHUP kills the process outright, with no message and no cleanup (a demo copy may be left half-made).

**Deadline (not an interrupt).**

- No wave starts past the deadline. A wave does not start if fewer than `MIN_WAVE_START_SECS = 15*60` remain (orchestrator.py:187, 668-688).
- Mid-wave at the deadline, every in-flight item is cancelled except those already `analyzing`. Those get `ANALYSIS_GRACE_SECS = 10*60` (orchestrator.py:192, 1178-1227). The events are `deadline_cancel` and `deadline_grace`.
- A cancelled item becomes `failed_infra` with `error_class="timeout"`, detail `run deadline reached — item cancelled in flight` (orchestrator.py:1229-1240).
- Then the normal exit with a report. Exit 0.
- Budget: checked at wave boundaries. At the ceiling, `budget_exhausted`. Within one projected wave of it, the next wave is the wrap-up. The wave in flight runs to completion.

**Error-screen endings** (`ending_lines`, error_screen.py:279-297), by how far the run got (`RunProgress`, error_screen.py:249-258, filled by `_run`):

- before the CLI check: `Nothing was spent and no run folder was created.`
- after the CLI check, before the run folder: `The only spend was the agent CLI's one test prompt. No run folder was created.`
- after the run folder: spend, `Run folder:` and `Report:` lines. These are `Unbroken`, so they are never wrapped by the screen.

**Exit codes:**

| Code | When |
|---|---|
| 0 | the run finished (any loop exit reason, including quota) |
| 1 | a failed check (not guided), an `OnboardingError`, `x`/Exit/Ctrl-D at a prompt, any `SHOWN_ERRORS`, the `SystemExit` messages (`--agent-cli`, `InstallError`), an uncaught exception |
| 2 | argparse, removed flags, `--demo-project` with a project flag |
| 130 | Ctrl-C at a start-up prompt or during a Kiln read (clean `stopped.`), or Ctrl-C mid-run (traceback) |
| 143 / 129 | SIGTERM / SIGHUP during `_run` |

**Doc drift to know about:**

- docs/your-own-task.md:665-670 says that on SIGTERM/SIGHUP "the report is written". The code writes **no** report on interruption.
- docs/architecture.md:880-885 mentions a separate `harness-tuner: report: <path>` stderr line after an error. The code shows it inside the error screen as `Report: <path>`.

---

## 6. Terminal-library dependencies

From `pyproject.toml` `[project] dependencies`, with locked versions from `uv.lock`:

- `rich>=13.7` (locked **15.0.0**). Used by cli.py:31, ui.py:42-44, onboarding/console.py:25-30, onboarding/error_screen.py:228-229.
- `questionary>=2.1.1,<2.2` (locked **2.1.1**). The narrow range exists because console.py reaches into its internals: `Question.application.key_bindings` and `InquirerControl` (`filtered_choices`, `found_in_search`, `search_filter`, `pointed_at`, `is_answered`, `add_search_character`). The pyproject comment says so (pyproject.toml, the questionary comment; also specs/projects/cli-onboarding/architecture.md §25).
- **prompt_toolkit is imported directly** (console.py:20-23: `Input`, `KeyBindings`, `KeyPressEvent`, `merge_key_bindings`, `Keys`, `Output`) but is **not a declared dependency**. It arrives through questionary (locked **3.0.53**).
- **Textual is not a dependency and is not in `uv.lock` at all.**
- The other runtime deps are httpx, jq, kiln-ai, mcp<2, pydantic and pyyaml.
- Python: `requires-python = ">=3.11"`. CI (`.github/workflows/ci.yml`) runs a matrix of 3.11 and 3.13 on `ubuntu-latest` only, plus an installed-wheel smoke test (`tests/packaging/smoke_installed_wheel.py`).
- Platform (docs/install.md:19-32): Linux x86_64/arm64 and macOS on Apple silicon. **Windows is not supported**: `cli.py` imports `fcntl` at module top (cli.py:18), so the command cannot start. Intel macOS is not supported because of the lancedb/pylance wheels.

---

## 7. What matters when replacing the terminal UI with Textual

1. **The start-up is synchronous and calls `asyncio.run` per Kiln batch.**
   - `kiln_runner` (flow.py:132-143) runs each batch in its own `asyncio.run`, and the demo, picker, readiness and key code all go through it. `asyncio.run` refuses to run inside a running loop, so `prepare_start` cannot be called as-is from inside a Textual app's event loop.
   - The existing seams help: `prepare_start(..., terminal=..., runner=...)` (flow.py:146-157) and the `Terminal` protocol (terminal.py:66-118). `pick`/`act`/`text` are blocking calls that return a value. `busy` is a context manager around a blocking call. Two ways to use them:
     - a Textual `Terminal` implementation that runs the flow in a worker **thread** and blocks on Textual through `call_from_thread` and futures;
     - or a `runner` that dispatches to the app's loop.
   - The scripted test terminal (`tests/onboarding_fakes.py` `ScriptedTerminal`) drives the whole flow through this protocol, so keeping the protocol keeps ~4,000 lines of flow tests valid.
2. **There is one event loop for the run, and today `main` owns it** (`asyncio.run`, cli.py:1448). A Textual app owns its own loop.
   - The orchestrator would have to run as a Textual worker, or Textual in another thread.
   - `_run_with_signals` installs SIGTERM/SIGHUP handlers with `loop.add_signal_handler` on whatever loop runs `_run`.
   - `ui.event()` is called **synchronously** on the loop thread from `FanoutLog` (wave_log.py:73-75), which has no try/except. The order is wave_log → tracker → **ui** → spend meter (cli.py:1310). An exception in a UI's `event` would propagate into the orchestrator **and skip the SpendMeter**, which breaks `--budget-usd` enforcement.
3. **The orchestrator does blocking work on the loop thread.**
   - Trace copying and redaction after every dispatch is synchronous file I/O (claude_cli.py:1490-1515).
   - OpenCode's transcript export is a blocking `subprocess.run(..., timeout=60)` inside async code (opencode_cli.py:346-366, `EXPORT_TIMEOUT_SECS = 60`).
   - Report and appendix rendering is CPU work.
   - `rich.live` keeps ticking through all of this because it refreshes from its **own thread**, reading `tracker.snapshot()` without a lock (ui.py:749-753). A Textual UI that shares the orchestrator's loop would freeze for those stretches.
4. **Stray terminal writers.**
   - Before `configure_run_logging` (cli.py:1215), the root logger has no handler, so Python's `logging.lastResort` prints WARNING+ to **stderr** as bare messages. Verified: `AsyncioSubprocessRunner()`, built in `check_agent_cli` → `make_dispatcher` (claude_cli.py:320-337, 752), prints `claude -p env: withholding ANTHROPIC_API_KEY, … — ANTHROPIC_API_KEY withheld, so sessions bill the CLI login, not the API` to stderr whenever such variables are set. This happens after the start-up and before the run line.
   - After `configure_run_logging`, logging is file-only by design ("the live UI owns the terminal", logbook.py:15-17).
   - `warnings.warn` is not routed to logging (there is no `captureWarnings`), so library warnings go straight to stderr.
   - Today, `rich.live` (default `redirect_stdout/redirect_stderr=True`) catches such writes and prints them above the live region. A Textual screen must capture or redirect them, or they will tear the display.
5. **Child processes do not touch the terminal.**
   - Agent CLIs are spawned with stdin/stdout/stderr all `PIPE` and `start_new_session=True` (claude_cli.py:349-359). They are outside the terminal's process group, so a terminal Ctrl-C never reaches them. They are killed with `os.killpg(..., SIGKILL)` on cancel.
   - `opencode export` uses `capture_output=True`.
   - The gateway and skill-installer MCP servers are their children, speaking stdio.
   - Nothing inherits the TTY. This is good for Textual.
6. **Ctrl-C semantics depend on SIGINT.**
   - Today, mid-run Ctrl-C relies on SIGINT → asyncio's handler → task cancel → `KeyboardInterrupt` in `main` (cli.py:1468-1473), and the user sees a traceback. Ctrl-C at a start-up prompt is a prompt_toolkit key that raises `KeyboardInterrupt` → `OnboardingStopped(130)`.
   - In a raw-mode TUI, Ctrl-C arrives as a key, not as SIGINT. The TUI has to map it (with the confirmation the overview asks for) to the same main-task cancel, to keep "children killed, lock released, `exit: interrupted` event" and the exit code. Today's mid-run code is 130 (by uncaught KeyboardInterrupt).
   - `docs/install.md:637-638` promises Ctrl-C=130 and Ctrl-D=1 at start-up prompts, and `harness-tuner: stopped.` for both.
7. **Scrollback is part of the product today.**
   - The guided start leaves a permanent record on stdout: the welcome box, step lines, ✗ blocks, warnings, the summary, and the rerun command, which is "printed here and nowhere else" (functional_spec §11; docs/your-own-task.md:621-628).
   - `LiveUi` freezes each finished wave and the exit block into scrollback, and its live region is `transient=True`.
   - A full-screen (alternate-screen) Textual app erases all of that on exit, unless it is re-printed after the app exits, or the app runs inline.
   - Users and docs also rely on `harness-tuner: run <id> — files in <dir>/` and `harness-tuner: run finished — report: <path>` appearing in the terminal.
8. **The streams are split, and scripts may depend on it.**
   - Guided: steps, blocks, warnings, prompts and the summary go to stdout. The closing error line, `stopped.`, `interrupted…`, the run-folder line and the error screen go to stderr.
   - Non-guided: steps and the summary go to stdout, and warnings and errors to stderr.
   - The run line and the finish line go to stdout. Both UIs write to stdout (`Console()`).
9. **There are three TTY rules** (section 2). Guided needs stdin and stdout to be TTYs. The run display uses rich's `is_terminal` on stdout, which honours `FORCE_COLOR`/`TTY_COMPATIBLE`. The error screen uses stderr `isatty`.
   - Keeping "all flags work" means `--no-progress` (and a non-TTY stdout) must keep a non-Textual plain line path. `PlainUi` can stay as is.
   - `--no-interaction` in a TTY means "no questions, but the live display", so the Textual run view must work without the Textual start-up.
   - `--no-progress` in a TTY means "Textual or guided questions, then plain lines". The TUI must hand the terminal back cleanly before `PlainUi` prints.
   - `PlainUi` hard-wraps at 80 columns on a non-TTY (rich `Console.print` without `soft_wrap`). If any test or log parser expects that, keep it.
10. **Tests that pin today's terminal layer:**
    - `tests/test_onboarding_console.py` drives `RichTerminal` through prompt_toolkit `create_pipe_input`/`DummyOutput` and checks questionary choice titles (`"Enter) Keep these"`, `"r) Check again"`), alignment, the welcome box width (68), `NO_COLOR`, the spinner off on a non-terminal, and the browser rule.
    - `tests/test_cli.py:641-667` monkeypatches `cli.LiveUi`, `cli.PlainUi` and `cli.Console` (as a zero-arg callable) to assert the display choice. `tests/test_cli.py:604-634` asserts that stdout **starts with** `harness-tuner: run <id> — files in <dir>/\n` and contains `harness-tuner: run finished — report: …`.
    - `tests/test_cli.py:1292-1323` asserts the interrupted and run-folder stderr lines.
    - `tests/test_error_screen.py` pins the error screen.
    - `tests/test_docs.py:195` requires every `build_parser()` flag to be documented exactly once, so a new TUI flag needs a docs row.
    - `tests/test_dispatch_boundary.py` forbids any module except `cli.py` from importing a concrete dispatcher. A TUI module must get facts (for example `REPORTS_COST`) the way `questions.py` restates them (questions.py:217-221).
11. **The ui.py docstring says the render functions are pure** (`RunStatus` → str) and unit-tested without a terminal (ui.py:27-30). `RunStatus` is "the one state object; everything downstream is a view of it" (docs/architecture.md §11, §14 item 4: "UI behind `RunStatus`"). The TUI can be built on `RunStatusTracker.snapshot()` plus the event stream, with no core change.
12. **questionary quirks the code works around** (console.py:105-152) disappear with Textual. The pickers' behaviour is specified, and the TUI must keep it:
    - typing filters on the whole row (name or ID);
    - Enter never picks a disabled row, or anything while the filter matches nothing;
    - arrows skip disabled rows;
    - the cursor starts on the default or the first enabled row;
    - each action key acts with no Enter;
    - `Enter)` may only lead a menu;
    - a hint replaces `PICK_HINT`;
    - descriptions show under the highlighted row.
13. **Opening a page.** `webbrowser.open` from a TUI on a non-graphical Linux could start a console browser in the foreground. That is why the rule is graphical-only (console.py:56-62). Keep the rule: print first, open only where a graphical browser can be expected.
14. **Windows** is unsupported today: the `fcntl` import at cli.py:18, `os.killpg`, POSIX `shlex.quote` in the rerun command, and `add_signal_handler` (guarded by suppress). Textual adds no new constraint there.

---

## 8. Spec and docs vs code differences (the code wins)

- The command name: the spec and the flow tests use `kiln-harness-tuner`. The real `PROG` is `harness-tuner` (cli.py:128). All the strings above use `harness-tuner`.
- The no-agent-CLI body: the spec §8 says "docs/install.md section 5". The code says **`section 4`** (questions.py:293), and docs/install.md agrees.
- The one-installed Agents step: the spec §8 shows `Claude Code (claude -p) · the only agent CLI installed`. The code puts the model first: `… · opus · the only agent CLI installed` (questions.py:366-369; docs/install.md:470).
- The spec §4 says non-guided errors are "prefixed `kiln-harness-tuner:`". The code uses `show_error`, which prints `harness-tuner: error: <title>` on a non-TTY stderr and the **rich ✗ screen when stderr is a TTY** (for example `--no-interaction` in a terminal).
- The SIGTERM/SIGHUP "report is written" wording in docs (section 5). Not true in code.
- The `--objective` help text describes only quality and speed, but `cost` is a valid choice (cli.py:411-419, 467).
- The `--max-concurrent-agents` help says "`claude -p` sessions" for all CLIs.
