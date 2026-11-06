# Pointing the loop at your own task

What a Kiln task needs before `kiln-harness-tuner` can work on it, what the loop's setup
phase prepares for you, what it refuses to guess, and every flag.

This document assumes nothing but a Kiln studio server with your project in it.
Read [concepts.md](concepts.md) first if the words *wave*, *split*, *arm* and
*baseline* are not yet familiar.

## 1. What the loop needs from your task

Five things. Each one, when it is missing, produces a specific failure — and
each failure is designed to arrive **before** any paid work, in pre-flight.

### 1.1 At least one run config

A run config is a complete, runnable configuration of the task: a model, a
prompt, tools and skills. It is what experiments branch from.

With none, the run stops:

> task … has no run configs, so there is nothing to optimize from. Create a run
> config for this task (a model + prompt the task can actually run), then
> re-invoke `kiln-harness-tuner`. The loop measures every experiment against a baseline
> run config and will not invent one.

This is the **one state code cannot decide**. No starter run config is minted
and no placeholder baseline exists anywhere in the system, which is why every
later stage can assume a real parent.

### 1.2 At least one active eval

An eval is the loop's only source of signal. Without one there is nothing to
measure, nothing to plan against, and nothing to promote on.

An eval is **active** when it has no spec, or its spec's status says active.
Specs marked future, deprecated or archived are skipped — so an eval you do not
want the loop spending on is deactivated through its spec, not deleted.

**Set a default eval config on every eval you want measured.** Each eval always
runs its default config, never one the loop chose, which is how a promotion
comparison is kept from mixing judges — and an eval with no default is skipped
by the measurement lane entirely. Setup's own active-set rule does not check
for one, so an eval without a default is still seeded as an issue and still
audited for splits, and then silently contributes nothing to any measurement.
That gap is named in [limitations.md](limitations.md).

### 1.3 Train and val splits on every active eval

Every active eval must carry a train filter **and** a val filter. The loop
measures experiments on train and gates promotion on val, and it will not run
an eval that cannot be split that way.

Missing splits fail pre-flight, listing every offending eval at once:

> N evals are missing a train/val split and cannot be used by the loop:
>   • … "…" — no val split
>   • … "…" — no train split
>
> The loop measures experiments on train and gates promotion on val, and it
> does not create or repair splits. Add splits to these evals (or deactivate
> them via their Spec status), then re-invoke `kiln-harness-tuner`.

One pass, one list, one fix. The audit exists because without it a task like
this dies at its first promotion gate instead — after the waves before it have
been planned, built and measured, and paid for.

**Setup audits; setup never repairs.** Splitting an eval is a decision about
what the eval measures, and it belongs to the eval's owner.

### 1.4 Enough eval inputs in each split

There is no minimum the loop enforces, because the right size depends on the
effect you expect. What the loop does instead is tell the planner the truth:
`eval_sizes` carries, per eval, the number of train rows a comparison can
actually *pair* — stored results, not the dataset's nominal size — so the
planner weighs detectability before it predicts an effect.

Two consequences worth knowing before you size a split:

- **Train has to power a paired verdict.** Too few rows and every arm comes
  back a well-powered-looking null, and the analyst is instructed to answer
  that with "enlarge the eval" rather than "the lever failed".
- **Val has to power the promotion gate**, which is a stricter bar: a candidate
  whose val measurement is less complete than the coverage floor cannot win the
  wave at all.

**A task needs enough failures, not only enough items.** A paired test can
call a win significant only when enough items changed, all the same way. At
p < 0.05 that is 6 items for a pass/fail score (exact McNemar) and 6 for a
five-star score (Wilcoxon). So a score shows a significant win only when at
least 6 of its items have room to improve — baseline failures, or ratings
below the best — and the smallest change it can show is 6 / n of the items.
On 30 items that is 20%; on 60 it is 10%. Those are best cases: with any
noise, a real win needs more.

Setup checks this before wave 1, for free (the detectability check,
[architecture.md](architecture.md) section 5.1). It prints one line per
score, and it stops the run when no score that could drive a promotion can
show a significant win on val — which, as a certain fact, means every such
eval's val split holds fewer than 6 items. An eval at 100% on train is not a
reason to stop: a guard eval exists to catch regressions. It gets a warning.
Because setup reads no val result, the val "items with room" is an estimate
from the train share, and an estimate only warns.

To size a split for an effect you expect, pass `--min-effect`: the check
then says per eval whether that change can show at all, and stops the run
when no eval can show it on val. To measure the noise the best case leaves
out, pass `--noise-check N` (paid; section 5.2 of the architecture).

Test is never read by the loop. Size it for yourself.

### 1.5 The project on disk

`--project-path` is the on-disk Kiln project directory. The orchestrator's
code reads it: the split membership of every item, the train and val inputs
it drives itself, and the stored results on held-out items, which it only counts.
No agent reads it — the tree holds the eval inputs and results of every
split, so the read fence denies it whole. Agents reach the project through
the loop gateway, which answers from the train items only.

**Stored val and test results stay in the project.** The loop counts them at
start-up and moves nothing. The gateway serves agents train items only, and
the read fence denies them the project's files. A split filter the split
check cannot evaluate (anything but `all` and `tag::<name>`)
stops the run at startup with the fix, and so does a `--project-path` that
does not name a readable directory.

### 1.6 A multi-turn task, or a task in a world

The loop drives items itself: the baseline timing, the timing chunks of a
measurement, the probes, and an agent's `run_task`. For a single-turn task
these drives use `POST …/tasks/{t}/run`. That endpoint runs one input once.
It starts no world episode and no simulated user.

So the loop drives an item through a one-item eval job when one of these is
true:

- the task's `turn_mode` is `multiturn`;
- a run config of the drive lists a world tool (`kiln_tool::world::…`);
- the item's input has a `world_reset`.

The eval job starts the episode and runs the synthetic user, as a
measurement does. The loop then reads the drive's cost and latency from the
task run that the job saved. You do not set anything.

Use a Kiln server whose eval jobs take `judge: false`. Then a drive runs no
judge, and it is always a new drive. An older server works, with one log
line at the first drive: the loop then uses judged jobs. They pay for the
judge. They also reuse a stored trace when one exists, so two timings can
come from different hours. [architecture.md](architecture.md) section 16
has the detail.

Multi-turn items need a synthetic-user drive config, as an eval needs it.
An item that the eval job cannot drive fails its drive and says why.

## 2. What setup does for you

Setup runs inside pre-flight on **every** invocation, not only cold ones —
keeping the issue store in step with your evals is the recurring half of its
job. On a warm task with nothing new it is a handful of reads.

### 2.1 It seeds the baseline record

| Your task | What setup does |
|---|---|
| already has a `baseline` memory record | **leaves it exactly as it is.** A newer default run config does not move the loop's baseline |
| has a default run config, no record | seeds the record pointing at it |
| has run configs but no default, no record | picks the most recently created one, and writes the ambiguity into the record's own prose so a reader can see the choice was mechanical |
| has zero run configs | stops, per section 1.1 |

The record carries no metrics. Its `rc_<id>` tag is the pointer, and val
numbers live in a separate record that the planner may not read.

### 2.2 It turns your evals into the planner's fuel

The planner does not plan against evals. It plans against **issues** — prose
statements of what the task gets wrong — and setup derives one per active eval.

The binding is a tag: an issue carries `eval_<eval_id>`, and that makes the
relationship a single API call in both directions. **The eval is the parent and
the issue is its derived child**, so there is one source of truth for the
requirement and one derived statement of it, rather than two records drifting
apart.

Each new or drifted eval gets one small agent session that reads the eval, its
current config and its spec, and writes a description a planner can act on
without re-reading the rubric. It is a one-time cost per eval.

A seeded issue starts with the `UNMEASURED:` prefix. Setup does **not** verify
that the baseline actually fails the eval — the planner decides when that is
worth spending on, and it settles it with a `measure_issue_baseline` item,
which is pure code: one eval run against the baseline on train, one memory
write. Fails the eval → the prefix is stripped and the issue is normal fuel.
Passes → `WONT_FIX:`, with the reason.

Seeding **every** active eval is deliberate, including on a wide task. A first
run on a task with many evals seeds many `UNMEASURED:` issues, and that is the
point: the planner now has the full requirement set and can triage it. Triage
is a planning judgment, not a seeding filter.

### 2.3 It keeps them in step, without destroying anything

Sync is additive. It creates memories, adds tags and appends lines. It never
deletes a memory, never rewrites agent-authored prose and never closes an
issue.

An issue's content has three writers — setup, the loop's code, and agents
recording findings — so the field is split by **one delimiter line**.
Everything above it is setup-owned and replaced wholesale on a refresh.
Everything from the delimiter down is loop-owned, append-only, and preserved
byte for byte. An issue record with no delimiter, including one you wrote
yourself, is treated as entirely loop-owned: setup adds its head above and
touches nothing else.

Drift has three shapes:

| What changed | What setup does |
|---|---|
| the rubric, the config or the priority | re-describes the eval, replaces the setup-owned head, appends one note below the delimiter |
| the eval was deleted | prefixes the issue `UNMEASURABLE:` and appends the cause. **Never deletes, never closes** — the finding may still be true |
| the spec was archived, so the eval left the active set | the same marker, a different cause line |

## 3. What setup refuses to guess

Three refusals, and they are one rule: setup seeds and audits, and it never
repairs a thing whose owner is a person.

1. **Zero run configs.** No starter config is minted.
2. **An ambiguous issue binding.** An issue with no eval tag is repaired only
   when its content names exactly one existing eval. Zero, several or a
   dangling reference → left untouched and listed as needing attention.
3. **A missing split.** Audited, never created.

Each is reported rather than worked around, because the alternative is a run
that produces numbers against a shape nobody chose.

A fourth stop comes after the baseline's train measurement, and it is a
question of size, not of shape: **no eval can show a significant val win**
(section 1.4). It lists every offending score at once, and
`--allow-underpowered` runs anyway.

## 4. Writing the guidance

`--guidance` is free text. It reaches **every planner, every wave, verbatim**,
and the promotion gate. It is the one place a person steers the run without
touching the task.

Three things it is good for:

- **Scope.** "Only work on the two issues about writes; leave the answer
  wording alone." The planner reads this as a hard constraint on candidate
  generation.
- **Prohibition.** "Do not change the model, and do not raise the reasoning
  level — this has to run at the current cost." A lever the guidance forbids is
  not tried.
- **Answering an open question.** The planner records questions it cannot
  resolve as constraint records with a re-ask condition. The promotion gate
  escalates on an *open* constraint instead of judging the numbers — but a
  constraint your guidance answers is answered, and the gate is told so
  explicitly.

> *An illustration, on an invented task.* A freight depot operations desk
> assistant has two problems: it reports actions as done when the API refused
> them, and it answers count questions from the first page of results.
> Guidance: *"Focus on the two reporting issues. The tool surface is fair game
> — wrappers and new tools are fine. Do not edit the system prompt's persona
> section, and do not change the model."*

What it is **not** for: instructions to a subject model. The guidance steers
the loop, not the task.

## 5. The first run

```
kiln-harness-tuner --project-path "/path/to/Your Project" \
          --project-id <id> --task-id <id> \
          --guidance "…" --timeout-mins 60
```

The loop shows its progress in the terminal, runs waves until the budget or the
planner ends it, and writes a report.

**Start it bounded, with headroom for a whole wave.** No wave ever *starts* past
`--timeout-mins` or `--budget-usd`, but the two do not end a run the same way:
at the budget ceiling the wave in flight runs to completion, while at the
deadline every in-flight item except one that is already analyzing is cancelled,
its drives paid for and no verdict stored. A first run against an unfamiliar
task is the wrong place to discover how long a wave takes.

**Keep the reconnaissance battery on a first run** — that is, do **not** pass
`--no-recon`. It is a handful of short drives on your own baseline, and it is
what stops the first planner assuming facts about your model. It is not free
either: it drives real items on the model you are optimizing, so on an expensive
model it is not trivial. Price it with [costs.md](costs.md) and the
[`experiment-costing`](../.agents/skills/experiment-costing/SKILL.md) skill
behind it, rather than against a figure from somebody else's task.

**Stopping a run.** In the foreground, Ctrl-C. A run started in the
background (`kiln-harness-tuner … &`, `nohup`) does not receive the terminal's
Ctrl-C, and a shell without job control — a script, an agent's shell —
starts it with SIGINT ignored, so `kill -INT` does nothing either. Send
SIGTERM instead: `kill <pid>`. The loop also
stops cleanly on SIGHUP, which a closing terminal sends. On either signal it
cancels the run the way Ctrl-C does: every `claude -p` child is killed, the
server's eval jobs are cancelled, the run lock is released and the report is
written; the exit code is 143 for SIGTERM and 129 for SIGHUP. `kill -9`
skips all of that and leaves the children running to their own time caps.

### Every flag

| Flag | What it does |
|---|---|
| `--project-path` | **Required.** The on-disk Kiln project directory. The orchestrator's code reads it; no agent can — the read fence denies the whole tree, because it holds the eval inputs and results of every split. |
| `--project-id` | **Required.** The Kiln project id. |
| `--task-id` | **Required.** The Kiln task id. The loop optimizes exactly one task and never touches another. |
| `--kiln-host` | The running Kiln studio server. Injected into every agent's MCP configuration, so agents and orchestrator always agree on the host. |
| `--guidance` | Section 4. Passed verbatim to every planner and to the promotion gate. |
| `--timeout-mins` | The time budget, default 180. A monotonic deadline: no wave ever *starts* past it. At the deadline itself an item already *analyzing* gets `ANALYSIS_GRACE_SECS` to finish, and every other in-flight item is cancelled at once — so a deadline that lands mid-wave loses that wave's unfinished items after their drives are paid for. |
| `--budget-usd` | A cost ceiling for the run, both spend lanes. At the ceiling the run exits; within one projected wave of it, the next wave is the wrap-up. Default: no ceiling. |
| `--coverage-floor` | The promotion gate's val coverage floor, default 0.95. A candidate whose fills sit below it cannot win this wave, whatever the gate decides. |
| `--max-concurrent-evals` | Eval jobs in flight at once, run-wide. Default 4; `0` is unlimited. This is the rate-limit knob — the loop's own parallelism is what manufactures the provider limit that then stalls it. |
| `--agent-cli` | The agent CLI every loop session runs, as the whole command you type, not just its name. Default `claude -p`. Also `codex exec` and `opencode run`, each with its own options, for example `'codex exec -m gpt-6.1-sol'` or `'opencode run -m zai-coding-plan/glm-5.3-flash --variant high'`. Its model and effort are the run-wide defaults under `--agent-model` and `--agent-effort`. The CLI must already be signed in: one short test prompt goes through it before the run starts, and a CLI that does not answer stops the start with what it printed. [agent-cli.md](agent-cli.md) section 9. |
| `--max-concurrent-agents` | Agent sessions in flight at once. Default 4; `0` is unlimited. Also told to the planner, so it sizes a wave to what can actually run. |
| `--agent-model` | `ROLE=MODEL`, repeatable. Per-role model override. Under `claude -p` the default is `opus` for every role except `generality_review`, which is `sonnet`. An unknown role fails at startup; the help text lists them. |
| `--agent-effort` | `ROLE=EFFORT`, repeatable. Per-role reasoning-effort override, same validation. |
| `--no-baseline-audit` | Skip the audit of the baseline's own text. By default, once per baseline, setup searches the baseline's prompt, skills and code tools for verbatim copies of train items (free), then runs one review session when the search finds none. A leak becomes one `baseline_audit` issue that asks for a scrub. The audit never stops the run. [architecture.md](architecture.md) section 3.1. |
| `--reference-check` | `warn` (default), `refuse` or `off`. After each build, code looks for names that point at nothing: a skill that the prompt or an attached skill names and the run config does not attach (only names of skills in the project or on the parent count); a tool that the parent attaches, that the build dropped, and that the prompt or an attached skill still names. A dangling skill reference that the parent already has is not reported. Free. `warn` writes the findings on the item outcome, on a `reference check:` line of the experiment record and in the analysis input, and measures the build. `refuse` gives the build one rebuild with the findings, like a generality leak, and a second hit ends the item `dead_end`. `off` skips the check. [architecture.md](architecture.md) section 4. |
| `--no-recon` | Skip the setup reconnaissance battery — the code probes that establish, on your baseline, whether the model calls an attached tool and reads an attached skill (only when one is attached), behaves under the alternate output mode, reasons more at a higher level (under `speed`: holds its output one level down and with thinking off, and how much faster), emits valid JSON, and produces output on the longest inputs. Levels come from the provider's own list. |
| `--gate-agent` | `auto` (default) or `always`. `auto` decides a one-candidate promotion gate by a code rule and dispatches the agent only to rank two or more. `always` dispatches it for every gate. |
| `--max-turns` | An opt-in hard turn cap per agent session. Default uncapped: the per-role wall-clock timeouts bound a runaway, and a cap near the observed maximum truncates a large healthy session near the end and then re-pays it. |
| `--no-ui` | Plain timestamped log lines instead of the live display. Use it for a non-interactive terminal or a CI log. |
| `--objective` | `quality` (default), `speed` or `cost`. `speed` optimizes per-request wall-clock latency: a candidate is promoted only when a paired drive pass shows it clearly faster on val — at the median and without a significant p90 regression — and its val quality is equal or better: nothing down significantly, net items over every eval at least zero, and no eval down more than max(1, round(0.10 × its items)) items. `cost` is the same rule on per-request provider cost. [architecture.md](architecture.md) section 16 has the method. |
| `--min-effect` | The smallest improvement you would act on, as a share of an eval's items (`0.1` is 10%). The free detectability check then says per eval whether the best case — the significance floor of 6 items over the split — can show that change on train and on val, and stops the run when no eval that can drive a promotion can show it on val. Default: none. Section 1.4. |
| `--allow-underpowered` | Run even when the detectability check stops the run because no eval can show a significant val win. The refusal still prints, and it lands in the report's Detectability appendix. The promotion agent then weighs nominal val wins. |
| `--noise-check` | `N`: before wave 1, re-drive the baseline on N train items per eval (a clone of the baseline run config, so Kiln's item cache does not answer) and re-judge the stored outputs (a copy of each V2 judge config, never the default), and report how often each score changed. Paid: an estimate prints first, it books to the budget, and it is skipped when the estimate does not fit `--budget-usd`. Default off. [architecture.md](architecture.md) section 5.2; [costs.md](costs.md). |
| `--model-policy` | A JSON file of allowed and denied (provider, model) pairs for the task's run configs. Enforced on every run config an agent creates or drives, and before any run config is measured or promoted. Default: no policy, every model allowed. [`examples/model-policy.json`](../examples/model-policy.json) shows the shape; [architecture.md](architecture.md) section 17 has the rules. |
| `--skip-model-preflight` | Skip the model preflight. By default, before the baseline measurement, the loop makes one short test call on each model the run may use: the baseline's own model, and the allowed models the planner can pick (at most 12). It also compares the baseline's prompt and tool definitions with each model's context window, when Kiln's model list gives one. A timeout, connection failure, 429 or 5xx gets one retry first. A hard failure — bad request, not found, no deployment, context too long, quota or auth — flags the model in the log and the report, and the model is not screened or measured this run; on the baseline's own model it stops the start. Any other failure leaves the model untested, with a warning, and excludes nothing. Each call costs one short prompt and one short answer, and is booked to the budget. [architecture.md](architecture.md) section 3.1. |

When latency is the goal, use `--objective speed`; when cost is the goal,
use `--objective cost`. Use `--model-policy` to keep costly models out.
Under `--objective quality`, the latency and the cost per request of each
arm are also reported, read from its stored eval runs.

## 6. Reading the result

| Where | What |
|---|---|
| `reports/kiln-harness-tuner-<run_id>.md` | the round report: what was planned, what was measured, what was promoted, and code-computed appendices: the spend, why each arm stopped and how many of its items were judged, and the trade-offs of the screened candidates (quality lost against cost and latency saved) |
| `runs/<run_id>/` | the wave log (append-only), the rendered per-run settings and MCP configuration, and each agent's scratch directory |
| `runs/.quarantine/<project>-<task>/archived_run_configs/` | the loop's own `PROBE …` run configs, archived out of the project after a run |
| `traces/` | every agent session's transcript, credential shapes redacted — the only record of *how* an agent reached its answer |
| Kiln memory | the durable result: the baseline record and its promotion chain, every issue's state, every experiment's verdict |

`runs/` and `traces/` are git-ignored: they are your local output.

**The report is not the whole result.** The memory store is. A second run on
the same task re-orients from it, which is why a crashed run costs spend but
not knowledge.

**The loop never reads test.** Whatever number you ship on is one the loop
never optimized against, and you take it yourself.

## Next

- [limitations.md](limitations.md) — read this before you plan a long run.
- [agents.md](agents.md) — which agent to blame when a run goes wrong.
- [architecture.md](architecture.md) — how it is all wired.
