---
status: complete
---

# Phase 3: The run model, pure Python

## Overview

The run screen of phase 6 shows the run from the loop's events. This phase builds the part that has no Textual in it: `tui/model/`. It takes the stamped events of the run, folds them into one `RunModel`, and builds one view per section of the run screen (functional spec sections 6 and 7, with 16; ui_design sections 7 and 14; architecture sections 7 and 8).

The split between the model and the widgets: the model gives every word and number the screen shows, already worded and formatted, with a tone where color carries a meaning. The widgets of phase 6 only lay them out, draw the character art (bars, strip, timeline) from the numbers in the views, and map tones to theme colors. So every rule of the run screen is tested here, with no terminal.

Nothing in `tui/model/` imports Textual, and nothing outside `tui/` imports `tui/model/` yet. The plain path is not changed.

## Steps

1. **`tui/model/__init__.py`**: a docstring. The package is pure Python.

2. **`events.py`**: the queue item.
   ```
   @dataclass(frozen=True)
   class Stamped:
       kind: str
       fields: Mapping[str, Any]
       mono: float        # time.monotonic() on the loop thread, at enqueue
       wall: datetime     # datetime.now() at the same moment
   ```

3. **`tracker.py`**: `UiTracker(RunStatusTracker)`.
   - The clock is the stamp of the event being folded, and the live clock (`time.monotonic` by default) at other times (audit 3.3).
   - `fold(stamped: Stamped) -> None` sets the stamp, calls `event(kind, **fields)`, clears the stamp.
   - `status` property: the folded `RunStatus` itself, not a copy. Fold and read are on the UI thread, so the model reads it without `snapshot()`'s deep copy.
   - No handler of the base class changes. The model reads from it the run-level money: total spend, spend by lane, unpriced tokens, the agent CLI's lane label.

4. **`state.py`**: the dataclasses.
   - Tones and spans, the one styling contract with the widgets:
     ```
     Tone = Literal["fg", "bold", "muted", "faint", "primary", "success",
                    "error", "warning", "promoted", "spinner"]
     @dataclass(frozen=True)
     class Span: text: str; tone: Tone = "fg"
     Line = tuple[Span, ...]
     def plain(line: Line) -> str
     ```
     A `spinner` span stands for the spinner frame; its text is empty.
   - The fold's state (mutable, owned by `RunModel`): `Concern` (key `(eval_id, score_key)` or `("cost", "")` / `("latency", "")`, name, kind `pass | rating | cost | latency`, direction), `ItemModel` (the C5 details of its `wave_start` entry, state and its stamp, builds and the rebuild reason, the last detail line, chunk counts and cells, rc id, spend, outcome, probe answer, train rows, cut-off, gate result), `WaveModel` (wave, probe phase, wrap-up, parent rc, items in plan order, planner steps), `GateModel` (candidates, fills, tables, decision, blocks, result), `Segment` (kind, wave, start and end in seconds from the run's start, mark), `PlanStep`.
   - The views (frozen): `TitleView`, `GoalView`, `GaugeView`, `ClockView`, `TimelineView`, `TierView`, `ConcernView`, `ResultsView`, `StepLine`, `CardView` with `StripView | GaugeLine | NoteLine` for line 4, `ExperimentsView`, `DoneView`, `LogLine`.

5. **`words.py`**: every user-facing string of the run screen.
   - Formats: `duration(secs)` ("38s", "3m10s", "1h24m"), `money(usd)` ("$21.40"), `clock(dt)` ("14:12"), `value_text(kind, value)` ("70.2%", "4.08", "$0.042", "8.4s"), `change_text(kind, change)` ("+9.6" points, "+0.12", "−8%"), with the "−" minus sign.
   - The verdict table: `Verdict(card, log, tone, glyph)` per outcome key, from functional spec 7.6 and 16.7, 16.10, and the card short forms of ui_design 7.5. Stop kinds with no row in the spec (`invalid_output_rate`, `max_items`, an unknown kind) get plain words of their own. `log_verdict(text)` turns a verdict into the log's form ("Stopped: can't beat its parent" → "stopped, can't beat its parent").
   - `how_it_ended(reason)`: the exit reasons in plain words (functional spec 7.6 Done, 16.14).
   - The goal rows per objective (functional spec 7.2 with 16.2), the data line (16.4), the section headers' words, the step labels, the log's glyph table (ui_design section 5), the stop dialog's question.

6. **`links.py`**: `compare_url(host, project_id, task_id, rc_ids)` (Kiln's rule: no repeats, the first 10, `columns` from 2 to 10, `&from=optimize`) and `lineage_url(host, project_id, task_id, selected=None)` (functional spec 7.8).

7. **`names.py`**: `RcNames` (architecture section 8).
   - `RcNames(seed: Mapping[str, str])`: the baseline's name from `StartReport`.
   - `see(rc_id)`: an unknown id makes the dictionary stale, once per id.
   - `lookup_due(now) -> bool`: stale, no lookup running, and 10 s since the last one started. `lookup_started(now)`, `learn(rows) -> bool` (the rows of `GET …/run_configs`: `id`, `name`, `description`), `lookup_failed()`.
   - `name(rc_id)`, `description(rc_id)`, `label(rc_id, item_id)`: the name, with the item id beside it when the rc was built this run ("Hold fields wrapper · w3-4"); before the name is known, the item id or the rc id.
   - The app's worker that runs the lookup is phase 6.

8. **`fold.py`**: `RunModel`.
   ```
   class RunModel:
       def __init__(self, info: RunInfo, *, started: float, names: RcNames): ...
       def apply(self, stamped: Stamped) -> None   # tracker first, then the model
       def agent_cli_checked(self, at: float) -> None
       def note_ui_error(self) -> None
       version: int
   ```
   - `RunInfo` (frozen, in `state.py`): the CLI facts (run id, project and task ids and names, Kiln host, agent CLI kind and command, slot counts, objective). `update_info(**changes)` replaces it (the run id is known only once `_run` makes it).
   - One `_on_<kind>` handler per event kind the run screen reads, each tolerant of missing fields. Unknown kinds are ignored. `version` goes up on every applied event.
   - The rules of architecture 7.2 live here and in the modules below:
     - **Concerns** from the `detectability` rows with `promotion_driving`, in order, then cost and latency; an eval first named by a C1b or C2 row is added before cost.
     - **Promotion confirmed**: a `promotion` with a winner counts only when the gate's `phase_end promotion_gate` arrives with no `promotion_blocked` for that rc in between. Each confirmed promotion starts a new generation of Best Unconfirmed.
     - **Gate results per candidate**, settled at the gate's end: promoted, blocked (`gate_block`, `promotion_blocked`), held (`decision.blocked_on`), skipped (`promotion_skipped`), else rejected. A rejected rc never counts for Best Unconfirmed again.
     - **Cut off**: an item named by `deadline_cancel` or `fatal_cancel` with no outcome.
     - **Rebuild**: an item that enters `building_rc` a second time; the reason is the last check finding (`judge_copy` copied, `generality` leaked, `reference_check` found).
     - **Mode**: Starting until the first planning phase, then Planning, Running (from `wave_start`), Promotion gate (the gate phase), Done (from `exit`).
     - **Waves and cycles**: a main wave's cycle runs from its first planning start to the next wave's; its cost is the spend between them.
   - View methods: `title()`, `goal()`, `clock(now)`, `timeline(now)`, `results()`, `experiments(now)`, `compare_url()`, `lineage_url()`, `live_count()`, and `log` (the list the Log section appends from).

9. **`results.py`**: concerns and the three tiers (functional spec 7.5, 16.3, 16.6, 16.12, 16.13).
   - `concerns_from(report: DetectabilityReport) -> list[Concern]`.
   - Values are raw (a pass rate as a fraction, a rating's mean, cost in USD per request, latency in ms). "Better" follows the concern's direction; cost and latency are lower-is-better.
   - **Start**: train pass rates from the detectability rows ((n − room) ÷ n, on the raw scale); rating, cost and latency from the first C1b row whose parent is Start ("not measured yet" until then); latency under speed and cost from `baseline_speed`. From the first gate's `gate_val_tables` on: the incumbent's val values.
   - **Best Confirmed**: the confirmed winner's table values (`candidate_score`; cost and latency from the table's paired comparison, else from the row comparison with the most items). Its change against Start is green or red by direction only.
   - **Best Unconfirmed**: among this generation's successful arms (C1b rows) and screen survivors (cost and latency only), not rejected at val, the best of parent value + change (cost and latency: parent value × candidate ÷ baseline median), where the parent value is Best Confirmed's (Start's before the first promotion). Shown only where it beats that parent value. Dim when the row's interval crosses zero (cost and latency: not `clearly_faster`).
   - **In flight**: live items whose C5 targets name the concern's eval; None for cost and latency.
   - The bar fraction per tier (pass rate on 0–1, rating on 1–5, cost and latency against Start at 0.8).
   - `results_view(model) -> ResultsView`: the key's three entries (Start's split, Best Confirmed's name or "no promotion yet"), one `ConcernView` per concern (the score key in the title only for an eval with more than one score), and the rc ids for Compare (Start, Best Confirmed, each distinct Best Unconfirmed holder).

10. **`cards.py`**: one `CardView` per item (functional spec 7.6, ui_design 7.5 and 14).
    - Title and qualifier by type: fix (target concern · technique), merge ("All evals · merge", "Combine … on top of …"), probe ("Question · probe"), eval build, issue baseline measurement, re-measure.
    - Steps by type and state; the gate stage for a candidate at or after the gate.
    - Line 4: queued, building ("an agent is writing the change · 3m10s", or "rebuilding once · …" in warning), the judged-item strip (cells fixed, held, failing, broke, unknown; planned total), val progress at the gate ("312 of 520 val items", from C4 with the detectability `val_n` before a fill lands), probe drives, "measuring · 3m10s" for the two measure types.
    - Line 5: fixed and broke (so far while measuring), the verdict, the gate's result, "Cut off: the run ended".
    - Subtitle: the item id and its total spend. Live while in progress or at the gate.
    - `verdict(model, item) -> Verdict | None`.

11. **`timeline.py`** (functional spec 7.3, 7.4; ui_design 7.2, 7.3).
    - Segment kinds: `setup`, `recon`, `plan`, `screen`, `items`, `gate` (mark `promotion` or `no_winner`), `backoff`, `report`. A screen segment runs from the planning end before it (or its first screening event) to its `screening_round`; an items segment from `wave_start` to its last outcome, the gate's start or `wave_end`.
    - `projected_waves(...) -> list[tuple[float, int]]`: the waves the run can still start, by the loop's own rules (`MIN_WAVE_START_SECS`, the wrap-up window `max(WRAP_UP_WINDOW_SECS, mean cycle)`, the budget against the mean cycle cost), from the current cycle's projected end. "wave N of ~M" is N plus their count, so the timeline's projected labels and the header agree. Nothing before the first finished cycle; nothing in the wrap-up wave.
    - `axis_ticks(limit_secs)`: every 30 minutes, and the limit.
    - `timeline_view(model, now) -> TimelineView`.

12. **`log.py`**: `lines_for(model, stamped) -> list[LogLine]`, called by `apply` after the fold, for the events of functional spec 7.7 only. A divider at the run's start and at each wave's first planning. Promotions bold. Assumed answers once per constraint id. Memory records of kind lesson, dead end and (new) issue. Gate lines (promoted with the target's val change, rejected with train and val changes, held with its question, blocked, no winner) at the gate's end.

## Tests

All in `tests/tui/`, offline, with no Textual. A helper module `tests/tui/model_events.py` builds events with the real emitters' field shapes: real `BatchItem`s through `status.item_summary`, real `DetectabilityReport`, `ItemOutcome`, `PromotionDecision` objects, `PerEvalResult` and `CandidateValTable` dumps, and a clock that stamps each event.

- `test_model_basics.py`
  - `test_ui_tracker_folds_on_the_event_stamp`: phase and item timings come from the stamps; between events the live clock is used.
  - `test_ui_tracker_status_is_not_a_copy`.
  - `test_unknown_and_malformed_events_are_ignored_and_bump_the_version`.
  - `test_title_and_goal_per_objective`: the three rows for quality, speed and cost; the data line with train and held-out counts.
  - `test_clock_view_gauges_lanes_and_times`: elapsed against the limit, spend against the budget, no gauge end with no budget, lanes named for the agent CLI with unpriced tokens, burn per hour, started and ends.
  - `test_note_ui_error_logs_once`.
- `test_model_results.py`
  - `test_concerns_follow_the_detectability_rows_then_cost_and_latency`; a multi-score eval names its score keys; a mid-run eval from a C1b row is added before cost.
  - `test_start_is_train_until_the_first_gate_then_val`; rating, cost and latency are "not measured yet" until a C1b row with Start as its parent.
  - `test_promotion_confirmed_only_when_the_gate_ends_unblocked`; `test_a_blocked_winner_is_not_best_confirmed`.
  - `test_best_confirmed_change_is_colored_by_direction_only`.
  - `test_best_unconfirmed_counts_only_successful_arms_and_beats_the_parent`; screen survivors count for cost and latency only; dim when the interval crosses zero.
  - `test_best_unconfirmed_clears_at_a_confirmed_promotion_and_drops_rejected_rcs`.
  - `test_lower_is_better_concerns_compare_the_right_way`.
  - `test_in_flight_counts_live_items_on_the_eval_and_none_for_cost_and_latency`.
  - `test_compare_rc_ids_are_start_confirmed_then_unconfirmed_without_repeats`.
- `test_model_cards.py`
  - Card views for each variant of ui_design 7.5: queued, building, rebuilding once, measuring with the strip and "so far", analyzing, landed success and "goes to the gate", at the gate with val progress, promoted, rejected, held, blocked, merge, probe asking and answered (YES, NO, UNCLEAR with drives), eval build, issue baseline measurement (reproduced and not), re-measure, cut off.
  - `test_verdict_words_for_every_stop_kind`: each row of the verdict table, card and log forms.
  - `test_dead_end_reason_comes_from_the_last_detail`.
  - `test_experiments_modes`: Starting (checklist and recon cards), Planning (planner steps, screen rounds, re-plan, last wave's cards under the subhead), Running (header counts), Promotion gate, Done (the run summary: how it ended, totals, Best Confirmed, report or "no report", next step).
- `test_model_timeline.py`
  - `test_segments_from_phase_wave_screen_and_gate_events`: setup with recon inside, plan, screen, items ending at the last outcome, gate with a promotion mark and with a no-winner mark, report.
  - `test_projection_mirrors_the_loop_rules`: the floor, the wrap-up window, the budget; none before the first finished cycle; none in the wrap-up wave; the header's "wave N of ~M" equals N plus the projected waves.
  - `test_axis_ticks_every_30_minutes`.
- `test_model_log.py`
  - One test per log line family: run start and exit, wave start with its parent, built, rebuild findings, verdicts, screening rounds, probe answers, assumed answers (once per constraint), health lines, memory records (lesson, dead end, new issue; other kinds left out), gate results at the gate's end, cut-offs, dividers.
  - `test_item_state_and_chunk_events_add_no_log_lines`.
- `test_model_links_names.py`
  - `test_compare_url_*`: 1, 2 and 11 ids, repeats removed, `columns` from 2 to 10.
  - `test_lineage_url_with_and_without_a_selection`.
  - `test_rc_names_*`: seed, stale once per unknown id, the 10 s rule, learn, a failed lookup, labels before and after the name is known.
- `test_model_replay.py`
  - `test_a_real_loop_run_folds_into_sensible_views`: the events of a full `orchestrate` run of the test suite (the quality run of `tests/test_loop_events.py`) applied in order; every view builds; the card's verdict, the promotion, Best Confirmed and the Done summary match what the run did.
