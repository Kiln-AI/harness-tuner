# Review finding: the noisy-eval playbook asks for a repeat the loop cannot do

From the deep code review of the biological_bootloader branch, 2026-11-13,
phase 6 (playbooks), critical finding 1. Copied as the reviewer wrote it.
The paths are relative to
`src/kiln_harness_tuner/agent_files/skills/kiln-harness-tuner-knowledge/references/playbooks/`.

- [dealing-with-a-noisy-eval.md:28-31, :54-56] **The main remedy is a repeat that the loop cannot do**
  Step 1 says "Repeat before you trust a small difference: a remeasure of the
  arm and of the baseline on the same items". The Budget section says "a
  repeat of the baseline early in the run shows the noise". The planner has
  no item that does this. `RemeasureItem` (`schemas.py:534-547`, and
  batch-selection SKILL.md:920-929) is a coverage top-up. "Kiln's
  item-grained cache re-drives only the items that never stored a row". So a
  remeasure of a fully measured rc changes nothing. The baseline side of
  every comparison is its stored rows (`docs/how-it-works.md` section 3).
  The only repeat in the loop is `--noise-check N`. The user sets it at
  start-up, not the planner. A planner that follows step 1 does one of two
  things. It plans a `remeasure` that returns the same rows and thinks the
  difference is confirmed. Or it builds a duplicate rc to get a fresh run,
  which the planner's skill forbids ("Never rebuild a duplicate rc just to
  re-run its evals"). The playbook's first and most important step is
  therefore wrong for this loop.
  Fix: replace step 1 with what the loop actually offers. Read
  `train_noise` when it is present. When it is absent, say that the noise is
  unknown and that the stop rules assume a 10% flip rate. Predict a larger
  effect, or choose an eval whose `train_detectability` can carry the
  question. Rely on the val gate as the repeat on new items. Note the
  `--noise-check` flag for the report, not as a planner action. Remove the
  "repeat of the baseline" sentence from the Budget section.
