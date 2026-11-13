# The baseline reviewer's entry file

Use this file to review the design of the baseline agent.

## How to review a design

1. **Read each smell in the table below.** When the one-line description is
   not enough, open its document.
2. **Look for each smell in the agent**: in the prompt, the skills, the
   tools, the model and its settings, and in the sampled traces.
3. **Apply "How to confirm it"** of the smell before you report it. That
   section also says what looks like the smell and is not.
4. **State the evidence**: what you saw, and where. Say how sure you are.
5. **Do not state a fix.** The smell document links to its remedies, and the
   planner chooses.

"Detected by" says who can find the smell: `code` (a detector in code, whose
detections are in your input) or `agent` (a reader of the design).

## Smells

<!-- BEGIN GENERATED: smells-detail -->
| Smell | Applies to | Detected by | Description |
|---|---|---|---|
| [document-behind-a-lookup-tool](smells/document-behind-a-lookup-tool.md) | tools | agent | A long reference document that the agent can reach only by page, by search or by section, through a lookup tool, with no contents to choose from. |
| [faulty-inherited-skill](smells/faulty-inherited-skill.md) | all | agent | A skill that the baseline attaches is wrong, stale, contradicts the task, or holds data shaped like test cases. It can be changed as a new version. |
| [fixed-call-chain](smells/fixed-call-chain.md) | tools | agent | The agent calls the same tools in the same order on each run, and passes an id from one result to the next call. The model decides nothing new between the calls. |
| [general-query-tool-without-a-guide](smells/general-query-tool-without-a-guide.md) | tools | agent | The agent has a SQL, GraphQL or general API tool, with no examples and no guide to the schema, so it must learn the data model on each run. |
| [old-model](smells/old-model.md) | all | code | The baseline's model was released more than the reference time before the run. A code detector reads the release date, and the detection gives the age, the reference and its source. |
| [one-file-skill-with-many-topics](smells/one-file-skill-with-many-topics.md) | all | agent | An attached skill is one long file that holds many topics, with no reference files. The agent reads all of it to use one part of it. |
| [skill-too-small-to-earn-its-place](smells/skill-too-small-to-earn-its-place.md) | all | agent | An attached skill holds a few words and no other file. Its description is in every run and opening it costs a call, so it can cost more than it saves. |
| [too-many-narrow-tools](smells/too-many-narrow-tools.md) | tools | agent | The agent has many tools that each do one small thing, or tools that overlap, or general tools that carry no rule of the product. |
| [tool-results-flood-the-context](smells/tool-results-flood-the-context.md) | tools | agent | A tool returns a very large result - a list with every field, a full schema, many pages - or the agent makes one call for each item of a list. |
| [tool-search](smells/tool-search.md) | tools | agent | The agent must guess that a tool exists, decide to search for it, and find the right search terms, before it can call it. |
| [tools-on-a-live-server](smells/tools-on-a-live-server.md) | tools | agent | The agent's tools read or write a staging or live system, so the right answer to an eval item can change with time, and a measurement cannot be repeated. |
| [very-long-prompt](smells/very-long-prompt.md) | all | code | The system prompt is longer than the reference length. A code detector measures it, and the detection gives the length, the reference and its source. |
| [very-long-skill-description](smells/very-long-skill-description.md) | all | code | The description of an attached skill is longer than the reference length. A code detector reads each skill's front matter, and each detection names the skill. |
| [very-long-tool-description](smells/very-long-tool-description.md) | tools | code | The description of an attached tool is longer than the reference length. A code detector measures each tool, and each detection names the tool. |
<!-- END GENERATED: smells-detail -->
