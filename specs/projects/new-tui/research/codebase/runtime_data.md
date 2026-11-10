# Runtime data inventory for the new TUI

Repo: `/home/user/harness-tuner`, branch `claude/sharp-volta-sdgwzg` at `2a9f72c`.
That is `origin/main` (`f33d877`, "Merge pull request #7") plus two spec-only
commits, so the code below is current main. All paths are under
`src/kiln_harness_tuner/` unless they say otherwise. No repo file was changed.

Classification used in section 4:

- **(a)** emitted in an event today (and whether `RunStatusTracker` folds it)
- **(b)** computed in the loop, not emitted (the variable or function is named)
- **(c)** derivable by the display from events, run files, the project on disk, or a Kiln read
- **(d)** not available at all

---

## 1. Pipeline: from the loop to the display

### 1.1 Start-up and where the UI hooks in

| Step | Where |
|---|---|
| `main()` parses args, runs the synchronous onboarding (`prepare_start`: prompt_toolkit prompts plus short `asyncio.run` calls for Kiln reads) | `cli.py:1433-1447`, `onboarding/flow.py:1-10, 91-100` |
| `asyncio.run(_run_with_signals(args))`: the only long-lived event loop | `cli.py:1448` |
| SIGTERM/SIGHUP handlers: `loop.add_signal_handler(sig, on_stop)` cancels the main task. SIGINT is Python's default (KeyboardInterrupt, which `asyncio.run` turns into a main-task cancel) | `cli.py:1331-1368`, `1459-1473` |
| `_run`: state folder, agent install, `check_agent_cli` (an agent session, BEFORE any UI exists), run lock, run id, run dir | `cli.py:1175-1208` |
| `print("harness-tuner: run <id> — files in <dir>/")` to stdout, before the display | `cli.py:1210` |
| `configure_run_logging(run_dir)`: root logger to a FILE only | `cli.py:1215`, `logbook.py:72-104` |
| `build_ctx(...)` | `cli.py:1261-1289`, `959-1037` |
| `tracker = RunStatusTracker(project_id, task_id, guidance, clock=ctx.clock, agent_cli=agent_cli.kind)` | `cli.py:1293-1299` |
| `ui = LiveUi(tracker, console=console) if not args.no_progress and console.is_terminal else PlainUi(...)` | `cli.py:1300-1306` |
| `with WaveLog(run_dir / "wave_log.jsonl") as wave_log, ui:` | `cli.py:1307` |
| `ctx.log = FanoutLog(wave_log, tracker, ui, ctx.spend)`: **the hook point** | `cli.py:1310` |
| `ctx.analyzer.log = ctx.log` (the analyzer emits too) | `cli.py:1313` |
| `ReportWriter(tracker, run_id, state.reports)` becomes `ctx.write_report` / `ctx.write_fallback`. It reads `tracker.snapshot()` | `cli.py:1314-1319`, `report.py:885-992` |
| `return await orchestrate(ctx, args.guidance, args.timeout_mins)` | `cli.py:1320` |
| After the display context exits: `print("run finished — report: …")` or an error screen / stderr lines | `cli.py:1426-1483` |

A new TUI is a drop-in replacement for the `ui` object at `cli.py:1302-1306`.
That is the composition root, not the core loop. The UI protocol is a context
manager plus `event(kind: str, **fields) -> None`.

### 1.2 The EventLog API

- Protocol: `EventLog.event(kind, **fields) -> None` (`orchestrator.py:200-208`; `NullLog` is the default on `Ctx`, `orchestrator.py:251`).
- `WaveLog.event` writes `{"ts": datetime.now().isoformat(), "kind": kind, **fields}` and flushes after every line. Pydantic models are dumped with `model_dump(mode="json")`, and any other non-JSON value falls back to `repr` (`wave_log.py:22-50`).
- `FanoutLog.event` forwards to every child in order, synchronously, **with no try/except** (`wave_log.py:65-75`).
- Inside `orchestrate`, `ctx.log` is wrapped by `_ReconcileBeforeExit`. Before the first `exit` event it runs `reconcile()` synchronously, which reads the project on disk and may book a `spend` event first (`orchestrator.py:428-455`).
- The `EvalJobDriver` captures `ctx.log` when it is created (`job_drives.py:435-440`, `245-247`).

### 1.3 Consumers

There are four consumers in the fan-out, in this order (`cli.py:1310`):

1. `WaveLog` writes the audit JSONL.
2. `RunStatusTracker` folds the event into `RunStatus` (`status.py:143-625`). It dispatches to `_on_<kind>` and ignores unknown kinds (`status.py:190-194`).
3. The UI (`LiveUi` or `PlainUi`, `ui.py:734-992`). It runs after the tracker, so it renders the post-update state.
4. `SpendMeter` sums `spend` events into `total` and `provider_total`, for the budget checks and reconciliation (`orchestrator.py:211-231`).

Other readers: `ReportWriter` (calls `tracker.snapshot()`) and `_RUN_PROGRESS` (`run_dir`, `spend`, `report_path`; `cli.py:1172, 1208, 1290, 1315`).

### 1.4 Sync vs async, threads, refresh cadence

- Every emitter is a coroutine on the one asyncio loop thread. `event()` is a plain synchronous call on that thread. No loop code runs in a thread: there is no `to_thread`, `run_in_executor` or `threading` use outside `status.py` and rich.
- `LiveUi` uses `rich.live.Live(_ActiveView(self), refresh_per_second=10, transient=True)` (`ui.py:757-762`, `_SPINNER_FPS = 10` at `ui.py:73`). Rich's **refresh thread** calls `_ActiveView.__rich__`, which calls `LiveUi.active_text()`, which calls `tracker.snapshot()`, 10 times a second (`ui.py:722-731, 780-804`). Scrollback lines are printed from `LiveUi.event` on the loop thread (`ui.py:806-874`).
- The tracker protects folds and snapshots with a `threading.Lock` exactly for that refresh thread (`status.py:184-186, 193, 201`).
- `snapshot()` deep-copies the whole `RunStatus` and adds in-flight elapsed values. It is called 10 times a second, and its cost grows with the run's history (`status.py:198-224`).
- `PlainUi` prints only on events, with an `[HH:MM:SS]` stamp (`ui.py:877-992`).

---

## 2. Every event kind

There are **108 kinds** (`setup_sync` and `setup_ok` counted apart). They were
found by an AST walk of every `.event(` and `._event(` call, multi-line calls
included. Under "Fold", ✔ means
`RunStatusTracker` has a handler. Under "Live", P means `LiveUi` prints a line
(through `SETUP_LINE_EVENTS`, `ui.py:469-507`, or its own branch,
`ui.py:817-874`). Fields marked `?` are present at only some sites.

Two handlers exist **with no emitter anywhere**: `eval_progress`
(`status.py:538-543`) and `item_trial` (`status.py:503-513`, kept for old logs).

### 2.1 Run lifecycle and phases

| Kind | Emitter | Fields (types) | Fold | Live |
|---|---|---|---|---|
| `run_start` | `orchestrator.py:462` `_orchestrate` | `timeout_mins:int`, `deadline:float` (Ctx.clock monotonic secs), `deadline_iso:str` (local naive ISO), `budget_usd:float\|None`, `objective:"quality"\|"speed"\|"cost"`, `model_policy:dict\|None` (`ModelPolicy.summary()`) | ✔ objective, deadline→time_remaining, timeout_mins, budget_usd (`status.py:228-254`) | header |
| `phase_start` | `orchestrator.py:553` (preflight), `589` (setup), `907` (planning, `wave`), `1380` (promotion_gate, `wave`), `2675` (baseline_measure), `872` (tripwire_backoff, `wave`); `report.py:943` (reporting) | `name:str`, `label:str` (prose; planning's says "Planning [wrap-up ]wave N…" or "Re-planning … with the probe answers"), `wave:int?` | ✔ phase, phase_detail. **`wave` is dropped** (`status.py:389-393`) | phase ticker |
| `phase_end` | `orchestrator.py:561, 597, 994, 1397, 2718, 882`; `report.py:966` | `name:str`, `elapsed_secs:float`, `wave:int?` | ✔ appends a `PhaseSegment` (`status.py:395-423`) | P done-line |
| `wave_start` | `orchestrator.py:821` (main wave), `768` (probe phase, `probe_phase=True`), `1125` (recon, wave 0) | `wave:int`, `should_wrap_up:bool`, `baseline_rc_id:str`, `plan_headline:str` (code-composed counts, `status.py:117-140`), `rejected_summary:str`, `items:list[{item_id,kind,label}]` (`status.item_summary`, `status.py:96-97`), `probe_phase:bool?` | ✔ new `WaveStatus` (`status.py:425-467`) | P plan line |
| `wave_end` | `orchestrator.py:781` (probe phase), `1136` (recon), `1360`, `1373`, `1396` (`_maybe_promote`; emitted BEFORE the gate's `phase_end`) | `wave:int`, `probe_phase:bool?` | – | flushes the wave to scrollback |
| `exit` | `orchestrator.py:492, 505, 516` (`_orchestrate`); `674, 681, 699, 738, 754, 760, 784, 798, 812, 816, 859, 863, 867` (`_run_loop`) | `reason:str` (`timeout`, `budget_exhausted`, `planner_failed`, `empty_plan`, planner `loop_exit` free text, `tripwire`, `wrap_up`, `quota_exhausted`, `fatal:<Type>`, `interrupted`), `detail:str?`, `spent_usd:float?`, `budget_usd:float?` | ✔ exit_reason | P exit + totals |
| `report_written` | `report.py:931` (fallback), `991` | `path:str`, `fallback:bool` | – | P |
| `probe_phase_repeated` | `orchestrator.py:819` | `wave:int` | – | – |

### 2.2 Pre-flight and setup

| Kind | Emitter | Fields (types) | Fold | Live |
|---|---|---|---|---|
| `train_set` | `orchestrator.py:2947` `run_quarantine` | `line:str`, `items:int` (distinct train items) | – | P |
| `heldout_tag` | `orchestrator.py:2961` | `tag:str`, `items:int`, `line` | – | P |
| `quarantine` | `orchestrator.py:2982` | `line`, `heldout_items:int` (val+test together), `orphan_items`, `stored_eval_runs`, `stored_task_runs`, `overlap_items`, `kept_shared_task_runs` (all int) | – | P |
| `preflight` | `orchestrator.py:568` | `kiln_host:str`, `cost_usd:float` | – | – |
| `baseline_seeded` | `setup.py:1243` `run_setup` | `rc_id:str`, `rule:str`, `unresolvable_default:str\|None` | – | P |
| `eval_unloadable` | `setup.py:1220` | `eval_id`, `name`, `reason`, `line` (str) | ✔ unloadable_evals | P |
| `setup_sync` / `setup_ok` | `setup.py:1271` (one call site; kind chosen by `plan.has_writes`) | `evals_total`, `evals_active`, `seeded`, `refreshed`, `repaired`, `unmeasurable`, `unbound_unresolved` (int), `cost_usd:float`, `report:SetupReport` | ✔ setup | P |
| `janitor` | `orchestrator.py:601` | `count:int`, `memory_ids:list[str]` | ✔ janitor_stamped | P |
| `baseline_found` | `orchestrator.py:611` | `rc_id:str` | ✔ baseline_history | P |
| `prior_screening` | `viability.py:547` | `results:int`, `restored:int`, `line` | – | P |
| `prior_screening_unreadable` | `viability.py:524` | `error:str` | – | – |
| `baseline_outside_policy` | `orchestrator.py:3049` | `rc_id`, `reason` | – | – |
| `contract_ok` | `orchestrator.py:627` (`**contract`) | `evals:int`, `eval_load_errors:int`, `statistics:"ok"`, `results_probed` (`contract.py:63-122`) | – | – |
| `model_preflight` | `model_preflight.py:424` (skip), `500` | `line`, `result:ModelPreflightResult` | ✔ model_preflight | P |
| `model_preflight_flag` / `model_preflight_untested` | `model_preflight.py:474` / `484` | `line`, `provider`, `model`, `reason` | – | P |
| `baseline_progress` | `orchestrator.py:2654` (every 30 s while the baseline measures) | `rc_id`, `line` ("baseline measuring: Eval 40/120, … — 2 job(s) running") | – | P |
| `baseline_measured` | `orchestrator.py:2739` | `rc_id`, `line`, `evals:{eval_id:{stored:int, expected:int\|None, how:str}}` (counts only, no scores), `cost_usd` | – | P |
| `baseline_speed` | `orchestrator.py:2799` (speed and cost objectives) | `rc_id`, `line`, `latency:LatencyComparison\|None` (single arm) | – | P |
| `baseline_audit` | `generality.py:762` | `rc_id`, `status`, `findings:int`, `issue_id`, `note` | – | – |
| `baseline_judge_copy_audit` | `generality.py:723` | `rc_id`, `status`, `findings:list[str]`, `issue_id`, `note`, `line` | – | P (when `line` is set) |
| `detectability_row` | `detectability.py:635` | `line` ("train R of N with room …; val V items …", `row_line` at `436-462`) | – | P |
| `detectability_warning` | `detectability.py:637, 654` | `line` | – | P |
| `detectability` | `detectability.py:649` | `line`, `report:DetectabilityReport` | ✔ detectability | P |
| `noise_estimate` / `noise_row` / `noise_warning` | `noise.py:421 / 578 / 580` | `estimate_usd`+`line` / `line` / `line` | – | P |
| `noise_clone` | `noise.py:447` | `rc_id`, `parent_rc_id` | – | – |
| `noise_check` | `noise.py:581` | `line`, `result:NoiseCheckResult` | ✔ noise_check | P |
| `recon_skipped` | `orchestrator.py:1120` | `reason` | – | – |
| `opening_screen_skipped` | `orchestrator.py:1081, 1092, 1099` | `reason` | – | – |
| `eval_job_drives_judged` | `job_drives.py:230, 331` | `note`, `eval_id?` | – | – |

### 2.3 Planning, screening, the wave runner

| Kind | Emitter | Fields (types) | Fold | Live |
|---|---|---|---|---|
| `screening_round` | `viability.py:479` `run_screening_round` | `round:int` (run-wide counter), `line:str` (multi-line table), `results:list[ScreenResult dict]` | ✔ screen_results (validated, `status.py:375-382`) | P |
| `screen_candidate_dropped` | `orchestrator.py:1048` | `wave`, `line` | – | P |
| `screening_cap` | `orchestrator.py:1030` | `wave`, `line` | – | – |
| `tripwire` | `orchestrator.py:1267` | `wave:int`, `error_class:str`, `failed:int` | ✔ WaveStatus.tripwire | banner |
| `deadline_cancel` | `orchestrator.py:1196, 1224` | `wave`, `item_ids:list[str]` | – | – |
| `deadline_grace` | `orchestrator.py:1208` | `wave`, `item_ids`, `grace_secs:int` | – | – |
| `fatal_cancel` | `orchestrator.py:1175` | `wave`, `item_ids`, `error:str` (repr) | – | – |
| `probe_rcs_archived` | `orchestrator.py:2885` | `rc_ids:list[str]`, `line` | – | P |
| `eval_split_refused` | `orchestrator.py:2488` | `eval_id`, `line` | – | – |
| `eval_sizes_unavailable` / `eval_size_unavailable` | `orchestrator.py:2527` / `2539` | – / `eval_id` | – | – |
| `lever_attempts_unavailable` | `orchestrator.py:3079` | – | – | – |
| `available_models_unavailable` | `orchestrator.py:2904` | – | – | – |

### 2.4 Item lifecycle (`items.py`)

| Kind | Emitter | Fields (types) | Fold | Live |
|---|---|---|---|---|
| `item_state` | `items.py:606` `_set_state`. Callers: `264` building_eval; `344`, `433`, `752` measuring; `478` probing; `705`, `1266` building_rc; `1014` reviewing (generality review session only); `811` analyzing | `item_id`, `state` | ✔ live_state, phase_secs (`status.py:485-496`) | PlainUi only |
| `item_detail` | `items.py:413, 461, 536, 718, 732, 769` (analysis progress "measuring <eval>…" / "<eval> done"), `778, 1031, 1073, 1122, 1262, 1299, 1409`, `1505` (every `candidate_budget` line and every `measure_chunk` line of an arm) | `item_id`, `detail:str` | ✔ ItemStatus.detail (cleared on each state change) | live sub-line |
| `item_change` | `items.py:987` `_record_build` (once per build; again on a rebuild) | `item_id`, `change:RcChange dict` (summary, changes, unchanged, consistency_check, smoke_items) | ✔ ItemStatus.change | P Δ block |
| `item_probe` | `items.py:526` | `item_id`, `probe:ProbeResult dict` (answer yes/no/unclear, evidence, drives, ephemeral_rc_ids, notes) | ✔ ItemStatus.probe | – |
| `item_outcome` | `items.py:298` `execute_plan_item` (exactly one per item) | `item_id`, `outcome:ItemOutcome` (an object in-process, a dict in the JSONL) | ✔ outcome, live_state=done, elapsed (`status.py:582-596`) | finding cell |
| `judge_copy` | `items.py:1061` (first check after each build) | `item_id`, `rc_id`, `status` (clean/copied/unchecked), `findings:list`, `note`, `unread_evals:list`, `line` | – | – |
| `generality` | `items.py:1016` | `item_id`, `rc_id`, `status` (clean/leaked/unchanged/unchecked), `leaks:list`, `notes:str`, `line` | – | – |
| `reference_check` | `items.py:1110` | `item_id`, `rc_id`, `mode`, `status` (clean/found/unchecked), `findings`, `note`, `line` | – | – |
| `generality_exact_match` / `generality_parent_known` | `generality.py:610 / 608` | `item_id`, `matches:int` | – | – |
| `generality_inputs_unavailable` / `generality_train_unavailable` | `generality.py:162 / 366` | `error` | – | – |
| `swap_check_unavailable` | `items.py:596` | `rc_id`, `error` | – | – |
| `measurement` | `items.py:1522` `_measure_arm` (when the engine finishes) | `item_id`, `rc_id`, `status` (completed/stopped/capped/failed), `stop_kind:str\|None`, `line` | – | P |
| `success_rule` | `items.py:855` | `item_id`, `analyzer_successful:bool`, `rule_ok:bool`, `reasons:list[str]` | – | – |
| `veto_overridden` | `items.py:901` | `item_id`, `veto_reason` | – | – |
| `probe_inputs_unavailable` | `items.py:565` | `error` | – | – |

### 2.5 Measurement engine (`measure.py`; arms and screening; `item_id` is None in a screening round)

| Kind | Emitter | Fields (types) | Fold | Live |
|---|---|---|---|---|
| `queue_drawn` | `measure.py:812` | `item_id:str\|None`, `seed:str`, `size:int` (planned items), `population:{eval:{stratum:int}}`, `skipped:{eval:str}`, `queue:list[[eval_id,item_id,stratum,block]]`, `fragility?` (efficiency), `per_eval_caps?` | – | – |
| `candidate_budget` | `measure.py:988` (once, when the measurement starts) | `item_id\|None`, `rc_id`, `usd:float`, `source:"planner"\|"default"`, `line` | – | P (screening only; an arm's goes through item_detail) |
| `measure_chunk` | `measure.py:1268` (per candidate per chunk) | `item_id\|None`, `rc_id`, `chunk:int`, `chunks:int`, `line:str` (`progress_line`, `measure.py:724-747`: "w7-1: chunk 3/9 · 24 judged · +3/−0 vs baseline · 0.88× latency · continue"; under speed/cost "lost L/N passes, fixed F/M") | – | P (screening only) |
| `lever_not_applied` / `lever_unverified` | `measure.py:1114 / 1131` | `item_id`, `rc_id`, `line` | – | – |
| `fragility_unavailable` | `measure.py:419` | `eval_id`, `error` | – | – |

### 2.6 Analyzer (`analysis.py`, through `ctx.analyzer.log`)

| Kind | Emitter | Fields (types) | Fold | Live |
|---|---|---|---|---|
| `trial` | `analysis.py:2148` `Analyzer.trial` (one per engine chunk per eval) | `eval_id`, `rc_id`, `landed:bool`, `scores:{train_item_id: float\|None}` (normalized, 1 = best) | – | – |
| `eval_job_errors` | `analysis.py:1275` (`_measure`), `2226` (`_trial_quota_check`) | `eval_id`, `rc_id`, `split`, `attempt:int`, `error_class:str`, `job_error:str\|None`, `errors:list[{dataset_id, error_message}]` | ✔ eval_job_error_items, eval_job_error_classes["<split> <class>"] | – |
| `measurement_shared` / `measurement_topup` | `analysis.py:1138, 1149 / 1156` | `eval_id`, `rc_id`, `split` | – | – |
| `drive_failure_diagnosed` | `analysis.py:1494` | `rc_id`, `item_id`, `error_class`, `message` | – | – |

### 2.7 Promotion gate (`orchestrator.py`)

| Kind | Emitter | Fields (types) | Fold | Live |
|---|---|---|---|---|
| `promotion_blocked` | `1351` (model policy), `1444` (speed/cost holes), `1479`, `1570`, `1592`, `1638` | `winner_rc_id`, `reasons:list[str]` | ✔ WaveStatus.promotion_skipped = "blocked: …" | PlainUi |
| `promotion_skipped` | `1368` (deadline; adds `candidate_rc_ids:list`), `1539` (agent gave no decision) | `reason:str`, `candidate_rc_ids?` | ✔ WaveStatus.promotion_skipped | banner |
| `gate_evals_skipped` | `2220` | `skipped:list[str]` | – | – |
| `val_fill_failed` | `2238` | `eval_id`, `rc_id`, `error_class`, `detail` | ✔ val_fill_failures | – |
| `gate_progress` | `2295` `_gate_line` | `rc_id`, `eval_id`, `line` ("gate rc: eval k/K · n judged · +f/−b vs baseline · continue\|done") | – | P |
| `gate_candidate_ineligible` | `2395` | `rc_id`, `reasons:list` | – | – |
| `integrity_input_unreadable` | `1673, 1692, 1702` | `rc_id?`, `eval_id?`, `what` | – | – |
| `integrity` | `1747` (one per candidate) | `rc_id`, `flags:list[str]`, `line` | – | – |
| `gate_block` | `1797` | `rc_id`, `exp_mem_id`, `reasons:list`, `line` | ✔ gate_blocks | P |
| `gate_block_recorded` | `1826` | `rc_id`, `exp_mem_id` | – | – |
| `speed_result` | `1919` (speed and cost objectives) | `rc_id`, `latency:dict\|None`, `cost:dict\|None` (LatencyComparison) | ✔ speed_results (with the wave) | – |
| `promotion_rule` | `1435` (`decision`, `objective`), `1471` (`rc_id`, `decision`, `skipped`) | `decision:PromotionDecision\|None` | – | – |
| `promotion` | `1436, 1496, 1549` | `winner_rc_id:str\|None`, `rationale:str`, `decision:PromotionDecision` (rationale, regressed_issues, blocked_on) | ✔ WaveStatus.promotion + baseline_history | banner |
| `promotion_winner_unmatched` | `3181` | `winner_rc_id` | – | – |
| `issue_regressed` | `3240` | `issue_memory_id`, `note` | – | – |
| `rejection_recorded` | `3364` (only when both writes landed) | `rc_id`, `exp_mem_id` | – | – |
| `hold_recorded` | `3347` (only when both writes landed) | `rc_id`, `exp_mem_id`, `constraint_id` | – | – |
| `model_policy_refused` | `3033` | `rc_id`, `reason` | – | – |

### 2.8 Health, bookkeeping, spend

| Kind | Emitter | Fields (types) | Fold | Live |
|---|---|---|---|---|
| `dispatch_retry` | `items.py:160` `call_with_retries` (before each backoff sleep) | `source:str`, `item_id:str\|None`, `attempt:int`, `backoff_secs:float`, `error_class:str` | – | P ⚠ |
| `provider_quota` | `orchestrator.py:491` (circuit breaker) | `line`, `detail` | – | P |
| `memory_write_failed` | `items.py:225, 544, 978, 994, 1044, 1085, 1605`; `orchestrator.py:1280, 3116, 3203, 3264, 3287`; `viability.py:495` | `write:str`, `what:str`, one or more of `item_id`/`rc_id`/`exp_mem_id`/`issue_memory_id`, `error:str` | – | P ⚠ |
| `memory_snapshot` | `snapshots.py:38` | `label`, `file`, `records:int` | – | – |
| `memory_snapshot_failed` | `snapshots.py:46` | `label`, `error` | – | – |
| `spend_reconciled` | `reconcile.py:225` | `when`, `booked_usd`, `found_usd`, `gap_usd`, `task_runs:int`, `eval_runs:int`, `line` | – | P |
| `spend_reconcile_failed` | `reconcile.py:209` | `when`, `error` | – | – |
| `spend` | 25 sites, below | `source:str`, `lane:"subscription_equivalent"\|"provider"` (always set by the emitter), `cost_usd:float`, `unmeasured_tokens:int?` (agent lane), `evidence_cost_usd:float?` (provider lane), `item_id:str?` | ✔ spend_total_usd, spend_by_lane, spend_by_source, evidence_spend_usd, unmeasured_tokens; per item spend_usd + spend_by_lane (`status.py:545-580`). Also SpendMeter | in totals |

`spend` sources (lane, file:line):

- Agent lane (`subscription_equivalent`): `preflight` `orchestrator.py:571`, `circuit_breaker` `483`, `batch_plan` `1000`, `promote_best` `1529`, `integrity_review` `1851`, `describe_eval` `setup.py:1111`, `eval_build` `items.py:274`, `create_experiment_rc` `items.py:944`, `analyze_experiment_results` `items.py:830`, `probe` `items.py:505`, `generality_review` `generality.py:642`, `baseline_audit` `generality.py:817` (item_id `"baseline-audit"`, in no wave), `report_writer` `report.py:971` (**after `exit`**, so no rendered total includes it; `ui.py:372-378`).
- Kiln lane (`provider`): `latency_drives` `orchestrator.py:1909, 2763`, `val_pass` `2403`, `baseline_measure` `2722`, `eval_jobs` `items.py:365, 448, 790`, `probe` `items.py:485`, `measurement` or `screening` (`spec.source`) `measure.py:1206` (per chunk), `model_preflight` `model_preflight.py:469`, `noise_check` `noise.py:501`, `reconciliation` `reconcile.py:217`.

---

## 3. The status models as they stand (`schemas.py`)

### `ItemStatus` (`schemas.py:1943-1974`)

| Field | Filled by |
|---|---|
| `item_id`, `kind`, `label` | `wave_start.items` (`status.item_summary`). `kind` is one of fix, merge, eval, measure, remeasure, probe (`status.py:63-72`). The label is "[wildcard] hypothesis (issue_id)" for a fix, " + ".join(experiment memory ids) for a merge, "probe: question" for a probe, and so on (`status.py:75-93`) |
| `live_state` | `planned` at wave_start, then `item_state` events, then `done` at `item_outcome`. States: planned, building_eval, building_rc, reviewing, measuring, analyzing, probing, done |
| `eval_progress: tuple[int,int]\|None` | `_on_eval_progress`. **Never emitted, so always None in production** |
| `outcome: ItemOutcome\|None` | `item_outcome` |
| `elapsed_secs` | the tracker clock from wave_start (live in `snapshot()`, frozen at outcome) |
| `spend_usd`, `spend_by_lane` | `spend` events that carry this `item_id` |
| `detail` | `item_detail` (the latest chunk line while measuring); cleared on state change and at outcome |
| `phase_secs: dict[state, secs]` | folded at each `item_state` transition, plus the in-flight partial on the snapshot copy |
| `change: RcChange\|None` | `item_change` |
| `trial` | `item_trial` (old logs only) |
| `probe: ProbeResult\|None` | `item_probe` |

### `PhaseSegment` (`schemas.py:1977-1984`)

`name`, `wave:int|None`, `elapsed_secs`. One per `phase_end` (`status.py:395-419`).
There is no start timestamp. Phase names: preflight, setup, baseline_measure,
planning (plan, replan and rescreen all use this name), promotion_gate,
tripwire_backoff, reporting.

### `WaveStatus` (`schemas.py:1987-1998`)

`wave`, `should_wrap_up`, `plan_headline`, `probe_phase`, `rejected_summary`,
`items`, `promotion` (from `promotion`), `tripwire` ("N× class"), and
`promotion_skipped` (from `promotion_skipped` and `promotion_blocked`). A probe
phase and its main wave are TWO `WaveStatus` entries with the same `wave`
number. Wave 0 is the reconnaissance battery. Only `waves[-1]` receives item
events (`_find_item`, `status.py:469-475`).

### `RunStatus` (`schemas.py:2001-2076`)

| Field | Filled by |
|---|---|
| `project_id`, `task_id`, `guidance`, `agent_cli` | the tracker constructor (`cli.py:1293-1299`) |
| `baseline_history: list[rc_id]` | `baseline_found`, `wave_start.baseline_rc_id`, `promotion.decision.winner_rc_id` (deduped) |
| `waves` | `wave_start` and later events |
| `spend_total_usd`, `spend_by_lane`, `spend_by_source`, `evidence_spend_usd`, `unmeasured_tokens` | `spend` |
| `time_remaining_secs`, `time_remaining_known`, `timeout_mins`, `budget_usd`, `objective` | `run_start`. Time remaining is computed at snapshot from the monotonic deadline |
| `elapsed_secs` | computed at snapshot, from the tracker's construction (just before `orchestrate`) |
| `exit_reason` | `exit` |
| `phase`, `phase_detail`, `phase_elapsed_secs` | `phase_start` / `phase_end` (one phase at a time) |
| `phase_segments` | `phase_end` |
| `janitor_stamped` | `janitor` |
| `setup` | `setup_sync` / `setup_ok` |
| `unloadable_evals` | `eval_unloadable` |
| `eval_job_error_items`, `eval_job_error_classes` | `eval_job_errors` |
| `val_fill_failures` | `val_fill_failed` |
| `speed_results` | `speed_result` |
| `gate_blocks` | `gate_block` |
| `detectability`, `noise_check`, `model_preflight` | the events of the same names (isinstance-gated) |
| `screen_results` | `screening_round` |

Not in `RunStatus`: the run id, the task or project name, the Kiln host, the
slots, the agent CLI command text, any timestamps, the live rc id or experiment
memory id of an item, per-eval numbers of any kind, gate tables, the plan's
items beyond id/kind/label, and open questions.

---

## 4. Display needs: availability

### 4.1 Objective, roles, splits

| Need | Class | Source |
|---|---|---|
| Objective | (a) ✔ | `run_start.objective` → `RunStatus.objective` |
| Roles: what is optimized, held, watched | (c) | A static mapping from the objective (`docs/objectives.md` table; `objective.py:24-47`). **quality:** optimized = each experiment's targeted evals (`ExperimentItem.evaluation_target`); held = every other active eval (guards; gate rule `decide_single_candidate`, `orchestrator.py:2064-2164`); watched = cost and latency (passive per-row `latency`/`cost`). **speed:** optimized = latency; held = all eval scores, "equal or better" (`analysis.speed_quality_reasons`); watched = cost. **cost:** optimized = provider cost per request; held = quality; watched = latency |
| The concern list (eval id, name, score key, type, direction) | (a) ✔ | `detectability.report.rows[].train` has `eval_id`, `eval_name`, `score_key`, `score_type`, `direction`, plus `promotion_driving`. It is on by default (`cli.py:1025`) and emitted at setup. Counts: `setup_*.evals_active`; omitted evals: `eval_unloadable`. An eval built mid-run (by an eval_only item or a `needs_eval` fix) appears only in its steps file (`EvalBuildReport.eval_id`) or later rows |
| Split sizes, train | (a) total / (c) per eval | `train_set.items` (total distinct train items, `orchestrator.py:2947-2955`). Per eval: `runs/<id>/train_set.json` `per_eval` |
| Split sizes, val/test | (a) partial / (c) | `quarantine.heldout_items` (val and test together); `detectability` rows `val_n` per (eval, score). Exact per-eval train/val/test counts: `detectability.split_sizes(ctx, split)` (`detectability.py:588-601`, only `ctx.target` is used) or `quarantine.membership(task_dir)` (`quarantine.py:219-261`). Both COUNT items from the project on disk and never read a result |

### 4.2 Spend and time

| Need | Class | Source |
|---|---|---|
| Spend total | (a) ✔ | `RunStatus.spend_total_usd` |
| Spend by lane (Kiln vs agent subscription) | (a) ✔ | `spend_by_lane`; `status.spend_split` (`status.py:100-114`); labels from `spend_lane_labels(agent_cli)` (`schemas.py:73-90`). Unpriced tokens: `unmeasured_tokens`. Per source: `spend_by_source` |
| Budget | (a) ✔ | `RunStatus.budget_usd` (None means no cap) |
| Elapsed | (a) ✔ | `RunStatus.elapsed_secs` (snapshot-computed) |
| Time limit, time left | (a) ✔ | `timeout_mins`, `time_remaining_secs`, `time_remaining_known` |
| Burn rate | (c) | spend_total ÷ elapsed, or a window over `spend` arrival times or JSONL `ts`. `reconciliation` spend lands in lumps at wave boundaries and at exit (`orchestrator.py:668`, `448-452`) |

### 4.3 Wave position

| Need | Class | Source |
|---|---|---|
| Current wave number | (a) ✔ partly, (c) | `waves[-1].wave`. While the NEXT wave is planning, the tracker still shows the previous wave. `phase_start(name="planning", wave=N)` carries N, but `_on_phase_start` drops it (`status.py:389`), so read the raw event |
| Waves remaining (mean wave time and cost) | (b) / (c) | (b) `wave_cycle_secs` and `wave_cycle_costs`, locals of `_run_loop` (`orchestrator.py:662-663`, appended at `853-855`), and `projected = fmean(wave_cycle_secs)` (`690`). (c) Mean cycle from consecutive `phase_start planning` (or `wave_start`) times; cost per wave = the spend between them. Combine with `should_wrap` (`orchestrator.py:402-413`: window = max(30 min, mean cycle)), the 15-minute start floor (`187`, `676-689`) and the budget rule (`711-712`) |
| This is the wrap-up wave | (a) ✔ / (c) | `wave_start.should_wrap_up` → `WaveStatus.should_wrap_up`. During planning it is only in the label text ("Planning wrap-up wave N…", `orchestrator.py:911-916`); the `wrap_up` local is (b) |

### 4.4 Each wave's timeline

| Need | Class | Source |
|---|---|---|
| Plan start/end | (a) | `phase_start`/`phase_end` `name="planning"`, `wave=N`. The fields carry no timestamp: stamp on arrival, or use the JSONL `ts`. `PhaseSegment` keeps only the elapsed time. Replan and rescreen also use `name="planning"`. A replan's label says "Re-planning"; a rescreen's label is the same as the first plan's (`orchestrator.py:906-916`). The steps file's `request.phase` tells them apart |
| Screening rounds | (a) end / (c) start | No start event and no phase (`_screening_phase`, `orchestrator.py:1013-1068`). The end is `screening_round`; the start ≈ the preceding planning `phase_end`. In-flight: `measure_chunk` and `candidate_budget` with `item_id=None` |
| Item work | (a) ✔ | `wave_start` → `item_state` … → `item_outcome` (per item) → `wave_end` |
| Gate start/end | (a) | `phase_start`/`phase_end` `promotion_gate`, only when at least one candidate survives the filter. Otherwise only `wave_end` (`orchestrator.py:1356-1361`). With deadline-skip: `promotion_skipped` |
| Promotion or not | (a) ✔ | `promotion.decision.winner_rc_id` (None = no winner; `blocked_on` set = held); `promotion_blocked`, `promotion_skipped`; tripwire backoff = phase `tripwire_backoff` |
| Projected waves ahead | (c) | as in "waves remaining" |

### 4.5 Per concern (each eval score, cost per request, latency)

| Need | Class | Source |
|---|---|---|
| Starting baseline, train value | (a) binary only / (b) / (c) | Binary scores: from `detectability.report.rows[].train`, pass rate = (n − room)/n, where room = items not at the best value. Rating means: (b) the baseline's stored train rows are read (`orchestrator.py:2535`, `detectability.train_detectability`) but never summarized. (c) `PerEvalResult.baseline_score` of an arm whose parent is the baseline, in its steps analyze file. Latency/cost: `baseline_speed.latency` (speed and cost objectives); the cost single arm `ctx.baseline_cost[rc]` (`orchestrator.py:2776`) is (b); under quality, `BatchPlanInput.baseline_latency` in `steps/NNN-batch_plan-loop.json` (c) |
| Starting baseline, val value | (d) until the first gate, then (c) | The loop reads val only at the promotion gate (`_assemble_val_tables`, `orchestrator.py:2167-2173`). After a gate: `PerEvalResult.baseline_score` (val) in `runs/<id>/val_summary.json`. That file is **overwritten by every gate** (`orchestrator.py:1874-1884`), so a live display must capture it. The in-memory `tables` (`orchestrator.py:1409`) are (b) |
| Current baseline (Best Confirmed), val per eval | (b) / (c) | The winner table's `per_eval[].candidate_score` from the gate that promoted it (`val_summary.json`, captured at that gate; or `steps/NNN-promote_best-loop.json` when the agent decided). Code-rule rationales print row deltas as text (`orchestrator.py:2036-2040, 2122`). The `val` memory record keeps rationale text only (`memory.py:1316-1365`) |
| Each experiment's per-eval train result (score, delta vs parent, CI) | **(b)** / (c) | (b) `bundle.per_eval` (`items.py:764-772`; `PerEvalResult`: `delta`, `ci_low`, `ci_high`, `p_value`, `significant`, `n_items`, `baseline_score`, `candidate_score`, `coverage`, `headroom`, `latency`, `cost`; `schemas.py:1224-1310`). Not emitted. (c) `runs/<id>/steps/NNN-analyze_experiment_results-<item_id>.json` → `request.bundle.per_eval`, but only for arms that completed and reached analysis. Stopped or capped arms: (a) pooled `outcome.measurement.net`, `net_ci_low`, `net_ci_high` only. Their per-eval estimates `Measured.per_eval: {eval: Estimate(net, se, rate, n)}` (`measure.py:588, 1298-1302`) are (b) and are dropped by `measurement_summary` (`items.py:1431-1453`) |
| Judged / planned | (a) at outcome; (a)+text in flight | Outcome: `MeasurementSummary.judged`, `judged_per_eval`, `planned_per_eval`. In flight: `queue_drawn.size`, `measure_chunk.chunk/chunks`. The judged count is only in the line text (`ev.judged_n`, `measure.py:731`, is (b)) |
| Cost/latency vs parent | (b) / (c) | (b) `bundle.per_eval[].latency/cost` (passive, from stored runs) and `bundle.latency/cost` (timed, `items.py:798-800`); `Measured.vs_parent`, `vs_parent_cost` (`measure.py:1327-1337`). (a) `outcome.speed` only for timing stops. (c) the steps analyze file. Note: the chunk line's "0.88× latency" is against the run's baseline (the reference), not the parent (`measure.py:737-745`) |
| Gate val numbers per candidate (speed/cost) | (a) ✔ | `speed_result.latency/cost` → `RunStatus.speed_results` |

### 4.6 Experiment status sets (for "best unconfirmed per concern since the last promotion")

| Need | Class | Source |
|---|---|---|
| Successful | (a) | `item_outcome.outcome.result.successful` (after the code floor); also `success_rule`, `veto_overridden` |
| Promoted | (a) ✔ | `promotion.decision.winner_rc_id`; `baseline_history` |
| Rejected at val | (a) / (c) | `rejection_recorded` (only when both memory writes landed, `orchestrator.py:3354-3366`). Otherwise derive: the gate candidates minus the winner minus the blocked, when `decision.blocked_on` is None |
| Held | (a) | `hold_recorded` (same caveat); `promotion.decision.blocked_on` |
| Blocked / ineligible | (a) | `gate_block` (folded), `promotion_blocked`, `gate_candidate_ineligible` |
| Merged into a promoted merge | (a) at outcome / (c) | A merge item's `outcome.item.work.wins: list[WinRef(experiment_memory_id, rc_id)]`; the label lists the ingredient experiment ids. When the merge's rc is promoted, its ingredients are covered. At plan time: the steps batch_plan file |
| Gate candidate set | (c) | Outcomes with `ExperimentItem.promotion_candidate`, `result.successful`, `rc_id` and an experiment memory id (`orchestrator.py:1331-1342`), minus the policy refusals (`promotion_blocked` at `1351`) |

The per-eval train numbers (4.5, (b)) are the one missing input here.

### 4.7 Per item

| Need | Class | Source |
|---|---|---|
| Hypothesis text | (a) as text | Embedded in `wave_start.items[].label` ("[wildcard] hypothesis (issue_id)"). The raw `FixWork.hypothesis` is (b) at wave start (`plan.items`, `orchestrator.py:828`), (a) at outcome (`outcome.item.work.hypothesis`), (c) in `steps/NNN-batch_plan-loop.json` `result.data.items` |
| Target eval(s), issue | (a) issue in label / (b) / (c) | `evaluation_target` and `issue_memory_id`: (b) at wave start, (a) at outcome, (c) in the steps batch_plan or `create_experiment_rc` request |
| Lever / technique | (b) / (c) / (a) prose | `FixWork.technique`: (b) at wave start, (a) at outcome, (c) steps. "What changed" prose: (a) `item_change` (RcChange) once the build lands |
| Type | (a) ✔ | `ItemStatus.kind`; wildcard flag in the label |
| Live state and transitions | (a) ✔ | `item_state` → `live_state` |
| Elapsed per state | (a) ✔ | `ItemStatus.phase_secs`, `elapsed_secs` |
| Eval job progress (done/total) | **(d)** | `eval_progress` has a handler and a field but no emitter. Jobs are driven by one `/api/jobs/wait` long-poll, so there is no mid-flight count (`analysis.py:1884-1887` says so). What exists: chunk progress (a) `measure_chunk` k/N. A display could poll Kiln `GET /api/jobs/{id}` for `ctx.analyzer.running_jobs` (`analysis.py:1029, 1208-1212`), but only full-measurement jobs register there (baseline, val fills, remeasure, issue baseline), not the engine's chunk jobs. `_baseline_progress` does this today (`orchestrator.py:2620-2662`) |
| Per-chunk fixed/broke | (a) as text / (b) / (c) | `measure_chunk.line` "+F/−B vs baseline" (or "lost L/N passes, fixed F/M"). Structured: `ev.fixed_broke()` (`measure.py:730`) is (b). (c) recompute from `queue_drawn.queue` (stratum = parent pass/fail) plus `trial.scores`, joined on rc_id |
| Judged / planned | (a) | see 4.5 |
| Spend | (a) ✔ | `ItemStatus.spend_usd`, `spend_by_lane` |
| Cap | (a) | `candidate_budget.usd/source/line` (not folded; once, at measurement start). The default is re-priced after each chunk but not re-emitted (`measure.py:1247`, (b)). Final: `outcome.measurement.budget_usd/budget_line` |
| Verdict and stop reason | (a) ✔ | `outcome.status`, `measurement.status/stop_kind/stop_line`, `speed_verdict`, `result.successful/veto_reason/memory_overview`. The `measurement` event comes earlier. Plain text: `report.stop_code` / `stop_reason_text` / `STOP_REASONS` (`report.py:409, 488-532`) and `ui.outcome_cell` (`ui.py:126-189`) |
| Rebuild after the generality / judge-copy checks | (c) | No explicit event. Signs of a rebuild: a second `item_change` for the item (`items.py:1306` → `987`); `judge_copy`/`generality`/`reference_check` with a NEW `rc_id`; `item_state building_rc` again (`items.py:1266`); `item_detail` "generality: leaked — …" / "judge copy: copied — …"; snapshot label `<item>-rebuild-before` |
| Probe answer | (a) ✔ | `item_probe` → `ItemStatus.probe`; `outcome.probe` |
| Item rc id while live | (a) not folded | `judge_copy`, `generality`, `reference_check`, `candidate_budget`, `measure_chunk` and `measurement` carry item_id plus rc_id. `ItemStatus` gets it only through `outcome.rc_id` |
| Experiment memory id while live | (b) | `exp_mem` (`items.py:703`) until `outcome.experiment_memory_id` |

### 4.8 Promotion gate

| Need | Class | Source |
|---|---|---|
| Candidates | (c) | as in 4.6. The count is in the `phase_start` label text |
| Val progress | (b) / (d) | Every fill fires in one gather (`orchestrator.py:2253-2254`). `gate_progress` (`2288-2301`) comes as a burst AFTER all fills land, so it is not progress. `val_fill_failed` arrives on failure. Live progress would need a Kiln poll of `running_jobs` (val jobs do register there) |
| Per-eval val result | (b) / (c) | (b) `tables: list[CandidateValTable]` (`orchestrator.py:1409`; `schemas.py:1883-1923`). (c) `runs/<id>/val_summary.json` (written at `1410`, again at `1425` under speed/cost, overwritten at the next gate); `steps/NNN-promote_best-loop.json` `request.candidates` when the agent ran. Text: `gate_progress.line` "+f/−b" |
| Integrity flags | (a) | `integrity` (rc_id, flags), `gate_block` (folded), `integrity_input_unreadable` |
| Decision | (a) ✔ | `promotion` (folded), `promotion_rule` |
| Rejections / holds | (a) | `rejection_recorded`, `hold_recorded`, `promotion_blocked`, `gate_candidate_ineligible`, `promotion_skipped`, `issue_regressed` |

### 4.9 Screening rounds

| Need | Class | Source |
|---|---|---|
| Candidates requested | (b) / (c) | `plan.screen` (`ScreenRequest.candidates`) in `_screening_phase`; (c) the steps batch_plan `result.data.screen` |
| Survivors, verdicts, drop reasons | (a) ✔ | `screening_round.results[]` (`ScreenResult`: `verdict` dead_end/survivor/error/capped, `reason`, `line`, `stop`, `stop_kind`, `net` with CI, `rank`, `latency`, `cost`, `spend_usd`, `budget_usd`, `overrides`, `parent_rc_id`, `round_no`; `schemas.py:894-968`) → `RunStatus.screen_results`. Also `screen_candidate_dropped`, `screening_cap`, `opening_screen_skipped` |
| In flight | (a) | `measure_chunk` / `candidate_budget` with `item_id=None`; the line starts with the candidate label |

### 4.10 Log-worthy events

| Need | Class | Source |
|---|---|---|
| Lessons and dead ends written by agents | (c) only | Agents write them through the gateway's `memory_create`/`memory_update` (`gateway/ops.py:782-800`), in a separate MCP process. No event, no run-dir record. Visible only by (1) diffing `runs/<id>/snapshots/NNN-*.json`, whole-store dumps with no per-record time, taken around plan, gate, build, analysis, probe and report (`snapshots.py:26-46`, `memory.py:742-773`); (2) the redacted agent transcripts `traces/<project>/<task>/<run_id>/<label>-<session>.jsonl` (the tool calls), copied after each session; (3) the TUI listing memories through Kiln. Code-written records ARE evented: probe lessons (`item_probe`), screen results (`screening_round`), dead-end stamps (outcome `dead_end` plus `item_detail` "dead end: …") |
| Planner open questions | (b) / (c) | `plan.open_questions` (`OpenUnknown`: question, default_assumed, gates, reask_when) goes to `memory.write_open_unknowns` at `orchestrator.py:740, 800, 1067`. Not emitted. (c) steps batch_plan `result.data.open_questions`; the `OPEN:` constraint records in snapshots |
| Rate limits | (a) | `eval_job_errors.error_class` (e.g. `rate_limit`, folded into counts by "<split> <class>"); `dispatch_retry.error_class` |
| Quota | (a) | `provider_quota`, then `exit` `quota_exhausted` |
| Tripwire | (a) ✔ | `tripwire` → `WaveStatus.tripwire`; phase `tripwire_backoff` |
| Provider errors | (a) | `eval_job_errors`, `val_fill_failed` (folded count), `drive_failure_diagnosed`, `model_preflight_flag`, outcome `infra_error` |
| Dispatch retries | (a) | `dispatch_retry` (source, item_id, attempt, backoff_secs) |
| Deadline cancels | (a) | `deadline_cancel`, `deadline_grace`, `fatal_cancel` |
| KilnClient transport retries | (d) | silent (`kiln.py:156-184`) |
| Slot saturation | (d) | private semaphores (`dispatch/claude_cli.py:774`, `analysis.py:1005`) |

### 4.11 Run identity

| Need | Class | Source |
|---|---|---|
| Task name, project name | (d) in events / (c) | Known during onboarding (`Selection.task.name`, `onboarding/flow.py:310`) but not handed to `_run`. (c) read `task.kiln` in the project dir or ask Kiln |
| Project id, task id | (a) ✔ | `RunStatus.project_id`, `task_id` (constructor) |
| Run id | (b) / (c) | `run_id`, a local at `cli.py:1206` (given to the dispatcher and ReportWriter). (c) the run dir name (`viability.run_label`, `viability.py:507-510`), `_RUN_PROGRESS.run_dir`, `ScreenResult.run_id` |
| Agent CLI | (a) kind / (b) command | `RunStatus.agent_cli` (claude/codex/opencode). The full command `agent_cli.text` is in cli only (`cli.py:1226`) |
| Slots | (b) | `args.max_concurrent_agents`, `args.max_concurrent_evals` (defaults 4 and 4, `cli.py:138-139`); `Ctx.max_concurrent_agents` |
| Kiln host URL | (a) / (b) | `preflight.kiln_host`; `ctx.target.kiln_host` |
| Code that builds Kiln web URLs | (a) partial / (d) | Only `onboarding/kiln_urls.py:14-41`: `{host}/settings/check_for_update`, `/settings/create_project`, `/settings/create_task/{pid}`, `/optimize/{pid}/{tid}` (run configs), `/specs/{pid}/{tid}` (evals), `/specs/{pid}/{tid}/{spec_id}/{eval_id}` (eval page), `/settings/providers`. **No Compare or Lineage route exists in the code.** README.md:178 names Kiln's "Compare view" and "Experiment Lineage view"; their routes must come from Kiln's web UI source. An eval page link needs the eval's spec id, which no event carries: it is in `GET /api/projects/{p}/tasks/{t}/specs` (read by `_active_evals` and `eval_weights`, `orchestrator.py:2467, 1935-1944`; `onboarding/readiness.py:85-86` builds the eval link this way) and in the `describe_eval` steps files (`DescribeEvalInput.spec_id`, `schemas.py:1880`) |

### 4.12 End of run

| Need | Class | Source |
|---|---|---|
| Exit reason | (a) ✔ | `exit.reason`, `detail` → `exit_reason` |
| Report path | (a) / (b) | `report_written.path`, `fallback`. Known up-front: `ReportWriter.report_path` (`report.py:894-896`; `_RUN_PROGRESS.report_path`, `cli.py:1315`). An interrupted run writes NO report (`orchestrator.py:513-517`) |
| Totals | (a) ✔ | `RunStatus` at `exit`. The report-writer spend lands after `exit` |
| Best Confirmed config | (a) ✔ | `baseline_history[-1]` |
| Event order after exit | – | `exit` → `phase_start reporting` → `memory_snapshot report-before` → `phase_end reporting` → `spend report_writer` → `report_written` |

---

## 5. Run files on disk

The state folder is `$KILN_HARNESS_TUNER_HOME`, else `~/.kiln_ai/harness_tuner/`.
It is created with mode 0700 (`paths.py:1-30, 79-97`). The run id is
`YYMMDD-HHMM`, with a `-2` or `-3` suffix when the minute is taken; the id is
claimed by creating its directory (`status.py:57-60`, `cli.py:1045-1063`).
`prepare_run_dir` pre-creates every `RUN_DIR_ENTRIES` entry, empty, so a file
can exist and still have no content (`cli.py:646-672`).

| Path | Written when | Format |
|---|---|---|
| `runs/<id>/wave_log.jsonl` | every event, first in the fan-out; append mode, **flushed per line** (`wave_log.py:41-50`) | One JSON object per line: `{"ts": local naive ISO with µs, "kind", **fields}`. Models are `model_dump(mode="json")`; anything else non-JSON is `repr` |
| `runs/<id>/kiln-harness-tuner.log` | the root logger, from `cli.py:1215` on | Text: `%(asctime)s %(levelname)-7s %(name)s %(message)s` (`logbook.py:41-43`) |
| `runs/<id>/steps/NNN-<agent>-<item\|issue\|eval\|loop>.json` | after EVERY agent dispatch returns, for all three CLIs (`ClaudeCliDispatcher._run` → `_persist_step`, `dispatch/claude_cli.py:1103-1165`; Codex and OpenCode subclass it) | `{seq, agent, label, started, ended, elapsed_secs, request, result:{status, detail, data, error, usage}}`. Holds: BatchPlanInput + BatchPlan (items, open_questions, screen, rejected_summary; `request.phase` = plan/replan/rescreen); RcBuildInput + RcBuildResult; AnalysisInput (`bundle.per_eval`) + ExperimentResult; PromotionInput (val tables) + PromotionDecision; probe, eval_build, describe_eval, integrity/generality review, report_writer. Not atomic (`write_text`) |
| `runs/<id>/snapshots/NNN-<label>.json` | around the planner, the gate, each build/rebuild, analysis, probe, and before the report; each followed by a `memory_snapshot` event | `{taken_at, records:[{id, scope, tags, overview, content}]}` |
| `runs/<id>/val_summary.json` | at each gate (`orchestrator.py:1410`, `1425`) | `{"candidates":[CandidateValTable…], "note"}`, **latest gate only**, overwritten |
| `runs/<id>/train_set.json` | once, before the first dispatch (`orchestrator.py:2944-2946`) | `{version:1, eval_inputs, task_runs, per_eval:{eval:[ids]}, heldout_tags}` (`gateway/trainset.py:70-77`) |
| `runs/<id>/gateway_created.jsonl` | appended by the gateway processes | `{id, kind, split, eval_id}` per created record (`gateway/ops.py:150-163`) |
| `runs/<id>/mcp_config*.json`, `settings*.json`, `settings.readonly.json`, `model_policy.json` | at start (`cli.py:702-957, 1243`) | config |
| `runs/<id>/scratch/<label>/`, `runs/<id>/scratch/traces/` | per session; the gateway writes train traces | sandbox; trace `.md` files (`gateway/trace_files.py:1-64`) |
| `runs/.lock-<project>-<task>` | at start; held with a kernel flock | `{pid, started, project_id, task_id}`. **No run id** (`cli.py:1114-1166`) |
| `runs/.quarantine/<p>-<t>/archived_run_configs/<stamp>/…` | at each wave end | moved PROBE run configs |
| `traces/<project>/<task>/<run_id>/<label>-<session>.jsonl` (also `-timeout.jsonl`, `-stderr.txt`) | after each agent session | redacted CLI transcript (`dispatch/claude_cli.py:1483-1522`) |
| `reports/kiln-harness-tuner-<run_id>.md` | at the end | Markdown; code appendices (`report.py:898-918`) |

### Can a separate process tail these to rebuild the display (attach or replay)?

**Yes, with a small display-side adapter and no core change:**

1. **Find the run.** Read the lock file (pid and start time) and take the newest `runs/<id>/`, or take the path printed at `cli.py:1210`. The folder is 0700, so the reader must be the same user.
2. **Tail `wave_log.jsonl`.** It is flushed per line. Feed each line to a fresh `RunStatusTracker`, but **re-hydrate the payloads by kind first**: `item_outcome.outcome` → `ItemOutcome`; `promotion.decision` → `PromotionDecision`; `setup_*.report` → `SetupReport`; `detectability.report` → `DetectabilityReport`; `noise_check.result` → `NoiseCheckResult`; `model_preflight.result` → `ModelPreflightResult`. Without this, `_on_promotion` raises `AttributeError` on a dict (`status.py:612-617`), the isinstance-gated handlers drop the data (`355-373`), and `outcome` stays a raw dict that `ui.outcome_cell` cannot read.
3. **Time.** `run_start.deadline` is the OTHER process's monotonic clock and means nothing to the reader. Use `deadline_iso` and `timeout_mins`. Inject a tracker `clock` (`status.py:159`) that returns the current event's `ts` as epoch seconds. Then elapsed, phase_secs and PhaseSegments come out right on replay; between events, tick with wall time.
4. **Side files.** `steps/`, `snapshots/` and `val_summary.json` are written with plain `write_text`, not atomically. Read each after its announcing event: a `spend` with that source after a dispatch, `memory_snapshot`, or `phase_end promotion_gate`. Handle a partial file by retrying. `val_summary.json` keeps only the latest gate, so a late attach loses earlier gates' val tables unless a `promote_best` steps file has them.
5. **Not reachable from another process:** in-memory state such as `Analyzer.running_jobs`, `Ctx` fields, and the `wave_cycle_*` locals. Kiln itself stays reachable over HTTP.

---

## 6. Threading and async caveats for a Textual app in the same process

1. **One asyncio loop, owned by `cli.main`** (`cli.py:1448`). Textual's `App.run()` makes its own loop with `asyncio.run`, so it cannot run inside the existing loop. The options are: `await app.run_async()` beside `orchestrate` in the same loop; restructure so Textual owns the loop and the run is a worker (this moves the signal handlers and `asyncio.current_task()` in `_run_with_signals`); or run `App.run()` in its **own thread** and feed it with `app.call_from_thread`. The tracker's lock already makes `snapshot()` safe from another thread (`status.py:184-201`).
2. **Blocking sync work on the loop thread.** Today rich's refresh thread hides it. On a shared loop it would freeze a Textual UI:
   - `reconcile()` scans and prices project files, synchronously, at every wave boundary (`orchestrator.py:668`) and before `exit` (`448-452`; `reconcile.py:191-195`).
   - `run_quarantine` and the train-set compute read the project at start (`orchestrator.py:2932-2998`).
   - `archive_probe_run_configs` globs and JSON-parses every `eval_run.kiln` and moves folders, each wave (`orchestrator.py:2809-2887`).
   - `split_sizes` / `_train_split_sizes` / `measure.stored_rows_on_disk` read from disk (`measure.py:235`).
   - Snapshot, steps and val-summary writes run `json.dumps` over the whole memory store.
   - OpenCode `subprocess.run(["opencode","export",…], timeout=60)` blocks the loop up to 60 s per session (`dispatch/opencode_cli.py:71, 350-366`).

   A UI in its own thread avoids all of these.
3. **Consumers must never raise and must stay fast.** `FanoutLog` has no guard (`wave_log.py:71-75`), and events are emitted from inside items, the gate and the exit path. An exception in the UI's `event()` propagates into the loop and can fail an item, a wave or the run. `_ReconcileBeforeExit` also runs `reconcile()` inline in the `exit` event.
4. **Signals.**
   - SIGTERM and SIGHUP: `loop.add_signal_handler` cancels the main task (`cli.py:1331-1368`).
   - Ctrl-C relies on SIGINT → KeyboardInterrupt / asyncio.run's main-task cancel (`cli.py:1459-1473`). That path kills children by process group, releases the lock and emits `exit interrupted` (`orchestrator.py:513-517`).
   - Textual puts the tty in raw mode, so Ctrl+C arrives as a KEY, not SIGINT. The TUI must call the main task's `cancel()` itself, after the requested confirmation, to keep that path.
   - Check for conflicts with Textual's own signal use (SIGWINCH, SIGTSTP/SIGCONT): a loop allows one handler per signal.
5. **What writes to the terminal during a run.**
   - `print` at `cli.py:1210` (before the display) and at `1426-1483` (after it).
   - Logging goes to the file only (`logbook.py:12-17`; httpx at WARNING). BUT logging before `configure_run_logging` (`check_agent_cli`, `cli.py:1198` < `1215`) goes through Python's lastResort handler to stderr at WARNING and above.
   - `warnings.warn` output is NOT captured (no `logging.captureWarnings`) and goes to stderr.
   - Agent subprocesses run with stdin, stdout and stderr all `PIPE` (`dispatch/claude_cli.py:349-359`). OpenCode export uses `capture_output`. MCP servers are grandchildren on the agent CLI's pipes. No child writes to the tty.
   - The current UIs print through a `rich.Console`.
6. **Clock domain.** The tracker clock must be `ctx.clock` (monotonic) because `run_start.deadline` uses it (`status.py:148-151`, `cli.py:1291-1297`). `concurrency.first_error_or_done` uses `time.monotonic()` directly.
7. **Before the loop.** The onboarding runs synchronously, before `asyncio.run`, with prompt_toolkit and per-batch `asyncio.run` calls (`onboarding/flow.py:1-10, 91-100`). The "Starting" bookend before `run_start` (agent install, `check_agent_cli`, lock, run dir, config render, `KilnClient`) happens before any tracker or UI exists (`cli.py:1175-1299`). To show it, the TUI must be created earlier in `_run`.
8. **Snapshot cost.** `snapshot()` deep-copies the whole run history on every call (`status.py:198-224`). Call it on events or at a few Hz, not per frame.

---

## 7. What would need a core change, and how small

| Need | Today | Smallest change |
|---|---|---|
| Per-eval train result per experiment (Best Unconfirmed) | (b) `bundle.per_eval`; (c) steps analyze file for completed arms only | Emit `item_rows(item_id, rc_id, per_eval=[r.model_dump()…], latency, cost)` after `items.py:797` (~5 lines). Add per-eval `Estimate` (net, se, rate, n) to `MeasurementSummary` in `items.py:1431-1453` and `schemas.py:322-343` (~8 lines) for stopped/capped arms |
| Structured chunk numbers (judged, fixed, broke, per-eval estimate, spend, cap) | text in `measure_chunk.line` | Add fields to the `measure_chunk` emit at `measure.py:1268` (~6 lines; the values are in scope: `s.ev.judged_n`, `s.ev.fixed_broke()`, `s.spend`, `s.budget`) |
| Gate val tables per gate | (b) `tables`; `val_summary.json` overwritten | Emit `val_tables` beside `write_val_summary` at `orchestrator.py:1410/1425` (~2 lines). Keep it emit-only (do not fold into RunStatus) |
| Gate val progress | burst after all fills | One event after each `ensure_measured` in `fill()` at `orchestrator.py:2230-2248` (~3 lines). Or a TUI poll of `running_jobs` |
| Waves remaining | (b) `wave_cycle_secs/costs` | Emit `wave_cycle(secs, cost, projected)` at `orchestrator.py:853-855` (~3 lines). Or derive in the TUI (no change) |
| Plan detail at wave start (technique, targets, issue, hypothesis, cap, promotion_candidate) | (b) `plan.items`; (c) steps | Extend `status.item_summary` (`status.py:96-97`), a display-side helper (~8 lines). Or read the steps file |
| Open questions | (b) | Emit at the three `write_open_unknowns` calls (`orchestrator.py:740, 800, 1067`, 1 line each). Or read steps |
| Screening round start and request | none | Emit `screening_start(wave, request)` before `run_screening_round` at `orchestrator.py:1050-1053` (~2 lines). Or read steps |
| Wave number and wrap-up during planning | dropped by the tracker | Keep `wave` in `_on_phase_start` (`status.py:389`, display side). Or read the raw event |
| Run id, task name, slots, CLI text, host in the header | (b) in cli | Pass them to the TUI constructor at `cli.py:1300-1306` (composition root, not core). The task name needs onboarding to return the `Selection`, or a disk read |
| Eval job done/total | (d) | Real core work (new polling in `analysis._wait_for_job`), not small. Drop it in favour of chunk progress, or let the TUI poll `GET /api/jobs/{id}` for `running_jobs` (full measurements only) |
| Agent-written lessons and dead ends | snapshots only | No core event is possible (they are written in the gateway process). Diff snapshots in the TUI, or poll Kiln memories |
| Kiln Compare / Lineage links | no route in code | Add routes to `onboarding/kiln_urls.py` after checking Kiln's web UI source (not core) |
