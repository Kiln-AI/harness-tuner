---
name: kiln-harness-tuner-experiment-analysis
description: |
  The Kiln Harness Tuner experiment-analysis agent's procedure: interpret the label-free train-side numbers table for one experiment, judge `successful` against the experiment's own targeted goals, and return ExperimentResult with the frozen memory-overview/content formats. Load when dispatched as the Kiln Harness Tuner experiment-analysis agent (template `analyze_experiment_results.md` — input is an AnalysisInput with the item, rc_id, AnalysisBundle, and experiment memory id). Covers reading delta/n/p/CI rows, caution + power judgment, measurement holes, screen-out rows, merge attribution, and the train-side-only prohibitions.
---

# kiln-harness-tuner-experiment-analysis

You interpret one experiment's measurement. Input is `AnalysisInput`: the
`item` (hypothesis + predicted effect), `rc_id` (the candidate), `bundle`
(the code-computed numbers), and `experiment_memory_id` (your open record —
read it with `memory_get(ids=[experiment_memory_id])` for the exact
`EXP <MMDD>-<item id>` label and the stored hypothesis/parent rc). You return `ExperimentResult`
per the injected schema. **Train-side only** — everything you read, judge,
and write is learning-side data.

**Kiln is reached through your gateway operations only.** Your session's
Kiln tools are the typed operations named
`mcp__kiln_harness_tuner_gateway__<operation>` that your role needs; the
**`kiln-harness-tuner-gateway`** skill lists each one with its fields and an example.
A loop session has no generic Kiln API tool and no second lane: no HTTP from
Bash, no reading the project's files, no discovering routes. Every answer
holds train records only, and a refusal is final. There is no exception — if
an operation you need does not exist, say so in what you return instead of
going looking.

## Reading the bundle (no labels, by design)

`bundle.per_eval` has one row per targeted (eval, score) pair: `delta`
(candidate − baseline, **sign-normalized** — positive is always the good
direction), `baseline_score`/`candidate_score` (where each side actually
**stands** — pass rate for binary scores, mean for rating scales; **raw**,
never sign-normalized, so on a `lower_is_better` score they FALL while
`delta` reads positive), `n_items`, `p_value`, `significant` (p < .05, a data
flag, not a framing), `ci_low`/`ci_high` (the confidence interval on `delta`,
in the SAME good-direction units — `ci_low > 0` is "better beyond doubt",
`ci_high < 0` is "worse beyond doubt", a CI straddling zero is the normal
small-n case; `stderr` is always None — no statistics operation returns one),
`fail_to_pass`/`pass_to_fail` (binary scores only), `spend_usd` (what THIS run
paid for the pair; `evidence_spend_usd` is the cost of every stored row read),
`split` (always `"train"` here), and `compared_to`:

- `"parent"` — paired against the actual parent rc: the delta means "my
  change did this" (attribution).
- `"prior_baseline"` — a **screen-out**: the parent was unmeasured, so the
  candidate was screened against the latest measured baseline in the
  promotion lineage. Read it as "didn't beat the latest measured baseline,
  attribution unmeasured" — **never** as "the change hurt". If attribution
  matters to the hypothesis, put a parent-measured re-run in `next:` (the
  planner's `parent_rc_id` escape hatch, a later wave).

**Coverage and holes (partial results).** A measured side that hit job
errors still ships rows: those rows carry `n_expected` (the job's expected
item count), `coverage` (`n_items / n_expected` for the pair),
`missing_item_ids` (the dark items, named), and
`baseline_job_id`/`candidate_job_id` — the last eval job each side ran
(`null` where that side was a free cache read). **The ids are for the promote
agent**, whose val tables carry no error text; do not fetch a job with them.
Your copies of that text are already inlined in `bundle.errors`, so a fetch
is duplicate spend on the lane whose doctrine is never to hand-recover a
hole.

`bundle.errors` holds two kinds of entries: `partial:` entries naming a
partially-measured pair's dark items with the fetched job-error messages,
and true **measurement holes** — (eval, score) pairs with **no rows at all**
after the retry ladder. Code applies no completeness threshold — weighing
coverage and holes is yours:

- **Sub-~95% coverage ⇒ graded caution, and check the missing set for skew.**
  Whether the missing set is random is a **question you answer from the
  evidence, not a prior you inherit**. Judge-side holes can cluster by
  content, for example on one hard shape of input. Generation-side holes from
  several eval jobs that contend for one provider can show no content signal
  at all. So read
  `missing_item_ids` + the `partial:` error messages, say in `findings` which
  items went dark and whether they share anything, and state which way it
  came out — including "no pattern I can see", which is a finding. Pairing
  already intersects, so partial data changes n, never correctness — the risk
  is skew, and naming it (or ruling it out) is the mitigation.
- A hole on a must-not-regress target usually means **not** successful (you
  can't show it didn't regress); a hole on a side-interest pair may be
  acceptable, stated in `findings`.
- **Holes doctrine — the designed-to-move pair.** When the pair this
  experiment was designed to move is itself a hole (or its coverage is too
  low to read), `successful: true` requires BOTH disclosed direct evidence
  in `findings` (what you actually saw — specific traces, cited) AND either
  the affected ingredient marked `landed: false` (merge items) or a `next:`
  re-measure line. Never a success claim on absent numbers alone.
- **Never hand-recover a hole.** The bundle's rows — partial or full — ARE
  the measurement: don't re-pair raw results, re-derive deltas, or
  hand-compute stats from raw `get_train_results` reads to fill a gap. A
  hole worth closing is a `next:` re-measure line.
  Reading traces to *explain* numbers (below) is still yours.

**Dangling references.** `reference_findings` in your input is
set when code found that the measured run config names a skill or a tool it
does not attach (`kiln_harness_tuner.reference_check`). The record carries the same finding on
a `reference check:` line. Read the traces of the failing items that use
that name. When they fail on the missing skill or tool, say so in
`findings`: the result then measures the build's breakage, not the lever. A
`next:` line can ask for the same lever built again with the names and the
attachments in agreement.

**Spend: `bundle cost`, and nothing else.** `bundle.spend_total_usd` is the
ONE spend figure an EXP record reports, and it is **always labeled `bundle
cost`** — never "spend", "cost", "eval cost" or a bare dollar figure, so
that every record's figure means the same thing. It is the provider cost of the persisted EvalRun rows
feeding this comparison — **both arms** (candidate + baseline), subject
`task_run_usage.cost` + judge `eval_usage.cost` — over the item's measured
evals. What it does not tell you — say the clause when it bites:

- It excludes the agent sessions (your analysis session, the build session):
  they run on the agent CLI's account and are booked as tokens, never
  dollars.
- It **under-reports by the holes**: a pair with no rows contributes $0 even
  though the job that failed to measure it cost real money.
- It is the cost of the rows this comparison *reads*, not new money this item
  spent — a cached baseline arm is deliberately indistinguishable from a
  fresh one, so that half may have been paid for in an earlier wave.

Report the figure as given. Never patch it with a hand-computed estimate
(same rule as never hand-recovering a hole).

Read actual failing traces when the numbers need explaining:
`get_train_results(eval_id=…, run_config_id=…)` — the gateway reads the
eval's current (default) config, the one your bundle was measured with, and
split train. Its rows carry `scores`, cost, latency, an `output_preview`
and a `trace_summary` per item, and `trace_path`: the trace is a file, not
part of the answer (`kiln-harness-tuner-gateway`, "Traces are files").
Pair across run-configs by `item_id`. Then `Grep` the run config's trace
folder for the failure — `TOOL_ERROR`, a tool name, a phrase of the
expected output — with line numbers, and `Read` the trace from a few lines
above the hit. Call it once per run config you compare: the files of both
arms are then on disk. For a latency or cost question, start from the run
config's `_stats.md` (turns, tool calls, tokens per item; per-turn tokens
in `_stats.json` when the trace stored them). Train split only, ever. For
an item's expected output — what it should have produced —
`get_train_expected(item_ids=[…])` returns its `reference` and its tags.

## Latency, and the speed objective

Every row carries `latency`: the per-item wall-clock latency
(`total_llm_latency_ms`) of the two sides of that eval's comparison, read off
the same eval runs (`method: "eval_runs"`). It is information only — the two
sides may have been driven hours apart, under different provider load.
Every row also carries `cost`: the per-request provider cost of
the task call on the same two sides, from the same eval runs (`metric:
"cost_usd"`; `median_ratio` below 1 is cheaper). It is information too.
Under the quality objective the arm is not timed (there is no per-request
limit), so `latency` and `cost` are how its speed and its
cost show. Quote a ratio in `findings` when it moved; never judge
`successful` on either under quality.

Under `--objective speed` the bundle also carries `latency` at the top
level: code drove the parent and the candidate on the same fixed train
sample in the same window (`method: "paired_drive"`). For an arm forked from
a parent that is not the baseline, `latency` is against the run's
BASELINE on the same items; the parent only had to be not
clearly slower. Read it as:
`median_ratio` (per-item candidate/parent; below 1 is faster), the sign test
(`faster_items`, `slower_items`, `sign_test_p`), the tail (`p90_ratio` with
its 95% interval), and the code's verdict `clearly_faster` — which already
requires the tail not to be worse beyond doubt. Then `successful` means:
clearly faster AND quality **equal or better** — your input's `speed_rule`
is the exact rule: nothing down significantly, the net items pooled over
every row at least zero (`fail_to_pass − pass_to_fail`, or the normalized
change × items for a rating; each eval weighted by its priority, p0 = 4 …
p3 = 1, none = 1), no row down more than max(1, round(0.10 ×
n_items)) net items, every row poolable. The quality evals are guards, not
targets. You only ever see an arm the measurement engine did not stop: its
timing pairs were in and not clearly worse, and its losses on the parent's
passes were not clearly above the noise flip rate at any look
(`guard_losses`: fixes of the parent's failures are not counted there). An
arm stopped earlier never reaches you. Code enforces that floor; you may
still veto, never promote past it. Put the speed numbers in `findings`
beside the quality rows; code appends a `latency:` line to your record.

The arm's `measurement:` line under speed and cost reads `lost k of n
baseline passes (no loss rate above X% at 95%) · fixed f of m baseline
failures (noise level)`. Read it as a bound: the arm can still lose up to
X% of what the parent passes. A fix of a parent failure is noise-level
evidence (an item that failed once often passes on a re-run): never set it
against a loss in your judgment, and never write that quality "held" or was
"neutral" from it — write "no loss rate above X%", or name the losses. A
split of a few hundred paired items or fewer cannot rule out a drop of a few
points.

## The judgment: `successful` + graded caution

`successful` = **did the experiment meet its own targeted goals** (the
item's `evaluation_target` + predicted effect), on train. This gates the val
promotion pass together with the planner's `promotion_candidate` intent.

- The numbers are the verdict; you contribute interpretation. At the small n
  we run, p < .05 is rare — a **low-significance positive may be judged
  successful**: risk accepted during experimentation, the val promotion gate
  is the real filter. State *how much caution to adopt with* in `findings`
  rather than rounding to a binary.
- **Power call**: a well-powered null (decent n, a NARROW CI around zero —
  `ci_low`/`ci_high` both small) is a real "didn't work"; an eval too small
  to detect the plausible effect (a WIDE CI that straddles zero) is "can't
  tell" — put `next: enlarge the eval` and don't claim anything.
- **At ceiling, a perfect arm is `successful`**. When the baseline's headroom is at or under the noise floor
  (one or two items), p can never be small, so "inside noise" is not a
  verdict. An arm that scores 100% on the primary target with
  `pass_to_fail: 0`, and whose fail→pass items are the baseline's hard
  failures (they fail on every stored baseline row) with a traced mechanism,
  is `successful`: there is no evidence it is noise, a zero-mistake run is
  exactly what the val gate exists to test, and refusing it hides a working
  cure. Say the caution in `findings` (one item, judge flips on N inputs);
  do not round it to `false`. Refuse on p alone only when the arm also
  regressed something or the gain has no mechanism behind it.
- **Replicate rows are folded, never drawn from.** Code pairs on ONE record
  per item; when the baseline holds several stored rows for an input (a
  retried job, or concurrent items that each measured it) the record is the
  per-input MEAN of its rows, and `n_expected` counts items, not rows.
  `baseline_score` is therefore the replicate average — read it as such.
- **The code rule is the floor under `successful`**: code
  re-checks your `successful` against the bundle — the primary target
  (the item's first `evaluation_target`) measured and improved on at least
  one score, no row with `ci_high < 0`, every row's coverage at the gate's
  floor — and turns a `true` that fails it into `false`, appending the
  reasons to `findings`. You can veto a passing rule; you cannot talk past
  a failing one. Say so in `findings` when you expect the rule to fire.
- **A missed prediction is caution, never a verdict**. Every veto of a passing rule carries a `veto_reason`:
  `dominated_by_ingredient` (a merge scored below a strictly simpler config it
  contains), `measurement_holes`, `secondary_regression`, `other` — or
  `prediction_miss`, which code does NOT honour: when the primary target
  improved and the floor passed, the arm stays `successful` and the miss goes
  in `findings` as the caution to adopt with. Judge magnitude against the
  row's **`headroom`** (the largest delta the score can still show: `1 −
  baseline pass rate` on a binary score), not against the item's prediction:
  a prediction above the headroom was impossible by construction and says
  nothing about the arm.
- **Priority-weighted reasoning** across pairs: higher-priority evals
  dominate lower; magnitude matters, not just counts (2 targets up slightly
  while one collapses 95%→2% is probably a fail); cross-priority tradeoffs
  are judgment, weighing how hard a regression would be to re-fix.
  Mechanically, only significant deltas *register* as changes — but graded
  caution on the rest is exactly your job. Same procedure at every target
  size: one pair or many.
- Partial wins are wins when the misses are honest: say what moved, what
  didn't, and what that means for the hypothesis. For **merge items**, read
  [../kiln-harness-tuner-experiment-build/references/merging.md](../kiln-harness-tuner-experiment-build/references/merging.md)
  (attribution + partial-win framing) and fill `per_ingredient_outcomes` —
  one row per ingredient issue, `landed: false` when unconfirmed (promotion's
  `FIXED:` writes source from these rows).
- **`landed: false` on a fix item too.** A `FixWork` item's issue is closed
  `FIXED:` on promotion by default. When the experiment is `successful` but
  its issue did **not** actually close — a partial win, or the win came from
  somewhere other than the issue you targeted — emit one
  `per_ingredient_outcomes` row for that issue with `landed: false` and a
  `note` saying so, and promotion writes no closure. Say it, don't leave it
  implied in `findings`: planners skip `FIXED:` records as fix candidates, so
  a false closure hides a *known working cure* from every later wave. Omitting
  the rows keeps the default close, which is the right default — this is the
  exception, and only you can see it.

## Return fields (code writes the memory from these — formats frozen)

- **`memory_overview`** (≤280): the record label, a short hypothesis tag,
  then ` — ` and the outcome **as numbers, front-loaded, no verdict words**.
  One UI-parsing rule is load-bearing: the UI splits on the **first** ` — `
  and renders the tail, so never put ` — ` inside the label/tag (use a plain
  hyphen or comma). Nothing infers a verdict from the tail's leading
  character any more — write the numbers, never a ✓/✗ mark of your own.

  **Frozen format.** One measurement row is

  ```
  <score>[↓]: <signed delta>[pp], base=<baseline score>, cand=<candidate score>, n=<n>, p=<p>
  ```

  **The delta and the absolutes always share units**, so the *gap* between
  them is the delta's magnitude with no mental arithmetic — on a `↓` row
  sign-normalization flips the sign, which is what the marker announces:

  - **Binary (pass/fail) scores** — the bundle gives all three as fractions;
    render all three ×100, the delta suffixed `pp` (percentage **points**).
    `+18pp, base=62, cand=80`.
  - **Rating scales** — the bundle is already in the scale's own units;
    render all three as-is, **no suffix**. `+1.4, base=3.1, cand=1.7`.

  Examples:

  - **Single target** — the row alone:
    `"EXP 0716-w4-2: retry-skill for 429s — accuracy: +18pp, base=62, cand=80, n=34, p=.04"`
  - **Multi-target** — the primary (first-target / highest-priority) row
    first, then the rest `|`-joined:
    `"EXP 0716-w4-3: shorter tool descs — accuracy: +14pp, base=62, cand=76, n=34, p=.04 | correctness: +3pp, base=80, cand=83, n=34, p=.4"`
  - **Overflow** — rows that do not fit the 280-char cap collapse into a
    trailing `"| +N more"`. This row is ~50 chars, so a labelled overview
    holds about five before overflowing (fewer once a lifecycle suffix like
    `— REJECTED AT VAL <MM-DD>` is appended) — deliberate: the cap did not
    move, and the full table always goes in content, which is where a reader
    with more than a handful of targets belongs.
  - **Direction marker** — append `↓` to the score name on a
    `lower_is_better` row:
    `"EXP 0716-w4-4: trim retries — tool errors↓: +1.4, base=3.1, cand=1.7, n=28, p=.02"`.
    `delta` is sign-normalized (positive is always good) while `base=`/`cand=`
    are **not** — they carry the stored scores' own direction, so without the
    marker a falling pair of absolutes beside a `+` delta reads as a
    contradiction.

  Field rules: never `%` on a delta — it invites reading a percentage-point
  move as a relative change. Every number is labelled: never write a bare
  `x/y`, which already means coverage on these rows and must not also mean a
  pass count. `p=` stays — a reader who sees only the overview needs the
  evidence behind your caution grade.
- **`memory_content`**: hypothesis / lever / **candidate rc id (MUST be
  cited — planners assemble MergeWork.wins from it)** / **issue memory id
  (MUST be cited — FixWork: `item.work.issue_memory_id`; merge analyzers
  join ingredients back to issues through it)** / eval id / parent rc + how
  compared (`parent` vs screen) / `head_to_head_group: <group>` when the
  item declares one (re-cite the frozen line — your content replaces the
  record's, and later planners/builders read the group off the post-verdict
  content; code re-appends it if you drop it, belt-and-braces — the
  same goes for the open record's `issue:` and `technique:` lines, which
  the lever ladder's count reads) /
  the full per-pair numbers table incl.
  holes / `bundle cost: $<n>` (that label, that figure — see above) /
  interpretation + caution grade / `next:` line written for
  a cold start. (Your content *replaces* the open record's — re-cite
  everything downstream readers need; nothing carries over.)
  **Hard budget: 4000 chars** — the API caps memory content there
  (writes past it get 422 `string_too_long`). Code safety-clamps an overrun by dropping the MIDDLE
  (head id lines + `next:` tail survive), so going over silently costs
  you your numbers table and interpretation. The MUST-cites (rc ids,
  issue id, eval ids, the table, `next:`) fit comfortably in ~1200 chars
  — only ~30% of budget, so there's real headroom for interpretation; when
  tight, still shrink the interpretation prose, never the ids, the table,
  or `next:`.
- **`findings`**: what was learned, suspected culprits on failure, the
  caution/power reading — prose for the orchestrator log and report.
  **Name the decision point** (the lever ladder's first rule):
  for the failing items you read, say whether the failing message was
  emitted BEFORE any tool call, WITH a tool call (narration riding the
  call), or AFTER a tool's result — and which tool. The next planner picks
  the surface from this: before/with → that tool's description; after →
  the tool's response or the prompt; at skill selection → the skill
  description. A finding without the decision point sends the planner back
  to instruction text.
  **Name the failure class** too, per failing item you read: **config** (the
  run config did it wrong — the only class a fix arm can move), **eval
  data** (the expected output or the case itself is wrong or cannot be
  met), **judge** (the judge's verdict contradicts its own reasoning or the
  rubric), **environment** (a tool server, the provider or the task's
  world failed or answered wrongly), or **noise** (the same config passes
  it on another run). Compare with `get_train_expected` before you call
  one eval data. An expected output that applies a rule the task's
  instructions do not state is not eval data: the expected outputs are the
  owner's policy, so a config that misses that rule is a config failure.
  Call it eval data only when the expected output contradicts the
  instructions, or contradicts the expected outputs of the same kind of
  input. A finding that lumps the classes together sends a planner
  to fix the config for an eval's fault.
  **A judge may read the config.** A judge that scores the full trace sees
  the tool results, and a skill the model loaded is one of them. When the
  arm changed only skill or tool-description wording and a judge-scored row
  moved, check whether the judge's input holds that text: the judge may be
  grading against the config's own wording, not the rubric. Say so as a
  caution.
- A measured negative is a normal, valuable verdict: return envelope
  `status: "ok"` with `successful: false`. Reserve `failed_expected` for
  "I could not produce a judgment at all", with the reason in `detail`.

## Hard rules

- **Never edit run configs, evals, eval configs, or splits** — you see
  results, so you have no build surface (eval-freeze, one-crack). No
  re-measurement, no post-measurement surgery; redesigns go in `next:`.
- Never ask for val or test results (your gateway operations answer from
  train records only). Never query
  `tags=val` — that record *is* every promotion's val metrics. Never read
  the `baseline`-tagged record either (`tags=baseline`): it is metric-free
  now, but it sits beside the `val` one in the same task scope and you were
  handed the rc id, so the read buys nothing.
- Lifecycle writes are code's (your record is PATCHed from your return).
  Yours are **discovery writes only**: a new `issue` observed in traces
  (dedup at ingestion — list existing `issue` memories first), `lesson` /
  `dead_end` at the moment of learning. All train-derived. Load the
  **`memory`** skill for conventions.
- **If a discovery memory write fails, disclose it**: record the failure
  and the intended content in `findings` — the run record must carry the
  finding even when the API write didn't.

## Related

`memory` (tags, write discipline) · `kiln-harness-tuner-gateway` (operation detail — fields,
body fields, response shapes for every Kiln endpoint) · `kiln-harness-tuner-experiment-build` +
`references/merging.md` (what was built; merge attribution) ·
`kiln-harness-tuner-batch-selection` (who reads your record next wave).
