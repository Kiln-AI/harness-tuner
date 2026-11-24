---
status: complete
---

# Functional spec: Promotion on a tie

## 1. Purpose

A candidate becomes the baseline only when held-out data shows that it is
better. Some changes are a better design and move no eval: a smaller tool
list, a harness with one group removed, the same model from a cheaper
provider. The knowledge system records this for each technique, in the
section `## If it does not move the evals`. That section starts with one of
three phrases: `Keep it.`, `It must move an eval.` or `It depends.`.

This project lets that guidance settle a tie at the promotion gate.

**The rule: knowledge settles a tie, never a loss.** When the gate finds no
winner on the evidence, a candidate whose technique says `Keep it.` is
promoted when code shows that it is not worse than the baseline on any eval,
on cost and on latency.

The first rule of the gate does not change. A candidate that is worse on
held-out data is not promoted.

## 2. Scope

In:

- How code learns which technique document an experiment used (section 3).
- How an arm with no gain on train reaches the gate (section 4).
- The test for "not worse" at the gate, on each eval, on cost and on
  latency (section 5).
- When the test runs, and the choice among several candidates (section 6).
- What each phrase does (section 7).
- The limit for one run (section 8).
- Merges (section 9).
- How the run marks a promotion on a tie (section 10).
- What crosses from val to the train side (section 11).

Out:

- **The speed and cost objectives.** Under them a winner is clearly faster
  or cheaper by definition. The tie rule is off.
- **A difference inside the variance** (the stretch goal). It is designed in
  section 12 and not built.
- A change to the phrase of any technique document.

## 3. The technique of an experiment

The `technique` field of an experiment names a lever (`prompt`,
`tool_config`), not a technique document. So two agents name the document,
at two times:

- `FixWork` has a new field `technique_id`: the id of the technique document
  of the knowledge skill that the plan applies. The id is the file name
  without `.md`. The planner sets it before any build or result exists. The
  value is `null` when the idea is in no document.
- `RcBuildResult` has a new field `technique_id`: the id of the document
  that the build followed. The builder returns the id of the plan when it
  built that technique, and another id or `null` when it did not.
- Code reads the document from the knowledge skill of the installed
  package. No agent passes the phrase.

Code gives a build a stance only when all of these are true:

1. The plan and the build name the same id.
2. The id names a technique document, and the document parsed.
3. The document has `status: active`.
4. The `levers` of the document name the lever of the experiment.
5. The section starts with one of the three phrases.

Rule 1 means that one agent alone cannot choose a `Keep it.` technique
after it sees what the change does. Rules 1 and 4 cannot prove that the
change follows the technique: a build can still name the technique of its
plan and make another change. The integrity checks and the val test are the
checks after that.

Any other case is "no stance", with the reason in the wave log (event
`technique_named`).

**An idea that is in no document** has no stance. The gate treats it as
today: it must move an eval. The same is true for a document that is
`dormant`, and for a document whose levers do not match.

**A reused run config** (`FixWork.existing_rc_id`) has no build session. It
keeps the stance that its build gave it in this run. When a prior run built
it, it has no stance.

## 4. How a tie reaches the gate

Today an arm with no gain on train never reaches the gate. Three train-side
rules stop it. For a **keep arm**, each one changes.

A keep arm is an experiment for which all of these are true:

- the objective is quality;
- the item is a fix item (not a merge) and a promotion candidate;
- its build has the stance `Keep it.`;
- the run has not used its promotions on a tie (section 8).

For a keep arm:

1. **Stop rules.** Code leaves out the two default rules that demand a
   gain: `futility` and `residual`. The rules that stop a worse arm stay
   (`eval_metric`, `invalid_output_rate`, the budgets). A `targets` rule
   stays, because the planner asked for it with `target_items`. A rule that
   the planner wrote for the arm stays.
2. **The train rule.** `successful` needs the code rule. For a keep arm the
   code rule is: the gain rule of today, or the tie rule. The tie rule is:
   the primary target was measured, each row is at the coverage floor, and
   the train rows are not worse on quality (section 5.1, on the train rows).
3. **The analyzer.** Its input has a new field `tie_rule`, with the rule in
   words. The analyzer does not veto the arm because it shows no gain. It
   can veto for any other reason. `successful` stays the analyzer's answer
   AND the code rule.

An arm that is not a keep arm is held to a gain, as today.

## 5. The test for "not worse"

Code runs the test. The promotion agent does not judge it.

### 5.1 On each eval

The test reads the candidate's val table: one row for each eval score,
candidate against the current baseline, on the same items. Every row counts,
a target or a guard. All of these must be true:

1. **The table is whole and clean.** It has no hole (`ineligible` is
   false). The gate left out no eval (`skipped` is empty). The integrity
   checks ran, and the table has no integrity flag.
2. **Each row can be pooled.** `analysis.row_net_items` gives its net change
   in items: for a pass/fail score, `fail_to_pass - pass_to_fail`; for a
   rating, the change of the normalized mean times the items.
3. **No row is down significantly** (`analysis.regressed_significantly`:
   the row fell, and the paired test is significant or the confidence
   interval is below zero).
4. **No row loses more than its noise allowance.** The allowance of a row,
   in net items, is `min(T, round_half_up(noise * n))`, where:
   - `n` is the number of items of the row;
   - `noise` is the noise that the run measured on this score with
     `--noise-check`: the share of the sampled items whose pass/fail
     outcome changed between two runs of the baseline. For a rating it is
     the mean absolute change, as a fraction of the scale;
   - `T` is the allowance of the speed gate,
     `max(1, round_half_up(0.10 * n))`.

   **When the run measured no noise on a score, the allowance is 0.** The
   run gives no allowance for noise that it did not measure. So without
   `--noise-check`, no row can be below the baseline.
5. **The pooled net is at least zero.** The net items of every row, each
   eval weighted by its priority (p0 = 4, p1 = 3, p2 = 2, p3 = 1, none = 1),
   add up to zero or more.

Rule 5 is the tie: over all the compared items, the candidate fixed at
least as many as it broke. Rule 4 lets one eval move inside the noise.
Rule 3 stops a real loss that rule 4 could let through on a large eval.

Examples, each with one pass/fail eval of 20 val items:

| Run | Row | Result |
|---|---|---|
| No noise check | 0 fixed, 0 broke | Not worse |
| No noise check | 2 fixed, 2 broke | Not worse (net 0) |
| No noise check | 1 fixed, 2 broke | Worse: the row lost 1, allowance 0 |
| Noise 10% | 1 fixed, 2 broke, one eval | Worse: the pooled net is -1 |
| Noise 10%, two evals | -1 on one, +1 on the other | Not worse |
| Noise 10%, two evals | -3 on one, +3 on the other | Worse: -3 is over the allowance of 2 |

### 5.2 On cost and on latency

Under the quality objective the gate has only the cost and the latency of
the stored eval runs. The two sides can come from different times, so that
comparison is information and not a verdict.

So the test uses the paired drive pass of the speed objective: code drives
the baseline and the candidate on the same fixed sample of val inputs (up to
30), back to back, in the same window. No judge runs. The pass gives one
paired comparison for latency and one for the cost of each request.

Code pays for the pass only for a candidate that passed 5.1. The spend is
in the report as `latency_drives`.

For each of the two metrics, all of these must be true:

1. The comparison was measured, and it is complete (at least two thirds of
   the planned pairs).
2. It is not clearly worse: not (more items worse than better, with an
   exact sign test below 0.05 and a median ratio above 1), and no
   significant p90 regression.
3. Its median ratio (candidate / baseline, per item) is at most 1.03. This
   is the width that the speed gate uses for "equally fast".

A task with no drivable val input has no paired pass. Then cost and latency are not shown as not worse, and the
candidate is not promoted on a tie.

## 6. When the test runs

The tie step is the last step of the gate. It runs only when all of these
are true:

- the objective is quality;
- the evidence step chose **no winner**. The evidence step is the code rule
  of a one-candidate gate, or the promotion agent;
- the agent did not hold the gate on an open constraint (`blocked_on`);
- at least one candidate has the stance `Keep it.`;
- the run has not used its promotions on a tie.

So evidence comes first. A candidate with a proven gain wins as today. When
the agent fails to answer (`promotion_skipped`), no tie step runs.

When several candidates pass the test, code promotes one: the highest
pooled net, then the lowest cost ratio, then the lowest latency ratio, then
the rc id. The others are passed over, as at any gate with a winner.

A candidate that fails the test is recorded as the evidence step decided:
it is stamped `— REJECTED AT VAL` with the reason of its own val table (for
example "no targeted gain", or an eval that dropped). Only a candidate that
also passed the tie test is "passed over". A candidate with val holes is
not judged, as today.

The code checks that apply to every promotion still apply
(`_apply_checked`): the integrity checks ran, and nothing blocked the
winner.

## 7. What each phrase does

| Phrase | At a tie |
|---|---|
| `Keep it.` | The candidate can be promoted, when the test of section 5 passes. |
| `It must move an eval.` | No promotion. |
| `It depends.` | No promotion. |
| No stance (section 3) | No promotion. |

`It depends.` gives conditions in prose ("keep it when the cost per item
falls and no eval falls"). Code cannot read prose, and no agent judges a
tie. So the conservative choice is no promotion. Section 11 of the
architecture gives a way to change this: a technique states its
conditions in front matter.

## 8. The limit

A "not worse" test on about 150 items cannot rule out a loss of a few
points. A chain of ties could add such losses with no eval evidence.

**One run makes at most one promotion on a tie**
(`tie.MAX_TIE_PROMOTIONS = 1`). With one, no chain exists inside a run.

After the run used it:

- no later arm is a keep arm (section 4), so the run pays no val pass for
  an arm that cannot win;
- the tie step does not run. A `Keep it.` candidate at the gate gets the
  reason "the run already made 1 promotion(s) on a tie".

A promotion on evidence after the tie does not give the run a new one.

**A higher limit needs a floor.** This is not built. The design: code keeps
an anchor, the baseline of the last promotion on evidence in the run, or
the first baseline of the run when there is none. A tie candidate must pass
the quality test of 5.1 against the anchor too. Its val rows are stored, so
this comparison costs no drive. Then each tie is compared with a baseline
that evidence supports, and small losses cannot add up below it.

## 9. Merges

A merge names no technique, so a merge has no stance. **A merge is never
promoted on a tie.** It needs a gain on val, as today.

A keep arm that was not promoted keeps its train verdict. The planner can
use it as an ingredient of a merge. That merge is then judged on evidence.

## 10. How the run marks a promotion on a tie

A reader must see that no eval gain stands behind the promotion.

- **The decision.** `PromotionDecision` has two new fields, set by code
  only: `grounds` (`evidence` or `design`) and `technique_id`. Code sets a
  decision that an agent returns back to `evidence`.
- **The `val` record.** The line of the promotion starts with
  `design grounds (tie): technique <id> says 'Keep it.'; val shows no gain
  and no loss`, then the summary numbers: the pooled net, the cost ratio,
  the latency ratio and the rows.
- **The wave log.** `tie_check` for each tested candidate, with the result
  and the reasons. `tie_promotion` for the promotion, with the rationale of
  the evidence step. The `promotion` event carries the decision.
- **The report.** The Promotions section labels the promotion "on design
  grounds (a tie on val)", names the technique, and says that the val
  numbers show no gain. The Outcome section does not count it as an
  improvement of quality. The fallback report prints the same mark.
- **The terminal app** shows "promoted on a tie".
- **The issue of the experiment is not closed.** A promotion on evidence
  writes `FIXED:` on the issue that the fix landed. On a tie, val confirmed
  no fix, so code writes no `FIXED:`. A row that slipped inside its
  allowance still flags its issue, as today.

The stamp on the experiment record does not change: `— PROMOTED <MM-DD>`.
The baseline record does not change: it is metric-free.

## 11. What crosses from val to the train side

Val stays read only at the gate. The planner, the builder and the analyzer
get nothing that is derived from val:

- the stance comes from the knowledge skill and from the build;
- `tie_rule` is fixed text with the technique id;
- the train rule reads train rows and the train noise check.

One fact can be inferred on the train side, at most once in a run: that a
promotion was made on a tie. A planner can see a promoted experiment whose
issue is not `FIXED:`. After the limit (section 8), a later `Keep it.` arm
gets no `tie_rule`, which says the same thing. This is one bit: val showed
no gain and no loss. The
alternative, a false `FIXED:`, hides an open issue from every later
planner. The `regressed` flags on issues already carry one val bit for each
issue in the same way.

## 12. The stretch goal: a difference inside the variance

Not built. The idea: a regression of half a point, for a large improvement
of the design, is noise. The rule extends to a candidate whose pooled net is
below zero and inside the variance of the measurement.

What it needs:

1. **A variance on val.** The noise check runs on train, on the first
   baseline. The gate would need the spread of the pooled net of two runs
   of one config on the val items. That is a second val pass of the
   baseline (a clone, as the noise check makes), at each gate or once for
   each baseline.
2. **A bound.** For example: the pooled net is at least `-z * sd`, with `sd`
   the standard deviation of the pooled net under noise, and `z` fixed in
   code.
3. **A stronger limit.** A rule that accepts a small measured loss needs
   the floor of section 8, also at a limit of one.

The first part is new val spend at each gate. So it is a decision for the
owners, and an entry in the backlog.

## 13. Edge cases

| Case | Result |
|---|---|
| The builder names a document that does not exist | No stance; `technique_named` gives the reason |
| The plan and the build name different documents, or the plan names none | No stance |
| The builder names a `Keep it.` document for another lever | No stance |
| A keep arm improves its target on train | It passes the gain rule, as today |
| A keep arm shows a proven gain on val | Promoted on evidence, `grounds: evidence` |
| A keep arm shows a nominal gain on val, and the agent says no winner | The tie step runs |
| The agent names a holed or flagged winner, and code refuses it | No tie step: the gate ends with no promotion, as today |
| A keep candidate has a val hole | Not promoted and not rejected by the tie step |
| A keep candidate has an integrity flag | Not promoted on a tie |
| Two keep candidates pass | One is promoted (section 6); the other is passed over |
| The val pass has an eval that the gate cannot compare | No promotion on a tie |
| The agent sets `grounds: design` | Code sets it back to `evidence` |
