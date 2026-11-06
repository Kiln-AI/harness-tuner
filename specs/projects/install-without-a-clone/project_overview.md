---
status: draft
---

# Install without a clone

**Goal:** `uv tool install kiln-harness-tuner` and `uvx kiln-harness-tuner` work from PyPI, with no checkout. The tuner's own code and state stay separate from the user's work.

**Why:** today the tuner only works from a git checkout. It reads its agent files from `.agents/`, which isn't in the wheel. It writes `runs/`, `traces/` and `reports/` under the checkout. It starts its gateway with `uv run --project <checkout>`. It finds all of this from `Path(__file__).resolve().parents[2]` (`cli.py`, `install_agents.py`), so the README's `uv tool install .` likely points at the wrong folder today.

Other branches touch the same docs: the "harness" → "agent CLI" rename (`claude/loving-mccarthy-esry86`) and the launch cleanup (license and docs). Start from them if they've merged.

## Design (agreed with Steve)

1. **Agent files ship in the package.** Include `.agents/` (skills and role instructions) as package data and read it with `importlib.resources`, not from the checkout.
2. **All tuner state lives in `~/.kiln_ai/harness_tuner/`** (Kiln already uses `~/.kiln_ai`). That covers `runs/`, `traces/`, `reports/`, the run lock and `.quarantine/`. Keep today's layout under the new root. Add an override (an env var such as `KILN_HARNESS_TUNER_HOME`) for tests and CI. Print the report's path at the end of a run.
3. **The agents' working directory.** The agent CLIs find skills from their working directory, which today is the checkout. Install the chosen CLI's agent files into a folder under the new root (for example `~/.kiln_ai/harness_tuner/agents/<cli>/`), and start agent sessions there. Keep `kiln-harness-tuner-install-agents --dest` working for people who want the skills in their own project.
4. **Gateway.** Start the gateway from the installed entry point (`kiln-harness-tuner-gateway-mcp`, with the same interpreter), not `uv run --project <checkout>`.
5. **Fences.** The file fences and per-role sandbox configs name these folders. Move every path consistently and keep every deny. Agents still can't read run state, traces, reports or the Kiln project tree, except the run's own scratch.
6. **Direct disk access to the Kiln project stays.** The split check, eval inputs, stored eval rows, skill installs and probe cleanup all read or write the project on disk. Don't change these. Say in the docs that the tuner runs on the same machine as the Kiln server.
7. **Contributors.** Running from a clone (`uv sync`, then `uv run kiln-harness-tuner`) still works and uses the same state folder.
8. **Docs.** The README and `docs/install.md` install with `uv tool install kiln-harness-tuner` (or `uvx`), drop "it has to be a real checkout", and say where reports and runs go.
9. **CI packaging smoke test.** Build the wheel, install it in a fresh environment, then run `--help` and a dry pre-flight from an unrelated folder. Check that the agent files are found and that state lands under the override folder.
10. **Don't publish.** Get it ready to publish; Steve publishes on release day.

## Out of scope

- Moving run state or traces into the Kiln API or database.
- Windows support.
- Any change to how the Kiln project is read or written on disk.

## Done when

- Tests and the new smoke test pass in CI.
- The summary lists every path that moved.
- Steve does a full live run from an installed wheel on a clean machine (no test covers a live run today).
