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
4. **Fit the technique to this agent.** Read it for the spirit of its
   guidance, as [the knowledge skill](../SKILL.md) says in "How to read
   these documents". The hard limits in the lever documents do not change.

The procedure of your role stays in the `kiln-harness-tuner-experiment-build`
skill. This file adds what to build, not how a build is done.

The column "Applies to" gives the task shapes a document applies to. A
document applies only when the task has every shape in its list. `all`
means that the shape does not matter.

## Techniques

Dormant techniques are not listed.

<!-- BEGIN GENERATED: techniques -->
| Technique | Levers | Applies to | Cost | Description |
|---|---|---|---|---|
| [add-the-missing-requirement](techniques/add-the-missing-requirement.md) | prompt | all | low | One prompt rule, in new words, for a requirement that the eval checks and that neither the prompt nor the task instruction asks for. |
| [answer-only-from-the-source](techniques/answer-only-from-the-source.md) | prompt | all | low | A short prompt rule of scope discipline - answer from the attached source, and say so when the source does not cover the question. |
| [attach-the-knowledge-source](techniques/attach-the-knowledge-source.md) | tool_config, skill | all | medium | Give the agent the knowledge its answers need - attach an existing search or RAG tool, or write the knowledge into a skill - when the answers are not grounded. |
| [cap-and-preview-wrapper](techniques/cap-and-preview-wrapper.md) | new_tool | tools | medium | A wrapper that returns only what this agent needs from a large tool result - a trim that says when it cut and how to get more, a capped preview with a follow-up, or an error when it is too large. |
| [consolidate-tools](techniques/consolidate-tools.md) | new_tool, tool_config | tools | high | Replace many narrow or overlapping tools with a few general wrappers by job, each with the product's policy inside - its permission checks, guards and defaults. |
| [decision-model-for-a-classifier](techniques/decision-model-for-a-classifier.md) | model | single_turn, structured_output, classifier, no_tools | medium | Run a classifier-shaped task on a decision model - a fast, cheap model that returns a prediction from a fixed set, not text - when cost or latency matters. |
| [decline-rules-paragraph](techniques/decline-rules-paragraph.md) | prompt | all | low | One prompt paragraph that states the agent's scope as rules - what it does, what it declines, how it declines, and what is not a reason to decline. |
| [directive-in-the-tool-response](techniques/directive-in-the-tool-response.md) | new_tool | tools | medium | A wrapper whose response states the tool's own status for the reply - derived facts, a fixable error, or how the tool works - as facts about the result, not as orders to the agent. |
| [document-into-a-skill](techniques/document-into-a-skill.md) | skill, tool_config | tools | medium | Move a long reference document that sits behind a lookup tool into a skill - a contents body with key terms, one reference file per section - and drop the tool. |
| [errors-the-agent-can-act-on](techniques/errors-the-agent-can-act-on.md) | new_tool | tools | medium | A wrapper that turns a raw tool error into an error the agent can correct from - what was wrong, the valid values or nearest matches, and what to do next. |
| [faster-provider-for-the-same-model](techniques/faster-provider-for-the-same-model.md) | model | all | low | Serve the same model from another provider - a faster one, or a cheaper one - with the harness unchanged, when the run allows that provider and the goal is latency or cost. |
| [guard-tool-calls-at-the-boundary](techniques/guard-tool-calls-at-the-boundary.md) | new_tool, prompt | tools | medium | A check in a wrapper that refuses a call which breaks a rule the product enforces, with an error that says why, and the rule moved from the prompt into the tool's description. |
| [guide-skill-for-a-tool-set](techniques/guide-skill-for-a-tool-set.md) | skill | tools | medium | A side skill that describes the agent's tools by progressive disclosure - which tool for which work, then one leaf file per tool or per query area. |
| [long-prompt-into-a-skill](techniques/long-prompt-into-a-skill.md) | skill, prompt | all | medium | Move the topics of a long prompt into a skill by progressive disclosure - a short prompt, a router body, one reference file per topic, a path index. |
| [model-swap-inside-a-class](techniques/model-swap-inside-a-class.md) | model | all | high | Lock one model of the class the task needs, tune the harness on it, then scan the class again for a model that is better, cheaper or faster with that harness. |
| [new-tool-description](techniques/new-tool-description.md) | new_tool | tools | low | A new description for a tool - a code-tool clone, or a wrapper - for a failure at a call. A rule in the tool moves with the tool, so the prompt does not change with the tool set. |
| [normalize-inputs-wrapper](techniques/normalize-inputs-wrapper.md) | new_tool | tools | medium | A wrapper that accepts the input forms the agent sends - case, aliases, display numbers, types - maps each to what the tool needs, and names the valid values when it cannot. |
| [one-general-rule-per-edit](techniques/one-general-rule-per-edit.md) | prompt, skill | all | low | A text edit states one general rule, with its reason and its source, and replaces text - not one rule for each failing case. |
| [policy-as-step-by-step-procedures](techniques/policy-as-step-by-step-procedures.md) | prompt, skill | all | low | Rewrite a policy that is written as prose into numbered procedures, one for each type of request, with yes-or-no conditions and a check after each fix. |
| [prune-one-group-at-a-time](techniques/prune-one-group-at-a-time.md) | prompt, skill, tool_config | all | low | Find what helps in a large harness - remove one group (a prompt section, a skill, a set of tools) on each arm and measure it, or add groups one at a time to a bare base. |
| [remove-or-scope-tools](techniques/remove-or-scope-tools.md) | tool_config | tools | low | Drop the attached tools that this task does not need, or that overlap, so the agent chooses among fewer tools and reads fewer descriptions on every call. |
| [rewrite-an-inherited-skill](techniques/rewrite-an-inherited-skill.md) | skill | all | medium | A new version of a skill that the baseline attaches - split, merged, restructured or scrubbed - attached in place of the old one on a new run config. |
| [roll-up-tool](techniques/roll-up-tool.md) | new_tool, prompt | tools | medium | One code tool that does what the agent does in many calls - a fixed chain that passes an id along, a loop of one call per item, or the context the agent needs at the start. |
| [skill-into-the-prompt](techniques/skill-into-the-prompt.md) | prompt, skill | all | low | Move the content of a skill into the prompt and drop the skill - for a small skill, content that most runs need, or a skill the agent does not open. |
| [state-the-output-contract](techniques/state-the-output-contract.md) | prompt | all | low | One prompt line that states a format rule the model gets in no other way - a layout of free text, a closing block, an escaping rule, or a rule about a field. |
| [structure-a-long-prompt](techniques/structure-a-long-prompt.md) | prompt | all | low | Give a long prompt a structure - topics under headings, the must-include parts marked, and a last step that checks each required property before the answer. |
| [thinking-level-ladder](techniques/thinking-level-ladder.md) | params | all | low | Change the thinking level one rung at a time from the baseline's level - up only for hard cases, down for cost or latency once quality is stable - and temperature at most once. |
<!-- END GENERATED: techniques -->

## Levers

<!-- BEGIN GENERATED: levers -->
| Lever | Gateway operations | Description |
|---|---|---|
| [model](levers/model.md) | `get_run_config`, `list_available_models`, `create_run_config` | The model and the provider that a run config names, chosen from what the run's model policy allows. Existing fine-tunes are candidates too. |
| [new_tool](levers/new_tool.md) | `list_code_tools`, `get_code_tool`, `get_tool_definition`, `trust_code_tools`, `test_code_tool`, `create_code_tool`, `create_run_config`, `archive_code_tool` | Sandboxed Python code tools for the subject task - a new tool, a clone of a code tool with a new description, or a wrapper of any attached tool. |
| [output_mode](levers/output_mode.md) | `get_run_config`, `list_available_models`, `create_run_config` | How Kiln asks the model for output that matches the task's output schema - the run config's structured_output_mode. A change is a model-level swap. |
| [params](levers/params.md) | `get_run_config`, `list_available_models`, `create_run_config` | The model's settings on a run config - thinking level, temperature and top-p - changed on a clone of the parent, as a params arm or in a screening round. |
| [prompt](levers/prompt.md) | `get_run_config`, `list_prompts`, `get_prompt_text`, `create_prompt`, `create_run_config` | The prompt text that a run config sends to the model, and its chain-of-thought instructions. A change is a new saved prompt and a new run config. |
| [skill](levers/skill.md) | `list_skills`, `get_skill`, `get_skill_content`, `create_run_config`, `archive_skill` | Skill bundles that a run config attaches as tools - a new skill, a new version of an inherited skill, or a skill attached or dropped on a new run config. |
| [tool_config](levers/tool_config.md) | `get_run_config`, `list_available_tools`, `get_tool_definition`, `list_code_tools`, `create_run_config` | Which existing tools a run config attaches - code, MCP, built-in, Kiln task and RAG tools - attached or dropped on a new run config, with no new code. |
<!-- END GENERATED: levers -->
