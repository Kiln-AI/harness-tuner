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
- [ ] **Phase 6: Backlog.** Review open backlog items with the user, then close or dismiss each through the standard phase flow.
