---
name: kiln-harness-tuner-knowledge
description: The Kiln Harness Tuner knowledge system - what to try when you tune an agent's harness. Smells (issues in the design of the agent), playbooks (how to solve one problem or reach one goal), techniques (one optimisation each) and levers (what the tuner can change, and how). Each loop role has its own entry file in references/ - planner.md, builder.md, smells.md, analyzer.md, report.md - and technique-index.md is only for a technique that the user's guidance names. Your prompt template names your entry file. No other files exist at the top of references/.
---

# kiln-harness-tuner-knowledge

This skill holds what is known about how to improve an agent's harness: the
prompt, the skills, the tools, the model, its settings and its output
mode. It is general knowledge, from the field, from public sources and
from other projects. It holds nothing that was measured on the task that
you tune.

## How to read these documents

**These are ideas to try.** The techniques, the playbooks and the smells
are ideas to try as part of experimentation. What actually works varies
from agent to agent. A document says what to try, in what order, and under
which conditions. You know more about this agent than the documents do.
Read each document for the spirit of its guidance. Fit its suggestions to
the agent's goal, its tools and its task, and change a suggestion where the
agent needs it. The hard limits are the exception: the safety and integrity
rules in the lever documents. For example: never attach a tool that can
reach the Kiln server; the allowlist and the shared-tool rules of a code
tool; no value from a train item in a change. No measurement overrides a
hard limit, and you do not change one to fit the agent.

**A clear measurement outranks a document.** A result measured on this task
outranks what a document suggests only when it is clearly past the noise,
as the stop rules and the val gate judge it. A result inside the noise does
not refute a technique: see
[dealing with a noisy eval](references/playbooks/dealing-with-a-noisy-eval.md).
When two options tie, the knowledge can settle the choice.

## The parts

| Part | What it is | Where |
|---|---|---|
| Smell | An issue in the design of the agent. It is true whether an eval passes or fails. Its document says how to recognise it and links to its remedies. | `references/smells/` |
| Playbook | How to solve one problem or reach one goal, as an ordered list of things to try. | `references/playbooks/` |
| Technique | One optimisation: when to use it, how, how to check it took effect, and its priors. | `references/techniques/` |
| Lever | One thing in the harness that the tuner can change, and how. | `references/levers/` |

The usual path is: entry file, then a smell or a playbook, then a technique,
then the levers it uses.

## Your entry file

Start from the entry file of your role. You do not need the entry file of
another role.

| Role | Entry file | What it gives you |
|---|---|---|
| Planner (batch selection) | [references/planner.md](references/planner.md) | Every smell and every playbook, grouped by kind |
| Builder (experiment build) | [references/builder.md](references/builder.md) | Every active technique and every lever |
| Baseline reviewer | [references/smells.md](references/smells.md) | Every smell, with who can detect it |
| Analyzer | [references/analyzer.md](references/analyzer.md) | The playbooks for failure patterns |
| Report writer | [references/report.md](references/report.md) | The smells, playbooks and techniques that can explain a run |

[references/technique-index.md](references/technique-index.md) lists the
techniques by name. Use it only when the user's guidance names a technique.
Otherwise, reach a technique through a playbook or a smell.

## Related

`kiln-harness-tuner-batch-selection` (the planner's procedure) ·
`kiln-harness-tuner-experiment-build` (the builder's procedure) ·
`kiln-harness-tuner-gateway` (every Kiln operation a role has).
