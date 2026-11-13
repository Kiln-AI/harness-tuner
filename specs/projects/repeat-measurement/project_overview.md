---
status: draft
---

# A repeat measurement the planner can ask for

**Status of this document.** A short record of the problem and the idea, so
that the project is easy to start later. It came from the deep code review
of the biological_bootloader branch on 2026-11-13: see
[review_finding.md](review_finding.md). It is not started.

## 1. The problem

The playbook `dealing-with-a-noisy-eval` tells the planner to measure an arm
and the baseline again on the same items before it trusts a small
difference. The planner cannot do this today:

- An `experiment` item builds a new run config and measures it.
- A `remeasure` item completes the measurement of an existing run config.
  Kiln stores one result for each eval config, run config and item, and
  uses it again. So a remeasure of a run config that is fully measured
  drives nothing and gives the same rows.
- The only repeat in the loop is `--noise-check N`. The user sets it at the
  start. It runs one time, before wave 1, on the baseline.

A planner that follows the playbook reads the same rows as a confirmation,
or builds a duplicate run config, which its skill forbids.

## 2. The idea

Give the planner a way to ask for a new measurement of an existing run
config on a set of train items. The functional spec chooses the shape:

1. A new item type, for example `repeat`, with the run config, the evals and
   the items or a sample size.
2. A flag on `remeasure`, for example `fresh: true`.

The code can use the method of `noise.py` again: a clone of the run config
with a new id and no stored rows, so that each item is driven and judged
again. The clone never becomes a parent, a candidate or the baseline.

## 3. Questions for the spec

- What comes back: a count of the items that changed against the stored
  rows, as the noise check gives, or a second set of rows that the
  comparisons use together with the first?
- Does a repeat of the baseline update `train_noise` and the flip rate of
  the stop rules?
- Limits: the items in one repeat, the repeats in one wave, and the cost
  estimate before the spend.
- The split stays train only, as for `remeasure`. The val gate is already a
  repeat on new items: when is a repeat on train worth its cost?
- The changes to the planner's skill and to the playbook.

## 4. Until this project is done

The playbook must give only steps that the loop can do. Read `train_noise`
when it is there. Else, the noise is not known, and the stop rules use a
flip rate of 10%. Predict a larger effect, or choose an eval whose
detectability can show the effect. Use the val gate as the repeat. This is
a change to the knowledge content, not to code.
