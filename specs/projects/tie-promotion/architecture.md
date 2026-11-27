---
status: complete
---

# Architecture: Promotion on a tie

The functional spec is [functional_spec.md](functional_spec.md). This
document says where each part is in the code.

## 1. Overview

```
build session ──technique_id──▶ items._keep_arm ──▶ ctx.tie_stances[rc_id]
                                     │                       │
                 knowledge skill ────┘ (tie.resolve_stance)  │
                                                             ▼
train side:   keep arm ──▶ stop rules without futility/residual
                       ──▶ AnalysisInput.tie_rule
                       ──▶ tie.train_verdict (when the gain rule says no)
                                                             │
gate:   val tables ──▶ evidence step (code rule or promote_best)
                              │ no winner
                              ▼
                       orchestrator._settle_tie
                         1. tie.quality_reasons   (val table, train noise)
                         2. _speed_val_pass       (paired drives, passers only)
                            tie.metric_reasons    (cost, latency)
                         3. tie.pick_winner
                              │
                              ▼
                PromotionDecision(grounds="design") ──▶ _apply_checked
```

One new module, `src/kiln_harness_tuner/tie.py`, holds the pure parts. The
orchestrator and the item flow call it. No gateway operation changes.

## 2. `tie.py`

Constants:

| Name | Value | Meaning |
|---|---|---|
| `MAX_TIE_PROMOTIONS` | 1 | Promotions on a tie in one run |
| `TIE_RATIO_BAND` | 0.03 | The largest median ratio above 1 that is "not worse" |
| `WORSE_ALPHA` | 0.05 | The alpha of the "clearly worse" sign test |
| `GAIN_RULE_KINDS` | `futility`, `residual` | The default stop rules that a keep arm does not get |

Functions:

- `agreed_stance(base, planned_id, built_id, lever)`. No stance unless the
  plan and the build name the same id; then `resolve_stance`.
- `resolve_stance(base, technique_id, lever) -> TechniqueStance`. Reads the
  technique document from a loaded `KnowledgeBase`. The four conditions of
  functional spec section 3, rules 2 to 5. The phrase is matched with
  `knowledge.schema.EVAL_STANCES`, the tuple that the knowledge check uses,
  so the check and the gate cannot disagree.
- `knowledge_of(ctx)`. Loads the knowledge skill of the installed package
  once (`knowledge.load()`), and keeps it on `ctx.knowledge_base`. A load
  failure gives `None`: no build then has a stance.
- `noise_per_item(noise_rows, eval_id, score_key)` and
  `row_allowance(row, noise_rows)`. The noise comes from the `NoiseRow` of
  the same (eval, score): `redrive_changed / items` for a pass/fail score,
  `redrive_mean_abs_change / (high - low)` for a rating. No row gives an
  allowance of 0.
- `rows_reasons(rows, weights, noise_rows)`. Rules 2 to 5 of functional
  spec 5.1, on any rows. The train rule and the val test both use it.
- `quality_reasons(table, weights, noise_rows)`. Rule 1 on the table, then
  `rows_reasons`.
- `clearly_worse(comparison)` and `metric_reasons(name, comparison)`. The
  test of functional spec 5.2 on one `LatencyComparison`. It accepts only
  `method: paired_drive`.
- `keep_arm_rules(defaults)` and `train_verdict(item, rows, floor, weights,
  noise_rows)`. The train side of functional spec section 4.
- `tie_rule_text(technique_id)`. The text for the analyzer. It has no
  number from the run in it.
- `pick_winner(tables, weights)` and `rationale(...)`.

`tie.py` imports from `analysis`, `knowledge` and `schemas`. It does not
import the orchestrator.

## 3. Schemas (`schemas.py`)

| Model | Field | Set by |
|---|---|---|
| `FixWork` | `technique_id: str \| None` | The planner |
| `RcBuildResult` | `technique_id: str \| None` | The builder |
| `AnalysisInput` | `tie_rule: str \| None` | Code, for a keep arm |
| `TieCheck` (new) | `technique_id`, `stance`, `note`, `not_worse`, `reasons` | Code |
| `CandidateValTable` | `tie: TieCheck \| None` | Code |
| `PromotionDecision` | `grounds: "evidence" \| "design"`, `technique_id` | Code only |

Each new field has a default, so an older wave log and an older agent
template still parse.

`PromotionDecision` is also the answer of the promotion agent. The agent can
write the two new fields. `_run_promotion_gate` sets them back to
`evidence` and `None` on every decision that an agent returns.

## 4. The train side (`items.py`)

- `_keep_arm(ctx, item, built, reused=...)` runs after the build review, on
  the build that is measured. For a new build it calls `agreed_stance`
  (the plan's `FixWork.technique_id` must equal the build's id, then
  `resolve_stance`),
  stores the result in `ctx.tie_stances[rc_id]` and logs `technique_named`.
  For a reused run config it reads the stored stance. It returns the stance
  only for a keep arm (functional spec section 4).
- `_measure_arm(..., keep=...)` passes the default rules through
  `keep_arm_rules` before `merge_rules`. So a rule that the planner wrote
  for the arm still applies.
- `AnalysisInput.tie_rule` is set for a keep arm.
- After the analysis, when the gain rule (`success_by_rule`) says no for a
  keep arm, code runs `train_verdict`. When it passes, it is the code rule
  of the arm. The event `tie_train_rule` records both. The event
  `success_rule` gets the field `on_tie`.

Under speed and cost `_keep_arm` returns `None` before it reads the build.

## 5. The gate (`orchestrator.py`)

`Ctx` gets three fields: `tie_stances`, `tie_promotions` and
`knowledge_base`.

- `_mark_tie_candidates` runs after `_assemble_val_tables`. It copies each
  candidate's stance to `table.tie`. The promotion agent then sees it. It
  skips a merge and the speed and cost objectives.
- `_settle_tie(ctx, candidates, tables, baseline_rc_id, decision)` is the
  tie step. `_run_promotion_gate` calls it at two places, each before the
  `promotion` event, so one gate still emits one `promotion` event:
  1. after the code rule of a one-candidate gate gave a decision;
  2. after the promotion agent gave a decision.
  It returns the decision that it was given when that decision has a
  winner or a `blocked_on`, when the objective is speed or cost, or when no
  table has the stance `keep`.
- The paired pass is `_speed_val_pass`, with only the tables that passed
  the quality test. It puts the comparisons on `table.latency` and
  `table.cost`, and logs the spend as `latency_drives`.
- After the test, `_settle_tie` writes the val summary again and emits
  `gate_val_tables` again, so the val readers and the wave log have the
  final tables.
- The tie decision goes through `_apply_checked`, as each promotion does.
- `_apply_promotion`: for `grounds == "design"` it adds one to
  `ctx.tie_promotions` after the baseline pointer moved, and it writes no
  `FIXED:` (the landed issues are an empty list). The `regressed_issues`
  writes do not change.

The speed and cost path of the gate returns before both call sites. The
path where code refuses the agent's winner returns `None` after the tie
step did not run (the decision had a winner).

## 6. Displays and the report

- `ui.py`: `tie_check` and `tie_promotion` are in `SETUP_LINE_EVENTS`, so
  both displays print their `line`.
- `tui/model/fold.py` and `log.py`: a promoted item whose decision has
  `grounds == "design"` gets the line of `log.promoted_on_tie`.
- `report.py`: the fallback report marks the promotion line.
- `RunStatus` needs no new field. `WaveStatus.promotion` is the decision,
  with `grounds` and `technique_id`.

## 7. Agent text

| File | Change |
|---|---|
| `kiln-harness-tuner-experiment-build/SKILL.md` | Return `technique_id` |
| `agents/create_experiment_rc.md` | Name the field |
| `kiln-harness-tuner-experiment-analysis/SKILL.md` | The `tie_rule` input |
| `kiln-harness-tuner-promote/SKILL.md` | The tie step is code's; `tie` on a table is information; never set `grounds` |
| `kiln-harness-tuner-report/SKILL.md` | How to label a promotion on design grounds |
| `kiln-harness-tuner-batch-selection/SKILL.md` | A `Keep it.` arm can be a promotion candidate with no predicted gain |
| `kiln-harness-tuner-knowledge/SKILL.md` | The section of a technique now changes a promotion |

None of this text has a number from a target.

`.agents/skills/knowledge-maintenance/SKILL.md` gets one rule: a technique
that starts its section with `Keep it.`, or changes to it, needs the
agreement of the owners, as a playbook does. Reason: the phrase is now an
input of the promotion gate, and agents help to write those documents.

## 8. Docs

The passages that say "promoted only when held-out data confirms that it is
better" change: `AGENTS.md`, `docs/how-it-works.md`,
`docs/concepts.md`, `docs/limitations.md` and `docs/architecture.md`.
`docs/limitations.md` also gets the limits of the tie rule: what "not worse"
cannot rule out, and the limit of one for each run.

## 9. Tests (`tests/test_tie_promotion.py`)

Offline, with the fakes of the suite.

- The stance: each phrase, an unknown id, a dormant document, a lever that
  does not match, no id.
- The noise allowance: no noise row gives 0; a pass/fail row; a rating row;
  the cap of the speed gate.
- `rows_reasons` and `quality_reasons`: a tie, a small loss, a loss inside
  the allowance with and without a pooled net of zero, a significant drop,
  a hole, a skipped eval, an integrity flag.
- `metric_reasons`: not measured, incomplete, clearly worse, over the band,
  inside the band.
- The gate, through `maybe_promote`: a tie with `Keep it.` is promoted with
  `grounds: design`; a tie with each other phrase and with no stance is not;
  a small loss is not; a candidate with a val hole is not promoted and not
  stamped; a worse cost is not; the limit; a merge; the speed objective; the
  agent path; an agent that sets `grounds: design`.
- The apply: no `FIXED:` on a tie; the `val` record line; the count.
- The train side: a keep arm with no gain reaches the gate; the same arm
  with no stance does not; the stop rules; the analyzer input.
- The report, the plain display and the terminal app mark the promotion.

## 10. Other open pull requests

- **#46 (val holes stay pooled)** changes `_run_promotion_gate` and
  `_record_rejections`. The tie step never promotes or stamps a holed
  candidate, so the two agree. The textual conflict is at the two call
  sites of `_settle_tie`, each a few lines.
- **#45 (the pass line)** changes `analysis.py`. `tie.py` reads
  `row_net_items` and the flip counts of a row, which #45 does not change.

## 11. Later changes

- **`It depends.` with conditions that code can read.** A technique could
  state its conditions in front matter, for example
  `keep_when: [cost_falls, latency_falls]`. Code would then promote on a tie
  when the paired pass shows a clear gain on a named metric. This is a
  change to the schema of the knowledge documents and to many documents.
- **A merge whose ingredients all say `Keep it.`** could take that stance.
- **A limit above one**, with the floor of functional spec section 8.
- **A stance that survives a run.** A line on the experiment record with
  the technique id would let a later run reuse the run config with its
  stance.
- **The stretch goal**, functional spec section 12.

## 12. Phases

One phase: the module, the wiring, the agent text, the docs and the tests.
