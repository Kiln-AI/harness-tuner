---
status: complete
---

# Functional Spec: New TUI

## 1. Purpose and scope

The new TUI is the terminal interface of `harness-tuner` when a person runs it in a terminal. It is one Textual app. It covers the start-up questions, the live run and the end of the run.

Today two libraries do this work: rich and questionary for the start-up (`RichTerminal`), and `rich.live` for the run (`LiveUi`). The new app replaces both for interactive terminals. It keeps every flag, every start-up question, and the plain output for scripts and CI.

The person watching wants answers to five questions, and the run screen has one section for each:

| Question | Section |
|---|---|
| What is this run, and what is it trying to do? | Title bar, Goal |
| Will it finish inside its limits, and where is it in the loop? | Time and spend, Run timeline |
| Is the agent getting better? | Results |
| What is it doing now? | Experiments |
| What just happened, good or bad? | Log |

### In scope

- One Textual app from launch to exit: the start-up flow, the run screen, the Done state.
- The start-up flow re-skinned in Textual, with the same steps, strings, order and behavior as today.
- The run screen: seven sections in one view (section 6).
- A confirmation before a run is stopped (section 9).
- A plain closing block printed to the terminal's scrollback when the app closes (section 10).
- Small additive changes to the loop that emit data it already computes (section 12).

### Out of scope

- Changes to how the loop works. The loop changes only by emitting more events (section 12), and only where this spec says so.
- The plain output (`PlainTerminal`, `PlainUi`). It stays as it is.
- The other console scripts (`harness-tuner-install-agents` and the MCP servers).
- Detail views, drill-downs, tabs and pages. The run screen is one view.
- Attaching to a run from another terminal, and replaying a finished run.
- Steering a run while it is in progress, and answering the planner's questions in the app.
- Stopping after the current wave (decided: not needed).
- Desktop notifications, the terminal tab title, a bell, and other signals outside the terminal (decided: none).
- The four picks of a run (best quality, best value, balanced, fastest). No code computes them; only the report writes about trade-offs.
- Streaming the agent sessions, so Planning and Building show more than a timer. That needs a change to dispatch.
- Live progress finer than one chunk of 12 judged items.

### Sources

- The design conversation's decisions: `project_overview_mocks/decisions_from_conversation.md`. Section 15 of this spec repeats the key ones with their reasons.
- The mocks: `project_overview_mocks/`. They are references. They are not the UI design. `ui_design.md` is the UI design, and it can depart from the mocks where Textual does something better.
- The code inventory: `research/codebase/`.

## 2. Users and situations

The user is a developer who starts a tuning run and checks on it now and then over minutes to hours. They want to know the run is healthy and getting somewhere, without reading logs. They do not operate the run while it is in progress: the loop is hands-off once it starts.

## 3. Entry: which interface a run gets

The app runs only when stdin and stdout are both terminals. These are the situations:

| Situation | Start-up | Run display |
|---|---|---|
| A terminal, no display flags | Guided start, in the app | Run screen, in the app |
| A terminal, `--no-interaction` | Plain start-up steps in the app, no questions | Run screen, in the app |
| A terminal, `--no-progress` | Guided start, in the app. The app then closes, prints the closing block of the start (section 10), and hands back the terminal | Plain lines (`PlainUi`), unchanged |
| A terminal, `--no-interaction --no-progress` | Plain start-up lines, unchanged | Plain lines, unchanged |
| No terminal (stdin or stdout is not a TTY) | Plain start-up lines, unchanged | Plain lines, unchanged |

Behavior changes from today, both small:

- `FORCE_COLOR` or `TTY_COMPATIBLE` with stdout piped no longer selects the live display. Textual needs a real terminal, so these runs get plain lines.
- A terminal run with stdin redirected (`harness-tuner < /dev/null`) gets plain lines, not the live display. Textual reads keys from stdin.

Argument errors (exit 2) happen before the app starts and print as today.

## 4. Lifecycle

1. **Launch.** The app opens on the start-up screen.
2. **Start-up flow** (section 5). It ends when the person answers "Start tuning?" with yes, or at once when no questions are asked.
3. **The run's own start.** This covers the agent CLI test prompt, the loop's preflight, setup, the baseline measurement, the model preflight, the detectability and noise checks, wave 0 (recon probes) and the opening screen. The run screen shows it in the Starting mode (section 7.6). Today the agent CLI test prompt can take up to 3 minutes with nothing on screen. The app shows it as a step with a spinner.
4. **Waves.** The run screen shows each wave's planning, experiments and promotion gate.
5. **Done.** The run ends (time limit, budget, the planner has nothing left to try, the tripwire, or a provider quota or authentication error). The report is written, and the run screen shows the Done mode. The app stays open until the person closes it.
6. **Close.** The app closes, prints the closing block to the terminal's scrollback (section 10), and exits with the run's exit code.

The exit codes stay as they are today:

| Code | When |
|---|---|
| 0 | The run finished |
| 1 | Ctrl+D at a start-up question, `x) Exit` in any menu ("Start tuning?" included), or a start-up error |
| 2 | Argument errors (before the app starts) |
| 130 | Ctrl+C during the start-up, or "Stop tuning" during the run |

## 5. The start-up flow

### Must not change

The start-up flow keeps everything it has today:

- the steps, in the same order: welcome, Kiln check, demo copy, project, task and folder, readiness, API keys, the questions, the key check again, the summary, the rerun command and "Start tuning?";
- every user-facing string, exactly;
- every choice, its key, its order and its default;
- every validation rule and its error message;
- the behavior with flags given, which removes the questions those flags answer;
- the behavior without questions (`--no-interaction`);
- the `What next?` problem loop: `r` re-checks, Open actions open a page and ask again, and `x) Exit` stops.

The source of truth for all of this is the code (`src/kiln_harness_tuner/onboarding/`) and `specs/projects/cli-onboarding/`. Section 3 of `research/codebase/cli_and_startup.md` lists it step by step with file and line.

### Interaction behaviors to keep

These behaviors are part of the flow's design. The new widgets must do the same:

- **Action menus.** One key acts at once, with no Enter. Arrows and Enter also work. `Enter)` may only lead a menu. A key that no action has is ignored.
- **Pickers.** Typing filters the rows, on the whole row text, so ids also work. A question's hint replaces the default hint `↑↓ move · type to filter · Enter pick`. Rows show aligned columns. The highlighted row shows its description under it. A disabled row shows its reason, can't be picked, and the arrows skip it. The cursor starts on the default row when that row is enabled, otherwise on the first enabled row.
- **Text answers.** The default is pre-filled, and Enter accepts it. A bad answer shows the validator's message and keeps what was typed.
- **Spinners.** Every blocking check shows its text with a spinner while it runs.
- **Step history.** When a question is answered, it is replaced by its step line (`✓`, `✗` or `!`, a label, and the text). Earlier step lines stay visible.
- **Problem blocks.** A failed check shows a `✗` title, its body lines and its warnings, then `What next?`.
- **Open a page.** With a graphical browser, the link opens. Otherwise the app shows "Open this link in your browser: <url>". The link is also clickable in the app.
- **Ctrl+C and Ctrl+D.** At any point in the start-up, Ctrl+C stops at once with `harness-tuner: stopped.` and exit 130. Ctrl+D stops with exit 1. Nothing is in flight, so there is no confirmation.

### What may change

The look: layout, colors, borders, widgets and spacing. It should feel native to Textual and match the run screen's conventions (section 8). The welcome box, the step lines, the problem blocks and the summary may be redrawn.

## 6. The run screen

One full-screen view. The sections, top to bottom:

1. Title bar
2. Goal, and Time and spend, side by side
3. Run timeline
4. Results
5. Experiments, and Log. Side by side on a wide terminal; stacked on a narrow one.
6. Footer

Section 8 sets the layout rules. Section 7 sets what each section shows.

## 7. Sections

### 7.1 Title bar

- "KILN HARNESS TUNER", the task name, the run id, the agent CLI, the agent and eval slot counts.
- No health area. Health events (rate limits, errored eval items, quota, tripwire, retries) go to the log only (7.7). A warning that comes and goes on a timer is hard to read. (Decided.)

### 7.2 Goal

What the run optimizes, what it holds and what it only watches. These follow `--objective`:

| Objective | Optimize | Hold | Watch |
|---|---|---|---|
| `quality` | Eval scores (targets up) | Every eval: none may drop beyond doubt on val | Cost, latency |
| `speed` | Latency per request | Quality: equal or better on val | Cost |
| `cost` | Cost per request | Quality: equal or better on val | Latency |

A data line shows the split sizes: train, val, and test with "sealed, never read".

The goal does not change during a run. It earns its place because it frames every number on the screen. (Decided after the user asked: "Did we drop goal entirely?")

### 7.3 Time and spend

- **Time:** elapsed time against the time limit, as a gauge, with start and end clock times.
- **Spend:** spend against the budget, as a gauge on the same scale as time. Below it, spend by lane (Kiln provider keys, and the agent CLI's subscription, named after the agent CLI) and the burn rate per hour. With no budget, it shows the spend with no gauge end.
- **Wave:** "wave N of about M". M is the waves the run can still afford: the lower of the time left divided by the mean wave time, and the budget left divided by the mean wave cost, plus the current wave. M is blank until wave 1 ends.
- **[LAST WAVE]:** a tag shown only on the wrap-up wave. When it is not the last wave, nothing shows. There is no "last wave: no" flag.
- Not shown (decided): which limit runs out first, and a budget overshoot warning.

### 7.4 Run timeline

One axis from the start of the run to its time limit:

- setup, and wave 0 (recon);
- each wave's planning, screening, experiments and promotion gate, as segments;
- a mark at each promotion, and a different mark for a gate with no winner;
- the waves still projected, at the mean wave length, up to the time limit;
- a now marker.

The timeline is how the screen shows where the run is in the loop. Its segments come from the loop's phase and wave events.

### 7.5 Results

Results answers "is the agent getting better?" for every concern at once.

**Concerns.** One per eval score that can decide a promotion, plus cost per request, plus latency. A multi-score eval gives one concern per score; the card names the eval and the score. Every concern gets the same treatment, so the section grows by one card per concern. All concerns are shown, in the task's own order, with cost and latency after the evals. There is no folding and no sorting by priority.

**Three tiers per concern:**

| Tier | Subtitle | What it is |
|---|---|---|
| **Start** | Starting point | The config the run began from. On train until the first promotion gate, then on val, because the gate measures the incumbent on every val item. The key says which split it shows. |
| **Best Confirmed** | Val set measured · Name: \<run config\> | One run config: the current baseline. It is best across all concerns at once, confirmed by the promotion gate on val. Its values form one coherent profile, a config you could ship. Before the first promotion, the key says "no promotion yet" and the tier shows no bar. |
| **Best Unconfirmed** | Train set measured | Not one config. For each concern, the best single run config seen for that concern since the last promotion, measured on train, usually on that concern's eval only. Each card can name a different config. The values do not combine. |

**Best Unconfirmed rules:**

- Only experiments since the last promotion count. At each promotion gate the tier clears. A config that does not hold on val drops out.
- Only configs with a successful verdict count, and screen survivors for cost and latency. Configs that were stopped, dead-ended, capped or rejected never count, however good one number looks. This stops "cheapest ever" from being a config that broke quality.
- It shows only where it beats Best Confirmed on that concern.
- Its number is the estimated change against its parent, on train. Its parent is Best Confirmed (or Start before the first promotion), because every wave branches from the current baseline. Its bar is drawn at the parent's value plus that change. The key marks it as an estimate.

**Each card shows:**

- the concern's name, and its priority when the eval has one;
- three bars, one per tier, each with its value;
- a colored change. For pass-rate evals it is in percentage points; for cost and latency it is the relative change; for rating evals it is the raw change. Green means better, red means worse, and dim means no clear change. Best Confirmed's change is against Start on val. Best Unconfirmed's change is against its parent on train.
- how many experiments in flight measure this concern;
- the run config that holds Best Unconfirmed, when there is one.

**The key** sits on the section's header line, beside the Compare link, as far as it fits. The rest wraps on the lines under it, between entries. It names the Best Confirmed config.

**Before the first gate**, the cards show Start (train) only, and Best Unconfirmed as experiments land.

### 7.6 Experiments

The Experiments section is always visible. Its mode follows the loop:

| Mode | When | Shows |
|---|---|---|
| Starting | From the hand-off until wave 1 is planned | A checklist of the run's own start steps: the agent CLI test, preflight, setup, the baseline, the model preflight, detectability, the noise check. Then wave 0's recon probes, as cards. Then the opening screen (speed and cost objectives), as a step with its progress. |
| Planning | While the planner works on a wave | The planner's steps: the planner session, each screening round and each re-plan, with elapsed time. The last wave's cards stay below until the new plan lands. The plan's items appear only when the planner session ends. |
| Running experiments | While the wave's items run | One card per item. |
| Promotion gate | While candidates are measured on val | The same cards. Candidates show "At the gate" with their val progress. |
| Done | After the run ends | The run summary (below). |

**One card per item.** A card answers four questions:

1. **What is being tried:** the hypothesis, in the planner's words, as the card's main text, up to two lines.
2. **Which concern:** the target, named as on its Results card.
3. **How far along:** steps (build, measure, analyze), plus gate for promotion candidates. Done steps, the current step and later steps look different.
4. **Is it working:** "14 of 36 judged · 5 fixed · 0 broke so far", spelled out. Then a strip with one cell per judged item, when it fits.

Secondary, quieter: after the item id on the top border, its target eval or its kind; on the bottom border, the item's total spend, with no cap (section 16, decision 9).

A card keeps its size and its place from the start of the wave to its verdict. A finished card shows its verdict in plain words. Cards never fold, move or regroup.

**Item states.** The loop's item states map to four card states:

| Loop state | Card state |
|---|---|
| `planned` | Queued: waiting for an agent slot |
| `building_eval`, `building_rc`, `reviewing` | Building. A failed generality, judge-copy or reference check shows "rebuilding once". |
| `measuring` | Measuring |
| `analyzing` | Analyzing |
| `probing` | Asking (probes) |
| `done` | Landed: the verdict |

**Item types:**

| Type | Card title | Steps |
|---|---|---|
| Fix | The hypothesis; target: its eval | build, measure, analyze |
| Merge | "Combine \<ingredients\> on top of \<baseline\>"; target: all evals | build, measure, analyze |
| Probe | The question; target: "Question" | asking, answered |
| Eval build | "Build an eval for \<issue\>" | build |
| Issue baseline measurement | "Measure \<issue\> on the baseline" | measure |
| Re-measure | "Complete the measurement of \<run config\>" | measure |

**Verdicts in plain words.** No stop-rule names:

| Outcome | Card text |
|---|---|
| Success, a promotion candidate | Train success · checking validation set |
| Success, not a candidate | Success |
| No win | No win |
| Stopped by `futility` | Stopped: can't beat its parent |
| Stopped by `eval_metric` | Stopped: quality clearly worse than its parent |
| Stopped by `residual` | Stopped: fixed too few of its targets |
| Stopped by `targets` | Stopped: a named target still fails |
| Stopped by `latency_ratio` / `cost_ratio` | Stopped: clearly slower / costlier than its parent |
| Stopped by `not_better` | Stopped: not clearly faster / cheaper |
| Stopped by `guard_losses` | Stopped: broke too many items that passed |
| Lever not applied | Stopped: the change did not take effect |
| Capped | Stopped: reached its spending cap (inconclusive) |
| Dead end | Dead end: \<reason\> |
| Infra failure | Failed: \<error class\> |
| Not reproduced | Not reproduced |
| Promoted at the gate | Promoted · now Best Confirmed |
| Rejected at the gate, which had no winner | Rejected at val |
| Not picked at a gate that promoted another | Not selected · \<winner item id\> promoted (yellow) |
| Held at the gate | Held: waiting on an open question |
| Blocked at the gate | Blocked: copies eval text |
| Probe answered | YES or NO, and the evidence count |

Left out of the cards and kept for the log (decided): the check findings, the stop-rule names, chunk numbers and the plan's predicted effect.

Four of these are too long for a card. The cards use shorter words for them, and the log keeps the words above (decided in the UI design review; the forms are in `ui_design.md` section 7.5).

**Done mode** replaces the cards with the run summary:

- how the run ended, in plain words (time limit, budget, nothing left to try, tripwire, provider error, stopped by you);
- elapsed time, spend against the budget, waves, experiments, promotions, experiments stopped early;
- Best Confirmed: its run config and its description;
- the report path, or "no report" when the run was stopped;
- the report path is a link;
- "Next steps": "Visualize Experiment Graph in Kiln ↗"; "Compare \<Best Confirmed item id\> to the starting point in Kiln ↗", with a note that the evals may need a run on the withheld 'test' dataset (or, with no promotion, "No experiment was promoted, so the starting point stays best."); "Read Tuning Report ↗" (muted "(writing…)" until it exists). The compare link holds only the starting point and Best Confirmed.

Results, the timeline and the log stay as they were at the end.

### 7.7 Log

What just happened. Newest at the bottom, like a log, and the view follows the newest line unless the person has scrolled up. One line per event, with a time, a glyph for the kind of event, and plain text. A divider marks each wave.

**In the log:**

| Line | Comes from |
|---|---|
| Promoted | The gate's decision |
| Gate results: rejected at val, held, blocked | The gate's events |
| Verdicts | Item outcomes |
| Stops, with the stop reason in plain words | Item outcomes |
| Screening round results: survivors and drops with reasons | Screening events |
| Probe answers | Probe events |
| Wave started: its parent baseline and item counts | Wave events |
| Built: what the run config changed | Build events |
| Check findings that force a rebuild or end an item | Check events |
| The planner's assumed answers to open questions | A new event (section 12) |
| Health: rate limits, errored eval items, quota, tripwire, retries | Health events |
| Run start, start config, detectability warnings, exit | Run events |
| Lessons, dead ends and new issues the agents wrote, with the record's one-line overview | A new event (section 12) |
| A tripwire backoff | Health events |

**Not in the log** (decided), and where each one goes:

| Event | Where it goes |
|---|---|
| Item state changes | Experiments |
| Chunk progress | Experiments |
| Phase start and end | Timeline, Experiments |
| Spend events | Time and spend |
| Agent transcripts | Links |
| Lineage | A link to Kiln |

The density is "everything notable": outcomes, plus builds and check findings. There is no density control.

### 7.8 Footer and links

The footer holds the main links and keys: `c` Compare in Kiln, `g` Experiment graph, `r` Report, `q` quit. Links are also clickable wherever they show.

Kiln web URLs, built from the Kiln host, the project id and the task id:

| Link | URL |
|---|---|
| Lineage | `{kiln_host}/optimize/{project_id}/{task_id}/experiment_lineage` |
| Compare | `{kiln_host}/specs/{project_id}/{task_id}/compare?models={rc_1},{rc_2},…&columns={n}&from=optimize` |
| A run config | The run-config route the code already has (`onboarding/kiln_urls.py`) |

Compare opens Start, Best Confirmed and each distinct Best Unconfirmed config, in that order, without repeats. These are the Kiln app's own rules (`compare_run_configs_link` in `app/web_ui/src/lib/utils/link_builder.ts` of the Kiln repo):

- `models` is a comma-separated list of run config ids, one per column;
- `columns` is the number of entries, from 2 to 10, and the page ignores a value outside that range;
- so the link always has at least 2 configs (Start and Best Confirmed are the same before the first promotion, which leaves one empty column), and it keeps the first 10.

Lineage also takes `?selected=<id>` to open with one node selected. A card's id can link there.

Report opens the report file when it exists. Before then, the key shows nothing.

## 8. Layout and conventions

- **One cohesive design.** Every section follows the same conventions: one way to title a section, one way to draw a box or card, one color meaning per color, one way to show a link, one way to show a state. Results and Experiments both use cards, so the run screen does not mix cards in one place with rows in another. `ui_design.md` defines the conventions. Every screen, including the start-up flow, follows them.
- **Brand.** The palette comes from the Kiln logo: red `#E74D31`, yellow `#F4B544`, blue `#415CF5`. Semantic colors (better, worse, in flight) are separate from the brand colors, and the mocks' palette is not carried over.
- **Light and dark.** The app follows the terminal's light or dark background. When the terminal does not report one, it uses dark.
- **Never color alone.** Every state also has a glyph or a word, so the screen reads under `NO_COLOR`, on light terminals and for colorblind people.
- **Reflow.** Cards stretch and reflow to the terminal width. At about 150 columns and up, Experiments and the Log sit side by side; narrower, they stack. (Proposal.)
- **Window size.** The screen is designed for a full-screen terminal on a laptop, about 210×62. There is no compact design. A smaller window scrolls, with whatever is simplest in Textual, and nothing is cut off. (Decided.)
- **Textual first.** Prefer Textual's own widgets and layout to drawing custom character art. Where a mock asks for custom drawing that Textual can't do cleanly, simplify the design.

## 9. Interaction and exiting

Interaction is minimal, on purpose. The run is hands-off.

- **Links:** clickable with the mouse, and the main ones also on keys (7.8).
- **Log:** scrolls with the mouse wheel and the keys. It follows the newest line unless the person has scrolled up.
- **Window:** a resize reflows the screen.
- **Nothing else:** no detail views, no settings, no steering.

**Exiting:**

| When | Key | What happens |
|---|---|---|
| Start-up flow | Ctrl+C | Stops at once, exit 130, as today |
| Start-up flow | Ctrl+D | Stops, exit 1, as today |
| During the run | `q` or Ctrl+C | A dialog: "Stop tuning?". It names what is in flight (for example "4 experiments in flight. Their work so far is lost.") and offers **Stop tuning** and **Keep tuning**. Keep tuning is the default. |
| After "Stop tuning" | – | The app shows "Stopping…" while the run is cancelled through the run's existing cancel path (the one SIGTERM uses): agent sessions killed, the run lock released. No report is written. The app closes and prints the closing block. The exit code and the "interrupted" line are whatever that path gives today (decided: the exit code does not matter). |
| Done | `q` or Enter | The app closes and prints the closing block. No confirmation; nothing is in flight. |
| Any time | SIGTERM, SIGHUP | As today: the app forwards them to the run's existing stop code, which cancels the run (decided: the run thread must get them). |

## 10. The closing block

A full-screen app erases what it drew when it closes. Today the terminal's scrollback holds the start-up's step lines, its summary, the rerun command and the frozen waves. The rerun command is printed nowhere else. So when the app closes, it prints a plain closing block to the scrollback.

The closing block uses the same text the plain path prints for the same moments, from the same code, so the two can't drift:

1. **The start:** the summary and the rerun command, as the guided start prints them today. This part is also what the app prints when it closes before the run under `--no-progress`.
2. **The run** (when a run happened): the exit line and totals that `PlainUi` prints at exit, then Best Confirmed, the report path (or "no report") and the run folder.
3. **An error** (when the app closes on one): the error, in the error screen's plain form.

## 11. Robustness

- **The display never changes the run.** An error in the app is caught, logged to the run log, and does not reach the loop. Spend metering and budget enforcement do not depend on the display.
- **A slow loop never freezes the app.** The loop sometimes blocks its own thread, for example a 60-second `opencode export`. The app stays responsive, keeps its spinners and clocks moving, and accepts the stop key.
- **A slow app never slows the loop.** The app reads the run's state; the loop does not wait for the app.
- **Stray output.** Python warnings and early log lines can write to stderr while the app is open. They must not corrupt the screen. They go to the run log file.
- **Platform.** Linux and macOS, Python 3.11 and up, as today. Windows can't start the command today, and this project does not change that.

## 12. Data contract

The app shows only what the loop exposes. Most of it is already in the wave log's events. The rest comes from the additive changes below, which the user approved: new events or fields only, with no change to what the loop does.

**Already emitted, used as is:** the objective; spend by lane and source; the budget; elapsed and time left; the wrap-up flag; phase start and end; wave start and end; item states and their times; per-item spend; each arm's spending cap; verdicts and stop reasons; probe answers; screening results; integrity flags; promotion decisions, rejections, holds and blocks; health signals; the exit reason; the report path.

**Derived by the app, from events or run files:** the goal's roles (a fixed map of the objective); split sizes; waves remaining and the burn rate; rebuilds (a second build event for an item); the Best Unconfirmed tier (from outcomes, promotions and merge ingredients); the timeline's segments and projection.

**Added to the loop (approved):**

1. **Per-eval train results per experiment:** for each (eval, score), the estimated change against the parent with its interval, judged and planned items, and the cost and latency against the parent. Emitted when an arm's measurement ends, stopped arms included.
2. **Val tables per gate:** each candidate's and the incumbent's per-eval val values, changes and intervals, and cost and latency. Emitted at each gate.
3. **Structured chunk counts:** judged, fixed and broke as fields on the per-chunk event, beside today's text line.
4. **Gate progress per val fill:** one event as each val fill completes, instead of a burst at the end.
5. **Item details at wave start:** each item's type, hypothesis, targets and kind of change, so cards show them from the start, not only at the verdict. (Approved.)
6. **The planner's open questions:** the question and the default it assumed, when the planner returns them.
7. **Memory records the agents write:** lessons, dead ends and new issues. Agents write memory through the loop's gateway, which already appends the objects agents create to a file in the run folder (`gateway_created.jsonl`). Memory writes get the same treatment: the gateway appends each one (its kind tags and one-line overview) to a run-folder file, and the loop forwards new lines into the wave log as events when each agent session ends. About 20 lines in all. This replaces comparing memory snapshots, which was rejected as the wrong approach. (Decided.)

**From the CLI, not the loop:** the run id, the task and project names and ids, the Kiln host, the agent CLI, and the slot counts. The CLI passes them to the app.

**Not available, so not shown:** eval-job progress inside a chunk (the tracker folds an `eval_progress` event that is never emitted), live agent-session activity, slot saturation, and Kiln transport retries.

`architecture.md` must check each item above against the code and confirm that each change stays additive.

## 13. Flags, configuration and dependencies

- **Flags.** All 27 flags work as today, `--no-progress` and `--no-interaction` included (section 3). The removed flags still exit 2 with their messages. No new flags. If a later step adds one, `tests/test_docs.py` requires a docs row.
- **Environment.** `NO_COLOR` is honored. `FORCE_COLOR` and `TTY_COMPATIBLE` no longer select the live display (section 3).
- **Dependencies.** Textual is added. rich stays (`PlainUi`, the plain start, the error screen). questionary and prompt_toolkit stay only if the plain path still needs them; otherwise they are removed. Architecture decides.
- **Docs.** `docs/` describes the new interface where it describes the old one.

## 14. Open questions

None at the functional level. The visuals of every card type, Eval build, Issue baseline measurement and Re-measure included, are settled in the UI design review.

## 15. Decision log

The key decisions from the design conversation and from speccing, with their reasons.

| Decision | Why | Rejected |
|---|---|---|
| One full-screen view with sections, no tabs or pages | The person checks in now and then and wants everything at a glance | Tabs, a dashboard with pages |
| Functional design before visual design; mocks are guidance | Avoid anchoring on an early look | Building the mocks as drawn |
| One Textual app from launch to exit | One interface, one look | A separate app for the start-up |
| Keep the start-up flow's steps, strings, order and behaviors; change only the look | The UX had a deep design pass; the UI had a light one | Redesigning the questions |
| Title "KILN HARNESS TUNER" | Brand | – |
| A Goal section | Every number on the screen means something only against the goal | Hiding the goal in the title bar |
| Twin gauges for time and spend | Two limits, one glance | A burn chart, capacity meters |
| "wave N of about M", and a [LAST WAVE] tag only on the wrap-up wave | A tag that only appears says more than a flag that is always there | "Last wave: false" |
| Cut "which limit runs out first" and the budget overshoot warning | Not needed | – |
| A run timeline | It shows the whole run and where the loop is, in one strip | A loop ring, slot lanes, a stage stepper |
| Results: three tiers named Start, Best Confirmed, Best Unconfirmed | Separates what is proven on val from what is promising on train | "Champion" (a new word; the code says baseline), "Promoted" (an action, not a state), baseline / best overall / promoted |
| Best Confirmed is one config, best across all concerns; Best Unconfirmed is the best config per concern | Best Confirmed is a config you can ship; Best Unconfirmed is evidence per concern that may not combine | Mixing the two into one stacked bar |
| Best Unconfirmed resets at each promotion and drops configs that fail val | After a promotion, older train wins are measured against an old parent | Keeping all wins since the start |
| Cost and latency only count configs whose quality held | "Cheapest ever" must not be a config that broke quality | Any config's best number |
| Best Unconfirmed's number is its estimated change against its parent | An experiment's raw train score is skewed: its parent's failures are judged first | A raw absolute score |
| Every concern gets the same card; all concerns shown; task order | Scales to many evals; nothing hidden | Folding quiet concerns, priority sorting |
| Results as cards (render F), reflowing with the width, key on one line | The most readable render, and it scales by width | Stacked bars with intervals and notes ("way too much information"), split half-block bars, column chart, delta table, small multiples, tiles |
| Experiments always visible | Live experiments are what is happening now | Experiments only in one mode |
| Experiments as cards (layout C), same style as Results | One cohesive look | Rows, a pipeline table, a stage board, focus-and-fold |
| A finished card keeps its size and place | Nothing jumps; the wave reads as a whole | Folding finished items to one line |
| Each card answers four questions; plain-word verdicts | The old rows were unreadable: ids and jargon first, five number formats on a line | Stop-rule names, chunk numbers, "+5 −0" |
| Three core modes (Planning, Running experiments, Promotion gate), plus Starting and Done | A few states a person sees most of the time; item sub-states kept apart | A long list of loop phases |
| Log in log order, newest at the bottom, with a glyph per kind and wave dividers | It reads like a log | Newest at the top |
| No "needs you" section | The loop is hands-off; nothing waits on a person | A pinned question panel |
| Health events in the log only | A warning that comes and goes on a timer is hard to read | A health area in the title bar |
| The gateway emits the memory records agents write, and the loop forwards them to the wave log | A direct event is simpler and more reliable than comparing memory snapshots, and the plain path gets the lines too | Comparing memory snapshots in the app |
| One design for a full-screen laptop terminal (about 210×62); smaller windows scroll | Any modern computer can drag the window larger; a second, compact design doubles the work | A compact design for small terminals |
| Links attach to things; the run summary is the Done mode | Neither is a section of its own | A links section, a separate summary screen |
| Interaction: links and the stop key only | The run is hands-off | Detail views, steering |
| Confirm before stopping a running run; no report on stop | Stopping loses in-flight work, which costs money | Stop at once; stop after this wave |
| Stay on Done until the person closes the app | They see the result | Exit on its own |
| A plain closing block in the scrollback, the same text as the plain path | The scrollback held the summary and the rerun command | Textual inline mode |
| Small additive events for per-eval results, val tables, chunk counts and gate progress | The Results section needs them; the loop already computes them | Reading run files off disk; dropping the numbers |
| Light and dark follow the terminal; brand colors from the logo | On brand, on any terminal | The mocks' palette |
| Out of v1: the four picks, streaming agent sessions, finer live grain, a paired comparison, notifications, the tab title, a bell | No data for them, a core change, or not needed | – |

## 16. Changes from the data audit (approved)

`research/codebase/data_contract_audit.md` checked every element of section 7 and the comps against the code. These decisions settle what it found. Where they differ from sections 7 and 12, they win.

| # | Element | Decision |
|---|---|---|
| 1 | Judged-item strip | Approved change 3 also carries the chunk's per-item cells: eval, item, parent passed, candidate passes. That is what tells "held" from "still failing". |
| 2 | Eval priority ("p0") | Removed from the cards. The Goal row no longer says "weighted by priority": under `quality` the evals are not weighted. |
| 3 | Score names | The card shows the eval name. It adds the score key only when the eval has more than one score. |
| 4 | Split sizes | The data line is "train N · held out M (val and test) · test never read". Val is per eval and test alone is not known. |
| 5 | Run config names | The app looks the names and descriptions up in Kiln (the task's run config list) and keeps a dictionary of them. It reads again when it sees an id it does not know. Ids alone are not enough. |
| 6 | Start values | Pass rates come from setup. Rating, cost and latency appear in the Start row when the first experiment that measures them completes. Until then the row says "not measured yet". |
| 7 | Probe answers | "YES · 3 drives": the answer and the number of drives. "UNCLEAR" is a third answer. |
| 8 | Issue measurement and re-measure cards | No progress gauge, because nothing is emitted while they measure. They show the elapsed time. |
| 9 | Card spend | The card shows the item's total spend only ("$1.60", above 0), with no cap and no item id. |
| 10 | Items cut off at the deadline | A new verdict, "Cut off: tuning ended", from the existing deadline and fatal-cancel events. |
| 11 | Promoted | "Promoted" shows only when the gate ends with no block for that run config. |
| 12 | Cost and latency cards | No in-flight count: experiments target evals only. An experiment card always names an eval. |
| 13 | Best Confirmed's change against Start | Colored by direction only (green better, red worse). There is no "no clear change" state for it. |
| 14 | How a wrap-up ended | "Ended early: not enough time or budget for another wave" (priority 2). |
| 15 | "wave N of about M" | M uses the loop's own wrap-up window and minimum wave start (priority 3: only if easy). |
| 16 | The plain path | Unchanged. New events are not printed by `PlainUi`; they are still in `wave_log.jsonl`. |
| 17 | Old code | `questionary`, `RichTerminal`, `LiveUi` and their tests are removed. Their behavior tests move to the new widgets. |
