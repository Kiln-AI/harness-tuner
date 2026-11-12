# Technique index

**Prefer a playbook. Use this index only when the user's guidance names a
technique** (for example "try a skill for the policy"). A choice by goal or by
situation is better than a choice by name, so the planner reaches a technique
through a playbook or a smell.

Dormant techniques are not listed.

<!-- BEGIN GENERATED: techniques -->
| Technique | Levers | Applies to | Cost | Description |
|---|---|---|---|---|
| [add-the-missing-requirement](techniques/add-the-missing-requirement.md) | prompt | all | low | One prompt rule, in new words, for a requirement that the eval checks and that neither the prompt nor the task instruction asks for. |
| [answer-only-from-the-source](techniques/answer-only-from-the-source.md) | prompt | all | low | A short prompt rule of scope discipline - answer from the attached source, and say so when the source does not cover the question. |
| [attach-the-knowledge-source](techniques/attach-the-knowledge-source.md) | tool_config, skill | all | medium | Give the agent the knowledge its answers need - attach an existing search or RAG tool, or write the knowledge into a skill - when the answers are not grounded. |
| [decline-rules-paragraph](techniques/decline-rules-paragraph.md) | prompt | all | low | One prompt paragraph that states the agent's scope as rules - what it does, what it declines, how it declines, and what is not a reason to decline. |
| [document-into-a-skill](techniques/document-into-a-skill.md) | skill, tool_config | tools | medium | Move a long reference document that sits behind a lookup tool into a skill - a contents body with key terms, one reference file per section - and drop the tool. |
| [guide-skill-for-a-tool-set](techniques/guide-skill-for-a-tool-set.md) | skill | tools | medium | A side skill that describes the agent's tools by progressive disclosure - which tool for which work, then one leaf file per tool or per query area. |
| [long-prompt-into-a-skill](techniques/long-prompt-into-a-skill.md) | skill, prompt | all | medium | Move the topics of a long prompt into a skill by progressive disclosure - a short prompt, a router body, one reference file per topic, a path index. |
| [one-general-rule-per-edit](techniques/one-general-rule-per-edit.md) | prompt, skill | all | low | A text edit states one general rule, with its reason and its source, and replaces text - not one rule for each failing case. |
| [path-index-in-the-skill-description](techniques/path-index-in-the-skill-description.md) | skill | all | low | A new skill version whose description lists the full path of each reference file, with its key terms, and says that no other paths exist. |
| [policy-as-step-by-step-procedures](techniques/policy-as-step-by-step-procedures.md) | prompt, skill | all | low | Rewrite a policy that is written as prose into numbered procedures, one for each type of request, with yes-or-no conditions and a check after each fix. |
| [prune-one-group-at-a-time](techniques/prune-one-group-at-a-time.md) | prompt, skill, tool_config | all | low | Find what helps in a large harness - remove one group (a prompt section, a skill, a set of tools) on each arm and measure it, or add groups one at a time to a bare base. |
| [rewrite-an-inherited-skill](techniques/rewrite-an-inherited-skill.md) | skill | all | medium | A new version of a skill that the baseline attaches - split, merged, restructured or scrubbed - attached in place of the old one on a new run config. |
| [skill-into-the-prompt](techniques/skill-into-the-prompt.md) | prompt, skill | all | low | Move the content of a skill into the prompt and drop the skill - for a small skill, content that most runs need, or a skill the agent does not open. |
| [state-the-output-contract](techniques/state-the-output-contract.md) | prompt | all | low | One prompt line that states a format rule the model gets in no other way - a layout of free text, a closing block, an escaping rule, or a rule about a field. |
| [structure-a-long-prompt](techniques/structure-a-long-prompt.md) | prompt | all | low | Give a long prompt a structure - topics under headings, the must-include parts marked, and a last step that checks each required property before the answer. |
<!-- END GENERATED: techniques -->
