# The report writer's entry file

Use this file when the knowledge helps you explain a run: why an experiment
was planned, what a smell means, or what a technique is.

- A smell names an issue in the design of the agent. A playbook names a
  problem or a goal. A technique names one optimisation.
- A document gives general knowledge and its conditions. The measured
  results of this run come from the run, not from a document.
- A prior in a technique is one case from elsewhere. Do not present it as a
  result of this run.

## Smells

<!-- BEGIN GENERATED: smells -->
| Smell | Applies to | Description |
|---|---|---|
| [tool-search](smells/tool-search.md) | tools | The agent must guess that a tool exists, decide to search for it, and find the right search terms, before it can call it. |
<!-- END GENERATED: smells -->

## Playbooks

<!-- BEGIN GENERATED: playbooks -->
| Playbook | Kind | Applies to | Description |
|---|---|---|---|
| [output-contract-not-met](playbooks/output-contract-not-met.md) | failure | all | The output breaks a format rule that nothing enforces - a closing block, a layout of free text, an escaping rule, or a rule about the content of a field. |
<!-- END GENERATED: playbooks -->

## Techniques

Dormant techniques are not listed.

<!-- BEGIN GENERATED: techniques -->
| Technique | Levers | Applies to | Cost | Description |
|---|---|---|---|---|
| [guide-skill-for-a-tool-set](techniques/guide-skill-for-a-tool-set.md) | skill | tools | medium | A side skill that describes the agent's tools by progressive disclosure - which tool for which work, then one leaf file per tool or per query area. |
| [state-the-output-contract](techniques/state-the-output-contract.md) | prompt | all | low | One prompt line that states a format rule the model gets in no other way - a layout of free text, a closing block, an escaping rule, or a rule about a field. |
<!-- END GENERATED: techniques -->
