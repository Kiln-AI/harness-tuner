# The analyzer's entry file

Use this file to name the failure pattern that a candidate's traces show,
or the eval problem when the measurement is the problem.

## How to name a failure pattern

1. **Read the traces first.** Find where the failing message is, and what
   came before it.
2. **Find the playbook whose situation fits** in the tables below: a
   failure pattern of the agent, or an eval problem, for example a judge
   that fails right answers. Open it when the one-line description is not
   enough. Its "Situation" says what the trace shows, and where.
3. **Name the playbook in your `memory_content`**, by its id, with the
   evidence: in the interpretation or in the `next:` line. The planner
   reads `memory_content`. It does not read `findings`. Name the playbook
   in `findings` too, for the report. When no playbook fits, describe the
   failure in your own words, in the same places.

You do not plan the next experiment. The planner reads your
`memory_content` and the playbook.

The column "Applies to" gives the task shapes a document applies to. A
document applies only when the task has every shape in its list. `all`
means that the shape does not matter.

## Failure patterns

A class of failure seen in the traces of eval items.

<!-- BEGIN GENERATED: playbooks-failure -->
| Playbook | Applies to | Description |
|---|---|---|
| [answers-not-grounded](playbooks/answers-not-grounded.md) | all | The answers need knowledge that is not in the agent's context - attach the source, then add scope discipline. A source behind a lookup tool can move into a skill. |
| [change-the-surface-not-the-words](playbooks/change-the-surface-not-the-words.md) | all | Two text changes failed on one issue, or the failure is mechanical. Change what the model touches before the words it reads. A policy failure comes here only after two text arms failed. |
| [failure-at-a-tool-call](playbooks/failure-at-a-tool-call.md) | tools | The failing message comes before or with a tool call - a preamble, or a narration that rides the call. A new tool description, then a directive in the tool's response. |
| [lost-in-long-instructions](playbooks/lost-in-long-instructions.md) | all | The agent misses a part of a long prompt or of a one-file skill. Progressive disclosure when the model reads skills, a prompt structure when it does not. A probe first. |
| [malformed-tool-calls](playbooks/malformed-tool-calls.md) | tools | The agent calls a tool with the wrong case, the wrong form of an id, a body on a GET, or a misspelled tool name. A wrapper that normalises, errors the agent can act on, a guard at the tool. |
| [missing-context-the-agent-could-be-given](playbooks/missing-context-the-agent-could-be-given.md) | tools | The agent fails because it did not know a fact that a tool could give it - if it had known that, it would have done the right thing. A prompt line to always call that tool, or a new tool. |
| [output-contract-not-met](playbooks/output-contract-not-met.md) | all | The output breaks its format - broken JSON or JSON that misses the task's schema, a closing block, a layout of free text, an escaping rule, or the form of a field. |
| [requirement-missing-from-the-prompt](playbooks/requirement-missing-from-the-prompt.md) | all | The eval checks for something that neither the prompt nor the task instruction asks for. State the requirement in new words - do not copy the eval. |
| [skill-is-not-read](playbooks/skill-is-not-read.md) | all | The agent never loads a skill file, guesses a path, or loads the skill and does not follow it. A rewrite of the skill, then a prompt rule. |
| [the-task-input-misleads-the-model](playbooks/the-task-input-misleads-the-model.md) | single_turn | The wrong answers come from how the task input reaches the model - a field it should not follow, a fact it must derive, a long text. Change the input with an input transform, the narrow change first. |
| [wrong-scope-or-refusal](playbooks/wrong-scope-or-refusal.md) | all | The agent answers requests outside its scope, or refuses valid ones. A paragraph of decline rules, then a rebalance for the refusals that remain. |
| [wrong-tool-chosen](playbooks/wrong-tool-chosen.md) | tools | The agent picks the wrong tool, or breaks a rule that the product enforces. Fit the tool layer first when the agent as a whole needs it, then remove and scope tools, descriptions, guards. |
<!-- END GENERATED: playbooks-failure -->

## Eval problems

The measurement is the problem.

<!-- BEGIN GENERATED: playbooks-eval -->
| Playbook | Applies to | Description |
|---|---|---|
| [a-few-items-carry-the-signal](playbooks/a-few-items-carry-the-signal.md) | all | Every difference between arms comes from the same few items. Find out why those items move - noise, a wrong expected output, or the only real failures - before you plan against them. |
| [an-eval-too-small-for-the-effect](playbooks/an-eval-too-small-for-the-effect.md) | all | The eval has too few items, or too few that fail, to show a change of the size you predict. Check the size before the arm, then aim at more items, a larger effect, or a merge. |
| [dealing-with-a-noisy-eval](playbooks/dealing-with-a-noisy-eval.md) | all | A score moves between repeats with no change to the agent. Read the noise, predict effects larger than it, let the val gate confirm on new items, and do not let a decision turn on one or two items. |
| [dealing-with-a-saturated-eval](playbooks/dealing-with-a-saturated-eval.md) | all | The baseline passes almost every item of an eval, so an arm has no room to show a gain on it. Keep the eval as a guard, put the effort elsewhere, and ask its owner for harder items. |
| [dealing-with-a-wrong-eval](playbooks/dealing-with-a-wrong-eval.md) | all | The judge fails right answers or passes wrong ones, or the eval does not measure the issue. Classify each fire, arm only the config failures, report the rest. Do not tune toward the judge. |
| [dealing-with-an-eval-near-zero](playbooks/dealing-with-an-eval-near-zero.md) | all | The baseline passes almost no item of an eval. Read the raw outputs first - text fixes a missing requirement or a wrong format. Try a smarter model only on evidence that the model cannot do the task. |
| [dealing-with-train-wins-rejected-at-val](playbooks/dealing-with-train-wins-rejected-at-val.md) | all | A train win ends REJECTED AT VAL. Read the reason first - passed over, copied eval text, an eval dropped, no gain, not clear, unverified, val incomplete. Only "no gain" shows a win did not carry. |
<!-- END GENERATED: playbooks-eval -->
