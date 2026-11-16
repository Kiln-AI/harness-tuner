---
status: draft
---

# Promotion on a tie

**Status of this document.** A record of the decisions and findings so far,
so that the project is easy to start later. It was split out of the
[biological_bootloader](../biological_bootloader/project_overview.md)
project on 2026-11-12, because the architecture step found it too large for
that project. It is not started.

## 1. The problem

Today a candidate becomes the new baseline only when held-out data shows
that it is better. That is not always best practice. A change to the design
of an agent can be a good idea and not be covered by the evals of the
project.

Two examples from the owner:

- A sub-agent is added for quality, and no eval moves. It is probably
  removed, unless it helps cost or speed.
- A very long prompt with 50 topics is converted to a skill, so that the
  agent reads only what it needs. No eval moves, and none is hurt. It is
  probably kept: it is a better design, and later optimisations may now be
  possible.

In the knowledge system of `biological_bootloader`, each technique states
what to do when it does not move the evals: "keep it, it is a better
design", "it must move an eval", or "it depends", with the conditions.
Until this project, that guidance cannot change a promotion.

## 2. Decisions so far

1. **Knowledge settles a tie, never a loss.** When validation shows that a
   candidate is not worse than the baseline on any eval, on cost or on
   latency, and its technique says "keep it", the candidate can be
   promoted.
2. **Code decides "not worse".** The promoter agent does not judge it.
3. **The report marks the promotion** as made on design grounds.
4. **Stretch goal: very close, inside the variance.** A regression of 0.5%
   for a large improvement to the architecture is noise. The rule extends
   to a candidate whose difference from the baseline is inside the measured
   variance.
5. **The first rule of the gate does not change.** A candidate that is
   worse on held-out data is not promoted.

## 3. What the investigation found

From the code on 2026-11-12. The gate change itself is small. Three things
outside the gate make the project medium, and large with the stretch goal
or with the speed and cost objectives.

**A tie never reaches the gate.**

- An experiment is a promotion candidate only when it is `successful` on
  train. The code half of that (`success_by_rule`, `analysis.py`) needs a
  positive delta on the primary target.
- The stop rules end a candidate with no gain before the gate: `futility`
  stops one whose best case is not above zero, and the default `residual`
  rule needs half of the failures fixed.
- So the train-side rules must change too, for a candidate whose technique
  says "keep it".

**No "not worse" test exists for cost and latency on val under the quality
objective.**

- The paired timing pass on val runs only under the speed and cost
  objectives.
- Under quality, the gate has only the cost and latency of the stored eval
  runs, which the code documents as information and not a verdict.
- A real test needs extra paid drives at each gate (about 30 for each arm
  and the baseline), or a more lenient rule.

**The stretch goal has no variance to measure against.**

- No variance is measured on val. The noise check is train-only, optional,
  and run once on the first baseline.
- The only tolerance at the gate is a fixed constant
  (`SPEED_ROW_LOSS_FRACTION`, 10% of the items of a row), used under speed
  and cost.

**Pieces that exist and can be reused.**

- `speed_quality_reasons` (`analysis.py`) is already a non-inferiority rule
  for quality with a noise allowance. It is used on val under speed and
  cost.
- `regressed_significantly` is the existing test for a real loss.
- Code holds the technique of each candidate at the gate, so it can pass
  the guidance to the promoter in a new input field. The promoter cannot
  read a skill file through its gateway operations.

**Other findings.**

- The speed and cost objectives need "clearly better" by definition. They
  need separate handling. Leave them out first.
- A changed stamp on the experiment record (for example
  `— PROMOTED (design)`) breaks the patterns that read the stamp, and it is
  one more bit that goes from val to the train-side agents. Keep the
  train-visible stamp as it is. Put the design mark in the val record, the
  events and the report only.
- A merge item carries no technique.

## 4. The cost to the guarantees

This project changes what the tool promises. Plan for it openly.

- About ten passages in the README, `docs/how-it-works.md`,
  `docs/concepts.md`, `docs/limitations.md`, `docs/architecture.md` and the
  promoter's skill say that a change is promoted only when held-out data
  confirms that it is better. Each one changes to "confirms that it is not
  worse".
- `docs/limitations.md` says that a scrub is not a score win and may never
  be promoted. A tie rule changes that.
- "Not worse" on about 150 items cannot rule out a loss of a few points.
  A chain of ties can move the baseline a long way with no eval evidence.
- The technique document becomes a lever on promotion, and agents help to
  write those documents.

## 5. A first shape for the change

Not approved. For the quality objective only.

1. A pure function, `not_worse_reasons`, that reuses
   `speed_quality_reasons` and adds a test for cost and latency.
2. A field on the promotion decision: grounds, "evidence" or "design".
3. A field on the candidate's val table, or on the promoter's input, with
   the technique's guidance.
4. A branch at the gate: where the rule says "no targeted row improved",
   and the not-worse test passes, and the technique says "keep it".
5. The design mark in the val record, the events and the report.
6. A change to the train-side admission, so that the candidate reaches the
   gate.

Tests that cover the gate today: `tests/test_orchestrator.py`,
`tests/test_speed_objective.py`, `tests/test_integrity.py`,
`tests/test_memory.py`, `tests/test_report.py`.

## 6. Depends on

`biological_bootloader`: the technique documents and their "If it does not
move the evals" section.

## 7. Related

- `biological_bootloader` functional spec, section 8, and architecture,
  section 2.9.
- The owner's notes:
  [knowledge_system_design.md](../biological_bootloader/knowledge_system_design.md),
  "What to do if it doesn't move evals?".
