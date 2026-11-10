---
status: draft
---

# UI Design: New TUI

This document sets the look of the app: its conventions, its theme, its layout and each screen. `functional_spec.md` sets what the app shows and does; this document does not repeat it.

The mocks in `project_overview_mocks/` are references only. This design departs from them on purpose where Textual has a better way, where they were inconsistent, or where they were off brand.

The preview app in `ui_preview/` draws every screen here with hardcoded data. Its screenshots are the visual reference. Where this text and the screenshots disagree, this text wins.

## 1. Principles

1. **One convention set.** Every screen uses the same parts (section 4). Results and Experiments are both card grids. There is no third style of box.
2. **Textual first.** Use Textual's widgets, layout, borders, themes and footer. Draw custom character art only where no widget does the job: the tier bars, the timeline strip and the judged-item strip.
3. **Meaning before decoration.** Color carries a fixed meaning (section 3), always paired with a glyph or a word.
4. **Calm by default.** The screen changes when the run changes. Only live things move: spinners, gauges and in-flight cards.
5. **Plain words.** The functional spec's wording tables are the copy. No stop-rule names, no internal ids where a name exists.

## 2. Theme

Two registered Textual themes, `kiln-dark` and `kiln-light`.

**Picking the theme.** Before the app starts, the CLI asks the terminal for its background color (an OSC 11 query, with a short timeout). A light background picks `kiln-light`; anything else, or no answer, picks `kiln-dark`. The app paints its own background, so a wrong guess still reads well. The command palette is off, so the theme can't change during a run.

**Brand.** The Kiln logo colors are red `#E74D31`, yellow `#F4B544` and blue `#415CF5`.

| Token | Role | kiln-dark | kiln-light |
|---|---|---|---|
| `$primary` | Live and interactive: links, focus, running cards, spinners | `#415CF5` (text uses `#7B90FF` for contrast) | `#415CF5` |
| `$warning` | Provisional and attention: Best Unconfirmed, [LAST WAVE], warnings | `#F4B544` | `#F4B544` for bars; `#9A6A00` for text |
| `$error` | Worse, stopped, failed, errors; the brand mark | `#E74D31` | `#D1401F` |
| `$success` | Better, success | `#4CC38A` | `#1E8A57` |
| `$foreground` | Best Confirmed, and body text | near white | near black |
| `$text-muted` | Start, labels, secondary text | gray | gray |
| `$background` / `$surface` / `$panel` | Screen, cards, dialogs | dark neutrals with a slight blue bias | light neutrals with a slight blue bias |

Green is the one color that is not from the logo. It is the meaning "better", which the brand has no color for.

## 3. Color meanings

One meaning per color, everywhere:

| Color | Means | Examples |
|---|---|---|
| Brand blue | Live or clickable | Spinners, running cards' borders, links, the now marker, focus |
| Foreground | Confirmed fact | Best Confirmed bars and values, verdict text |
| Brand yellow | Provisional, or needs attention | Best Unconfirmed bars and values, [LAST WAVE], warnings in the log |
| Green | Better | Positive changes, ✓ steps, success verdicts, promotions |
| Brand red | Worse or stopped | Negative changes, stops, failures, errors; the title mark |
| Muted gray | Context | Start bars and values, labels, ids, spend, timestamps |

Every state also has a glyph or a word (section 5), so nothing depends on color alone.

## 4. Components

These are the only building blocks.

### 4.1 Title bar

One line, docked at the top, on `$panel`. On the left: the mark ("◆ KILN HARNESS TUNER", the ◆ in brand red, the words bold), then the task name and the run id. On the right, muted: the agent CLI and the slot counts. The same bar is on the start-up screen and the run screen.

### 4.2 Section header

One line above a section's content: the section name in bold capitals, then muted context, then the right-aligned meta (for example a link or an elapsed time). No borders around sections. Sections are separated by one blank line.

Example: `RESULTS   ■ Start · starting point   ■ Best Confirmed · val · w3-4 get_container wrapper   ■ Best Unconfirmed · train estimate          Compare in Kiln UI ↗`

### 4.3 Card

A Textual container with a rounded border (`border: round`). It is used for Results concerns, experiment items and the run summary.

- **Border title (top left):** what the card is. Bold name, then muted qualifiers. For example "Hold placement · p1" or "Hold placement · wrapper".
- **Border subtitle (bottom right):** muted meta. For example "⠸ 1 in flight · w4-1" or "w4-1 · $1.20 of $4".
- **Border color:** `$primary` while the card is live (an item in progress), muted otherwise. Results cards are never live.
- **Padding:** one cell left and right, none top and bottom.
- **Grid:** cards sit in a Textual grid with one cell gap. Column count follows the width (section 6).

### 4.4 Bar

A horizontal bar of block characters with 1/8-cell precision, followed by its value and, when there is one, its change. It is used only inside Results cards: one bar per tier.

### 4.5 Gauge

Textual's `ProgressBar` with the percentage and ETA hidden, with a label on the left and the limit on the right. It is used for time and spend.

### 4.6 Steps

A one-line row of stage names: `✓ build  ✓ measure  ⠸ analyze  · gate`. Done stages are muted with a green ✓. The current stage is bold `$primary` with a spinner. Later stages are muted with `·`. A stop shows a red ✕ on the stage where it stopped.

### 4.7 Step line

The start-up flow's history line: a glyph (✓ green, ✗ red, ! yellow), a 9-character label, and the text. Also used for the Starting mode's checklist and the Planning mode's planner steps, so the start-up and the run read the same way.

### 4.8 Tag

A short label in reverse video, for example ` LAST WAVE ` in `$warning`. Only for rare states that must stand out.

### 4.9 Link

`$primary`, underlined, with "↗" at the end. Clickable. The main links also have keys in the footer.

### 4.10 Log line

A muted time, a glyph colored by meaning (section 5), and plain text. Promotions are bold. A wave divider is a muted rule with the wave's name.

### 4.11 Dialog

A Textual `ModalScreen`: a card centered over a dimmed screen, with a title, the message and buttons. The safe choice has the focus.

### 4.12 Footer

Textual's `Footer`, showing the bindings of the current screen.

## 5. Glyphs

| Glyph | Means |
|---|---|
| ✓ | Done, passed |
| ✕ | Stopped, rejected, failed |
| ● | Success |
| ○ | No win |
| ◆ | Promoted, Best Confirmed |
| ▸ | A wave started |
| ! | Warning |
| ? | Assumed answer to an open question |
| ≈ | Lesson or dead end |
| ⠋⠙⠹… | In progress (a spinner) |
| · | Not started, neutral |
| ↗ | Opens Kiln or a file |
| ■ | A key swatch |

## 6. Layout

**Design size:** a full-screen terminal on a laptop, about 210×62. A smaller window scrolls the whole screen vertically. There is no compact design.

**Breakpoints** (`HORIZONTAL_BREAKPOINTS`): at 150 columns and up, Experiments takes the left two-thirds and the Log the right third of the bottom area. Below 150 columns they stack, with the Log under Experiments at a fixed height of 12 rows.

**Run screen, top to bottom:**

```
Title bar                                                        1 row
Goal (one third)              | Time and spend (two thirds)      6 rows
Run timeline                                                     5 rows
Results: header and key, then the card grid                      grows
Experiments (two thirds)      | Log (one third, full height)     the rest
Footer                                                           1 row
```

**Card widths:** Results cards are at least 34 columns wide (about 5 per row at 210 columns). Experiment cards are at least 44 columns wide (about 3 per row in the two-thirds column at 210).

## 7. Run screen sections

### 7.1 Goal

A section header "GOAL", then three rows and a data row:

```
▲ optimize  Quality        5 evals, weighted by priority
═ hold      Every eval     none may drop on val
○ watch     Cost, latency  shown, never decide
  data      train 312 · val 104 · test 104  sealed, never read
```

"▲ optimize" is green, "═ hold" yellow, "○ watch" muted.

### 7.2 Time and spend

A section header "TIME & SPEND", with "wave 4 of ~7" and the [LAST WAVE] tag right-aligned. Then two gauges on the same width, time over spend, and a muted line with spend by lane and the burn rate.

### 7.3 Run timeline

A section header "RUN TIMELINE" with a muted legend on the right. Then:

- a row of wave labels ("w1", "w2", …) above their planning segments;
- the strip: planning in muted `░`, screening in yellow `▒`, experiments in blue `▓`, a gate with a promotion as a foreground `◆`, a gate with no winner as a muted `·`, and projected waves as a faint `┄`;
- the time axis with ticks every 30 minutes;
- the now marker, a blue `▲`, with the clock time.

### 7.4 Results

A section header "RESULTS" with the key and the "Compare in Kiln UI ↗" link. The key wraps to its own line below 150 columns. Then a grid of cards, one per concern.

```
╭ Refusal honesty · p0 ──────────────────╮
│ ███████████████████████▍       70.2%   │
│ ███████████████████████████    79.8%  +9.6 │
│ ██████████████████████████████▉ 89.0% +9.2 │
╰─────────────────── ⠸ 1 in flight · w4-2 ╯
```

- Three bars, top to bottom: Start (muted), Best Confirmed (foreground), Best Unconfirmed (yellow). A tier with no value leaves its row empty, so every card has the same height.
- Values are right-aligned in one column; changes in the next, colored by meaning.
- The subtitle shows experiments in flight on this concern, and the Best Unconfirmed run config.

### 7.5 Experiments

A section header "EXPERIMENTS" with the mode and the wave ("Running · wave 4 from w3-4 · 2 building · 1 measuring · 1 analyzing · 2 landed") and the elapsed time on the right.

**Card (Running and Promotion gate modes):**

```
╭ Hold placement · wrapper ───────────────────╮
│ Return only the 6 hold fields from          │
│ get_container, so the model stops missing … │
│ ✓ build  ⠸ measure  · analyze               │
│ ████▓▓▓▓▓▓··························  14/36  │
│ 5 fixed · 0 broke so far                    │
╰──────────────────────── w4-1 · $1.20 of $4 ╯
```

- Line 1–2: the hypothesis (foreground).
- Line 3: the steps (4.6).
- Line 4: the judged-item strip, one cell per item: green █ fixed, dim green ▓ held, dim red ░ still failing, red ✕ broke, muted · queued. When the items don't fit, a gauge replaces the strip.
- Line 5: the result so far, or the verdict when landed, in the functional spec's words.
- Every card has the same height in every state.

**Card variants**, by item type and state:

| Card | Title | Lines 1–2 | Line 3 | Line 4 | Line 5 |
|---|---|---|---|---|---|
| Fix, building | concern · kind | hypothesis | steps | "an agent is writing the change · 3m10s", or "rebuilding once · copied text found" in yellow | – |
| Fix, measuring | concern · kind | hypothesis | steps | item strip and count | fixed and broke so far |
| Fix, analyzing | concern · kind | hypothesis | steps | item strip and count | fixed and broke |
| Fix, landed | concern · kind | hypothesis | steps | item strip and count | the verdict, colored by meaning |
| At the gate | concern · kind | hypothesis | steps with gate current | val progress gauge, "312 of 520 val items" | "◆ At the gate · 7 fixed · 1 broke" |
| Merge | "All evals · merge" | "Combine w3-1 and w3-3 on top of w3-4" | steps | as Fix | as Fix |
| Probe | "Question · probe" | the question | "✓ asked  ⠸ answering" | "3 drives" | "● YES, 3 of 3 drives" |
| Eval build | concern · eval build | "Build an eval for \<issue\>" | "⠸ build" | – | "Eval built" or the reason |
| Issue baseline measurement | concern · measure | "Measure \<issue\> on the baseline" | "⠸ measure" | gauge | "Reproduced" or "Not reproduced" |
| Re-measure | concern · re-measure | "Complete the measurement of \<run config\>" | "⠸ measure" | gauge | "Measured" |
| Queued | as the type | as the type | all stages `·` | "waiting for an agent slot" | – |

**Starting and Planning modes** show step lines (4.7) above the cards. In Planning, the last wave's cards stay below with a muted header "LAST WAVE · wave 4", until the new plan replaces them.

**Done mode** shows one wide card titled "RUN COMPLETE" in place of the grid, with the summary lines and links from the functional spec.

### 7.6 Log

A section header "LOG". Textual's `RichLog`, newest at the bottom. It follows the newest line until the person scrolls up, and follows again when they scroll back to the bottom. Each wave starts with a divider.

```
── wave 4 ─────────────────────────────
15:19 ▸ Wave 4 from w3-4 · 5 experiments, 1 probe
15:24 + w4-1 adds get_container_for_hold
15:25 ! w4-4: copied judge text, rebuilding once
15:29 ✕ w4-6 thinking low: stopped, can't beat its parent
15:31 ● w4-2 Refusal honesty: success, goes to the gate
15:41 ◆ w4-2 promoted · Refusal honesty +7.8 on val
```

## 8. Start-up screen

One screen, a transcript that grows down, with the title bar on top and the footer at the bottom. It keeps the flow's strings and behaviors (functional spec section 5); this section sets only how each part looks.

| Part | Look | Textual pieces |
|---|---|---|
| Welcome | A card titled "Welcome", with the welcome text | Container with border title |
| Step history | Step lines (4.7) | Static rows |
| Busy | A step line with a blue spinner in place of the glyph | Static with a timer |
| Problem | A card with a red border and the ✗ title as its border title; body lines, then warnings as `!` lines | Container |
| Action menu ("What next?") | The question, then rows "r) Check again", one per action, the highlighted row in reverse `$primary`. One key acts at once. | `OptionList` with key bindings |
| Picker | The question, a filter input, the rows with aligned columns, the highlighted row's description under the list, and the hint line in muted text. Disabled rows are muted with their reason and can't be highlighted. | `Input` and `OptionList` |
| Text answer | The question, an input with the default pre-filled, the hint, and the validator's message in red under the input | `Input` with a validator |
| Summary | A card titled "Summary" with the summary lines, the rerun command in a muted line, then "Start the run?" as an action menu | Container, `OptionList` |

When an answer is given, its prompt is removed and its step line takes its place. The newest part is always in view.

## 9. Stop dialog, stopping and closing

**Stop dialog** (`q` or Ctrl+C during a run):

```
╭ Stop the run? ──────────────────────────────────────╮
│ 4 experiments are in flight. Their work so far is   │
│ lost, and no report is written.                     │
│                                                     │
│              [ Keep running ]  [ Stop the run ]     │
╰─────────────────────────────────────────────────────╯
```

"Keep running" has the focus. "Stop the run" is red. Esc closes the dialog.

**Stopping:** the dialog closes, and the Experiments header shows "Stopping…" with a spinner until the run is cancelled.

**Done:** the footer shows `q Close`. The app then closes and prints the closing block.

## 10. Keys

| Screen | Keys |
|---|---|
| Start-up | The prompt's own keys (functional spec section 5); Ctrl+C stop; Ctrl+D stop |
| Run | `c` Compare in Kiln UI, `l` Lineage in Kiln UI, `r` Report (once it exists), `q` / Ctrl+C stop dialog; mouse wheel and Page Up / Page Down scroll the log or the screen |
| Done | `c`, `l`, `r`, `q` / Enter close |
| Stop dialog | Tab / arrows move, Enter chooses, Esc keeps running |

## 11. Departures from the mocks

| Mock | This design | Why |
|---|---|---|
| Sections drawn as boxes, and cards inside them | Sections have a header line and no box; only cards have borders | One box style. Boxes inside boxes were the "mishmash". |
| Gray · White · Violet for the tiers | Gray · Foreground · Brand yellow | On brand; yellow already means "provisional" |
| Cyan for in flight | Brand blue for live and clickable | On brand; one color for "live" |
| The card's in-flight count top right and its run config bottom right | Both in the border subtitle | Textual has one title per edge |
| Health in the title bar | The log | Decided in the functional spec |
| Custom gauges drawn in characters | Textual's `ProgressBar` | Less code |
| A custom scrolling log | Textual's `RichLog` | Less code, scrolling built in |
| Pickers and menus drawn by questionary | `OptionList` and `Input` | Native to Textual |
| Italic hypotheses | Plain text | Italics are uneven across terminals |

## 12. The preview app

`ui_preview/` holds a small Textual app with hardcoded data that draws every screen in this document. It is design material, not product code, and it is not shipped.

- Run it: see `ui_preview/README.md`.
- Keys in the preview switch between the start-up parts, the run modes, the card variants, the stop dialog, and the light and dark themes.
- `ui_preview/screenshots/` holds SVG screenshots of each screen at 210×62 and at 120×40, made by its screenshot script.
