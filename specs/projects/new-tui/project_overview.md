---
status: draft
---

# New TUI

A new TUI for the tuner. It is the screen a person watches while a tuning run is in progress.

## Design and functional goals

- See the design conversation that started this project. Record all our decisions from that conversation in the functional design: what is in and out, the design decisions we made, and their justifications. All key decisions from that conversation should be captured in the functional design.
- All the mocks from that conversation are guidance only. If in later planning or implementation it's better to do them another way that's cleaner in Textual and gets cleaner code, the plan should adapt.
- The interactive questions at startup: keep the questions, strings, order, etc. We did a pretty deep design pass on the UX of that flow. However, we were light on UI design. Update the UI design to use Textual, and to feel more native to the new plan.
- There may be more open questions to answer during speccing. Follow the normal `new project` process.

## Tech

- Use the Textual framework.
- All flags should still work. Tell me if that's not possible (e.g. `--no-progress`).
- The goal is to only work on the TUI, not to change the core loop. This is big and complicated, which is fine as an isolated TUI, and bad if it means complicating the core loop. Some minimal and safe changes to expose things are okay, but nothing here says we should change the core harness-tuning classes. We want minimal or zero changes to core.
  - Check in planning that all the data we need is exposed.
  - If not: can we make a small, safe change to core to expose it?
  - If not: can we update the UI so it doesn't need changes to core?

## Architecture

- Check the legitimacy of the comps.

## UI design

- The mocks are not final. I'd love for the final to be higher quality and better; these are early mocks. They are saved as references in `project_overview_mocks/`, not as final UI designs.
- Style: the mocks' colors were inconsistent and not on brand, with a mishmash of titles and section styles.
- The mocks were not designed with Textual in mind. Textual may have great components we should use. Rendering these exact designs might be 3x the code and buggier, where off-the-shelf Textual pieces would do.
- Interaction: almost none is probably okay. Just clickable links?
- Exiting: needs a confirmation once the run is going, because exiting is expensive (in-flight work is lost).
- I want to see and approve designs when we spec the UI. Either use artifacts, or a small hardcoded Textual app I can play with.
- Define and follow conventions: the whole UI should feel cohesive. Not cards in one frame and rows in the next.
