---
status: complete
---

# Functional Spec: A repeat measurement the planner can ask for

## 1. Goal

The planner can ask the loop to measure an existing run config again on
train items, and get the result in its next planning call. The playbook
`dealing-with-a-noisy-eval` can then tell it to repeat a measurement that
a decision rests on, and the loop can do that step.

The problem is in [project_overview.md](project_overview.md), section 1,
and in [review_finding.md](review_finding.md).

## 2. The shape: a new item type, `repeat`

```
{
  "id": "w3-2",
  "type": "repeat",
  "rc_id": "<the arm, the baseline, or another run config of the task>",
  "eval_ids": ["<eval>", ...],
  "max_items": 30,          // optional; none = every stored item
  "reason": "the arm's win rests on two items"
}
```

`split` is pinned to `train`, as for `remeasure`.

**Why an item and not a flag on `remeasure`.** The two do opposite
things. `remeasure` fills the items with no stored row and is cheap when
the run config is almost complete. `repeat` measures the items that have a
stored row and costs about one measurement of them. They have different
refusals, a different cost rule and a different result. A flag would change
what `remeasure` means for a planner that does not read the flag.

## 3. How a repeat gets fresh drives and fresh judgments

Kiln stores one result for each (eval config, run config, item) and uses it
again. So code uses the re-drive of the noise check (`noise.py`): it creates
a clone of the run config (the same properties, a new id, named
`REPEAT <n> of <rc>`, provenance derived from the run config) and runs each
named eval's default config on the clone, narrowed to the sample
(`Analyzer.judge_items`). The clone has no stored row, so each sampled item
is driven and judged again. The run config itself is never edited.

There is no re-judge pass (a copy of the judge on the stored outputs). The
noise check measures judge noise alone. A repeat answers one question: does
the same run config give the same result again.

## 4. The sample

For each eval: the items that the run config has a stored train row for,
with a value on every comparable score. `max_items` takes the first N by a
hash of the item id (`noise.sample_items`). So:

- an arm and the baseline with the same stored items get the same sample;
- the sample of `--noise-check N` is the same N items.

Each row carries `items_key`, a short hash of the items it compared. Two
rows with the same key compared the same items.

## 5. What comes back

One `RepeatRow` per (eval, score), compared item by item with the stored
rows: `items`, `changed`, `improved` and `worsened` (in the score's good
direction), `mean_abs_change` (rating scales), and the raw mean of each
measurement on the compared items (`stored_score`, `repeat_score`).
`repeat_index` counts the repeats of the same (run config, eval) in the
run. Each repeat is compared with the stored rows, never with an earlier
repeat.

**Decision: a count against the stored rows, not a second set of rows for
the comparisons.** No comparison, statistic, stop rule or promotion reads a
clone's rows. The bundle and the val gate compare the first measurement of
each side, as before. Reason: a second set of rows in the paired statistics
would change what `p_value` and the CI mean, and every skill that reads
them. A rule that pools repeats is the next project (section 10).

## 6. Where the repeats are stored

- In Kiln: the rows on the clone, like any eval run.
- In the wave log: the `repeat` event holds the whole `RepeatResult`, with
  the clone's id; `repeat_estimate`, `repeat_clone`, `repeat_row` and
  `repeat_note` events carry the lines.
- In the run: `Ctx.repeat_results` and `Ctx.train_repeats`.
- In the status fold: `RunStatus.repeats`, for the report.
- Not in memory. A later run does not read them.

## 7. Who reads them

- Every later planner: `BatchPlanInput.train_repeats`, all the rows of the
  run, oldest first.
- An analyst: `AnalysisInput.train_repeats`, the rows on its arm's run
  config, its parent and the baseline, for the evals of its bundle.
  Evidence about noise beside the table. The analyzer's skill says to use
  it to grade caution, and never to pass or fail `successful` on it alone.
- The report: code appends "Repeat measurements (code-computed)" after the
  agent's markdown.
- The terminal: a plain line per repeat, the item's finding cell, and a
  `repeat` card and panel in the terminal app.

## 8. What a repeat does not change

**Decision: a repeat of the baseline does not update `train_noise` or the
flip rate of the stop rules.** Only `--noise-check` sets them. Reason: the
noise check is a fixed sample of the first baseline, taken before any
choice. A repeat's sample and its run config are the planner's choice, made
after it saw a result. If a planner's repeat could lower the flip rate, the
planner could loosen the stop rules that judge its own arms.

## 9. Limits and cost

- **Cost.** About one measurement of the sampled items: each item driven
  once, judged once per eval. No build, no agent session. Before any spend,
  one line gives an estimate from the run config's own stored rows
  (`noise.estimate_usd`).
- **Cap.** The estimate must fit `budget.hard_max_usd`: the most one
  candidate may take (min of $60 and 25% of the remaining run budget). When
  it does not, the item ends `capped` (inconclusive, not a failure),
  nothing is spent, and the reason gives the `max_items` that fits. While
  it runs, the item holds its estimate in `BudgetHolds`. The spend is
  booked as source `repeat`, in the provider lane, and counts against
  `--budget-usd`.
- **In one plan.** At most 4 repeat items (`MAX_REPEATS_PER_PLAN`), never
  two on the same (run config, eval). None in a probe phase (the existing
  rule: a probe-phase plan carries only probes).
- **Refused, before any spend** (`failed_infra`, with the reason):
  - a run config the task does not have;
  - a run config outside the model policy;
  - a run config with no stored train row on any named eval (a `remeasure`
    comes first);
  - a run config that code made to measure or to probe: a `REPEAT …` or
    `NOISE …` clone, or a `PROBE …` fork.
- **A clone is never a parent.** The same rule holds for an experiment: an
  experiment whose `parent_rc_id` or `existing_rc_id` names a run config
  that code made to measure or to probe fails before any session and
  before its experiment record opens. This also closes the same gap for the
  noise check's clone, which had no such guard.

## 10. When a repeat on train is worth its cost

The val gate is already a repeat, on new items. A train repeat is worth its
cost when a decision rests on a small train difference, a wrong call is
costly, and the gate cannot settle it:

- a dead end that would retire a lever;
- the choice of the parent of the next waves;
- a small val split, or a decision that never goes to the gate (a guard
  regression, a planner's own ordering).

The playbook `dealing-with-a-noisy-eval` names this as step 3: plan a
`repeat` on the arm and one on the baseline, same evals and same
`max_items`. Its "What not to do yet" keeps the rule that `remeasure` is
not a repeat, and adds: do not repeat every arm. Its Budget section says to
keep a repeat small with `max_items`, with the binomial arithmetic for the
size, and that a repeat of the baseline early in the run shows the noise
when `train_noise` is absent.

## 11. The seam for budget-aware planning

The backlog entry "Budget-aware planning" wants a gate that uses support
across repeats. It is not built here. Its start:

- `repeats.repeat_rows(ctx, rc_id, eval_id)`: each repeat of a (run
  config, eval) this run, oldest first, as item id → the repeat's stored
  row. The first measurement is the run config's own stored rows.
- `repeats.repeat_clones(ctx, rc_id, eval_id)`: the clones that hold them.
- `RepeatRow.items_key`: which rows compared the same items.

## 12. Decisions for the owner

Each takes the conservative choice. Each can change later without a change
to the item's shape.

1. A repeat does not update `train_noise` or the stop rules' flip rate
   (section 8).
2. No comparison or gate pools a repeat's rows (section 5).
3. No re-judge pass (section 3).
4. Not written to memory (section 6).
5. The cap is the hard maximum for one candidate; there is no separate
   planner `max_usd` on a repeat (section 9).
6. At most 4 repeats in one plan (section 9).
7. The playbook change: a new step 3, and the following steps renumbered
   (section 10). A change to a playbook's order needs the owners.
