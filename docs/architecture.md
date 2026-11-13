# Architecture

How the loop is built. [concepts.md](concepts.md) defines the words; this
document says what the parts are and how they are wired.

Every claim here was read off the code in this repository. Where a constant is
quoted, the module and the name are quoted with it, so a reader can check it
rather than trust it.

## 1. Topology

```
terminal
  $ harness-tuner --project-path <dir> --project-id <id> --task-id <id> \
      --guidance "…"

  ORCHESTRATOR  (src/kiln_harness_tuner, the harness-tuner package)
    ├─ REST (httpx) ─────────► Kiln studio server
    │                            eval jobs, statistics, memories, run configs
    ├─ claude -p subprocess ─► agents
    │    cwd = the agents folder  tools: the loop gateway's typed operations
    │                             (per role, train-only); skills from
    │                             .claude/skills in the agents folder
    └─ terminal app (tui/) ──► the person running it

  KILN STUDIO SERVER ──────► the subject model, whose tools reach whatever
                              the task's run config attaches
```

The Kiln host defaults to `kiln.DEFAULT_KILN_HOST` and is set per run with
`--kiln-host`.

A run keeps everything it writes in the **state folder**, `<state>` below
([`paths.py`](../src/kiln_harness_tuner/paths.py)): `~/.kiln_ai/harness_tuner/`,
or the folder `KILN_HARNESS_TUNER_HOME` names. It holds the run folders, the
run lock, the quarantine, the copied transcripts, the reports and the **agents
folder**, `<state>/agents/<cli>/<version>/`, which holds the skills and
instructions the run's sessions read. A path in this document that starts
with `runs/`, `traces/` or `reports/` is inside the state folder
([install.md](install.md#where-the-tuner-keeps-its-files) has the layout).
The tuner reads its agent files and role templates from its own package, and
nothing from a checkout.

Two properties are load-bearing:

- **Orchestrator to Kiln is REST only.** Nothing in `src/kiln_harness_tuner` imports
  `kiln_ai`. The HTTP client in [`kiln.py`](../src/kiln_harness_tuner/kiln.py) is the one
  client: the orchestrator calls it directly, and the agents reach Kiln
  through the gateway's typed operations, which are loop code over the same
  client.
- **The studio server reads the project from disk live.** So a Kiln project
  checked into a repository is reset with `git checkout`. The agents do not
  read the project's files (section 12):
  the tree holds the eval inputs and results of every split, so the read
  fence denies it whole.

## 2. The two seams

The loop has exactly two places where a different host would substitute its own
implementation.

| Seam | Interface | The implementations here |
|---|---|---|
| Agent dispatch | `AgentDispatcher` in [`dispatch/base.py`](../src/kiln_harness_tuner/dispatch/base.py) | `ClaudeCliDispatcher` — one `claude -p` subprocess per call; `CodexCliDispatcher` and `OpenCodeCliDispatcher` the same over `codex exec` and `opencode run`, chosen with `--agent-cli` |
| Kiln API access | `KilnClient` (orchestrator) and the loop gateway (agents) | httpx direct, and the gateway MCP server in `src/kiln_harness_tuner/gateway/` |

No module outside `dispatch/` imports an agent CLI's dispatcher except `cli.py`,
which is the composition root.
`tests/test_dispatch_boundary.py` asserts that the exception set is exactly
that one module. [agent-cli.md](agent-cli.md) is the dispatcher seam's
full contract.

Loop agents never get a generic Kiln API tool. Each role's
session is given its own MCP config holding one Kiln server, the loop
gateway, started for that role; its tools are named
`mcp__kiln_harness_tuner_gateway__<operation>` (section 12). The `.mcp.json`
that `harness-tuner-install-agents --dest` writes, and its `kiln_api_mcp`
server (`call_kiln_api`), serve interactive sessions only and are never
rendered into a loop session.

## 3. The wave loop

`orchestrate()` in [`orchestrator.py`](../src/kiln_harness_tuner/orchestrator.py). A
clean exit gets the agent-written report. A quota or auth stop, and a fatal
error after the first wave started, get a code-rendered fallback built from
`RunStatus`. An interrupt (Ctrl-C), or a fatal error before wave 1, writes no
report.

### 3.1 Pre-flight

In this order, and the order is the design. Two start-up steps come first,
and they write in the state folder only:

- **The state folder** (`paths.py`). Its path is read; a state folder and a
  Kiln project that overlap (one inside the other) are refused before
  anything is created; then the folder and its layout are created, and a
  test write proves it is writable. Each failure is one message with a fix
  line.
- **The agent files** (`install_agents.install_for_run`). The skills and the
  instructions for the run's agent CLI go into the agents folder,
  `<state>/agents/<cli>/<version>/`, where its sessions read them. A folder
  that already matches the package is used as it is. Otherwise a fresh copy is
  built in a temporary folder beside it and renamed into place, so a failed
  build leaves nothing half-built where sessions read.

Then:

0. **The agent-CLI check** (`check_agent_cli`). One short prompt
   with no tools goes through `--agent-cli`, before the lock and the run
   directory. A CLI that cannot answer stops the start and leaves no lock and
   no run directory behind ([agent-cli.md](agent-cli.md) section 9).
1. **The run lock.** A kernel `flock` on `runs/.lock-<project>-<task>`. One
   live run per task and state folder. The kernel releases it when the holder
   dies, so there is no stale-lock reclaim step and no reclaim race. The run
   folder `runs/<run_id>/` is created next, and its path is printed before
   the run's first display line (section 11).
2. **The rendered per-run configuration.** `render_run_configs` writes
   `runs/<run_id>/mcp_config.<role>.json` and
   `runs/<run_id>/settings.<role>.json` for every role (plus the shared
   `mcp_config.json`, `settings.json` and `settings.readonly.json`). Section
   12 is what they contain.
3. **The start-up split check.** Code only. The held-out ids the analyzer
   checks against are set, and the TRAIN item set the gateway serves agents
   from is written (section 12). Stored results on val and test items stay in
   the project. It comes before every dispatch, because an agent must never
   start before the gateway knows what it may serve.
4. **The MCP lane pre-flight.** One short agent session that calls the
   gateway's `ping` operation and proves the lane end to end. It runs **first among the
   dispatches** because the setup phase below dispatches agents, and a broken
   lane must fail before paid work, not during it.
5. **Setup.** Section 5.
6. **The janitor.** Every task-scope `experiment` record still reading
   `— RUNNING` is stamped `— ABANDONED (orchestrator restart …)`. It runs
   *after* setup so a record setup just seeded is visible to it.
7. **The baseline check.** `require_baseline_record` — defence in depth.
   Setup seeds the record, and this check still catches a record deleted
   mid-run.
8. **The contract pre-flight.** A no-cost probe of the live server's response
   shapes: the evals envelope, both statistics operations, one stored-results
   read. A field that moved fails here, with the field names, rather than three
   waves in while silently measuring an empty set.
9. **The model preflight** (`model_preflight.run_model_preflight`).
   One test call per model the run may use: the baseline's own model, and
   the models the planner receives in `allowed_models` (at most
   `MAX_CHECKED_MODELS`; past that, only the pairs the policy names
   exactly). The call is `POST …/run` on the shortest train item (as
   plaintext when the task has no input schema), with the task's simple
   prompt, no tools, the lowest thinking level the server offers and, for
   another model, the structured output mode Kiln suggests for it. So it
   costs one short prompt and one short answer. Its cost is booked as
   `spend`, source `model_preflight`. A model whose context window (when
   Kiln's model list gives one) is smaller than the baseline's prompt and
   tool definitions, estimated at characters / 4, is flagged without a
   call. A failure is classed from the body Kiln returns (`message` and
   `error_type`). A timeout, a connection failure, a 429 or a 5xx gets one
   retry. Only a hard failure flags the model: a bad request, not found, no
   deployment, context too long, quota or auth. A flag is never silent: a
   line, `RunStatus.model_preflight`, and `Ctx.flagged_models`, which keeps
   the model out of `allowed_models`, out of every screening round and out
   of every measurement (`require_policy`) this run. A hard failure on the
   baseline's own model stops the start, and the message gives the fix. Any
   other failure — still transient after the retry, output that could not
   be read, or an error that names neither the model nor the provider —
   leaves the model untested, with a warning and the reason, and excludes
   nothing. It comes before the baseline measurement, the first step that
   spends on the provider. Skipped with `--skip-model-preflight`.
10. **The baseline measurement** (`prepare_baseline`). Where the
   baseline's stored train rows do not cover an active eval's train split
   (`--coverage-floor`), code measures it on the full split; under the speed
   objective it also times the baseline judge-free on the fixed speed
   sample. On a multi-turn task, or a task in a world
   ([your-own-task.md](your-own-task.md) section 1.6), each timing drive is
   a one-item eval job (section 16, **Drives through eval jobs**). Both
   objectives: the planner needs the eval sizes, and the first
   arm would otherwise pay for the same measurement of its parent. Two plain
   lines report it, in the form
   `baseline measured on train (<rc>): <eval> <k>/<n> measured, … — $<x> this run`
   and `baseline speed: median <s>, p90 <s>, IQR <s> over 30 train items`.
   A stored result on test never counts as a train measurement, so a
   baseline whose stored results are all on test is measured here.
11. **The baseline audit** (`generality.audit_baseline`).
    The generality check (section 4) reads only what a build adds, so text
    the baseline already carries is never checked. Once per baseline, the
    same two checks read its WHOLE prompt, skills and code tools as if all
    of it were added: the free exact-match search first, then one
    `generality_review` session only when the search finds nothing. A leak
    becomes one `baseline_audit` issue, bound to no eval, that asks for a
    scrub; an open one for the same baseline skips the audit next run. It
    never stops the run. Skipped with `--no-baseline-audit`.
    The audit has a second half, for eval text: the
    judge-copy matcher (section 4, step 4) reads the baseline's whole text,
    with only the task's instructions exempt. A copy becomes one
    `baseline_judge_copy` issue, in the same shape, that asks for a scrub in
    new words. The build-time check exempts text the baseline carries, so
    this issue is where such a phrase is reported. Code only, no spend; it
    also runs once per baseline.
12. **The detectability check** (`run_detectability_check`).
    Free: it reads the baseline's stored train rows and the split sizes on
    disk. It can refuse the run here, before any wave spend. Section 5.1.
13. **The noise check**, only with `--noise-check N`
    (`run_noise_check`). Paid, estimated before it spends. Section 5.2.
14. **The reconnaissance battery.** Wave 0. Skipped with `--no-recon`.
    Shaped by the baseline's tools, skills and output mode
    (`probes.recon_items`). Its
    drives take the same route as the timing drives: one-item train eval
    jobs on a task that needs them.
15. **The opening screen**, under `speed` and `cost` only
    (`run_opening_screen`). One screening round: the baseline's own model at
    every thinking level the provider offers below the current one, measured
    in code. Section 16.

### 3.2 The loop body

Per wave, in order:

1. **The clock check.** Before the time limit, the wave is a normal wave.
   At or past it, the wave is the wrap-up, and it is the last wave (section
   3.3).
2. **The budget check.** First the ledger is reconciled with the records
   this run created (`kiln_harness_tuner.reconcile`; [costs.md](costs.md)), so
   the check reads the true spend. With a budget (any `--budget-usd` but
   `none`; the default is 50): at or past the ceiling, exit
   `budget_exhausted`; within one mean wave's cost of it, this wave is the
   wrap-up. This check is a wave boundary and nothing else — **a wave already
   running is never killed for the budget**, so the wave that crosses the
   ceiling runs to completion and is paid for. The clock works the same way
   (section 3.3).
3. **Read the baseline**, fresh, because the previous wave's promotion moved
   it. The planner receives the run-config id, and no val number of the
   baseline.
4. **The design review of the baseline** (`smells.ensure_baseline_reviewed`),
   once for each baseline: on wave 1, and on the wave after a promotion,
   when the run config differs from the one last reviewed. The smell
   detectors in code run first, then one `baseline_review` session, and
   code writes one `smell` issue for each finding (section 18). No session
   runs with `--no-design-review` or on a wrap-up wave; the detectors
   still run. It never raises, apart from a quota error. The review is part
   of the wave: the clock is not checked again after it, so a review that
   ends past the time limit is still followed by this wave's plan.
5. **Plan.** One `batch_plan` dispatch. Its input carries the present
   baseline's detections (`BatchPlanInput.detections`), and it reads the
   `smell` issues from memory with the other issues. A hard failure exits the run
   (`planner_failed`) — it is the loop's brain, and another attempt after the
   retry ladder only burns budget. **The eval-building guard**
   (`items.drop_eval_building_items`): eval building is experimental and off
   by default. While it is off (no `--experimental-eval-building`), code
   drops every planned `eval_only` item and every fix with
   `needs_eval: true` from each plan the planner returns, with one
   `eval_item_dropped` event and log line for each. The rest of the wave
   runs. `_run_wave` applies the same guard, and `_run_eval_build` itself
   refuses to dispatch (the item ends `failed_infra`), so no path dispatches
   the `eval_build` role. Each later planner input lists the dropped items
   (`dropped_eval_items`), so the planner knows that they never ran. When
   code drops every item of a plan, the planner gets one more dispatch for
   the same wave, with the drops in its input (one
   `plan_all_items_dropped` event). If that plan also has no item left, the
   run exits `empty_plan`. The report names each open issue that has no eval
   (section 11).
6. **Optionally, screening rounds** (`_screening_phase`). When the plan
   carries `screen` instead of items, code measures every variant it names
   on the screening subset and calls the planner again with the table
   (phase `rescreen`), up to a fixed number of rounds per wave.
7. **Optionally, the probe phase.** When the plan comes back with
   `probe_phase: true` it carries probes only. The loop runs them, then calls
   the planner again with the answers and plans the wave's arms on facts. One
   probe phase per wave; a second is logged and runs as an ordinary wave.
8. **Run the wave.** Every item in parallel, bounded by
   `--max-concurrent-agents` and `--max-concurrent-evals`.
9. **The tripwire.** Section 10.
10. **The promotion gate.** Section 9. It runs over what completed even when
   the wave tripped, or the time limit passed during the wave, so a measured
   winner is never stranded.
11. **Exit, or carry one thing forward.** The planner's own
   `rejected_summary` is the single piece of in-context state that crosses a
   wave boundary; everything else the next planner reads from memory.

Exit reasons, each as the code names it: `timeout`, `budget_exhausted`,
`planner_failed`, `empty_plan`, the planner's own `loop_exit`, `tripwire`,
`wrap_up`, `quota_exhausted` (a classified quota or auth error from an agent
session, an eval job or a drive), `interrupted` (Ctrl-C) and `fatal:<ExceptionClass>`.

### 3.3 The clock

`--timeout-mins` sets a monotonic time limit. The loop checks it at wave
boundaries only. Three rules govern it:

- **Before the limit**, every wave is a normal wave. The loop does not
  predict when to wrap up: nobody can say beforehand how long a wave takes,
  and a guess either wastes the time that is left or stops a wave early.
- **At the limit**, no new normal wave starts. The wave in flight is not
  cancelled. Its items run to their end (build, measurement, analysis), and
  its promotion gate on val runs too.
- **After the limit**, exactly one wrap-up wave runs (`should_wrap_up:
  true`). Its job is to merge the pooled unpromoted wins. When nothing is
  pooled, the planner returns `loop_exit` and the run goes to the report.
  After the wrap-up wave, the run goes to the report. The exit reason is
  `timeout`, and its detail names the last normal wave:
  `finished wave 3, then the wrap-up`, or
  `finished wave 3; no wrap-up: nothing to merge`
  (`orchestrator.time_limit_detail`).

**The time limit is approximate.** At the limit, no new wave starts. The
wave in flight is not cancelled: its builds, measurements, analyses and
promotion gate finish. Then one wrap-up wave merges the pooled wins, and the
run writes its report. So the run can end later than the limit, by the length
of one or two waves. How long a wave takes depends on the agent model and on
the task: the size of the evals, the number of experiments and the speed of
the task's model. `--budget-usd` stays a hard cap, and Ctrl+C stops the run
at once.

The limit stops new work. It does not stop work that has started. The budget
is different: it stays a hard ceiling (section 3.2). When the budget ends the
run first, the budget's own wrap-up wave is the last (exit `wrap_up`), or the
run exits `budget_exhausted`. A signal and a fatal error also stop the run at
once.

The planner is handed the conclusion — `should_wrap_up: bool` — never the
clock. Budget arithmetic belongs in code, not in an agent reading a minutes
number.

## 4. One item's path

`execute_plan_item` in [`items.py`](../src/kiln_harness_tuner/items.py). Its shape is
where one-crack and eval-freeze are visible.

| Item | Path |
|---|---|
| `eval_only` | experimental, off by default (section 3.2): one `eval_build` dispatch. Reproduced → link the eval to the issue. Not reproduced → mark the issue `WONT_FIX:` with the reason. Both are useful outcomes. |
| `measure_issue_baseline` | pure code. Run the issue's existing eval against the baseline on train, apply the headroom rule, PATCH the issue. No builder, no analyst, no new run config. |
| `remeasure` | pure code. Re-drive only the items an existing run config never stored a row for. |
| `probe` with a `check` | pure code. Fork the parent, attach a throwaway surface, apply overrides, drive a few train items, count in the traces. |
| `probe` without a `check` | one short `probe` agent session that reads the traces. |
| `experiment` (fix or merge work) | the full path below. |

The full path:

1. If the item needs an eval it does not have (only with
   `--experimental-eval-building`; section 3.2), one `eval_build` dispatch
   **first**, given the issue id and the baseline run config and nothing else.
   The hypothesis and the technique deliberately do not cross that boundary:
   the eval measures the *issue*, not the planned fix.
2. Open the experiment memory record — code, at an exact moment, so the record
   exists before anything can fail.
3. One `create_experiment_rc` dispatch. It builds one new run config, stamps
   provenance, smokes it for execution, and **exits before any results exist**.
4. **The generality check**
   ([`generality.py`](../src/kiln_harness_tuner/generality.py)). When the built prompt
   text, the text of its attached skills, or its attached code tools
   (their code and the description the model is shown) differ
   from the parent's, two checks run, in order:
   - **The exact-match pre-check**, in code, free. Every train item's id,
     the ids and long numbers in its input and expected output, every
     eight-word run of its input and its longer expected values are
     searched for in the lines the build added. A string in more than a
     fifth of the train items is boilerplate; a string the parent or the
     task's instructions already have is not added. A match is a leak, and
     the review does not run.
   - **The review.** One `generality_review` session (no tools) reads what
     the build added to the prompt, to the skills and to the code tools (a
     diff against the version each new one replaces), beside the task's
     instructions, up to 12 train inputs (the target items first) and their
     expected outputs, and names text that memorises the train set or games
     the measurement. Naming a tool or a skill is allowed.
   On a leak: ONE more `create_experiment_rc` dispatch with the findings
   (`rejected_rc_id`, `generality_findings`), then the check again. A rebuild
   that still leaks ends the item `dead_end` here, before any train spend.
   A reused run config (`existing_rc_id`) is checked too and never rebuilt:
   a leak on it ends the item `dead_end` at once.
   Each finding is a `generality:` line on the experiment record, carried past
   the verdict. A review that cannot run is logged, written on the record, and
   does not stop the item; so does skill or code-tool text that cannot be
   read (the rest is then reviewed alone).
   **The judge-copy check**
   ([`judge_copy.py`](../src/kiln_harness_tuner/judge_copy.py)) runs at the same
   point, on every build, merges included. It uses the matcher of the
   gate's integrity check (a) (section 9) on the WHOLE text of the run
   config — its prompt and every file of its skills and code tools — not
   only on the added lines, so a phrase that a parent carries is found. It
   compares that text with the judge prompts, eval configs, specs and
   descriptions of the active evals, and with their names. The exempt text
   is the **baseline's**, not the parent's: for the prompt, the baseline's
   prompt and the task's instructions (the gate's own rule, so a prompt that
   passes here passes check (a) while the evals stay the same); for a skill
   or code-tool file, the baseline's whole text and the task's
   instructions. The parent is not exempt, because an inherited phrase comes
   from the parent. A hit shares the one rebuild with the generality check:
   ONE more `create_experiment_rc` dispatch with every finding
   (`judge_copy_findings` beside `generality_findings`), then both checks
   again. A second hit ends the item `dead_end` before any train spend. A
   reused run config is checked and never rebuilt. Each finding is a `judge
   copy:` line on the record, carried past the verdict, a `judge_copy` event
   with a line, and a `judge copy: …` detail line on the item. Code only,
   no spend. A check that cannot read its inputs is logged and does not
   stop the item.
   **The reference check**
   ([`reference_check.py`](../src/kiln_harness_tuner/reference_check.py)) runs at the
   same point, on every build, merges included. It looks for names that
   point at nothing the run config attaches:
   - a skill name in the prompt or in an attached skill's text, when the
     run config does not attach that skill. Only the names of the skills
     in the project (`skills/` on disk) and on the parent are looked for.
     A finding names an archived project skill as archived;
   - a tool function name in the prompt or in an attached skill's text,
     when the parent attaches that tool and the build does not.
   Only what the build introduced is reported. A skill reference that the
   parent already has, in the same place and not attached to the parent
   either, is the parent's, and the check drops it.
   A code tool may call a tool that it allowlists even when that tool is
   not attached, so the check does not compare allowlists.
   A name matches on its token boundaries: the character before and after
   it is not a letter, a digit, `_` or `-`. So `foo` is not found inside
   `foo-v2`. A name with no `-`, `_` or digit is an ordinary word too. It
   matches only in backticks, in quotes that a `:` does not follow (a JSON
   key is not a reference), or as "the <name> skill", "the <name> tool" or
   "<name> skill". So "the tool result" does not name a tool `result`. A
   code tool's own text is not read for names, because a wrapper's
   description names the tool it replaces. Each text is split into tokens
   once, and the scan runs in a worker thread.
   `--reference-check` sets the outcome. `warn` (the default) writes each
   finding on the item outcome (`reference_findings`), on a `reference
   check:` line of the record carried past the verdict, and in the
   analysis input, and the item goes on to its measurement. When another
   check causes the one rebuild, the same line also says what the check
   found on the rebuild, for example `rebuilt once as <rc> — clean`. `refuse` shares
   the one rebuild with the other two checks (`reference_findings` in the
   build input), and a second hit ends the item `dead_end`. `off` skips the
   check. Every check is a `reference_check` event with a line. Code only,
   no spend. A check that cannot read its inputs is logged and does not
   stop the item.
5. `run_experiment_analysis` — code. Section 7.
6. One `analyze_experiment_results` dispatch, given the item, the run-config
   id, the numbers and the memory id. It has data and ids, never edit surface.
7. Code writes the verdict to the experiment record.

One-crack is step 3 ending before step 5 begins — the one rebuild in step 4
happens before any result exists too. Eval-freeze is step 1 happening before
step 3, and step 6 happening after. Both are **sequencing**.
The gateway enforces part of each: only the eval builder
has eval-writing operations, and they accept only an eval that the same run
created; the builder can rename a run config but not change its
properties. What stays sequencing is covered in
[limitations.md](limitations.md).

Every path lands exactly one `item_outcome` event, which is what lets the live
display replace a running row with its finding.

## 5. The setup phase

[`setup.py`](../src/kiln_harness_tuner/setup.py). It runs on **every** invocation, not
only cold ones: keeping the issue store in step with the task's evals is the
recurring half of its job. On a warm task with nothing new it is a handful of
REST reads and one `setup_ok` event.

1. **Baseline seeding.** An existing `baseline` record is left exactly as it
   is. Otherwise: the task's default run config if there is one, else the most
   recently created one with the ambiguity named in the record's own prose,
   else a fatal error — zero run configs is the one state code cannot decide.
2. **The eval ↔ issue sync.** The eval is the parent; the issue memory is a
   derived child bound by an `eval_<eval_id>` tag. Unbound active evals are
   seeded, bound ones are refreshed when their source fingerprint has changed,
   orphans are flagged and never deleted.
3. **The description agent.** One small `describe_eval` session per new or
   drifted eval, in parallel, bounded. A one-time cost per eval.
4. **The split readiness audit.** Every active eval must carry a train filter
   and a val filter. Missing → fatal, listing every offending eval at once.
   Setup **audits; it never repairs.**

The sync is additive. It creates memories, adds tags and appends content lines.
It never deletes a memory, never rewrites agent-authored prose and never closes
an issue. The two baseline-audit issues (`baseline_audit`, and
`baseline_judge_copy`; section 3.1, step 11) and the `smell` issues
(section 18) are bound to no eval on purpose: the sync neither repairs them
into a binding nor reports them as unbound.

An issue's `content` has three writers — setup, the loop's code writers, and
agents recording findings — so the field is split by one delimiter line.
Everything above it is setup-owned and replaced wholesale on a refresh;
everything from the delimiter down is loop-owned, append-only, and preserved
byte for byte. A record with no delimiter is treated as entirely loop-owned.

[your-own-task.md](your-own-task.md) is this section from the reader's side.

### 5.1 The detectability check

[`detectability.py`](../src/kiln_harness_tuner/detectability.py). It
answers one question before wave 1: can this task's evals show the
improvement the loop looks for at all? It runs after the baseline's train
measurement (section 3.1, step 10) and before the reconnaissance battery. It
starts no eval job and no agent.

**The floor.** A paired test reaches p < `SIGNIFICANCE_ALPHA` only with a
minimum number of changed items, even when every change is a win. Exact
two-sided McNemar with k discordant pairs all one way gives p = 2 · 0.5^k:
5 gives 0.0625, 6 gives 0.03125. The server's Wilcoxon signed-rank gives no
p below 5 non-zero differences, and 0.059 at 5 and 0.036 at 6 when all are
positive. Code computes both floors from the alpha (`binary_floor`,
`rating_floor`). So a score can show a significant win only when at least
`floor` items have room to improve, and the smallest change it can show is
floor / n — a best case.

**Per active (eval, score)** — directional, pass/fail or five-star;
`informational` and custom scores never count — the check reports:

| Side | Number | Source | Grade |
|---|---|---|---|
| train | n, items with room, items unscored | the baseline's stored train rows, the split on disk | exact |
| train | floor, fewest wins, best case (floor / n) | computed | exact |
| val | n | the val split's size on disk | certain, val-grade |
| val | items with room | val n × the train share with room | an estimate, val-grade |

The val side reads no val result: the gate is the only reader of val
results. Setup does not measure the baseline on val: that would add
spend to a check that must be free, and the spend is lost when no gate
runs. So the only certain val fact is the split's size.

**The interval route.** The one-candidate code rule (section 9) also
promotes when a row's interval lies above zero. The server's intervals can
do that with fewer wins than the floor: its Newcombe paired interval says
so for 3 of 5 failures fixed on 10 items, and for 1 of 1 on one item. The
check does not count that route as detectable. It reports it
(`interval_wins`) beside the floor, in the report only.

**The refusal.** Under `--objective quality` the check raises
`UnderpoweredEvalsError` (a `SetupError`) when, as a certain fact, no score
that could drive a promotion can show a significant val win: every such
score's val split holds fewer items than its floor, or there is no such
score. With `--min-effect` it also refuses when no such score's val split
can show that change even in the best case. The message lists every
offending score at once, like the split audit. An estimate never refuses; it
warns. One eval at its ceiling never refuses: a guard eval at 100% catches
regressions. `--allow-underpowered` goes on anyway, and the refusal is kept
in the warnings and in the report. Under `speed` and `cost` quality only
has to hold, so the check reports and never refuses.

**Where the numbers go.** The train half of each row
(`TrainDetectability`) goes to every planner as
`BatchPlanInput.train_detectability`, recomputed each wave from the current
baseline's stored train rows. The full rows go to the terminal, the event
log and the report's code-computed Detectability appendix, whose val columns
are labelled val-grade. Nothing goes to memory.

### 5.2 The noise check

[`noise.py`](../src/kiln_harness_tuner/noise.py), only with
`--noise-check N`. It measures what one run cannot: how often the same
config scores differently on the same item between two runs, and how often
the judge scores the same output differently. It uses N train items per
eval, sampled by a hash of the item id, from the items the baseline has
stored rows for.

Kiln stores one result per (eval config × run config × item) and reuses it,
so a plain re-run returns the stored rows. The check goes round the cache in
two ways, and neither edits anything the loop measures with:

- **Re-drive.** Code creates a clone of the baseline run config (the same
  properties, a new id, named `NOISE …`) and runs each eval's default
  config on it, narrowed to the sample (`Analyzer.judge_items`). The clone
  has no stored row, so each item is driven and judged again. The clone is
  exempt from the model policy, like the baseline it copies. It is never a
  parent, a candidate or the baseline.
- **Re-judge.** For an eval whose default config is a V2 judge, code
  creates a copy of that config (the same type, properties and model,
  never set as the default) and runs it on the baseline run config. A V2
  config shares the task's stored traces across eval configs, so the copy
  judges the baseline's own outputs again and drives nothing. Code checks
  this per row: the copied row must name the baseline row's scored task run
  (or carry the same output), else the eval's judge noise is reported as
  not measured. After the pass the copy leaves the project, to
  `runs/.quarantine/…/archived_eval_configs/`. A legacy judge config drives
  inside its own job, and a code eval is deterministic: neither is
  re-judged, and the row says why.

**Spend.** Before any spend, one line gives an estimate from the baseline's
stored rows. With a budget (any `--budget-usd` but `none`), an estimate that
does not fit the remaining run budget skips the check with the reason. The
spend is booked as `spend` source `noise_check`, in the provider lane.

**The result.** Per (eval, score): how many sampled items changed on the
re-drive and on the re-judge (a pass/fail outcome, or a rating value with
its mean absolute change). Then the comparison with section 5.1: with a
change rate r on n train items, about r · n / 2 items move each way on any
measurement, so a real improvement needs more wins than the floor. The
check reports that number (`noise_adjusted_wins`, a sign-test
approximation) and warns when it is above the best case, or above the items
with room. It never refuses. The rows are train rows: every planner gets
them as `BatchPlanInput.train_noise`. Under speed and cost the pooled
re-drive change rate of the pass/fail rows is also the noise flip rate of
the `guard_losses` stop rule (section 16). Under quality it is the noise
flip rate of the guard half of the `residual` stop rule (section 16).

## 6. Splits

The rule from [concepts.md](concepts.md), with the access matrix that enforces
it.

| Actor | `baseline` record | `val` record |
|---|---|---|
| `select_baseline` (code) | read — passes the run-config id onward, never metrics | — |
| the planner, the builder, the analyst, the eval builder, the prober, the baseline reviewer | **none** (by instruction: the gateway does not hide this record) | **none** (the gateway hides it) |
| the promotion gate (agent + code apply) | read and write | read and write |
| the report writer | read | read, and its numbers are labelled val-grade |

**Enforced by structure:** every agent input is code-assembled, and the val
pass runs only inside the promotion gate, *after* analysis, so no
memory-writing context ever holds val data. The planner-visible `baseline`
record carries no val data at all, which is why the guarantee does not rest on
a string format surviving an agent-authored label.

**Three sanctioned val → train channels**, all code-composed and all carrying
zero numbers:

1. A regressed issue's status flag — a `FIXED:` issue that repros under the new
   baseline goes back to open with a provenance line.
2. `— REJECTED AT VAL <date>` on a candidate the gate passed over.
3. `— PROMOTED <date>` on the winner's experiment record.

Each discloses one bit. The planner may re-target an issue or decline to
re-submit a run config unchanged; every subsequent measurement is back on
train.

**Enforced in code,** at four layers, so that one gap is not
a leak:

1. **The loop gateway** (section 12): agents reach Kiln only through typed
   operations that answer from provably TRAIN records, field by field, and
   write through validated requests. There is no operation that reads a
   held-out item, names a split, or edits one.
2. **The file fence** (section 12): the run's own project tree is denied
   whole, and so is every file of the run directory but the scratch — the
   wave log, the step records and the memory snapshots carry val tables.
3. **The start-up split check** (section 12): the held-out item ids and the
   train set are decided before the first dispatch. Stored results on val and
   test items stay in the project, behind fences 1 and 2.
4. **The analyzer's own check:** every train read is checked against the
   held-out item ids; a train read that returns one raises
   `HeldOutLeakError`, and the rows never reach an agent.

The val readers — the promotion agent and the report writer — run read-only
(section 12), so what they hold cannot reach a record a later agent reads.
Their only val view is `get_val_summary`, the gate's code-assembled tables;
the gateway hides the `val` memory record from every other role, by tag and
by id, in listings and in fetches.

## 7. Measurement

[`analysis.py`](../src/kiln_harness_tuner/analysis.py). Judgment-free numbers, assembled
in code.

**Measured once, read forever.** Kiln's eval runner indexes stored results by
(eval config × run config × dataset item) and generates only the missing ones.
Re-invoking a measured combination runs zero items and costs nothing. The same
run config on the same input yields the same result, so this is correctness,
not a shortcut — and every baseline comparison is a free read.

**Split scoping.** Experiment measurement runs `split=train`; the promotion
gate runs `split=val`; the loop never passes `split=test`. The cache is
item-grained, so splits compose.

**One measurement per key at a time.** `ensure_measured` holds a lock per
(eval, config, run config, split) and remembers a clean result for the run, so
four concurrent arms that each find no stored baseline rows await one job
rather than driving three.

**Failures are cache-shaped.** An item that errors persists no result row, so
the retry is just re-invoking the same job: it runs exactly the unfinished
items. `DEFAULT_JOB_RETRY_SLEEP_SECS` between attempts,
`DEFAULT_JOB_WAIT_TIMEOUT_SECS` per wait.

**Holes are named, not hidden.** Pairs still unmeasured after the ladder become
entries in the bundle's `errors` and analysis proceeds on what landed. There is
no mechanical completeness threshold in the experiment lane — the analyst
weighs the holes in its judgment. The promotion gate is stricter, because it
writes the baseline: section 9.

### 7.1 Statistics

Per targeted (eval, score) pair, paired by dataset item across the two run
configs:

- pass/fail scores → exact McNemar,
- rating scales → Wilcoxon signed-rank with a paired bootstrap interval,
- custom score types → a hard error at the site. There is no
  statistics operation for them yet, and none is guessed.

Code computes `delta` (sign-normalized to good-direction units), `n`, `p`,
`significant`, the interval where the operation returns one, and binary flip
counts. **Nothing else, and no label.** At the sizes these evals run, a
vocabulary of `win` / `loss` / `insignificant` would mostly emit its most
biasing value, and "insignificant" primes "unusable" when the real question is
how much caution to adopt. That judgment belongs to the analyst, reading the
numbers.

Arithmetic is never done in an agent's context.

### 7.2 The baseline-if-needed tree

```
stored = results(eval, rc=parent, split=train)     # free read
if stored:                 compare(candidate, stored)          # attribution
else:
    ref = latest baseline with FULL train coverage on this eval
    if ref and candidate ≤ ref:  screen out           # attribution unmeasured
    else:                        measure the parent, then compare  # pays once
```

The screen reference walks the baseline promotion chain newest to oldest and
takes the first run config with full coverage. Not the best stored prior: at
small n the best of many measured configs is upward-biased and would screen out
genuinely competitive candidates, and the question being asked is "does this
plausibly beat where the system is *now*". With no covered baseline in the
lineage there is no screen — measure the parent.

A screen-out is not a verdict. It is recorded as unmeasured attribution, and
the analyst is told to read it that way.

## 8. Memory

[`memory.py`](../src/kiln_harness_tuner/memory.py) owns **when** a record is written;
[`memory_schema.py`](../src/kiln_harness_tuner/memory_schema.py) owns **what** it says.
The split exists because the API has no structured field beyond tags, so every
state is encoded in text. One module owns that text, so each format has one
writer and one parser.

`memory_schema.py` holds the tag-to-kind precedence, the issue prefixes, the
experiment suffixes, one `key: value` line codec, the code-owned render blocks,
and one typed view per kind. Parsing is lenient, so legacy and agent-authored
records load with empty fields instead of raising. Rendering is strict and byte
identical. Every writer that takes a record id hydrates it as the view for its
kind, so an issue writer handed an experiment id refuses with `RecordKindError`
**before** writing.

Writers, one function per write: open an experiment, record the run config it
built, record the verdict, mark it failed, promote the baseline, record the val
stats, stamp promoted / held / rejected, mark an issue fixed / regressed /
won't-fix, link an eval, record an open question, record a smell.

**Smell issues** (`record_smell`, section 18) are written by code from the
baseline reviewer's findings. Each is an issue with the tags `issue`, `p1`,
`smell`, `smell_<id>` (the smell document) and `audited_rc_<rc>` (the run
config it was found in), and with no eval binding. An open issue of the
same smell and run config is not written again. `find_smell_issues` reads
them back in any state: one is enough to show that the run config was
reviewed.

Both caps are enforced by the API and clamped client-side before any write:
`OVERVIEW_MAX_CHARS` and `CONTENT_MAX_CHARS` in `memory_schema.py`. The clamps
are structure-aware — a replacement write keeps the head's identity lines and
the `next:` tail around a truncation marker, and an append always keeps the
appended line, dropping the oldest first behind a history marker, so lineage
survives.

**Planner text is bounded before it is rendered.** An experiment overview
starts with a label that carries the planner's technique, `EXP <id>
(<technique>): `, and ends with a state suffix such as ` — RUNNING`. A long
technique could fill `OVERVIEW_MAX_CHARS`, and the clamp would then cut the
suffix. The record would read as a verdict, and the next lifecycle write on
it would be refused. Four rules keep the state readable:

- Every planner value in an experiment record is cut to one line and a bound
  before it is rendered: the technique to `TECHNIQUE_MAX_CHARS` (60), in the
  label and on the `technique:` line alike, the hypothesis, the predicted
  effect, the ids, the build's `change:` and the check findings. The planner's
  item keeps its full text. `_fit_stateful` also cuts the PREFIX when the
  prefix alone fills the cap, so the state suffix always lands whole.
- An experiment append keeps every line code reads back
  (`EXPERIMENT_HEAD_KEYS`, the gate notes) and drops other lines, oldest
  first.
- No suffix is a verdict only in the verdict's shape (`EXP <id>: …`). An
  overview still in the opening's shape (`EXP <id> (…`) with no suffix reads
  `UNKNOWN`. `UNKNOWN` takes the forward moves of `RUNNING` and is not
  stampable. `record_verdict` removes a `(technique)` after the label, so a
  verdict never has the opening's shape. The janitor leaves an `UNKNOWN`
  record as it is.
- A refused lifecycle write (`MemoryWriteRefused`) on an arm's own record is
  logged as `memory_write_failed`, and the item keeps its outcome
  (`items._experiment_write`). So are a refused tripwire stamp and a refused
  winner stamp. Section 10 has the policy.

Agents do their own discovery writes — `lesson`, `dead_end`, a new `issue` —
through the gateway's `memory_create` / `memory_update`, in the two scopes
only. Lifecycle writes are code's alone by contract: the gateway enforces
the two scopes and refuses the `val` tag, and does not check the record kind.

## 9. The promotion gate

`maybe_promote` in `orchestrator.py`. A candidate needs **both** halves: the
planner marked the item a promotion candidate, and the analyst judged it
successful on train.

1. **The val pass — code.** Every active eval at `split=val`, for each
   candidate and for the current baseline. An eval is active if it has no spec,
   or its spec's status says so; an eval with no default eval config is not
   active. Every candidate and the baseline run the **same** eval config, so no
   comparison mixes judges. This is the only place the loop reads val.
2. **The integrity checks — code**
   ([`integrity.py`](../src/kiln_harness_tuner/integrity.py)). (a) A candidate whose
   prompt shares a run of 8 words with a judge prompt, an eval config, a
   spec or an eval description — a run the baseline's prompt and the task's
   instructions do not contain — or names an eval (10 characters or more)
   verbatim is made ineligible. (b) A median output length on val more than
   2× the baseline's either way, or changed top-level JSON keys, and (c) a
   kind of write action in the candidate's val traces the baseline's never
   show are `integrity_flags`: a flagged winner goes to the promotion agent
   even under the code rule, and is promoted only when the rationale answers
   the flags in an `Integrity:` line. (d) A candidate whose prompt text
   differs from the baseline's gets one `integrity_review` session
   (no tools, no MCP server, read-only, input = the two prompts
   and the evals' criteria, never item data) that looks for PARAPHRASED
   criteria and eval-specific gaming; its findings are `review: …` flags,
   never a block, and a failed review is itself a flag.
   **The prompt texts** are the run config's frozen `prompt`,
   else the text the server renders for its `prompt_id` (`gen_prompt`: a
   saved prompt `id::<id>`, a generator such as `simple_prompt_builder`, a
   frozen `task_run_config::…`), else the saved-prompt listing, matched by
   the full `id::<id>`. A text that cannot be read is never a silent pass:
   an `unread: …` flag names it, and a flagged winner goes to the promotion
   agent. Every candidate gets one `integrity` line, "no flag" included.
   **One apply** (`_apply_checked`): whichever path decided — the speed or
   cost code rule, the single-candidate rule, the agent, a merge — code
   applies no promotion unless the winner's table went through these checks
   (`integrity_checked`), nothing BLOCKED it, and every other flag is
   answered by an `Integrity:` line. A winner that is not one of the
   candidates has no table, and is refused.
   **A block is visible and usable.** For each candidate that
   check (a) blocked, code prints one `gate_block` line with the reason
   (the copied passage and the eval object), stamps the candidate's
   experiment record `— REJECTED AT VAL` and appends a `- blocked at the
   val gate <MM-DD>: …` line with the reasons, and adds the block to
   `BatchPlanInput.gate_blocks` for every later planner of the run and to
   `RunStatus.gate_blocks` for the report. The rejection writer then skips
   that candidate: "did not beat it" would be false. With the build-time
   judge-copy check in place, check (a) is the final check, for a candidate
   whose evals changed after its build.
   **Only the val-free part travels.** The planner is a train-side agent,
   and the experiment record is train-readable, so nothing derived from val
   may reach them. The block path carries the findings of check (a) alone
   (`CandidateValTable.copy_findings`): the copied phrase and the eval
   object, read from the prompt and the eval definitions. The (b)
   output-shape flags, the (c) write-action flags, the `integrity_review`
   findings and every val number are computed from val traces. They go to
   the wave log, the val summary and the promotion agent, and never to `BatchPlanInput.gate_blocks` or to the record.
3. **The decision.** Under quality, with one candidate, a code rule
   (`decide_single_candidate`) decides. When it cannot decide, or its winner
   carries integrity flags, one `promote_best` session decides. With two or
   more candidates, one `promote_best` dispatch ranks them. Under speed and
   cost, `decide_speed_gate` decides in code whatever the number of
   candidates (section 16), and a winner with integrity flags goes to
   `promote_best`. `--gate-agent always` dispatches the agent every time.
4. **The apply — code, strict order.** The `baseline` record first, then the
   `val` stats record, then the issue writes (fixed, from the winner's
   *train-side* result; regressed, from the decision's flags), then the
   winner's `— PROMOTED` stamp, then one `— REJECTED AT VAL` stamp per
   candidate passed over.

Every candidate's table carries `errors` — every hole in its val assembly — and
`ineligible`. A candidate whose fill failed on either side, or whose coverage
fell below `--coverage-floor`, cannot win, and the orchestrator blocks it
regardless of what the agent decided. Without that field, a failed fill would
drop an eval in silence, and the rationale could still say "no regressions".

**There is no rollback, deliberately.** The ordering is the recovery design:
the baseline record is the authoritative pointer and is written first, so any
partial apply leaves the system pointing at the right run config. A crash after
it costs at worst some re-attempted work on an already-fixed issue, or a
missing stamp.

When the gate escalates on an open constraint instead of judging the numbers,
the candidates are stamped `— HELD` rather than rejected, and the planner may
resubmit them unchanged once the question is answered.

## 10. Errors

| Failure | Policy |
|---|---|
| eval job failure, errored items | sleep, re-invoke, bounded attempts — errored items store no row, so the re-invoke runs exactly the unfinished ones |
| a hung await | every await is capped: dispatches by their per-role wall-clock timeout, eval jobs by `DEFAULT_JOB_WAIT_TIMEOUT_SECS` |
| an agent `failed_infra` | the backoff ladder in `items.LOOP_DISPATCH_BACKOFFS`, then the item fails and code stamps its experiment record `— FAILED (infra: …)` at that moment |
| a malformed agent result | one in-session repair retry, then `failed_infra(parse_failure)` |
| plain REST | a few quick retries; a memory write still failing is **fatal** — state integrity beats liveness |
| a memory write REFUSED on a precondition (`MemoryWriteRefused`, e.g. a move the record's state does not allow) | logged as `memory_write_failed`; the item keeps its outcome and the run goes on (section 8). The refusal concerns one record and a retry cannot help |
| a missing train or val split | a hard error naming the eval. The loop never creates or repairs splits |
| `failed_expected` | not an error. A measured negative, recorded normally |
| the planner hard-failing | exit the run, `planner_failed` |
| the promotion gate hard-failing | skip promotion, note it. The candidates stay pooled and merge-eligible |
| the report writer hard-failing | the report still ships, rendered by code from `RunStatus` |
| a smell detector raising | that detector is skipped (`smell_detector_failed`); the others run. A run config that cannot be read skips them all (`smell_detection_skipped`) |
| the baseline reviewer failing, timing out or turned off | the run goes on with the code detections. The event `baseline_review_skipped` gives the reason, and the report states it |
| the knowledge skill unreadable at run time | detections and findings are kept unchecked (`knowledge_unreadable`); a finding whose id does not fit a memory tag is dropped |
| a smell id that is not a smell document | the detection or the finding is dropped (`smell_unknown`) |

**The tripwire.** More than half a wave's items ending `failed_infra` with the
same error class marks the wave aborted, notes the incomplete experiment
records, and backs off `TRIPWIRE_BACKOFF_SECS` once (not after the time limit:
the wrap-up wave starts at once). Two consecutive tripwires
end the run — *after* the promotion gate has run over what completed.
Items that completed keep their results and their honest status; only the
memory overview gets the abandoned stamp.

**Provider quota** is its own circuit breaker (`provider_errors.py`; it
knows OpenRouter's "Key limit exceeded" 403, OpenAI's
`insufficient_quota`, Anthropic's "credit balance is too low", a Gemini
RESOURCE_EXHAUSTED that names a daily or billing quota, and any 402, and a
failed drive is diagnosed through a one-item eval job). It prints one plain
line, e.g. "stopped: OpenRouter key limit exceeded (monthly limit) — raise
it on the OpenRouter dashboard": a classified quota error ends
the run through the code-only report rather than through the ladder.

**The janitor** has exactly one source of work. Inside a live run, a terminal
failure stamps `— FAILED` immediately and a tripwire-aborted item is stamped at
once, so a `— RUNNING` record at startup can only be a dead orchestrator. The
run lock is what makes that inference safe.

## 11. Observability

**`RunStatus`** in [`schemas.py`](../src/kiln_harness_tuner/schemas.py) is the one state
object. Everything downstream is a view of it and of the events: the
display and the report writer's input. Another host can render it its own
way.

- **The display.** It sits in the event fan-out beside the wave log and the
  tracker, and it never changes the run. Two implementations:
  - **The terminal app** (`tui/`, built on Textual), when stdin and stdout are
    both terminals. It shows the start-up and the run in one full-screen app.
    The app runs on the main thread, the start-up on a thread of its own, and
    the run on a third thread with its own asyncio loop. The run's display is
    `TuiSink` (`tui/sink.py`): it stamps each event, puts it on a queue, and
    never raises into the loop. A timer on the app drains the queue four times
    a second, folds the events into the run model (`tui/model/`, pure Python,
    with its own `RunStatusTracker` subclass), and redraws only the widgets
    whose text changed. Nothing on the run thread waits for the app. The app
    reaches the run only by cancelling it, the way a signal does: it catches
    SIGTERM, SIGHUP and SIGINT itself and passes them on. The app looks up the
    run config names in Kiln on its own loop, at most once every 10 seconds.
    When it closes, it prints a plain closing block: the start's summary and
    rerun command, the exit line, Best Confirmed, the report, the run
    folder and the next steps, built by the same code as the plain lines.
  - **`PlainUi`** (`ui.py`): plain timestamped lines, for `--no-progress`, a
    CI log, and every run with no terminal. The plain path never imports
    Textual.
- **The wave log** (`wave_log.py`) is the append-only audit trail under
  `runs/<run_id>/`.
- **The report** — the report-writer agent returns markdown and **code** writes
  `reports/kiln-harness-tuner-<run_id>.md`, then appends a code-computed spend appendix.
  A format-critical file is never written by an agent. Code also appends,
  when they have content, the speed table, the Detectability appendix, and
  two tables from `report.py`. "Why each candidate stopped" has one row per
  arm and merge item: the stop rule or outcome in one plain sentence
  (`STOP_REASONS`, with the raw code for a code it does not know), and the
  items judged of the items planned per eval. "Screened candidates:
  trade-offs" has one row per screening candidate of the run
  (`RunStatus.screen_results`, folded from the `screening_round` events): the
  baseline passes it lost and the failures it fixed, its cost and latency
  ratios against its round's baseline, the verdict and the rc id. The rows
  are cheapest first under `cost`, fastest first under `speed`, and highest
  net change first under `quality`. These tables only report. They change
  no stop rule, no screening verdict and no promotion. "Issues with no
  eval" is there only while eval building is off (section 3.2). It lists
  each open issue that no eval binds, read from memory when the report is
  written (`RunStatus.issues_without_eval`), so the issues the run created
  are in it too. Closed issues, the baseline-audit issues and the `smell` issues (section 18) are left out.
  A row says when the eval-building guard dropped a planned eval build for
  the issue (`RunStatus.dropped_eval_items`, folded from the
  `eval_item_dropped` events). The code-rendered fallback report reads
  once, without the client's retry ladder, because on a fatal exit Kiln
  can be what failed. When the read fails, the rows are the dropped items'
  issues and the ones setup found with no eval binding. The
  section tells the owner to create each eval in the Kiln app, with a link
  to the Kiln docs on evals.
  "## Design review" (`render_smells_appendix`) is there when a baseline of the
  run had a detection or a finding, or a review that was not done. For
  each baseline it gives the review's status line, the detections table
  (the fact, the reference and its source, and the reviewer's reading of
  it) and the findings table (the evidence, where, the confidence, and the
  id of the `smell` issue code wrote). It is folded from the
  `smell_detections` and `baseline_review` events (`RunStatus.detections_by_rc`,
  `RunStatus.baseline_reviews`), and the fallback report has it too.
- **The transcripts.** The dispatcher copies every agent session's
  transcript into `traces/` (Claude's and Codex's session files, OpenCode's
  `opencode export`), through a redactor that blanks known credential
  shapes — an agent's tool result can carry whatever it read.
- **The terminal lines** (`cli._run` and `cli.main`). When the run folder
  exists, before the first display line:
  `harness-tuner: run <run_id> — files in <run folder>/`. In the app, this
  line and any other write to stdout or stderr go to a stray-output file,
  which is added to the run log when the app closes; the closing block names
  the run folder. At a clean finish:
  `harness-tuner: run finished — report: <path>`. After a SIGTERM, a SIGHUP
  or a Ctrl-C, on stderr: `harness-tuner: run folder: <run folder>` once the
  folder exists, whose `kiln-harness-tuner.log` holds the details. After an
  error, the error screen names the run folder (`Run folder: <path>`) and the
  fallback report when one was written (`Report: <path>`).

All of these are in the state folder, never in the folder the run was started
from.

## 12. Sandbox and scope

`render_run_configs` in [`cli.py`](../src/kiln_harness_tuner/cli.py) writes two files
per role (and the shared preflight and read-only ones), and the dispatcher
passes the role's pair to every session. This section describes the Claude
fence. Codex and OpenCode build theirs from the same rendered files
([agent-cli.md](agent-cli.md) sections 10 and 11).

**`mcp_config.<role>.json`** — the loop gateway started for that role
(and, for the builder and the probe, the skill installer), passed with
`--strict-mcp-config` so nothing else loads. No generic Kiln server. The
two reviewers (`integrity_review`, `generality_review`) get no MCP config at
all: each runs with
`--strict-mcp-config`, no server and every built-in tool refused
(`NO_TOOL_ROLES`), so neither has gateway operations.

**`settings.<role>.json`** — four mechanisms, and the gateway hook for that
role. **`settings.readonly.json`** and the val readers' files are the same
with no write grant at all, and with two more write denies first: the whole
state folder and the scratch tree. The read-only roles (`READ_ONLY_ROLES` in
`dispatch/claude_cli.py`: the promotion agent and the report writer, which
hold val data, the two reviewers, and the baseline reviewer) run under it,
and get no scratch directory.

Every settings file also sets `"verbose": false`. With verbose on,
`claude -p --output-format json` prints a list of events in place of one
result object. A `--settings` file overrides the user's and the project's
Claude Code settings, so a `"verbose": true` there has no effect on a loop
session. A `--verbose` in the CLI options still wins, so the dispatcher also
reads a list: it takes the last `"type": "result"` event.

1. **File writes.** One path-scoped allow rule for the run's scratch tree, and
   an explicit deny over the Kiln project tree. The session's working directory
   is the agents folder (`<state>/agents/claude/<version>/`), which Claude Code
   lets the file tools write by default. So every role also gets a deny over
   the agents folder: its skills and `CLAUDE.md` are read by every later
   session and every later run on the same version. A further deny covers the
   gateway's trace folder (`runs/<run_id>/scratch/traces/`): agents read it,
   and only the gateway writes it. The deny binds the file tools. Bash inside
   the OS sandbox can still write the scratch tree, the trace folder included;
   a file changed that way changes what a later agent reads, never what is
   measured, which is read from Kiln.
2. **The read fence.** `read_fence_for` builds it. Denied: the run's OWN
   Kiln project, whole — its eval inputs, task runs and eval runs
   are every split's data; the state folder's `runs/` (this run's directory
   included, except its scratch), `traces/` and `reports/`; the demo project
   that ships in the package (`src/kiln_harness_tuner/demo_project/`), by
   path; and every other Claude session's transcripts (`~/.claude/projects`).
   Allowed: the run's scratch, and the `.claude/skills` and `CLAUDE.md` that
   the run installs for Claude Code in the agents folder.

   **The trace folder.** The roles that read traces —
   `TRACE_READER_ROLES` in `gateway/ops.py`: the planner, the builder, the
   probe, the analyst, the eval builder and the baseline reviewer — also
   get an explicit allow
   rule, `Read(//<run dir>/scratch/traces/**)`, in their own settings file.
   The scratch tree is not denied, so the fence alone would let the read
   through; the explicit rule is there so that auto mode does not refuse it
   either. A Read rule also covers Grep and Glob: Claude Code applies Read
   rules to every built-in tool that reads files, and a `Grep(…)` or
   `Glob(…)` path rule never matches. No other role gets the rule, and
   nothing else is opened. Codex gives the same roles the folder as a
   `read` path of their permission profile, and OpenCode as a `read` and
   `external_directory` allow; their other roles cannot read it.

   What an agent needs from the project reaches it another way: train
   items for a probe arrive in its input (`ProbeInput.train_inputs`,
   read from disk by code), usage numbers come from the results API on train,
   and a project skill a builder clones is copied into its scratch by the
   skill installer's `export_skill_bundle`.

   Two details matter. A denied tree containing an allowed path is replaced by
   its entries minus the allowed ones, recursively down to the allowed path and
   files included, because a read deny rule has no allow override. And because
   those entries are enumerated when the settings are rendered, every file the
   orchestrator writes into the run directory is created before then
   (`RUN_DIR_ENTRIES` in `cli.py`) — and the run directory is denied by default
   besides: before every session the dispatcher re-reads it and adds a deny
   rule for each entry but `scratch/` that has none, in both settings files
   (`dispatch/run_fence.py`), so a writer that skips the list is fenced from
   the next session on.

   The fence is a deny list, so a path nobody names is readable: other Kiln
   projects, Kiln's `~/.kiln_ai/settings.yaml`, and any clone of this
   repository with its `examples/`, which is an answer key
   ([limitations.md](limitations.md#containment)). A state folder and a Kiln
   project that overlap are refused at start-up (`paths.check_no_overlap`):
   the project is denied whole, and the scratch inside the state folder must
   be writable.
3. **The OS sandbox.** Bash and its children run sandboxed: the state folder,
   which holds the working directory, is deny-write, the scratch tree is
   re-opened, the read fence is repeated, and the outbound network allowlist
   is empty. A missing sandbox is a loud failure rather than a silent
   unsandboxed fallback.
4. **The loop gateway**
   ([`gateway/`](../src/kiln_harness_tuner/gateway/)). Loop agents never get a generic
   Kiln API tool. Each role's MCP config holds the gateway, started with
   `--role <role>` outside the sandbox as
   `<python> -P -m kiln_harness_tuner.gateway.server`, where `<python>` is the
   interpreter that runs the tuner (the skill installer is started the same
   way). `-P` keeps the current folder off `sys.path`: Codex and OpenCode
   start an MCP server in the session's workspace, which the agent writes.
   The gateway serves that role's typed operations and nothing else
   (`gateway/ops.py`, `ROLE_OPS`; the `kiln-harness-tuner-gateway` skill
   documents each). Three properties:

   - **Positive allowlisting.** Before the first dispatch the orchestrator
     computes the run's TRAIN item set (`gateway/trainset.py`) from every
     eval's `splits` and writes it to `runs/<run_id>/train_set.json`: an
     item in some eval's train split, and in no eval's non-train split
     (whatever it is called), carrying none of the split check's held-out
     tags. A split filter it cannot evaluate, or a train split of `all`,
     refuses the start. Every read builds its answer only from records whose
     item is in that set, or that this run created on a train item (the
     gateway's own registry, `gateway_created.jsonl`); everything else is
     dropped, and a direct read of one is refused.
   - **Field-by-field projection.** Every answer is re-built into the
     gateway's own pydantic models (`gateway/models.py`), copying named
     fields only; Kiln's raw responses are never forwarded, and a field
     Kiln adds cannot pass.
   - **Typed, validated writes.** Run configs are checked against
     the run's model policy (`--model-policy`, or the `--models keep` pin;
     section 17); a drive (`run_task`) runs a saved config on ONE train
     item and takes the input from the item, never from the agent — on a
     multi-turn or world task as a one-item TRAIN eval job (section 16); an eval
     job runs split `train` on the eval's train items; memory writes stay in
     the two scopes, and the `val` tag is code's. A new eval
     (`create_eval`) gets splits the gateway assigns; its val and test
     items are never readable back. There is no operation that changes
     settings or providers, and no operation for deletes, split edits or
     held-out items. The model operations read the run's model catalogue
     snapshot (section 17.1).
   - **Traces in files, and a cap on every answer**
     ([`gateway/trace_files.py`](../src/kiln_harness_tuner/gateway/trace_files.py)).
     Claude Code does not give an agent an MCP answer over about 25,000
     tokens: it saves it to a file under `~/.claude/projects`, which the
     read fence denies, so the agent sees nothing. A stored trace of a
     multi-turn agent task repeats the whole system prompt and every tool
     definition, then every tool result in full, and is far over that
     limit. An agent that cannot read a failing trace plans and builds from
     the prompt text alone. So `get_train_results`,
     `get_task_run` and `run_task` write each train trace to a file under
     `runs/<run_id>/scratch/traces/` (the gateway's `--traces-dir`) and
     answer with its path and a one-line summary (turns, tool calls, tool
     errors, final-reply length). The layout:

     ```
     runs/<run_id>/scratch/traces/
       <run_config_id>/
         <eval_id>/<item_id>.md        header, then one block per message
         _system/<hash>.md             system prompt and tool definitions,
                                       once per distinct text
         _stats.json, _stats.md        per item: turns, tool calls by tool,
                                       tool errors, tool-result sizes,
                                       tokens (per turn when the trace has
                                       them), cost
         _stats/<eval_id>/<item_id>.json   one item's statistics
       task_runs/<task_run_id>.md      get_task_run, run_task
       _expected/<item_id>.json        a reference too large for an answer
     ```

     A file is written only for a row the gateway already accepts as train,
     after the train filter: no val or test record is ever written. Each
     write is atomic (a temporary file, then a rename), and a file whose
     text has not changed is not written again. Every id in a path is
     reduced to letters, digits, `_` and `-`, and every path is checked to
     be inside the folder. `_stats.json` is rebuilt from the item files on
     every call, so two gateway processes that write at once lose no item.
     Kiln stores the per-turn usage on each assistant message of a newer
     trace; for an older one the stats say `per_run_only` and give the
     run's total.

     No answer is over `ANSWER_MAX_CHARS` (40,000 characters). JSON runs at
     3 to 4 characters a token and can fall to about 2 when it is dense or
     escaped; 40,000 characters is then still under 20,000 tokens.
     `get_train_results` and `get_train_expected` answer in pages
     (`offset`, `limit`, `next_offset`), and a page also ends at the cap.
     `get_train_expected` clips a reference at 12,000 characters and writes
     the whole of it to `_expected/`. Any other answer over the cap is
     returned as its first piece and a continuation; the role's
     `get_answer_part` operation returns the next pieces from the copy the
     gateway process keeps, so a write is never run twice to read its
     answer.

   Val access exists only as `get_val_summary`, the gate's code-assembled
   tables (`runs/<run_id>/val_summary.json`), for the promotion agent and
   the report writer.

   **The pre-tool hook** ([`gateway/hook.py`](../src/kiln_harness_tuner/gateway/hook.py))
   is the third, independent check on MCP tools: in each role's settings it
   admits the role's gateway operations and, for the builder and the probe,
   the skill installer, and refuses every other `mcp__…` tool; the shared
   settings files carry role `none` and admit none. The permission deny list
   names `mcp__kiln_api_mcp` as well. File tools are fenced by the rules
   above.

5. **The start-up split check**
   ([`quarantine.py`](../src/kiln_harness_tuner/quarantine.py)). Before the first
   dispatch — the MCP-lane preflight included — code reads the project from
   disk and resolves every eval's train, val and test items. It sets the
   held-out item ids, writes the train set, and counts the stored results on
   held-out items: eval runs on an item that is not in its eval's train split
   and is in its val or test split (or held out overall), the task runs they
   scored, task runs driven from a held-out eval input, and unsourced task
   runs whose input equals a held-out input. Those results stay in the
   project: the pass moves nothing. The gateway (fence 1) and the read fence (fence 2) keep them from agents. An
   item in the train split of one eval and a held-out split of another counts
   as train, and as an overlap. An item that carries a `val`, `test`, `val_*`,
   `test_*`, `holdout*` or `heldout*` tag (any case) and is in no train split is held out even when
   no current eval uses the tag (an ORPHAN). An eval that appears after start-up (an
   agent built it, perhaps through a spec, whose splits the server derives)
   is checked from disk the first time the active evals are listed: a train
   split that holds a held-out item, or that the pass cannot evaluate, keeps
   it out of every measurement (`eval_split_refused`). It refuses the start
   with a fix line when it cannot decide (a split filter it does not evaluate,
   an unreadable record).

What each of these is worth, and what happens when an agent CLI has none of
them, is [agent-cli.md](agent-cli.md) section 6.

## 13. Layout

```
src/kiln_harness_tuner/
  cli.py            the console script: flags, run lock, rendered per-run
                    config, the read fence, the composition root
  paths.py          the state folder: its path, its layout, its checks
  install_agents.py the agent files: a run's install into the state folder,
                    and harness-tuner-install-agents --dest
  kiln.py           KilnClient, the one REST client
  kiln_names.py     Kiln-valid names for the objects the loop creates
  budget.py         the per-candidate spending caps and their holds
  reconcile.py      the spend ledger checked against this run's records
  objective.py      the quality, speed and cost objectives
  levers.py         the check that a declared lever took effect
  integrity.py      the reward-hacking code checks at the promotion gate
  orchestrator.py   the wave loop, the promotion gate, the tripwire, the
                    janitor, the budget
  items.py          one plan item from planned to measured
  generality.py     the leak check between the build and the measurement
  reference_check.py  the check for skill and tool names a build leaves
                    dangling
  judge_copy.py     the eval-text copy check at the same point, and the
                    eval-text reader the gate shares with it
  prompt_text.py    the prompt and skill text a run config gives the
                    model, read by code
  measure.py        the one measurement engine: queue, chunks, stop rules
  job_drives.py     drives through one-item eval jobs, for multi-turn and
                    world tasks
  stop_rules.py     the engine's stop rules and their tests
  estimate.py       the stratified estimate of a candidate's net change
  viability.py      screening rounds (N candidates through the engine)
  analysis.py       eval jobs, the cache, paired statistics, the bundle
  probes.py         code-answered probes and the reconnaissance battery
  setup.py          baseline seeding, the eval ↔ issue sync, the split audit
  detectability.py  the pre-run check: the floor, the refusal
  noise.py          the opt-in run-to-run noise check
  quarantine.py     the start-up split check: held-out ids, stored results
  latency.py        per-item latency, the paired comparison, the drive pass
  model_policy.py   the (provider, model) policy and its checks
  memory.py         when a record is written
  memory_schema.py  what a record says
  schemas.py        every agent input and output, and RunStatus
  status.py, ui.py, wave_log.py, memory_feed.py, report.py, logbook.py   observability
  contract.py       the no-cost live response-shape pre-flight
  model_preflight.py  one test call per model before the first spend
  model_catalog/    the model catalogue: the fetch of Kiln's published
                    model file and models.dev, the join, and the run's
                    snapshot (model_catalog.json)
  concurrency.py, provider_errors.py, snapshots.py
  knowledge/        the knowledge system's code: the schema, the loader,
                    the structural checks, the generated index blocks, and
                    the command line (check, build)
  smells/           smell detection: the detection model, the detectors
                    and their registry, the thresholds, and the step that
                    runs the detectors and the baseline reviewer
  onboarding/       the start before a run: the Kiln and task checks, the
                    API-key check, the guided questions (the agents' model
                    list in agent_models.py, the Models question and its
                    picked policy in models.py), the summary and the rerun
                    command
  agents/           one prompt template per agent role
  agent_files/      the agents' skills and instructions (package data),
                    installed per agent CLI into the state folder; the
                    knowledge system is the skill
                    kiln-harness-tuner-knowledge
  demo_project/     the moderation example's Kiln project, shipped as
                    package data for --demo-project
  dispatch/         the seam (base.py), --agent-cli (agent_cli.py), the
                    claude -p, codex exec and opencode run implementations,
                    and the run-directory read fence (run_fence.py)
  gateway/          the loop gateway: train set, operations, MCP server, hook,
                    and the trace files it writes (trace_files.py)
  skill_installer/  the MCP server that installs a skill bundle atomically
  tui/              the terminal app (Textual): the entry (entry.py), the
                    app and its threads, the screens (screens/: start-up,
                    the start-up prompts, run, the details panel and its
                    picker, the stop dialog, the modal base), the widgets and the stylesheet
                    (tuner.tcss, package data), and the run model (model/,
                    pure Python, details.py for the panel)
src/kiln_api_mcp/   the vendored call_kiln_api MCP server (interactive sessions)
tests/              the offline suite
  tui/              the app's tests: the model, pilot tests of each screen,
                    the entry, and pseudo-terminal tests (marked slow)
  packaging/        the CI smoke test of the installed wheel, and the
                    fake claude and fake Kiln it runs with
specs/              the project specs, and backlog.md: the known gaps and
                    the pending ideas of the knowledge system
.agents/skills/     the skills for coding agents that work on this
                    repository: knowledge-maintenance
```

## 14. Portability guardrails

Four invariants, kept so that moving the loop to a server stays mechanical:

1. **REST only, behind the Kiln-access seam.** No `kiln_ai` import. The
   orchestrator speaks REST through `KilnClient`; agents speak the gateway's
   typed operations, whose implementation is loop code over the same
   client, so a host swaps the transport, not the operations and not the
   skills.
2. **Dispatch behind the seam.** `claude -p`, `codex exec` and `opencode run`
   are three implementations.
3. **State in memory and Kiln artifacts only.** The wave log and the scratch
   tree are disposable observability, never state a host would have to carry.
4. **UI behind `RunStatus`.**

## 15. Testing

The suite is fully offline: a fake dispatcher, a fake Kiln client and canned
transcripts. No live server, no model spend, so it runs on every change.

Tiers: the statistics assembly and the baseline-if-needed tree against a fake
client; the memory formats string-asserted, because they *are* contracts;
whole waves against scripted agent results — promotion, head-to-head, tripwire,
time limit into wrap-up, janitor, `loop_exit`; the dispatcher's plumbing against
recorded `claude -p` output, which covers prompt rendering, the fenced-JSON
extraction, the repair retry and usage parsing.

Five guards hold properties that are not behaviour: `test_paths.py` (no
module derives a path from outside the package),
`test_dispatch_boundary.py` (the seam), `test_preflight_messages.py` (every
pre-flight error carries a fix line, and the site count is stated so the guard
cannot narrow in silence), `test_docs.py`, and `test_cli_reference.py` (every
console script is named in `docs/cli.md`, and every flag of `harness-tuner`
and `harness-tuner-install-agents` is there, with its default;
`docs/objectives.md` has every objective and the example policy).
`test_docs.py` also holds `docs/agents.md` to the roles (a row and a
section for each) and the layout blocks to the package's directories.

The knowledge system has its own tests (section 18): `test_knowledge.py`
runs every structural check on the shipped skill and compares each
generated block with the files; `test_knowledge_checks.py` builds a small
tree with one deliberate violation for each rule and asserts that exactly
that rule is reported; `test_backlog.py` holds `specs/backlog.md` to its
format. `test_smells.py` and `test_baseline_review.py` cover the detectors
and the review step against the fake dispatcher and the fake Kiln.

**The packaging smoke test** (`tests/packaging/smoke_installed_wheel.py`)
runs in CI, not under pytest, because the suite runs from the checkout and
cannot see a package that works only from one. CI builds the sdist and the
wheel with `uv build`, and the script checks what each holds, the app's
stylesheet included. It installs the wheel as a uv tool with a temporary
`HOME`, and runs the commands from an empty folder. It imports the terminal
app on the tool's interpreter and checks that the app's stylesheet is a file
of the installed package. Then it makes a run with a fake `claude` on `PATH`
(`tests/packaging/fake_claude.py`) and a fake Kiln
(`tests/packaging/fake_kiln_server.py`) that answers the start-up reads from
the run's copy of the demo project. The run installs the agent files, passes
the agent CLI check, takes the lock, renders the configs and checks the
splits. The fake claude starts the gateway from the rendered config and calls
`ping`, which reaches the fake Kiln. The run stops with exit code 1 at its
first write to Kiln, which the fake refuses. The script then checks
that the state is in the state folder, that the sessions ran in the agents
folder, that the gateway runs on the tool's interpreter, and that nothing was
written to the current folder, `HOME` or the checkout. It makes the run with
`KILN_HARNESS_TUNER_HOME` and without it.

**What no test covers** is a live run. The rendered settings, the rendered MCP
configuration and the read fence are proven against a real `claude -p` only by
running one. The suite covers their construction, not their effect.

## 16. The speed and cost objectives

`--objective speed` (default `quality`) makes the loop optimise per-request
wall-clock latency and hold quality. `--objective cost` does the same with
the per-request provider cost (`run_cost`, the task run's own `usage.cost`)
in place of latency: every comparison, rule and stage below applies with
"cheaper" for "faster" (`LatencyComparison.metric` is `latency_ms` or
`cost_usd`; [`objective.py`](../src/kiln_harness_tuner/objective.py) names the
decisive metric). The metric is per-item
`total_llm_latency_ms` of the task run — the sum of its LLM call latencies,
which for a single-turn task is the wall-clock time of the call. For a
multi-turn item it is the sum over the whole conversation; the synthetic
user's calls are not in it. It is read
from a task run's own `usage`, and from an eval run's `task_run_usage` only;
an eval run's `eval_usage` is judge time and is never read. Tokens per
second is never the metric: a model that finishes in fewer tokens is fast at
any token rate. [`latency.py`](../src/kiln_harness_tuner/latency.py) holds all of it.

**The comparison is paired.** Latency moves with provider load and with the
loop's own concurrency, so a new arm is never compared with a stored number.
`paired_drive_pass` drives the reference and the candidate on the SAME items
in the SAME window — per item, the arms back to back, the order rotating from
item to item — on a fixed sample (`SPEED_SAMPLE_ITEMS`, chosen by a hash of
the item id). No judge runs; the drives are provider spend
(`latency_drives`). Three numbers come out:

- `median_ratio`, the median over items of candidate / reference;
- the exact two-sided **sign test** on how many items got faster and how many
  slower, ties dropped — a few slow outliers cannot carry it;
- **the tail**: each arm's p90 and the ratio, with a 95% paired bootstrap
  interval (`BOOTSTRAP_RESAMPLES` resamples of item pairs, fixed seed).

`clearly_faster` = the sign test significant at 0.05 with more items faster,
a median ratio below 1, and no significant p90 regression — the bootstrap
interval of the p90 ratio lying entirely above 1 blocks it. A faster middle
with a slower tail is not clearly faster. Fewer than `MIN_PAIRS` pairs gives
no verdict. The spread (p75 − p25) is reported beside both arms.

**Drives through eval jobs**
([`job_drives.py`](../src/kiln_harness_tuner/job_drives.py)). A drive normally uses
`POST …/tasks/{t}/run`. That endpoint runs one input once: it starts no
world episode and no simulated user. So `EvalJobDriver.needed` sends a
drive through an eval job when the task's `turn_mode` is `multiturn`, when
a run config of the drive lists a world tool (`kiln_tool::world::…`), or
when an input has a `world_reset`. The route covers the three drive paths:
`timed_drives` (the baseline timing, the timing chunks, the val gate's
pass), the code probes (`probes.drive_set`), and the gateway's `run_task`.

- **One drive, one job.** `EvalJobDriver.drive` starts an eval job on the
  item's own eval (the first by id whose split holds it) with that eval's
  default config, `item_ids` = the one item and `split` = `train`. The
  val gate's pass is the one caller that names `val`; it is code, and no
  agent sees its items. A drive never names `test`. The job starts the
  episode and runs the synthetic user exactly as a measurement does.
- **Judge-free when the server has it.** Code reads the server's OpenAPI
  schema once: `EvalJobParams` has a `judge` field on a server that runs
  judge-free jobs. Then the job is sent with `judge: false`. It drives the
  item again even when the item has a score, so the drive is of this
  window. The job result's `driven_runs` names the task run, and code reads
  it with `GET …/runs/{id}`: `usage.total_llm_latency_ms` and `usage.cost`
  are the metric, as on the run endpoint. Such a job is never
  deduplicated, so its request is sent once, without the client's retry.
- **Judged on an older server.** One log line (`eval_job_drives_judged`),
  then judged jobs. Code reads the usage, output and trace from the item's
  stored row (`task_run_usage`). That row can come from an earlier drive:
  the server reuses a stored trace, and it skips an item that has a score.
  So a pair can come from two windows, and the judge is paid for. An eval
  whose default config is a legacy judge gets judged jobs on a new server
  too: the server drives without judging only through a V2 judge.
- **The pairing is the same.** Per item the arms still run back to back
  and the order still rotates. Each arm's drive is one job, and the next
  arm's job starts when it ends.
- **Spend.** The task call's cost is the per-request metric. The synthetic
  user's cost (`synthetic_user_usage`) and, on a judged job, the judge's
  cost are provider spend too: they are added to the pass's spend, never
  to the metric.
- **Concurrency.** Each job holds one eval-job slot of the analyzer
  (`Analyzer.job_slot`), so `--max-concurrent-evals` caps these jobs with
  every other eval job of the run. A gateway process gets the same number
  as its own cap (`--max-concurrent-evals` on the gateway's command line).
- **Errors.** A failed drive carries the job's own error text, which holds
  the provider's message. A quota or auth failure stops the run, as on the
  run endpoint. No diagnosis job follows.

**Setup.** Setup measures the baseline on the full train split in every
objective. Under speed and cost it times the baseline on the sample at the
same time (`ensure_baseline_speed`, also once per new baseline): the
planner's `baseline_latency` and `baseline_cost`. The two run concurrently
because the measurement takes most of setup's wall-clock and the timing is
only drives; the timing is therefore taken under the load of the
baseline's own eval jobs, and it is orientation for the planner, never a
verdict. While the measurement runs, a line every
`progress_interval_secs` (30) gives items done of total per eval, read from
each running job's own progress (`GET /api/jobs/{id}`): "baseline
measuring: eval_a 40/120, eval_b 12/30 (1 errored) — 2
job(s) running".

**One measurement engine**
([`measure.py`](../src/kiln_harness_tuner/measure.py)). Every candidate run config
is measured on train by `measure(ctx, candidates, spec)`: an arm, on its
whole targeted train split, and a screening round, N candidates on one
capped queue. An arm's
measurement that completes IS its train measurement: the analysis reads its
stored rows and re-drives nothing.

- **The queue**, drawn once from TRAIN only, seeded (`<run id>:<item id>`,
  or `<run id>:screen` for a round) and recorded (`queue_drawn`, with every
  item, its stratum and its block). First the items the planner named
  (`target_items`). Then the DISCRIMINATING block: up to `DISC_FAILS` (8)
  items the parent fails on the targeted evals and up to `DISC_GUARDS` (4)
  it passes, round-robin across the evals. Then the REMAINDER: per eval the
  rest of the train split, the parent's failures, passes and unscored items
  interleaved in proportion (`_interleave`), round-robin across the evals.
  The universe is the eval's train split on disk plus any train row the
  server stored. The parent's rows come from `ensure_measured` (a stored
  row is never re-driven). Under speed and cost the block is guard-first
  (below).
- **Chunks.** The first chunk is the named items and the discriminating
  block; each later one `JUDGE_CHUNK` (12) items; a screening round has
  `looks` equal chunks. Each chunk is judged per eval by one eval job
  narrowed to its items (`Analyzer.trial`, rows stored and reused by any
  later measurement). The parent's judgments are its stored rows. When
  timing or cost matter — a speed or cost objective, a model-level swap
  whose lever needs checking, and every screening round —
  each chunk first drives the next `TIMING_CHUNK` (15) items judge-free, up
  to `TIMING_MAX` (30): the candidates, the reference (the run's baseline)
  and each candidate's parent when that is another run config, back to
  back per item in one window (`timed_drives`). Timing pairs come from the
  uncapped queue order, so a small screening round is still timed on 30
  items. Latency, cost and output validity are recorded per item.
- **Stop rules after every chunk**
  ([`stop_rules.py`](../src/kiln_harness_tuner/stop_rules.py)): the objective's
  defaults (`default_stop_rules`) merged with the planner's rules for the
  candidate (`FixWork.stop_rules` or `ScreenRequest.stop_rules`; one of the
  same kind replaces a default, any other is added). The rules read off the
  timing drives alone (`TIMING_KINDS`) are checked right after the chunk's
  drives, so a candidate they stop is never judged on that chunk. The
  first rule that fires stops the candidate with its numbers. The kinds:
  - `eval_metric` (quality objective) — quality clearly worse: a one-sided
    z-test on the stratified estimate (below) of the net change against
    the parent, alpha 0.05 split over the quality looks, never before
    `QUALITY_MIN_N` (6) judged items;
  - `guard_losses` (speed and cost, in place of `eval_metric`)
    — losses on the judged items the parent passes clearly above the
    noise flip rate: a one-sided exact binomial test, alpha 0.05 split
    over the quality looks, never before 6 judged passes. Fixes of the
    parent's failures are not counted (below);
  - `latency_ratio` (speed), `cost_ratio` (cost) — the candidate over the
    baseline on the paired drives; with `alpha`, a one-sided sign test that
    the per-item ratios lie above
    the threshold, alpha split over the timing looks, never on fewer than
    `TIMING_MIN_PAIRS` (6) pairs; without `alpha`, a kill line;
  - `not_better` (speed, cost) — once the timing pairs are in: not clearly
    faster (cheaper) than the baseline, or clearly slower than its parent
    (`latency.clearly_slower`); a pass that lost pairs to errors stops with
    its incomplete note; a split too small for a verdict gets none;
  - `futility` (arms only, early only) — the targeted eval cannot end
    better than the parent even if every item still to judge went the
    candidate's way (under speed and cost: an eval cannot end within its
    noise allowance);
  - `residual` — once the discriminating block is judged: fewer than half
    its failures fixed, or guard breaks clearly above the noise flip rate
    (a one-sided exact binomial test at alpha 0.05, one look; see below)
    (under speed and cost: more broken than fixed). Its line
    says which: `k of n guards broke, within noise (p=…)` or `clearly
    above noise (p=…)`. On by default, under the quality objective only, for a fix
    arm that changes no model-level key (`items.SWAP_KEYS`) and was not
    opted out (`residual_gate: false` with a reason). It is not a default
    under speed and cost: it sets fixes against losses,
    and `guard_losses` covers the losses;
  - `targets` — a named item still fails after the first chunk;
  - `invalid_output_rate` — any output that fails the task's output schema;
  - `max_items` — an item budget: the candidate is `capped`, not stopped,
    and never a dead end.
  Before any rule, after the first timing chunk, the declared lever is
  checked (below, "lever not applied").
- **Repeated looks.** A test checked at k looks is run at alpha / k — a
  Bonferroni split over a FIXED look schedule, so by the union bound a
  candidate that is not worse is stopped at any look with probability at
  most alpha, whatever the correlation between looks and however discrete
  the statistic. The quality looks are geometric (when the judged items
  first reach 12, 24, 48, …; every chunk in a screening round); the timing
  looks come after each timing chunk (15 and 30 pairs). Alpha spending
  (O'Brien–Fleming, Pocock) would give more power at the last look, but its
  boundaries assume a normal statistic with a known information fraction
  per look; here the early looks hold a dozen binary items, and the last
  look of an arm is a census, where the train rule decides and no test is
  needed. The deterministic rules take no alpha: they cannot stop a
  candidate that could still meet its bar.
- **The estimator** ([`estimate.py`](../src/kiln_harness_tuner/estimate.py)). The
  hard-first order would bias a plain mean: it over-weights the parent's
  failures, where a candidate can only fix. So each item of an eval belongs
  to a stratum by the parent's stored result (fail, pass, unscored, named),
  whose size is known exactly; each stratum is consumed in its own seeded
  random order, so its judged items are a simple random sample of it at
  any stopping point. The net change is Σ W_s · mean_s(d), W_s = N_s /
  N_known, with the variance Σ W_s² · v_s / n_s · (1 − n_s / N_s) and v_s
  an Agresti–Coull-smoothed Bernoulli variance; an unsampled stratum counts
  as mean 0 with variance 1/4. Pooled over evals each eval weighs its
  N_known. When every item is judged the estimate is the exact full-split
  result. A missing candidate row counts as a fail.
- **The spending cap**
  ([`budget.py`](../src/kiln_harness_tuner/budget.py)). Every candidate carries a
  dollar budget: the planner's `max_usd` (with a `budget_reason`) or code's
  default, min(1.5 × the expected full measurement, $20, 10% of the
  remaining run budget). The expected cost is priced from the parent's
  stored rows before the first chunk and at the candidate's own measured
  median cost per judged item after each one. A planner's value is clamped
  to min($60, 25% of the remaining run budget); the default is inside that
  by construction. Before each chunk the engine projects its cost (items ×
  price per item, pairs × price per pair) and caps a candidate whose spend
  plus the projection passes its budget; the last chunk, once paid,
  completes the measurement. A candidate being measured holds its budget
  less its spend (`BudgetHolds` on `Ctx`), updated after each chunk and
  released when it stops; the remaining run budget every new candidate is
  sized against is net of those holds. Before each chunk the run as a
  whole is checked too: booked spend + the other holds + the chunk's
  projection must stay inside `--budget-usd`, or the candidate is capped
  ("capped by the run budget"). A candidate's spend is its own fresh judged
  rows and timing drives, plus an equal share of the baseline's timing
  drives among the live candidates, of its parent's among that parent's
  candidates, and of any drive cost no arm's rows account for; the shares
  sum to the pass. The planner input carries the measured prices
  (`measurement_prices`).
- **Outcomes.** `completed` (the whole queue judged), `stopped` (a rule:
  its kind and line), `capped` (the spending cap, an item budget or a
  round's size — inconclusive, never a dead end), `failed` (the
  first chunk stored no row — infra). A stopped arm ends `NOT LANDED
  (stopped at n=… by <rule>: …)` — `(<k>/<n> fixed, <b>/<g> guards broke)`
  for the discriminating block, `(<k>/<n> targets fixed)` for named items —
  or `DEAD END (not viable: lever not applied …)`; an arm at its spending
  cap ends `CAPPED (capped at $X after n items: inconclusive — net +a/−b
  so far)`, and a screening candidate `capped` (`SCREEN CAPPED`, a lesson);
  a completed one goes to the analysis, with `measurement: <line>` on its
  record.
- **Progress.** One line per candidate per chunk (`measure_chunk`; an arm's
  also as its `item_detail`): `w7-1: chunk 3/9 · 24 judged · +2/−1 vs
  baseline · 0.88× latency · continue`. The val gate reports the same way,
  one line per candidate per eval (`gate_progress`: `gate rc_1: eval 2/8 ·
  64 judged · +3/−1 vs baseline · continue`); it is a census and never
  stops early.

`tests/test_measure.py` simulates these rules with fixed seeds and checks
their error rates.

**Quality under speed and cost: losses, not net.** Under an efficiency
objective the quality question is whether the candidate KEEPS what the
parent passes. A net change is the wrong measure there. On a baseline
failure a candidate can only tie or win, and a baseline failure often
passes on a re-run of the same config, so such a "fix" is mostly noise. A
net that sets those fixes against the losses on the passes hides a real
drop, most of all when the screen judges only a few passes. So, under speed
and cost only:

- **The queue is guard-first**
  ([`measure.py`](../src/kiln_harness_tuner/measure.py)). The discriminating block
  is up to `DISC_GUARDS_EFFICIENCY` (24) items the parent passes, then up
  to `DISC_FAILS` (8) it fails, round-robin across the evals. The passes
  are taken most FRAGILE first. An item's fragility is the share of the
  other run configs with a stored TRAIN row on it (the eval's default
  config) that fail it, with one passing config added: 1 of 1 reads 0.50,
  9 of 20 reads 0.43, no row reads 0. The parent and the candidates being
  measured are not counted, and a config's replicates count once. The
  rows are read from the project on disk in one pass over the config's
  run folders (`stored_rows_on_disk`), not with one server read per run
  config; a file is read once per run (`Ctx.eval_run_index`). Ties keep
  the seeded order, so with no other config's row the order is the seeded
  one. `queue_drawn` records the counts (`fragility`). The first chunk of
  an arm keeps the quality block's size (12), so a candidate that the
  timing stops is not judged on 32 items first. A screen at 3 items per
  eval is then all guards: the baseline's failures come after the guards
  and are not judged at all in a small screen, because a fix there is
  noise-level evidence and does not answer the question.
- **The verdict counts losses** (`guard_losses`,
  [`stop_rules.py`](../src/kiln_harness_tuner/stop_rules.py)). It replaces the
  default `eval_metric` and `residual` rules, which both set fixes against
  losses. It stops a candidate when its loss rate on the judged passes is
  clearly above the noise flip rate p0: a one-sided exact binomial test,
  alpha 0.05 split over the quality looks like every test here, never
  before `QUALITY_MIN_N` (6) passes. p0 is the run's `--noise-check`
  re-drive change rate pooled over the pass/fail rows of the targeted
  evals (`noise_flip_rate`), never below `GUARD_NOISE_FLOOR` (0.02);
  without a noise check it is `GUARD_NOISE_DEFAULT` (0.10). A fragile guard also flips more often
  by noise, so the test's null is the mean over the judged guards of
  max(p0, the item's fragility). A binomial test at the mean of unequal
  probabilities is conservative (Hoeffding, 1956). At p0 = 0.10, 24
  guards stop at 7 losses; at p0 = 0.02, at 3. Fixes are reported on
  their own and never offset a loss.
- **A model or provider swap is judged on more guards**
  ([`viability.py`](../src/kiln_harness_tuner/viability.py)). A screening candidate
  that changes `model_name` or `model_provider_name` against its parent
  (`MODEL_SWAP_KEYS`) carries `min_guards` = `SWAP_MIN_GUARDS` (24): the
  engine raises its own per-eval cap (`guard_cap`) until its items hold
  24 of the parent's passes, or every pass in the queue, without passing
  `SWAP_MAX_ITEMS` (40) items. 0 losses in 24 bound the loss rate at 12%
  (95%), and the round-1 size on 4 evals (12 guards) only at 22%; 0 in 40
  bound it at 7%, and past that the arm and the val gate measure the whole
  split anyway. A thinking-level, sampling or output-mode candidate keeps
  the round's size; its items are the first of the swap's items, and it
  stays live for the timing pairs. The budget of each candidate is priced
  on its own items. A swap stopped by an item budget before its minimum
  is `capped` (inconclusive), not a survivor.
- **The report says what was measured.** The candidate's line reads `lost
  k of n baseline passes (no loss rate above X% at 95%) · fixed f of m
  baseline failures (noise level)`, with no net (the progress line: `lost
  1/20 passes, fixed 0/4`). X is the one-sided exact (Clopper–Pearson)
  upper bound on the loss rate over the judged passes. The screen record
  (`screen` tag) carries it: overview `SCREEN SURVIVOR: <label> — lost k of
  n baseline passes, no loss rate above X% at 95%`, a `quality` line with
  the fixes apart, and `loss_bound` in its result. Survivors in one speed
  band rank by the lower bound, then by net. A survivor means "no loss
  rate above X%", never "quality held". The passes are taken fragile
  first, so the bound is for those passes; when fragility predicts a
  loss, it is conservative for all of them. For the same reason a
  stopped measurement's `net` leans toward a loss: the pass stratum is
  not a simple random sample of the passes at an early stop.

The val gate does not use these rules. Its limit stays: a split of about
150 paired items cannot rule out a drop of a few points (see
[limitations.md](limitations.md)).

**The residual rule's guards against noise**
([`stop_rules.py`](../src/kiln_harness_tuner/stop_rules.py)). Under the quality
objective the `residual` rule reads a fix arm's discriminating block: up
to 8 items the parent fails and up to 4 it passes (the guards). A config
equal to the parent also breaks guards by noise: at a flip rate of 0.17 it
breaks at least one of 4 about half the time. A rule that stopped on one
broken guard would stop good fixes at random. So:

- **The guard half is a test.** It fires only when the breaks are clearly
  above the noise flip rate p0: a one-sided exact binomial test of k
  breaks in the n guards with a row. p0 is the one of `guard_losses`: the
  run's `--noise-check` re-drive change rate pooled over the pass/fail
  rows of the targeted evals, never below `GUARD_NOISE_FLOOR` (0.02), else
  `GUARD_NOISE_DEFAULT` (0.10). The null is the mean over the guards of
  max(p0, the item's fragility); the quality queue records no fragility,
  so the null is p0. A guard with no row is not counted.
- **One look, the full alpha.** The block is judged in the first chunk and
  does not change after it, so the test sees the same numbers at every
  check. It runs at alpha 0.05, with no split. At the defaults the fewest
  breaks that fire are: 2 of 2 (p = 0.01), 2 of 3 (p = 0.028), 3 of 4
  (p = 0.0037). 1 of 4 gives p = 0.34 and 2 of 4 p = 0.052: no stop. At a
  p0 of 0.02, 2 of 4 fire; at a p0 of 0.17, 3 of 4.
- **The fix half is not a test.** Fewer than half the failures fixed
  stops the arm, whatever the guards.
- **The rule carries its numbers.** The default rule has `alpha` and
  `noise_rate` (a field of `StopRule` for `residual` only). A planner's
  `residual` rule without them takes the default's (`merge_rules`). The
  stop line, the `residual gate:` memory line and `residual_misses` read
  `8/8 fixed, 3 of 4 guards broke, clearly above noise (p=0.0037)`, or
  `… within noise (p=…)` when the fix half stopped the arm.
- **A real regression is still caught.** `eval_metric` reads the net over
  the rest of the split at its looks, `futility` bounds the targeted eval,
  and the val gate decides promotion.

**Screening in code.** Candidate variants that need no build — a model,
provider, thinking level, temperature, `top_p` or output mode on a fork of
a parent — are screened by code, not by agents. A plan with `screen` set
(`ScreenRequest`: candidates, round, `target_evals`, `mode`, `stop_rules`,
`looks`) runs `run_screening_round`
([`viability.py`](../src/kiln_harness_tuner/viability.py)): code forks each
candidate's run config (policy-checked; a thinking level must be one the
provider lists), then puts every candidate through the engine on ONE queue
drawn from the baseline's own results, cut at `SCREEN_ROUND1_PER_EVAL` (3)
items per eval in round 1, doubling each round (`mode: "hard_cases"`: the
discriminating block only). Items outside `target_evals` are never driven
or judged, and a candidate screened on some evals only is never treated as
screened on the others. The candidate's verdict: `dead_end` when a rule
stopped it, `error` when its measurement failed, else `survivor`, with
`net` and its 95% interval from the stratified estimate and a `rank` (under
speed and cost by the objective's ratio in bands of `SPEED_BAND` (0.03),
then by the loss bound, then by net; under quality by net). Under speed
and cost a survivor's quality is its losses on the baseline's passes, with
their bound (above). Before any rule, after the first timing
chunk, code checks that the declared lever took effect
([`levers.py`](../src/kiln_harness_tuner/levers.py)), and stops a candidate only
when every available signal says the lever did nothing. A thinking level
the candidate sets above none (and different from its parent's) is applied
when a drive shows reasoning (content or reasoning tokens) OR the
output-token count moved by 1.25× or more either way against the parent on
the same items (a provider that hides reasoning still bills its tokens);
not applied when neither moved; unverified when no drive shows reasoning
and fewer than 2 paired items carry token counts. A model or provider
switch is not applied when a drive records another model or provider than
the run config names (in `output.source`, the usage or a trace message),
and unverified when no drive records one. Not applied stops the candidate
as `lever not applied: …` (event `lever_not_applied`) before anything more
is paid for it; unverified is logged (`lever_unverified`) and shown on the
candidate's line, and the candidate goes on. The same check runs on an
arm's timing drives. Every result is recorded in task memory with the
`screen` tag (`SCREEN DEAD END: …` as a `dead_end`, `SCREEN
SURVIVOR: …` as a `lesson`; the content shape is in the memory skill). At
run start `load_prior_screening` reads them back: the planner gets them as
`prior_screening_results` (marked `prior_run`, with run id and date;
latency from another window is orientation only), and `screened_rcs` is
restored for each survivor screened on every eval the task still has whose
run config still exists. A survivor quality-neutral or better (judged
passes at least the baseline's) in two or more rounds, this run's and
earlier runs', is a `settled_survivors` entry; code drops a candidate that
re-screens one — by run config, or by the same overrides on the same
parent — unless it gives a `rescreen_reason` (`drop_settled_rescreens`,
logged as `screen_candidate_dropped` and passed to the planner as
`screening_notes`). The planner is called again with the table (`phase:
"rescreen"`, at most `MAX_SCREEN_ROUNDS_PER_WAVE` rounds a wave) and
promotes survivors to arms by `existing_rc_id`; a survivor arm goes through
the same engine, and its screening rows are reused. Under speed and cost
the run opens with one round: the baseline's model at every lower thinking
level its provider offers (`initial_screen_request`). A probe check may not
name a variant key (`SCREEN_ONLY_OVERRIDES`, refused by the schema); probes
answer mechanism questions only, and nothing times a probe. A DATA question
is a `check` with `source: "stored"`: code counts over the parent's stored
train rows, with no fork and no drive.

**Litter.** At every wave end `archive_probe_run_configs` moves every
`PROBE …` run config out of the project to
`runs/.quarantine/…/archived_run_configs/` — the API has neither an archive
flag nor a delete for run configs — and never a measured one, the baseline
chain or the task's default.

**An arm forked from another parent** (`FixWork.parent_rc_id`
set to an rc that is not the baseline — for example a prompt fix on a fast
screening survivor). The objective's question is asked against the run's
BASELINE: the timing rules compare the arm with the baseline, driven in the
same window. The queue and the quality rules use the arm's parent (its
stored train rows). Against its parent the arm only must not be **clearly
slower** (`latency.clearly_slower`): at least six pairs, the sign test
two-sided p < 0.05, and a median ratio above 1.5; the parent is driven
beside the arm on the same timing items. The rule stops only a clearly
slower arm, and it never fires on fewer than six pairs. The val gate
compares every candidate with the baseline. Fix and merge arms are
measured alike; probes, eval builds and measurements are not arms.

**No per-request limit.** The loop sets no per-request limit on latency or
cost. An absolute limit compares a mean per request that the item mix
controls: a few large items move it, and on a multi-turn task a "request" is
the whole conversation. A ratio limit is sound, because it is paired per
item, but it needs the paired timing path on every arm, which is about two
to three times the drives. When latency is the goal, use
`--objective speed`. When cost is the goal, use `--objective cost`, and use
the model policy (`--model-policy`, section 17) to keep costly models out.

**Timing under each objective.** `latency_ratio` is the default rule of
`--objective speed` and `cost_ratio` is the default rule of
`--objective cost`. Under those two objectives every arm is timed, and the
val gate times every candidate. A screening round is always timed. Under
`--objective quality` an arm is not timed: no reference or parent
re-drives. The one exception is a model-level swap (the model, the
provider or a sampling parameter changes against the parent): its lever is
checked on the timing drives. The val gate under quality times nothing.

The latency and the cost stay visible with no drives. Each train and val
row carries `latency` and `cost`: the per-request latency and cost of the
task call on the two sides, read off the same stored eval runs
(`analysis.compare_rows`, `method: eval_runs`). These are information,
never a verdict: the two sides may have been driven at different times.
The record of an arm that was not timed gets one `latency <eval>:` and one
`cost <eval>:` line per eval, from its train rows. The promotion agent and
the report writer read the val rows. When the val gate runs its own timed
pass (speed and cost), that pass is on the tables too.

**Short samples.** Every failed drive is retried once. A drive that still
fails is recorded with its reason, and one of them is re-run as a one-item
eval job, whose errors carry the provider's text (the server answers a
failed `POST …/run` with a generic 500). A drive that was already an eval
job has that text, so no re-run follows. A pass that paired fewer than
`paired_floor(planned)` = max(6, ⌈2/3 · planned⌉) items — 20 of 30 — is
`incomplete`: its numbers are kept, it is never a verdict, and it cannot
make an arm successful or a candidate promotable ("speed incomplete: 8 of 30
pairs (22 errored)"). Two thirds keeps a real speed-up detectable (15 of 20
one way is p = 0.041) while refusing a remainder that failures have already
biased: long, slow items fail first.

**Where it runs.** On train, in the measurement engine, against the arm's parent
(`AnalysisBundle.latency`). At the gate, on val, the baseline and
every candidate together (`CandidateValTable.latency`). Every comparison row,
under either objective, also carries a passive `latency` read off its own
eval runs (`method: eval_runs`) — information only, because the two sides may
have been driven hours apart.

**Quality must be equal or better** — one rule, `speed_quality_reasons` in
`analysis.py`, used on train and at the gate. Over every compared (eval,
score) row:

1. no row regressed significantly (it fell and the paired test or its CI
   says so, whatever the eval's priority);
2. the pooled net is at least zero. Each row becomes net items
   (`row_net_items`): pass/fail rows by paired flips, `fail_to_pass −
   pass_to_fail` (reversed for `lower_is_better`); other rows by the change
   of both sides' scores normalized to [0, 1] (`normalize`,
   `_SCORE_DOMAINS`) times the row's items. Each row's net is weighted by
   its eval's priority (`priority_weight`, from the eval's spec: p0 = 4,
   p1 = 3, p2 = 2, p3 = 1; an eval with no priority weighs 1), so a loss on
   a p0 eval needs four times the gain on a p3 eval to be balanced;
3. no row loses more than `row_loss_tolerance(n)` = max(1, round-half-up(
   `SPEED_ROW_LOSS_FRACTION` × n)) net items — 2 items at n = 16 and at
   n = 24. A zero tolerance is impossible: an equal candidate flips one or
   two items per eval by noise, and across many evals one would trip it
   almost every time. The pooled net has no tolerance, since noise cancels
   across rows and a real loss does not;
4. a row that cannot be pooled (no flips, no known score domain) blocks the
   candidate, and so does an empty set of rows.

The significance test alone is not enough. For example, on 20 val items a
real drop of 10 to 15 points on one eval is 2 or 3 net items, and a paired
test needs at least 6 changed items, all one way, to be significant.

**The rules, all code.** On train, `success_by_speed_rule`: clearly faster,
quality equal or better, coverage at the floor. At the gate,
`apply_speed_eligibility` marks every other candidate `ineligible` — the flag
the gate already refuses to promote, whoever decides — and
`decide_speed_gate` picks the fastest eligible candidate; candidates within
`SPEED_TIE_RATIO` of it are tied, and the higher pooled net (priority
weighted, `pooled_net`), then the lower p90 ratio, breaks the tie. Candidates with val holes stay pooled, as under the quality
objective. The planner and the analyst receive the exact rule as `speed_rule`
(`speed_rule_text`, composed from the same constants), the planner favours
speed levers, and the report appends a code-computed speed table.

## 17. The model policy

`--model-policy <path>` names a JSON file of allow, deny and exception rules
over (Kiln provider id, Kiln model id), as `fnmatch` globs;
[`examples/model-policy.json`](../examples/model-policy.json) is one.
[`model_policy.py`](../src/kiln_harness_tuner/model_policy.py) decides a pair in this
order: an **exception** (one exact pair, no wildcards) allows it; a **deny**
match refuses it; an **allow** match allows it; anything else is refused. No
policy (`--models any`) means every model is allowed.

**`--models` and the policy.** Without `--model-policy`, `--models` says
which models the task may use (`model_policy.resolve_models`): `keep`, the
default under `--objective quality`, or `any`, the default under `speed`
and `cost`. `any` is no policy. `keep` becomes a policy at start-up:
`orchestrator.pin_baseline_model` reads the baseline's (provider, model),
once the baseline record is known and before the model preflight, and
sets `Ctx.model_policy` to one exception for that pair
(`model_policy.keep_policy`). It writes the same policy to
`runs/<run_id>/model_policy.json`, so the gateway enforces it like a policy
file. A baseline whose pair cannot be read pins nothing; the planner still
gets the baseline's model alone (`_allowed_models`). The guided start's
picker writes a policy of exceptions to the state folder's `policies/`
(`onboarding.models`) and passes it as `--model-policy`.

So a model or provider swap is a lever exactly when the policy allows a
model other than the baseline's. The planner skill says so, and the
thinking level, the temperature and `top_p` stay levers under every
choice.

It is enforced at every place a run config comes into being or is used:

- **the gateway**, on `create_run_config` and `run_task`: the config's
  (provider, model) pair must pass; a non-`kiln_agent` config is refused.
  The gateway reads the policy from `runs/<run_id>/model_policy.json`, a
  copy no agent can write; an unreadable copy refuses every model;
- **the analyzer's `rc_guard`**, awaited before every eval job and every
  trial, so a run config that exists by any other route is never measured
  (`ModelPolicyRefused`, the item fails);
- **the gate**, which drops a refused candidate before the val pass;
- **code probes**, which never fork or drive a refused model.

The model preflight (section 3.1, step 9) checks the allowed models at
start-up. A model it flags is left out of `allowed_models`, is not
screened, and is refused by the `rc_guard` like a model outside the policy
for the rest of the run; the planner receives the reason in
`flagged_models`.

The baseline chain is exempt from the measurement checks: it is the
operator's starting point, and every promotion after it is checked. A
baseline outside the policy is named once at start-up
(`baseline_outside_policy`). Judges are not subject to the policy: an eval
config names its model under different keys, and evals are frozen. The
planner receives the policy and `allowed_models` — the server's
`GET /api/available_models`, filtered by it.

### 17.1 The model catalogue

The loop agents choose models and providers. The model catalogue
([`model_catalog/`](../src/kiln_harness_tuner/model_catalog/)) gives them
the data to choose with: price, context window, release date, Kiln's notes
for agents, and tags.

**The two outside hosts.** At the start of each run, after the policy copy
and before the rendered per-run configuration (section 3.1, step 2),
`fetch_catalog` gets two public files at the same time:

- Kiln's published model file,
  `https://remote-config.getkiln.ai/kiln_config_v2.json`: each model, its
  provider entries, the notes and the tags, and the provider list;
- models.dev, `https://models.dev/api.json`: the prices, the limits, the
  release date and other facts of each (provider, model) pair.

Each fetch is a plain GET with a timeout of 10 seconds and no retry. It
sends nothing about the run, the task or the user. A body over 50 MiB is
refused. `--no-model-catalog` turns both fetches off. The orchestrator
makes the two fetches. Agent sessions do not: they read the snapshot.

**The join** (`model_catalog/build.py`) is pure. A bad model or provider
entry is dropped alone. The price and the limits are matched on the exact
(provider, model) pair: the provider's models.dev id comes from Kiln's
provider list, else from a built-in port of the Kiln server's table
(`model_catalog/providers.py`). The model facts come from the first
matched entry, in the Kiln server's provider order. When no entry matches,
the release date has a looser match on the model's name, marked
`release_date_match: "loose"`.

**The snapshot.** `write_catalog` writes the result to
`runs/<run_id>/model_catalog.json`. It is in `RUN_DIR_ENTRIES`, so the read
fence denies it to agents like the other run files (section 12). The
snapshot does not change during the run, and every agent of the run sees
the same data. Each gateway process loads it at start
(`gateway/server.load_state`), as it loads `model_policy.json`. A missing
or bad snapshot loads as "no snapshot" and never stops the gateway. The
orchestrator keeps the same catalogue on the run's context
(`ctx.model_catalog`), and the old-model detector reads the baseline's
release date from it (section 18.4).

**Failures.** A fetch that fails never stops the run. The source is
recorded as `none` with a reason, and the wave log has one
`model_catalog_unavailable` event for each source that failed, then always
one `model_catalog` event with the state of each source and the counts.
Without models.dev, the catalogue has Kiln's data and null prices. Without
Kiln's file, there is no catalogue data, and the model operations serve
the Kiln server's model list only.

**The model operations** (`gateway/model_info.py`, pure) join the snapshot
with the server's `GET /api/available_models` at each call, and with the
model policy:

- `list_models`: one row per model, with its provider entries, filters,
  three sort orders (`featured`, `price`, `newest`) and pages;
- `get_model_details`: one model, with every provider entry, the notes
  and the capability flags;
- `list_providers`: each provider, its status, its count of `ready` pairs,
  its tags and notes.

Each (provider, model) pair has a status. `ready`: the server offers it
and the policy allows it. `blocked_by_policy`: the server offers it and the
policy refuses it. `not_offered`: the provider is connected, but not with
this model. `not_connected`: the server offers nothing on the provider. A
pair that only the server knows, such as a fine-tune, is listed with null
catalogue fields. For a pair that the server offers, the thinking levels,
the default level and the output mode are the server's (`SERVER_FIELDS`),
because the probes and the model preflight check against the server. The
status is information: `create_run_config` refuses a pair that the policy
refuses, as before. A pair that the server does not offer fails when it
runs, so the gateway skill tells agents to use `ready` pairs only. The
three operations go to the planner, the builder and the probe
(`ROLE_OPS`). Each answer has a `catalog` field that says which sources
it has.

## 18. The knowledge system and smell detection

The loop's planner chooses what to try. The knowledge system is the general
knowledge that it chooses from, and smell detection finds the issues in the
design of the baseline that the knowledge says how to fix. Neither one
changes how a result is measured or promoted.

### 18.1 The knowledge skill

The knowledge system is one skill,
[`kiln-harness-tuner-knowledge`](../src/kiln_harness_tuner/agent_files/skills/kiln-harness-tuner-knowledge/SKILL.md).
A loop agent can read only the installed skills folder, so the knowledge is
a skill, and a run installs it for every agent CLI with the others. It holds
general knowledge: field knowledge, public sources, synthetic experiments
and anonymised cases from other projects. Nothing measured on a run's
target, or on the moderation example, goes in it.

It has four component types. Each is one Markdown file with YAML front
matter, and its directory gives its type:

| Type | Directory | What it is |
|---|---|---|
| Lever | `references/levers/` | One thing in a harness that the tuner can change, and how: the gateway operations, the rules of the object, the limits. Never when or why. Its id is the short name that the planner writes in an experiment's `technique` field (`prompt`, `skill`, `tool_config`, `new_tool`, `params`, `model`). |
| Technique | `references/techniques/` | One optimisation: when to use it and when not, its alternatives, how, a cheap check that it took effect, its cost, what to do when it does not move an eval, priors and confidence. It names the levers it uses. |
| Playbook | `references/playbooks/` | How to solve one problem or reach one goal, as an ordered list of techniques, each with its condition. Its kind is `goal`, `order`, `failure`, `eval` or `design`. |
| Smell | `references/smells/` | An issue in the design of the agent, true whether an eval passes or fails: how to recognise it, how to confirm it, who can detect it, and at least one remedy. |

The file name without `.md` is the id. Front matter holds only what code
filters on or checks; links are ordinary Markdown links in the body. A
component can have sub-files in a folder beside it with the same name.

**Entry files.** Each reading role has its own first file at the top of
`references/`, and its prompt template names that file, so that no role
depends on a decision to open a skill: `planner.md` (every smell, and every
playbook by kind), `builder.md` (every active technique, and every lever),
`smells.md` (every smell, for the baseline reviewer), `analyzer.md` (the
playbooks for failure patterns) and `report.md`. `technique-index.md` lists
the techniques by name, for one case only: the user's guidance names a
technique. The tables in these files are **generated blocks** between
markers. Code writes them from the component files, and a person never
edits them.

### 18.2 The package

[`knowledge/`](../src/kiln_harness_tuner/knowledge/) loads, checks and
indexes the skill. It has no console script; it is run as a module:

```
uv run python -m kiln_harness_tuner.knowledge check
uv run python -m kiln_harness_tuner.knowledge build
```

`check` prints one line for each broken rule and exits 1 when there is
one (2 when there is no tree). `build` rewrites the generated blocks, then
checks. `--root` names another skill folder. `check` is pure: the caller
passes the gateway operations and the detector registry that the documents
are held to.

`schema.py` fixes the task shapes, the required sections of each type, the
size limits (in characters: a lever 6,000, a technique 12,000, a playbook
8,000, a smell 4,000, a sub-file 20,000) and the depth limit: every file is
reachable from an entry file within `MAX_DEPTH` (5) links. Other rules: each
link resolves inside the skill; each lever names gateway operations that
exist; each lever a technique names has a document; each playbook step links
a technique or a playbook; each smell has a remedy; each smell that names a
code detector names a registered one, and each registered detector names a
smell that exists; each generated block matches the files; no file is
outside the layout. The loader never raises for a bad document: each fault
is a problem with its rule id. At run time the loop uses the loader only to
check smell ids.

### 18.3 The maintenance skill and the backlog

A coding agent that changes the knowledge system reads
[`.agents/skills/knowledge-maintenance/`](../.agents/skills/knowledge-maintenance/SKILL.md),
and the root `AGENTS.md` points to it. Loop agents never read it: it is not
in the package. It holds the procedure for each kind of request, one
template for each component type, the placement rules, the content rules
with their reasons, the conflict rule (stop and ask the person: deprecate
the old guidance, or state when to use each), the approval rule for priors,
the commands that end each change, and the audit.

[`specs/backlog.md`](../specs/backlog.md) lists the known gaps and the
pending ideas in a fixed format. `test_backlog.py` holds the format. Loop
agents do not read it.

### 18.4 Smell detectors in code

[`smells/`](../src/kiln_harness_tuner/smells/). A detector is a small pure
function over `DetectorInputs` (the run config's properties, its prompt,
skill and code-tool texts, the definition of each attached tool, the
baseline's model with its release date from the model catalogue, and the
date the run started) that returns `Detection`s. One detector belongs to
one smell, and the smell document names it. A new detector is one file in
`smells/detectors/` and one
entry in `DETECTORS`. A detector that reads stored train trace stats sets
`reads_traces`; no detector does yet, so the detectors read only the run
config, its texts, its tool definitions and the catalogue snapshot
(`ctx.model_catalog`, section 17.1), and spend nothing.

| Detector | Smell | Fires when |
|---|---|---|
| `long_prompt` | `very-long-prompt` | the prompt is over `LONG_PROMPT_CHARS` (200,000 characters) |
| `long_tool_description` | `very-long-tool-description` | a tool's description is over `LONG_TOOL_DESCRIPTION_CHARS` (4,096) |
| `long_skill_description` | `very-long-skill-description` | a skill's description is over `LONG_SKILL_DESCRIPTION_CHARS` (1,024) |
| `old_model` | `old-model` | the baseline's model was released more than `OLD_MODEL_MONTHS` (8) calendar months before the run started; silent when the catalogue has no release date for it, for example a fine-tune |

The thresholds are in `smells/thresholds.py`, each with its source. No
measured optimum is known for any of them, so each is a default that can
change. A detection states a fact with its numbers, the reference it was
compared with and the source of that reference. It states no verdict.

`run_detectors` reads the run config once and runs each detector in its own
`try`. A detector that raises, or returns something that is not a
detection, is skipped. A detection whose smell is not a smell document is
dropped. It never raises, apart from `ProviderQuotaError`.

### 18.5 The baseline reviewer

`ensure_baseline_reviewed` (section 3.2, step 4) runs the detectors, then
dispatches the `baseline_review` role ([agents.md](agents.md) has its
input, its output and its gateway operations). The session reads the smell
documents, the baseline's harness and a fixed sample of its stored train
traces: up to `REVIEW_ITEMS_PER_EVAL` (4) items per active eval and
`REVIEW_MAX_ITEMS` (20) in all, in the order of the train set. It names the
smells it finds and says whether each detection holds. It reads train only,
writes nothing, and does not diagnose a failed eval item. It has no
`get_train_expected` and no `get_task_run`. Its trace-folder read rule also
lets it read the `_expected/` files that the `get_train_expected` calls of
other roles write there. Its skill forbids them; no settings rule denies
them.

Code checks each finding's smell id against the smell documents, merges
two findings of one smell, keeps at most `REVIEW_MAX_FINDINGS` (15), and
writes one `smell` issue for each (section 8). The review is recorded with
one of four statuses: `done`; `skipped` (a wrap-up wave, or memory already
holds this run config's smell issues); `failed` (the session gave no
result, or the step raised); `off` (`--no-design-review`).
The run goes on in every case. The planner reads the detections in its
input and the smell issues in memory, and the report's `## Design review` section
shows both (section 11).

The displays call the step the **design review**. Its one log line counts
the smells flagged by the findings and by the code detections, each smell
one time, and names them with spaces for the hyphens: "Design review
complete: 2 possible issues flagged (very long prompt, fixed call chain).",
or "Design review complete: nothing flagged.". A review that is not done
gives "Design review skipped: <reason>." or "Design review failed:
<reason>.". The `baseline_review` event carries the line and the
`flagged` smell ids.

A resumed run reviews a baseline again when its first review wrote no
issue (it found no smell, or every write failed), because the check for an
earlier review reads the smell issues in memory.
