# The analyzer's entry file

Use this file to name the failure pattern that a candidate's traces show.

## How to name a failure pattern

1. **Read the traces first.** Find where the failing message is, and what
   came before it.
2. **Find the playbook whose situation fits** in the table below. Open it
   when the one-line description is not enough. Its "Situation" says what
   the trace shows, and where.
3. **Name the playbook in your findings**, by its id, with the evidence. When
   no playbook fits, describe the failure in your own words.

You do not plan the next experiment. The planner reads your findings and the
playbook.

## Failure patterns

<!-- BEGIN GENERATED: playbooks-failure -->
| Playbook | Applies to | Description |
|---|---|---|
| [output-contract-not-met](playbooks/output-contract-not-met.md) | all | The output breaks a format rule that nothing enforces - a closing block, a layout of free text, an escaping rule, or a rule about the content of a field. |
<!-- END GENERATED: playbooks-failure -->
