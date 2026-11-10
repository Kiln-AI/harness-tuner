# Decisions from the design conversation

A record of what the design conversation decided, written before the functional spec so nothing is lost. The functional spec is the authority; this file is its source material. "User" means the project owner's own call; "proposed" means it was suggested and not pushed back on, so it stands until the spec says otherwise.

## Framing

- The screen is for a person watching a live tuning run, not for the start-up questions. (User)
- One big view with sections, not tabs or pages. (User)
- Functional design came before visual design. Mocks are guidance, not specs. (User)
- Interactivity is low priority. Clickable links to Kiln are enough. (User)

## Sections

| Section | Decision | Notes |
|---|---|---|
| Title bar | In. Reads "KILN HARNESS TUNER". (User) | Task, run id, agent CLI, slot counts. Health warnings appear on the right only when there is one: rate limits, quota, tripwire, provider errors. |
| Goal | In. (User: "did we drop goal entirely?") | Sketch C1·B: what is optimized, what is held, what is only watched, plus the data rule (train / val / test sealed). The roles change with `--objective`. |
| Time and spend | In. Sketch C2·A twin gauges. (User) | Spend split by lane (Kiln provider keys and Claude Code subscription), burn rate, "wave N of about M", and a [LAST WAVE] tag shown only on the wrap-up wave, not an always-on "last wave: false". (User) |
| Run timeline | In. Sketch C5·B. (User: "really nice") | Each wave's planning, screening, experiments and gate on one axis, promotions marked, projected waves ahead, a now marker. It carries "where are we in the loop". |
| Results | In. Render F. (User) | See below. |
| Experiments | In, always visible. Layout C. (User: "live experiments is important to show") | See below. |
| Log | In. Newest at the bottom, like a log. (User) | Content per `02-activity-feed.html`. A link to Kiln's lineage graph replaces any graph in the TUI. (User) |
| Links | In, attached to things, not a section. (User: "not a section per se") | "Compare in Kiln UI ↗", "Lineage in Kiln UI ↗", "Report ↗". Colored link style. (User preferred it over plain key hints.) |
| Run summary | Not a section. It is the Done state of the experiments area; keep the design, decide what it shows. (User) | Exit reason, totals, Best Confirmed, report path, links, "run your test split next". |
| Needs-you / human questions | Out. (User: "the whole thing is built to be hands off once started") | The loop never waits on a person. The planner records open questions with an assumed default; the gate can HOLD a candidate on one. Surface both as log lines and in Done. |
| Which limit runs out first | Out. (User) | |
| Budget overshoot warning | Out. (User) | |

Cut from the screen, with where it goes instead: the experiment graph (Kiln lineage link), issue board, lever map, coverage grid (Kiln and the report), stop-rule internals and statistics (at most a detail view), slot and concurrency use (a health warning only when saturated), planner transcript and prompt (on-demand link), static setup facts such as guidance text, model policy and eval headroom (start-up output), big-number ambient mode (later, if at all).

## Results

- Three tiers per concern. (User)
  - **Start**, subtitle "Starting point".
  - **Best Confirmed**, subtitle "Val set measured", with the run config's name. One run config: the current baseline, best across all concerns at once, confirmed by the promotion gate on val. Its column is a coherent profile of one config you could ship.
  - **Best Unconfirmed**, subtitle "Train set measured". Not one config: for each concern, the best single run config seen for that concern, measured on train and usually on that eval only. A different config per row; the rows cannot be combined.
- Names rejected: "Champion" (a new word; the code says "baseline") and "Promoted" (an action, not a state). (User)
- Best Unconfirmed resets at every promotion round; a config that does not hold on val drops out. (User, technical note)
- Best Unconfirmed shows only where it beats Best Confirmed. (Proposed)
- Cost and latency only count configs whose quality held (a successful verdict or a screen survivor). A cheap config that was stopped, dead-ended, capped or rejected never shows. This answers "cheapest ever, even if it was awful". (User raised the problem; rule proposed)
- Every concern (each eval, cost, latency) gets the same treatment, so the section scales to more concerns. (User)
- Show all concerns; no folding of quiet ones. (User)
- No special ordering; the task's order. (User)
- Render F: one card per concern, three bars (one per tier), value and a colored change ("+4.5%" style: percentage points for pass-rate evals, relative change for cost and latency; green better, red worse, dim no real change). Cards stretch and reflow with the terminal width. The key sits on one line: on the section's title line when wide, its own line when narrow, and it names the Best Confirmed config. In-flight count in the card corner; Best Unconfirmed's run config on the bottom border. (User chose F; reflow and one-line key were user decisions)
- Rejected along the way: a stacked bar with a hatched "unconfirmed gain" extension, intervals, parents and notes (user: "low readability, way too much information"); split half-block bars (works, but uneven spacing); the column chart, tables and tiles (F won). The delta table (C3·B) and per-eval small multiples (C4·B) were compared in place in the whole-screen comp; F still won. (User)
- In-flight indication on results is kept. (User: "in flight is nice")
- Palette proposed in the renders: Gray · White · Violet for the three tiers, keeping green and red for changes only. The user later said the mocks' colors are inconsistent and off brand, so the final palette is open.

## Experiments

- Each item answers four questions. (Proposed in the sprint, accepted with C)
  1. What is being tried: the hypothesis, in plain words, as the item's title.
  2. Which concern: the same name as its Results card.
  3. How far along: build, measure, analyze, then the verdict, drawn as steps.
  4. Is it working: "14 of 36 judged · 5 fixed · 0 broke so far", spelled out.
- Verdicts in plain words: "Stopped: can't beat its parent", not "futility". (Proposed)
- Secondary and dim: the kind of change (prompt, skill, tool wrapper, model, thinking), the id, spend against its cap, the judged-item strip. (Proposed)
- Out of the section, left to the log: check details (show only "rebuilding once"), stop-rule names, chunk numbers, the predicted effect. (Proposed)
- Layout C: one card per item. A finished card keeps its size and its place in the grid and shows the verdict; no folding. (User)
- Mode of the section (user asked for "a few core states, sub-states isolated"):
  - Core: Planning, Running experiments, Promotion gate. Bookends: Starting, Done. Tripwire backoff is a health line, not a mode.
  - Item sub-states: Building (includes building an eval and the pre-spend checks with their one rebuild), Measuring, Analyzing, Landed. Probes: asking, answered.
- Planning keeps the last wave's cards visible until the new plan lands. The gate shows candidates "At the gate" with val progress. (Proposed in pass 3)

## Log

- Content per `02-activity-feed.html`. What goes in: promotions, gate results, verdicts, stops, screens, probes, wave starts, builds, check findings, lessons and dead ends, the planner's assumed answers, health, run start and exit. What stays out: item state changes, chunk progress, phase start and end, spend, transcripts, lineage. (Accepted)
- Log order, newest at the bottom. (User)
- Glyph per kind and dividers per wave (pass 2 log, accepted as "much better").

## Layout

- At wide widths (about 150 columns and up) the experiments sit on the left with the log as a full-height column on the right; narrower, everything stacks. (Proposed)
- Order: title bar, goal and time-and-spend side by side, run timeline, Results, then experiments and log. (Pass 3 comp)

## Not decided

- Signals outside the terminal (tab title, OSC 9;4 progress, desktop notifications). Proposed as "lean in" early, never decided.
- Deferred until the high-level design is done (user): a paired Best Confirmed vs Start comparison on val; whether code should compute the four picks (best quality, value, balanced, fastest) for Done; streaming the agent sessions so Planning and Building show more than a timer; per-item results inside a chunk.

## What the code had, at the time (verify against current main)

- The wave's headline is code-composed counts ("5 experiments, 1 probe"); the planner writes no headline.
- The four picks are not computed in code; only the report agent writes about trade-offs.
- Per-eval train rows (`PerEvalResult`) and val tables (`CandidateValTable`) are computed but not emitted as events.
- Lessons and dead ends are agent-written memory records, visible only by diffing the memory snapshots under `runs/<id>/snapshots/`.
- Per-chunk progress exists: `measure_chunk` carries a line with fixed and broke counts against the parent (text; structured values computed). `eval_progress` comes from job polling.
- The inputs for "waves remaining" exist in the orchestrator (mean wave time and cost per wave) but are not emitted.
- The agent CLI runs with JSON output, so planner and builder sessions do not stream.
- The planner's open questions are written to memory, not emitted.
- Item states: planned, building_eval, building_rc, reviewing, measuring, analyzing, probing, done. Loop phases: preflight, setup, baseline_measure, planning, promotion_gate, tripwire_backoff, reporting.
