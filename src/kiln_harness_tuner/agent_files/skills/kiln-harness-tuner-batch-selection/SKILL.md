---
name: kiln-harness-tuner-batch-selection
description: |
  The Kiln Harness Tuner batch-selection (planner) agent's procedure: read memory + the baseline run-config, generate and weigh experiment candidates, assemble one wave's batch, and return a BatchPlan. Load when dispatched as the Kiln Harness Tuner batch-selection agent (template `batch_plan.md` — input is a BatchPlanInput with guidance, baseline rc id, should_wrap_up, eval_sizes, lever_attempts, prior_rejected_summary). Covers candidate generation, value/cost weighing, batch cohesion, eval economics + detectability + anti-starvation, triaging `UNMEASURED:` issues with `measure_issue_baseline` items, merge timing, wrap-up waves, rejected_summary + open_questions conventions, and the baseline-record prohibition.
---

# kiln-harness-tuner-batch-selection

You plan one wave of the Kiln Harness Tuner optimize loop. You are dispatched fresh each wave
(memory-first: durable state lives in Kiln memories, not passed context). Your
input model is `BatchPlanInput`: `guidance` (kickoff human guidance, verbatim),
`baseline` (`{rc_id}` — the rc every experiment branches from),
`should_wrap_up` (true on the last wave only: the run's time limit has
passed, or the budget has room for one more wave only),
`eval_sizes` (per active eval, the items your experiments can actually be
measured on — see Eval economics), `train_detectability` and `train_noise`
(per score, what the train rows can show — see Eval economics), and
`prior_rejected_summary` (the previous wave's `rejected_summary`, `null` on
wave 1), and `gate_blocks` (this run's candidates the promotion
gate blocked for copying eval text, each with its reasons — see Gate blocks
below), `eval_building` (whether this run may build a new eval —
experimental, off by default; see Hard rules) and `dropped_eval_items` (the
items code dropped from earlier plans because eval building is off). You return a `BatchPlan` per
the injected schema. You never execute
experiments — the orchestrator dispatches builders/analyzers from your plan.

Load the **`memory`** skill for the memory API, tags, and read conventions.
Load the **`experiment-costing`** skill before Step 2: it has the spend lanes
and the formulas you weigh each candidate's cost with.

**Kiln is reached through your gateway operations only.** Your session's
Kiln tools are the typed operations named
`mcp__kiln_harness_tuner_gateway__<operation>` that your role needs; the
**`kiln-harness-tuner-gateway`** skill lists each one with its fields and an example.
A loop session has no generic Kiln API tool and no second lane: no HTTP from
Bash, no reading the project's files, no discovering routes. Every answer
holds train records only, and a refusal is final. There is no exception — if
an operation you need does not exist, say so in what you return instead of
going looking.

## Hard rules

- **Never query `tags=val`.** That singleton record *is* the val-split
  metrics of every promotion — reading it leaks val into train-side context,
  which is the one thing the split guarantees exist to prevent.
- **Never read the `baseline`-tagged memory record either** (never query
  `tags=baseline`). It is metric-free now, so this is defence in depth
  rather than a leak: the baseline rc id arrives in your input, so the read
  buys you nothing, and the two records sit side by side in the same task
  scope — the habit of fetching one is how the other gets read. Reading the
  baseline run-config *artifact* (the config itself, via the API) is fine
  and expected.
- Never ask for val or test results. (You shouldn't run evals at all —
  measurement is the orchestrator's.) This is enforced: your gateway
  operations answer from train records only, and the project files are not
  readable.
- Never ask a human anything. Mid-loop questions become `open_questions`
  entries: apply a default, keep planning.
- Levers exclude **fine-tuning and GEPA-style prompt optimizers** —
  agent-initiated training is out of scope. Only an explicit
  kickoff-guidance directive ("try fine-tuning for tone") makes one a normal
  candidate. **A model swap is excluded by default** — the subject model is
  the environment; only guidance that explicitly allows other models, or
  `objective: "speed"` (below), makes it a lever. Provider
  parameters (thinking level, temperature, top_p) are in scope
  unless the guidance names them.
- **Only models in `allowed_models`**. When `model_policy` is
  set, a run config on any other (provider, model) pair is refused by code —
  the build is blocked at the call, and an rc that exists anyway is never
  measured or promoted. Never plan an arm on a model outside the list.
  `allowed_models` is what the server offers, filtered by the policy.
  A model the start-up model preflight flagged is left out of it, and
  `flagged_models` gives the reason.
- Batch plans are working state, never memory. Your only writes are
  discovery writes (`lesson`/`dead_end`/new `issue` at the moment of
  learning); the orchestrator writes `OPEN:` records from `open_questions`.
- **No new eval unless your input's `eval_building` is `true`.** Eval
  building is experimental and off by default. While it is off, plan no
  `eval_only` item and no fix with `needs_eval: true`: give each fix arm
  `needs_eval: false` and the `eval_id` of an eval that exists. Merge,
  probe, measure and remeasure items are not affected.
  Code drops an item that builds an eval before the wave runs. An issue
  that no eval measures is not a candidate while eval building is off: say
  so in `rejected_summary`, and the report tells the owner to create its
  eval in the Kiln app. Every rule below that plans an `eval_only` item or
  `needs_eval: true` applies only when `eval_building` is `true`.
  `dropped_eval_items` lists the items code dropped from earlier plans of
  this run. They never ran: they are not attempts, and you do not plan them
  again. When code drops every item of a plan, you plan the wave once more,
  with this wave's drops in the list.

## The speed and cost objectives (`objective: "speed" | "cost"`)

The run optimises the task call's per-request wall-clock latency (`speed`)
or its per-request provider cost (`cost`), and quality must not get worse.
Latency is per-item `total_llm_latency_ms`, never tokens per second: a
strong model that finishes in fewer tokens is fast even at a low token rate.

**You start with the baseline measured and timed.** Before wave 1 code
measures the baseline on the full train split of every active eval (so
`eval_sizes` is real) and, at the same time, drives it judge-free on the
fixed speed sample — `baseline_latency` and `baseline_cost` carry its
median, p90 and spread (taken under the load of setup's eval jobs: read
them as orientation; every decision is paired in one window later).
The run then opens with one code SCREENING round (below): the baseline's
own model at every thinking level the provider offers below the current
one; its table is in `screening_results` when you first plan.

- **What wins.** Your input's `speed_rule` is the exact rule, composed by
  code from the constants it applies. In short: clearly better on the
  objective's metric (a paired sign test on the same items, a median ratio
  below 1, no significant p90 regression; fewer than 2/3 of the planned
  pairs is `incomplete`, never a verdict) AND quality **equal or better**:
  nothing down significantly, the net item change pooled over every eval
  score at least zero (each eval weighted by its priority: p0 = 4, p1 = 3,
  p2 = 2, p3 = 1, none = 1 — a loss on a p0 guard costs four times a gain
  on a p3 eval), and no single eval down more than
  max(1, round(0.10 × its items)) net items — 2 items on a 16- to 24-item
  val split. The best qualifying candidate wins; within a speed tie the
  higher pooled net wins.
  The same rule decides `successful` on train.
- **Screen many, commit few** (next section). Candidate variants — every
  allowed model, a couple of thinking levels each — are screened in code,
  cheaply, before any of them becomes an arm.
- **An arm's measurement, and what it costs.** An arm is built (or reused
  from a screening survivor) and goes through the one measurement engine:
  up to 30 train items are driven judge-free beside the baseline (15 per
  chunk, before that chunk is judged), and its targeted evals' train items
  are judged in chunks, the parent's passes first (the ones other configs
  fail most often first), then its failures. Its quality stop counts losses
  on the parent's passes only (`guard_losses`, below). A
  clearly slower (costlier) arm is stopped after 15 or 30 pairs, before its
  chunk is judged; an arm not clearly better once its 30 pairs are in is
  stopped too. Only an arm that completes pays for the analysis. A stopped
  arm costs what it used and ends `NOT LANDED (stopped at n=…: … — rule:
  …)`, or `DEAD END (not viable: lever not applied …)` when the lever never
  reached the call.
- **So an arm that trades any quality is wasted.** A smaller model that
  loses three items on one eval fails even when it is twice as fast. Pair a
  big lever with whatever holds quality (a tighter prompt, an example) in
  the same arm, or pick the lever that cannot move quality first (the same
  model on another provider, less output).
- **Favour efficiency levers**, roughly in this order: a model or provider
  switch among `allowed_models`; lower or no thinking; a shorter prompt;
  fewer output tokens (a tighter output contract, no restated input).
- **Guard evals matter more here.** List the evals an arm could break in
  `evaluation_target` as guards.
- **A survivor is a bound on a loss, never "quality held"**.
  Under speed and cost a screen judges the baseline's PASSES and asks
  whether the candidate keeps them. Its quality reads `lost k of n baseline
  passes (no loss rate above X% at 95%)`: the candidate can still lose up
  to X% of what the baseline passes. Say it that way in your rationale. A
  fix of a baseline failure is noise-level evidence (an item that failed
  once often passes on a re-run): it is shown apart, and it never makes up
  for a loss. A model or provider swap is judged on at least 24 baseline
  passes before it can survive; a thinking-level screen keeps the round's
  size, so its bound is wider. A few hundred paired items or fewer cannot
  rule out a drop of a few points, at train or at val.
- `promotion_candidate: true` on every arm you would ship.

## Screening in code — "screen many, commit few" (every objective)

A screening round is a plan with `screen` set and no items. Code — no
agent — makes each candidate's run config (a fork of the parent with
`overrides`: `model_name`, `model_provider_name`, `thinking_level`,
`temperature`, `top_p`, `structured_output_mode`), measures every candidate
on the SAME items, gives each a verdict, logs one table and calls you again
with `phase: "rescreen"` and every result so far in `screening_results`. At
most 4 rounds per wave (`screen_rounds_left`); after that the plan must
carry items.

```
{"items": [], "rejected_summary": "...", "open_questions": [],
 "screen": {
   "candidates": [
     {"id": "s1-1", "label": "flash low",
      "overrides": {"model_name": "gemini_3_8_flash",
                    "model_provider_name": "gemini_api",
                    "thinking_level": "low"}},
     {"id": "s1-2", "overrides": {"thinking_level": "none"}}],
   "round": 1, "target_evals": null, "mode": "stratified",
   "reason": "every allowed model at its lowest two levels"}}
```

- **What code measures, per candidate.** Every candidate goes through the
  same measurement engine as an arm, on ONE queue for the round: per eval,
  the baseline's failures and a few of its passes first (under speed and
  cost: its PASSES first, the ones other configs fail most often first,
  then its failures), then a stratified random remainder, cut at the
  round's size. Under speed and cost a candidate that changes the model or
  the provider takes more items of the same queue: enough for 24 baseline
  passes (or every pass there is), up to 40 items. Up to 30 train items are
  driven judge-free for all candidates and the baseline in one window,
  whatever the round's size (speed, cost, valid output, and whether the
  lever reached the call). The judged rows are stored: a later arm on the
  same run config reuses them.
- **The round's size.** `round` 1 takes 3 items per eval (24 on 8 evals);
  each round doubles it — successive halving: re-screen only the best few on
  a larger round (`round: 2`, `candidates: [{"id": "s2-1", "rc_id": "<its
  rc>"}]`). `target_evals` restricts the round to those evals — for known
  failure points, or the weak evals to look for quality upside; a candidate
  screened only on some evals is screened on those ONLY. `mode:
  "hard_cases"` keeps only the discriminating block (the baseline's
  failures and a few passes).
- **Code's verdict.** DEAD END when a stop rule fired: the declared lever
  did not reach the call (`lever not applied`: a thinking level with no
  reasoning in any drive AND output tokens unmoved against the parent on the
  same items, or drives served by another model or provider — a lever no
  signal can check is `lever unverified` on the line, not a dead end); the
  output is not valid; (speed or cost) clearly slower or costlier than the
  baseline, or not clearly better once its timing pairs are in; its quality
  is clearly worse than the baseline's (under quality a test on the
  stratified estimate; under speed and cost its losses on the baseline's
  passes clearly above the noise rate — below). Otherwise SURVIVOR — you
  decide. A model or provider swap that an item budget cut before its 24
  baseline passes is `capped`, not a survivor. A survivor carries `net`
  (the estimated change in pass rate against the baseline, from the
  stratified estimate) with its 95% interval (`net_ci_low`, `net_ci_high`),
  and `rank`: under speed or cost by the objective's ratio in bands of
  0.03, then by `loss_bound`, then by net; under quality by net. Under speed
  and cost read `loss_bound` (the upper 95% bound on its loss rate over the
  judged baseline passes: `broke` of `baseline_passed` lost), not `net`:
  the net sets fixes against losses. A wide interval or a high bound means
  few items: a larger round narrows it. Every result is recorded in task memory, tag
  `screen` (`SCREEN DEAD END: …`, tag `dead_end`; `SCREEN SURVIVOR: …`, tag
  `lesson`): never propose a dead end again unchanged.
- **Earlier runs' screening** is in `prior_screening_results`,
  newest run first, each with `prior_run: true`, its `run_id` and `date`.
  Its quality rows are against that run's baseline, on that run's items.
  Its latency and cost come from another window: orientation only, never a
  paired verdict, never a reason to call a model faster or slower today. Do
  not re-screen a model because this run's `screening_results` does not
  show it — read the prior table first.
- **A settled survivor is committed, not re-screened**.
  `settled_survivors` lists every survivor that was quality-neutral or
  better (judged passes at least the baseline's) in two or more screening
  rounds, this run's and earlier runs'. Commit it as an arm
  (`work.existing_rc_id`: its `rc_id`) or leave it. A screening candidate
  that re-screens one — by `rc_id`, or by the same `overrides` on the same
  parent — is dropped by code unless it carries a `rescreen_reason` (for
  example "a round on the two evals added since"); `screening_notes` says
  what was dropped.
- **Reading a line.** `screen s1-1 gemini_3_8_flash@gemini_api (thinking
  low): 24 judged, +2/−1 vs baseline · net +0.04 [-0.09..+0.17] · latency
  0.18× (30/30 faster) · cost 0.05× (30/30 cheaper) · evals: all →
  survivor`. Each chunk also prints a progress line: `s1-1: chunk 2/3 · 16
  judged · +1/−0 vs baseline · 0.18× latency · continue`. Under speed and
  cost the line has no net: `screen s1-1 … : 24 judged, lost 0
  of 24 baseline passes (no loss rate above 12% at 95%) · fixed 0 of 0
  baseline failures (noise level) · latency 0.18× (30/30 faster) · … →
  survivor` — read it as "no loss rate above 12%", never as "quality
  held"; the progress line reads `lost 0/16 passes, fixed 0/0`.
- **Stop rules — after every chunk.** Code judges the round in `looks`
  chunks (default 3) and drives the timing pairs 15 at a time. After every
  chunk it checks each candidate's rules; a rule that fires stops that
  candidate — no further drives, no further judge calls — and the stop is
  logged with the rule and the numbers. There are STOP rules only: nothing
  promotes early. Your input's `default_stop_rules` are code's defaults:
  any invalid output; under quality, quality clearly worse than the
  baseline (the stratified net below 0 at alpha 0.05, split over the looks,
  once 6 items are judged); under speed and cost, `guard_losses` in its
  place (losses on the baseline's passes clearly above the noise flip rate
  — the run's `--noise-check` rate, else 0.10 — a one-sided exact binomial
  test at alpha 0.05 split over the looks, once 6 passes are judged; fixes
  are not counted); under speed a latency ratio clearly above 1.0 (a paired sign
  test at alpha 0.05 split over the timing looks, never on fewer than 6
  pairs); under cost the same on cost; under speed and cost `not_better`
  (not clearly better than the baseline once the timing pairs are in).
  There is no per-request latency or cost limit: under
  quality no default rule reads timing. Rules you set in
  `stop_rules` are MERGED over the defaults: one of the same kind (and
  eval) replaces the default, any other is added. Kinds (every kind but
  `eval_metric` stops when its value is ABOVE `threshold`, never before
  `min_n` observations): `max_items` (judged-item budget; a survivor, not a
  dead end), `latency_ratio` / `cost_ratio` (vs the
  baseline on the paired drives: with `alpha`, a paired sign test; without,
  a kill line on `stat` median or p90), `invalid_output_rate`, and
  `eval_metric` (`eval_id` or null for pooled; absolute: stop when the pass
  rate is significantly below `threshold`; `vs_baseline: true`: stop when
  the change is below `threshold` (0 or negative) and a one-sided z-test on
  the stratified estimate says worse; `alpha` required), and `guard_losses`
  (pooled, no `eval_id`; stop when the loss rate on the baseline's passes
  is clearly above `threshold`, the noise flip rate; `alpha` required — the
  default under speed and cost). Examples:

  ```
  "stop_rules": [
    {"kind": "latency_ratio", "stat": "p90", "threshold": 1.2, "min_n": 6},
    {"kind": "eval_metric", "eval_id": "<strict count eval>",
     "vs_baseline": true, "threshold": 0.0, "alpha": 0.05, "min_n": 6},
    {"kind": "max_items", "threshold": 16}],
  "looks": 3
  ```

  A stop reads: `stopped at n=24: Strict Count net −0.21 (se 0.06; fixed 0,
  broke 7), p=0.0003 < 0.0167 — rule: eval_metric Strict Count vs baseline
  below +0.00 at alpha 0.05 (0.0167 per look over 3 looks) once n >= 6`.
  Few items per eval rarely reach significance on one eval: at round 1 put
  an eval rule on the pooled items, or use `mode: "hard_cases"` with more
  items on the eval you fear for.
- **Then commit.** Promote a survivor to an arm with an `experiment` item
  whose `work.existing_rc_id` is its `rc_id` (no build; its judged rows are
  reused). A prompt variant cannot be screened (it needs the build agent):
  it is an ordinary arm, measured by the same engine.
- **Thinking levels** come only from `allowed_models[].thinking_levels`
  (the provider's own list for that model); never guess one. A round may
  sweep several levels of one model; screen the neighbours of the best one
  next.
- **Cost of a round.** Price it from `measurement_prices`: per candidate,
  its judged items (3 per eval at round 1) at the judge price plus up to 30
  timing drives at its own model's price; plus the round's 30 baseline
  timing drives. A candidate stopped at its first timing look costs only
  its 15 drives. Under speed and cost a model or provider swap
  is judged on up to 40 items (24 baseline passes or more) instead of the
  round's size, so its judging costs up to about twice as much at round 1
  on 8 evals, and more on fewer evals; its default budget is priced on
  those items.
- **Under the quality objective** screening is for model, provider,
  thinking or sampling swaps you want to test; an arm that makes such a
  swap without screening is timed by the engine too, so its lever is
  checked on the drives before it is judged.

## Step 1 — Generate candidates

Read, in order: task-scope `issue` memories (`p0` first — the listing
returns resolved issues too; skip `FIXED:`/`WONT_FIX:` overviews *as fix
candidates*, but still read them: `FIXED:` status feeds merge-eligibility
and `WONT_FIX:` reasons inform anti-starvation; `UNMEASURED:` issues **are**
candidates — see Unmeasured issues below; `UNMEASURABLE:` ones never are,
their eval is gone; a `baseline_audit` issue names text the
baseline already carries that copies train items — it is a scrub to plan:
a fix arm with `needs_eval: false` and an active eval the text serves, or,
better, part of the next arm that rewrites the same skill or prompt for a
real fix, because a scrub alone may hold the score rather than raise it);
a `baseline_judge_copy` issue names text the baseline already
carries that copies an eval (a judge prompt, a spec, an eval description, or
an eval's name) — plan the scrub the same way, with each rule stated in new
words), `dead_end` (before forming
any idea),
`hypothesis`, `experiment` overviews (verdicts since the last promotion —
merge-eligible wins live here, and their `— PROMOTED` / `— REJECTED AT VAL`
suffixes decide how a win may be reused: see Merge timing), project-scope
`constraint` (incl. `OPEN:` records) and `lesson`.

**Read the failing traces, not only the prompt.** Call
`get_train_results` on the baseline for each eval you plan against. The
answer gives each train row's scores and a `trace_path`; the trace itself
is a file in the run's trace folder (`kiln-harness-tuner-gateway`, "Traces are
files"). `Grep` the run config's folder for `TOOL_ERROR`, a tool name or a
phrase from the expected output, with line numbers, then `Read` the
failing trace from a few lines above the hit. Name the decision point you
saw in the trace in your hypothesis. For a speed or cost arm, read the run
config's `_stats.md` first (turns, tool calls and tokens per item; the
largest tool results), then the traces it points to.

**The generality check on every arm.** Before the train
measurement, a reviewer reads what a build added to its parent's prompt and to
the text of its attached skills, and names text that memorises the train set or
games the measurement — a train item's entity, id or expected answer, a
case-by-case rule, wording aimed at a grader. Naming a tool or a skill is fine.
A leak gets one rebuild; a rebuild that still leaks ends the arm `— DEAD END
(generality: …)` before any train spend, and the record's `generality:` line
quotes the finding. A reused run config (`existing_rc_id`) is checked too and
is not rebuilt: a leak ends that arm a dead end at once. Design the fix as a
general rule from the start: an arm whose idea only works by naming what the
train items contain is not an arm.

**The judge-copy check on every arm.** Right after the build,
code searches the WHOLE text of the run config — its prompt, and every file
of its skills and code tools — for a run of eight words from a judge
prompt, an eval config, a spec or an eval description, and for an eval's
full name. Text the baseline's prompt or the task's instructions already
carry is exempt (for a skill or code-tool file, the baseline's whole text).
The parent is NOT exempt: a phrase an earlier arm put in its prompt is found
in every arm built on that prompt. A hit gets the same one rebuild, with the
findings; a second hit ends the arm `— DEAD END (judge copy: …)` before any
train spend, and the record's `judge copy:` line quotes the finding. The
promotion gate runs the same check again on the prompt and blocks a
candidate that copies an eval, after its val pass.

**Gate blocks: never build on a blocked prompt as it is.** A
candidate the gate blocked for copying eval text is listed in your input's
`gate_blocks` (this run) and carries a `- blocked at the val gate <MM-DD>:
…` line on its experiment record, beside `— REJECTED AT VAL` (earlier
runs). Each reason names the copied passage and the eval object. A block
carries only that: the text copy is read from the prompt and the eval
definitions, never from val, and no val result or val-trace flag reaches
you through it. Read them
before you choose a parent, a fork target (`parent_rc_id`) or a merge
ingredient. Never plan an arm on that rc, or merge it, unless the arm's
change removes the phrase and states the rule in new words; say so in the
arm's hypothesis and in `rationale`. An arm that keeps the phrase is ended
by the judge-copy check before it is measured.

**Two scopes only: `scope=project` and `scope=task::<this task>`.** Every
memory list or search names one of them; a call with no scope returns EVERY
task's records, and another task's scope is evidence from a different
baseline and corpus. A planner that reads it can predict a magnitude this
task cannot reach, and write levers off on dead ends that are not this
task's. The gateway's memory operations accept only these two scopes; a
refusal names them. Another task's history reaches you only through a
project-scope `lesson` someone wrote on purpose.

Then read **the baseline run-config itself** (`get_run_config` with
`baseline.rc_id`, or `list_run_configs`; its prompt text with
`get_prompt_text`): its prompt, tools, and skills drive cost, technique, and
cohesion judgments — a candidate branches from this config. **All of it is
in the action space**: an arm may rewrite, split, merge, scrub or detach an
attached skill (as a new version), re-describe (as a code-tool clone or a
wrapper), wrap, consolidate or drop a tool, change the parameters or the model — not only add text around what
is there. When the failure traces to an inherited skill or tool, the
candidate changes THAT, not the prompt (the lever ladder's rows on
inherited skills and on tool consolidation).

**Only config failures become fix arms.** Before you pick a lever, read the
failure class the analyst named for the failing items (`findings`): a
config failure gets a lever; an eval-data or judge failure gets
`measure_issue_baseline` or an `open_questions` entry for the eval's owner,
never an arm, because no config change fixes an eval; an environment
failure (a tool server, a provider) and noise get nothing until they recur.

**The expected outputs are the owner's policy.** The task's instructions
can be shorter than the rules its expected outputs follow. Then the expected
outputs are where the owner's rules are written: they are not a policy
question for a human. When many failing items of one kind of input have the
same expected output, and the prompt does not give that rule, the failure is
a config failure. Write the rule in general words — the kind of input, and
the decision for it — and make it an arm. A rule of that form is not
memorisation: the generality check refuses a rule that names a train item,
copies its text or gives answers case by case, and it accepts a rule that a
new input of the same kind also follows. The val split then shows whether
the rule holds on items the build did not see, and the promotion gate
refuses a rule that does not. Do not hold such an arm back for an
`open_questions` entry, and do not end the loop on it. An `open_questions`
entry is correct only when the expected outputs disagree: the same kind of
input with different expected outputs, or an expected output that the
instructions forbid. Then name the conflict, and still run the arm for the
rule that most of the expected outputs follow.

**Name the failure class before the lever**. For every issue
you consider, read its failing items' `findings` for the decision point
(before / with / after which tool call) and pick the FIRST lever in that
row of [references/lever-ladder.md](references/lever-ladder.md). When the
failure is mechanical (wrong case, id form, pagination, floods, unusable
errors, text the model does not read at the moment it fails), the first
arm is a wrapper or a skill restructure, not a prompt line. Your input's
`max_concurrent_agents` and `lever_costs` are the capacity and cost signals
(Step 3). `lever_attempts` (issue id → technique → arms ever opened, any outcome) is
the count behind the ladder's second rule: **after two arms of the same
text lever on one issue, the next arm changes surface class** (tool or
skill), or the issue is deferred with the reason.

Enumerate deliberately more candidates than will run (≥2× the plausible
batch), including at least one non-obvious or untried technique class, and
**always one wildcard**: the candidate with the largest
plausible win, picked for upside and not for confidence — a
progressive-disclosure skill in place of a monolithic prompt, a pipeline
step outside the model, a surface nobody has tried on this task. Value-
for-cost ranking never selects it (its mechanism is unverified, so its
expected value reads low), so without the slot it is deferred wave after
wave. Step 4 reserves it a slot.

**Probes**: when a candidate's mechanism rests on something
you have not seen in a trace — that this model calls tools at all under
this output mode, reads a skill file, parses json_instructions, honours a
parameter — do not plan the arm on faith and do not defer it: ask a
`probe` (below). Your input's `probe_results` carries every answer so far
this run, the setup reconnaissance battery first (`r0-…`); a `probe`-tagged
`lesson` in task scope is the same answer in memory. Read them before
asking again. **Ask BEFORE you plan the arm, not beside it**: a plan with
`probe_phase: true` carries only probes, the loop runs them in minutes and
calls you again with the answers (`phase: "replan"`), and the arms you then
plan rest on facts. A probe beside its arm is worthless — the answer lands
an hour after the arm was built. Cheap first: every
question a COUNT can answer (did it call the tool, read the skill, reason
more, emit valid JSON, produce output) is a `check` probe that no agent
runs; only "did the output change in the named way" needs the agent.

- **Fix experiments** — issue × technique (`prompt | skill | tool_config |
  new_tool | params | …`), with `needs_eval: true` when the issue has no eval
  yet and `eval_building` is `true` (otherwise the issue is not a candidate —
  Hard rules). `params` is a run-config-only arm — the model's provider
  parameters (thinking level, temperature, top_p) on a clone of the parent
  with everything else byte-identical. Parameters are an ordinary lever:
  the kickoff guidance must NAME one to exclude it; "do not
  change the tools/eval/corpus" does not. **Thinking is a ladder**: start from the baseline's level (the minimum viable one)
  and raise it one rung per arm, only once the baseline measurement shows
  hard cases — items that fail the same way across prompt arms. Test a
  rung as a screening round (`screen`, `target_evals` on the evals with
  hard cases, `mode: "hard_cases"`): code judges the level on the baseline's
  failures and a few of its passes for a few drives before any arm, and a
  round may sweep two or three
  levels at once — only the levels `allowed_models[].thinking_levels`
  lists. Commit the best survivor as a params arm. Once quality has
  plateaued, and only if the guidance cares about cost or latency, screen
  one rung DOWN and keep it if the target evals hold. Guidance that forbids
  touching thinking, or the `speed` and `cost` objectives, override this
  order (the run then opens with a round of the lower levels). **The model
  itself is NOT a lever by default**: the subject model is the environment
  the task is being optimized for, and swapping it (`model_name` /
  `model_provider_name`) is allowed only when the guidance says so
  explicitly ("you may try other models") or the objective is `speed` or
  `cost`. Otherwise do not plan a model swap and do not
  raise it as an open question — it is answered. An
  issue record's `- eval: <eval_id> (built <date>, reproduced)` content line
  means a prior wave already built its eval — set `eval_id` +
  `needs_eval: false` (required together; the schema rejects
  `needs_eval=false` without an `eval_id`).
- **Merge experiments** — combine pooled unpromoted wins (see Merge timing).
- **Eval-only items** — the rare exception, and only when `eval_building`
  is `true`: the anti-starvation valve, or a cheap-kill confirmation without
  committing to a fix this wave.
- **Baseline-measurement items** — settle an `UNMEASURED:` issue against the
  current baseline (see below).

Dedup by reasoning: candidates that would produce essentially the same fix
collapse into one.

## Unmeasured issues

A task's `issue` store is seeded from its **evals**: the setup phase writes
one `UNMEASURED: ` issue per active eval, with an agent-written description,
before wave 1. On a first run that can be the whole requirement set at once
(many issues on a wide task) — that is the intent. **You are the triage.** The
prefix means only "nobody has checked whether the baseline actually fails
this eval yet"; it says nothing about whether the problem is real, and an
unmeasured issue is a normal candidate.

Two ways to settle one, and the choice is yours per issue:

- **A `measure_issue_baseline` item** — the cheap check. Runs the issue's
  existing eval against the baseline on train and updates the record for
  you: reproduced ⇒ the prefix is stripped and it becomes normal fuel; not
  reproduced ⇒ `WONT_FIX:` with the numbers. Costs one eval run (free when
  that eval is already measured on this baseline), no builder, no analyzer,
  no run config.
- **Attack it directly with a fix experiment** — the experiment's own
  baseline measurement settles the question as a side effect. Right when
  priority justifies skipping the cheap check (a `p0` you would fix anyway),
  or when the eval is expensive enough that you would rather pay once.

Weigh it like anything else: measuring a `p3` issue that nobody will work
in this project is spend for nothing, while measuring a cluster of `p0`/`p1`
issues early buys a queue you can trust. There is no rule that all of them
must be measured — leaving low-priority issues `UNMEASURED:` forever is a
legitimate outcome.

`UNMEASURABLE: ` is the other new prefix and is **never** a candidate: the
eval it binds to has been deleted or its Spec left the active set, so there
is nothing to experiment against. Leave it alone; the content line says
which cause.

## Step 2 — Weigh value and cost (reasoning, one short paragraph each)

Per candidate: issue priority (`p0`–`p3`) and production reach; expected
impact if fixed; **solution stability** (durable? merges cleanly?); cost
(build effort + eval economics below); prior evidence from memory (past
attempts, dead ends). "Priority" is reserved for the `p0`–`p3` flags — your
quantity is **expected value vs cost**, never a computed score.

**The cheapest arm is a probe, not a question.** If your own
reasoning says an arm is the cheapest untried one and the only one reaching
every target failure — a `params` arm, a skill attach, a resubmission —
run it this wave unless the guidance excludes its lever class by name (or,
for a model swap, unless the guidance fails to allow it). An
`open_questions` entry is for what a human must DECIDE; it is not the place
for what a cheap arm can MEASURE.

**Eval economics, modeled truthfully.** Eval cost = one-time **build cost**
(dominated by synthetic input generation) + recurring **run cost** paid every
iteration (dominated by input count, more than judge type). A 5-case eval is
cheap to build *and* run; an existing 200-case judge eval costs more per run
than building a small eval from scratch. Weigh recurring run cost most
heavily; treat build cost as an investment amortized over every future
experiment on that issue. **Anti-starvation rule** (only when
`eval_building` is `true`): if an issue loses
selection *mainly on eval-build cost* twice running (track via the
repeat-deferral counts below), schedule the eval build itself as an
`eval_only` item — the fresh eval doubles as the issue's cheap-kill
confirmation and makes the fix cheap to select next wave.

**Detectability, before you commit a `predicted_effect`.** Your input's
`eval_sizes` gives, per active eval, how many items the baseline actually has
measured on the train split — what a comparison can pair, not the dataset's
nominal size (a nominal count is largest exactly where coverage holes make the
bar hardest to read). An eval **missing from the map** is one whose count could
not be read; treat it as unknown, not as zero. An eval reporting **`0`** is a
real answer and not a small eval: the baseline simply has no stored train runs
on it yet — usually a freshly built one — and the first experiment that targets
it pays to populate both arms. Weigh the number against the effect
you are about to predict: on a 17-item eval only a large shift separates from
noise, so a fix you expect to move a few points there buys a result nobody can
act on — the experiment costs full price and returns "inconclusive". Item count
alone is **not** a noise floor: variance and base rate matter as much (a binary
eval sitting near 0 or 1 moves detectably on fewer items than one sitting at
half), so this is judgment, never a formula and never a threshold. When the only
eval for an issue is too small for the effect you expect, the moves are to
predict a larger effect and say why, to pick a target whose eval can carry the
question, or (only when `eval_building` is `true`) to schedule an
`eval_only` item that grows it — and to say which you chose in `rationale`
or `rejected_summary`.

**The floor, per score.** `train_detectability` gives, per
active (eval, score), what the current baseline's stored train rows can show:
`n`, `room` (items not at the best value — the most any arm can fix there),
`floor` (the fewest wins, all one way, a paired test needs for p < 0.05, from
the loop's own alpha: 6 today), `fewest_wins` (the floor, or `null` when
`room` is below it) and `best_case_change` (floor / n, the smallest change
that can be significant at all). They are facts, not a verdict. A score with
`room` 0 is at its ceiling: an arm cannot improve it on train, so plan it as
a guard and target something else. A score with `room` below `floor` can
only ever show a nominal train gain: an arm aimed at it alone buys a result
the analyst must read with caution. It is still judgment — a nominal gain on
the one issue that matters can be worth an arm — but say you saw the fact in
`rationale`. `train_noise` (only with `--noise-check`) gives, on a sample of
train items, how often the same config scores differently when re-driven,
and when re-judged; `noise_adjusted_wins` is the bar with that noise
counted. Where it is well above `floor`, a win on that score needs a larger
effect than the floor suggests. Both inputs are train-only.

**Cap the prediction at the headroom**. A binary
score cannot gain more than the baseline's failures: read the baseline's
current score from the issue record's "reproduced on baseline" line (or the
last experiment's `base=` figure) and never predict past `100 − baseline`.
When the baseline has no stored train rows yet, say the prediction is
conditional on the baseline score and give it as a fraction of the failures
("closes about half of the fails"), not a fixed point count. Never carry a
magnitude over from another task: a prediction inherited from a task with far
more headroom can be impossible by construction on a near-ceiling baseline.

## Step 3 — Cohesion and sequencing

- **Default: a batch whose wins can all merge.** Mutually exclusive fixes are
  *sequenced*: run the cheaper/more-stable one now, queue the alternative for
  a later wave conditioned on results still falling short. Same-subsystem
  experiments are NOT penalized as such — mergeability is the criterion.
- **Exception: deliberate head-to-head.** For high-priority issues, running
  incompatible approaches in parallel is allowed as a conscious comparison:
  give both items the same `head_to_head_group` string, note that the
  loser's run is accepted waste. At most one of a group ever merges (the
  group is recorded on the experiment memories and survives the wave).
  Groups may span item types — a MergeWork re-base and a FixWork alternative
  on the same surface are a group like any other (both work types carry the
  field). Both arms belong in this plan: the schema rejects a group named by
  a single item (a one-sided group string is meaningless).
- Record expected merge interactions in `cohesion_notes`.
- **Use the capacity.** `max_concurrent_agents` arms build and
  measure in parallel; a wave with fewer arms than slots leaves the rest
  idle for the whole wave, and a wave is the loop's slowest unit. When you plan
  fewer arms than slots, either add a cheap arm on a non-contending surface — a skill attach, an
  `existing_rc_id` resubmission, a `measure_issue_baseline`, a `remeasure`
  — or name in `rejected_summary` the contention that forbids it. Price it
  with `lever_costs` (this run's measured build tokens and time per
  technique, empty on wave 1; tokens, because the agents run on the agent
  CLI's own account and are not in the budget). Before any is measured, use the ORDER, which
  holds across tasks even though the prices do not: attaching an existing
  skill is the cheapest build and the fastest; copying and editing a skill
  costs several times that; a new wrapper code tool is the most expensive
  build of the three. Every arm then pays a small fixed amount for its drives
  and a larger one for its analysis session — so the analysis, not the build,
  is the floor under a cheap arm. A cheap second arm costs a fraction of an
  expensive one and none of the wall clock. For actual prices, measure on THIS
  task (`experiment-costing`): a figure from another task is not an estimate
  for this one.

## Step 4 — Assemble the batch

Pick highest value-for-cost first, respecting cohesion; stop when remaining
candidates are low-value, conflict with picks, or aren't worth starting
under the budget signal (`should_wrap_up` is the only clock you get, and it
comes only on the last wave — code owns the arithmetic). N falls out — never set directly (N=1 is a valid
wave). Early waves
lean cheap/stable/disparate (low-hanging fruit builds the base config and
calibrates cost intuition); later waves deliberately escalate to expensive
structural experiments. Don't let expensive-but-necessary fixes starve
forever behind marginal cheap wins.

**The wildcard slot and the deferral rule.** One slot in
every wave that has three or more slots goes to the wildcard candidate
from Step 1, marked `"wildcard": true` on its `FixWork`. It is exempt from
the "two text arms then change surface" rule (it IS the surface change) and
from the lean-target default (measure every active eval on it — a big swing
needs its guards). When its mechanism is unverified, its slot may instead
hold the `probe` that verifies it, and the wildcard builds next wave on the
answer. **An idea may be deferred once.** State the deferral in
`rejected_summary` as `deferred (1st): <idea> — <reason>`; the next wave
must probe it or build it, and `deferred (3rd)` is a plan the skill
forbids — if you cannot build it, write the `dead_end` that says why and
retire it, or raise the blocker in `open_questions`. Deferral counts carry
in `prior_rejected_summary`; read them first.

Per experiment item:

- **`id`** — run-unique, `"w<wave>-<n>"`. Derive the wave number from
  `prior_rejected_summary` first: it is mandated to open with the previous
  wave's label (`"w3: …"` — see Returning the plan), so this wave is
  `w<k+1>`. `prior_rejected_summary: null` (a run's first wave) or a
  missing/unparseable label → use the memory fallback: take the **newest**
  `EXP <MMDD>-w<k>-…` experiment memory and continue at `w<k+1>` when its
  `<MMDD>` is today **or yesterday** (the label prefix is frozen at run
  start — a run crossing midnight keeps yesterday's date); anything
  older, or no records at all → wave 1. (Two mechanisms, two failure modes:
  the summary anchor covers mid-run derivation — it survives an
  all-`eval_only` wave, which opens no experiment memories; the memory rule
  covers run starts — a same-day second run must continue the count, never
  re-mint `EXP <MMDD>-w1-…` labels the morning run already used.)
- **`evaluation_target`** — the evals(+scores) to measure after the build,
  on train. **Lean-target default**: the designed-to-move evals plus only
  *explicitly-hypothesized* couplings — a neighbor you have a concrete,
  stated reason to fear regressing (name it in `cohesion_notes` or the
  hypothesis). For promotion candidates, must-not-regress guarding defaults
  to the **val gate**: the promotion pass measures the full suite anyway, so
  routine train-side guard evals are duplicated spend paid per item. This is
  your cost lever — one or a few, never the full suite (the full val pass is
  the promotion stage, not your call). `scores` omitted = all directional
  scores; list a subset only for multi-score evals. Informational scores
  can't be targeted. Merge items list their **ingredients' targeted evals**
  (attribution order).
- **`promotion_candidate`** — intent: designed to become the baseline if
  successful. Knowledge-only / one-off probes set `false` and never pay the
  val pass. `MergeWork` requires `true` (schema-enforced). A **single big
  win** needs no merge construction — its own fix item with
  `promotion_candidate: true` goes through the same promotion measurement.
- **`target_items` — the named block.** When the baseline has
  failing train items and your hypothesis explains WHY specific ones fail,
  name them: the eval-input ids (train lane only — never val or test) from
  the failing traces you read. They lead the arm's measurement queue: code
  judges them in the first chunk, and an arm that leaves any of them
  failing stops there, `— NOT LANDED (stopped at n=…: <k>/<n> named items
  pass; still failing …)`, with the rest of the split never judged. So name
  the items your mechanism must flip, not every failure — a target you
  cannot explain is a guess, and a named item that stays failing costs the
  arm. Leave it empty when the baseline is at ceiling or the lever is
  diffuse (a params arm). Without named items, an arm can pay a full
  measurement to learn that it fixed nothing; the first chunk says so for a
  few drives. A `— NOT LANDED` record means the
  BUILD missed, not that the lever is disproven: plan a different build for
  the same targets (say what changes) — never resubmit the same rc.
- **The discriminating block — on by default.** When you name
  no `target_items`, the first chunk of a fix arm's queue is its
  discriminating block: from the parent's stored train rows on the arm's
  targeted evals, up to 8 items the parent FAILS and up to 4 it PASSES
  (spread across those evals, seeded, recorded). Under the quality
  objective the arm goes on when at least half the failing items now pass
  and the guard breaks are within noise; otherwise it stops `— NOT LANDED
  (<k>/<n> fixed, <b> of <g> guards broke, within noise (p=…))` (or
  `clearly above noise`) with the rest of the split never judged. The guard
  half is a test: it fires only when the breaks are clearly
  above the run's noise flip rate (the `--noise-check` rate in your
  `train_noise`, else 0.10), one-sided exact binomial at alpha 0.05. At
  0.10, 1 or 2 of 4 guards broken is noise and 3 of 4 is not; 2 of 3 and 2
  of 2 are not. So read the stop by its words: `clearly above noise` means
  the build broke what the parent passes, so look at those guards first;
  `within noise` means the arm stopped on the fix half (too few failures
  fixed), and a broken guard in it is not evidence against the lever. An
  arm that broke 1 of 4 guards and fixed its failures goes on; the
  quality rule over the rest of the split and the val gate judge it.
  Under speed and cost it goes on when it broke no more guards than it fixed.
  The rule applies to arms that change no model, provider, thinking level
  or sampling parameter (a prompt, skill, tool or code-tool lever); a
  model-level swap is judged by the other rules (its lever is checked on
  the timing drives). Opt an arm out with `residual_gate: false` and a
  `residual_gate_reason` only when its effect is diffuse by design — a
  whole-prompt rewrite or a new surface that should move many items a
  little rather than the failing ones. A stopped arm is listed in your
  next input's `residual_misses`: its rc, the items still failing and the
  guards it broke, per eval. Read those traces (`get_train_results` with
  `item_ids` on that rc, then `Grep` and `Read` the trace files it names)
  and plan a different build for them; never
  resubmit the same rc. Named `target_items` replace this rule for that
  arm and keep their stricter one (every named item must flip).
- **The other stop rules on an arm.** After every chunk code also stops an
  arm whose quality is clearly worse than its parent's (a one-sided test
  on the stratified estimate, alpha 0.05 split over looks at 12, 24, 48, …
  judged items), and — under the quality objective — an arm whose first
  targeted eval can no longer end better than its parent even if every
  item still to judge went its way (`futility`). Under speed and cost its
  timing rules apply as in screening. Under quality an arm is not timed
  (unless it swaps the model), so a timing rule you give it has no pairs
  to read. Its latency and cost per request still show on its record and
  its rows, read off the stored eval runs. An arm's own
  `stop_rules` (optional) are merged over these; set one only with a
  reason. A dollar cap is not a stop rule: it is the item's `max_usd`
  (below).
- **Every measured candidate has a spending cap.** Set
  `max_usd` on each experiment item and each screening candidate, with a
  one-line `budget_reason`: what finding is worth that much ("the p0 eval,
  two failure classes: worth a full split" — not "enough to finish").
  Price it from your input's `measurement_prices`: `drive_usd_per_request`
  (the baseline model's median cost per request on this task),
  `per_eval[].judge_usd_per_item` and `full_measurement_usd` (one full
  measurement of that eval: its train items × (drive + judge)), and
  `timing_usd` when timing applies. A candidate on another model costs its
  own model's price: a screened one carries `cost_per_item_usd` and
  `expected_full_usd` in its result. Without `max_usd`, code sets
  min(1.5 × the expected full measurement, $20, 10% of the remaining run
  budget), re-priced after each chunk at the candidate's own cost
  (`measurement_prices.default_budget_usd` is that default for a full
  measurement now). Code clamps any value to `hard_max_usd`: min($60, 25%
  of the remaining run budget). A candidate whose next chunk would pass its
  cap ends CAPPED — `— CAPPED (capped at $X after n items: inconclusive —
  net +a/−b so far)` on an arm's record, `SCREEN CAPPED: …` for a
  candidate. Capped is NOT a dead end and NOT a failure: nothing was
  refuted. Its judged rows are kept. To go on, plan the same rc again
  (`work.existing_rc_id`) with a larger `max_usd` and a reason that says
  what the capped rows showed; the stored rows are not paid again.
- **FixWork** carries `hypothesis` + `predicted_effect` (required discipline:
  a result you didn't predict is exploration, not an experiment).
  **`technique` is the lever's short name** (`prompt`, `skill`,
  `tool_config`, `new_tool`, `params`, or a name of a few words): at most
  60 characters, one line. It is the lever ladder's key, and the experiment
  record's label carries it. Put what the build does in the `hypothesis`
  (the record keeps its first 900 characters) and in `cohesion_notes`. Code
  cuts a longer technique on the record, so the ladder then counts a cut
  name.
  `parent_rc_id` is a rare escape hatch (an experiment that must fork a
  specific non-baseline config) — absent means the batch baseline. A forked
  arm's speed and cost are judged against the run's baseline, not against
  its parent: a prompt fix on a fast survivor may be somewhat slower than
  that survivor and still win. It only must not be clearly slower than its
  parent (more than 1.5× on a significant majority of at least six items);
  code drives the parent beside it on the same timing items. It carries
  the same `— REJECTED AT VAL` constraint as merge-eligibility below: **check
  the fork target's experiment record for the stamp before naming it.** An rc a
  val gate passed over may be forked as the starting point of a genuinely
  different change; it may never be re-submitted unchanged — that rc alone, or
  the same combination that already lost. (The carve-out below is
  merge-shaped, so it does not bind `FixWork`. A fork of a rejected rc owes
  one line in `rationale` — "forking rc X, `— REJECTED AT VAL` <MM-DD>,
  because <what changed since / why that base still beats the baseline>".
  If you cannot write that line, the fork target is the baseline,
  not the rejected rc; and spending a wrap-up wave's *whole* batch downstream
  of one rejected rc is a concentration bet you have to argue for, not a
  default.)

The two non-experiment item types are flatter — no build, no `evaluation_
target`, no promotion:

- **`eval_only`** (`issue_memory_id`, `reason`) — commissions a **new** eval
  via the eval-build agent for an issue that has none. Experimental and off
  by default: plan it only when `eval_building` is `true`. Like the item below,
  it is refused on a **closed** issue: if its agent cannot reproduce the
  issue it writes `WONT_FIX:`, and the loop never un-closes a `FIXED:` one.
- **`measure_issue_baseline`** (`issue_memory_id`, `eval_id`, optional
  `scores`, `reason`) — runs an eval that **already exists** against the
  current baseline to settle an `UNMEASURED:` issue (above). Take `eval_id`
  from the issue's `eval_<id>` tag or its `- eval: <id>` content line — the
  pair is **checked**: an `eval_id` the issue is not bound to is refused and
  the item fails, because a mismatch would stamp a terminal verdict on the
  wrong issue. A **closed** issue (`FIXED:`/`WONT_FIX:`) is refused too — the
  loop never un-closes one — so only point these at `UNMEASURED:` (or plain
  open) issues. `scores` omitted = every directional score on that eval; a
  list narrows it to the scores the issue is actually about, and an **empty**
  list is an error (omit the field instead). Executed in pure code — no
  agent, no run config, one memory update. Cheap enough to batch several in
  a wave beside real experiments; still not free, so triage by priority.
- **`remeasure`** (`rc_id`, `eval_ids`, `reason`) — completes an
  **existing** run config's measurement on the named evals, in pure code. The
  eval-job cache re-drives only the items that never stored a row, so this is
  the coverage top-up: use it when a prior arm's train measurement was wiped
  or shortened (rate-limit storms, provider flakes — the wave log's
  `eval_job_errors` events name the dark items) or when the promotion gate
  refused a candidate on coverage (`gate_candidate_ineligible` /
  `promotion_blocked` events). Never rebuild a duplicate rc just to re-run
  its evals — that forks provenance and doubles build cost. `split` is
  pinned to train.
- **`probe`** (`question`, `method`, optional `parent_rc_id`, `max_drives`
  ≤ 6 default 3, optional `for_idea`, `reason`, optional `check`)
  — one yes-or-no question about a MECHANISM. No judge, no eval
  job, no experiment record; code writes the answer as a task-scope
  `lesson` tagged `probe` (`PROBE <item>: <question> → YES|NO|UNCLEAR`,
  evidence with counts in the content), the wave log carries it, and it
  reaches your next planning call in `probe_results`. Two forms:
  - **`check` (code, no agent — prefer it):** `{attach: none|probe_tool|
    probe_skill, overrides: {<run_config_properties key>: value}, count:
    tool_calls|skill_reads|reasoning_chars|output_chars|json_valid, rule:
    any|all (default any; all for output_chars/json_valid),
    compare_to_parent: bool, select: first|longest}`. Code forks the parent
    (default the baseline), attaches the throwaway tool or skill, applies
    the overrides, drives `max_drives` train items and counts in the
    traces. `rule: any` → YES when at least one drive's count is > 0; `all`
    → YES when every drive's is; `compare_to_parent` → YES when the count
    MOVED against the parent on the same items (mean ratio ≥ 1.5 either
    way). "Does it call a tool under json_instructions" = `{attach:
    probe_tool, overrides: {structured_output_mode: json_instructions},
    count: tool_calls}`. "Does json_mode change how much it reasons" =
    `{overrides: {structured_output_mode: json_mode}, count: reasoning_chars,
    compare_to_parent: true}`. `overrides` may not name a model, a
    provider, a thinking level, `temperature` or `top_p` — those are
    candidate variants, screened in code (the schema refuses them here). A
    minute, and the cheapest thing in the kit.
  - **a DATA question: `check` with `source: "stored"`.** When
    the answer is already in the parent's stored train rows — "do the
    baseline's train answers call the tool at all?", "how many of its
    outputs parse as JSON?" — code counts `count` over those rows: no fork,
    no drive, no agent, seconds. It takes no `attach`, `overrides` or
    `compare_to_parent`. Ask a data question this way, never with drives.
  - **No probe answers a speed or cost question.** Nothing times a probe;
    speed and cost are measured only by the measurement engine, paired with
    the baseline — screen the variant or build the arm.
  - **no `check` (agent):** for a question only reading answers — "with
    the contract block at the top, does every claim open with its
    decision?" — the probe agent forks, drives and reads at most six
    traces. Phrase `method` so it knows what to fork and what to look for.
    Minutes, and a small fraction of an arm.
  Phrase `question` so yes/no is the whole answer. Use it BEFORE an arm
  whose mechanism nobody has seen work on this task, and to gate the
  wildcard — in a `probe_phase` plan, so the answer precedes the arm.
  `unclear` is a real answer: plan the follow-up probe or the arm with the
  risk stated.
- **`existing_rc_id` on a FixWork** — the arm was BUILT by a
  prior run and lost only its analysis (the run stopped before it: Ctrl+C,
  a signal or a fatal error). Its experiment record reads `— ABANDONED (…)` or
  `— FAILED (infra: timeout)` and carries an `rc: <id>` content line (code
  writes it the moment a build lands). Set `existing_rc_id` to that rc and
  restate the hypothesis: no build session, no build spend — the item
  measures whatever rows are missing and analyzes the rc, and it is a
  promotion candidate like any fix arm. Never pay a full build, in money and
  in wall clock, to rebuild such an arm. A record with
  no `rc:` line predates this field — treat it as unbuilt. The reuse is marked on the new
  record (`reused rc:`), so the lever ladder does not count it twice.
  **The second use is a HELD candidate**: an experiment record stamped
  `— HELD (constraint <id>) <MM-DD>` was not judged on its numbers — the
  gate escalated on that OPEN constraint. Once the constraint record is
  answered (its `OPEN:` prefix gone, or the kickoff guidance answers it),
  resubmit that rc unchanged with `existing_rc_id`: its train and val rows
  are already stored, so the arm costs one analysis session and the gate.
  A `— REJECTED AT VAL` stamp is the opposite case and stays final.

## Merge timing

Merge-eligible wins = `experiment` memories with successful verdicts, not
`— PROMOTED`, whose issues aren't `FIXED:` (take `rc_id` for
`MergeWork.wins` from the experiment memory's content — the analyzer cites
it in the id lines at the top. If those lines were truncated away — content
is capped at 4,000 chars and appends drop the oldest lines first — the rc id
is not recoverable from the record: leave that win out of the merge rather
than guessing an rc, and say so in `rejected_summary`).

A win a val gate already passed over — `— REJECTED AT VAL`, no
`— PROMOTED` — is still an eligible **ingredient**; what it may never be is a
**re-submission**: never re-propose that rc alone, and never re-propose the
same combination that already lost. A genuinely different combination is a
new experiment with its own record (val-gate carve-out below).

- Merge when **several stable wins** have pooled; never while the queue holds
  only marginal wins. Value = overlap-discounted combined gain; cost includes
  the full val pass the winner pays at promotion.
- **Hold off while high-value independent experiments remain** — several
  fix-only waves in a row are normal and expected; merging after every batch
  is an anti-pattern (the pass costs more than the fix waves it punctuates).
- Merge size scales with conflict risk, not count: 15 disparate bug-fix wins
  merge all at once; subtle same-surface fixes shrink the batch. Flag
  same-layer/overlapping pairs in `conflict_flags` (blame priors, zero eval
  cost). Never combine two rcs from the same `head_to_head_group`.
- **Never end holding unmerged wins** — see wrap-up below.

## Wrap-up wave (`should_wrap_up: true`)

This is the last wave. It comes after the run's time limit: the wave that
was in flight at the limit has finished, promotion gate included, and no
normal wave starts after it. It also comes when the budget has room for one
more wave only. How to spend it is your call, but the strong default
is **merge the pooled unpromoted wins** (a merge, or a merge plus one last
cheap fix). **Never return `loop_exit` while unpromoted wins sit pooled**
— spend the wave merging instead. **Carve-out:** a pooled win whose
rc **failed a val gate** does not count as an unpromoted win for this rule —
it never holds `loop_exit` open, and never spend a wave re-submitting it
unchanged (that rc alone, or the identical merge that just lost). It remains
an eligible **ingredient** of a genuinely different combination — which is a
new experiment with its own record. Detect one from its experiment record:
the overview carries `— REJECTED AT VAL <MM-DD>` and no `— PROMOTED`. The
stamp is never removed and names no run, so read it as "a gate passed this
rc over" — not as a dead lever, and not as a this-run-only fact: the same
design re-enters as a new experiment once the condition that failed it
changes (an enlarged eval, an answered OPEN). `loop_exit` on a wrap-up wave
is right only when there are genuinely no wins to merge and nothing worth
its cost. The run then goes straight to the report.

## Returning the plan

- **`probe_phase`** — `true` makes this plan the wave's probe
  phase: `items` holds ONLY probes (at least one), the loop runs them and
  calls you again with `phase: "replan"`, your `rejected_summary` as
  `prior_rejected_summary` and the answers appended to `probe_results`.
  Put the arms in THAT plan. Use it whenever an arm you intend rests on an
  unsettled mechanism; skip it when every candidate's mechanism is already
  answered (the reconnaissance battery settles the common ones before wave
  1). One probe phase per wave — a second probe-phase plan in the same wave
  runs as an ordinary wave of probes.
- **`rejected_summary`** — brief, non-exhaustive prose; this is the one
  in-context carry to the next wave. **Open it with this wave's label**
  (`"w3: …"`) — the next planner derives its wave number from that anchor
  (it survives waves that open no experiment memories). Re-emit what still
  matters from `prior_rejected_summary` and bump its counts — the
  repeat-deferral counts ride this chain and drive the anti-starvation
  rule. Example shape: `"w3: passed on mem_84 again — 3rd time, eval-build
  cost; deferred the tool rewrite pending w3-1's outcome; …and 9 others,
  lower value"`. A few sentences, never a table. **Budget ~1,200 chars**:
  the chain ratchets when each wave re-emits and appends — compress instead
  of accreting: keep live deferral
  counts and still-pending conditionals, drop entries that resolved or
  stopped mattering. (A memory-write-failure disclosure — `memory` skill —
  is exempt: it rides outside this budget, once, summarized, and is never
  re-carried by later waves.)
- **`open_questions`** — human-gated unknowns you defaulted around:
  `{question, default_assumed, gates, reask_when}`. Check existing `OPEN:`
  constraint records first and never re-raise an answered one; when
  re-raising a still-open one, keep the question text verbatim (code dedups
  by exact question text). The orchestrator writes the records. Keep the
  four fields tight — the API caps memory content at 4000 chars; an overrun gets code-clamped
  and loses text.
- **`loop_exit`** — set with a reason to recommend ending the loop ("out of
  worthwhile experiments"; "plateau: acceptance rate falling across waves").
  Explicit beats inference — an empty `items` list exits anyway. Exit means
  **nothing worth doing NOW** — the orchestrator terminates the run *instead
  of* executing your wave, and the schema rejects a plan carrying both items
  and `loop_exit`. **Don't compose work you won't execute**: the batch is
  the deliverable — if you designed items, return them. `loop_exit` is never
  a way to say "the run should end after this wave"; that is the *next*
  planning round's call (`should_wrap_up`, or no work left). Sequencing
  recommendations for future waves ride the prose fields
  (`rejected_summary` / `open_questions`), never the exit channel. The same
  rule covers side effects: never write memory records for work you are not
  returning for execution (no discovery/issue records for dropped candidates
  — and `OPEN:` records are never yours to write at all; the orchestrator
  writes those from `open_questions`).
- Experiment records read as: `— RUNNING` from a past date = abandoned
  debris; `— FAILED (infra: …)` = infra noise, neither win nor dead end,
  retryable; `— ABANDONED` = no verdict.

## Related

`memory` (API + tags — load always) · `kiln-harness-tuner-gateway` (every Kiln operation you have, read
instead of discovered) ·
[references/lever-ladder.md](references/lever-ladder.md) (**which lever to
try first per failure class, with the run evidence — read it in Step 1
before weighing economics**) · `kiln-harness-tuner-experiment-build`
(what builders do with your items) · `kiln-harness-tuner-experiment-analysis` (how verdicts
you read were written) · `experiment-costing` (cost formulas — load before
Step 2).
