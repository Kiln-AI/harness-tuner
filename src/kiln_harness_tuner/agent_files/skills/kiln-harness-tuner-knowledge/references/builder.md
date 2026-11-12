# The builder's entry file

Use this file to build the technique of your work item.

## How to use the knowledge

1. **Open the technique of your item.** Find it in the table "Techniques",
   or follow the link that the planner's hypothesis gives. Read its "How"
   and its "Check that it took effect" before you build.
2. **Open each lever that the technique links.** A lever document says how
   the tuner creates and changes that part of the harness: the gateway
   operations, the rules of the object and the limits.
3. **Use the check of the technique in your smoke.** A change that did not
   reach the agent is not built.

The procedure of your role stays in the `kiln-harness-tuner-experiment-build`
skill. This file adds what to build, not how a build is done.

## Techniques

Dormant techniques are not listed.

<!-- BEGIN GENERATED: techniques -->
| Technique | Levers | Applies to | Cost | Description |
|---|---|---|---|---|
| [guide-skill-for-a-tool-set](techniques/guide-skill-for-a-tool-set.md) | skill | tools | medium | A side skill that describes the agent's tools by progressive disclosure - which tool for which work, then one leaf file per tool or per query area. |
| [state-the-output-contract](techniques/state-the-output-contract.md) | prompt | all | low | One prompt line that states a format rule the model gets in no other way - a layout of free text, a closing block, an escaping rule, or a rule about a field. |
<!-- END GENERATED: techniques -->

## Levers

<!-- BEGIN GENERATED: levers -->
| Lever | Gateway operations | Description |
|---|---|---|
| [prompt](levers/prompt.md) | `get_run_config`, `list_prompts`, `get_prompt_text`, `create_prompt`, `create_run_config` | The prompt text that a run config sends to the model, and its chain-of-thought instructions. A change is a new saved prompt and a new run config. |
| [skill](levers/skill.md) | `list_skills`, `get_skill`, `get_skill_content`, `create_run_config`, `archive_skill` | Skill bundles that a run config attaches as tools - a new skill, a new version of an inherited skill, or a skill attached or dropped on a new run config. |
<!-- END GENERATED: levers -->
