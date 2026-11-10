# Costs

What a run costs, and how to estimate one before you start it.

**This document quotes no measured figure.** The few unit prices below are
invented, to show the method. They are not a quote for your task.
It is method: the lanes to price, the formulas to price them with, the rule that
makes a price from somewhere else useless to you, and the smoke run that
replaces guessing. A number copied from another task would be worse than no
number, and the rest of this page explains why.

It is a summary of the [`experiment-costing`](../src/kiln_harness_tuner/agent_files/skills/experiment-costing/SKILL.md)
skill, which the loop's own planner is pointed at when it weighs what a wave
will cost. Read the skill when you are about to spend; read this to know what
you are about to be asked.

## The two lanes the loop reports

The loop meters its spend in two lanes and keeps them apart everywhere — on the
run screen and in the plain log lines, in the wave log's `spend` events, and
in the report's spend appendix.

| Lane | Wire name in `schemas.py` | What is in it |
|---|---|---|
| Claude Code (or Codex, or OpenCode) | `subscription_equivalent` | every agent dispatch the loop makes: planning, building, analysis, promotion, probes, the report. The agents run on the agent CLI's own account. The loop shows their tokens, a rough count, and not their cost. |
| Kiln | `provider` | every eval job: the model under test, the synthetic user on multi-turn tasks, and the judges |

They are separate invoices, on separate accounts. `--budget-usd` is a ceiling on
the **Kiln lane only**, for every agent CLI. The agent lane is information: the
run screen, the log lines, the report's spend appendix and the error screen
show the agents' tokens, and the budget does not count them. No figure the
tuner shows or computes includes a dollar cost for the agents: their `spend`
events in the wave log carry `tokens`, not `cost_usd`.

Which one dominates is a property of your task, not of the loop: a task whose
eval corpus is large and whose model is expensive is provider-heavy, and a task
with a small corpus is agent-heavy. Measure yours rather than assuming either.

## First principle: cost is a function, never a constant

Every price is conditional on the target model and provider, the tool surface,
the turn count, the sizes of tool results, the judge model, the prompt shape,
the item counts and the eval filters. **A number measured under one
configuration is invalid input for estimating another**, and quoting one without
its conditions is itself the error.

The sharpest form of it, and a failure the skill was written from: **never scale
across models by list-price ratio.** A model advertised at a fraction of
another's list price can cost *more* per conversation on the same corpus,
because its reasoning bills as output tokens and its cache economics are
different. Two errors can also cancel — a volume assumption that is too low
against a cache discount nobody modelled — and the result looks like a good
estimate and is luck.

## The six lanes to price before starting

1. **Drives — the model under test.**
   `$ ≈ Σ_calls (uncached_in × in_$/M + cached_in × cached_$/M + out × out_$/M)`
   The cached term usually dominates: an agentic loop re-sends most of its
   context on every call, so most input tokens are prefix re-reads billed at a
   fraction of list. Cost is linear in **LLM calls**, not in items — a scenario
   that elicits a long failure loop costs what that loop's calls cost.
2. **The synthetic user**, on multi-turn tasks. The same formula at the driver
   model's prices, usually on a different provider. Small next to drives, never
   free. Its input scales with the *agent's* verbosity, so a chatty agent makes
   its own driver expensive.
3. **Judging.**
   `$ ≈ Σ_items (trace_tokens + scaffold_tokens) × judge_in_$/M`
   Input, effectively: judge output is a rounding error. **Trace size, not
   rubric size, sets the price.** Drives are paid once per fingerprint; this
   lane is paid again on every re-score, which makes it the dominant *recurring*
   cost.
4. **Anything you call that belongs to someone else** — a third party's MCP
   server, a staging API, a live write path. Against a local mock this lane is
   $0: say `$0` rather than omitting it, so a reader can see it was considered.
5. **Quotas and keys.** Not dollars, but a hard stop. A key at its limit zeroes
   every lane routed through it mid-run, and a key can carry its own cap
   separate from the account balance, so a run can die with money still on the
   account.
6. **The replicate.** Run-to-run noise costs a full extra sweep, and without one
   no "we improved it" claim has a bound. Either budget it, or say up front that
   the result carries no error bar.

## Measure the variables; do not remember them

- **Model prices** from a live lookup, never from memory. Prices and provider
  routings drift, and a direct-provider lane is usually cheaper than an
  aggregator lane for the same model.
- **Drive sizes, call counts and cache behaviour** from the stored eval runs.
  Re-scoring stored traces with a code eval costs **$0**, which makes it the
  cheapest instrument available.
- **Judge and synthetic-user sizes** from the per-item eval-run records, which
  carry usage for both. No endpoint aggregates them; read them offline.
- **Recorded cost is not the invoice.** Where the runner's price table has no
  cached-token awareness and the lane runs at a high cache-hit rate, recorded
  cost overstates the invoice — by how much depends on the lane, so measure it
  rather than carrying a correction factor. Where the provider returns a
  cache-aware cost, the two agree closely. Quote both, and reconcile against the
  provider's dashboard after any sizable run.
- **Distributions, not means.** The expensive tail is what breaks a budget, and
  a mean hides it.

## Smoke first

The only price that transfers to your run is one measured on your task, your
model and your corpus. So make the first run the measurement:

- Bound it with both knobs, and leave headroom for a whole wave. Both have
  defaults, `--budget-usd 50` and `--timeout-mins 720` (12 hours), which a
  guided start pre-fills as its answers, and `--budget-usd none` runs with no
  cap. For the smoke run, answer or pass the bounds you want it to stay under.
  The two do not end a run the same way. `--budget-usd` is a pure
  wave-boundary check: the wave in flight when the ceiling is crossed runs to
  completion, and you pay for it.
  `--timeout-mins` is a deadline, and no wave *starts* past it, but at the
  deadline itself an item that is already *analyzing* is given
  `ANALYSIS_GRACE_SECS` to finish and **every other in-flight item is cancelled
  at once**. A cancelled item's build and drives are already paid for and store
  no verdict, which is the most expensive way for a run to end. A deadline set
  tight enough to land mid-wave buys nothing.
- Hold the parallelism down at first. `--max-concurrent-evals` and
  `--max-concurrent-agents` default to 4 each; the loop's own parallelism is
  what manufactures the provider rate limit that then stalls it.
- Keep the model preflight on — that is, do **not** pass
  `--skip-model-preflight`. It makes one short test call per model the run
  may use, before the baseline measurement. A model that has no deployment,
  or a window smaller than your prompt, is then flagged at start-up, and
  never fails a screening round's eval jobs one by one.
- Keep the reconnaissance battery on a first run — that is, do **not** pass
  `--no-recon`. It is a handful of short drives on your own baseline, and it is
  what stops the first planner assuming facts about your model. It is not free
  either: it drives real items on the model you are optimizing.

Then read the actuals. Two places, both code-computed rather than narrated by an
agent:

- the report's **spend appendix**, a per-source × per-lane table appended to
  every report, including the one written on a fatal exit;
- the `spend` events in the run folder's `wave_log.jsonl`
  (`~/.kiln_ai/harness_tuner/runs/<run_id>/` by default), each carrying the
  item it belongs to, the source that spent it and its lane.

Divide by what you got — items measured, experiments run, waves completed — and
that is your task's rate. It is the only one worth keeping, and it is worth
keeping with its conditions written beside it.

## What the speed and cost objectives add

Every arm, under every objective, goes through one measurement engine
(`kiln_harness_tuner.measure`). It judges the arm's targeted train split in chunks,
the parent's failures and a few passes first, and stops the arm when a
stop rule fires. A stopped arm pays only for the chunks it reached; an arm
that completes pays for every train item, driven and judged, which is by
far the largest part. Under `--objective speed` or `--objective cost`, the
engine also drives up to `SPEED_SAMPLE_ITEMS` (30) train items judge-free
for the arm and for the baseline, 15 per chunk and before that chunk is
judged: 60 drives, and 90 when the arm's parent is another run config. A
clearly slower (costlier) arm stops after its first 15 pairs and is never
judged. Under those two objectives, at every gate, on val, the baseline
and every candidate are timed on the sample. All timing drives are subject-model cost only. Under
`--objective quality`, an arm is not timed and pays none of these drives,
unless it changes the model, the provider or a sampling parameter: then
its lever is checked on the timing drives. Its latency and its cost per
request are read off its stored eval runs at no extra cost. The baseline
is also timed once per baseline on that sample. Stored val results stay in
the project, so a restarted run reuses the baseline's val side where its rows are on disk.

**A screening round** is the same engine on a capped queue. It costs, per
candidate, up to 30 timing drives and its drives and judge calls on the
round's items (3 per eval in round 1, doubling each round), plus up to 30
baseline timing drives for the round. For example, with invented prices: on
a task with 4 evals, $0.05 per drive and $0.02 per judge call, round 1
judges 12 items. One candidate then costs about 30 × $0.05 + 12 × ($0.05 +
$0.02) ≈ $2.34, and the baseline timing costs about 30 × $0.05 = $1.50.
Price your own round from the baseline's stored rows. A screening round is
booked as source `screening`; an arm's chunks are booked as source
`measurement`.

The report keeps what a screening round bought, also when nothing is
promoted. Its "Screened candidates: trade-offs" table has one row per
candidate: the baseline passes it lost, its cost ratio and its latency ratio
against its round's baseline, and the verdict. Under `--objective cost` the
cheapest candidate is first; under `--objective speed` the fastest is
first. A candidate that the rules refused can still be a trade-off that you
accept. The table only reports: it promotes nothing.

## The cap on each candidate

Every arm and screening candidate carries a spending cap
([`budget.py`](../src/kiln_harness_tuner/budget.py)). The planner sets `max_usd`
with a one-line `budget_reason`; without one, code sets min(1.5 × the
expected full measurement, $20, 10% of the remaining `--budget-usd`),
re-priced after each chunk at the candidate's own measured cost per item.
Any planner value is clamped to min($60, 25% of the remaining run budget),
so no single idea takes a large slice. The remaining run budget is net of
what the candidates already being measured hold: each holds its cap less
its spend until it stops. So arms and screening candidates measured at the
same time cannot each take 25% of the same dollars. Before each chunk the
engine also checks the run as a whole — the spend booked, what the other
candidates hold, and this chunk — against `--budget-usd`, and caps a
candidate whose chunk would pass it. The planner sees the prices it needs
in `measurement_prices`: the model's median cost per request on the task,
each judge's median cost per item, and a full measurement per eval. A
candidate is charged its own fresh judged rows and timing drives, plus an
equal share of the baseline's timing drives in its window (and of its
parent's, among that parent's candidates). A capped candidate is
inconclusive: its rows are kept, and a larger cap on the same run config
does not pay for them again.

## The baseline measurement at setup

Where the baseline's stored train rows do not cover the train split, setup
measures it — drives and judges on every train item of every active eval.
It is paid once: the item cache makes every later read of it free, and the
first arm would otherwise pay for the same rows of its parent.

## The detectability check and the noise check at setup

The **detectability check** is free. It reads the baseline's stored train
rows and the split sizes on disk, and starts no eval job and no agent. It
can stop the run before any wave spend, which is the point of it: a task
whose evals cannot show a significant val win pays for every wave and
promotes on no evidence.

The **noise check** (`--noise-check N`, off by default) is paid. It has two
passes, each over N train items per eval:

- the re-drive: each sampled item driven once on a clone of the baseline
  run config (a drive shared by every eval that samples that item, where
  the eval configs share traces), and judged once per eval;
- the re-judge: each eval with a V2 judge config judges its sample once
  more on a copy of that config. No drive: the copy judges the stored
  outputs.

So its cost is about (distinct sampled items × one drive) + (N × one
judgment × evals) + (N × one judgment × V2-judged evals). Before any spend
one line prints that estimate, priced from the baseline's own stored rows
(the median drive and the median judgment per eval). With a budget (any
`--budget-usd` but `none`), an estimate that does not fit the remaining
budget skips the check with the reason. What it spent is booked as source
`noise_check` in the Kiln lane, and it counts against the budget like every
other line.

## How spend is booked

Each stored record (an eval run: its task drive plus its judge call) is
booked once, by its id, the first time the run costs it; reusing it — the
baseline rows setup measured, read again as an arm's parent or by a
measure-baseline item — costs $0. Timing and probe drives are booked from
the runs they return.

**Reconciliation** (`kiln_harness_tuner.reconcile`). Before every wave
and at every exit — the loop's own, the quota breaker, a fatal error, an
interrupt — code prices the records this run created on disk and books what
the ledger lacks as `spend` source `reconciliation`, with one line:
`spend reconciled: booked $X, found $Y in records (+$Z in-flight work)`.
A record is this run's when it was created inside the run's window and is
either an eval run on an (eval, config, run config) this run started a job
on — at or after the first such job — or a task run with a loop-only tag
(timing drives, code probes, gateway drives) or scored by such an eval run.
It is priced once per id: the task run's `usage.cost` plus the eval run's
`eval_usage.cost` (the judge). So the work in flight at an interrupt, the
agents' gateway drives, and a drive whose judge call failed reach the
ledger, and `--budget-usd` reads the reconciled figure. Still not seen: an
eval job an agent started (`run_train_eval`), whose rows are on a config
the loop never started a job on, and work the server finishes after the
process has stopped.

## What is free

- Re-scoring stored traces with a **code** eval: no drives, no judge tokens.
- Creating run configs, evals and specs: $0 until something runs.
- The detectability check at setup: stored rows and split sizes only.
- Cloning a run config: $0 — but the clone does not inherit the trace-reuse key,
  so the first eval behind it is a full fresh sweep.

## What a run costs

This page gives no measured cost for a run. To find what a run costs, measure
a bounded one on your own task, and use this page as the checklist.

## Next

- [limitations.md](limitations.md) — what the loop does not do. Its Costs
  section is the short form of this page.
- [cli.md](cli.md#limits) — every flag, including the two budget knobs above.
