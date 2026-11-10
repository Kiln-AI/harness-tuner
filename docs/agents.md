# The agents

Ten agent roles. Each is one agent-CLI session (`claude -p` by default, or
the CLI `--agent-cli` names), dispatched fresh, given a
code-assembled input, and required to return one typed object. None of them
holds context from a previous session: durable state lives in Kiln memory
records, which is why a crashed run loses spend but not knowledge.

This document is written so that a reader whose run went wrong can tell which
agent to look at. Each section ends with a **Blame** paragraph naming what that
agent's failure looks like from the outside.

The prompt templates are in [`src/kiln_harness_tuner/agents/`](../src/kiln_harness_tuner/agents/)
and the procedures are in
[`src/kiln_harness_tuner/agent_files/skills/`](../src/kiln_harness_tuner/agent_files/skills/).
A template is deliberately thin — it names the skill to load, states the
role's hard rules, and injects the input as JSON.

| Agent | Skill | Wall-clock cap | A failure costs |
|---|---|---|---|
| `describe_eval` | none, by design | 10 min | the run — a description that fails twice is fatal |
| `eval_build` (experimental, off by default) | none, by design | 30 min | one item |
| `batch_plan` | `kiln-harness-tuner-batch-selection` | 15 min | the run |
| `probe` | `kiln-harness-tuner-probe` | 15 min | one item |
| `create_experiment_rc` | `kiln-harness-tuner-experiment-build` | 30 min | one item |
| `analyze_experiment_results` | `kiln-harness-tuner-experiment-analysis` | 30 min | one item |
| `promote_best` | `kiln-harness-tuner-promote` | 15 min | this wave's promotion |
| `report_writer` | `kiln-harness-tuner-report` | 15 min | nothing — code renders a fallback report |
| `integrity_review` | none; no tools | 10 min | nothing — a failed review is itself a flag |
| `generality_review` | none; no tools | 10 min | nothing — the item is measured, and its record says the check did not run |

The caps are `AGENT_TIMEOUTS_SECS` in
[`dispatch/claude_cli.py`](../src/kiln_harness_tuner/dispatch/claude_cli.py). One more
entry, `preflight`, is the lane check rather than an agent role.

The other six skills belong to no single agent section above.
`kiln-harness-tuner-gateway` (the gateway operations) and `memory` (the record
conventions) are cross-referenced by several roles; loop agents do not load
the `kiln-api` REST reference. `experiment-costing` is the **planner's**:
it weighs what a wave will cost before committing to it, and no other role
decides to spend. `kiln-task-setup` (run-config and prompt composition) and
`kiln-harness-tuner-code-tool-authoring` are the **builder's**, loaded when the item's
lever calls for them.

---

## `describe_eval`

**Dispatched when** setup finds an active eval with no issue memory bound to
it, or finds one whose source fingerprint has changed. One session per eval, in
parallel, bounded. This is a one-time cost per eval: a warm run dispatches
none.

**Given** `DescribeEvalInput` — `eval_id`, `eval_name`, and `spec_id` when the
eval has a spec. Ids only. The agent reads the eval, its **current** config and
the spec itself, through its gateway operations, with a named collection
fallback for each single-resource read.

**Must return** `EvalDescription`: `overview`, one line stating the **problem**
the eval catches — code prefixes it `UNMEASURED: issue: `, so it is written as
the tail of that sentence — and `description`, a short paragraph a planner can
act on without re-reading the rubric.

**Skill:** none. The job is two reads and a paragraph, and the template carries
its whole procedure.

**Blame.** A cold run that dies in pre-flight with a description failure. The
rule is deliberate: seeding a requirement set that is missing an entry means
paying the loop's cost twice, and this session is the cheapest thing the loop
dispatches. The template degrades rather than guessing — it reports failure
only after both a single-resource route *and* its collection fallback have
failed.

---

## `eval_build`

**Experimental, and off by default.** It is not well tested, and an eval it
builds can have no val split. The loop dispatches it only with
`--experimental-eval-building`. While it is off, the planner is told so, and
code drops a planned `eval_only` item or a fix with `needs_eval: true`
before its wave runs, with one log line for each. The other items of the
wave run. The report names each issue that has no eval, so that you can
create its eval in the Kiln app.

**Dispatched when** eval building is on and a planned item names an issue
that has no eval, either as an `eval_only` item or as the first step of an
experiment item's fix work.

**Given** `EvalBuildInput` — `issue_memory_id` and the baseline run config.
**That is the whole input, deliberately.** The hypothesis and the technique do
not cross this boundary: the eval must measure the *issue*, not the planned
fix, and an agent that knew the fix would be authoring the test for it.

**Must return** `EvalBuildReport` — the eval id, its config id and type, the
case count, `reproduced`, a `not_reproduced_reason` when it is false, a
baseline headline, and a priority set at creation. `reproduced: false` is a
**valid and useful outcome**: code marks the issue `WONT_FIX:` with the reason
and the item ends without a build.

The eval it builds must carry pre-sized train, val and test splits — train
powering paired verdicts, val powering the promotion gate — and stored baseline
results. It is frozen at handoff.

**Skill:** none. The template sets the boundary and the rules. The agent
decides how to build the eval.

**Blame.** An item that consumed a long session and produced nothing
measurable, or a new eval whose splits are the wrong size, which shows up two
waves later as verdicts nobody can act on.

---

## `batch_plan`

**Dispatched** once per wave (`phase` `plan`). It is dispatched again in the
same wave when the first plan asked for a probe phase (`replan`, with the
probe answers), and after each screening round it asked for (`rescreen`,
with the screening table).

**Given** `BatchPlanInput`: the kickoff `guidance` verbatim; the baseline run
config id; `should_wrap_up`, code's judgment that this is the last wave (the time limit
has passed, or the budget fits one more wave only) rather than the clock;
`eval_sizes`, the count of train rows a comparison can actually pair per eval;
`lever_attempts`, issue → technique → arms opened; `max_concurrent_agents` and
`lever_costs`, so the plan is sized to what can run at once; `probe_results`,
every probe answered so far this run, newest last; `prior_rejected_summary`,
the one thing carried in context across waves; `phase` (`plan`, `replan` or
`rescreen`); `objective` (`quality`, `speed` or `cost`),
`baseline_latency` (the baseline's median, p90 and spread on its stored train
drives), `model_policy` (the rules, or null), `allowed_models` (what the
server offers, filtered by the policy) and, under `speed`, `speed_rule` — the
exact promotion rule in words, composed by code from the constants it
applies. The analyst receives the same `speed_rule` in `AnalysisInput`.
The screening fields — `screening_results` (this round's table),
`screen_rounds_left`, `prior_screening_results`, `settled_survivors` and
`screening_notes` — carry the screening rounds of this wave and of earlier
ones. `default_stop_rules`, `residual_misses` and `measurement_prices` are
what the measurement engine needs to price and stop a candidate, and
`baseline_cost` is the baseline's cost per run under `cost`.
It also gets `train_detectability` — per active (eval,
score), the baseline's train items with room to improve and the fewest wins
a significant result needs — and, with `--noise-check`, `train_noise`. Both
are train-only ([architecture.md](architecture.md) sections 5.1 and 5.2).
`eval_building` says if the plan may build a new eval: `false` unless the
run has `--experimental-eval-building`. While it is `false`, the template
and the skill tell the planner to give each fix arm an eval that exists,
and to plan no item that builds an eval.

Everything else it reads for itself: issues, dead ends, experiment verdicts and
open constraints, plus the baseline run config's own artifact.

**Must return** `BatchPlan` — `items`, a `rejected_summary` that carries
repeat-deferral counts forward, `open_questions`, and optionally `probe_phase`,
`screen` (a screening round instead of a wave: no items, a list of variants
code measures on the screening subset) or `loop_exit`. Two validators hold shapes the schema alone would not:
`loop_exit` and `items` are mutually exclusive, because the loop honours
`loop_exit` by terminating *instead of* running the wave; and a probe-phase
plan carries probes only.

**Skill:** `kiln-harness-tuner-batch-selection` — candidate generation, value and cost
weighing, batch cohesion, eval economics and detectability, anti-starvation,
merge timing, the wrap-up wave, and the lever ladder in
`references/lever-ladder.md`.

**Blame.** The most consequential agent in the loop, and the one whose failures
are least obvious. A plan that spends every slot on one surface; a plan that
exits while unpromoted wins are still pooled; a plan that predicts an effect
larger than the eval has headroom for, which later makes a real improvement
read as a miss. A planner hard-failure exits the run.

---

## `probe`

**Dispatched when** a plan carries a probe item with no `check`. A probe item
*with* a `check` is answered by code and never reaches an agent.

**Given** `ProbeInput` — the probe item (its question, its method, its
`max_drives` cap), the baseline run config to fork from, and `train_inputs`:
train items code read from disk for it. The project files are not readable
in its session, so these are the only items it can drive.

**Must return** `ProbeResult`: `answer`, one of `yes`, `no`, `unclear`;
`evidence`, the trace-level fact with counts; `drives`; and
`ephemeral_rc_ids`, every config it created, named `PROBE <item id>: …` so
nobody forks one by mistake. `unclear` is a real answer — the drives did not
settle it.

**Skill:** `kiln-harness-tuner-probe`.

**Blame.** An answer the traces do not support: a `yes` the evidence does not
show, a `no` from drives that errored, or an `unclear` reported as either.
Check the `evidence` field — it must carry the counts the answer rests on, and
an answer that cannot cite them is the probe's fault. A probe that creates a
run config and does not report it in `ephemeral_rc_ids` leaves litter a later
planner may fork.

**What is not the probe's fault:** a `yes` that a later arm fails to convert
into a score. A probe answers "does the lever move the behaviour"; the arm
answers "does it score", and the whole point of spending a minute on the first
question is that the second one can still come back no.

---

## `create_experiment_rc`

**Dispatched** once per `experiment` item, after the eval is resolved and
the experiment memory record is open.

**Given** `RcBuildInput` — the `ExperimentItem` with its hypothesis, predicted
effect, lever and resolved eval id, and the baseline run config it branches
from.

**Must return** `RcBuildResult` — the new run config's id, build notes, and an
`RcChange` block: a one-sentence summary, the concrete changes one per line,
what deliberately stayed as the parent had it, the declared-text consistency
check, and the named target items it was smoked on. That block is the
one-screen explanation that the plain log lines, the experiment record and the
report all show. The run screen's log shows its one-sentence summary.

The build is **additive**: a new run config, never a mutation of the parent.
Provenance is stamped at creation. The smoke is for *execution*, not
measurement.

**The agent finishes before any results exist.** That is what makes one-crack
structural rather than a rule.

It is dispatched a second time for the same item in one case only: the
generality review found a leak, or the judge-copy check found
eval text in the build's prompt, skills or code tools. Then `RcBuildInput`
also carries `rejected_rc_id` and `generality_findings` and/or
`judge_copy_findings`, and the agent builds the item again without the
flagged text, with each rule in its own words.

**Skill:** `kiln-harness-tuner-experiment-build`, plus `references/merging.md` for a
merge item, `references/skill-authoring.md` for a skill lever, and
`kiln-harness-tuner-code-tool-authoring` for a new tool or a tool wrapper.

**Blame.** An arm that measures something other than its hypothesis. The usual
shapes: a change that carried a second change with it, so the verdict cannot be
attributed; a wrapper whose declared parameter text does not match its code, so
the model calls it wrongly; a merge that combined two arms that were meant to
be mutually exclusive. The `RcChange` block's `unchanged` list is the reader's
scope check, and it is there because this failure is hard to see otherwise.

---

## `analyze_experiment_results`

**Dispatched** once per `experiment` item, after code has measured it.

**Given** `AnalysisInput` — the item, the candidate run config id, the
`AnalysisBundle` (the label-free numbers table plus every named hole in it),
and its own experiment memory id.

**Must return** `ExperimentResult`:

- `successful` — did this arm meet **its own** targeted goals on train. With
  the item's promotion-candidate intent, this is what gates the val pass.
- `veto_reason` when `successful` is false and the code rule would have passed
  it. One reason is special: `prediction_miss` — the primary target improved,
  but by less than the item predicted — is caution rather than a verdict, and
  code restores `successful`. A real improvement must not be thrown away for
  missing a prediction the eval had no headroom for.
- `findings`, and the frozen `memory_overview` / `memory_content` formats that
  code PATCHes into the record. The overview's row format is a string contract
  with exactly one owner: the skill.
- `per_ingredient_outcomes` — required for a merge, since promotion's
  fixed-issue writes are sourced from those rows.

It reads numbers. It never re-derives them, and no arithmetic happens in its
context.

**Skill:** `kiln-harness-tuner-experiment-analysis` — reading delta / n / p / interval
rows, the caution and power judgment, weighing measurement holes, reading a
screen-out row correctly, merge attribution, and the frozen formats.

**Blame.** A verdict that does not match its own numbers. Two shapes to look
for: a well-powered null read as a failure of the lever rather than as a signal
to enlarge the eval, and a screen-out row read as "the change hurt" when it
means "attribution was never measured". Both send the next planner in the wrong
direction, and both are visible in the experiment record.

---

## `promote_best`

**Dispatched** at the promotion gate when there are two or more candidates, or
on every gate with `--gate-agent always`. One candidate is decided by a code
rule.

**Given** `PromotionInput` — one `CandidateValTable` per candidate (rows are
candidate against the *current* baseline on `split=val`, computed by code),
each carrying its own `errors`, `skipped` and `ineligible` fields; the baseline
reference; and the kickoff guidance verbatim.

**Must return** `PromotionDecision` — `winner_rc_id` and
`winner_experiment_memory_id`, or both null, which is a valid outcome; a
`rationale` carrying the val metrics summary, which lands verbatim in the `val`
stats record; `regressed_issues`, status intent only, with code composing the
metric-free lines; and `blocked_on`, the open constraint id when the gate
escalates instead of judging.

Two rules the input enforces. A candidate listed as `ineligible` cannot win —
code blocks it regardless of the decision. And the agent may not claim "no
regressions" for an eval that appears in `errors`, because that eval was not
measured.

**Skill:** `kiln-harness-tuner-promote`. This is the one mid-loop agent allowed to read
the `baseline`- and `val`-tagged memory records.

**Blame.** The wrong run config becomes the baseline, and every later wave
branches from it. Look for a rationale that claims a clean sweep over a table
with holes in it, or an escalation on a constraint the guidance already
answered — the guidance is in the input precisely so that cannot happen.

---

## `report_writer`

**Dispatched** once, at exit, on every path including the failure paths.

**Given** `ReportInput`, which embeds the full `RunStatus`: every wave, every
item, every promotion, the spend and the exit reason.

**Must return** `ReportMarkdown` — the report as markdown. **Code writes the
file** at `reports/kiln-harness-tuner-<run_id>.md` in the state folder
([install.md](install.md#where-the-tuner-keeps-its-files)) and appends a
code-computed spend appendix. A format-critical file is never written by an
agent. Code also appends why each arm stopped, and a trade-off table of the
screened candidates ([architecture.md](architecture.md) section 11). The agent
points at these tables and does not restate them.

It may read exit-time memory, including both val-carrying records, and it must
label their numbers val-grade.

**Skill:** `kiln-harness-tuner-report`.

**Blame.** Least costly to get wrong: a hard failure still ships a report, code
rendered from `RunStatus`. The failure that matters is a report that restates
spend or the clock instead of narrating them — those numbers are code's, and
the appendix is authoritative.

---

## `integrity_review`

**Dispatched** at the promotion gate, once per candidate whose prompt text
differs from the baseline's.

**Given** `IntegrityReviewInput`: the candidate's instructions, the
baseline's, and the evals' own criteria — judge prompts and eval config
steps, spec text, eval descriptions. Never item data: no input, output,
trace or score.

**Must return** `IntegrityReviewResult`: `flags`, one sentence each naming a
candidate instruction that paraphrases a judge's criterion or games how an
eval scores, and short `notes`. Each flag becomes an `integrity_flags` entry
(`review: …`) on the candidate's val table: a flag for the promotion agent,
never a block. A session that fails is itself a flag.

It runs with **no tools at all**: no MCP server (`--strict-mcp-config` and
no config) and every built-in tool disallowed, under the read-only settings,
with no scratch directory. Its API allowlist is empty by construction.

**Skill:** none; the template is the whole procedure.

**Blame.** A missed paraphrase lets a gamed prompt reach the promotion agent
unflagged; the verbatim block in code still stands. A false flag costs the
promotion agent one `Integrity:` sentence.

---

## `generality_review`

**Dispatched** once per `experiment` item between the build and the train
measurement, when the built run config's prompt text, the text of its
attached skills, or its attached code tools differ from its parent's
and the exact-match pre-check found no copy. Once more after a rebuild. And
once per baseline at setup, by the baseline audit: then `item_id` is `baseline-audit`, the parent
instructions are empty and every skill and code-tool file is shown whole, so
the baseline's own text is reviewed as if all of it were added.

**Given** `GeneralityReviewInput`: the candidate's instructions, the parent's,
`skill_changes` (what the build added to the skills' text: one block per
changed or new skill file, a unified diff against the skill version it replaces
or the whole file when it is new, at most 30,000 characters),
`code_tool_changes` (the same for the code tools: their code and the
description the model is shown, at most 20,000 characters), the task's own
instructions, the item's hypothesis, up to 12 TRAIN inputs that code read
from disk — the item's target items first, their ids in `target_items` — and
`train_expected`, those items' expected outputs. Never a val or test item, a
score or a trace.

**The exact-match pre-check** runs in code before this session, at no cost.
It searches the lines the build added for every train item's id, the ids and
long numbers in its input and expected output, every eight-word run of its
input and its longer expected values. A string in more than a fifth of the
train items is boilerplate and is not searched; a string the parent or the
task's instructions already have is not added. A match is a leak — the
finding starts `exact match:` — and the session is not dispatched. Code reads skill text from the
project's `skills/` tree on disk (`prompt_text.rc_texts`): `SKILL.md`, the
`references/` files and the text files under `assets/`. A binary asset is not
read, and neither is an asset over 256 KiB, so a change to one is not reviewed.
A skill is immutable, so the same skill ids mean the same text. When changed
skills cannot be read, the prompt is reviewed alone and the record says so.

**Must return** `GeneralityReviewResult`: `leaks`, one sentence each quoting
text the build ADDED that memorises the train set or games the measurement —
a train item's entity, id or expected answer, a case-by-case rule that fits
only the observed items, wording aimed at a grader — and short `notes`.
The `notes` go on the wave log's `generality` event, not on the record.
The rules are the same for the prompt and for skill text. Naming a tool, a
skill or a field the prompt needs is not a leak.

On a leak, code sends the findings to `create_experiment_rc` for **one**
rebuild and checks the rebuild. A rebuild that still leaks ends the item
`dead_end` before any train spend, the record stamped `— DEAD END (generality:
…)`. A run config reused from an earlier run (`existing_rc_id`) is reviewed too
but never rebuilt: reuse measures that exact run config, so a leak ends the
item `dead_end` at once. Every finding lands on the experiment record as a
`generality:` line, carried past the verdict, so the planner reads it. A review
that cannot run, or prompts that cannot be read, let the item go on to its
measurement; the record says the check did not run.

It runs with **no tools at all**, like `integrity_review`. It never sees the
evals' criteria or judge prompts, so it catches train-set memorisation;
paraphrased criteria and judge gaming stay the job of `integrity_review` at
the val gate.

It runs on **`sonnet`** by default, not `opus` like the other roles.
`--agent-model generality_review=MODEL` overrides it.

**Skill:** none; the template is the whole procedure.

**Blame.** A missed leak lets a memorised prompt spend a train measurement and
reach the val gate, where `integrity_review` and val itself are the remaining
filters. A false leak costs one rebuild, and — when the rebuild is flagged
again — the arm. On a reused run config it costs the arm at once.

---

## What every agent is forbidden

The same short list appears in five templates (`batch_plan`, `eval_build`,
`probe`, `create_experiment_rc`, `analyze_experiment_results`), and it is
one rule with five enforcement points.

- **Never pass `split=val` or `split=test`** to any eval API. Val belongs to
  the promotion gate; test belongs to nobody in the loop. Enforced: the split
  fence refuses every split but train, and every route that returns val or
  test data; the project files are not readable.
- **Never read the `val`-tagged memory record**, and, except at the promotion
  gate and the report, never read the `baseline`-tagged one either — the
  baseline run-config id arrives in the input.
- **Never ask a person anything.** An unresolved question goes in
  `open_questions` and becomes a constraint record with a re-ask condition.
- **Discovery memory writes only** — `lesson`, `dead_end`, a new `issue`.
  Lifecycle writes are code's.
- **Kiln through the gateway only.** Each role has its typed
  gateway operations (`kiln-harness-tuner-gateway`) and no generic Kiln API tool; a
  refusal is final. If an operation is missing, say so instead of going
  looking.
- **Traces are files.** `get_train_results`, `get_task_run`
  and `run_task` write each train trace to a file in the run's
  `scratch/traces/` and answer with its path and a one-line summary. The
  planner, the builder, the probe, the analyst and the eval builder `Grep`
  those files for the failure and `Read` a line range; a cost question
  starts from the run config's `_stats.md`. No answer is over 40,000
  characters: lists come in pages, and any other large answer in pieces
  (`get_answer_part`).
- **Learn the rule from a train label; never copy the label.**
  The planner, the builder, the probe and the analyst can read a train
  item's expected output (`get_train_expected`). A train item's id, number,
  sentence or expected value written into a prompt, a skill or a code tool
  is a leak: the generality check's exact-match pre-check finds it before
  any train spend.

The memory scopes, train-only reads, validated writes and (with
`--model-policy`) the models an arm may use are enforced by the gateway in
code ([architecture.md](architecture.md) section 12). The promotion agent
and the report writer have read operations only. The others hold on the
skills alone.

## Per-role model and effort

Every dispatch runs at one model and one reasoning effort, pinned in code so
that runs do not differ across machines. Under `claude -p` the model is
`opus` for every role except `generality_review`, which is `sonnet`
(`AGENT_MODEL` and `AGENT_MODEL_DEFAULTS`). Under Codex and OpenCode the
model is the one named in `--agent-cli`, or the CLI's own default. A model
named in `--agent-cli` also replaces Claude's defaults
([agent-cli.md](agent-cli.md) section 9). The effort is `high` for
every role unless `--agent-cli` names one (`AGENT_EFFORT`). `--agent-model ROLE=MODEL` and
`--agent-effort ROLE=EFFORT` override per role, repeatably; an unknown role
fails at startup rather than silently keeping the default. The roles are the
keys of `AGENT_TIMEOUTS_SECS`, and `--agent-model`'s help text lists them.
