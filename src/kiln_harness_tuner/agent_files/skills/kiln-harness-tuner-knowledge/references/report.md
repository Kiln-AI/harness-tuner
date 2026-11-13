# The report writer's entry file

Use this file when the knowledge helps you explain a run: why an experiment
was planned, what a smell means, or what a technique is.

- A smell names an issue in the design of the agent. A playbook names a
  problem or a goal. A technique names one optimisation.
- A document gives general knowledge and its conditions. The measured
  results of this run come from the run, not from a document.
- A prior in a technique or a playbook is one case from elsewhere. Do not
  present it as a result of this run.

## Smells

<!-- BEGIN GENERATED: smells -->
| Smell | Applies to | Description |
|---|---|---|
| [document-behind-a-lookup-tool](smells/document-behind-a-lookup-tool.md) | tools | A long reference document that the agent can reach only by page, by search or by section, through a lookup tool, with no contents to choose from. |
| [faulty-inherited-skill](smells/faulty-inherited-skill.md) | all | A skill that the baseline attaches is wrong, stale, contradicts the task, or holds data shaped like test cases. It can be changed as a new version. |
| [fixed-call-chain](smells/fixed-call-chain.md) | tools | The agent calls the same tools in the same order on each run, and passes an id from one result to the next call. The model decides nothing new between the calls. |
| [general-query-tool-without-a-guide](smells/general-query-tool-without-a-guide.md) | tools | The agent has a SQL, GraphQL or general API tool, with no examples and no guide to the schema, so it must learn the data model on each run. |
| [one-file-skill-with-many-topics](smells/one-file-skill-with-many-topics.md) | all | An attached skill is one long file that holds many topics, with no reference files. The agent reads all of it to use one part of it. |
| [skill-too-small-to-earn-its-place](smells/skill-too-small-to-earn-its-place.md) | all | An attached skill holds a few words and no other file. Its description is in every run and opening it costs a call, so it can cost more than it saves. |
| [too-many-narrow-tools](smells/too-many-narrow-tools.md) | tools | The agent has many tools that each do one small thing, or tools that overlap, or general tools that carry no rule of the product. |
| [tool-results-flood-the-context](smells/tool-results-flood-the-context.md) | tools | A tool returns a very large result - a list with every field, a full schema, many pages - or the agent makes one call for each item of a list. |
| [tool-search](smells/tool-search.md) | tools | The agent must guess that a tool exists, decide to search for it, and find the right search terms, before it can call it. |
| [tools-on-a-live-server](smells/tools-on-a-live-server.md) | tools | The agent's tools read or write a staging or live system, so the right answer to an eval item can change with time, and a measurement cannot be repeated. |
| [very-long-prompt](smells/very-long-prompt.md) | all | The system prompt is longer than the reference length. A code detector measures it, and the detection gives the length, the reference and its source. |
| [very-long-skill-description](smells/very-long-skill-description.md) | all | The description of an attached skill is longer than the reference length. A code detector reads each skill's front matter, and each detection names the skill. |
| [very-long-tool-description](smells/very-long-tool-description.md) | tools | The description of an attached tool is longer than the reference length. A code detector measures each tool, and each detection names the tool. |
<!-- END GENERATED: smells -->

## Playbooks

<!-- BEGIN GENERATED: playbooks -->
| Playbook | Kind | Applies to | Description |
|---|---|---|---|
| [answers-not-grounded](playbooks/answers-not-grounded.md) | failure | all | The answers need knowledge that is not in the agent's context - attach the source, then add scope discipline. A source behind a lookup tool can move into a skill. |
| [change-the-surface-not-the-words](playbooks/change-the-surface-not-the-words.md) | failure | all | Two text changes failed on one issue, or the failure is mechanical. Change what the model touches before the words it reads. A policy failure is the exception - procedure text comes first. |
| [cheaper-or-faster](playbooks/cheaper-or-faster.md) | goal | all | The user wants the agent to cost less or answer sooner, with quality held - each step is marked cheaper, faster or both, because the two goals do not always agree. |
| [dealing-with-a-noisy-eval](playbooks/dealing-with-a-noisy-eval.md) | eval | all | A score moves between repeats with no change to the agent. Read the noise, predict effects larger than it, let the val gate confirm on new items, and do not let a decision turn on one or two items. |
| [dealing-with-a-saturated-eval](playbooks/dealing-with-a-saturated-eval.md) | eval | all | The baseline is at the ceiling of an eval, or the judge's fires are false. Measure the issue on the baseline, judge the fires, then grow or change the eval. No fix arm. |
| [dealing-with-an-eval-near-zero](playbooks/dealing-with-an-eval-near-zero.md) | eval | all | The baseline passes almost no item of an eval. Read the raw outputs - a format or protocol failure can be fixed, a capability floor cannot, and a stronger model gives a signal. |
| [dealing-with-train-wins-rejected-at-val](playbooks/dealing-with-train-wins-rejected-at-val.md) | eval | all | A train win ends REJECTED AT VAL. Read the reason first - passed over, copied eval text, an eval dropped, no gain, or a gain not clear. Only "no gain" shows that the win did not carry. |
| [failure-at-a-tool-call](playbooks/failure-at-a-tool-call.md) | failure | tools | The failing message comes before or with a tool call - a preamble, or a narration that rides the call. A new tool description, then a directive in the tool's response. |
| [fit-the-tool-layer-to-the-agent](playbooks/fit-the-tool-layer-to-the-agent.md) | design | tools | The agent's tools are the company's software APIs, and a good API can be a poor agent tool. Wrap them, do not rewrite them - which wrapper for which problem, with the prompt and the guide skill. |
| [know-the-agent-before-you-plan](playbooks/know-the-agent-before-you-plan.md) | order | all | Classify the agent before the first plan - its turns, its output, a classifier shape, its tools, skills and model - because each document says which shapes it fits. |
| [lost-in-long-instructions](playbooks/lost-in-long-instructions.md) | failure | all | The agent misses a part of a long prompt or of a one-file skill. Progressive disclosure when the model reads skills, a prompt structure when it does not. A probe first. |
| [malformed-tool-calls](playbooks/malformed-tool-calls.md) | failure | tools | The agent calls a tool with the wrong case, the wrong form of an id, a body on a GET, or a misspelled tool name. A wrapper that normalises, errors the agent can act on, a guard at the tool. |
| [missing-context-the-agent-could-be-given](playbooks/missing-context-the-agent-could-be-given.md) | failure | tools | The agent fails because it did not know a fact that a tool could give it - if it had known that, it would have done the right thing. One tool that loads it, and a prompt line to call it first. |
| [ordering-work](playbooks/ordering-work.md) | order | all | How to order the work of a run - the priority comes from the smells and the failures of this agent, not from a fixed order of levers. All of it is guidance. |
| [output-contract-not-met](playbooks/output-contract-not-met.md) | failure | all | The output breaks its format - broken JSON or JSON that misses the task's schema, a closing block, a layout of free text, an escaping rule, or the form of a field. |
| [requirement-missing-from-the-prompt](playbooks/requirement-missing-from-the-prompt.md) | failure | all | The eval checks for something that neither the prompt nor the task instruction asks for. State the requirement in new words - do not copy the eval. |
| [skill-is-not-read](playbooks/skill-is-not-read.md) | failure | all | The agent never loads a skill file, guesses a path, or loads the skill and does not follow it. The path index in the description, then a prompt rule. |
| [spending-the-budget](playbooks/spending-the-budget.md) | order | all | How to spend a fixed budget - fewer experiments, each large enough to pass its own test, the cheap techniques first on a small budget, and the scans only on a large one. |
| [working-inside-restrictions](playbooks/working-inside-restrictions.md) | goal | all | The user states a restriction - providers, the origin of a model, data retention, a tool that must not change. It is a hard limit on the plan. What to do when the best lever is not allowed. |
| [wrong-scope-or-refusal](playbooks/wrong-scope-or-refusal.md) | failure | all | The agent answers requests outside its scope, or refuses valid ones. A paragraph of decline rules, then a rebalance for the refusals that remain. |
| [wrong-tool-chosen](playbooks/wrong-tool-chosen.md) | failure | tools | The agent picks the wrong tool, or breaks a rule that the product enforces. Consolidate the tools with the policy inside, or remove and scope them, then better descriptions. |
<!-- END GENERATED: playbooks -->

## Techniques

Dormant techniques are not listed.

<!-- BEGIN GENERATED: techniques -->
| Technique | Levers | Applies to | Cost | Description |
|---|---|---|---|---|
| [add-the-missing-requirement](techniques/add-the-missing-requirement.md) | prompt | all | low | One prompt rule, in new words, for a requirement that the eval checks and that neither the prompt nor the task instruction asks for. |
| [answer-only-from-the-source](techniques/answer-only-from-the-source.md) | prompt | all | low | A short prompt rule of scope discipline - answer from the attached source, and say so when the source does not cover the question. |
| [attach-the-knowledge-source](techniques/attach-the-knowledge-source.md) | tool_config, skill | all | medium | Give the agent the knowledge its answers need - attach an existing search or RAG tool, or write the knowledge into a skill - when the answers are not grounded. |
| [cap-and-preview-wrapper](techniques/cap-and-preview-wrapper.md) | new_tool | tools | medium | A wrapper that returns only what this agent needs from a large tool result - the fields it uses, a capped preview with a follow-up query, or an error when the result is too large. |
| [consolidate-tools](techniques/consolidate-tools.md) | new_tool, tool_config | tools | high | Replace many narrow or overlapping tools with a few general wrappers by job, each with the product's policy inside - its permission checks, guards and defaults. |
| [decision-model-for-a-classifier](techniques/decision-model-for-a-classifier.md) | model | single_turn, structured_output, classifier, no_tools | medium | Run a classifier-shaped task on a decision model - a fast, cheap model that returns a prediction from a fixed set, not text - when cost or latency matters. |
| [decline-rules-paragraph](techniques/decline-rules-paragraph.md) | prompt | all | low | One prompt paragraph that states the agent's scope as rules - what it does, what it declines, how it declines, and what is not a reason to decline. |
| [directive-in-the-tool-response](techniques/directive-in-the-tool-response.md) | new_tool | tools | medium | A wrapper whose response carries a short directive for the reply, or the derived facts the reply must state, so that the agent reads it at the moment it writes the reply. |
| [document-into-a-skill](techniques/document-into-a-skill.md) | skill, tool_config | tools | medium | Move a long reference document that sits behind a lookup tool into a skill - a contents body with key terms, one reference file per section - and drop the tool. |
| [errors-the-agent-can-act-on](techniques/errors-the-agent-can-act-on.md) | new_tool | tools | medium | A wrapper that turns a raw tool error into an error the agent can correct from - what was wrong, the valid values or nearest matches, and what to do next. |
| [faster-provider-for-the-same-model](techniques/faster-provider-for-the-same-model.md) | model | all | low | Serve the same model from another provider - a faster one, or a cheaper one - with the harness unchanged, when the run allows that provider and the goal is latency or cost. |
| [guard-tool-calls-at-the-boundary](techniques/guard-tool-calls-at-the-boundary.md) | new_tool, prompt | tools | medium | A check in a wrapper that refuses a call which breaks a rule the product enforces, with an error that says why, and the prompt text that the guard takes over removed. |
| [guide-skill-for-a-tool-set](techniques/guide-skill-for-a-tool-set.md) | skill | tools | medium | A side skill that describes the agent's tools by progressive disclosure - which tool for which work, then one leaf file per tool or per query area. |
| [long-prompt-into-a-skill](techniques/long-prompt-into-a-skill.md) | skill, prompt | all | medium | Move the topics of a long prompt into a skill by progressive disclosure - a short prompt, a router body, one reference file per topic, a path index. |
| [model-swap-inside-a-class](techniques/model-swap-inside-a-class.md) | model | all | high | Lock one model of the class the task needs, tune the harness on it, then scan the class again for a model that is better, cheaper or faster with that harness. |
| [new-tool-description](techniques/new-tool-description.md) | new_tool | tools | low | A new description for a tool - a code-tool clone, or a wrapper - because the description is the one text the model reads at the moment it decides to call the tool. |
| [normalize-inputs-wrapper](techniques/normalize-inputs-wrapper.md) | new_tool | tools | medium | A wrapper that accepts the input forms the agent sends - case, aliases, display numbers, types - maps each to what the tool needs, and names the valid values when it cannot. |
| [one-general-rule-per-edit](techniques/one-general-rule-per-edit.md) | prompt, skill | all | low | A text edit states one general rule, with its reason and its source, and replaces text - not one rule for each failing case. |
| [path-index-in-the-skill-description](techniques/path-index-in-the-skill-description.md) | skill | all | low | A new skill version whose description lists the full path of each reference file, with its key terms, and says that no other paths exist. |
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
