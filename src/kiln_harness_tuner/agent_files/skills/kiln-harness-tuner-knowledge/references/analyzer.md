# The analyzer's entry file

Use this file to name the failure pattern that a candidate's traces show.

## How to name a failure pattern

1. **Read the traces first.** Find where the failing message is, and what
   came before it.
2. **Find the playbook whose situation fits** in the table below. Open it
   when the one-line description is not enough. Its "Situation" says what
   the trace shows, and where.
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

<!-- BEGIN GENERATED: playbooks-failure -->
| Playbook | Applies to | Description |
|---|---|---|
| [answers-not-grounded](playbooks/answers-not-grounded.md) | all | The answers need knowledge that is not in the agent's context - attach the source, then add scope discipline. A source behind a lookup tool can move into a skill. |
| [change-the-surface-not-the-words](playbooks/change-the-surface-not-the-words.md) | all | Two text changes failed on one issue, or the failure is mechanical. Change what the model touches before the words it reads. A policy failure is the exception - procedure text comes first. |
| [failure-at-a-tool-call](playbooks/failure-at-a-tool-call.md) | tools | The failing message comes before or with a tool call - a preamble, or a narration that rides the call. A new tool description, then a directive in the tool's response. |
| [lost-in-long-instructions](playbooks/lost-in-long-instructions.md) | all | The agent misses a part of a long prompt or of a one-file skill. Progressive disclosure when the model reads skills, a prompt structure when it does not. A probe first. |
| [malformed-tool-calls](playbooks/malformed-tool-calls.md) | tools | The agent calls a tool with the wrong case, the wrong form of an id, a body on a GET, or a misspelled tool name. A wrapper that normalises, errors the agent can act on, a guard at the tool. |
| [missing-context-the-agent-could-be-given](playbooks/missing-context-the-agent-could-be-given.md) | tools | The agent fails because it did not know a fact that a tool could give it - if it had known that, it would have done the right thing. One tool that loads it, and a prompt line to call it first. |
| [output-contract-not-met](playbooks/output-contract-not-met.md) | all | The output breaks its format - broken JSON or JSON that misses the task's schema, a closing block, a layout of free text, an escaping rule, or the form of a field. |
| [requirement-missing-from-the-prompt](playbooks/requirement-missing-from-the-prompt.md) | all | The eval checks for something that neither the prompt nor the task instruction asks for. State the requirement in new words - do not copy the eval. |
| [skill-is-not-read](playbooks/skill-is-not-read.md) | all | The agent never loads a skill file, guesses a path, or loads the skill and does not follow it. The path index in the description, then a prompt rule. |
| [wrong-scope-or-refusal](playbooks/wrong-scope-or-refusal.md) | all | The agent answers requests outside its scope, or refuses valid ones. A paragraph of decline rules, then a rebalance for the refusals that remain. |
| [wrong-tool-chosen](playbooks/wrong-tool-chosen.md) | tools | The agent picks the wrong tool, or breaks a rule that the product enforces. Consolidate the tools with the policy inside, or remove and scope them, then better descriptions. |
<!-- END GENERATED: playbooks-failure -->
