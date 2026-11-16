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
detections are in your input) or `agent` (a reader of the design). A smell
with both has a sure case that code detects, and a criterion for a reader
that applies with no detection.

The column "Applies to" gives the task shapes a document applies to. A
document applies only when the task has every shape in its list. `all`
means that the shape does not matter.

## Smells

<!-- BEGIN GENERATED: smells-detail -->
| Smell | Applies to | Detected by | Description |
|---|---|---|---|
| [document-behind-a-lookup-tool](smells/document-behind-a-lookup-tool.md) | tools | agent | A long reference document that the agent can reach only by page, by search or by section, through a lookup tool, with no contents to choose from. |
| [faulty-inherited-skill](smells/faulty-inherited-skill.md) | all | agent | A skill that the baseline attaches is wrong or stale, contradicts the task, or holds an example whose answer contradicts a rule. It can be changed as a new version. |
| [fixed-call-chain](smells/fixed-call-chain.md) | tools | agent | The agent calls the same tools in the same order on each run, and passes an id from one result to the next call. The model decides nothing new between the calls. |
| [general-query-tool-without-a-guide](smells/general-query-tool-without-a-guide.md) | tools | agent | The agent has a SQL, GraphQL or general API tool, with no examples and no guide to the schema, so it must learn the data model on each run. |
| [one-file-skill-with-many-topics](smells/one-file-skill-with-many-topics.md) | all | agent | An attached skill is one long file that holds many topics, with no reference files. The agent reads all of it to use one part of it. |
| [skill-too-small-to-earn-its-place](smells/skill-too-small-to-earn-its-place.md) | all | agent | An attached skill holds a few words and no other file. Its description is in every run and opening it costs a call, so it can cost more than it saves. |
| [too-many-tools](smells/too-many-tools.md) | tools | code | The run config attaches more than 40 tools, and every tool definition is in the context of every call. A code detector counts them. |
| [tool-results-flood-the-context](smells/tool-results-flood-the-context.md) | tools | agent | A tool returns a very large result - a list with every field, a full schema, many pages - or the agent makes one call for each item of a list. |
| [tool-search](smells/tool-search.md) | tools | agent | The tools are listed nowhere the model can read, so the agent must guess that a tool exists and guess the raw search terms that find it. |
| [tools-on-a-live-server](smells/tools-on-a-live-server.md) | tools | agent | The agent's tools read or write a staging or live system, so the right answer to an eval item can change with time, and a measurement cannot be repeated. |
| [unnecessary-tools](smells/unnecessary-tools.md) | tools | agent | Tools that overlap - two tools used for the same kind of request - or tools never called in the traces. One tool for each job, and each tool needed. |
| [very-long-prompt](smells/very-long-prompt.md) | all | code, agent | The system prompt is longer than the reference length, or it holds many unrelated topics and each request needs a few. Code measures the length. |
| [very-long-skill-description](smells/very-long-skill-description.md) | all | code, agent | A skill description is longer than the reference length, or it holds rules that the agent needs only after it opens the skill. Code measures the length. |
| [very-long-tool-description](smells/very-long-tool-description.md) | tools | code | The description of an attached tool is longer than the reference length. A code detector measures each tool, and each detection names the tool. |
<!-- END GENERATED: smells-detail -->
