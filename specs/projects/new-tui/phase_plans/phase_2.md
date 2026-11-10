---
status: complete
---

# Phase 2: CLI seams, with no behavior change

## Overview

The app of phases 4 to 6 plugs into the CLI at a few points that do not exist yet: a start report out of `prepare_start`, a display and a start hook into the run, a mode choice in `main`, exception arms that both paths share, the plain exit line as a function, and the start-up's display rules outside `console.py`. This phase adds those seams (architecture sections 2, 4.3, 4.4, 5.1, 5.2). Every one is additive or a move. Each default keeps today's code path, so `harness-tuner` behaves exactly as before: `main` still runs the plain body for every invocation, and the app stays unreachable until phase 5.

## Steps

1. **`StartReport` and `prepare_start(report=)`** (`onboarding/flow.py`, `onboarding/keys.py`, `onboarding/__init__.py`).
   - `keys.Baseline` gets a required `rc_id: str` field (last), set from `_run_config_id(row)` in `_baseline`. The summary does not read it.
   - New frozen dataclass in `flow.py`:
     ```
     @dataclass(frozen=True)
     class StartReport:
         project_name: str
         task_name: str
         baseline_rc_id: str | None
         baseline_name: str | None
         summary: str          # as the start last said it
         rerun: str | None     # as the start last said it; None when not guided
     ```
   - `prepare_start(..., report: Callable[[StartReport], None] | None = None)`. After `answers.apply(args)`, just before `return args`, it calls `report` once with the selection's names, `keys.baseline`'s id and name, `summary()` and, when guided, `rerun()`. The closures read state that no longer changes, so the texts equal the ones the terminal was last given. Not called on a stop or an error.
   - `onboarding/__init__.py` exports `StartReport`.

2. **The display and the start hook** (`cli.py`).
   - A `RunDisplay` protocol (`event(kind, **fields)`, `__enter__`, `__exit__`) and `DisplayFactory = Callable[[RunStatusTracker, Console], RunDisplay]`.
   - `async def _run(args, *, display: DisplayFactory | None = None) -> str`: with `display` None, today's `LiveUi` / `PlainUi` choice; else `ui = display(tracker, console)`.
   - `async def _run_with_signals(args, *, display=None, on_started: Callable[[asyncio.AbstractEventLoop, asyncio.Task[Any]], None] | None = None) -> str`: calls `on_started(loop, task)` after `_stopped_by.clear()` and before the signal handlers go in (so a stop the hook's owner records is not cleared), then `_run(args, display=display)`.

3. **`main` split** (`cli.py`).
   - `DisplayMode = Literal["plain", "app_startup", "app"]` and `display_mode(args) -> DisplayMode`: `plain` when stdin or stdout is not a terminal (a missing stream counts as not one), or with both `--no-interaction` and `--no-progress`; else `app_startup` with `--no-progress`; else `app`.
   - `_plain_body(args, raw_argv, parser) -> str`: today's `prepare_start(...)` call, unchanged, then `asyncio.run(_run_with_signals(args))`.
   - `_finish(body: Callable[[], str], *, kiln_host: str) -> int`: today's `except` arms and the finish line, moved as they are. `kiln_host` is the parsed `--kiln-host` (the start never changes it).
   - `main`: reset `_RUN_PROGRESS`, parse, validate, `return _finish(lambda: _plain_body(args, raw_argv, parser), kiln_host=args.kiln_host)`. It does not call `display_mode` yet: there is no app to choose.

4. **`plain_exit_line`** (`ui.py`).
   - `totals_suffix(status: RunStatus) -> str`: `" — total <totals_text>"`; `PlainUi._totals_suffix` calls it.
   - `plain_exit_line(fields: Mapping[str, object], status: RunStatus) -> str`: `f"exit: {fields.get('reason')}{totals_suffix(status)}"`. `PlainUi`'s `exit` branch prints `plain_exit_line(fields, self._tracker.snapshot())`.

5. **The start-up's display rules out of `console.py`**.
   - `COLUMN_GAP`, `PICK_HINT` and `aligned_rows` move to `onboarding/terminal.py`, the protocol module that both guided terminals import (Textual-free, so the plain path never loads Textual through it). The `pick` docstring names `PICK_HINT` as the default hint.
   - New package `src/kiln_harness_tuner/tui/` (an `__init__.py` with a docstring only) and `tui/opener.py` with `graphical_browser_expected` (architecture section 3). Neither imports Textual.
   - `console.py` imports the three names back for `RichTerminal`; its module docstring and imports lose what moved.

## Tests

- `test_start_report_names_the_choice_and_repeats_the_last_texts` (`tests/test_onboarding_flow.py`): a guided start with a changed answer; `report` is called once, with the project and task names, the baseline's rc id and name, and the last summary and rerun text the terminal said.
- `test_start_report_of_a_start_that_isnt_guided_has_no_rerun`: summary equals the one said; `rerun` is None.
- `test_no_start_report_when_the_start_stops`: Exit at "Start the run?" and an `OnboardingError` give no call.
- `test_start_report_with_no_baseline_names_none`: a task whose baseline record names no run config gives None for both baseline fields.
- `tests/test_onboarding_keys.py`: the baseline tests assert `rc_id` too.
- `test_display_mode_*` (`tests/test_cli.py`): parametrized over stdin/stdout TTY or not (and a `None` stdin) and the two flags; each gives the mode of architecture section 2.
- `test_run_shows_the_run_on_the_display_it_is_given`: `main` with `_run_with_signals` driven by a display factory; the factory gets the run's tracker and a console, and its display is entered and gets the run's events.
- `test_run_with_signals_hands_its_loop_and_task_to_on_started`: the hook gets the running loop and the main task, before the run starts, and a stop recorded by the hook survives.
- `test_finish_*`: `_finish` with bodies that return or raise each arm's exception gives today's lines and codes (the existing `main` tests keep covering them end to end).
- `test_main_*` existing: unchanged except that `fake_run` takes `**_kwargs`.
- `test_plain_exit_line_is_the_plain_ui_exit_line` (`tests/test_ui.py`): the pure function gives the text `PlainUi` prints for `exit`, with the totals of the status it is given.
- `test_pick_rows_are_aligned_across_options` moves to `tests/test_onboarding_terminal.py`; `test_a_graphical_browser_is_expected_only_where_one_can_show` moves to `tests/tui/test_opener.py`.
- `test_the_plain_path_never_imports_textual`: importing `kiln_harness_tuner.cli` and `kiln_harness_tuner.tui.opener` in a fresh interpreter loads no `textual` module.
