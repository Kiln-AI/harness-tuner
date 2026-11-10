---
status: complete
---

# Phase 1: Loop data additions

## Overview

The new TUI shows numbers the loop computes but does not emit today. This phase adds the seven approved additive changes (architecture section 9; schemas in `research/codebase/data_contract_audit.md` section 2, with functional spec 16.1 for the chunk cells). Each change is a new event kind or new fields on an event. No change alters what the loop does. `PlainUi` and the loop's own `RunStatusTracker` are not changed, so the plain output and the report stay the same. Every new payload is a fresh, JSON-safe dump, built on the loop thread at emit time.

## Steps

1. **C1a `arm_estimates`** (`measure.py`, `items.py`).
   - `measure.arm_estimates(m: Measured, item_id: str, parent_rc_id: str) -> dict[str, Any]`: a pure function that returns the event's fields. `per_eval` has one row per eval in `m.per_eval` (`eval_id`, `eval_name` from `m.evidence.eval_names`, `net`, `ci_low`, `ci_high`, `rate`, `n`, `complete`, `judged`, `planned` from `m.planned_per_eval`, `fixed`, `broke` from `m.evidence.fixed_broke(eval_id)`). `pooled` is `{net, ci_low, ci_high, n}` from `m.estimate`, or None. `latency`, `cost`, `latency_vs_parent`, `cost_vs_parent` are `model_dump(mode="json")` or None. `spend_usd`, `cap_usd` (`m.budget.usd` or None), `status`, `stop_kind`.
   - `items._measure_arm`: `ctx.log.event("arm_estimates", **arm_estimates(measured, item.id, parent_rc_id))` right after the `measurement` event. Every arm gets it, stopped, capped and failed arms included.

2. **C1b `arm_results`** (`items.py`).
   - In `_execute_plan_item`, after `bundle.latency` and `bundle.cost` are set: `ctx.log.event("arm_results", item_id=, rc_id=, parent_rc_id=, per_eval=[row.model_dump(mode="json")], latency=, cost=, errors=[e.model_dump(mode="json")])`.
   - A small shared helper `schemas.dump_or_none(model: BaseModel | None) -> dict | None` for the optional dumps (used by C1a, C1b).

3. **C3 `measure_chunk` fields** (`measure.py`).
   - `_State` gets `reported_n: int = 0`: the judged rows already sent in a chunk event.
   - `measure.chunk_counts(ev: Evidence, since: int) -> dict[str, Any]`: pure. `judged`, `fixed`, `broke`, and `cells: [[eval_id, item_id, parent_pass, cand_pass]]` for `ev.judged[since:]`, in judging order. Under speed and cost also `lost`, `guards`, `fixes`, `fails` from `ev.guard_losses()`.
   - At the `measure_chunk` emit: add `**chunk_counts(s.ev, s.reported_n)`, `planned=sum(own_per_eval(s).values())`, `spend_usd=s.spend`, `cap_usd=s.budget.usd` (or None), `decision=decision`. Then `s.reported_n = s.ev.judged_n`. The `line` field is unchanged.

4. **C2 `gate_val_tables`** (`orchestrator.py`).
   - `_emit_val_tables(ctx, baseline_rc_id, tables)`: `{baseline_rc_id, objective, tables: [t.model_dump(mode="json")]}`.
   - `_run_promotion_gate`: under quality, call it after `_record_gate_blocks`; under speed and cost, after the second `write_val_summary`. The tables are final at both points.

5. **C4 `gate_fills_planned`, `gate_fill`** (`orchestrator.py` `_assemble_val_tables`).
   - Before the fill tasks start: `gate_fills_planned {baseline_rc_id, rc_ids (the candidates), eval_ids (the gate's evals), fills: len(fill_keys)}`.
   - Inside `fill()`: a closure counter `done` counts every finished fill, a failed one too (that one keeps its `val_fill_failed` event and gets no `gate_fill`). After `ensure_measured` returns: `gate_fill {rc_id, eval_id, role: "baseline" | "candidate", stored: pairable_item_count(runs), expected: health.n_expected, missing: len(health.missing_item_ids), clean: health.clean, done, total}`.

6. **C5 item details at wave start** (`status.py`).
   - `item_details(item: BatchItem) -> dict[str, Any]`: every key on every item, None where it does not apply to the item's type: `type`, `hypothesis`, `technique`, `issue_memory_id`, `wildcard`, `needs_eval`, `eval_id`, `targets: [{eval_id, scores}]`, `promotion_candidate`, `max_usd`, `merge_wins: [{experiment_memory_id, rc_id}]`, `probe: {question, max_drives, for_idea, code}`, `remeasure: {rc_id, eval_ids}`, `measure: {eval_id, scores}`, `parent_rc_id`, `existing_rc_id`. All lists are new lists (the loop changes the item's own lists during the wave).
   - `item_summary(item) -> dict[str, Any]` returns `item_id`, `kind`, `label` (unchanged) plus `item_details(item)`.

7. **C6 `open_questions`** (`orchestrator.py`).
   - `_emit_open_questions(ctx, wave, phase, questions, ids)`: skipped when the list is empty. `{wave, phase: "plan" | "replan" | "rescreen", questions: [{question (canonical form, as stored), default_assumed, gates, reask_when, constraint_id}]}`.
   - The three `write_open_unknowns` calls bind the returned ids and call it after the write.

8. **C7 memory records** (`gateway/ops.py`, new `memory_feed.py`, `dispatch/claude_cli.py`, `orchestrator.py`, `cli.py`).
   - `gateway/ops.py`: `MEMORY_FILE = "gateway_memory.jsonl"` beside `CREATED_FILE`. `State.record_memory(op, view: m.MemoryView)` appends `{"ts", "op", "id", "role", "scope", "tags", "overview"}` (no content) with one `os.write` on an `O_APPEND` fd, and ignores `OSError`. `memory_create` and `memory_update` call it after the Kiln call returns.
   - `src/kiln_harness_tuner/memory_feed.py`: `class MemoryFeed(path)`, `forward(log, forwarded_by) -> int`. It keeps a byte offset, reads only complete lines, skips a line that is not a JSON object, and emits `memory_written {id, op, role, scope, tags, overview, kind (record_kind(tags, overview).value), written_at, forwarded_by}` per line. A missing or unreadable file forwards nothing. It never raises.
   - `ClaudeCliDispatcher`: attribute `on_session_end: Callable[[str, str], None] | None = None`; `_run` calls it with `(agent, label)` after `_persist_step`, inside `try/except Exception` (logged).
   - `orchestrator.Ctx`: `memory_feed: MemoryFeed | None = None`. `_ReconcileBeforeExit.event` forwards once more after `reconcile`, before the first `exit` goes out, with `forwarded_by="exit"`.
   - `cli._run`: after `ctx.analyzer.log = ctx.log`, build `MemoryFeed(run_dir / MEMORY_FILE)`, set `ctx.memory_feed`, and set `ctx.dispatch.on_session_end = lambda _agent, label: feed.forward(ctx.log, label)` (reads `ctx.log` at call time).
   - `cli.RUN_DIR_ENTRIES` gets `"gateway_memory.jsonl"`.

## Tests

New file `tests/test_loop_events.py`, plus additions to the existing suites where their fixtures live.

- `test_arm_estimates_fields_for_a_completed_quality_arm`: a full loop under quality; one `arm_estimates` with the item and rc ids, per-eval row numbers that match the `measurement` event's net and the judged/planned counts, a pooled row, `cap_usd` set.
- `test_arm_estimates_for_a_stopped_arm`: the futility case; the event comes with `status="stopped"` and `stop_kind="futility"`, and no `arm_results`.
- `test_arm_estimates_carry_the_paired_timing_under_speed`: `latency` and `cost` dumps are present (plain dicts).
- `test_arm_estimates_is_pure`: `arm_estimates` on a hand-built `Measured` gives the expected dict, with None for missing estimates and comparisons.
- `test_arm_results_dumps_the_bundle`: one `arm_results` after the analysis bundle, with `PerEvalResult` dumps (`baseline_score`, `candidate_score`, `delta`, `ci_low`, `ci_high`), errors as dicts.
- `test_measure_chunk_counts_and_cells`: each chunk event has `judged`, `planned`, `fixed`, `broke`, `spend_usd`, `cap_usd`, `decision`; the cells of all chunks together are the judged rows once each, in order; `judged` equals the running count of cells.
- `test_measure_chunk_guard_counts_under_speed`: `lost`, `guards`, `fixes`, `fails` present under speed, absent under quality.
- `test_chunk_counts_is_pure`: `chunk_counts` on a hand-built `Evidence` with `since`.
- `test_gate_val_tables_once_per_gate_under_quality` and `..._under_speed`: one event per gate, with the incumbent id, the objective, and table dumps (the speed table carries its paired `latency`).
- `test_gate_fills_planned_then_one_gate_fill_per_fill`: `fills` equals evals × (candidates + 1); each `gate_fill` has the right role, `stored`, `clean`, and `done` runs 1..total.
- `test_a_failed_val_fill_counts_toward_done`: a failed fill gives `val_fill_failed`, no `gate_fill`, and the other fills' `done` still reaches `total`.
- `test_wave_start_items_carry_their_details`: in `tests/test_status.py`, `item_summary` for a fix, a merge, an eval-only, a measure, a remeasure and a probe item; the new keys, None where they do not apply; the lists are copies.
- `test_open_questions_after_each_write`: a plan with open questions gives one `open_questions` event with the written constraint ids; an empty list gives none; a replan gives `phase="replan"`.
- Gateway (`tests/test_gateway.py`): `memory_create` and `memory_update` append one line each to `gateway_memory.jsonl` with the role and no content; an unwritable file does not fail the write.
- `MemoryFeed` (`tests/test_loop_events.py`): forwards new complete lines only, keeps its offset across calls, leaves a partial last line for the next call, skips bad lines, sets `kind` and `forwarded_by`, and a missing file forwards nothing.
- Dispatcher (`tests/test_dispatch_claude_cli.py`): `on_session_end` is called after the step file is written, with `(agent, label)`; a hook that raises does not fail the dispatch.
- `test_memory_lines_are_forwarded_before_the_first_exit`: `_ReconcileBeforeExit` forwards pending lines before the `exit` event, once.
- `test_run_dir_entries_include_the_memory_file` (`tests/test_cli.py`): `prepare_run_dir` makes `gateway_memory.jsonl`.
- `test_plain_ui_prints_the_same_with_and_without_the_new_data`: the events of a full loop replayed into `PlainUi` twice, once as emitted and once with the new kinds and the new fields removed, print the same text.
