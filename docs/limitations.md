# Limitations

What the loop does not do, what it has been seen to get wrong, and what its
guards cannot see. Read this before planning a long run.

Nothing here is a roadmap. Each entry names the mechanism and, where one
exists, the constant or the module that holds the behaviour, so a reader can
check it rather than take it on trust.

## Operational

**A crashed wave re-pays its agent sessions.** The only recovery from a
mid-wave fatal is a fresh run, whose janitor stamps the debris `— ABANDONED`.
Every in-flight item's plan, build and analysis sessions are paid again from
scratch. There is **session**-level resume — a build, eval-build or probe
session that hits its wall-clock cap is resumed once with a finish-up
instruction rather than re-run, which is what stops it duplicating every
artifact it had created — but there is no warm resume of a *run*.

**The one-run-per-task lock is local.** It is a kernel `flock` on a file under
the state folder's `runs/`
([install.md](install.md#where-the-tuner-keeps-its-files)), so it holds on one
machine and for one state folder. Two machines pointed at the same Kiln server
and the same task are not covered, and neither are two runs on one machine with
different `KILN_HARNESS_TUNER_HOME` values. The janitor's inference — that a
`— RUNNING` record at startup means a dead orchestrator — is only safe because
of that lock.

**`uvx` runs the tuner from `uv`'s cache.** With `uvx harness-tuner`, the
interpreter and the package are in `uv`'s cache, and every agent session
starts its MCP servers with that interpreter. A cache clean (`uv cache clean`)
during the run removes them, and the run's later sessions then fail. A
`uv tool install` has no such window.

**The budget and the clock are checked at wave boundaries, and only the budget
is purely that.** No wave *starts* past either limit, and a wave already running
is never killed for the **budget**. The **clock** is not the same at the
deadline itself: an item that is already analyzing is given
`ANALYSIS_GRACE_SECS` to finish, and every other in-flight item is cancelled at
once — its build and drives already paid for, no verdict stored. The wrap-up
window is the larger of a static reserve and the run's own **mean** wave time so
far, which is backward-looking: a wave much longer than the mean still runs into
the deadline and loses its unfinished items there. What the loop guarantees is
narrower than `--timeout-mins` sounds.

**Per-eval progress inside one eval job is not live.** The status model, the
tracker and both renderers handle an `eval_progress` count, and nothing emits
one: an eval job is driven by a single long poll that returns only at a
terminal state. What is live is coarser: a measured candidate shows its
chunks (`measure_chunk`, for example `chunk 3/9 · 24 judged`), and the
baseline measurement prints items done of total every 30 seconds.

**The loop never reads test.** That is deliberate — it is how the number you
ship on stays one the loop did not optimize against — but it means the loop
cannot tell you that number. You take it yourself, outside the loop.

## Measurement

**A code eval cannot judge prose, and a judge is sometimes wrong.** A code eval
reads the record of what the agent did: free, identical every time, and blind
to everything that lives in what the agent *said*. An LLM judge reads the
conversation, costs money on every item it has not already judged, and
produces some false failures. Read a judge's failures before quoting its rate.

**Small evals are underpowered, and the loop says so rather than hiding it.**
Code computes delta, n, p, an interval where the operation returns one, and
flip counts — and **no label**. At these sizes a vocabulary of win / loss /
insignificant would mostly emit its most biasing value. The analyst is asked
for a caution judgment instead, and the honest answer to a well-powered null is
often "enlarge the eval", not "the lever failed".

**The detectability check is a best case** (`detectability.py`). Its floor assumes that every change is a win and that
every other item stays the same. A real arm also breaks items, and the same
config scores some items differently from run to run, so a real win needs
more than the floor. A score that passes the check can still be too small
for the effect you expect.

**Its val side is a size and an estimate.** Setup reads no val result: the
gate is the only reader. So the
check knows the val split's size for certain, and estimates the val items
with room from the train share. Only the size can stop the run, so a val
split of 40 items that the baseline passes in full, with a train split it
fails often, passes the check and cannot show a win at the gate.

**The gate's interval route is not counted.** The one-candidate code rule
promotes on a significant val row, and also on a row whose interval lies
above zero. The server's Newcombe paired interval lies above zero with fewer
wins than the significance floor: 3 of 5 failures fixed on 10 items, 1 of 1
on a single item. Its lower end is built from the baseline's lower margin
and the candidate's upper margin, which are the narrow ones when the
candidate only gains; the textbook interval on the same tables still needs
3 to 5 wins. The check treats that route as no evidence, so it can stop a
run that the code rule could in fact promote on an interval. The report
lists the interval's own count (`interval`) beside the floor.

**The noise check is a sample.** `--noise-check N` compares N train items
per eval once. A change rate read on 10 items is a coarse number: 1 change
in 10 and 3 in 10 are not far apart at that size. The adjusted bar
(`noise_adjusted_wins`) is a sign-test approximation that spreads the noise
evenly both ways. Judge noise alone is measured only for a V2 judge config,
and only when every copied row names the baseline's own scored task run;
a legacy judge config and a code eval report no judge noise. The sample and
the clone are taken on the run's first baseline and are not measured again
after a promotion.

**Custom score types have no statistics operation and no normalization
domain.** Both sites raise in `analysis.py`. They are
different questions — a custom score with no declared range cannot be
normalized at all, while it might still be comparable — and neither is guessed.
The promotion gate is the one place that degrades gracefully: an uncomparable
score is listed as skipped on every candidate's table rather than failing the
gate.

**An eval with no spec is treated as active.** Status lives on the spec. The
resolution site is in `orchestrator.py`.

**"Active" has two definitions.** The measurement lane also requires a default
eval config, because an eval always runs its default and never one the loop
picked; setup's rule does not read that field at all. So an eval with no
default config is described by a paid agent session, seeded as an issue,
audited for splits and planned against — and contributes to no measurement.
Nothing reports the discrepancy.

**A data-shape failure is reported as a provider failure.** The `ErrorClass`
union has no literal for "this eval's shape is wrong" — a missing direction
flag, an unmappable score type, a missing default eval config — so those are
classified `provider_error`. Since the tripwire groups by class, a wave that is
mostly failing on eval *shape* aborts as though the provider broke, and two
such waves exit the run labelled `tripwire`. It is a mislabel, not lost
information: the detail rides the event and the report either way.

**A screening round is small, so its verdicts are about what is clearly
bad.** Round 1 judges 3 items per eval, taken hard-first from the
baseline's own results. A screen kills only a candidate a stop rule stops:
invalid output, clearly worse quality on those few items, clearly slower or
costlier on up to 30 timing pairs, or not clearly better once they are in.
It never proves a survivor good: a survivor's net change carries a wide
interval at 3 items per eval, so it still pays the full train measurement,
and the gate still reads val. The net is a stratified estimate, so the
hard-first order does not bias it; the interval says how little 3 items per
eval know.

**Under speed and cost a screen bounds a loss, and it cannot show there is
none.** There the screen judges the baseline's passes, most
fragile first, and a survivor reads "lost k of n baseline passes (no loss
rate above X% at 95%)". With 0 losses, 12 passes (round 1 on 4 evals) rule
out only a loss rate above 22%, and 24 passes (the minimum for a model or
provider swap) only above 12%. The loss test is against the noise flip
rate; without `--noise-check` that rate is a default (0.10), which can be
too high for a quiet task, so a real loss there is stopped less often. The
fragility order makes the bound conservative only when an item that other
configs fail is also one a worse candidate loses. A fix of a baseline
failure is reported apart and counts for nothing: on a noisy task, a
baseline failure often passes when the same config runs it again.

**A split of about 150 paired items cannot rule out a drop of a few
points.** That holds for a full train measurement and for the val gate. For
example, on 150 paired items, a candidate that fixes 7 items and breaks 14
is about 4.7 points worse, but a one-sided sign test on those 21 changed
items gives p of about 0.09: not significantly worse. The loss rule does
not apply at the val gate. Under speed and cost, read a promotion as "no
clear loss", never as "no loss".

**A small speed gain cannot be shown on 30 pairs.** The sign test needs 20
or more of 30 pairs faster (one-sided p < 0.05). When the per-pair latency
noise is large next to the gain, a real small speed-up often does not get
there. Then the `not_better` rule stops it, and the val gate, on 30 val
pairs, refuses it too. For example, if the log of each pair's latency ratio
has a standard deviation of 0.3 (an invented figure), a true 10% speed-up
makes about 64% of the pairs faster and reaches 20 of 30 only about 45% of
the time; a true 20% speed-up reaches it about 94% of the time. The engine
gives no verdict of any kind on fewer than 6 pairs.

**The quality test stops a clearly worse candidate, not a slightly worse
one.** At alpha 0.05 split over the geometric looks, a candidate with a
large true loss is usually stopped part way through the split. A candidate
with a small true loss is usually not stopped. It completes and meets the
train rule and the val gate, which is where a small loss is refused.

**"Lever not applied" reads the evidence a run records.** Thinking is judged
on two signals — reasoning shown, and output tokens against the parent on
the same items — and a candidate is stopped only when both are available
and neither moved. A provider that shows neither leaves the lever
UNVERIFIED (logged, not stopped), so an ignored thinking parameter on such
a provider is paid for. Output tokens also move for other reasons (a new
level changes the answer's length), so a real no-op whose answers happen to
lengthen by a quarter passes. A model switch no drive records is
unverified the same way.

**The integrity checks are heuristics.** Quoting is blocked only as a run
of 8 identical words, or an eval's full name. The gate checks the run
config's prompt; the build-time judge-copy check also reads
the text of its skills and code tools, so a verbatim quote there ends the
arm before measurement. A run of 7 words, or a quote with one word changed,
passes both. Text the baseline already carries is exempt from both checks;
the baseline audit reports it once as a scrub issue, and it stays until an
arm scrubs it. A paraphrase is left to one read-only reviewer session per
changed prompt, whose findings are flags, not blocks, and which
reads only the prompt: a paraphrase placed in a skill or a tool description
is not reviewed. The shape checks read length and top-level JSON keys
only. A "write action" is a tool call whose method is POST, PUT, PATCH or
DELETE or whose action or name contains a write verb; a tool that writes
under another name is not seen. The flags go to the promotion agent, whose
answer is judged only for having an `Integrity:` line.

## Containment

**A scrub is not a score win.** The baseline audit turns train-specific
text the baseline already carries into a `baseline_audit` issue that asks
for a scrub. The promotion gate promotes a score gain on val, and a scrub
can hold the score or lower it: where the copied data helped, it was
helping. A scrub arm on its own may then never be promoted. The issue tells
the planner to put the scrub into an arm that rewrites the same text for a
real fix; failing that, the operator applies the scrub to the baseline by
hand.

**Agents read train labels.** Four roles can read a train
item's expected output (`get_train_expected`). Train labels are train data:
what keeps a run honest is that val and test stay out of reach, and they
do. The cost is a higher pull toward memorising: a build can copy an
expected value as well as an input. Three filters stand against it, in
order: the exact-match pre-check (code, every train item, verbatim copies
only), the generality review (a model, a sample of 12 items, paraphrase
too), and the val gate. A paraphrased copy of an item outside the review's
sample passes the first two; the val gate is then what stops it gaining
anything.

**Eval-freeze is structural.** The loop gateway gives
each role only its typed operations, and only the eval builder has
operations that create an eval, a judge config or items. The gateway
accepts those writes only on an eval that it created in the same run; the
evals the loop measures with get no new items, no new judge config from an
agent and no change of their current config. Code makes one exception, and
only under `--noise-check`: a copy of a V2 judge config, never the default,
run once on N train items and then moved out of the project to the state
folder's `runs/.quarantine/…/archived_eval_configs/`. A run that dies between
the two steps leaves the copy, named `NOISE …`, in the eval. The eval
builder can still run a train eval job on an existing eval
(`run_train_eval`), which adds results and changes no definition.

**The gateway is loop code between the agents and Kiln.** It answers from a
train set computed from the evals' own splits and its own record of what
the run created; a Kiln response shape it does not know (a new wrapper
around results, say) yields empty answers rather than leaks, but also empty
answers — an agent sees fewer train records than exist. Its operation set
is fixed; a legitimate need outside it has no operation until one is
added. Eval inputs are read from the project
on disk (the API documents no eval-input route), so `run_task` and
`list_train_items` cover eval-input items only, not a new eval's dataset
items.

**Drives through eval jobs.** On a multi-turn task, a run
config with a world tool, or an item with a `world_reset`, every drive is a
one-item eval job ([architecture.md](architecture.md) section 16). These
limits follow.

- **An older Kiln server times less fairly.** Without judge-free eval jobs
  the loop uses judged jobs. A judged job reuses a stored trace and skips a
  scored item, so a timing pair can come from two windows. It also pays
  for the judge. One log line says so; the timing is not refused.
- **A legacy default judge has the same effect.** The server drives
  without judging only through a V2 eval config, so an eval whose default
  config is a legacy judge gets judged jobs.
- **Each drive is a whole job.** A one-item job has the job's own set-up
  cost: the server loads the eval and the run config, and reads the
  world's tool list again. On many short items this is slower than the run
  endpoint.
- **The gateway's cap is per process.** Each gateway process caps its own
  eval jobs at `--max-concurrent-evals`. Several agent sessions at once can
  together run more jobs than that number.
- **A drive through a job carries no caller tags.** The task run is the
  job's trace, so `run_task`'s `tags` do not reach it.

**Code that an agent writes runs on the Kiln server.** The builder, the
probe and the eval builder have `test_code_tool` and `create_code_tool`,
and `run_task` runs a run config with the tools the builder wired into it.
That code runs in the Kiln server's code-tool runtime, outside the agent
sandbox, and its output comes back to the agent. The gateway does not read
the code. If that runtime can read the project directory or reach the Kiln
API, an agent can get held-out records through its own tool. The loop does
not test what the runtime can reach; that is a Kiln server property.

**The eval builder's splits.** `create_eval` sets the new eval's test set
(`eval_set_filter_id`) and train set (`train_set_filter_id`) to tags the
gateway chooses; the documented API offers no way to set a val split, so an
eval built this way has no val split for the promotion gate. Its items are
dataset task runs (`add_eval_items`), not eval inputs. Its val and test
items are never readable back, but the eval builder writes their CONTENT
in the first place — it chooses them, and nothing stops it from writing a
train item that repeats a val item's text.

**The split discipline is enforced, with named holes**
([architecture.md](architecture.md) sections 6 and 12). What is not closed:

- **The `val`-tagged memory record, by id.** A task-scope listing must name
  a tag and never `val`, which keeps the record's id and its
  content out of every listing an agent can make. A `by_ids` read of its id
  is still allowed: the id reaches an agent only through a channel the
  fences close (a listing, a file, the report).
- **Held-out items with no split-like tag.** The split check decides "held
  out" from the evals' split filters and from any
  `val`/`test`/`val_*`/`test_*` tag even when no eval uses it. An item held
  out under a tag with another name that no current eval uses (`holdout`,
  `eval_set`) is not recognised. The fences still keep agents off it: the
  project files are denied and the task-run listing is blocked.
- **A task run fetched by its id — the ones the split check did not see.**
  `…/runs/{id}` is refused for every held-out item id the split check found.
  A drive on a held-out input that the split check could not
  place (made after start-up by code, or with an input text that differs)
  is not in that list; its id reaches an agent only by a channel the fences
  close.
- **Evals created through a spec — between creation and the next check.**
  The hook checks a new eval's train split when the request body names it.
  An eval whose splits the server derives from another record is checked by
  code instead, from disk, the first time the loop lists the active evals:
  a train split that holds a held-out item, or that the
  split check cannot evaluate, keeps the eval out of every measurement. An
  agent that created such an eval could still read its definition in the
  same session; the definition names filters, not items.
- **An entry created in the run directory while a session runs.** The read
  deny over the state folder's `runs/<run_id>/` names its entries one by one.
  The dispatcher re-reads the run directory before every session and denies
  every entry but `scratch/`, listed in `RUN_DIR_ENTRIES` or not
  (`dispatch/run_fence.py`). A session that is already running keeps the
  rules it started with, so a file created during it is readable to its
  `Read` tool (not to Bash, whose sandbox denies the whole tree).
- **What the probe agent drives.** The probe gets train items in its input,
  and the API refuses any split but train, but `POST …/run` takes any input
  text. An agent that already knew a held-out input could drive it; the
  fences are what keep it from knowing one.

**The Claude read fence is a deny list.** It denies by resolved path: the run's
own Kiln project whole, the state folder's `runs/` (except this run's scratch),
`traces/` and `reports/`, and other Claude sessions' transcripts. A path nobody
names is readable to a Claude loop agent. That includes:

- **other Kiln projects** on the machine;
- **`~/.kiln_ai/settings.yaml`**, where Kiln keeps the provider API keys;
- **any clone of this repository.** Its `examples/` is the answer key of the
  moderation example: `posts.jsonl` holds the label and the split of every
  post, and the scorer and the README state the house rules. A Claude agent
  in a run on the example can read them. A clone used before the state folder
  existed can also hold old `runs/`, `traces/` and `reports/`, with the val
  tables of the tasks it ran on: delete them or move them out
  ([install.md](install.md#where-the-tuner-keeps-its-files)).

The Codex and OpenCode fences are allow lists, so they do not have these gaps
([agent-cli.md](agent-cli.md) sections 10 and 11).

**The pre-tool hook checks tool names only.** It admits the role's gateway
operations and refuses every other MCP tool; what a gateway operation may do
is the gateway's code, not the hook's.

**The model policy covers the subject's run configs, not the judges.** Eval
configs keep whatever model they have. And the baseline chain is measured
whatever its model; only arms are refused.

**Write containment has one sanctioned hole, and two roles hold it.** File
writes are denied outside the run's scratch tree, and the skill-installer MCP
server is the single exception: trusted code doing atomic installs into the
project's skills directory. It is granted to the builder and to the probe, both
of which stage skills — so two roles, not one, can change the subject task's
skill surface.

## Testing and reference

**No test covers a live run.** The rendered settings, the rendered MCP
configuration and the read fence are built from paths, and the suite covers
their construction, not their effect. They are proven only by running the loop
for real.

**The Kiln API reference can go stale silently.** Loop agents do not use
it: they reach Kiln through the gateway's typed operations, and the contract
pre-flight checks the response shapes the loop reads. The `kiln-api` skill
serves interactive sessions. It records the spec version and hash it was
generated from, which tells you *afterwards* which document it was cut from.
Nothing notices when the server moves past it.

## Not built

- **Other agent CLIs.** Three dispatchers ship: `claude -p`, `codex exec`
  and `opencode run` (`--agent-cli`). Another agent CLI needs its own
  dispatcher, and it must fence its sessions: [agent-cli.md](agent-cli.md) sections 6, 10
  and 11. The OpenCode fence is a tool-level allow list, not an OS sandbox.
- **No credential management.** The loop uses each CLI's own sign-in. It does
  not take an API key, and it withholds secret-looking environment variables
  from every session unless `KILN_HARNESS_TUNER_CHILD_ENV_KEEP` names them.
- **No outer loop.** Pulling production traces in, mining them for issues and
  triggering a run is a practice, not code here.
- **No curator pass over the issue store.** Duplicates are handled at
  ingestion and by the planner's repeat-deferral counts, not by a periodic
  clean-up.
- **No pre-start survey.** Code decides the baseline mechanically. Two
  questions a person should be asked are not asked: which run config is the
  baseline when a task has several and no default, and whether a newer default
  should replace an existing baseline. The loop never changes the baseline on
  its own, so the second one has no other path.
- **No flake re-runs.** The same run config on the same input yields the same
  result, so a measured item is never re-measured to second-guess its score.
  Sampling noise is handled by the paired statistics *across* items. Errored
  items are different: they store no row, so the retry re-runs exactly them.
  The one exception is opt-in and measures noise only: `--noise-check N`
  re-drives and re-judges N train items per eval before wave 1, on a clone
  and a copy, and no comparison ever reads those rows.

## Costs

This repository quotes no measured cost figure. [costs.md](costs.md) is the
method for estimating a run before starting it.
[`experiment-costing`](../src/kiln_harness_tuner/agent_files/skills/experiment-costing/SKILL.md) is the
skill behind it. It covers the six spend lanes and the formulas, and its first rule is the one that
matters most here — never scale a cost across models by a list-price ratio.

Two shapes of the bill are worth knowing in advance anyway, without numbers.
The agent side and the provider side are metered separately and reported as
separate lanes, and they are not the same order of magnitude. And a reasoning
model bills its reasoning as output, which is why a model that looks cheap per
list price can be the most expensive lane in a comparison.
