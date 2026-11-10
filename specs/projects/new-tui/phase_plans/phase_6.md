---
status: complete
---

# Phase 6: The run screen

## Overview

Phases 3 to 5 built the run model, the app shell and the start-up screen. The run screen is still the phase 4 placeholder: three lines of plain text. This phase builds the real one (functional spec sections 6 to 9, ui_design sections 4 to 7, 9, 10 and 14, architecture sections 5, 7.3 and 8): the title bar, Goal, Time and spend, the run timeline, Results, Experiments in its five modes and Done, the Log and the footer, with the links and the keys. The app looks up the run config names in Kiln. Then `LiveUi`, its renderers and its tests go: no code path uses them once the app shows every terminal run.

The split stays as phase 3 set it: the model gives every word, number and tone; the widgets lay them out, draw the character art (tier bars, the timeline, the judged-item strip) from the numbers, and map tones to theme colors. So the widgets hold no rules of the run, only drawing.

`ui_preview/widgets.py`, `preview_app.py` and `preview.tcss` are the starting point. The widgets read the phase 3 views instead of the preview's sample data.

## Steps

1. **`tui/model/words.py`**: the words the widgets draw around the views: the section names (`GOAL`, `TIME & SPEND`, `RUN TIMELINE`, `RESULTS`, `EXPERIMENTS`, `LOG`), the link and key words (`Compare in Kiln UI`, `Lineage in Kiln UI`, `Report`, `Stop`, `Close`), the timeline legend (`plan`, `screen`, `experiments`, `promotion`, `gate, no winner`, `projected`), and `stopping_header(live) -> Line`: the Experiments header while the run stops ("⠸ Stopping… · cancelling 4 experiments").

2. **`tui/widgets.py`**: the building blocks of ui_design section 4 that the run screen adds.
   - `TONE_STYLES: dict[Tone, str]` (tone → theme style) and `line_content(line, frame) -> Content`: a view's spans as `Content`, the spinner span drawn as the current frame. `has_spinner(line) -> bool`.
   - `Spinning`: the protocol of a widget that holds a spinner. It has the class `-spins` while it does, and `spin()` redraws it with the current frame. The run screen turns every `-spins` widget on one 0.1 s timer.
   - `LineView(Static)`: one `Line`. `show(line)` updates only when the line changed.
   - `SectionHeader(Horizontal)` (4.2): the bold name, muted context and right-aligned meta; `show(context, meta)` takes `Content` or a `Line`, and updates only what changed.
   - `KeyLine(Widget)`: the Results key on its own line; it wraps between entries, never inside one.
   - `CardGrid(Grid)`: as many columns as fit at `min_width` each, with one cell between them; recomputed on resize.
   - `Gauge(Horizontal)` (4.5): label, bold value, a `ProgressBar` with no percentage and no ETA, and the limit. With no total (no budget, or before `run_start`) the bar is hidden and the limit says so.
   - `TierBars(Widget)` (4.4): three rows (Start, Best Confirmed, Best Unconfirmed) from three `TierView | None`: a bar of block characters with 1/8-cell precision, the value right-aligned in one column, the change in the next with its tone. A tier with no value leaves its row empty; Start with no value says "not measured yet". It sizes itself from its own width.
   - `ResultCard(Card)`: one `ConcernView`: the bold title and the muted qualifier, the tier bars, the subtitle (in flight with a spinner, the Best Unconfirmed holder in yellow).
   - `Hypothesis(Widget)`: lines 1–2 of an experiment card, wrapped at the card's width and cut with "…" after two lines.
   - `JudgedStrip(Horizontal)`: line 4 as one cell per judged item (`█` fixed green, `▓` held dim green, `░` still failing dim red, `✗` broke red, `·` queued muted; a cell with no parent result is muted `▒`), then the count. When the items don't fit, a `ProgressBar` replaces the strip.
   - `LabelledBar(Horizontal)`: line 4 as a `ProgressBar` and a muted label ("312 of 520 val items").
   - `CardLineFour(Vertical)`: one row that shows a `StripView`, a `GaugeLine`, a `NoteLine` (its tone) or nothing.
   - `ExperimentCard(Card)`: one `CardView`: hypothesis, steps (4.6: green ✓ done, bold blue current with a spinner, muted `·` later, red ✗ stopped), line 4, line 5, the subtitle (item id and spend), blue border while live. Seven rows in every state. `show(view)` updates only the parts that changed.
   - `run_step_content(step, frame)`: a `StepLine` of the model as a step line (4.7), with a spinner for `busy` and a muted `·` for `skip`.
   - `Timeline(Widget)` (7.3): four rows from a `TimelineView`: the wave labels, the strip (`░` plan, setup and report muted; `▒` screen yellow; `▓` experiments and recon blue; a gate `·` muted, its promotion a green `◆`; `┄` faint after now, up to the limit), the axis with a tick every 30 minutes, and the tick labels with the now marker (`▲ now 15:36`, or `ended 17:10 ▲` at the right edge). A tick label that would touch the marker is left out. Projected waves get faint labels. With no view (before `run_start`) it is blank.
   - `RunLog(RichLog)` (4.10, 7.6): `max_lines=2000`, wrapping. `show(lines)` writes only the lines it has not written. A line is a grid of a muted time, the glyph in its tone (bold for a promotion) and the text (bold when the line is), so a long line wraps under its text. A divider is a faint rule with the wave's name. It follows the newest line only when it is at its end before the write. On a resize and on a theme change it writes all its lines again, at the new width and colors.
   - `link(url, label=None)`: the label (or the address), then "↗"; a click opens the address.

3. **`tui/screens/run.py`**: `RunScreen`, ui_design section 6:
   ```
   TitleBar
   VerticalScroll#body
       Horizontal#top: GoalSection#goal | ClockSection#spend
       TimelineSection#timeline
       ResultsSection#results: SectionHeader (key, Compare link), KeyLine, CardGrid(min 34)
       Container#bottom: ExperimentsSection#experiments | LogSection#log
   Footer
   ```
   - `HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (150, "-wide")]`.
   - `refresh_from(model, *, stopping, now)`: builds the views once and hands each section its own. Each section's update is in its own `try`; a section that fails is logged and keeps its last text (architecture section 14). The screen has the class `-done` in the Done mode.
   - `GoalSection`: the three rows ("▲ optimize" bold green, "═ hold" bold yellow, "○ watch" muted), aligned, and the data line.
   - `ClockSection`: the header with the wave line and the ` LAST WAVE ` tag; the time and spend gauges; the lanes and burn rate; the start and end times.
   - `TimelineSection`: the header with the muted legend, then the `Timeline`.
   - `ResultsSection`: the key sits on the header line when it fits there, else on its own line (measured on each resize and each new key). The Compare link is the meta. One `ResultCard` per concern; the cards are made again only when the concerns change.
   - `ExperimentsSection`: the header (the mode words, or "Stopping…" while the run stops) and the mode's elapsed time; the step lines (Starting and Planning); the subhead; one `ExperimentCard` per item, kept by item id, made again only when the wave's items change; the note; in the Done mode a `RunCompleteCard` in place of the grid. It keeps the bottom area at least as tall as itself, so the screen scrolls and the cards are never cut off.
   - `RunCompleteCard(Card)`: "RUN COMPLETE", how the run ended, the summary, the totals, Best Confirmed, the report (or "no report"), the links (Compare, Lineage, and Report when there is one) and the next step; the run id as its subtitle.
   - `LogSection`: the header and the `RunLog`.
   - Keys: `c` Compare in Kiln UI, `l` Lineage in Kiln UI, `r` Report (shown only once the report exists), `q` Stop (run) or Close (Done), Enter Close (Done, hidden). The links open with `opener.open_link` through the app.
   - Spinners: one 0.1 s timer calls `spin()` on each `-spins` widget of the screen.

4. **`tui/tuner.tcss`**: the run screen styles of `ui_preview/preview.tcss`: sections, the two breakpoints (`RunScreen.-wide` and `RunScreen.-narrow`), the card grids, the gauges and every `ProgressBar`, the experiment card rows, the step lines, the subhead and note, the run complete card, the log. The placeholder's rules go.

5. **`tui/app.py`**:
   - `TunerApp(..., clock=time.monotonic)`: the clock the views read (`now`), injectable so tests are deterministic.
   - `_refresh`: the screen is shown again when the model changed, the stop began, or the clock moved to a new second (the elapsed times and the gauges move without events). The widgets still update only what changed.
   - `Session.lookup_names: Callable[[RunInfo], Awaitable[Iterable[Mapping]]] | None`. After each drain, when `model.names.lookup_due(now)`, the app starts one async worker on its own loop (architecture section 8): it calls the lookup, then `model.learn_names(rows)`; a failure is logged and `names.lookup_failed()` keeps the ids. Never more than one at a time, and at most once every 10 s (`RcNames`).
   - `open_report()`, `open_compare()`, `open_lineage()` for the screen's keys and links.

6. **`tui/entry.py`**: `run_config_rows(info)`: `GET /api/projects/{p}/tasks/{t}/run_configs` with its own `KilnClient`, no retry ladder and a short timeout; the rows that are objects. The session gets it as `lookup_names`.

7. **Remove `LiveUi`** (`research/codebase/startup_bridge.md` 5.2, 5.3, 5.5):
   - `ui.py`: `LiveUi`, `_ActiveView`, the styling and wrapping layer (`style_line`, `style_block`, `wrap_line`, `wrap_block`, `_hanging_indent`, `_break_offsets` and their patterns), `spinner_frame`, `progress_bar`, `item_line`, `item_lines`, `render_header`, `render_wave_plan`, `render_wave_items`, `render_wave`, `_banners`, `render_active_wave`, `render_phase_line`, `render_totals_row`, `RULE` and the constants only they read, and the dead imports. `PlainUi` and the helpers it and `report.py` use stay. The module docstring describes `PlainUi` only.
   - `cli.py`: `_run` builds `PlainUi` when it is given no display. The docstring and the comments name the app, not the live display.
   - `status.py`, `logbook.py`: the comments that name `rich.live`.
   - Tests: the `LiveUi` and renderer tests of `tests/test_ui.py` (the `render_totals_row` tests become `totals_text` tests); the `LiveUi` halves of `tests/test_setup_speed.py` and `tests/test_detectability.py`; `tests/test_cli.py`'s display-choice test asserts the plain display with and without `--no-progress`, and its fakes stop patching `cli.LiveUi`.

## Tests

- `tests/tui/run_screen.py` (helper): a pilot app over a `Run` of `model_events.py`, with the clock on the run's own clock, and `refreshed(pilot)`: drain, then wait for the screen.
- `tests/tui/test_run_screen.py` (pilot, `run_test(size=(210, 62))`):
  - `test_title_bar_names_the_task_the_run_and_the_slots`.
  - `test_goal_shows_the_three_roles_and_the_data_line`, for quality and speed.
  - `test_time_and_spend_show_two_gauges_lanes_and_the_wave`; with no budget the spend bar is hidden; the LAST WAVE tag shows only in the wrap-up wave.
  - `test_timeline_draws_setup_waves_a_promotion_and_the_now_marker`: the strip's glyphs and labels, the axis ticks, `▲ now`, and `ended … ▲` at the end.
  - `test_results_show_one_card_per_concern_with_three_tiers`: bars, values, changes, the in-flight subtitle, Best Unconfirmed's holder; "not measured yet" for a cost card before the first arm.
  - `test_the_results_key_moves_under_the_header_when_it_does_not_fit` (210 and 120 columns).
  - `test_starting_mode_shows_the_checklist_and_the_recon_cards`.
  - `test_planning_mode_shows_the_planner_steps_and_the_last_wave_cards`.
  - `test_running_mode_shows_one_card_per_item_in_order`: titles, hypothesis cut to two lines, steps, the strip and "so far", the subtitle; a card keeps its place when it lands.
  - `test_the_strip_turns_into_a_gauge_when_the_items_do_not_fit`.
  - `test_gate_mode_shows_val_progress_and_the_gate_verdicts`: "At the gate" progress, then promoted and rejected words.
  - `test_done_mode_shows_the_run_summary_with_its_links`: how it ended, totals, Best Confirmed, the report, the three links, the next step; the footer has `c`, `l`, `r`, `q`.
  - `test_the_log_writes_new_lines_only_and_follows_the_end`: dividers and lines, nothing written twice; a reader scrolled up stays where they are.
  - `test_stopping_shows_in_the_experiments_header`.
  - `test_the_keys_open_compare_lineage_and_the_report`: `c`, `l` and `r` open the model's URLs and the report path; `r` shows only once the report exists.
  - `test_a_link_click_opens_its_address`.
  - `test_unchanged_views_update_no_widget`.
  - `test_a_failing_section_keeps_its_last_text_and_the_rest_refresh`.
  - `test_the_narrow_layout_stacks_the_sections` (120×40): Goal over Time and spend, the Log under Experiments; nothing cut off.
  - `test_light_theme_renders_every_mode`: no error with `kiln-light`.
- `tests/tui/test_app.py`: the phase 4 tests read the new widgets (the title bar, the Experiments header, the log); the footer has `c`, `l`, `q` during the run and `c`, `l`, `q` Close on Done; `test_run_config_names_are_looked_up_once_at_a_time_and_learned`; `test_a_failed_name_lookup_keeps_the_ids_and_the_app_running`.
- `tests/tui/test_entry.py`: `test_run_config_rows_reads_the_task_run_configs` with a fake Kiln transport.
- `tests/test_ui.py`, `tests/test_cli.py`, `tests/test_setup_speed.py`, `tests/test_detectability.py`: as in step 7; `PlainUi` output unchanged.
