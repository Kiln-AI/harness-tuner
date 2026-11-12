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
| [tool-search](smells/tool-search.md) | tools | agent | The agent must guess that a tool exists, decide to search for it, and find the right search terms, before it can call it. |
<!-- END GENERATED: smells-detail -->
