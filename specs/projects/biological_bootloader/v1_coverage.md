---
status: draft
---

# V1 Coverage: Biological Bootloader

This phase is special to this project. It comes after the architecture and
before the implementation plan. It decides which documents the first version
of the knowledge system holds, and what goes in the backlog.

**The rule for V1.** This project is groundwork. V1 holds:

1. everything the tuner already tells its agents (the lever ladder), moved
   into the new structure, so that no guidance is lost;
2. a small number of new documents, enough to use each component type and
   each part of each schema with real content;
3. what the smell detectors name.

Everything else goes in the backlog.

**Sources.** The lever ladder
(`kiln-harness-tuner-batch-selection/references/lever-ladder.md`), the
[project overview](./project_overview.md), the owner's
[notes](./knowledge_system_design.md), the
[research](./research/harness-optimization/summary.md) and
[kiln_gaps.md](./kiln_gaps.md). The owner's separate research report is not
yet included: see section 8.

The ids below are the file names. A title can differ.

## 1. Levers (6)

One for each thing the tuner changes today.

| Id | What it covers | Existing how-to it links |
|---|---|---|
| `prompt` | The system prompt: a new prompt object, chain-of-thought instructions | The build skill |
| `skill` | Skill bundles: install, a new version of an inherited skill, attach and drop | `experiment-build/references/skill-authoring.md` |
| `new_tool` | Code tools: a new tool, a clone with a new description, a wrapper of any tool | The code-tool authoring skill and its wrapper templates |
| `tool_config` | Which tools the agent has: attach and drop | The build skill |
| `params` | The model's settings: thinking level, temperature, top-p | The planner's screening section |
| `model` | The model and the provider | The planner's screening section, the model policy |

`model` is a new name. Today a model swap has no fixed short name in the
`technique` field.

## 2. Smells (19)

"Row" is the row of the lever ladder's table that the smell comes from.

| Id | Recognised by | Detected by | Source |
|---|---|---|---|
| `output-contract-not-met` | The output has the wrong shape, no closing block, or bad escaping | agent | Row 1 |
| `answers-not-grounded` | The answer needs knowledge that is not in the context | agent | Row 2 |
| `wrong-scope-or-refusal` | Off-topic answers, or refusals of valid requests | agent | Row 3 |
| `failure-at-a-tool-call` | The failing message comes before or with a tool call | agent | Row 4 |
| `skill-resource-not-read` | The agent never loads a skill file, or guesses a path | agent | Row 5 |
| `tool-results-flood-the-context` | A very large tool result, or one call for each item | agent | Row 6, overview 6.3 |
| `malformed-tool-calls` | Wrong case, wrong id form, a misspelled tool name | agent | Row 7 |
| `lost-in-long-instructions` | A long prompt or a one-file skill, and the agent misses a part | agent | Row 8 |
| `lookup-tool-over-a-long-document` | A document behind a paged or search tool, and the agent picks the wrong part | agent | Row 9 |
| `faulty-inherited-skill` | An attached skill is wrong, stale, or holds data shaped like test cases | agent | Row 10 |
| `too-many-narrow-tools` | The agent picks the wrong tool, or breaks a rule of the product | agent | Row 11, research |
| `tool-search` | The agent must guess that a tool exists and search for it | agent | Overview 6.3 |
| `saturated-eval` | The baseline is at the ceiling: the train rows cannot show a win | code, agent | Row 12 |
| `unreliable-judge` | A judge's score disagrees with its own reasoning | agent | Row 12 |
| `noisy-eval` | The same config gives a different result on a second run | code | The noise check |
| `train-wins-rejected-at-val` | Fixes that win on train keep losing at the gate | agent | Ladder rule 2, owner's notes |
| `very-long-prompt` | The system prompt is over the reference length | code | Overview 9 |
| `very-long-tool-description` | A tool description is over the reference length | code | Overview 9 |
| `very-long-skill-description` | A skill description is over the reference length | code | Architecture |

## 3. Techniques (20)

| Id | Levers | Cost | Source |
|---|---|---|---|
| `state-the-output-contract` | prompt | low | Row 1 |
| `attach-the-knowledge-source` | tool_config, skill | medium | Row 2 |
| `answer-only-from-the-source` | prompt | low | Row 2 |
| `decline-rules-paragraph` | prompt | low | Row 3 |
| `new-tool-description` | new_tool | low | Row 4 |
| `directive-in-the-tool-response` | new_tool | medium | Row 4, wrappers |
| `path-index-in-the-skill-description` | skill | low | Row 5 |
| `cap-and-preview-wrapper` | new_tool | medium | Row 6, wrappers |
| `normalize-inputs-wrapper` | new_tool | medium | Row 7, wrappers |
| `errors-the-agent-can-act-on` | new_tool | medium | Wrappers, owner (2026-11-12) |
| `long-prompt-into-a-skill` | skill, prompt | medium | Row 8, overview 6.2 |
| `document-into-a-skill` | skill, tool_config | medium | Row 9 |
| `rewrite-an-inherited-skill` | skill | medium | Row 10 |
| `consolidate-tools` | new_tool, tool_config | high | Row 11 |
| `remove-or-scope-tools` | tool_config | low | Owner (2026-11-12), research |
| `thinking-level-ladder` | params | low | Row 13 |
| `structure-a-long-prompt` | prompt | low | Row 14 |
| `roll-up-tool-for-context` | new_tool, prompt | medium | Overview 7.5 |
| `guide-skill-for-a-tool-set` | skill | medium | Overview 6.3 and 10 |
| `model-swap-inside-a-class` | model | high | Overview 6.4 |

Notes:

- The first seventeen carry what the lever ladder says today. The last
  three are new, and each one uses a part of the schema that the others do
  not: a technique of two levers that is one set of changes, a technique
  with sub-files (the tips for a query tool), and a technique of high cost
  that is left out on a small budget.
- The evidence column of the lever ladder becomes the priors of these
  techniques, with the source "own experiment". That text is already public
  in this repository, so it needs no new approval.
- `guide-skill-for-a-tool-set` has sub-files for the owner's tips: sample
  queries at each level, the schema as leaf files, a section on timestamps.

## 4. Playbooks (5)

| Id | Kind | What it holds | Source |
|---|---|---|---|
| `default-order-of-work` | Order | Lock a model. Fix what blocks other work first. Then the tool surface, then missing context, then the wording, then the settings. The model scan comes last. A budget section: what to leave out on a small budget, and what a large budget opens. | Overview 5.1, 6.4 and 10; the ladder's cost order; research (the draft stack rank) |
| `change-the-surface-not-the-words` | Response to a smell | When a failure is mechanical, or two text arms failed on one issue: change what the model touches before the words it reads. | The ladder's "rule above the table" and rule 2 |
| `dealing-with-a-saturated-eval` | Response to a smell | Measure the issue on the baseline, judge the fires, then grow or change the eval. No fix arm. | Row 12 |
| `tool-less-generation-task` | Situation | A probe first. Then progressive disclosure, or a prompt-structure arm. Then a step outside the model. | Row 14 |
| `cheaper-or-faster` | User goal | The order under the speed and cost objectives: another provider of the same model, less thinking, a shorter prompt, fewer output tokens. | The planner's skill, speed and cost section |

## 5. Where each part of the lever ladder goes

| Part of the ladder | Goes to |
|---|---|
| Rows 1 to 12 and 14 of the table | The smells and techniques above. "Try first" and "Then" become the smell's remedies, in order. "Do not" becomes "When not to use" of the techniques it names, and a line in the smell. |
| Row 13 (provider parameters) | `thinking-level-ladder`. It is not a smell. |
| The evidence column | Priors |
| The evidence rule at the top | Replaced by the content rules of the functional spec, section 6 |
| "The rule above the table", first paragraph | `change-the-surface-not-the-words` |
| "Everything the baseline attaches is in the action space" | The levers `skill` and `new_tool` (the rules of the object) |
| Rule 1, name the decision point | Stays in the planner's and the analyzer's skills: it is process. `failure-at-a-tool-call` links to it. |
| Rule 2, two failed text arms | `change-the-surface-not-the-words`. The count from `lever_attempts` stays in the planner's skill. |
| Rule 3, a skill bundle is measured against every eval | "Check that it took effect" of `long-prompt-into-a-skill` |
| "What bake it into code means" | The lever `new_tool`, which links to the wrapper templates |
| "Cost order" | The `cost` of each technique, and the budget section of `default-order-of-work` |

The implementation phase that moves the ladder ends with a check of this
table against the removed file, line by line.

## 6. Entry files and other content

As the architecture gives them: `planner.md`, `builder.md`, `smells.md`,
`report.md`, `technique-index.md`, and `SKILL.md`. The planner's entry file
tells the planner to read `default-order-of-work` first.

## 7. The backlog

The first contents of `specs/backlog.md`. The owners rank them. The order
here is a first proposal, by expected value against cost.

### Ranked

| Title | Kind | Where |
|---|---|---|
| Input transforms as a lever, with its techniques | gap | tuner |
| Sub-agents: create and change, with three techniques (smarter model, specialist, cheap reader) | gap | tuner |
| Model and provider information ([project](../model-provider-info/project_overview.md)) | code | tuner, kiln |
| Promotion on a tie ([project](../tie-promotion/project_overview.md)) | code | tuner |
| Single-turn prompt caching in Kiln (the measured cost can be wrong) | gap | kiln |
| Task shape visible to loop agents (`turn_mode`, model capability flags) | gap | tuner |
| RAG: create and tune, with RAG-against-skill playbook | gap | tuner |
| Structured-output mode as a lever | gap | tuner |
| Decision models (Jev) for classifier-shaped tasks | technique | knowledge |
| Detector: very old model (needs model information) | detector | tuner |
| Detector: a tool result of very many tokens, from trace stats | detector | tuner |
| Detectors on the process of a run (train wins rejected at val, repeated failed arms) | detector | tuner |
| OpenEnv worlds: read and change; smell for evals against a live server | gap | tuner, kiln |

### Unranked

Techniques: skill into the prompt (the reverse direction, for a cached
prompt); tool-result clearing; the advisor pattern; confidence cascades;
dynamic few-shot; a verbosity option on a tool; tool-use examples; opaque
ids to readable names; namespaced tool names; a sub-agent return contract;
reranking for RAG; fine-tuning as a late step; speed with hardware
acceleration (Groq, Cerebras); on-device models; on-prem open-weight models;
prompt lines left from an older model.

Playbooks: open models on-prem; an eval near zero with traces that suggest
no fix; high variance; working with a restriction (zero data retention, a
provider list); a stateful agent with no environment.

Smells: too many tools or tools that overlap (a count); an almost empty
skill; very many skills; repeated calls and loops; unused tools and skills;
an old model; evals that do not cover the product; evals written against a
staging server; a low cache hit rate.

Levers and gaps: the rest of [kiln_gaps.md](./kiln_gaps.md) (compaction,
mid-conversation switching, run config fields, OpenRouter routing controls,
the `llm` tool in code tools, prompt generators, multi-turn eval inputs,
code files in skills, other decision-model providers).

Code: the rename of the code field `technique` to `lever`; a baseline
review that can close a smell issue that a new baseline no longer shows.

## 8. Still to do in this phase

The owner's separate research report (how other systems and practitioners
improve an agent, and how they order the work) is not yet read. It is not
committed to this repository. When the owner supplies it:

- its ideas become entries in the backlog, in section 7;
- an idea that the owner rates above something in sections 2 to 4 can
  replace it or join it;
- only the distilled entries are written here.
