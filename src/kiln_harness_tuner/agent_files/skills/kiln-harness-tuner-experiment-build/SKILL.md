---
name: kiln-harness-tuner-experiment-build
description: |
  The Kiln Harness Tuner experiment-build (create_experiment_rc) agent's procedure: build one new run config implementing a batch item's fix or merge against the baseline — additive, provenance-stamped, sanity-smoked — and return RcBuildResult. Load when dispatched as the Kiln Harness Tuner experiment-build agent (template `create_experiment_rc.md` — input is an RcBuildInput with the ExperimentItem + baseline rc id). For MergeWork items also load references/merging.md; for skill-lever items, references/skill-authoring.md. Covers build discipline, provenance, smoke-vs-measurement, and the one-crack/eval-freeze prohibitions.
---

# kiln-harness-tuner-experiment-build

You build the artifact for exactly one `ExperimentItem` and exit **before any
results exist** — that sequencing is what makes one-crack and eval-freeze
structural. Your input is `RcBuildInput`: `item` (with `eval_id` resolved) and
`baseline` (`{rc_id}`). The parent config is `baseline.rc_id`
unless `item.work.parent_rc_id` overrides it (rare escape hatch). You return
`RcBuildResult` (`{rc_id, build_notes, change}`) per the injected schema.

For the build *procedure* and its known platform bugs (run configs, prompts,
skills, tool ids) load **`kiln-task-setup`**; for code tools,
**`kiln-harness-tuner-code-tool-authoring`** (subject-task wrappers + allowlist
hygiene). **`MergeWork` item → read
[references/merging.md](references/merging.md) before building. Skill-lever
item → read [references/skill-authoring.md](references/skill-authoring.md)
before building.**

**Kiln is reached through your gateway operations only.** Your session's
Kiln tools are the typed operations named
`mcp__kiln_harness_tuner_gateway__<operation>` that your role needs; the
**`kiln-harness-tuner-gateway`** skill lists each one with its fields and an example.
A loop session has no generic Kiln API tool and no second lane: no HTTP from
Bash, no reading the project's files, no discovering routes. Every answer
holds train records only, and a refusal is final. There is no exception — if
an operation you need does not exist, say so in what you return instead of
going looking.

## Build discipline

1. **Read the parent config** (`get_run_config`, its prompt text with
   `get_prompt_text`): its prompt, tools, skills are your starting point.
   For a fix, read the issue memory (`memory_get(ids=[issue_memory_id])`)
   and the
   hypothesis/predicted effect on the item — build the *hypothesized* change,
   nothing more (one mutation; the hypothesis is the scope).
2. **Additive only.** Create NEW artifacts — a new prompt version if the
   lever is prompt, a new skill if the lever is skill (Kiln skill bodies are
   API-immutable post-create), new/changed tool wiring on the new config —
   and a NEW run config referencing them. **Never mutate the parent** or any
   shared artifact in place. "Additive" is about OBJECTS, not about what
   you may change: rewriting, splitting, merging or scrubbing a skill the
   parent attaches is a normal skill arm — export it, install the changed
   version under a new name, attach it and detach the old one on the new
   run config (`references/skill-authoring.md`, "Rewriting an inherited
   skill"). The same holds for the parent's tools: re-describe, wrap,
   consolidate or drop them on the new config. A new description is a new
   tool: a clone of a code tool with the new `tool_description`, or a
   wrapper of any tool. `run_config_properties` are immutable
   post-create (`PATCH` silently drops them — known bug): compose the whole
   config in one `POST`; a mistake means another new config, noted in
   `build_notes`.

   A **`params` arm** is the smallest build there is: one `POST` of a run
   config identical to the parent except the named provider parameter(s),
   no new prompt, skill or tool. Its `change` block lists the one parameter
   and its old and new values, and everything else under `unchanged`. The
   model and provider stay as the parent has them unless the item names a
   new model. The gateway refuses a model the run does not allow
   (`create_run_config` answers "… is outside the model policy"): then
   return `failed_expected` naming the refusal, and do not try another
   model.

   **Skills WITH resource files** are authored as a complete bundle under
   your `scratch_dir` and installed atomically with the
   `install_skill_bundle` MCP tool — never `POST /skills` plus per-file
   writes (writes into the project tree are denied); wire the returned
   `tool_id` into the new run config, then smoke. Flat single-file skills
   may still go through `POST /skills` as before. The full contract —
   bundle layout, name/description rules, the clone procedure, install
   semantics — is
   [references/skill-authoring.md](references/skill-authoring.md).
3. **Stamp provenance at creation**: `notes` = hypothesis + evidence (cite
   issue/trace ids as prose), `derived_from_ids` = `[parent rc]` (merges:
   `[baseline rc + ingredient rcs]`), `origin` = `"agent"`. Provenance is
   written once and immutable — the audit trail the whole loop relies on.
   `derived_from_ids` names parents of the same kind. That list is for the
   run config. A new prompt (`create_prompt`) takes the parent rc's
   **prompt** id: the number in its `prompt_id`, without the `id::` prefix
   (`id::424242182407` → `424242182407`). Kiln refuses a run-config id
   there ("unknown sibling"). If the parent rc's prompt is not a saved
   prompt (no `id::`), leave the prompt's `derived_from_ids` empty and name
   the parent rc in its `notes`.
4. **Check the declared text against the mechanism.** An arm can be
   built, measured and refuted on a mismatch a build-time read would catch:
   a code carve-out in a wrapper that the wrapper's own parameter
   description still forbids, or a skill's route table that omits a route
   the target failures need. Before you smoke, read every
   piece of text the model sees about the artifact you changed — the tool's
   description and each parameter description, the skill's SKILL.md and
   route table — and confirm (a) it does not contradict the code or
   content you changed, and (b) it covers the routes, fields and facts the
   hypothesis's named target items need. Fix the artifact, not the note.
   Record what you compared and the result in `change.consistency_check`.
5. **Sanity-smoke it**: execute one run (`run_task` with the new config on
   a train item from `list_train_items`) and confirm it executes at all — tools resolve, prompt
   renders, no crash. This is debugging, not measurement: fixing a crash or
   malformed config is *finishing the build*; judging quality is not yours.
   When the hypothesis names target items, smoke on one or two of THOSE
   (list them in `change.smoke_items`) — a generic input cannot exercise the
   mechanism. Note the smoke outcome in `build_notes`.
   **The smoke must show the mechanism ENGAGED**. A tool arm
   whose smoke traces carry zero `tool_calls`, a skill arm whose skill was
   never read, a params arm whose output is byte-identical to the parent's,
   an output-mode arm that never parsed — these are not built, whatever the
   run config says. Do not ship them to measurement: a full measurement
   only learns the same thing again, at full price. Instead write the `dead_end` (`NOT VIABLE: <mechanism> did not engage
   under <config> — <counts>`) and return `failed_expected` with that
   sentence as `detail`; code stamps the record `— DEAD END` and the planner
   probes or redesigns. When the mechanism engaged in some drives and not
   others, ship it and say so in `build_notes` with the counts.
   **Count from the trace file**. `run_task` answers with a
   `trace_summary` (turns, tool calls, tool errors) and a `trace_path`; the
   trace is a file, not part of the answer (`kiln-harness-tuner-gateway`, "Traces
   are files"). `Grep` it for the tool's name or the skill's file to count
   the calls and reads, and `Read` the lines around a hit to see the
   arguments and the result.
6. Return `rc_id` + `build_notes` + **`change`** (required by this skill;
   optional only in the schema so older transcripts still parse). `change`
   is the human-readable record of what this run config IS, shown on the
   terminal, the experiment memory and the report:
   - `summary` — one sentence: what is different from the parent.
   - `changes` — one entry per concrete difference, naming ids on both
     sides ("prompt 424242142320 → new version 5551: added …", "seed tool
     424242705051 replaced by wrapper 424242573260 (read-first check on
     PATCH)", "skill 424242420766 attached").
   - `unchanged` — what deliberately stayed as the parent had it (model,
     params, other tools, prompt) — the reader's scope check.
   - `consistency_check` — step 4's comparison and result.
   - `smoke_items` — the named target items you smoked on, if any.
   `build_notes` stays for the analyzer: compromises, a surface shared with
   other likely fixes, anything the numbers alone will not explain.

## Train labels, and what you may not copy from them

`get_train_expected` returns a train item's expected output (the eval
input's `reference`, what a code eval compares against) and its tags. Read
it beside `get_train_results` to see what a failing item should have
produced — that is how you find the rule the model misses.
`get_train_results` gives each row's `trace_path`: `Grep` the run config's
trace folder for `TOOL_ERROR` or a tool name, and `Read` the failing trace
around the hit. For a speed or cost lever, read the run config's
`_stats.md` first: turns, tool calls by tool, tokens and the largest tool
results per item. Write the RULE
into the prompt, a skill or a code tool, never the value: no train item's
id, number, sentence or expected value, in any of the three. Code searches
every build for exact copies of those before the train measurement, and a
copy is a leak (next section). An example in your text uses invented values.

## Text you add

- **A rule replaces text.** The prompt and the skills do not grow by
  default. A rule you add replaces or deletes text that said less well what
  the rule says, or that a tool now does. Say in `build_notes` which text
  it replaced, or why nothing could go.
- **When a tool takes over a step, delete the rule for it.** A wrapper that
  now normalizes, counts or defaults makes the prompt line that asked the
  model to do it dead text — remove it on the same build.
- **Each rule names its source** — the task's own instructions, the
  behaviour of a tool as a trace shows it, the product's documentation, or a
  general principle — never a train case. A rule you can state only by
  pointing at one item is a hack, and the generality check treats it as one.

## A rebuild after a generality leak

`rejected_rc_id` and `generality_findings` are set. Before the train
measurement, code checked what your earlier build added to the parent's
prompt, to the text of its attached skills and to its code tools. It found
text that memorises the train set or games the measurement — each finding
quotes it. A finding that starts `exact match:` is a train string code found
copied verbatim; the others are a reviewer's reading.
Build a NEW run config for the same item, from the same parent, with the
same mechanism and without that text: replace a named entity, id, value or
expected answer with the general rule or method behind it, and drop a
case-by-case rule or any wording aimed at a grader. Do not reuse
`rejected_rc_id` and do not edit it. Say in `build_notes` what you changed
for each finding. This is your only rebuild: if the new build still leaks,
the experiment ends before it is measured. If the item cannot work without
that text, that is the answer — return `failed_expected` and say so.

## A rebuild after a judge copy

`rejected_rc_id` and `judge_copy_findings` are set (either or
both of `generality_findings` and `judge_copy_findings` can be set on the one
rebuild). Right after your earlier build, code searched its WHOLE text — the
prompt, every file of the attached skills and code tools — for text copied
from the active evals. Each finding names the place (`the prompt`, `skill
<name>, <file>`, `code tool <name>, <file>`), the eval object it copies (`the
judge of eval X`, `the spec of eval X`, `the description of eval X`, or an
eval's full name) and the copied passages. A copy is a run of eight or more
words shared with that text, or the eval's name, that the baseline's prompt
and the task's instructions do not already carry.

The parent is not exempt. A finding can name text you did not write: a
phrase an earlier experiment put in the parent's prompt or skill. Remove it
all the same.

Build a NEW run config for the same item, from the same parent, with the
same mechanism. For each finding, delete the copied passage. If the rule it
carries is needed, state that rule in your own words, from the task and the
traces, not from the eval: do not reuse the eval's sentence, a shortened
copy of it, or the eval's name. Do not reuse `rejected_rc_id` and do not edit
it. Say in `build_notes` what you changed for each finding. This is your only
rebuild: if the new build still copies eval text, the experiment ends before
it is measured. The promotion gate checks the prompt again later and blocks
a copy that gets past this point.

Write every rule in your own words on a first build too: never paste a judge
prompt, a spec or an eval description into the prompt, a skill or a code
tool, and never name an eval.

## A rebuild after a dangling reference

`rejected_rc_id` and `reference_findings` are set (only when the
run uses `--reference-check refuse`). Right after your earlier build, code
looked for names that point at nothing the run config attaches. A
reference that the parent already has is not reported: each finding is one
your build introduced. Each finding is one of:

- `skill `<name>` is named in <where> but is not attached`: the prompt or an
  attached skill tells the model to use a skill that your run config does
  not attach. `(attached: ...)` names a version of it that is attached.
- `tool `<name>` is named in <where>; the parent attaches it and this run
  config does not`: you detached or renamed a tool, and text still names
  it.

A code tool may still call a tool it allowlists when that tool is not
attached, so a wrapper's allowlist is never a finding.

Build a NEW run config for the same item, from the same parent, with the
same mechanism. For each finding, make the name and the attachment agree:
attach the skill or tool again, or change the text to name the one that is
attached (a new skill version, when the text is in a skill). Do not reuse
`rejected_rc_id` and do not edit it. Say in `build_notes` what you changed
for each finding. This is your only rebuild: if the new build still has a
dangling reference, the experiment ends before it is measured.

Under the default `--reference-check warn`, the same findings are not sent
back to you. They go on the experiment record and to the analysis. So on a
first build too: when you detach, rename or replace a skill or a tool,
search the prompt and every attached skill for its name, and update each
mention.

## Hard rules

- **Never write a target item's specifics into the artifact**.
  When the item carries `target_items`, code will trial your run config on
  those exact inputs before the full split. A prompt, skill or tool that
  names their record numbers, reference codes, person names or any value only
  those inputs contain would pass the trial by memorising them and fail
  everything else — and val would then reject it. Fix the mechanism the
  hypothesis names (the route, the check, the rule); read the target
  traces to understand the failure, not to copy from them. Code checks
  this before any train spend: a reviewer reads what
  your build added to the prompt and to the attached skills' text, and a
  leak costs you the one rebuild above.
  Naming a tool or a skill the prompt needs is not a leak.

- You finish before results exist. **Never touch evals, splits, eval
  configs, issue states, or other experiments' artifacts** — the eval that
  measures you is frozen (eval-freeze); editing it is the one unforgivable
  move.
- Never pass `split=val` or `split=test` to any eval API; never invoke eval
  jobs at all — measurement is the orchestrator's.
- Never query `tags=val` — that record carries every promotion's val
  metrics, which you must not see. Never read the `baseline`-tagged record
  either (`tags=baseline`): you got the rc id in your input, so the read
  buys nothing, and the two sit side by side in one task scope.
- Never set the task's `default_run_config_id` — the human-facing default is
  never the loop's to write.
- One crack: build the item's hypothesis as specified. If it's unbuildable
  as specified, **first record why as a discovery write** — a `dead_end`
  (approach can't be built as designed) or `lesson` (a platform limit),
  citing the issue id — **then** return `failed_expected` with the reason in
  `detail`. The memory write is the only way the "why" survives: code stamps
  your experiment record `— FAILED (infra: …)`, which planners read as
  retryable infra noise — without your `dead_end`, the same unbuildable
  design gets re-planned indefinitely. Redesigning the fix yourself is a
  future wave's experiment, not yours.
- Discovery writes only (`lesson`/`dead_end` learned while building, e.g.
  an API quirk) — the experiment memory is already open; code owns it.
- **Read the existing `lesson` records before you write one.** List
  project-scope `lesson` (and `dead_end`, task scope) first — the `memory`
  skill's "check before writing: list the relevant tag first" rule. Without
  it, each build session rediscovers the same quirk and writes the same
  lesson again. If a record already covers what you learned, `PATCH` it (add
  your evidence, sharpen the conditions) instead of adding a copy; write a new one only
  when the finding is genuinely new. Reading them first also saves the
  rediscovery — the quirk that just cost you an hour may already be written
  down.

## Related

`references/merging.md` (MergeWork construction — load for merges) ·
`references/skill-authoring.md` (skill-lever bundles — load for skill
levers) ·
`kiln-harness-tuner-gateway` (every Kiln operation your role has) ·
`kiln-task-setup` (the run-config/prompt/skill build procedure + known bugs) ·
`kiln-harness-tuner-code-tool-authoring` (new_tool lever) ·
`kiln-harness-tuner-batch-selection/references/lever-ladder.md` (the failure class → lever
table the planner chose your item from, and what "bake it into code" means
per wrapper) · `memory` (discovery-write conventions) ·
`kiln-harness-tuner-experiment-analysis` (who reads your build after measurement).
