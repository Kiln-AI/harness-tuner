---
status: complete
---

# Phase 2: Project and task selection, the rerun line, and readiness

## Overview

After this phase, `--project-id`, `--task-id` and `--project-path` are optional. When one is missing, the start-up lists what Kiln has and lets the user pick, or uses the only choice (architecture §7). When the CLI filled anything in, it prints a command that re-runs the same thing with no questions (§8). Then it checks that the task is ready to tune: a run config, and an eval the loop can measure (§9). A task the user picked can be swapped for another with `[t]` at the readiness prompt (§5).

The readiness rule for evals is the loop's own measurement rule. So that rule moves out of `orchestrator._active_evals` into two pure helpers in `setup.py`, which both use.

The API-key check (phase 3) and `--demo-project` (phase 4) are not part of this phase.

## Steps

1. **`setup.py`: `active_eval_rows` and `measurable_evals`** (§9). Pure, next to `SPEC_STATUS_ACTIVE`:

   ```
   def active_eval_rows(eval_rows: list[Any], spec_rows: list[Any]) -> list[dict[str, Any]]:
       """Dicts with an id, whose spec is absent or has status 'active'."""

   def measurable_evals(eval_rows: list[Any], spec_rows: list[Any]) -> list[tuple[dict[str, Any], str]]:
       """active_eval_rows that carry a current_config_id, paired with it."""
   ```

   - The status map is keyed by `str(spec["eval_id"])`, from specs that are dicts with an `eval_id`. An eval's id is compared as `str` too.
   - A spec with no `status`, or any status but `active`, makes its eval inactive, as today.
   - Row order is kept.

2. **`orchestrator._active_evals`** uses them:
   - It reads and unwraps the evals as today, with the load-error warning.
   - It reads the specs with `or []`.
   - It returns `[(e, c) for e, c in measurable_evals(evals, specs) if not _new_eval_refused(ctx, str(e["id"]))]`.
   - The only change: an eval row with no `id`, or a row that isn't a dict, is skipped instead of raising.

3. **`onboarding/choose.py`** (§7):
   - `ProjectRow(id, name, path)`, `TaskRow(id, name)` and `Selection(project, task, project_path, filled, task_picked)`, all frozen dataclasses.
   - `async fetch_projects(kiln) -> list[ProjectRow]` reads `GET /api/projects`. `async fetch_tasks(kiln, project_id) -> list[TaskRow]` reads `GET /api/projects/{id}/tasks`.
     - The body goes through setup's `_require_rows`: `None` is no rows, and a non-list raises `SetupPreconditionError(unreadable_response_message(...))`.
     - Rows that aren't dicts, or have no `id`, are dropped. `name` falls back to the ID. `path` is kept only when it is a non-empty string.
   - `choose_one(rows, *, noun, heading, flag, where, terminal) -> tuple[row, bool]` (§7.1):
     - 1 row: `Using {noun} {name} ({id}), the only {noun} {where}.`, and `False`.
     - 2+ rows, not interactive: `OnboardingError("{flag} is needed when not run interactively. {heading}:\n  {id}  {name}…")`.
     - 2+ rows, interactive: `{heading}:` and the rows numbered from 1 (`  1. {name}  ({id})`), then ask `{Noun} [1-N, x to exit]: ` until the answer is a number in range. `x` raises `OnboardingStopped(1)`. Returns `True`.
     - `prog` is not a parameter (the architecture lists it): no message in it names the command.
   - `select(args, *, terminal, run, host, prog) -> Selection` (§7.2), the FS §5.1 table:
     - **Project.** By `--project-id` (an unknown ID is an error with the listing). Else by `--project-path`, matching the parent of each row's `path` after `expanduser().resolve()`. Else by `--task-id`, reading every project's tasks in one batch and choosing among the projects that have it. Else `choose_one` over all projects.
     - **Task.** By `--task-id` (an unknown ID is an error with the listing). Else `choose_one` over the project's tasks. The task-only lookup reuses the tasks it already read.
     - **Path** (FS §5.4). The flag and Kiln's folder must match after `expanduser().resolve()`, else the mismatch error. With no flag, Kiln's folder is used, and a project with no Kiln path is an error asking for `--project-path`. A flag that is given is kept as typed.
     - No projects, or no tasks, is the FS §5.2 error. An unknown ID when Kiln has none of that kind gives the same text after the "has no" line.
     - `filled` is True when any of the three flags was `None`. `task_picked` is what `choose_one` returned for the task.
   - `run` is typed `KilnRunner` through a `TYPE_CHECKING` import of `flow`, which imports this module.

4. **`onboarding/readiness.py`** (§9):

   ```
   async def readiness_problem(kiln, *, host, project_id, task_id, task_name) -> tuple[Problem | None, list[str]]
   ```

   - One `asyncio.gather` of `{base}/run_configs`, `{base}/evals` and `{base}/specs`.
   - The run configs go through `_require_rows`, then `_usable_run_configs`. The evals go through `eval_rows_from_response`. The specs go through `_require_rows`.
   - The problem lines, in order: no run configs (link `kiln_urls.run_configs`); active evals with no default eval config, naming them in quotes; or no eval at all (both link `kiln_urls.evals`).
   - The message is `Task {name} ({id}) isn't ready to tune:` and one `  • ` line per problem. `open_url` is the first line's link, and `open_label` is `the first link`.
   - A non-zero `load_error_count` gives one warning that names the count.

5. **`onboarding/flow.py`**:
   - `rerun_command(prog, argv, *, parser_long_options, project_path, project_id, task_id) -> str` (§8):
     - It drops `--demo-project`, `--project-id`, `--task-id` and `--project-path` from `argv`, in the `--x v` and `--x=v` forms. A token's name (the part before `=`) resolves to the long option it equals, else to the single long option it is a prefix of, as argparse's `allow_abbrev` does.
     - Scanning stops at `--`.
     - It returns `shlex.join([prog, "--project-path", path, "--project-id", pid, "--task-id", tid, *rest])`.
   - `prepare_start(args, argv, *, prog, parser_long_options, terminal=None, runner=None)`:
     - The Kiln check, as in phase 1.
     - Then a loop. It runs `select`, writes the three IDs and the path into `args`, and prints `To run this again without the questions:\n  <command>` when `selection.filled` is True. Then it runs readiness through `resolve_problem(..., offer_pick_task=selection.task_picked)`, printing the check's warnings through `terminal.warn`.
     - `PICK_TASK` sets `args.task_id = None` and goes round again. The project and its path are kept, so only the task picker runs.
     - The architecture's `filled` flag outside the loop is left out. Without the demo, a `[t]` pass always has `task_id` `None`, so `selection.filled` is already True. Phase 4 adds the flag with the demo.

6. **`cli.py`** (§12):
   - `--project-path`, `--project-id` and `--task-id` lose `required=True`. Their help says what happens when each is left out.
   - `_Parser` records its long option strings in `long_options`, through an `add_argument` override. That is the public way to know them; the parser's own table is private.
   - `main` keeps the raw argv (`sys.argv[1:]` when `argv` is `None`), and calls `prepare_start(args, raw_argv, prog=PROG, parser_long_options=parser.long_options)`.

7. **Tests and fakes:**
   - `tests/onboarding_fakes.py`: the `openapi()` document builder moves here from the Kiln check tests. New builders for a project row, a task row, the routes of a ready task, and a whole Kiln with projects and their tasks.
   - `tests/test_onboarding_kiln_check.py`: `start()` passes argv, the long options and the IDs of a ready task, so these tests stop after the Kiln check's output. Assertions on call paths look at the Kiln-check calls only.
   - `tests/test_cli.py`: the fixture and the fakes take the new `argv` and `parser_long_options` arguments.

## Tests

- `tests/test_setup.py` or `tests/test_onboarding_readiness.py`, for the helpers:
  - `test_active_eval_rows_keeps_evals_with_no_spec_or_an_active_one`.
  - `test_active_eval_rows_drops_inactive_and_statusless_specs`.
  - `test_active_eval_rows_skips_rows_that_arent_dicts_or_have_no_id`, and specs that aren't dicts.
  - `test_measurable_evals_pairs_each_active_eval_with_its_default_config`, dropping evals with no `current_config_id`.
- `tests/test_onboarding_readiness.py`:
  - `test_a_ready_task_has_no_problem`: one run config and one measurable eval. The three reads are made.
  - `test_no_run_configs`: the line and its link. `None`, `[]` and rows with no `id` all count as none.
  - `test_no_eval`: no evals, and only evals whose spec isn't active.
  - `test_active_evals_without_a_default_config_are_named`.
  - `test_both_problems_are_listed_at_once`, and `open_url` is the first link.
  - `test_the_eval_envelope_is_read_and_load_errors_warn`: `{"evals": […], "load_error_count": 2}`.
  - `test_a_drifted_listing_raises_setup_precondition_error`.
- `tests/test_onboarding_choose.py`:
  - `fetch_projects` and `fetch_tasks`: rows that aren't dicts or have no `id` are dropped, `name` falls back to the ID, `None` is no rows, and a non-list raises `SetupPreconditionError`.
  - `choose_one`: 1 row uses it and says so; 3 rows interactive pick by number; invalid input (`0`, `4`, `abc`, empty) asks again; `x` stops with 1; end of input stops with 1; 2+ rows not interactive gives the listing error with no prompt.
  - `select`, projects: 0 projects (the FS text, with the create link and `--demo-project`), 1 project, several picked interactively, several not interactive (the error lists them).
  - `select`, tasks: 0 tasks (the create-task link), 1 task, several picked, several not interactive.
  - Unknown project ID, and unknown task ID, each with the listing, interactive or not. An unknown ID when Kiln has none.
  - Task ID only: in one project (used), in two (picked, or the not-interactive listing of those two), in none (error).
  - `--project-path` only: the project is found; a folder Kiln doesn't have is an error.
  - The path: Kiln's folder when the flag is missing; a mismatch is the FS error; a matching flag with `~` or a relative form is accepted and kept as typed; a project with no Kiln path uses the flag, or is an error without one.
  - `filled` and `task_picked`: all three given gives `False, False`; a single task gives `task_picked` `False`.
- `tests/test_onboarding_rerun.py`:
  - `test_the_three_flags_are_added_first`, after `prog`.
  - `test_given_flags_are_replaced` in both the `--x v` and `--x=v` forms.
  - `test_abbreviations_are_resolved_like_argparse` (`--project-i`, `--task=…`), and an ambiguous prefix is kept.
  - `test_demo_project_is_dropped`.
  - `test_other_flags_are_kept_in_order`, with `--agent-model` repeated.
  - `test_values_with_spaces_and_quotes_are_shell_quoted`; the output splits back to the same list.
  - `test_scanning_stops_at_a_double_dash`.
- `tests/test_onboarding_flow.py`:
  - `test_no_flags_walks_the_kiln_check_the_pickers_and_readiness`: two projects and two tasks, picked by number. The rerun line is printed once, before readiness, and the returned namespace holds the IDs and Kiln's folder.
  - `test_all_flags_given_prints_no_rerun_line`.
  - `test_a_single_project_and_task_are_used_and_the_rerun_line_is_printed`.
  - `test_a_task_that_isnt_ready_retries_until_it_is`.
  - `test_t_goes_back_to_the_task_picker`: the first task isn't ready; `t`, pick the second; the rerun line names the second task.
  - `test_t_is_not_offered_for_a_task_that_wasnt_picked`.
  - `test_not_ready_and_not_interactive_fails`.
  - `test_ctrl_c_at_the_picker_stops_with_130`.
- `tests/test_cli.py`:
  - `test_the_ids_and_the_path_are_optional`: parsing with none of them gives `None` for each.
  - `test_main_runs_the_start_up_checks_before_the_run`: `prepare_start` gets the raw argv and `parser.long_options`.
  - `test_the_parser_records_every_long_option`: `long_options` equals the long option strings of every action.
