---
status: complete
---

# Phase 3: Docs

## Overview

Phases 1 and 2 changed how the tuner installs, where it keeps its state and
where agent sessions run. The docs still describe a checkout: `uv tool install
.`, "it has to be a real checkout", `uv` on the agent CLI's `PATH`, reports in
`reports/` beside the code, sessions in the repository root, and read denies
built from the checkout (`examples/`, sibling projects). This phase makes every
user and contributor document describe the code as it now is (FS §11,
architecture §11). No code changes, apart from one docs guard test.

The names follow the merged "agent CLI" rename: `--agent-cli`, `AGENT_CLIS`,
`docs/agent-cli.md`. The behaviour described is phase 2's as committed,
including its deviations: the overlap check runs before the state folder is
made; OpenCode workspaces have the `.agents/skills` link with an edit deny on
`<workspace>/.agents/**`; Ctrl-C, SIGTERM and SIGHUP print the run-folder line;
a handled error prints the run folder and an existing fallback report.

Agent-facing text (skills, role templates, `agent_files/AGENTS.md`) is not
touched (FS §11 last bullet). Their paths are relative to the run folder and
stay correct.

## Steps

1. **`README.md`**
   - Badge and clone URLs: `Kiln-AI/harness-tuner`.
   - Requirements: drop the "`uv` on the `PATH` that `claude -p` inherits"
     row. The Kiln row says the tuner runs on the same machine as the Kiln
     server, because it reads and writes the project on disk.
   - Platform note: `uv sync` (a clone) fails on macOS x86_64 because of the
     lock; `uv tool install` resolves without the lock, and that is untested
     there.
   - Install: `uv tool install harness-tuner`, or `uvx harness-tuner …` with
     no install. No checkout. Verify block with the new command names.
   - "From a clone": `git clone`, `uv sync`, `uv run harness-tuner …`; same
     state folder; agent files read from `src/`.
   - "Where the files go": one short paragraph naming
     `~/.kiln_ai/harness_tuner/`, `KILN_HARNESS_TUNER_HOME`, the start line and
     the finish line, linking to the new install.md section.
   - The skills section rewritten for FS §9: a run installs its own agent
     files into the state folder; `harness-tuner-install-agents --agent-cli
     … --dest DIR` is for interactive sessions in your own project; `--dest`
     is required; no instructions file; no `--force`.
   - Quickstart: clone to get the demo, copy `Forum Moderation/` out, run the
     installed command. Every "writes a report under `reports/`" names the
     state folder's `reports/` and the printed path.

2. **`docs/install.md`**
   - Delete step zero (get the repository).
   - Platform support: as in the README.
   - Section 1 (Python and `uv`): the check is `uv --version`; `uv` finds or
     downloads a Python 3.11+ for the tool. The floor error is the one
     `uv tool install` prints when no 3.11+ is available and downloads are
     off; fix `uv python install 3.11`.
   - Sections 2 and 3 become one section, "2. The package": `uv tool install
     harness-tuner` (output lists the five executables), the `PATH` failure
     and fix, `uvx harness-tuner` as the no-install form with the cache note,
     how a run starts its MCP servers (`<python> -P -m …`, nothing on the
     agent CLI's `PATH`), `kiln_api_mcp` for interactive sessions, and a
     "From a clone" part.
   - New unnumbered section "Where the tuner keeps its files": location,
     override rules (empty = unset, `~`, relative), mode 0700, layout block,
     the start and exit lines, the agents folder per version (no clean-up),
     the refusals (unwritable, overlap with the project), old checkout state
     not migrated, the lock is per state folder.
   - Renumber sections 4–6 to 3–5, with every in-text "section N".
   - Section 3 (Kiln server): the tuner runs on the same machine as the
     server; the metadata check runs as `uvx --from harness-tuner python -c …`.
   - "Verify the whole install": the installed commands; `uv run pytest` only
     from a clone.
   - The intro's count of sections and of unnumbered sections.

3. **`docs/limitations.md`**
   - The lock: under the state folder's `runs/`; one machine and one state
     folder; two runs with different `KILN_HARNESS_TUNER_HOME` on one task are
     not stopped.
   - A new operational entry: `uvx` runs from uv's cache, and cleaning it
     during a run breaks the run's later sessions.
   - Quarantine and run-directory paths name the state folder.
   - "The read fence is a path fence": the deny list as it is now (project,
     the state folder's runs/traces/reports except this run's scratch, other
     transcripts), and the gaps of a deny list: other Kiln projects,
     `~/.kiln_ai/settings.yaml`, and any clone of this repository with its
     `examples/` and old run state. Codex and OpenCode fences are allow lists.
     Replaces the `examples/` and project-scan text.

4. **`docs/architecture.md`**
   - §1 topology: `harness-tuner`; Claude sessions run in the agents folder
     and read `.claude/skills` there; one line naming the state folder.
   - §2: the `.mcp.json` that `--dest` writes is for interactive sessions.
   - §3.1: the state folder and the agent-file install before step 0; the
     lock and the rendered configs in the state folder's `runs/`; the start
     line when the run folder exists.
   - §11: report and transcript paths in the state folder; a "terminal
     lines" bullet (start, finish, handled error, interrupt); the
     "git-ignored here" sentence replaced.
   - §12: file writes (the working folder is the agents folder; the Edit
     deny on it for every role; the read-only roles' deny on the state
     folder); the read fence's deny and allow lists; the project-scan and
     `examples/` paragraphs replaced by the deny-list gap; the OS sandbox's
     deny-write on the state folder; the MCP servers started as `<python> -P
     -m <module>`.
   - §13 layout: `paths.py`, `install_agents.py`, `agent_files/` inside the
     package; `.agents/` removed.
   - §15: `test_paths.py` asserts that no module walks out of the package.

5. **`docs/agent-cli.md`**
   - §2: the working directory note; the trace folder path.
   - §7: transcript and scratch paths in the state folder; a bullet for the
     Claude working folder and its transcript slug.
   - §10 Codex: the skills rule names the agents folder's `.agents/skills`;
     the refused-reads sentence; the link and `AGENTS.md` copy come from the
     agents folder.
   - §11 OpenCode: `read` names the agents folder's skills; `edit` denies the
     workspace's `.agents/`; the worktree is the git root or `/`; the
     refused-reads sentence drops the repository's `CLAUDE.md`; skills and
     instructions come from the workspace link and copy.

6. **Smaller docs**: `docs/concepts.md` (run folder and report paths, the
   command name), `docs/costs.md` (wave log path), `docs/how-it-works.md`
   (trace folder and report paths), `docs/agents.md` (report path),
   `docs/your-own-task.md` (command name, quoted messages, stop lines,
   "Reading the result" paths and the git-ignore sentence).

7. **`.github/CONTRIBUTING.md`**: setup from a clone (`uv run
   harness-tuner`), same state folder; the layout block with `agent_files/`;
   the rule about what loop agents read names `agent_files/`; the ruff
   exclude path; the contributor install with `--dest .` and its ignored
   outputs.

8. **`examples/moderation/README.md`**: the clone is where the demo comes
   from; `harness-tuner`; the report is in the state folder's `reports/`,
   and the run prints its path.

9. **`tests/test_docs.py`**: a guard over the user-facing documents
   (`linked_files()`, `.github/CONTRIBUTING.md`, `examples/moderation/README.md`)
   that no retired form comes back: the bare `kiln-harness-tuner` command and
   clone name (file names `kiln-harness-tuner.log` and
   `kiln-harness-tuner-<run_id>.md` and skill names allowed), the old
   `kiln-harness-tuner-install-agents`, `-gateway-mcp`, `-skill-installer-mcp`
   commands, `uv run --project`, `uv tool install .`, and `.agents/AGENTS.md`.

## Tests

- `test_docs_name_no_retired_command`: every user-facing document is free of
  the retired command names, the checkout install and the old instructions
  path, and reports each hit with file and line.
- `test_docs_name_no_retired_command` coverage: the scanned set includes
  `.github/CONTRIBUTING.md` and `examples/moderation/README.md`, and each file
  exists, so the guard cannot pass on a narrowed set.
- The existing `test_docs.py` link and flag checks stay green after the edits
  (the new install.md anchor and moved sections included).
