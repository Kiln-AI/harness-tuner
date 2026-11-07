---
status: complete
---

# Implementation Plan: CLI onboarding

Each phase leaves the suite green (`uv run pytest`, `uv run ruff check`, `uv run ruff format --check`) and the CLI working. Section numbers point into `architecture.md`.

## Phases

- [x] Phase 1: Wiring, prompts and the Kiln check.
  - `onboarding/` with `errors.py`, `terminal.py`, `kiln_urls.py` and `kiln_check.py`, plus `flow.py` running only the Kiln check (§3–§6).
  - `cli.py`:
    - `PROG`.
    - `main` calls `prepare_start` and handles `OnboardingError` and `OnboardingStopped` (§12).
    - The flags stay required in this phase.
  - `tests/onboarding_fakes.py`.
  - Tests for the terminal and the Kiln check.
  - The autouse pass-through fixture in `tests/test_cli.py`.
- [x] Phase 2: Project and task selection, the rerun line, and readiness.
  - `active_eval_rows` and `measurable_evals` in `setup.py`; `_active_evals` uses them (§9).
  - `choose.py`, `rerun_command` and `readiness.py`, with the `[t]` loop in `prepare_start` (§5, §7–§9).
  - `--project-id`, `--task-id` and `--project-path` become optional (§12).
  - Tests for choose, rerun and readiness, the flow up to readiness, and the `test_cli.py` changes for the optional flags.
- [x] Phase 3: The API-key check.
  - `choose_baseline_rc` in `setup.py`; seeding uses it (§10.1).
  - `keys.py`: the baseline, judge, simulated-user and policy providers, and `key_problem` (§10).
  - The policy loader is passed into `prepare_start`.
  - Tests for keys and `choose_baseline_rc`, plus the full no-flags flow test.
- [x] Phase 4: `--demo-project`.
  - `git mv` of the demo project into `src/kiln_harness_tuner/demo_project/`, as a commit of its own with no content change (§13).
  - The `.gitignore` rule, `tests/test_moderation_example.py`, `examples/moderation/README.md` and `.agents/AGENTS.md` (§13).
  - The one-time wheel check: `uv build --wheel`, then list the demo files in it.
  - `demo.py`, `--demo-project` and `validate_args`, `make_demo` in the flow, and the `read_fence_for` deny (§11, §12).
  - The `--demo-project` row in the flag table of `docs/your-own-task.md`, because `tests/test_docs.py` requires one row per flag.
  - Tests for the demo, the demo flow (including the `-1` second copy), and the cli test for `--demo-project` with `--task-id`.
- [x] Phase 5: Docs.
  - `docs/install.md` §4 and §6, with the two corrections, and the new "Try the demo" section (§14).
  - `docs/your-own-task.md` §1, §5 and the flag table rows for the optional flags (§14).
- [x] Phase 6: The terminal layer and the messages (guided start, part 1).
  - `questionary>=2.1.1,<2.2` in `pyproject.toml`, and a regenerated `uv.lock` (§17).
  - `Option`, `Action`, `ActionKind`, the new `Terminal` protocol, `Problem` with title, body and actions, and the new `resolve_problem` (§18, §19).
  - `onboarding/console.py`: `RichTerminal` (step lines, blocks, spinners, pickers with filter, single-key action menus, text questions) and `PlainTerminal`, plus the `open_url` browser rule. This closes backlog item 1: mark it closed in `backlog.md`.
  - The FS §4–§9 messages and actions in `kiln_check.py`, `choose.py` (no projects → use the demo, no tasks → pick another project), `readiness.py` (one action per link), `keys.py` and `demo.py`. `kiln_urls.py` gains the download and update links.
  - `flow.py`: the header, step lines and spinners. The terminal is chosen by the guided rule, and `--no-interaction` is added in `cli.py` with its row in the flag table of `docs/your-own-task.md` (`tests/test_docs.py` requires one row per flag). The rerun line stays where it is until phase 7.
  - Tests: `test_onboarding_console.py`, `ScriptedTerminal` by key, and the existing onboarding tests updated to the new messages (§24).
- [x] Phase 7: The questions and the summary (guided start, part 2).
  - `questions.py`: `Answers`, the question registry, the value parsers, agent CLI detection, the Advanced menu and `ask_one` (§20).
  - `summary.py`: `OptionInfo`, `given_dests`, `summary_lines` and the multi-line rerun command with `--no-interaction`. `flow.rerun_command` and the early rerun line go (§21).
  - `keys.needed_providers` returns the `Baseline` (§21).
  - `prepare_start` in its new order: questions, the policy re-check, the summary, Start / Change an answer / Exit, and the plain summary when not guided (§22).
  - `cli.py`:
    - `option_dests`;
    - `--budget-usd` with `parse_budget` and a default of 50;
    - `--timeout-mins` with a default of 720;
    - `--no-ui` renamed `--no-progress`, with the old name in `REMOVED_FLAGS`.

    Also the flag table rows for these in `docs/your-own-task.md` (§23).
  - Tests: `test_onboarding_questions.py`, `test_onboarding_summary.py`, and the flow and `test_cli.py` cases of §24.
- [x] Phase 8: Docs for the guided start.
  - `docs/install.md`:
    - §4 and §6 and "Try the demo" rewritten for the new messages and actions;
    - a section on guided and not guided, with the `--no-interaction` and `--no-progress` table (FS §14).
  - `docs/your-own-task.md` §1 and §5: §1's readiness prompt example, and the questions, the summary and the rerun command.
  - `docs/costs.md`: any statement of the old budget or timeout default.
  - `examples/moderation/README.md`: what `--demo-project` asks.
- [ ] **Phase 9: Backlog.** Review open backlog items with the user, then close or dismiss each through the standard phase flow.
