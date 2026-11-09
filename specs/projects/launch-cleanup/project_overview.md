---
status: complete
---

# Launch cleanup (license and docs)

**Context:** Kiln Harness Tuner goes public soon. This pass makes the repo
legally open source and makes the docs agree with the code. Don't change
behavior except where an item says so.

Another branch (`claude/loving-mccarthy-esry86`) is renaming "harness" to
"agent CLI" in many of the same docs. Start from it if it has merged;
otherwise expect small conflicts.

## 1. License (MIT)

- Add `LICENSE` (MIT). Use the same copyright holder as the MIT-licensed parts
  of the main Kiln repo.
- `pyproject.toml`: add the license fields (`license = "MIT"`,
  `license-files`) and the classifier.
- `.github/CONTRIBUTING.md`: update the "Licence" section, which says the file
  isn't there yet.
- README: add a short License section.

## 2. Doc contradictions (fix the docs to match the code)

- `docs/install.md` (around L108) lists 4 installed executables.
  `pyproject.toml` declares 5: `kiln-harness-tuner-install-agents` is missing.
- ~~**Eval builder splits.**~~ Removed from this project by the owner.
- **Held-out tags.** `docs/limitations.md` (around L291-294) says items tagged
  `holdout` aren't treated as held out. `quarantine.py` (around L54) treats
  `holdout*` and `heldout*` as held out. Fix the doc.
- **Minimum Kiln version.** README (around L16) and `docs/install.md` (around
  L155-161) say to get Kiln from kiln.tech. The loop needs server APIs that
  Kiln v1.2.0 doesn't have: the memory API, the eval-job API and run-config
  provenance. State **Kiln v1.3** as the minimum version (it ships after this
  pass). If it's easy, say what fails on an older Kiln.
- **Windows.** README (around L25) and `docs/install.md` (around L27) say
  Windows works. `cli.py` imports `fcntl` (Unix-only) at the top, and the
  sandbox fences are macOS and Linux mechanisms. Say Linux and macOS (Apple
  silicon) are supported and Windows isn't.

## 3. Small corrections (docs and agent skills)

- **"max tokens" isn't a run-config field**, but
  `.agents/skills/kiln-harness-tuner-batch-selection/SKILL.md` lists it as a
  parameter lever (around L61 and L446). Remove it there and anywhere else:
  the planner could propose a lever nothing can build.
- **Tool descriptions.** Wherever a doc or skill suggests editing a tool's
  description in place, reword it: a new description means a new tool or a
  wrapper.
- **Wrappers.** Wherever a doc or skill implies wrappers only apply to code
  tools, reword it: they also work on MCP and built-in tools. Check this
  against the wrapper code before you change the text.

## Out of scope

- installing without a clone (PyPI); that's a separate project
- provenance checks
- anything about promotion or "shipping" a winner
- the harness → agent CLI rename (the other branch)
- the scrub pass (a separate task)

## Done when

- Tests pass.
- Each item above is fixed in every doc and skill where it appears.
- The summary lists each change with file and line, and anything that
  couldn't be verified.
