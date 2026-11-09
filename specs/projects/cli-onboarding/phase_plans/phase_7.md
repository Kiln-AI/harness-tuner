---
status: complete
---

# Phase 7: The questions and the summary (guided start, part 2)

## Overview

Phase 6 gave the start-up checks the guided terminal. This phase adds what a guided start asks after the checks pass, and what every start shows before the run (FS §10–§12, architecture §20–§23):

- the core questions (objective, guidance, agent CLI and model, budget, time limit) and the Advanced menu, each skipped when its flag is on the command line;
- the key check again when an answer set or changed the model policy;
- the `Ready to start` summary, with the multi-line rerun command and the Start / Change an answer / Exit menu in a guided start, and the plain summary otherwise;
- the new defaults (`--budget-usd 50` with `none`, `--timeout-mins 720`) and `--no-ui` renamed `--no-progress`.

The phase-2 rerun line and `flow.rerun_command` go: the rerun command is printed in the summary and nowhere else.

## Steps

1. **`onboarding/terminal.py`.**
   - `Terminal.pick` gains `default: T | None = None`: the option the cursor starts on. The objective and agent CLI pickers start on the current answer, so Change an answer shows what is there.
   - `ENTER = "enter"`: the key of an action Enter picks, shown as `Enter)`. It must be the menu's first action (the Advanced menu's `Enter) Keep these`).

2. **`onboarding/console.py`.**
   - `pick` passes `default` to questionary as the index of that option, when it is enabled.
   - `act` draws each title as `<key>) <label>` itself (`Enter)` for `ENTER`) with `use_shortcuts=False`, since questionary's shortcuts must be single characters; its eager per-key bindings stay, and the `ENTER` action gets none, since Enter picks the pointed row.

3. **`onboarding/questions.py`** (new):
   - The value parsers, each raising `ValueError` with the FS message:
     ```python
     def parse_budget(text: str) -> float | None      # "50", "$12.50", "none"
     def parse_duration(text: str) -> int             # "90", "90m", "12h", "1.5h"; whole minutes >= 1
     def parse_fraction(text: str) -> float           # above 0, at most 1
     def parse_count(text: str) -> int                # a whole number >= 0
     ```
   - The text helpers: `number_text` (no trailing `.0`), `budget_text` (`$50.00`, `no cap`), `duration_text` (`12 hours`, `90 minutes`), `budget_default` / `duration_default` (`50`, `none`, `12h`, `90m`).
   - `Answers` (the ten fields of architecture §20) with `from_args(args)` and `apply(args)`.
   - Agent CLIs: `AgentKind(kind, name, command, model_option, default_model, reports_cost)` for `claude`, `codex` and `opencode`, with `reports_cost` read from each dispatcher's `REPORTS_COST`; `installed_agent_clis(which)`; `agent_command(kind, model)` builds the command through `shlex.join` and checks it through `parse_agent_cli`; `agents_text(command)` is the step and summary text, `Claude Code (claude -p) · opus`.
   - `Question(name, title, dests, advanced, ask, show)` and `QUESTIONS` in FS order: `objective`, `guidance`, `agents` (the CLI and its model, one question for `--agent-cli`), `budget`, `time`, then the advanced `model_policy`, `min_effect`, `max_concurrent_evals`, `max_concurrent_agents`, `noise_check`.
   - `QuestionContext(terminal, defaults, policy_loader, demo, which)`.
   - Each core `ask` asks, sets its field and prints its step line. The agent CLI: none installed is a `Problem` through `resolve_problem` whose check runs detection again; one installed is used with `· the only agent CLI installed`; otherwise the picker, with not-installed rows disabled. Then the model question, whose hint and default depend on the CLI.
   - The budget hint adds `<CLI> doesn't report its cost, so this caps the task's model calls only.` for an agent CLI whose dispatcher doesn't report cost.
   - The model policy question's validate runs `policy_loader` and shows its message.
   - `advanced_values(answers)` for the menu question (`any model · 4 evals and 4 agents at once · no minimum effect · no noise check`) and `advanced_changes(answers, defaults)` for the step and summary line (`defaults`, or `model policy ./policy.json · 2 evals at once`).
   - `ask_all(answers, ctx, *, given)`: with the demo and no `--guidance`, the guidance starts as `DEMO_GUIDANCE`; each core question not given; then, when any advanced question is not given, the Advanced menu (`Enter) Keep these`, `c) Customize`), Customize asking each of them, and the `Advanced` step line.
   - `ask_one(name, answers, ctx)`: one question again; an advanced one prints the `Advanced` line.
   - `changeable(given)`: the questions whose flags weren't given.

4. **`onboarding/demo.py`.** `DEMO_GUIDANCE = "Improve the moderation decisions. Keep the output format."`.

5. **`onboarding/keys.py`.** `Baseline(name, model, provider)`; `needed_providers` returns `(needs, baseline)`. The `Baseline` comes from the baseline row it finds: its name, `model_name` and the display name of `model_provider_name`. `None` when no run config is found.

6. **`onboarding/summary.py`** (new):
   ```python
   @dataclass(frozen=True)
   class OptionInfo:
       dest: str
       takes_value: bool
       default: Any = None

   SET_BY_START: frozenset[str]   # architecture §21
   def given_dests(argv, option_dests) -> frozenset[str]
   def parser_defaults(option_dests) -> Answers
   def summary_lines(selection, answers, baseline, defaults) -> list[tuple[str, str]]
   def summary_text(lines) -> str
   def rerun_command(prog, argv, *, option_dests, selection, answers) -> str
   ```
   - `_long_option` moves here from `flow.py`.
   - `given_dests` and the kept tokens read `argv` the same way: an option with a value takes the next token unless it has `=`, and the scan stops at `--`.
   - `rerun_command` builds the lines of architecture §21, quotes each token with `shlex.quote`, indents the lines after the first by two spaces, and joins them with ` \` and a newline.

7. **`onboarding/flow.py`** (architecture §22):
   - `prepare_start(args, argv, *, prog, option_dests, policy_loader, terminal=None, runner=None, home=None, which=shutil.which)`.
   - `rerun_command`, `_long_option`, `FILLED_FLAGS` and the early rerun line go.
   - After the key check: `Answers.from_args(args)`. Guided: `ask_all`, the key check again when the policy differs from the one last checked, then the loop of summary, rerun command and `Start the run?` (`s) Start the run`, `e) Change an answer` when any question is changeable, `x) Exit`). Change picks a question, `ask_one`, and the key check again when the policy changed. Not guided: the summary only.
   - `answers.apply(args)` before returning.

8. **`cli.py`** (architecture §23):
   - `_Parser.option_dests: dict[str, OptionInfo]` replaces `long_options`, recorded for each long option with its dest, whether it takes a value, and its default. `main` passes `option_dests=parser.option_dests`.
   - `--budget-usd`: `type=_budget` (wrapping `parse_budget`), `default=DEFAULT_BUDGET_USD = 50.0`, help with `none`.
   - `DEFAULT_TIMEOUT_MINS = 720`.
   - `--no-progress` replaces `--no-ui`; `REMOVED_FLAGS["--no-ui"]`; `_run` reads `args.no_progress`; the docstrings in `cli.py` and `ui.py` follow.
   - `_fraction` wraps `parse_fraction`.

9. **Docs.** The flag table of `docs/your-own-task.md`: `--no-progress` replaces `--no-ui` (and the `--no-interaction` row names it), the `--budget-usd` and `--timeout-mins` defaults. `docs/architecture.md`'s one `--no-ui` mention follows the rename.

## Tests

- **`tests/onboarding_fakes.py`:**
  - `ScriptedTerminal.pick` records each pick's default; `text` checks a plain answer against `validate`, as the real field would refuse it; `say("")` is recorded.
  - `flags()` starts from the parser's defaults.
  - `ANSWERED`: an argv that answers every question with its default, for tests about the checks.
- **`tests/test_onboarding_questions.py`** (new):
  - Each parser's valid and invalid answers, with the FS messages.
  - The text helpers.
  - `Answers.from_args` and `apply` round trip.
  - `ask_all` with nothing given: every question in order, each step line, the pre-filled defaults, and the answers.
  - Each flag given skips its question; every advanced flag given skips the Advanced menu.
  - The demo guidance pre-filled; guidance none.
  - Agent CLI detection with 0 (retry, then exit), 1 and 3 installed; a not-installed CLI is disabled.
  - The model question per CLI: its hint, its default, and the command built; a model with a space refused.
  - The budget hint for a CLI that doesn't report cost.
  - Keep, and Customize with each value; values given by flags skipped in Customize.
  - A bad policy file refused with the loader's message.
  - `ask_one` re-asks with the current answer pre-filled; an advanced one prints the Advanced line.
  - `advanced_values` and `advanced_changes`.
- **`tests/test_onboarding_summary.py`** (new):
  - `given_dests` with abbreviations, `=`, values that look like flags, and `--`.
  - `parser_defaults`.
  - The rerun command: every answer present, in its lines; flags it sets replaced; other flags kept on their own line; `none` and `''`; advanced only when not the default; `--no-interaction` last; quoting.
  - It parses back (`build_parser().parse_args`) to the same `Answers` and selection, and a POSIX shell reads it as the same tokens.
  - `summary_lines` with and without a baseline, and with Advanced changes; `summary_text` layout.
- **`tests/test_onboarding_keys.py`:** the `Baseline` from the recorded, default and most recent run config; no model or provider; none when there's no baseline row.
- **`tests/test_onboarding_console.py`:** a pick starts on its default; an `Enter)` action is drawn and picked by Enter; letter keys still act at once.
- **`tests/test_onboarding_flow.py`:**
  - The whole guided flow with no flags: checks, questions, summary, rerun command, Start, and the returned `Namespace`.
  - Not guided: no prompt, the summary printed, the defaults applied.
  - A policy set in Advanced runs the key check again; a policy changed by Change an answer too; an unchanged policy doesn't.
  - Change an answer re-asks and shows the summary again; `e` is not offered when every question was given.
  - Exit at the summary stops with 1.
  - The demo: the guidance pre-filled, and the rerun command on the copy.
  - The existing check tests updated for the summary and the start prompt.
- **`tests/test_onboarding_kiln_check.py`:** the flow-based tests answer every question by flag and start at the prompt.
- **`tests/test_onboarding_rerun.py`:** removed; `test_onboarding_summary.py` covers the rerun command.
- **`tests/test_cli.py`:**
  - `--budget-usd` defaults to 50, takes `none` and `$12.50`, and an invalid value exits 2.
  - `--timeout-mins` defaults to 720.
  - `--no-progress` parses and gives the plain display; `--no-ui` exits 2 with the rename message.
  - `option_dests` records every long option with its dest, value and default; `main` passes it.
