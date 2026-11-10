---
status: complete
---

# New TUI

Run /spec new project for a new TUI. This is the project overview.

Start by pulling main from Origin. Some important PRs were merged, so memory about codebase is out of date.

Design and functional goals:
- See conversation above. Record all our decisions from that conversation into functional design. That should include decisions of what's in and out, the design decisions we made and their justifications. All key decisions from this conversation should be captured in functional design.
- All our mocks here are guidance only. If in later planning/implementation it's better to do them another way that's cleaner in Textual, which gets cleaner code, the plan should adapt.
- The interactive questions at startup: keep the questions/strings/order/etc. We did a pretty deep design pass designing the UX of that flow. However we were light on UI design. Update UI design to use Textual, and feel more native to the new plan.
- There may be more open questions to answer during speccing. Follow normal `new project` process.

Tech:
- use Textual framework
- all flags should still work, tell me if that's not possible (eg, `--no-progress`)
- Goal is to really only work on TUI, not change core loop. This is big and complicated, which is fine as an isolated TUI, and bad if it means complicating the core loop. Okay with some minimal and safe changes to expose things, but nothing here indicates we should make changes to the core harness-tuning classes. We want minimal or zero changes to core.
  - Check in planning all data we need is exposed
  - If not: can we do a small safe change to core to expose it?
  - If not can we just update UI to not need changes to core.

Architecture:
- need to check the legitimacy of the comps.

UI design:
- While I've given you mocks, they are not final. I'd love for the final to be higher quality and better, these are early mocks. Save these as references for "project_overview_mocks/" not final UI designs.
- Style: colors were inconsistent and not on brand, mishmash of titles/sections/etc
- Not designed with Textual in mind: maybe textual has some great components/pieces we should use. Maybe rendering these exact designs will be 3x the code and buggier, but I can use some off the shelf textual things instead.
- Interaction: almost none is probably okay, just clickable links?
- Exiting: should have confirmation once running as exiting is expensive (in flight work lost).
- I want to see and approve designs when we spec UI. Can either use artifacts, or a little hardcoded textual app I can play with.
- Define and follow conventions: whole UI should feel cohesive. Not cards in one frame and rows in the next.
