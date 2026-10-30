# The merging reference

Loaded by **kiln-harness-tuner-experiment-build** for `MergeWork` items (construction) and by
**kiln-harness-tuner-experiment-analysis** for merge items (attribution reading, partial-win
framing). A merge is an ordinary experiment with `MergeWork` construction —
same record conventions, same pipeline; only this guidance is merge-specific.

## Construction: apply the ingredients to the baseline, manually

**Not diff-based.** You — one LLM step — read the ingredient run-configs, how
each differs from its parent, their issue descriptions, and their
targeted-eval findings (the experiment memories), and *write* the combined
run-config against the baseline by hand. Diffs are a reading tool, never a
merge mechanism. Inputs per ingredient (`wins: [{experiment_memory_id,
rc_id}]`): the experiment memory (hypothesis, verdict, eval id, parent rc)
and the rc artifact itself.

- **Layer the run-config** — tool set / skill set / prompt sections /
  sub-agent wiring. Cross-layer fixes compose near-mechanically. Same-layer
  fixes (two edits to the same prompt section, semantically overlapping
  tools) get extra scrutiny during application and are the prior suspects on
  regression. Scrutiny includes modality, not just nouns: a
  same-nouns-opposite-modality pair ("quote X verbatim" vs "do not
  volunteer X") is a **conflict**, never "redundant" — flag it in
  `build_notes` even when the planner's `conflict_flags`/cohesion notes
  call the pair compatible.
- **Conflict flags are blame priors.** The planner may pass
  `conflict_flags` (pairs editing the same layer/section); verify them while
  building and note in `build_notes` any new overlaps you find — zero eval
  cost now, cheap attribution later.
- **Never combine two rcs from the same `head_to_head_group`** (the
  group is recorded in the experiment memories' content; the field exists on
  both FixWork and MergeWork, so a merge arm itself can be an exclusive arm
  of a group). They were declared mutually exclusive at planning; at most
  one of a group merges.
- Provenance: `derived_from_ids = [baseline rc + every ingredient rc]`,
  `notes` naming the ingredient experiments.
- **Reopened issues are rebuilt, never replayed**: if an ingredient's issue
  was reopened after a failed earlier merge, the next fix is built fresh
  against the new baseline with that merge's feedback as context — never a
  replay of the old change. (That rebuild is its own future fix item, not
  part of this merge.)

## Measurement order (code runs it; know the shape)

The merge item's `evaluation_target` lists the ingredients' targeted evals —
they run first, on train, for attribution: a fix that didn't survive
combination fails its own eval immediately, with its culprit label attached.
The full pass is the promotion stage's val pass, not the merge's scope.

## Attribution + partial-win framing (for the analyzer)

- **A merge is not required to land 100% of its ingredients.** Fixing 8/10
  makes a better baseline than we had — judge `successful` on the whole, and
  report per-ingredient landing as bookkeeping, not gating: fill
  `per_ingredient_outcomes` with one row per ingredient issue
  (`{issue_memory_id, landed, note}`). Promotion's `FIXED:` writes source
  from these rows — an ingredient you can't confirm landed must be
  `landed: false`.
- **Finding each ingredient's `issue_memory_id`**: `MergeWork.wins` carries
  only `{experiment_memory_id, rc_id}` — read each ingredient's experiment
  memory; its content cites the issue memory id (the analysis skill mandates
  it). Fallback for older records that don't: take the ingredient's eval id
  (also in its content) and join to the `issue` memory whose content carries
  the eval-link line `"- eval: <eval_id> (built <date>, reproduced)"`.
  No join at all → still emit the row with the best-evidence issue
  id you have, or omit that ingredient and say so in `findings` — never
  guess an id.
- **Culprit reading is cheap, not exhaustive**: an ingredient failing its own
  targeted eval on the merged config is the culprit; conflict flags are the
  priors for anything subtler. Finding the exact cause isn't worth much —
  eject, mark unlanded, move on (the partial-win policy).
- **One-crack forbids post-measurement surgery**: "the same merge minus the
  culprit" is a fresh experiment for a later wave, measured like any other.
  Say so in `findings`/`next:` rather than attempting a repair.
- Memory example (merge verdict — code writes these from your
  return): overview
  `"EXP 0723-w4-1 (merge): w1-1 + w2-3 — acc: +11pp, base=64, cand=75, n=40, p=.02 | cost↓: +0.8, base=4.1, cand=3.3, n=40, p=.07"`
  — the measurement row's exact shape is **not defined here**: it has one
  owner, the "Frozen format" block in
  [../../kiln-harness-tuner-experiment-analysis/SKILL.md](../../kiln-harness-tuner-experiment-analysis/SKILL.md),
  which this example only illustrates (multi-target `|` join, and the `↓`
  marker on the `lower_is_better` score whose absolutes fall while its
  normalized delta reads `+`). Content cites the merged rc id, per-ingredient
  rows, conflict notes, and a `next:` line ("re-fix mem_84 against the new
  baseline — its tool edit collided with w1-1's").

## Timing guidance (for the planner — see kiln-harness-tuner-batch-selection)

Merge when several stable wins have pooled; hold off while high-value
independent experiments remain; fix-only waves in a row are normal; merge
size scales with conflict risk, not count; always merge before a round ends.
