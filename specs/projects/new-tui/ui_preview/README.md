# UI preview

A small Textual app that draws every screen of `../ui_design.md` with
hardcoded data. It is design material for review. It is not product code: it
is not shipped, nothing imports it, and the package does not depend on
Textual.

The data is an invented run: a freight-depot desk agent, tuned for quality
with a $50 budget and a 3-hour limit, in wave 4. The numbers are made up, but
the sections agree with each other.

## Files

| File | What it holds |
|---|---|
| `preview_app.py` | The app: the two themes, the run screen, the start-up screen, the card gallery and the stop dialog |
| `widgets.py` | One widget per building block of the design (section 4) |
| `preview.tcss` | The stylesheet |
| `sample_data.py` | The invented run |
| `make_screenshots.py` | Saves an SVG of each state into `screenshots/` |
| `screenshots/` | The SVGs, at 210×62 and at 120×40 |

## Run it

From the repository root:

```
uv run --with textual==8.2.8 python specs/projects/new-tui/ui_preview/preview_app.py
```

It opens on the run screen in the Running mode. To open on another state,
give its name: `starting`, `planning`, `running`, `gate`, `done`, `welcome`,
`picker`, `problem`, `text`, `summary` or `gallery`.

## Keys

| Key | What it shows |
|---|---|
| `1` … `5` | The run screen: Starting, Planning, Running, Promotion gate, Done |
| `6` `7` `8` `9` `0` | The start-up screen: busy, picker, problem, text answer, summary |
| `g` | The card gallery: every experiment card variant |
| `q` | The stop dialog (run screen). In Done, `q` and Enter close |
| `w` | The [LAST WAVE] tag on or off (run screen) |
| `t` | Light or dark theme |
| `c` `l` `r` | Compare, Lineage and Report links (shown as a notice) |
| Ctrl+Q | Quit the preview |

These keys are hidden from the footer, so the footer shows only the keys the
real app has.

The start-up prompts work: an action menu acts on one key, the picker filters
as you type and skips the disabled row, and the text answer shows its
validator's message. In the picker and the text answer, the input has the
focus and takes every printable key, number keys included. Press Esc to leave
the input, then the preview keys work again.

## Screenshots

From the repository root:

```
uv run --with textual==8.2.8 python specs/projects/new-tui/ui_preview/make_screenshots.py
```

The script runs the app headless with `App.run_test`, with the spinner frame
and the input cursor fixed, so a file changes only when the design changes.
To look at an SVG, open it in a browser.
