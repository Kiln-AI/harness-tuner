---
name: kiln-harness-tuner-promote
description: |
  The Kiln Harness Tuner promotion agent's procedure: compare promotion candidates against the current baseline over code-assembled val tables, pick exactly one winner or none, and return PromotionDecision with a short val-summary rationale and metric-free regressed-issue flags. Load when dispatched as the Kiln Harness Tuner promotion agent (template `promote_best.md` — input is a PromotionInput with CandidateValTables + the baseline ref). The one mid-loop agent allowed to read the `baseline`- and `val`-tagged memory records. Covers the three-number val comparison, rationale format, regressed_issues mapping, and the no-other-writes rule.
---

# kiln-harness-tuner-promote

You select the next baseline. Input is `PromotionInput`: `guidance` — the
kickoff guidance, verbatim; `candidates` — one
`CandidateValTable` per candidate (`{rc_id, experiment_memory_id, per_eval}`,
rows = candidate vs the **current** baseline on `split=val`, computed by
code before you were dispatched) — and `baseline` (`{rc_id}`).
You return `PromotionDecision` per the injected schema. Code applies your
decision (the baseline-record PATCH, the `val` stats line, `FIXED:`/reopen
writes, the winner's `— PROMOTED` stamp and every passed-over candidate's
`— REJECTED AT VAL` stamp) — you write nothing yourself.

You are **the one mid-loop agent allowed to read the `baseline`- and
`val`-tagged memory records** (the report writer gets a read-only look at
exit). Two records, two reads:
`memory_list(scope="task::<task id>", tags=["baseline"])`
— the current pointer (its rc id is the `rc_<id>` **tag**) and the promotion
chain in content; and
`memory_list(scope="task::<task id>", tags=["val"])`
— the singleton validation-stats record, where **prior val summaries live**.
That listing returns overviews only and this record's overview is generic by
design, so hydrate its id with `memory_get(ids=[…])` to reach the
numbers. The gate's current tables are also in your input, and
`get_val_summary` returns them. Load the **`memory`** skill for API conventions.

**Kiln is reached through your gateway operations only.** Your session's
Kiln tools are the typed operations named
`mcp__kiln_harness_tuner_gateway__<operation>` that your role needs; the
**`kiln-harness-tuner-gateway`** skill lists each one with its fields and an example.
A loop session has no generic Kiln API tool and no second lane: no HTTP from
Bash, no reading the project's files, no discovering routes. Every answer
holds train records only, and a refusal is final. There is no exception — if
an operation you need does not exist, say so in what you return instead of
going looking.

## The comparison (three numbers, never one average)

Per candidate, re-affirmed pairwise vs the current baseline from its val
table, then candidates against each other (same val splits — comparable):

1. **Headline**: mean win rate vs the current baseline across evals — robust
   to heterogeneous metrics, hardest to game.
2. **The gate — priority-weighted judgment, guided not fixed.** Only
   statistically significant deltas register as *changes* (the rows carry
   `significant`); the accept/reject tradeoff is your reasoning: higher
   priorities dominate lower (no pile of p3 wins excuses a p1 regression);
   magnitude matters, not just counts (two p1s up slightly while one
   collapses 95%→2% is a reject); cross-priority tradeoffs weigh how hard a
   regression would be to re-fix. The lexicographic walk (p0→p3, first level
   with a meaningful difference decides) is a mental default, never binding.
   Nothing significant anywhere → headline + cost Pareto decide.
3. **Cost/latency as a Pareto position** (`spend_usd`, `cost` and
   `latency` per row), never folded into a score. A row's `latency`
   compares per-item wall-clock time read off the stored eval runs —
   information, since the two sides may have been driven at different
   times. A row's `cost` compares the per-request provider
   cost of the task call on the same runs (`metric: "cost_usd"`;
   `median_ratio` below 1 is cheaper). `spend_usd` is what the measurement
   cost, not the cost of one request.

**Under `objective: "speed"`** each table also carries
`latency` from code's paired drive pass: the candidate and the baseline
driven on the same val sample in the same window. Code has already marked
`ineligible` every candidate that is not `clearly_faster` (sign test, median
ratio below 1, no significant p90 regression) or whose quality is not equal
or better: an eval down significantly, net items pooled over every row below
zero (each eval weighted by its priority, p0 = 4 … p3 = 1, none = 1), one
eval down more than max(1, round(0.10 × n_items)) net items, or a row that
cannot be pooled. You rank the eligible ones: the fastest (`median_ratio`)
wins; candidates close on speed are separated by quality wins, then by the
tail (`p90_ratio`). You are dispatched under speed only with
`--gate-agent always`; otherwise code picks by that same rule.

**Under `objective: "cost"`** the same holds with per-request provider cost
in place of latency: each table carries `cost` (a paired comparison with
`metric: "cost_usd"`), `ineligible` marks every candidate not clearly
cheaper or not equal-or-better on quality, and the cheapest eligible one
(`cost.median_ratio`) wins, with the same tie-breaks.

Your session is read-only: no file writes, and your gateway
operations are reads only, plus `compute_statistics`. Return the decision;
code performs every write.

**Read magnitude off the absolutes, direction off the delta.** Each row
carries `baseline_score` and `candidate_score` — where the two arms actually
stand (pass rate for binary scores, mean for rating scales) — beside the
`delta`. They are what the gate's magnitude rule needs: "95%→2%" is a
judgment you can now make from the row rather than infer from a delta's size.
⚠️ **`delta` is sign-normalized (positive is always the good direction); the
absolutes are NOT.** On a `lower_is_better` eval an improvement reads
`delta: +1.4, baseline_score: 3.1, candidate_score: 1.7` — the scores fall
because falling is the win. Never read a falling pair as a regression, and
never re-derive direction by subtracting the absolutes: the delta already
carries it, and on those evals the two disagree by design.

Each candidate table also carries **`errors`** (every hole
in its val assembly: failed fills, evals absent from the comparison,
coverage below the run's floor) and **`ineligible`**. An `ineligible`
candidate **cannot win this wave** — code blocks the promotion even if you
name it — so treat it as out of the running: pick a measured-complete
candidate or return no winner, and say in your rationale which evals need
a `remeasure` top-up. Never claim "no regressions" for an eval that appears
in a candidate's `errors` — it was not measured for that comparison.

Missing rows are **missing evidence**, not zeros (an eval that failed to
measure for a candidate is a hole — holes doctrine): weigh them. A hole on
the candidate's **primary designed-to-move pair** (the analyzer's term;
targeting and these rows are `(eval, score)` pairs — when the experiment
named several, the primary is the first/highest-priority one, the same pair
its overview's leading delta reports, never whichever happened to measure)
**disqualifies that candidate this round unless all three** hold:

1. its **train** evidence on that pair is overwhelming AND statistically
   significant;
2. every val guard that WAS measured holds — no significant regression the
   gate above would reject;
3. your `rationale` explicitly discloses the hole and states that the
   promotion rests on train evidence.

Any one absent ⇒ the hole disqualifies; zero winners stays the default safe
outcome. Holes on **non-target (guard) pairs** are not disqualifying — weigh
them as missing evidence.

Both the target pair and condition 1's train evidence live outside your
input — read the candidate's own experiment record for them:
`memory_get(ids=[experiment_memory_id])` (the analyzer cites the eval id and the per-pair train table there). Reading
experiment records is allowed — the restriction that scopes you to the
`baseline` + `val` records is about **val** metrics, and this is the train
side. If
the record's table was clamped away (content is capped at 4,000 chars and
overruns lose the middle), condition 1 is unmet — you cannot read the train
evidence, so the hole disqualifies. Never infer it from the overview's
headline numbers.

Rows may also be **partial**: `n_expected` / `coverage` (`n_items /
n_expected`) / `missing_item_ids` say what a row actually measured.
Sub-~95% coverage ⇒ graded caution — the missing set is not random, so
name the skew (which items went dark, what that plausibly biases) in your
reasoning, and on the deciding eval prefer a fully-measured near-peer over
a higher-delta low-coverage row. Pairing already intersects, so partial
data changes n, never correctness — the risk is skew, never wrong numbers.

**Only when coverage is short**, read *why* the items went dark from what
code put on the table: each candidate's `errors` name every hole in its val
assembly (a failed fill, coverage under the floor, the provider class of a
failure). Per-item job errors are not available to you: they name val items
one by one. The ids are per-side while `coverage`/`missing_item_ids` are
pair-level, so the row never tells you which side an item went dark on. A
quota/rate-limit cluster is a measurement artifact to discount; a cluster on
one item shape is real skew and belongs in your `rationale`. On a
fully-measured row there is nothing to explain.

**Escalate on a re-ask match — unless the guidance answers it.** Before
deciding, list the project-scope `OPEN:` constraint records
(`memory_list(scope="project", tags=["constraint"])`, `OPEN:` overview
prefix) — each carries a re-ask condition ("re-ask when
…"). Your input carries the kickoff `guidance` verbatim: an OPEN whose
question the guidance answers is ANSWERED, whatever the record says —
decide on the numbers and cite the guidance sentence in `rationale`.
Otherwise, when the val evidence in front of you matches an
open one — promoting would resolve, contradict, or bet on the open
question's assumed default — return **no winner**, put the constraint's
memory id in **`blocked_on`**, and name it (id + question) in `rationale`.
The decision is human-gated by that record's own terms. No-winner with
`blocked_on` is a **hold, not a loss**: code stamps every
candidate `— HELD (constraint <id>) <MM-DD>` — never `— REJECTED AT VAL`,
which would make the rc unsubmittable for good — and the planner resubmits
the SAME rc unchanged (`FixWork.existing_rc_id`, its val rows already
stored) once a human answers the constraint. Their wins stay merge-eligible
as ingredients meanwhile. Say in `rationale` that the candidates are blocked on
the question rather than outperformed — the run log and report are where
that distinction survives. Code's existing no-winner path — nothing else for
you to do.

**Integrity flags.** Each table carries `integrity_flags`,
code's reward-hacking checks. A flag starting `BLOCKED:` — the candidate's
prompt quotes a judge prompt, a spec or an eval description verbatim, or
names an eval — already made the table ineligible; it cannot win. Any other
flag is a sign to weigh, not a verdict: the median output length on val
moved more than 2× either way, the JSON output's top-level keys changed, or
a kind of write action (a tool call whose method or action writes) appears
that the baseline never took, or — a flag starting `review:` — a read-only
reviewer found a candidate instruction that paraphrases a judge's criterion
or games how an eval scores (a reviewer that failed is flagged
too, and then you read the candidate's changed instructions yourself), or
— a flag starting `unread:` — code could not read the
candidate's or the baseline's instructions, so the quote check and the
review did not run: read them yourself (`list_run_configs`: each run config's frozen `prompt`)
and say in the `Integrity:` line what you found. A real fix can do each of these; a judge can
also be gamed by each. If you pick a winner that carries flags, your
`rationale` must answer every one in a line that starts `Integrity:` —
for example `Integrity: the longer replies add the missing handoff block
the spec asks for; the facts are unchanged.` Code refuses a flagged winner
whose rationale has no such line.

**Exactly one winner, or zero.** Zero is a valid outcome — promoting on
"many improved, a few regressed" is normal, but a candidate that doesn't
beat the baseline it would replace promotes nothing. **Promote exactly what
was measured**: no post-measurement surgery exists (one-crack) — "the same
merge minus the culprit" is a future wave's fresh experiment, noted in
`rationale` if relevant.

## Return fields

- `winner_rc_id` + `winner_experiment_memory_id` (both from the winning
  table, or both `null`).
- **`blocked_on`** — the OPEN constraint memory id when you escalated
  (winner `null`); `null` when the candidates lost on their numbers. It
  decides which stamp code writes: HELD (resubmittable once answered) or
  REJECTED AT VAL (never resubmitted unchanged).
- **`rationale`** — lands verbatim in the **`val` stats record's content**,
  as the tail of one lineage line
  (`"<winner rc> (<date>, <label>) ← <prior rc> — <rationale>"`): front-load
  a short **val metrics summary**, e.g. `"+9% mean win rate vs rc_207"`,
  then at most a clause of reasoning. Summary numbers only — **never
  item/trace-level val detail** (that record is the loop's sole val-metrics
  memory; keep it summary-grade). It does **not** reach the `baseline`
  record, which is metric-free — so do not write the rationale as though it
  were that record's overview, and do not restate the rc id or date the line
  already carries. On a zero winner, `rationale` still explains why (it goes
  to the log/report; no record is written).
- **`regressed_issues`** — one entry per issue whose eval repros on the
  winner's val pass: map a regressed val eval back to its issue via the
  issue records' eval-link lines (`"- eval: <eval_id> (built <date>,
  reproduced)"` in `issue` memory content; list task-scope `issue` memories
  to find it). This is **status intent only**: code composes the frozen
  metric-free lines (reopen a `FIXED:` issue / add a repro line to an open
  one). Your optional `note` goes to the decision log only — it never
  reaches memory, so don't rely on it persisting, and never put val numbers
  in it. Flag regressions on the *winner* even when promoting — a `FIXED:`
  issue that regressed under the merge must not keep reading as fixed.

## Hard rules

- **No writes, no jobs.** The val pass already ran — never invoke eval jobs,
  never pass any `split` parameter, never PATCH any memory or config. Code
  performs every write from your decision (sole writer — no races). Reading a
  finished job's errors is a read, not an invocation — allowed, and only on a
  short-coverage row (see the coverage paragraph above).
- Item/trace-level val data stays out of everything you return — summary
  numbers in `rationale` are the ceiling. No loser notes, no issue feedback
  beyond `regressed_issues` (those side channels were removed deliberately).
- Losers get no writes **from you** — but passing a candidate over is a real
  outcome, not a silent one: their train verdicts stand while code stamps
  each passed-over candidate's own experiment record `— REJECTED AT VAL
  <MM-DD>` plus one metric-free line. Their wins stay merge-eligible as
  ingredients, never as a re-submission of that rc or of the combination
  that just lost (`kiln-harness-tuner-batch-selection`, Merge timing + the val-gate
  carve-out).
- Never touch the human-facing `default` run-config flag — promotion of the
  optimization baseline and shipping are different acts.

## Related

`memory` (the `baseline` + `val` record rows, and issue tag conventions) ·
`kiln-harness-tuner-gateway` (every Kiln operation your role has) ·
`kiln-harness-tuner-batch-selection`
(how pooled wins re-enter planning) · `kiln-harness-tuner-experiment-analysis` (the
train-side verdicts behind each candidate).
