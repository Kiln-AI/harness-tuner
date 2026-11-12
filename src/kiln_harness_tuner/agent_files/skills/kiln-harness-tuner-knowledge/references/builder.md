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

## Levers

<!-- BEGIN GENERATED: levers -->
| Lever | Gateway operations | Description |
|---|---|---|
| [model](levers/model.md) | `get_run_config`, `list_available_models`, `create_run_config` | The model and the provider that a run config names, chosen from what the run's model policy allows. Existing fine-tunes are candidates too. |
| [new_tool](levers/new_tool.md) | `list_code_tools`, `get_code_tool`, `get_tool_definition`, `trust_code_tools`, `test_code_tool`, `create_code_tool`, `create_run_config`, `archive_code_tool` | Sandboxed Python code tools for the subject task - a new tool, a clone of a code tool with a new description, or a wrapper of any attached tool. |
| [params](levers/params.md) | `get_run_config`, `list_available_models`, `create_run_config` | The model's settings on a run config - thinking level, temperature and top-p - changed on a clone of the parent, as a params arm or in a screening round. |
| [prompt](levers/prompt.md) | `get_run_config`, `list_prompts`, `get_prompt_text`, `create_prompt`, `create_run_config` | The prompt text that a run config sends to the model, and its chain-of-thought instructions. A change is a new saved prompt and a new run config. |
| [skill](levers/skill.md) | `list_skills`, `get_skill`, `get_skill_content`, `create_run_config`, `archive_skill` | Skill bundles that a run config attaches as tools - a new skill, a new version of an inherited skill, or a skill attached or dropped on a new run config. |
| [tool_config](levers/tool_config.md) | `get_run_config`, `list_available_tools`, `get_tool_definition`, `list_code_tools`, `create_run_config` | Which existing tools a run config attaches - code, MCP, built-in, Kiln task and RAG tools - attached or dropped on a new run config, with no new code. |
<!-- END GENERATED: levers -->
