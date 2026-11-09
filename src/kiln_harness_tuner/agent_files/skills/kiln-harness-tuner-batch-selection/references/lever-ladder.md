# Lever ladder — what to try first, per failure class

> **Evidence rule.** The evidence column names the MECHANISM that was seen and
> the KIND of outcome it produced. It cites nothing: no task, no run id, no
> measured number, no model name. Every planner reads this file, and a planner
> that can recall a task's answer will recall it instead of measuring it. The
> numbers live in each run's report, which agents cannot read.

Use it in Step 1 (candidate generation) BEFORE the economics: name the failure
class from the trace (the analyzer's `findings` name the decision point), take
the first lever in its row, and only move down the row when the arm before it
was measured. The planner's input carries `lever_attempts` (issue → technique →
arms ever opened on that pair, ANY outcome: promoted, refuted, failed on infra)
so the second rule below starts from a count, not a memory search. The count
does not know which arms failed: read the experiment verdicts for that.

## The rule above the table

**When the failure is mechanical, change the surface the model touches
before you change the words it reads.** Instruction text is the cheapest
arm to BUILD and the most expensive to LEARN FROM: on one failure class,
five instruction-text arms in a row won on train and every one lost at val,
and the tool-surface wrapper was the first arm to hold. A wrapper that
normalizes inputs, absorbs pagination, or returns an error the model can act
on removes the failure instead of asking the model to avoid it. When a tool
takes over a step, the same arm deletes the prompt text that asked the
model to do that step.

**Everything the baseline attaches is in the action space.** Its prompt,
each attached skill, each tool and its description, its parameters and its
model are all config. An inherited skill is not a fixed input that new
rules must work around: a skill is immutable as an OBJECT, so changing it
means a new version (export, rewrite, install under a new name, swap it in
on the new run config), and that is an ordinary `skill` arm. A tool's
description is fixed in the same way: a new description is a new tool — a
clone of a code tool with the new description, or a wrapper of any tool
(MCP and built-in tools included).

## The table

| Failure class, as seen in the trace | Try first | Then | Do not | Evidence |
|---|---|---|---|---|
| Output contract missing or wrong (no closing block, wrong shape, bad escaping) | One prompt line stating the contract verbatim | Add the edge rule the residuals show (escaping, empty case) | Skills, tools, model swaps | One prompt line stating the contract turned a near-total failure into near-total compliance in a single arm; the residual was an escaping edge case, closed by one more line |
| Answers ungrounded, the knowledge is absent from context | Attach the source (RAG tool or skill) | Scope discipline in the prompt ("answer only what the source covers") | Model swap, closed-list qualifiers | Attaching the source moved it, and scope discipline moved it again; on the same issue a model swap measured worse than the baseline and a closed-list qualifier gave back most of the gain |
| Scope or refusal behaviour (off-topic, over-refusal) | A decline-rules paragraph | Rebalance for over-refusal from the residuals | Decline-by-topic lists | A decline-rules paragraph promoted on a clear margin; a decline-by-topic list measured worse than no list — it taught the model which topics to refuse and broke the general guard |
| The model fails BEFORE or WITH a tool call (a preamble, a narration riding the call) | A new description for the tool (a code-tool clone, or a wrapper) — the description is the one text read at that decision point | A directive field in the tool RESPONSE for the post-tool reply | Prompt, skill body, skill description | Five instruction-text arms on one such failure all lost at val; the wrapper (tool description + a directive in the response) was the first to survive it |
| The model never loads a skill resource, or guesses a path | The skill DESCRIPTION with the full `references/` path index and "no other paths exist" | A prompt rule | A longer SKILL.md body | A ladder of arms against one skill-read failure: the full path index in the DESCRIPTION moved it furthest by a wide margin, and moved it further than the same contract written into the prompt |
| The model floods itself (schema fetch, list results) or loops one call per item | A wrapper: minify / preview-and-cap / batch with per-item errors | One prompt line to prefer the wrapper | Prompt alone | A minifier cut an oversized tool response several-fold; a preview wrapper plus a follow-up query tool replaced list floods, and the model reached for the follow-up tool in most conversations |
| The model malforms calls (wrong case, wrong id form, body on GET, misspelled tool name) | A wrapper that normalizes: case-insensitive enums, display-number → id, trimmed strings, and errors that name the valid values | Rename the tool so the model cannot misspell it | A prompt list of formatting rules | Filter-syntax errors and invented tool names (a misspelling of the tool's own name) recur across build traces; the normalizing-wrapper class is what closes them |
| The model gets lost in a long prompt or a monolithic skill | Progressive disclosure: a short router body, one `references/` file per topic, the index in the description, and a contents line per file that names what it contains — its key terms, the words a question would use — not only its title | Trim the prompt to the router | Rewriting the long text in place | Progressive disclosure holds even on a skill tree of hundreds of files. The trap: a router that narrates "load X first" couples call order, and one such bundle cost points on an eval it was not aimed at |
| A long reference document sits behind a lookup tool (paged, searched or by section), and the model picks the wrong part or never looks | Move the document into a skill: a contents file that names what each section contains (with its key terms), and one `references/` file per section; drop the lookup tool on the same arm | A keyword contents file in front of the existing tool | Prompt rules on which page or section to choose | Side by side, contents plus section files beat contents plus the lookup tool; prompt rules on choosing a section did not hold |
| The failure comes from an inherited skill — it is wrong, stale, monolithic, contradicts the task, or carries data shaped like test cases | Rewrite the skill as a new version: split, merge, restructure or scrub it (`references/skill-authoring.md`, "Rewriting an inherited skill") | Progressive disclosure, as in the row above | Prompt rules added around a skill you could change | Agents left inherited skills untouched and stacked prompt rules around them; rewriting the skill itself moved the eval those rules had not, and a scrub found data in inherited skills that no build had added |
| Many narrow tools, or generic tools with no rules: the model picks the wrong one, or breaks a rule the product enforces | Consolidate into a few general tools that carry the policy inside: the permission check, guards and defaults, from the product's own rules | Better descriptions, on code-tool clones or wrappers of the existing tools | A prompt list of which tool to use when | Unproven as a separate lever: a consolidated surface carried a large lift, but together with other changes. Plan it as a causal arm (this change alone) before you build on it |
| Baseline at ceiling, or judge fires that are false | `measure_issue_baseline`, then adjudicate the fires | Grow or re-bait the eval | Any fix arm | A wave that spent three arms on evals already at ceiling closed all three WONT_FIX; and judges have been seen storing a 0 beside reasoning that ends in PASS |
| Provider parameters (thinking level, temperature) | **Thinking: start at the minimum viable level and ladder up one rung at a time, only when hard cases exist.** The baseline's level is rung 0. When the measured baseline shows hard cases (items that fail the same way on every prompt arm), plan ONE params arm with thinking raised one rung and `target_items` = those hard cases: they lead the arm's measurement, so its first chunk is the cheap probe ("does more reasoning fix them?") and the rest of the split is judged only if it does. Temperature: one arm at most, only with a trace-level reason | Once quality has plateaued (no arm moved a target eval for two waves), and only if the guidance cares about cost or latency, the ladder runs DOWN: one rung less thinking, measured; keep it if the target evals hold | Reading a probe's direction as quality: a `compare_to_parent` probe says the lever MOVES the behaviour (more or fewer reasoning chars), never that it helps or hurts — a moved lever earns the arm, it never retires it (a run has dropped thinking on that misreading). Exceptions: guidance that says not to touch thinking, or to optimise for latency or cost rather than quality — then that guidance is the ladder | `thinking_level` has measured completely inert on some providers — accepted, billed, and changing nothing; a temperature move measured null; and on judge-scored text tasks both parameters measured negative when raised without target items. Measure on THIS task, never assume |
| A tool-less, judge-scored generation task whose prompt is long (rubric-style cards, summaries, extractions) — no surface to change | A `probe`: does this model call a tool / read a skill under the config's output mode? Then the wildcard: progressive disclosure (short instruction + a skill the model reads on demand) if yes; a prompt-structure arm (mark must-includes before pruning, a per-property final step) if no | A pipeline step outside the model (a checker on the draft, a second pass) | Trusting that a tool or skill will be called, or that an output-mode change is free: a probe settles the first for cents; the second is a lever of its own and is measured as one | Measured twice on judge-scored text tasks: the probe was decisive and cheap, and the output-mode change was not free |

## Three rules that go with it

1. **Name the decision point.** The analyzer writes in `findings` whether
   the failing message came BEFORE, WITH, or AFTER a tool call, and which
   tool. The planner picks the surface the model reads at that moment:
   before or with a call → that tool's description (a new one is a
   code-tool clone or a wrapper); after → the tool's
   response or the prompt; when a skill is chosen → the skill description.
2. **Two failed text arms on one issue means change surface class.**
   "Text" is the `prompt` technique and a `skill` arm that only changes
   words (a description, a body); `lever_attempts["<issue>"]["prompt"]`
   plus the skill arms that were text edits is the count, and the
   experiment records say which of them failed. The third arm must be a
   tool lever or a structural skill change (a router with `references/`
   files), or the planner returns `loop_exit` for that issue with the
   reason.
3. **A skill bundle is measured against every active eval, not only its
   target.** A router that says "load X first" changes call order and can
   break another criterion.

## What "bake it into code" means, per wrapper

The build agent reaches for these before any prompt edit when the failure
is in the row above. Each is a complete template in
`kiln-harness-tuner-code-tool-authoring/references/wrappers/README.md` (its plumbing is
tested by `tests/test_wrapper_templates.py` against a stub of
`kiln.tools`; the subject-specific edits are dry-run with `test_code_tool`):

- **Normalize inputs.** Accept upper and lower case, trim whitespace, map
  aliases and display numbers to ids, coerce types, default optional
  fields. The model's own typos no longer cause errors.
- **Absorb protocol.** Fetch all pages up to a cap and return `total` plus
  a cursor; fold resolve-then-fetch into one call. (No template retries a
  transient error yet; add that inside the tool when a trace shows one.)
- **Return errors the model can act on.** Replace a raw 4xx body with
  "field X must be one of [...]", "record not found, nearest matches are
  [...]", "you passed a body with GET".
- **Deliver context on the tool surface.** A static context tool, or a
  directive field in a response, when the failure is that instruction text
  is not read at the moment it matters. A variant: return the derived facts
  the reply must state — counts, the filters applied, resolved dates and
  time zones — so the model copies them instead of computing them.

## Cost order, for the economics step

Prompt line < code-tool clone with a new description (an MCP or built-in
tool gets a new description only through a wrapper) < tool_config (attach or drop) < flat
skill < wrapper tool < skill bundle (a new one, or a rewrite of an inherited
one) < new tool < tool consolidation < model swap. Build cost is
not the whole cost: a text arm that cannot win at val is the most expensive
arm there is.
