---
name: kiln-harness-tuner-report
description: |
  The Kiln Harness Tuner report-writer agent's procedure: turn the run's structured RunStatus history plus exit-time memory state into the round report, returned as markdown (code writes reports/kiln-harness-tuner-<run_id>.md). Load when dispatched as the Kiln Harness Tuner report-writer agent (template `report_writer.md` — input is a ReportInput embedding RunStatus). Covers the report template, how to read RunStatus, val-grade labeling, and which memories to read at exit.
---

# kiln-harness-tuner-report

You write the human-facing round report at loop exit. Input is `ReportInput`:
`run_status` — the full structured history. You **return the report as
markdown** in `ReportMarkdown` (`{markdown: "..."}`); **code writes the
file** (`reports/kiln-harness-tuner-<run_id>.md`) — never write files or memories yourself.

Read memory at exit for what `RunStatus` doesn't carry (load the **`memory`**
skill): the `baseline` record (allowed here — exit-time, human-facing; the
current rc id is its `rc_<id>` tag, the promotion chain is its content), the
`val` record (also allowed here, and the **only** place promotion val
summaries live — its overview is generic by design, so hydrate the content
with `memory_get(ids=[…])`;
label its numbers val-grade in the report), `issue` records
(what got `FIXED:`/`WONT_FIX:`/reopened), `OPEN:` constraint records (open
unknowns + unconfirmed assumptions), and experiment records for detail the
wave summaries lack.

**Kiln is reached through your gateway operations only.** Your session's
Kiln tools are the typed operations named
`mcp__kiln_harness_tuner_gateway__<operation>` that your role needs; the
**`kiln-harness-tuner-gateway`** skill lists each one with its fields and an example.
A loop session has no generic Kiln API tool and no second lane: no HTTP from
Bash, no reading the project's files, no discovering routes. Every answer
holds train records only, and a refusal is final. There is no exception — if
an operation you need does not exist, say so in what you return instead of
going looking.

## Reading `RunStatus`

- Top level: `project_id`/`task_id`/`guidance` (the kickoff ask — open the
  report against it), `baseline_history` (rc ids in promotion order),
  `spend_total_usd` (the run's spend: the task's model calls through Kiln,
  the only spend the budget counts), `exit_reason`.
- Spend dimensions: `spend_by_lane` (lane → total) and `spend_by_source`
  (source → lane → $). Two lanes, and **call them by their display names**,
  which is what the code-computed appendix's columns say: `provider` =
  **Kiln** — the provider tokens Kiln burns on the drives and eval jobs this
  run triggered (subject `task_run_usage` + judge `eval_usage`), real money
  today, and the only spend in the budget; `subscription_equivalent` = the
  agent lane, named for the agent CLI (**Claude Code**, **Codex** or
  **OpenCode**) — the agent sessions, on the agent CLI's own account. State
  the agent lane in tokens only: `agent_tokens` and `agent_tokens_by_source`,
  a rough count. Never state it in dollars: no dollar figure in `RunStatus`
  includes the agents. Each item's `spend_by_lane` carries the Kiln money for
  that item alone.
- The run's own clock: `elapsed_secs` (how long the run ran),
  `timeout_mins` (its budget, `None` if it had none) and
  `time_remaining_secs` (meaningful only when `time_remaining_known`).
  **These are the run-level totals** — and code already states the first two
  in the appendix header, so quote that header, never a total of your own.
- Time breakdowns: `phase_segments` (run-level spans — preflight, per-wave
  planning, promotion gates, tripwire backoffs — with their own
  `elapsed_secs`) and each item's `phase_secs` (seconds per state: planned /
  building_eval / building_rc / measuring / analyzing). These are where every
  "where did the time go" sentence must come from — they explain the
  **breakdown**, and are never summed into the run's total (they don't
  cover the whole run, and `elapsed_secs` above already is it).
- `setup`: the pre-flight setup phase's record (`SetupReport`) — how the
  baseline was picked when the run seeded one (`baseline_rule`/
  `baseline_rc_id`), how many evals are active, and the id lists: issues
  seeded/re-synced, bindings repaired, issues flagged `UNMEASURABLE:`, and
  `unbound_unresolved_issue_ids` — issue memories with no eval binding that
  setup refused to guess at. The last two are a human's work queue: name
  them in the Setup section. `None` = the phase did not run.
- `unloadable_evals`: the active evals whose default config Kiln can't load
  (`eval_id`, `name`, `reason` — Kiln's own message). The run left them out:
  they were never measured or gated, and their issues were left as they
  were. Name each in the Setup section, under the evals count, as one line:
  `Left out: "<name>" (Kiln can't load it: <reason>)`, joined with `; `.
- `eval_building`, `dropped_eval_items` and `issues_without_eval`: eval
  building is experimental and off by default; `eval_building` is `true`
  only when the run turned it on. While it is off, code drops each planned
  item that would build a new eval (an `eval` item, or a `fix` that needs an
  eval) before its wave runs: `dropped_eval_items` lists each one
  (`item_id`, `wave`, `kind`, `issue_memory_id`). A dropped item never ran
  and has no outcome. `issues_without_eval` is the open issues no eval
  binds (`issue_memory_id`, `overview`), read from memory when the report
  is written, so it includes the issues this run created; `null` when it
  was not read. While eval building is off, code appends an "Issues with no
  eval (code-computed)" section after your markdown: these issues, with the
  advice to create each eval in the Kiln app and a link to the Kiln docs.
  Name the issues in Suggested next steps and point at that section; never
  restate its link or call a dropped item a failure.
- `detectability` and `noise_check`: the pre-run check —
  per (eval, score) the train items with room, the significance floor, and
  the val split's size with an estimate of its room — and, with
  `--noise-check`, how often a re-drive and a re-judge changed a sampled
  train item. Code appends a "Detectability (code-computed)" table after
  your markdown. Point at it; never restate its numbers. Its val columns
  are a split size and an estimate, never a val result, and they are
  val-grade. If `detectability.overridden` is true, say in the Setup section
  that the check refused and the run went on under `--allow-underpowered`.
- `model_preflight`: the start-up model preflight — each
  model checked (`checks[]`: provider, model, `baseline`, `outcome`,
  `reason`, `attempts`, `context_window`), the baseline's estimated prompt
  size, `untested` (allowed models not checked) and `spend_usd`. An
  `outcome` of `flagged` is a hard failure: the model was left out of
  screening and measurement for this run. `untested` means the check could
  not decide, and nothing was excluded. Name each flagged and each untested
  model and its reason in the Setup section. Code appends a "Model
  preflight (code-computed)" table after your markdown; point at it, and never restate its numbers.
- `gate_blocks`: every candidate the promotion gate blocked
  this run because its prompt copies eval text — `rc_id`,
  `experiment_memory_id` and `reasons` (each names the copied passage and
  the eval object: `the candidate's prompt quotes the spec of eval X
  verbatim (…, e.g. "…")`). Report each one in the Promotions section with
  its reasons. The block came after a full val pass, so say what it cost the
  round, and that the run config is stamped `— REJECTED AT VAL` and is not
  built on unless the phrase is removed. An arm the build-time judge-copy
  check ended is an item with `dead_end` and a `judge copy:` reason; report
  it like a generality dead end.
- `waves[]`: `wave`, `should_wrap_up` (the wrap-up wave), `plan_headline`,
  `rejected_summary`, `items[]`, `promotion` (a `PromotionDecision` — winner
  + rationale + regressed issues), `promotion_skipped` (a promote hard-fail
  — report it: the candidates' wins stayed pooled), `tripwire` (aborted
  wave, infra class).
- `items[]`: `item_id`/`kind` (`fix` / `merge` / `eval` = build a new eval /
  `measure` = run an existing eval against the baseline to settle an
  `UNMEASURED:` issue)/`label`, `outcome` (`status`:
  `completed` / `skipped_not_reproduced` (eval couldn't reproduce the issue
  → `WONT_FIX:`) / `failed_infra` / `abandoned` / `not_landed` (a stop
  rule of the measurement engine ended the arm before its train split was
  judged in full: `outcome.measurement` carries the rule, the
  line with its numbers, the items judged and the net estimate with its
  interval; `outcome.residual` the per-item scores when the discriminating
  block stopped it; an older run may carry `outcome.trial`
  instead) / `capped` (the arm reached its spending cap —
  inconclusive, not a dead end or a failure; `outcome.measurement` has the
  cap, the spend and the items judged; report it as unfinished, not as a
  loss); `result` — the analyzer's
  `successful` + `findings` + `memory_overview`, `None` for `eval` and
  `measure` items, which run no analyzer), `spend_usd`, `elapsed_secs`. A
  completed `measure` item carries no verdict field: whether the baseline
  reproduced the issue is in the issue memory, not in `RunStatus` — say it
  measured, and read the memory if the round's story needs the answer. An
  unfinished item (`outcome: null`) means the run ended mid-wave — say so.
  `outcome.measurement.judged_per_eval` and `planned_per_eval`
  give, per eval, the items judged and the items the arm set out to judge.
  Code appends a "Why each candidate stopped (code-computed)" table: one row
  per `fix` and `merge` item, the stop rule or outcome in one plain sentence,
  and "in full" or "partly: <judged> of <planned> items". When an arm was
  measured only partly, say which rule stopped it and point at that table.
  Never call a partly measured arm unfinished work that should have run in
  full: a stop rule ended it on purpose.
- `screen_results`: the first 12 rows of the trade-off table
  below, in its order, without per-eval counts; the table itself has every
  candidate this run screened (`ScreenResult`): label, `overrides`, `items`,
  `baseline_passed`, `broke`
  (baseline passes lost), `fixed`, the `cost` and `latency` comparisons
  against the round's baseline (`median_ratio`), `verdict`, `stop_kind`,
  `rc_id`. Code appends a "Screened candidates: trade-offs (code-computed)"
  table, cheapest first under `cost` and fastest first under `speed`. Point
  at it. Quote its rows if you need them, and never compute a number of
  your own. Under `cost` and `speed` it is often the run's most useful
  result, also when nothing is promoted: name the cheapest or fastest
  candidates and what each one lost, and say that the owner can accept a
  trade-off that the rules refused. It is train-side screening evidence,
  not a val result.
- `exit_reason` vocabulary: `wrap_up` (normal, budget-driven) · `loop_exit`
  reasons from the planner (out of experiments / plateau) · `empty_plan` ·
  `timeout` (hard deadline — flag any unmerged pooled wins: they persist,
  merge-eligible next run; any stamped `— REJECTED AT VAL` only as
  ingredients of a different combination, never re-run as-is) · `tripwire`
  (infra broke — say which class) · `planner_failed`.
- `exit_detail`, when it is set, says why a `timeout` exit came: how much
  time was left, under the minimum to start a wave. Give it with the exit.

## Report template

```markdown
# Kiln Harness Tuner round report — <task name or id>, <date>

**Guidance:** <verbatim kickoff guidance>
**Exit:** <exit_reason, in words> · **Spend:** $<spend_total_usd> · **Waves:** <n>

> Numbers in this report are **loop-internal** — train-side attribution
> evidence or val-grade selection measurements, labeled per section. The
> human-facing truth is the test split, which the task owner measures —
> this loop never reads test.

## Setup
<only when `run_status.setup` says something happened or
`run_status.unloadable_evals` is not empty: issues seeded for new evals,
bindings repaired, `UNMEASURABLE:` flags, evals left out because Kiln can't
load them, and anything needing a human (unbound issues, how the baseline
was picked). Drop the section on a warm run that seeded nothing and left no
eval out.>

## Outcome
<2–5 sentences: what changed, headline val summary of the final promotion(s),
baseline lineage old → new. If nothing promoted, say what pooled instead —
and when the gate returned a decision, every candidate it passed over is
stamped `— REJECTED AT VAL`, so that rc returns only inside a different
combination, never re-run as-is (a `promotion_skipped` wave stamps nothing:
those wins are freely re-nominatable).>

## Promotions
<per promotion: winner rc, its experiment, the rationale/val summary,
regressed issues flagged (reopened), and the winner's latency and cost per
request against the baseline: the `latency.median_ratio` and
`cost.median_ratio` of its val rows in `get_val_summary` (read off the
stored eval runs, so information, not a verdict). "None" is a valid
section. Then one line per entry of `gate_blocks`: "blocked: <rc> (<experiment>) — <reasons>".
Never leave a block out: it is the one gate outcome that says a prompt
copies an eval.>

## Experiments, by wave
<per wave: one line per item — label, `change.summary` when the item carries a `change` (the build agent's structured what-changed block — cite its `changes` entries when the outcome turns on one of them), outcome numbers (from memory_overview),
or the failure class. Include rejected_summary highlights and tripwires.>

## Issues moved
<FIXED (with citations) · reopened at promotion · WONT_FIX (not reproduced /
deliberate) · still open, worth the next run.>

## Open unknowns & assumptions
<each OPEN: record raised or still standing: question, default assumed, what
it gates, re-ask condition.>

## Spend
<narrate: both lanes named with what they mean (Kiln $ = real money today,
and the only spend the budget counts; the agent lane = the agents' tokens,
`agent_tokens`, a rough count on the agent CLI's own account, never in the
budget — name it in tokens, not dollars),
notable line items (expensive evals, val passes), anything unusual. Code
appends the appendix after your markdown — the budget/elapsed/exit header
and the per-source × per-lane table — point at it, NEVER rebuild it or
arrive at a number of your own (Hard rules).>

## Speed
<only when `run_status.objective` is "speed" or `speed_results` is not
empty: say what the run did for speed — which gate promoted a faster
config, which candidate was clearly faster but lost quality, which was not
clearly faster. Code appends a "Speed (code-computed)" table with the paired
val numbers (median and p90, both arms, the ratios, the sign test): point at
it and never restate or re-derive a latency number. Speed sits beside
quality, never instead of it: name the quality outcome of every promotion
too. Val-grade, like every gate number. A screening survivor under speed
or cost is a bound, not a verdict on quality: quote its
record as "lost k of n baseline passes, no loss rate above X% at 95%" and
never as "quality held" or "quality-neutral"; its fixes of baseline
failures are noise-level and do not offset a loss. A val split of a few
hundred paired items or fewer cannot rule out a drop of a few points, so a
promotion is "no clear loss", never "no loss". When the run screened candidates, point at the
"Screened candidates: trade-offs (code-computed)" table and name the
cheapest or fastest rows with what they lost (see `screen_results`).>

## Suggested next steps
<from the experiments' next: lines, pooled unmerged wins (say which are
val-rejected: ingredients for a different combination, not re-runs), starved
issues, and, while eval building is off, the issues no eval measures (create
their evals in the Kiln app — see the "Issues with no eval (code-computed)"
section).>
```

Adapt sections to the run (drop empty ones, keep the number-provenance
banner) — the template is the default shape, not a form.

## Hard rules

- **Label every number val-grade** where it came from a promotion pass;
  train-side experiment numbers are attribution evidence, labeled as such.
  Never present either as the shipped/test truth.
- **Source every claim**: every dollar, minute, and causal sentence cites a
  `RunStatus` field (by name: `spend_by_source`, `phase_segments`, an
  item's `phase_secs`/`spend_usd`…) or quotes a finding/memory record.
  Prohibited patterns: **inferred composition** ("mostly judge cost",
  "driven by retries" — without the lane/source row or segment that shows
  it) and **unsourced quantifiers** ("most", "nearly all", "the bulk of").
  Composition/explanation claims about spend ("X is the biggest line
  BECAUSE it measures Y") are causal claims — they too need a cited
  structured field or quoted finding; if the books don't explain a number,
  report the number and say the books don't explain it.
  If the books don't carry it, say the books don't carry it.
- A code-computed spend appendix is appended after your markdown: a
  `budget <N> min · elapsed <M> min · exit <reason>` header over the
  per-source × per-lane table and the exclusions boilerplate. Code states the
  run's budget, elapsed and exit — **narrate** them (why the run ended where
  it did, what the clock bought) and **never compute a second version of a
  number code already states**: no elapsed of your own summed out of
  `phase_segments`, no re-derived budget, no rebuilt spend table. The
  template's headline fields (`**Exit:**`, `**Spend:**`) name the same facts
  on purpose — quoting the appendix's own figures is not a restatement;
  arriving at a different one is.
- **Separate this run's counts from cumulative ones — the divide is the
  record, not the medium.** Hole and recurrence counts from records that
  **outlive the run** (`lesson` / `issue` — "this eval holes on most waves")
  are **cumulative across runs**: label them as such and cite the record.
  Never present one as this run's, and never add it to a run-scoped count —
  the same hole is in both. This run's holes have **two** sources, and
  neither is a counter (`RunStatus` has **no hole counter**; the structured
  list lives on the `AnalysisBundle` each analyzer saw, which never reaches
  you): the analyzers' `findings` prose on each item's `outcome`, and **this
  run's own `experiment` records** — run-scoped even though they live in
  memory, because the analyzer wrote each one this run (its per-pair table
  carries that item's holes). Count by reading both, de-duplicate across them
  (they describe the same measurements), and say that's how you counted.
  Infra failures (items whose `outcome.status` is `failed_infra`, with their
  `infra_error`) and aborted waves (`tripwire`) are separate populations with
  their own counts — never folded into a hole count.
- Eval-job incidents: `eval_job_error_items` counts the dataset items that
  errored in eval jobs, on every attempt. `eval_job_error_classes` splits the
  same count by `"<split> <error_class>"` (for example `"val rate_limit": 68`).
  Use it to say why items errored. An errored item can pass on a retry, so
  this count is not a hole count. Never add it to the holes you count above.
  `val_fill_failures` counts the evals that went dark at a promotion gate.
- Return the markdown in the envelope's `data.markdown` — no file writes, no
  memory writes, no eval jobs, no `split` params. Reading is your only API
  activity.
- Report honestly: failed waves, tripwires, skipped promotions, holes, and
  abandoned items belong in the report — the run's value includes knowing
  what didn't work.

## Related

`memory` (what the exit-time reads mean) · `kiln-harness-tuner-gateway` (every Kiln
operation you have) · `kiln-harness-tuner-promote` (where the
promotion rationale you're quoting came from) · `kiln-harness-tuner-batch-selection`
(rejected_summary semantics).
