# Data contract audit for the new TUI

Repo: `/home/user/harness-tuner` at `656854f`. The commits after `f33d877`
change specs only, so the code is the code `runtime_data.md` read. All paths
are under `src/kiln_harness_tuner/` unless they say otherwise. This audit
changed no code.

It checks every element of `functional_spec.md` section 7, and the comps
(`ui_design.md` section 7 and the preview data in
`ui_preview/sample_data.py`), against the real event stream. The method:
an AST walk of every `.event(` call (212 call sites, 108 kinds; the line
numbers in `runtime_data.md` section 2 are still correct), then a read of
each emitter and of `status.py`.

Status codes in the tables:

| Code | Means |
|---|---|
| **E** | Emitted today. The source is `event.field` at `file:line` |
| **D** | Derived by the app from events. The cell says how |
| **C1–C7** | Needs approved change N (section 2) |
| **CLI** | From the CLI at the hand-off, not an event |
| **NA** | Not available in any event, and not covered by the 7 changes |
| **COMP** | The comp shows a value that the code does not have |

---

## 0. Headline findings

These have no real source and the 7 approved changes do not cover them.
Section 1.13 has the full list of comp problems.

1. **Per-item strip cells.** Change 3 as approved gives counts (judged,
   fixed, broke). The strip needs each judged item's parent result and
   candidate result (`Judged.parent_pass`, `cand_pass`,
   `estimate.py:53-71`). With counts only, "held" and "still failing"
   cannot be told apart. Today the cells can be rebuilt from `queue_drawn` +
   `trial` events, but that join is fragile (1.6). Change 3 needs two more
   counts or a per-chunk cell list.
2. **Eval priorities ("p0", "p1").** No event carries them. The loop reads
   `spec.priority` only in `eval_weights` (`orchestrator.py:1930-1944`), and
   only under speed and cost.
3. **Score display names.** Events carry `score_key` (the JSON key) only.
4. **Test split size, and val as one total.** Only train (a union over
   evals, `train_set.items`) and held-out (val and test together,
   `quarantine.heldout_items`) are emitted. Val is per (eval, score)
   (`detectability` rows, `val_n`). Test is nowhere.
5. **Run config names and descriptions** (Best Confirmed's "Name", the
   Done card's description). Events carry 12-digit Kiln rc ids only. The
   name and the description are Kiln fields (the demo project's
   `task_run_config.kiln` has `id`, `name`, `description`).
6. **Start (train) values for rating, cost and latency concerns.** Only
   pass rates come from setup (`detectability` rows). Rating means and,
   under quality, cost and latency come only from change 1 rows, and only
   for evals that a completed arm targeted. Under `--objective cost`, the
   baseline's cost per request (`ctx.baseline_cost`,
   `orchestrator.py:2776`) is never emitted.
7. **Probe "k of N drives".** `ProbeResult` has `answer`
   (yes/no/**unclear**), free-text `evidence` and `drives` (a total)
   (`schemas.py:651-661`). It has no count of the drives that showed "yes".
8. **In-flight gauges on Issue baseline measurement and Re-measure cards.**
   Each eval is one `ensure_measured` long-poll (`items.py:357-359`,
   `454-456`). Nothing is emitted until it returns.
9. **Spending caps on eval-build, issue-baseline and re-measure cards.**
   Only experiment arms and screening candidates have a cap (`measure.py`
   `set_budget`, `943-951`). Also, the arm card's "$1.20 of $4" mixes
   two things: `ItemStatus.spend_usd` is all of the item's spend (build
   session, analysis session, eval jobs, chunks), but the cap covers
   only the measurement's provider dollars.
10. **Items cancelled at the deadline, or by a fatal sibling, get no
    `item_outcome`.** `_run_wave` builds their `failed_infra` outcomes
    after cancellation and does not emit them
    (`orchestrator.py:1229-1247`). Only `deadline_cancel` and `fatal_cancel`
    (`1175`, `1196`, `1224`) name them. In `RunStatus` they stay live
    forever. The spec's verdict table has no row for them.
11. **`promotion` is not proof of a promotion.** It is emitted before
    `_apply_checked`, which can still refuse the winner with
    `promotion_blocked` (`orchestrator.py:1436-1447`, `1496-1505`,
    `1549-1609` and `1612-1647`). `_on_promotion` appends the winner
    to `baseline_history` anyway (`status.py:614-622`). A "Promoted" line
    must wait for `phase_end promotion_gate` with no `promotion_blocked`
    for that rc.
12. **Cost and latency as experiment targets.** `ExperimentItem` targets
    evals only (`evaluation_target`, `schemas.py:440`). Under quality, no
    item targets "Cost / request". The comp card "Cost / request ·
    thinking" and its in-flight count have no source.
13. **"Dim means no clear change" for Best Confirmed against Start.** After
    the first promotion, gate tables compare a candidate with the
    incumbent, not with Start. No interval exists for Best Confirmed
    against Start. The preview uses a fixed threshold (0.5 pp). That
    threshold is a magic value.
14. **Exit cause of `wrap_up`.** The `exit` event does not say whether the
    wrap-up came from time (`should_wrap`, `orchestrator.py:691`) or from
    budget (`711-712`). "How the run ended" cannot say "time limit" or
    "budget" for this reason.
15. **"Wave N of ~M".** The spec's formula ignores the loop's own rules: the
    30-minute wrap-up window (`WRAP_UP_WINDOW_SECS`, `orchestrator.py:181`,
    `402-413`) and the 15-minute start floor (`MIN_WAVE_START_SECS`, `187`,
    `676-689`). It can promise one wave more than the loop will start.

---

## 1. Element-by-element audit

### 1.1 Title bar

| Element | Source | Status |
|---|---|---|
| "KILN HARNESS TUNER" | Static text | – |
| Task name | `selection.task.name` in onboarding (`onboarding/flow.py:310`). `prepare_start` returns `args` only (`flow.py:105-125`; `cli.py:1441-1447`), so the name does not reach `_run` | CLI. Onboarding must return the selection, or the app reads `task.kiln` under `args.project_path` |
| Project name | `selection.project.name` (`onboarding/summary.py:141-143`). Same gap | CLI, same fix |
| Run id | `run_id`, a local in `_run` (`cli.py:1206`), from `make_run_id` (`status.py:57-60`). It is the run folder name | CLI |
| Agent CLI | `parse_agent_cli(args.agent_cli)` (`cli.py:1177`). `.kind` goes to the tracker (`cli.py:1298`, `RunStatus.agent_cli`). `.text` is logged only (`cli.py:1226`) | CLI (E for the kind, via the tracker constructor) |
| Agent slots, eval slots | `args.max_concurrent_agents`, `args.max_concurrent_evals` (flags `cli.py:344`, `325`; defaults 4 and 4, `cli.py:139`, `138`) | CLI |

### 1.2 Goal

| Element | Source | Status |
|---|---|---|
| Objective | `run_start.objective` (`orchestrator.py:469`) → `RunStatus.objective` (`status.py:236-237`) | E |
| Optimize / hold / watch rows | A fixed map of the objective (functional spec 7.2). The quality hold rule is real: a row whose CI is entirely below zero rejects (`decide_single_candidate`, `orchestrator.py:2064-2095`). The speed and cost rule is `apply_speed_eligibility` (`1947-1972`) | D |
| "5 evals" | Distinct `eval_id` in `detectability.report.rows` with `promotion_driving` (`schemas.py:1587-1603`; emitted `detectability.py:649`; always on, `cli.py:1025`). Or `setup_*.evals_active` (`setup.py:1273`) | E |
| Comp: "Quality · 5 evals, weighted by priority" | Under quality, no code rule weights by priority. Priority weights are used only under speed and cost (`eval_weights`, `orchestrator.py:1930-1944`; used at `1423` and in `items.py` under efficiency) | COMP |
| Data: train | `train_set.items`, distinct train items over all evals (`orchestrator.py:2947-2956`). Per eval: `baseline_measured.evals[e].expected` (`2739-2745`; `measure_baseline_first` is on, `cli.py:1024`) | E |
| Data: val | Per (eval, score) only: `detectability` rows `val_n` (`schemas.py:1597`). `quarantine.heldout_items` is val and test together (`orchestrator.py:2982-2991`) | NA as one total |
| Data: test | No event, no field | NA |

### 1.3 Time and spend

| Element | Source | Status |
|---|---|---|
| Elapsed | `RunStatus.elapsed_secs`, computed at snapshot from the tracker's construction (`status.py:177`, `203`) | E (tracker) |
| Time limit | `run_start.timeout_mins` (`orchestrator.py:463`); `deadline` (monotonic, `459`) | E |
| "started 14:12" | Wall clock at `run_start` arrival, or `deadline_iso` − `timeout_mins`. `run_started_at` (`cli.py:1205`) is not emitted | D |
| "ends 17:12" | `run_start.deadline_iso` (`orchestrator.py:467`), local naive ISO | E |
| Spend | `spend.cost_usd` → `spend_total_usd` (`status.py:555`) | E |
| Budget | `run_start.budget_usd` (`orchestrator.py:468`); None means no cap | E |
| Spend by lane | `spend.lane` → `spend_by_lane` (`status.py:570-572`). Labels: `spend_lane_labels(agent_cli)` (`schemas.py:85-90`). Unpriced agent tokens: `unmeasured_tokens` (`status.py:561`). The comp omits them; Codex reports tokens, not cost | E |
| Burn rate | Spend ÷ elapsed hours. Lumps: reconciliation spend lands at each wave boundary and before `exit` (`orchestrator.py:668`, `446-450`). The report writer's spend lands after `exit` (`report.py:971`) | D |
| Wave N | `wave_start.wave` (`orchestrator.py:821`). During planning: `phase_start(name="planning").wave` (`907-910`). The tracker drops `wave` on `phase_start` (`status.py:389`), so read the raw event | E |
| "of ~M" | The loop's values are locals: `wave_cycle_secs`, `wave_cycle_costs` (`orchestrator.py:662-663`, `853-855`). The app must re-derive the mean cycle from arrival times (consecutive main-wave planning starts, and the spend between them). See finding 15: mirror `should_wrap` and the 15-minute floor, or the number misleads | D (with caveat) |
| [LAST WAVE] | `wave_start.should_wrap_up` (`orchestrator.py:823`). While the wrap-up wave is planned, only the label text says it: "Planning wrap-up wave N…" (`911-913`) | E; text-parse while planning |

### 1.4 Run timeline

No event carries a start time. The app stamps each event when it arrives
(section 5).

| Segment | Source | Status |
|---|---|---|
| setup | `run_start` arrival → the first `phase_start planning`, or `wave_start wave=0`. Inside: phases `preflight` (`orchestrator.py:553`/`561`), `setup` (`589`/`597`), `baseline_measure` (`2675`/`2718`). Model preflight, contract, janitor, prior screening, baseline audit, detectability and noise have no phase | D |
| recon (wave 0) | `wave_start wave=0` (`1125`) → `wave_end wave=0` (`1136`) | E |
| Opening screen (speed, cost) | No start event. `candidate_budget` / `queue_drawn` / `measure_chunk` with `item_id=None`, then `screening_round` (`viability.py:479`) | D |
| plan | `phase_start` / `phase_end` `name="planning"`, `wave` (`907`/`994`). Replan label: "Re-planning … with the probe answers" (`911-914`). A rescreen has the same label as the first plan | E |
| screen | No start event. Start ≈ the previous planning `phase_end`, or the first `queue_drawn` with `item_id=None`. End: `screening_round`. When every candidate is dropped, only `screen_candidate_dropped` (`1048`) | D |
| items | `wave_start` (`821`) → the wave's last `item_outcome` (`items.py:298`), or `phase_start promotion_gate`. `wave_end` comes after the gate (`orchestrator.py:1396`), so it is not the items' end | D |
| Probe phase | `wave_start probe_phase=True` (`768`) … `wave_end probe_phase=True` (`781`): a second items stretch inside one wave | E. The spec and the comps have no style for it |
| gate | `phase_start` / `phase_end promotion_gate` (`1380`/`1397`). No gate when no candidate survives (`1360`) or at the deadline (`1368`) | E |
| Promotion mark | `promotion.winner_rc_id` (`1436`, `1496`, `1549`) and no later `promotion_blocked` for that rc in the same gate (`1638`). Finding 11 | D |
| Gate with no winner | `promotion` with `winner_rc_id=None`, `promotion_blocked`, `promotion_skipped` | E |
| Tripwire backoff | `phase_start` / `phase_end tripwire_backoff` (`872`/`882`), 5 minutes (`TRIPWIRE_BACKOFF_SECS`, `194`) | E. No segment style in the spec or the comps |
| report | `phase_start` / `phase_end reporting` (`report.py:943`/`966`), after `exit` | E |
| Projection | Mean wave length, as in 1.3 | D |
| Axis, ticks | `timeout_mins` | E |
| Now marker | Wall clock | – |

### 1.5 Results

| Element | Source | Status |
|---|---|---|
| The concern list | `detectability.report.rows` (one per (eval, score)), with `promotion_driving` (`schemas.py:1587-1603`; `detectability.py:649`; always on, `cli.py:1025`). Order follows `_active_evals` (`orchestrator.py:2448`). An eval built mid-run (`EvalOnlyItem`, or a fix with `needs_eval`, `items.py:684-701`) is not in it. Its id appears only in later C1/C2 rows, with no name (C1a can carry `Evidence.eval_names`, `stop_rules.py:337`) | E, partial |
| Card name: the eval | `rows[].train.eval_name` (`schemas.py:1572`) | E |
| Card name: the score | `rows[].train.score_key` only. No display name | NA (show the key) |
| Priority | Finding 2 | NA |
| Rating marker "1–5" | `rows[].train.score_type == "five_star"` (`RATING_SCORE_TYPES`, `analysis.py:83`). Pass rate: `pass_fail`, `pass_fail_critical` (`analysis.py:82`) | E |
| Start (train), pass rate | (n − room) ÷ n from `rows[].train` (`schemas.py:1575-1576`). For a binary score, "room" is the items not at the best value | D |
| Start (train), rating mean | No event. C1b row `baseline_score` (a mean for ratings, `schemas.py:1291`), for evals a completed arm targeted | C1, partial |
| Start (train), cost and latency, quality | No event. C1b rows' passive `cost` / `latency` (`LatencyComparison`, `baseline_median_ms`). One comparison per eval, not one number per config | C1, partial |
| Start (train), latency, speed or cost | `baseline_speed.latency` (`orchestrator.py:2799`) | E |
| Start (train), cost, cost objective | `ctx.baseline_cost[rc]` (`2776`), never emitted | NA |
| Start (val) | The first gate's incumbent is Start: its rows' `baseline_score` on val | C2 |
| Best Confirmed: rc | `baseline_history[-1]` (`status.py:439-443`, `614-622`). Finding 11 | E, with a caveat |
| Best Confirmed: name | Finding 5 | NA |
| Best Confirmed: values | The winning table's `candidate_score` at the gate that promoted it | C2 |
| Best Confirmed: change against Start | A difference of absolutes from two gates. Finding 13 | C2 + a magic threshold |
| Best Unconfirmed: value | Successful arms: C1b `delta` against the parent, added to the parent's value. Screen survivors (cost, latency): `screening_round.results[].latency`, `.cost`, `.net` (`schemas.py:894-968`) | C1 / E |
| Best Unconfirmed: color | C1b `ci_low` / `ci_high` | C1 |
| Best Unconfirmed: holder | `item_outcome.outcome.rc_id` ↔ `item_id` (`items.py:298`) | D |
| Best Unconfirmed: resets and drop-outs | `promotion` (with finding 11), `rejection_recorded` (`orchestrator.py:3364`) or a derived rejection, merge ingredients (`MergeWork.wins`, `schemas.py:410-411`) | D |
| "N in flight" | Live items whose targets include the concern. Targets need C5. Cost and latency: finding 12 | C5; NA for cost and latency under quality |
| Compare link | rc ids: Start (`baseline_found`, `orchestrator.py:611`), Best Confirmed, Best Unconfirmed holders. The URL rule is the Kiln repo's (functional spec 7.8). No code here builds it (`onboarding/kiln_urls.py:14-41`) | D |

### 1.6 Experiment cards

| Element | Source | Status |
|---|---|---|
| Target concern | `ExperimentItem.evaluation_target` (`schemas.py:440`). A list: an item can target several evals, and the card names one. A `needs_eval` fix gets its eval mid-item (`items.py:691`, `701`) | C5 |
| Merge target "All evals" | `kind == "merge"` (`status.py:72`) | E |
| Kind of change | `FixWork.technique` (`schemas.py:181-183`), free text, not a fixed set. Today only at the outcome (`outcome.item.work.technique`) | C5 |
| Hypothesis | Inside `wave_start.items[].label`: "[wildcard] hypothesis (issue_id)" (`status.py:92-93`). Raw: C5 | E as text; C5 |
| Merge: "Combine … on top of …" | Label: experiment memory ids joined by " + " (`status.py:91`). Ingredient rc ids: `MergeWork.wins[].rc_id` (C5, or at the outcome) → item ids through the rc map | E / D |
| Probe question | Label "probe: <question>" (`status.py:88`) | E |
| Eval build: "Build an eval for <issue>" | Label "build eval for <issue id>" (`status.py:81`). The issue's own words (its overview) are not emitted | E (id); NA (words) |
| Issue baseline: "Measure <issue> on the baseline" | Label (`status.py:83`), id only | E (id); NA (words) |
| Re-measure: "Complete the measurement of <rc>" | Label "re-measure rc <id> on <evals>" (`status.py:86`) | E |
| Item id | `wave_start.items[].item_id`. Recon ids are `r0-N` (`probes.py:786`); the comps use `w0-N` | E |
| Steps and their state | `item_state` (`items.py:606`): `building_eval` 264, `building_rc` 704 and 1266, `reviewing` 1014, `measuring` 344, 433 and 752, `analyzing` 811, `probing` 478; `done` at `item_outcome` | E |
| "rebuilding once · copied text found" | The state goes back to `building_rc` (`items.py:1266`) after `judge_copy status=copied` (`1061`), `generality status=leaked` (`1016`) or `reference_check status=found` (`1110`). A second `item_change` follows (`987`) | D |
| "an agent is writing the change · 3m10s" | `ItemStatus.phase_secs`, live at snapshot (`status.py:215-223`) | E |
| "14 of 36 judged" | Judged: text in `measure_chunk.line` (`measure.py:724-747`, emitted `1268`). Planned: `queue_drawn.size` (`812-816`) | E as text; C3 |
| "5 fixed · 0 broke so far" | Text in `measure_chunk.line` ("+F/−B vs baseline"; under speed and cost, "lost L/N passes, fixed F/M"). F and B are against the queue parent (`estimate.py:66-70`), although the text says "baseline" | C3 |
| Strip cell: fixed / broke | Per item: `parent_pass` and `cand_pass` (`measure.py:1194-1203`) | Finding 1 |
| Strip cell: held / still failing | Needs each item's parent result. Counts cannot split them | Finding 1 |
| Strip, today | Rebuild from `queue_drawn.queue` `[eval_id, item_id, stratum, block]` (`measure.py:192-198`) + `trial.scores` (`analysis.py:2148`). `queue_drawn` has no `rc_id`, so join through `candidate_budget` or `measure_chunk` (item_id + rc_id). `cand_pass` = score ≥ 0.5 (`measure.py:1201`). `parent_pass` = stratum is "pass"; the "named" stratum's `parent_pass` is not in the JSON (`measure.py:436`) | D, fragile |
| Spend against cap | Cap: `candidate_budget.usd` (`measure.py:988`), once. The default is re-priced each chunk (`1248`) and not emitted again. Spend: see finding 9 | E, but mismatched; C3 should carry both |
| Cap on eval build, issue baseline, re-measure | Finding 9 | COMP |
| Probe "3 drives" while asking | `ProbeItem.max_drives` (`schemas.py:557`) | C5 |
| Probe steps "✓ asked · ⠸ answering" | One state: `probing` (`items.py:478`) | COMP (use "asking → answered") |
| Issue baseline gauge "24 of 40 items" | Finding 8. `eval_progress` has no emitter (`status.py:538-543`) | NA |
| Re-measure gauge "48 of 72 items" | Per eval, at the end of its fill only: `item_detail` "eval: S/T stored" (`items.py:458-462`). The total is not known up front | Partial, as text |
| At the gate: "312 of 520 val items" | C4 | C4 |
| Live border | `live_state` | E |

### 1.7 Verdict table, row by row

| Outcome | Source | Status |
|---|---|---|
| Success · goes to the gate | `outcome.status == "completed"`, `result.successful`, `item.type == "experiment"`, `item.promotion_candidate` (`schemas.py:438-446`). It is a prediction: the gate also needs `rc_id` and an experiment memory id (`orchestrator.py:1331-1342`), drops policy refusals (`1351`) and is skipped at the deadline (`1362-1374`) | D |
| Success | Completed, successful, not a candidate | E |
| No win | Completed, `result.successful` False (`veto_reason` says why) | E |
| Stopped: futility, eval_metric, residual, targets, latency_ratio, cost_ratio, not_better, guard_losses | `status == "not_landed"`, `measurement.stop_kind` (`items.py:1618-1633`) | E |
| Stopped: the change did not take effect | `status == "dead_end"`, `measurement.stop_kind == "lever_not_applied"` (`items.py:1580-1597`) | E |
| Capped | `status == "capped"` (`items.py:1556-1565`). A run-budget cap has the same kind; only `stop_line` says "run budget" (`report.py:524`) | E |
| Dead end: <reason> | `status == "dead_end"`. For build and check dead ends, the outcome has no reason. The reason is only in the `item_detail` before it: "dead end: …" (`items.py:732`, `1262`, `1299`, `1409`). The tracker clears `detail` at the outcome (`status.py:590`), so keep the last one | D (text) |
| Failed: <error class> | `status == "failed_infra"`, `infra_error.error_class` | E |
| Not reproduced | Eval build: `status == "skipped_not_reproduced"`. Issue baseline: `status == "completed"` either way; the verdict is only in `item_detail` "reproduced …" / "not reproduced …" (`items.py:409-414`) | E / D (text) |
| Promoted | Finding 11 | D |
| Rejected at val | `rejection_recorded` (`orchestrator.py:3364`), only when both memory writes landed. Else: gate candidates − winner − blocked, when `decision.blocked_on` is None. No rejection is written when the winner is refused at apply (`1638`) | E / D |
| Held | `hold_recorded` (`3347`) / `promotion.decision.blocked_on` | E |
| Blocked: copies eval text | `gate_block` (`1797`) | E |
| Probe answered | `item_probe.probe.answer` + `drives` (`items.py:526`). Finding 7 | E, partial |

Outcomes in the code with **no row** in the spec's table:

| Outcome | Source |
|---|---|
| Stopped by `invalid_output_rate` or `max_items` | `StopKind` (`schemas.py:726-738`) |
| Cut off by the deadline, or by a fatal sibling | Finding 10. The report calls it `cut_off` (`report.py:513-520`) |
| Probe answer "unclear" | `ProbeResult.answer` (`schemas.py:657`); also when the agent could not run it (`items.py:516-521`) |
| Refused by the model policy before the gate | `promotion_blocked` (`orchestrator.py:1351`) |
| Val evidence incomplete, stays pooled | `promotion_blocked` (`1444`); `gate_candidate_ineligible` (`2395`) |
| No usable val evidence (one candidate) | `promotion_blocked` (`1477-1480`) |
| Winner ineligible, or integrity flags not addressed | `promotion_blocked` (`1570`, `1592`) |
| Winner refused at apply | `promotion_blocked` (`1638`) |
| Gate skipped: deadline, or no decision from the agent | `promotion_skipped` (`1368`, `1539`) |

### 1.8 Starting checklist

| Step | Source | Status |
|---|---|---|
| Agent CLI test | `check_agent_cli` (`cli.py:1197`), before any tracker or `FanoutLog`. No event. The app must time it itself. "claude -p" is `agent_cli.text` | CLI |
| Preflight | `phase_start` / `phase_end preflight` (`orchestrator.py:553`/`561`); `preflight` (`kiln_host`, `cost_usd`, `568`) | E |
| Setup | `phase setup` (`589`/`597`); `setup_sync` / `setup_ok` (`setup.py:1271`). Comp "5 evals and 8 issues synced": `seeded` and `refreshed` count evals (`seeded_eval_ids`, `refreshed_eval_ids`, `schemas.py:1153-1154`); only `repaired` counts issues | E; comp wording is loose |
| Baseline | `baseline_found` (`orchestrator.py:611`); `baseline_seeded` (`setup.py:1243`); `phase baseline_measure` (`orchestrator.py:2675`/`2718`); `baseline_progress` every 30 s (`2654`); `baseline_measured` with per eval `stored`, `expected`, `how` (`2739-2745`). "train scores read from Kiln" = every `how == "stored"` | E |
| Model preflight | `model_preflight` with `result.checks[].outcome` (`model_preflight.py:424`, `500`); `model_preflight_flag` / `_untested` (`474`/`484`). With `--skip-model-preflight`, no event at all (`orchestrator.py:633`) | E; CLI for the skip |
| Detectability | `detectability_row`, `detectability_warning`, `detectability` (`detectability.py:635-654`) | E |
| Noise check | `noise_estimate`, `noise_row`, `noise_warning`, `noise_check` (`noise.py:421-581`), only with `--noise-check`. Else no event (`orchestrator.py:645`) | E; CLI for "skipped" |
| Also run, not in the spec's list | `quarantine`, `train_set` (`2947`, `2982`), `eval_unloadable` (`setup.py:1220`), `janitor` (`orchestrator.py:601`), `prior_screening` (`viability.py:547`), `contract_ok` (`orchestrator.py:627`), baseline audits (`generality.py:723`, `762`) | E |
| Recon probes | `wave_start` / `wave_end` wave 0. `recon_skipped` (`orchestrator.py:1120`); `--no-recon` has no event | E / CLI |
| Opening screen | `opening_screen_skipped` (`1081`, `1092`, `1099`); `candidate_budget` / `measure_chunk` with `item_id=None`; `screening_round` | E |

### 1.9 Planning steps

| Step | Source | Status |
|---|---|---|
| Planner session, elapsed | `phase_start` / `phase_end planning` (`orchestrator.py:907`/`994`) | E |
| "asked for a screening round first" | `plan.screen` is not emitted. Derive: screening activity (`queue_drawn` or `candidate_budget` with `item_id=None`, `screen_candidate_dropped`, `screening_cap`) follows the planning `phase_end` | D |
| "Screen 1 · 6 candidates · 16 items each · 3 survive" | Candidates: `candidate_budget` with `item_id=None`, or `len(screening_round.results)`. Items: `queue_drawn.size` with `item_id=None` (capped per eval). Survivors: `results[].verdict == "survivor"` | E / D |
| Round number | `screening_round.round` is a run-wide counter (`orchestrator.py:1050-1053`), not per wave | E |
| Re-plan | A second planning phase in the wave. "Re-planning" is in the label for a replan after probes only; a rescreen's label is the same as the plan's | D |
| Probe phase | `wave_start probe_phase=True` | E |
| Assumed answers | `plan.open_questions` | C6 |

### 1.10 Promotion gate

| Element | Source | Status |
|---|---|---|
| Candidate count | `phase_start` label text (`orchestrator.py:1384-1386`) | E (text) |
| Candidate set | Derive from outcomes (`1331-1342`), less policy refusals (`1351`) | D |
| Val progress | `gate_progress` is a burst after every fill lands (`2286-2299`) | C4 |
| Speed and cost val drives | `_speed_val_pass` (`1887-1925`): no progress; one `speed_result` per rc at the end (`1919`) | Partial |
| Promote agent session | No progress (`1516-1530`) | – |
| Integrity flags | `integrity` (`1747`) | E |
| Decision | `promotion`, `promotion_blocked`, `promotion_skipped`, `hold_recorded`, `rejection_recorded` | E (finding 11) |

### 1.11 Done summary

| Field | Source | Status |
|---|---|---|
| How it ended | `exit.reason` (`orchestrator.py:492`, `505`, `516`, `674-867`): `timeout`, `budget_exhausted`, `planner_failed`, `empty_plan`, the planner's free-text `loop_exit`, `tripwire`, `wrap_up`, `quota_exhausted`, `fatal:<Type>`, `interrupted`. Finding 14 for `wrap_up`. `planner_failed` and `fatal:<Type>` have no plain words in the spec | E, partial |
| Elapsed; spend against budget | Tracker | E |
| Waves, experiments, promotions, stopped early | Count from waves, items and outcomes | D |
| Best Confirmed: rc | `baseline_history[-1]` | E |
| Best Confirmed: name, description | Finding 5. For rcs built this run, `item_change.change.summary` (`schemas.py:1106-1123`) is a short "what changed". `item_change` has no `rc_id`, so join through the item | NA |
| Report path | `report_written.path` (`report.py:931`, `991`). Known up front: `ReportWriter.report_path` (`report.py:894-896`, wired at `cli.py:1315`). The name is `reports/kiln-harness-tuner-<run_id>.md`. An interrupted run writes none (`orchestrator.py:513-517`) | E |
| Links | URL rules (functional spec 7.8) | D |
| "Next: run your test split on <rc>" | rc id | E |

### 1.12 Log lines

| Line | Source | Status |
|---|---|---|
| Promoted, with the val change | `promotion` + finding 11; the target eval's val delta from C2 | D + C2 |
| Rejected at val, "train +5.1, val +0.4" | `rejection_recorded` or derived; the numbers from C1b and C2 | E/D + C1 + C2 |
| Held / blocked | `hold_recorded`, `gate_block`, `promotion_blocked` | E |
| Verdicts, stops | `item_outcome` (1.7) | E |
| Screening round results | `screening_round.results` (`verdict`, `reason`, `line`); `screen_candidate_dropped` | E |
| Probe answers | `item_probe` | E |
| Wave started, "from w3-4" | `wave_start.baseline_rc_id` + `plan_headline` / `items`. "w3-4" needs the rc → item map; "from start" when the rc is Start's | E / D |
| Built: what changed | `item_change.change.summary` (`items.py:987`) | E |
| Check findings | `judge_copy`, `generality`, `reference_check` (with `line`), `item_detail` | E |
| Assumed answers | C6 | C6 |
| Rate limits, errored eval items | `eval_job_errors` (`error_class`, `len(errors)`, `split`, `attempt`; `analysis.py:1275`, `2226`) | E |
| Quota | `provider_quota` (`orchestrator.py:491`), then `exit quota_exhausted` | E |
| Tripwire, backoff | `tripwire` (`1267`); `phase tripwire_backoff` | E |
| Retries | `dispatch_retry` (`items.py:160`) | E |
| Run start, start config | `run_start` (objective, budget, limit) | E |
| Detectability warnings | `detectability_warning.line` | E |
| Exit | `exit` | E |
| Lessons, dead ends, new issues | C7 | C7 |
| Deadline cut-off | `deadline_cancel`, `deadline_grace`, `fatal_cancel` | E. Not in the spec's log table |

### 1.13 Comp values with no source (`ui_preview/sample_data.py`)

| Comp value | Problem |
|---|---|
| `CONCERNS` priorities "p0", "p1", "p2" | Finding 2 |
| Goal "5 evals, weighted by priority" | Not true under quality (1.2) |
| Goal "val 104 · test 104" | Finding 4 |
| `START_TRAIN` for rating, cost, latency, before any arm lands | Finding 6 |
| Best Confirmed "w3-4 · get_container wrapper" | Real rc ids are 12-digit Kiln ids. "w3-4" is an item id. The name is finding 5 |
| `COMPARE_URL` "models=rc-0003,w3-4,…" | `models` takes rc ids, which are numeric |
| `REPORT_PATH` ".../reports/261109-1412-depot-ops-desk.md" | The real name is `kiln-harness-tuner-<run_id>.md` (`report.py:896`) |
| Card "Cost / request · thinking" (w4-6), and in-flight counts on Cost and Latency | Finding 12 |
| Strips with H (held) and S (still failing) | Finding 1 |
| "$1.20 of $4" on arms | Finding 9 (spend and cap measure different things) |
| "$0.60 of $3", "$0.30 of $2", "$0.70 of $2" | Finding 9 (no cap on these items) |
| Gauges "24 of 40 items", "48 of 72 items" in flight | Finding 8 |
| Probe "YES, 3 of 3 drives", "YES, 2 of 3" | Finding 7 |
| Probe steps "✓ asked · ⠸ answering" | One state (1.6) |
| Recon ids "w0-1" | Real: `r0-N` (`probes.py:786`) |
| Log "Screen round 1: 3 of 6 candidates survive" in wave 5, after a wave 3 screen | The round counter is run-wide (1.9) |
| Log "Screen: 2 of 8 models survive" | Candidates are variants: model, provider, thinking, sampling (`schemas.py:672-678`) |
| Log "Building on 41 records from 2 earlier runs" | `prior_screening` counts screening results (`viability.py:547-557`) |
| Log "openai rate limit · 6 eval items retried" | `eval_job_errors` has no provider field, and does not say a retry followed |
| Step "5 evals and 8 issues synced" | 1.8 |
| Baseline "rc 0003" | The name is known in onboarding only (`Baseline.name`, `onboarding/summary.py:122-128`), not passed on |
| Done "Time limit reached" for a `wrap_up` exit | Finding 14 |
| "wave 4 of ~7" | Finding 15 |

These comp values are sound: "rebuilding once · copied text found", "an
agent is writing the change · 3m10s", the Experiments header counts, the
"N experiments in flight" stop dialog, "Output format can only guard: 4
failures, a win needs 6" (from a detectability row's `room` and `floor`),
"Latency · median" (`LatencyComparison` medians), and the Kiln and agent
lanes.

---

## 2. The 7 approved additive changes

Rules that apply to all seven:

- **Use new kinds, not `eval_progress`.** `_on_eval_progress` takes
  `done` and `total` as required arguments (`status.py:538-540`). An
  `eval_progress` event without them raises `TypeError`. `FanoutLog` has no
  guard (`wave_log.py:73-75`), so the error reaches the loop. The proposed
  kinds below collide with no `_on_<kind>` handler (`status.py:190-194`).
- **Send plain data.** `WaveLog._jsonable` dumps Pydantic models, but it
  writes any other object as `repr` (`wave_log.py:22-33`). `Estimate`,
  `Measured`, `Evidence`, `JobHealth` and `Budget` are dataclasses. Convert
  them to dicts. Dump Pydantic models with `model_dump(mode="json")`. The
  dump is also the copy that section 3 needs.
- **`PlainUi` and `LiveUi` ignore new kinds.** Both print only the kinds
  they name and `SETUP_LINE_EVENTS` (`ui.py:469-507`, `817-874`,
  `920-992`). New fields on a kind in `SETUP_LINE_EVENTS` (`measure_chunk`,
  `measurement`) do not change the print, which is `fields["line"]` only
  (`own_line`, `ui.py:510-516`). A spec conflict to settle: section 1 says
  the plain path stays as it is (`functional_spec.md:35`), but the decision
  log says "the plain path gets the lines too" for memory records
  (`functional_spec.md:470`). With new kinds, `PlainUi` prints nothing new,
  and `wave_log.jsonl` gets the lines in every mode.
- **Keep the loop's tracker as it is.** Do not add handlers to the
  `RunStatusTracker` that `ReportWriter` reads (`cli.py:1314`). It feeds the
  report agent (`report.py:957`). The UI's own tracker can be a subclass
  with more handlers.

### Change 1: per-eval train results per experiment

There are two kinds of numbers, at two points. Both points are needed.

**1a. At the end of every arm's measurement (stopped arms included).**

- Where: `items.py` `_measure_arm`, at the `measurement` event (`1522-1533`),
  right after `measure()` returns (`1508-1521`).
- In scope: `measured: Measured` (`measure.py:567-610`): `status`,
  `stop_kind`, `per_eval: dict[str, Estimate]`, `estimate`, `evidence`
  (`Evidence`: `judged: list[Judged]`, `fixed_broke(eval_id)`,
  `guard_losses()`, `eval_names: dict[str, str]`), `planned_per_eval`,
  `latency` and `cost` (against the run's baseline, the timing reference),
  `vs_parent` and `vs_parent_cost` (only when the parent is not the reference
  and timing ran), `spend_usd`, `budget`, `chunks`. Also `item.id`,
  `rc_id`, `parent_rc_id`. `Estimate` (`estimate.py:74-84`) has `net`, `se`,
  `rate`, `n`, `complete`, and `ci()`.
- Proposed kind `arm_estimates`:

```
arm_estimates {
  item_id: str, rc_id: str, parent_rc_id: str,
  status: "completed" | "stopped" | "capped" | "failed",
  stop_kind: str | None,
  per_eval: [ { eval_id: str, eval_name: str,
                net: float, ci_low: float, ci_high: float,
                rate: float | None, n: int, complete: bool,
                judged: int, planned: int, fixed: int, broke: int } ],
  pooled: { net: float, ci_low: float, ci_high: float, n: int } | None,
  latency: LatencyComparison dump | None,          # against the run's baseline
  cost: LatencyComparison dump | None,
  latency_vs_parent: LatencyComparison dump | None,
  cost_vs_parent: LatencyComparison dump | None,
  spend_usd: float, cap_usd: float | None
}
```

- Meaning: `net` is a change in pass rate against the parent. For a rating
  eval the engine makes each score a pass at normalized ≥ 0.5
  (`measure.py:1201`), so this is not the raw rating change that functional
  spec 7.5 asks for. The engine judges only the first score of each target
  (`analysis.py:2094`).

**1b. When a completed arm's analysis bundle exists.**

- Where: `items.py` `_execute_plan_item`, after `bundle.latency` and
  `bundle.cost` are set (`797-800`).
- In scope: `bundle: AnalysisBundle` (`schemas.py:1313-1331`): `per_eval:
  list[PerEvalResult]` (one row per (eval, score), against the parent,
  `analysis.py:1888-1890`), `latency`, `cost`, `errors`; also `rc_id`,
  `parent_rc_id` (`items.py:737-739`), `item.id`. `PerEvalResult`
  (`schemas.py:1224-1310`) has `delta` (raw units: pass-rate fraction or
  rating points), `ci_low`, `ci_high`, `significant`, `n_items`,
  `n_expected`, `coverage`, `baseline_score`, `candidate_score`,
  `score_type`, `direction`, passive `latency` and `cost`.
- Proposed kind `arm_results`: `{item_id, rc_id, parent_rc_id, per_eval:
  [PerEvalResult dump], latency, cost, errors: [ErrorInfo dump]}`.
- This is the source for Best Unconfirmed: only successful arms count, and
  every successful arm has a bundle. It also gives the parent's absolute
  values (`baseline_score`), which the Start (train) tier needs. The
  approved wording of change 1 does not list absolute values. Include them.

**Additive.** Yes. Both are emits of values that already exist. No Kiln
call, no control flow. The cost is one dump per arm.

**Risks.** Train numbers only, which the planner already reads. An arm whose
analysis fails (`items.py:774-788`) has 1a and no 1b. Under quality,
cost and latency are per eval (passive, `method: eval_runs`), so the app
needs a rule to show one number per config.

### Change 2: val tables per gate

- Where: `orchestrator.py` `_run_promotion_gate`, at the point where the
  tables are final. Under quality: after `_record_gate_blocks` (`1412`).
  Under speed and cost: after the second `write_val_summary` (`1425`),
  which follows the speed pass (`1421`) and the eligibility marks
  (`1423-1424`). One helper, called at the two points.
- In scope: `tables: list[CandidateValTable]` (`schemas.py:1883-1923`):
  `rc_id`, `experiment_memory_id`, `per_eval: list[PerEvalResult]` (val
  rows; `baseline_rc_id` is the incumbent; `baseline_score`,
  `candidate_score`, `delta`, `ci_low`, `ci_high`, `n_items`, `n_expected`,
  `coverage`, passive `latency`, `cost`), `errors`, `ineligible`, `skipped`,
  paired `latency` / `cost` (speed and cost only), `integrity_flags`. Also
  `baseline.rc_id` (`1408`) and `ctx.objective`. The wave number is not in
  scope there (it is in `_maybe_promote`); the gate's `phase_start` has it.
- Proposed kind `gate_val_tables`: `{baseline_rc_id: str, objective: str,
  tables: [CandidateValTable dump]}`.
- Additive: yes.
- Risks:
  - The tables are changed in place during the gate: integrity
    (`1681-1744`), the speed pass (`1899-1918`), eligibility
    (`1968-1969`). Dump them at emit time. An emit at the first
    `write_val_summary` (`1410`) would miss the integrity and speed data.
  - Val numbers go into `wave_log.jsonl`. That file is in `RUN_DIR_ENTRIES`
    (`cli.py:651`), so the read fence denies it to agents (`cli.py:624-639`).
    Nothing reads the wave log back (no reader in `src/`). Keep it out of the
    loop's `RunStatus` (see the rules above).
  - `val_summary.json` keeps the latest gate only (`1874-1884`). The event
    keeps every gate.

### Change 3: structured chunk counts

- Where: `measure.py` `_measure`, the `measure_chunk` emit (`1268-1275`).
- In scope: `s.ev: Evidence` (`judged_n` `stop_rules.py:343-344`,
  `fixed_broke()` `346-351`, `guard_losses()` `360-371`, `judged`),
  `s.spend`, `s.budget` (`Budget.usd`, `.source`; re-priced at `1248`),
  `s.status`, `s.stop_kind`, `decision`, `k`, `len(bounds)`, and
  `own_per_eval(s)`.
- Proposed new fields on `measure_chunk`: `judged: int`, `planned: int`,
  `fixed: int`, `broke: int`, `spend_usd: float`, `cap_usd: float | None`,
  `decision: str`. Under speed and cost also `lost`, `guards`, `fixes`,
  `fails` (from `guard_losses()`).
- **Needed for the strip, beyond the approved counts:** `held: int` and
  `still_failing: int` (and `unknown_parent: int`), or `cells: [[eval_id,
  item_id, parent_pass, cand_pass]]` for the rows judged in this chunk
  (`s.ev.judged[previous_n:]`, at most one chunk of items). The cell order is
  the judging order.
- Additive: yes. `measure_chunk` is in `SETUP_LINE_EVENTS`, but `PlainUi`
  prints only `line`.
- Risks: "fixed" and "broke" are against the queue parent, but the existing
  text says "vs baseline". `spend_usd` and `cap_usd` here are the right
  pair for "spend against cap" (finding 9).

### Change 4: gate progress per val fill

- Where: `orchestrator.py` `_assemble_val_tables`, inner `fill()`
  (`2230-2248`), after `ensure_measured` returns (`2234-2236`). And once
  before the fills start (`2253`).
- In scope: `eval_id`, `eval_config_id`, `rc_id`, the result `(runs:
  list[dict], health: JobHealth)` (`analysis.py:206-224`: `n_expected`,
  `missing_item_ids`, `error_messages`, `job_id`), `baseline_rc_id`,
  `fill_keys`, `gate_evals`.
- Proposed kinds:

```
gate_fills_planned { baseline_rc_id: str, rc_ids: [str], eval_ids: [str], fills: int }
gate_fill { rc_id: str, eval_id: str, role: "baseline" | "candidate",
            stored: int,           # pairable_item_count(runs), analysis.py:393-401
            expected: int | None,  # health.n_expected
            missing: int, clean: bool, done: int, total: int }
```

- Additive: yes. `pairable_item_count` is pure. `done` is a counter in a
  closure. A failed fill already emits `val_fill_failed` (`2238`).
- Risks: do not reuse `gate_progress`. It is in `SETUP_LINE_EVENTS`, so a
  per-fill `gate_progress` would add plain lines. Progress moves one fill at
  a time: a 5-eval gate moves in 5 steps per candidate, and cached fills
  (`measurement_shared`, `analysis.py:1138`, `1149`) land at once. The
  speed and cost val drives after the fills, and the promote session, have
  no progress. The denominator before a candidate's fills land: the sum of
  `val_n` over the gate's evals (one per eval) from the detectability rows.

### Change 5: item details at wave start

- Where: `status.item_summary` (`status.py:96-97`). It is loop code: the
  orchestrator calls it to build every `wave_start.items` (`orchestrator.py:774`,
  `827`, `1131`). (`runtime_data.md` section 7 calls it display-side. It is
  not.)
- In scope: `item: BatchItem` (`schemas.py:438-578`).
- Proposed added keys per item (all copies): `type`, `hypothesis`,
  `technique`, `issue_memory_id`, `wildcard`, `needs_eval`, `eval_id`,
  `targets: [{eval_id, scores}]`, `promotion_candidate`, `max_usd`,
  `merge_wins: [{experiment_memory_id, rc_id}]`, `probe: {question,
  max_drives, for_idea, code: bool}`, `remeasure: {rc_id, eval_ids}`,
  `measure: {eval_id, scores}`, `parent_rc_id`, `existing_rc_id`.
- Additive: yes. The tracker reads `item_id`, `kind`, `label` only
  (`status.py:451-456`). `PlainUi` prints those three only (`ui.py:957-960`).
- Risks: `_execute_plan_item` changes the item during the wave:
  `item.work.eval_id = …` (`items.py:691`) and
  `item.evaluation_target.append(…)` (`701`). Copy the lists
  (`model_dump`); never put the item or its lists in the event. A
  `needs_eval` item's targets grow after wave start; the final item is in
  `item_outcome.outcome.item`.

### Change 6: the planner's open questions

- Where: the three `write_open_unknowns` calls (`orchestrator.py:740` plan,
  `800` replan, `1067` rescreen). Emit after the write, so the event names
  only what landed.
- In scope: `plan.open_questions: list[OpenUnknown]` (`question`,
  `default_assumed`, `gates`, `reask_when`; `schemas.py:172-176`), `wave`,
  the phase, and the constraint ids the call returns
  (`memory.py:1757-1800`). The loop drops the return value today.
- Proposed kind `open_questions`: `{wave: int, phase: "plan" | "replan" |
  "rescreen", questions: [{question, default_assumed, gates, reask_when,
  constraint_id}]}`. Skip it when the list is empty.
- Additive: yes. Binding the return value is not a control-flow change.
- Risks: plan, replan and rescreen can repeat a question. The write PATCHes
  the same record, so the id repeats. The app de-duplicates by
  `constraint_id`. That id is also `hold_recorded.constraint_id`
  (`orchestrator.py:3347`), so a hold can name its question.

### Change 7: memory records the agents write

**Gateway side.**

- Where: `gateway/ops.py` `memory_create` (`782-789`) and `memory_update`
  (`791-800`), after the Kiln call returns.
- In scope: the projected answer `m.MemoryView` (`gateway/models.py:263-271`:
  `id`, `overview`, `tags`, `scope`, `created_at`, `updated_at`), and
  `self.s.role`, `self.s.run_dir`.
- File: `gateway_memory.jsonl` in the run folder, a constant beside
  `CREATED_FILE` (`ops.py:46`).
- Line: `{"ts": local ISO, "op": "create" | "update", "id": str, "role":
  str, "scope": str, "tags": [str], "overview": str}`. No `content` (up to
  4000 characters, `models.py:446`).
- Write it the way `State.record` does: one `os.write` with `O_APPEND`
  (`ops.py:148-163`). Unlike `record`, catch `OSError`: a failed append must
  not fail the agent's write.
- Add `"gateway_memory.jsonl"` to `RUN_DIR_ENTRIES` (`cli.py:648-660`).
  Then `prepare_run_dir` makes it before the first session
  (`cli.py:663-672`), and the fence denies it from the first session. An
  entry not listed is denied only from the next session on
  (`cli.py:641-646`).
- Roles that write memory: `batch_plan`, `create_experiment_rc`,
  `analyze_experiment_results`, `eval_build` (`_MEM_WRITE`), and `probe`
  (create only) (`ops.py:1312-1404`).

**Loop side: where a session ends.**

- Every agent session ends in `ClaudeCliDispatcher._run`
  (`dispatch/claude_cli.py:1103-1117`), after `_persist_step` (`1116`).
  The Codex and OpenCode dispatchers subclass it (`codex_cli.py:286`,
  `opencode_cli.py:212`) and do not override `_run`. `preflight`
  (`claude_cli.py:1050`) does not go through `_run`, and has no memory
  operations.
- The dispatcher has no event log. Add an optional attribute
  `on_session_end: Callable[[str, str], None] | None`. Call it after
  `_persist_step` with `(agent, label)`, inside `try/except`. `cli.py` sets
  it once the fan-out exists, beside `ctx.analyzer.log = ctx.log`
  (`cli.py:1313`).
- The forwarder keeps a byte offset into the file. It reads only complete
  lines (a still-running gateway may be mid-append). For each new line it
  emits, through `ctx.log` read at call time (so `_ReconcileBeforeExit`
  wraps it):

```
memory_written { id: str, op: "create" | "update", role: str, scope: str,
                 tags: [str], overview: str,
                 kind: str,           # memory_schema.record_kind(tags, overview), memory_schema.py:203-213
                 written_at: str,     # the gateway's ts
                 forwarded_by: str }  # the label of the session whose end forwarded it
```

- Add one more forward before the first `exit`, for example in
  `_ReconcileBeforeExit.event` after `reconcile` (`orchestrator.py:446-450`).
  A cancelled session never reaches `_persist_step`.
- Additive: yes. No Kiln call (the view is the write's own answer). One
  small append per agent memory write, and one file read per session end.
- Risks:
  - A line cannot be tied to its session. Concurrent sessions append to one
    file, and the gateway knows its role but not its item (the MCP config is
    per role: `gateway/server.py:9-12`, `46-60`). So `forwarded_by` is not
    the writer. Use `role` from the line.
  - Code-written records go through the in-process `MemoryWriter`, not the
    gateway: probe lessons, screen results, verdicts, dead-end stamps,
    open questions. They are not in this file. They are already evented
    (`item_probe`, `screening_round`, `item_outcome`, `item_detail`, C6).
  - The app filters by `kind` (lesson, dead_end, issue). An agent's
    `memory_update` can touch other kinds.

---

## 3. Thread safety of event payloads

### 3.1 How payloads reach a second thread

- Every emit runs on the loop's one asyncio thread. One worker thread
  exists (`reference_check.py:377`, `asyncio.to_thread(scan)`), but `scan`
  is pure and emits nothing (`reference_check.py:371-379`).
- `FanoutLog.event` calls each child with `**fields` (`wave_log.py:73-75`),
  so each child gets a new dict. The values are shared objects.
- `WaveLog` adds `ts` in the file only (`wave_log.py:47`). The other
  consumers get no time.
- No event carries a `RunStatus` or tracker fragment.

### 3.2 Field types per kind the UI needs

"Fresh" means the emitter builds the value for the event (a literal, a
`list(...)` copy, a `model_dump`). "Shared" means the event holds a live
object the loop keeps.

| Kind | Fields and types | Mutable shared values | Verdict |
|---|---|---|---|
| `run_start` | `timeout_mins:int`, `deadline:float`, `deadline_iso:str`, `budget_usd:float\|None`, `objective:str`, `model_policy:dict\|None` | `model_policy` is fresh (`model_policy.py:116-129`) | Safe |
| `phase_start` / `phase_end` | `name:str`, `label:str`, `wave:int?`, `elapsed_secs:float` | – | Safe |
| `wave_start` / `wave_end` | `wave:int`, `should_wrap_up:bool`, `baseline_rc_id:str`, `plan_headline:str`, `rejected_summary:str`, `items:list[dict[str,str]]`, `probe_phase:bool?` | `items` is fresh (`status.py:96-97`). C5 must stay fresh | Safe |
| `item_state`, `item_detail` | `item_id:str`, `state:str` / `detail:str` | – | Safe |
| `item_change` | `change:dict` | Fresh dump (`items.py:987`) | Safe |
| `item_probe` | `probe:dict` | Fresh dump (`items.py:526`) | Safe |
| `item_outcome` | `outcome:ItemOutcome` | **Shared.** A non-frozen Pydantic model; no schema model is frozen (`schemas.py`, `model_config` only at `797`). `outcome.item` is the plan's own `BatchItem`. The last change before the emit is `items.py:297`. After it, the loop only reads it (`check_wave_tripwire`, `_maybe_promote`, `_apply_promotion`). Safe in practice, not by construction | Treat as read-only |
| `spend` | `source:str`, `lane:str`, `cost_usd:float`, `unmeasured_tokens:int?`, `evidence_cost_usd:float?`, `item_id:str?` | – | Safe |
| `candidate_budget`, `measure_chunk`, `measurement` | str, int, float | – (C3 adds ints and floats) | Safe |
| `queue_drawn` | `size:int`, `seed:str`, `population:dict`, `skipped:dict`, `queue:list[list[str]]`, `fragility:dict?`, `per_eval_caps:dict?` | **Shared:** `population`, `skipped`, `fragility` are the `Queue`'s own dicts (`measure.py:812-821`). The engine only reads them after the emit. `queue` is fresh (`to_json`) | Safe in practice |
| `trial` | `eval_id`, `rc_id`, `landed:bool`, `scores:dict[str,float\|None]` | `scores` is also `TrialResult.scores` (`analysis.py:2119-2154`); only read after | Safe in practice |
| `screening_round` | `round:int`, `line:str`, `results:list[dict]` | Fresh dumps (`viability.py:479-488`) | Safe |
| `judge_copy`, `generality`, `reference_check` | str, `list[str]` | `list(...)` copies (`items.py:1016-1024`, `1061-1070`, `1110-1120`) | Safe |
| `integrity`, `gate_block` | `flags` / `reasons: list[str]` | `list(...)` copies (`orchestrator.py:1747-1751`, `1797-1802`) | Safe |
| `speed_result` | `latency`, `cost: dict\|None` | Fresh dumps (`1919-1924`) | Safe |
| `promotion` | `winner_rc_id:str\|None`, `rationale:str`, `decision:PromotionDecision` | **Shared** Pydantic model; read only after | Treat as read-only |
| `promotion_blocked`, `promotion_skipped` | `winner_rc_id`, `reasons:list[str]`, `reason:str`, `candidate_rc_ids:list[str]?` | Fresh lists at every site | Safe |
| `rejection_recorded`, `hold_recorded` | str | – | Safe |
| `setup_sync` / `setup_ok` | ints, `cost_usd:float`, `report:SetupReport` | **Shared.** Its last changes (`setup.py:1263-1264`) come before the emit (`1271`) | Treat as read-only |
| `detectability` | `line:str`, `report:DetectabilityReport` | **Shared**, complete before the emit (`detectability.py:620-649`) | Treat as read-only |
| `noise_check` | `line`, `result:NoiseCheckResult` | **Shared**; `result.rows` is also `ctx.train_noise` (`noise.py:568`), read only | Treat as read-only |
| `model_preflight` | `line`, `result:ModelPreflightResult` | **Shared**, complete before the emit (`model_preflight.py:468-500`) | Treat as read-only |
| `baseline_speed` | `rc_id`, `line`, `latency:LatencyComparison\|None` | **Shared**: the same object as `ctx.baseline_speed[rc]`, later handed to `BatchPlanInput`. Changed only before the emit (`orchestrator.py:2785-2792`) | Treat as read-only |
| `baseline_measured` | `evals:dict` | Fresh (`2742-2744`) | Safe |
| `eval_job_errors` | `errors:list[dict]` | A local list per attempt (`analysis.py:1275-1283`) | Safe |
| `janitor` | `memory_ids:list[str]` | The janitor's return value, not kept | Safe |
| `deadline_cancel`, `deadline_grace`, `fatal_cancel` | `item_ids:list[str]` | Fresh | Safe |
| `tripwire`, `exit`, `report_written`, `provider_quota`, `dispatch_retry`, `preflight`, `train_set`, `quarantine` | primitives | – | Safe |
| C1–C7 as proposed | primitives, lists, dicts | All fresh dumps | Safe |

The mutable shared objects (`ItemOutcome`, `PromotionDecision`,
`SetupReport`, `DetectabilityReport`, `NoiseCheckResult`,
`ModelPreflightResult`, the `baseline_speed` comparison, and the
`queue_drawn` dicts) are not changed after their emits today. Nothing
enforces this. Two cheap guards: the UI's queue sink dumps these fields
(`model_dump`) on the loop thread at enqueue, or the loop emits dumps.
The first guard changes nothing in the loop. Its cost is one dump per
outcome and per setup report. The UI's tracker then needs the same
re-hydration that replay needs (`runtime_data.md` section 5,
step 2): `_on_item_outcome` stores whatever it gets (`status.py:588`), and
`_on_promotion` reads `decision.winner_rc_id` as an attribute
(`status.py:612-622`), so a dict there raises.

### 3.3 Does a second tracker reach the same state?

The fold is deterministic given the events and the clock readings:

- Dispatch is by kind, to `_on_<kind>` (`status.py:190-194`). Unknown kinds
  are ignored.
- The handlers read only their arguments and `self._clock()`. They use pure
  helpers (`lane_for_source`, `ScreenResult.model_validate`,
  `RcChange.model_validate`, `ProbeResult.model_validate`). They read no
  file and no global state. `_on_speed_result` uses `len(waves)`, which
  comes from events (`status.py:281`).
- Phase segments use the event's own `elapsed_secs` (`status.py:405-417`).
  Every `phase_end` carries it, so segments come out the same.

The clock is the difference. The tracker reads `self._clock()` at
construction (`_run_started`, `status.py:177`), at `wave_start` (`436`), at
`phase_start` (`393`), at `item_state` (`492`) and at `item_outcome`
(`591`). The loop's tracker reads it at emit time, because the fan-out is
synchronous. A tracker fed from a queue reads it at dequeue time. So:

- Item `elapsed_secs` and `phase_secs` move by the queue delay. A stall on
  the UI thread (a large render) makes a later event late, and a duration
  longer.
- `elapsed_secs` (`status.py:203`) counts from the UI tracker's own
  construction. If the app builds its tracker at launch (to show the agent
  CLI test), the elapsed time includes the start-up. The loop's tracker
  starts just before `orchestrate` (`cli.py:1293-1299`).
- `time_remaining_secs` is right if the UI tracker's clock is
  `time.monotonic`: `run_start.deadline` is `time.monotonic` based
  (`orchestrator.py:252`, `459`; `build_ctx` passes no clock), and that
  clock is the same on every thread of the process.

To get the loop's numbers exactly: stamp each event with `time.monotonic()`
in the queue sink, on the loop thread. Give the UI tracker a clock that
returns the stamp of the event being folded, and `time.monotonic()` at
other times (for `snapshot()`'s live values). Fold and snapshot both run on
the UI thread, so the switch needs no lock.

Two more points:

- `RunStatus` is not all the UI needs. It keeps no start times, no rc → item
  map, no chunk counts, no screening rounds, no deadline cancels, and none of
  C1–C7. The app needs its own fold beside the tracker (or a subclass).
- The queue sink must never raise. An unbounded `queue.SimpleQueue.put`
  does not. Put it where `ui` is in the fan-out today (`cli.py:1310`).

---

## 4. Identifiers and names

| Item | Where the UI gets it | Status |
|---|---|---|
| Run id | `run_id` local (`cli.py:1206`); format `YYMMDD-HHMM`, with `-2`, `-3` when taken (`status.py:57-60`; `cli.py:1045-1063`). Also the run folder name (`viability.run_label`, `viability.py:507-510`) | CLI |
| Project id, task id | `target_from_args(args)` (`cli.py:1194`) → tracker constructor (`cli.py:1294-1295`) → `RunStatus.project_id`, `task_id` | CLI / E |
| Project name, task name | Onboarding only (`onboarding/flow.py:310`; `summary.py:141-143`) | CLI, needs onboarding to return the selection (1.1) |
| Kiln host | `args.kiln_host` → `target.kiln_host` (`TargetBlock`, `schemas.py:159-166`); `preflight.kiln_host` (`orchestrator.py:568`) | CLI / E |
| Agent CLI | `parse_agent_cli` (`cli.py:1177`): `.kind` → `RunStatus.agent_cli` (`cli.py:1298`); `.text` (`cli.py:1226`). Lane label: `spend_lane_labels` (`schemas.py:85-90`) | CLI / E |
| Slot counts | `args.max_concurrent_agents`, `args.max_concurrent_evals` (`cli.py:344`, `325`; defaults `139`, `138`); to `build_ctx` (`cli.py:1272-1273`) | CLI |
| Eval names | `detectability.report.rows[].train.eval_name` (`schemas.py:1572`). Mid-run evals: none (C1a can add `Evidence.eval_names`) | E, partial |
| Score names | `score_key` only (rows; C1, C2 rows) | NA for display names |
| Eval priorities | Finding 2. A mid-run eval's priority is in `EvalBuildReport.priority` (`schemas.py:1103`), which reaches only the steps file | NA |
| Score type | `rows[].train.score_type` (`five_star` = rating 1–5; `pass_fail`, `pass_fail_critical` = pass rate; `analysis.py:82-83`). Also `PerEvalResult.score_type` in C1b and C2 | E |
| Direction | `rows[].train.direction`; `PerEvalResult.direction` | E |
| Kind of change | `FixWork.technique` (`schemas.py:181-183`) via C5, or `outcome.item.work.technique`. Item type: `ItemStatus.kind` (fix, merge, eval, measure, remeasure, probe; `status.py:63-72`) | C5 / E |
| Best Confirmed rc id | `baseline_history[-1]`; `wave_start.baseline_rc_id` (`orchestrator.py:824`) | E |
| Run config names and descriptions | Finding 5 | NA |
| rc → item id | `item_outcome.outcome.rc_id`; in flight: `candidate_budget`, `measure_chunk`, `measurement`, `judge_copy`, `generality`, `reference_check` carry `item_id` + `rc_id` | E |
| Split sizes | Train total: `train_set.items` (`orchestrator.py:2947-2956`). Train per eval: `baseline_measured.evals[e].expected` (`2742-2744`), or detectability `n + unscored`. Val per (eval, score): `val_n`. Val + test union: `quarantine.heldout_items`. Test: none | E, partial; finding 4 |

---

## 5. Clocks

### 5.1 What the loop uses

| Clock | Where | Used for |
|---|---|---|
| `time.monotonic` | `Ctx.clock` default (`orchestrator.py:252`); `build_ctx` passes none | `run_start.deadline` (`459`), every `elapsed_secs` (phases, waves), `should_wrap`, the deadline checks |
| `time.monotonic` | Tracker `clock=ctx.clock` (`cli.py:1297`) | `elapsed_secs`, `time_remaining_secs`, item and phase timings |
| `datetime.now()`, local, naive | `run_start.deadline_iso` (`orchestrator.py:467`); `WaveLog` `ts` (`wave_log.py:47`); `run_started_at` and the run id (`cli.py:1205-1206`); steps files' `started`, `ended` (`dispatch/claude_cli.py:1114`, `1131-1144`); `PlainUi` stamps (`ui.py:910-912`) | Labels and audit times |

No event field carries a time of day, except `run_start.deadline_iso`. The
rest are monotonic: `deadline` (an absolute monotonic value) and durations
(`elapsed_secs`, `backoff_secs`, `grace_secs`).

### 5.2 How the UI shows wall-clock times

- **Stamp at enqueue.** The queue sink runs on the loop thread, in the same
  synchronous fan-out as `WaveLog`. Stamp each event with
  `time.monotonic()` and `datetime.now()` there. The wall stamp then matches
  the `ts` in `wave_log.jsonl` to within microseconds.
- **Log line times.** The wall stamp.
- **"started 14:12".** The wall stamp of `run_start`. The run id's minute
  and `run_started_at` (`cli.py:1205`, the budget's clock zero) are a few
  seconds earlier. Time spent before `run_start`, such as the agent CLI test
  (`cli.py:1197`), is outside the time limit: the deadline starts in
  `_orchestrate` (`orchestrator.py:459`).
- **"ends 17:12".** `run_start.deadline_iso`. It is computed at the same
  moment as the monotonic deadline.
- **Timeline positions.** Use monotonic stamps. The axis starts at
  `run_start.deadline − timeout_mins × 60`, the loop's own start in its own
  clock. A segment starts at its start event's stamp and ends at its end
  event's stamp (or at the start plus `elapsed_secs`).
- **Clock labels.** Compute each label as the `run_start` wall stamp plus
  the monotonic offset. Then an NTP step or a DST change does not move the
  labels against the axis.
- **Live values between events.** Spinners, elapsed time and the now marker
  read `time.monotonic()` on the UI thread. It is the same clock as the
  loop's in-process deadline.

---

## 6. Corrections to `runtime_data.md`

| `runtime_data.md` says | The code |
|---|---|
| 1.4: "No loop code runs in a thread: there is no `to_thread`" | `reference_check.py:377` runs `asyncio.to_thread(scan)`. `scan` emits nothing, so every event still comes from the loop thread |
| 2.4: `item_outcome` comes "exactly one per item" | Not for items cancelled at the deadline or by a fatal sibling (`orchestrator.py:1229-1247`). Finding 10 |
| 4.4, 4.8: `promotion` → "promotion or not ✔" | `promotion` comes before `_apply_checked`, which can still block (`orchestrator.py:1638`). The tracker records the winner anyway. Finding 11 |
| 2.2: `prior_screening` has `restored:int` | `restored` is `list[str]` (`viability.py:534-549`) |
| 4.7: per-item fixed/broke can be recomputed from `queue_drawn` + `trial` "joined on rc_id" | `queue_drawn` has no `rc_id` (`measure.py:812-821`). The join goes through `candidate_budget` or `measure_chunk` (item_id + rc_id). The "named" stratum's parent result is not in the queue JSON (`measure.py:192-198`, `436`) |
| 4.10, 7: agent-written lessons: "No core event is possible" | The gateway already appends to a run-folder file (`gateway_created.jsonl`, `ops.py:148-163`). The same path works for memory writes (change 7) |
| 7: extend `status.item_summary`, "a display-side helper" | The orchestrator calls it to build `wave_start` (`orchestrator.py:774`, `827`, `1131`). Changing it changes the loop's events (additively) |
| 4.7: "Cap: (a) `candidate_budget`" | Also, `ItemStatus.spend_usd` counts every lane of the item, while the cap counts the measurement's provider dollars only (finding 9) |
| 2.4: `item_change` fields | Correct, but note that it has no `rc_id` (`items.py:987`) |

Everything else checked in `runtime_data.md` sections 1–4 still holds,
including the emitter line numbers.
