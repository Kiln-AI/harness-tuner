---
status: complete
---

# Phase 7: Docs, packaging and the screenshot pass

## Overview

Phases 1 to 6 built the app and removed `RichTerminal`, questionary and `LiveUi`. The docs still describe the old interface: questionary-shaped menus (`? What next?`, ` » r) …`), "the menu is erased", the rich welcome box, "the live display" that freezes waves into the scrollback, and a rerun command "printed here and nowhere else". This phase makes `docs/` (and the other mentions, `research/codebase/startup_bridge.md` 5.6) describe the app: the start-up screen, the run screen, the stop dialog, Done and the closing block. The wheel smoke test checks that the stylesheet ships and that the app imports from the installed wheel (architecture 13). Last, the real app is shot in every state of the UI preview, at the preview's sizes, and compared with the preview and `ui_design.md`; the differences found are fixed in the app.

The screenshot pass found three differences, which this phase fixes:

1. The Goal data line ("train 312 · held out 208 (val and test) · test never read", 69 cells with its label) is wider than the Goal section at 210 columns (67 cells). It wraps to the section's left edge, under the labels, and the Goal is one row taller than Time & spend.
2. The log does not show its newest line when the run screen opens on a long log, or after its height changes. `RichLog` scrolls to the end only after the next refresh, and the layout then changes the log's height without a scroll, so the test "at the end now" is wrong at both moments.
3. A probe's log line reads "w4-5 Does … output mode?: YES · 3 drives": a colon after the question mark.

## Steps

1. **`tui/screens/run.py`, `tui/tuner.tcss`: the Goal data line.** `goal_content` draws the three role rows only. `GoalSection` gets a data row: a `Horizontal` of the muted label (`"  data"`, the role column's width plus its gap) and the data text in a `Static` of width `1fr`, so a long line wraps under its text, never under the label. `#top > #goal` gets `min-width: 70`, so at the design size (210 columns) the line does not wrap and the Goal and Time & spend are the same height.

2. **`tui/widgets.py`: `RunLog` follows the newest line.**
   - `following: bool`, True at the start. Only a scroll changes it: `watch_scroll_y` sets it to whether the new position is at the end. The reader's scroll up clears it, a scroll back to the end sets it, and the log's own scroll to the end keeps it.
   - `show` writes the new lines and, when following, scrolls to the end. Before its first width it writes nothing; the rewrite that the first width starts writes them all.
   - `on_resize`: a new width rewrites all lines, as now; a height change scrolls to the end when following.
   - `rewrite` keeps `following` across its clear, whose scroll to the top is no reader's.

3. **`tui/model/log.py`: `probe_answered`.** A question that ends with "?" is followed by a space, not ": ".

4. **The screenshot pass: `specs/projects/new-tui/app_screenshots/`.**
   - `sample_run.py`: the preview's story (`ui_preview/sample_data.py`) as loop events, with the builders of `tests/tui/model_events.py`: one function per moment (`starting`, `running`, `gate_open`, `planning`, `done`).
   - `make_screenshots.py`: the real app headless at 210×62 and 120×40, the spinner frame fixed, in each state the preview has a file for: Starting, Planning, Running, Promotion gate, Done, the last wave, the stop dialog, the light theme, the stacked layout and its bottom; and the start-up's picker (dark and light), problem, text answer and summary, from the real `prepare_start` on the onboarding fakes' Kiln. It writes one SVG per state, named as the preview's file of the same state.
   - The SVGs it makes, after the fixes of steps 1 to 3.

5. **Docs** (`research/codebase/startup_bridge.md` 5.6). The renders become the app's, as text: a card is a rounded box with its title in the top border, a menu is the question then one `key) label` row per action, and the highlighted row is named in words.
   - `docs/install.md`:
     - The Kiln-check, agent-CLI and API-key problem renders: problem cards and the `What next?` menu.
     - "Guided or not": a guided start runs in the terminal app; the welcome card, step lines, the spinner line, the problem card, the menu (one key acts; the prompt goes and its step line takes its place); Ctrl-C and Ctrl-D; what prints when the app closes (the closing block); where each part goes (the app on the terminal, the closing block to stdout, errors to stderr). The table of the two flags: `--no-progress` gives plain lines instead of the run screen. Which interface each case gets (functional spec 3), with the two small changes (`FORCE_COLOR`/`TTY_COMPATIBLE` with stdout piped, stdin redirected).
     - "Try the demo": the render of the screen at "Start the run?".
     - "Where the tuner keeps its files": the run line goes to the terminal only without the app; with the app, the closing block names the run folder.
   - `docs/your-own-task.md`:
     - Section 1's readiness render; section 5's welcome and step render; 5.1's picker render ("the list is erased" becomes "the picker goes"); 5.2's advanced-options menu; 5.3's summary render and "printed here and nowhere else" (it also prints in the closing block).
     - A new 5.5 "Watching the run": the run screen's sections, the keys and links, the stop dialog, Done, and the closing block.
     - 5.4 "Stopping a run": `q` or Ctrl-C opens the stop dialog; signals; no report is written on a stop (the code writes none, `orchestrator.py` `interrupted`).
     - The `--no-interaction` and `--no-progress` flag rows name the run screen.
   - `docs/architecture.md`: the topology's display line; 3.1's run-folder line; section 11's display paragraph (the app in `tui/`, `TuiSink`, the model, the plain path's `PlainUi`) and its terminal lines (the run line in the app's stray-output file, the closing block; no `harness-tuner: report:` line, which the code does not print); section 13's layout gets `tui/`; section 15's smoke test paragraph names the new checks.
   - `docs/costs.md`, `docs/agents.md`: "the live display" becomes "the run screen".
   - `AGENTS.md` and `.github/CONTRIBUTING.md`: the layout lists `tui/`.

6. **`tests/packaging/smoke_installed_wheel.py`**: `WHEEL_FILES` gets `kiln_harness_tuner/tui/tuner.tcss`. A new check runs the installed tool's Python: `import kiln_harness_tuner.tui.app`, and the app's `CSS_PATH` is a file in the installed package, not in the checkout. The module docstring says so.

## Tests

- `tests/tui/test_run_screen.py`:
  - `test_goal_shows_the_three_roles_and_the_data_line`: reads the role rows and the data row.
  - `test_the_goal_data_line_wraps_under_its_text`: at 150 columns with a long split line, the wrapped part starts in the text column, and at 210 columns the line is one row.
  - `test_the_log_opens_at_its_newest_line`: a run whose log is longer than the log's height opens with the newest line in view.
  - `test_the_log_follows_after_a_height_change`: after a resize that changes only the log's height, a following log is at its end; a log scrolled up stays where it is.
- `tests/tui/test_model_log.py`: `test_a_probe_line_puts_no_colon_after_a_question_mark`.
- `tests/test_docs.py` stays green (no new flags; the flag rows keep one row each).
- The smoke test runs the new check when CI builds the package.
