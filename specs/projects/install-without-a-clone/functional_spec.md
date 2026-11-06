---
status: draft
---

# Functional Spec: Install without a clone

## 1. Goal

`uv tool install harness-tuner` and `uvx harness-tuner` work from PyPI with no checkout. The tuner's code lives in its installed package. Its state lives in one folder in the user's home. Nothing is written to the folder the user runs it from.

Running from a clone (`uv sync`, then `uv run harness-tuner`) keeps working and uses the same state folder.

## 2. Names

| What | Today | After |
|---|---|---|
| Product | Kiln Harness Tuner | unchanged |
| Repository | `Kiln-AI/harness-tuner` | unchanged |
| PyPI distribution | `kiln-harness-tuner` | `harness-tuner` (reserved) |
| Python import package | `kiln_harness_tuner` | unchanged |
| Commands | `kiln-harness-tuner`, `kiln-harness-tuner-install-agents`, `kiln-harness-tuner-gateway-mcp`, `kiln-harness-tuner-skill-installer-mcp`, `kiln_api_mcp` | `harness-tuner`, `harness-tuner-install-agents`, `harness-tuner-gateway-mcp`, `harness-tuner-skill-installer-mcp`, `kiln_api_mcp` (unchanged) |
| Report and log file names, skill folder names, gateway tool names (`mcp__kiln_harness_tuner_gateway__…`), `KILN_HARNESS_TUNER_*` env vars | as today | unchanged |

`uvx harness-tuner` runs the command named `harness-tuner`, which is why the commands change.

A command's own name in its messages follows the command: the error prefix `kiln-harness-tuner: …` becomes `harness-tuner: …`, and argparse's `prog` is the new name. A message that tells the user to run a command names the new command.

## 3. User flows

### 3.1 Installed

```
uv tool install harness-tuner
harness-tuner --project-path … --project-id … --task-id …
```

or, with no install:

```
uvx harness-tuner --project-path … --project-id … --task-id …
```

The user can run it from any folder. The tuner runs on the same machine as the Kiln server: it reads and writes the Kiln project on disk (the split check, eval inputs, stored eval rows, skill installs, probe clean-up), and that does not change.

### 3.2 From a clone (contributors)

```
git clone https://github.com/Kiln-AI/harness-tuner.git
cd harness-tuner
uv sync
uv run harness-tuner …
```

Same state folder, same behaviour, with the agent files read from the package source in `src/` (editable install). The tuner does not know or care that it runs from a clone: no code looks for a checkout (§8.3).

### 3.3 What the user sees

- **At start**, as soon as the run folder exists (before the live display starts): `harness-tuner: run <run_id> — files in <state>/runs/<run_id>/`.
- **At a clean finish**: `harness-tuner: run finished — report: <absolute path>`. Today the path is already printed; only the wording changes.
- **At a fatal error after the run folder exists**: the error as today, plus a line naming the run folder (its `kiln-harness-tuner.log` holds the details). If a fallback report was written, a `report: <path>` line too. Today a fatal exit prints neither.
- **At an interrupt**: as today, plus the run-folder line.
- **At a start-up error before the run folder exists**: as today (one error line with a fix).

## 4. The state folder

### 4.1 Location

- Default: `~/.kiln_ai/harness_tuner/` (Kiln already keeps its settings in `~/.kiln_ai`).
- Override: the env var `KILN_HARNESS_TUNER_HOME`. An empty value counts as unset. `~` is expanded. A relative path is resolved against the current folder at start-up. No CLI flag.
- Created on first use, with mode `0700` (it holds session transcripts and val data). Missing parents are created. An existing folder's mode is not changed.

### 4.2 Layout

Today's layout under the checkout moves unchanged under the state folder, plus one new folder for agent files:

```
<state>/
  runs/
    <run_id>/                 one run (contents unchanged: scratch/, steps/,
                              snapshots/, wave_log.jsonl, kiln-harness-tuner.log,
                              settings*.json, mcp_config*.json, train_set.json, …)
    .lock-<project>-<task>    the one-run-per-task lock
    .quarantine/<project>-<task>/   archived probe and eval configs
  traces/<project>/<task>/<run_id>/  copied session transcripts
  reports/kiln-harness-tuner-<run_id>.md
  agents/<cli>/<version>/  the agent files a run's sessions read (§5.3)
```

### 4.3 Every path that moves

| What | Today | After |
|---|---|---|
| Run folders | `<checkout>/runs/<run_id>/` | `<state>/runs/<run_id>/` |
| Run lock | `<checkout>/runs/.lock-<project>-<task>` | `<state>/runs/.lock-<project>-<task>` |
| Quarantine archive | `<checkout>/runs/.quarantine/<project>-<task>/` | `<state>/runs/.quarantine/<project>-<task>/` |
| Session transcript copies | `<checkout>/traces/<project>/<task>/<run_id>/` | `<state>/traces/<project>/<task>/<run_id>/` |
| Reports | `<checkout>/reports/` | `<state>/reports/` |
| Agent file source (skills, instructions, Claude MCP config) | `<checkout>/.agents/` | `src/kiln_harness_tuner/agent_files/` in the package (moved with `git mv`) |
| Contributor instructions | `<checkout>/.agents/AGENTS.md` (shared with loop agents) | tracked `<checkout>/AGENTS.md` and `<checkout>/CLAUDE.md` (§5.2) |
| Claude skills a run's sessions read | `<checkout>/.claude/skills/` | `<state>/agents/claude/<version>/.claude/skills/` |
| Claude instructions a run's sessions read | `<checkout>/CLAUDE.md` | `<state>/agents/claude/<version>/CLAUDE.md` |
| Claude interactive MCP config a run writes | `<checkout>/.mcp.json`, `<checkout>/.claude/settings.json` | not written by a run any more (only `--dest` installs write it) |
| Codex and OpenCode skills a run's sessions read | `<checkout>/.agents/skills/` | `<state>/agents/<cli>/<version>/.agents/skills/` |
| Codex and OpenCode instructions | `<checkout>/AGENTS.md` | `<state>/agents/<cli>/<version>/AGENTS.md` |
| Claude session working folder | `<checkout>` | `<state>/agents/claude/<version>/` |
| Where the tuner finds Claude's own transcripts | `~/.claude/projects/<slug of checkout>/` | `~/.claude/projects/<slug of the Claude agents folder>/` |
| Codex and OpenCode session folders | `<run>/scratch/<label>/` | unchanged inside the run folder (they move with it) |
| Gateway trace folder | `<run>/scratch/traces/` | unchanged inside the run folder |
| Gateway launch | `uv run --project <checkout> kiln-harness-tuner-gateway-mcp …` | `<python> -m kiln_harness_tuner.gateway.server …` |
| Skill installer launch | `uv run --project <checkout> kiln-harness-tuner-skill-installer-mcp …` | `<python> -m kiln_harness_tuner.skill_installer.mcp_server …` |

`<python>` is the interpreter running the tuner (`sys.executable`).

### 4.4 Old state in a checkout

Not migrated. A checkout's old `runs/`, `traces/` and `reports/` stay where they are, and nothing denies them any more (§8.3). The docs say so, and say to delete or move them.

### 4.5 The lock

The lock lives in the state folder, so it covers every run that uses the same state folder. Two runs with different `KILN_HARNESS_TUNER_HOME` values on the same task are not stopped. The docs say so.

## 5. Agent files

### 5.1 They ship in the package

`.agents/` moves to `src/kiln_harness_tuner/agent_files/` with `git mv`, so history follows. The wheel contains it. The code reads it with `importlib.resources`, from the wheel and from an editable install the same way. Nothing reads agent files from a checkout path.

The role prompt templates (`src/kiln_harness_tuner/agents/*.md`) already ship and do not move.

### 5.2 The instructions file is split in two

Today one file, `.agents/AGENTS.md`, is both the loop agents' instructions and the contributor guide.

- **Loop-agent instructions**, `agent_files/AGENTS.md`: only the heading and the opening paragraph (what the loop is, that every agent is an agent-CLI session, that every measurement is a Kiln eval job, that test is never read). No layout, no example, no repository rules, and no "Write in ASD-STE100 Simplified Technical English" rule. This changes what loop agents read; it is a deliberate change.
- **Contributor guide**, a tracked `AGENTS.md` at the repository root, with the rest of today's content (layout updated to the new paths). A tracked root `CLAUDE.md` holds one line, `@AGENTS.md`, so Claude Code contributors load the same file. Codex and OpenCode read the root `AGENTS.md` themselves. These two names stop being gitignored install targets.

No skill or role template changes.

### 5.3 A run installs them into the state folder

Before its first session, a run installs the agent files for its agent CLI (the one `--agent-cli` names) into `<state>/agents/<cli>/<version>/`:

| CLI | Skills | Instructions |
|---|---|---|
| `claude` | `.claude/skills/` | `CLAUDE.md` |
| `codex` | `.agents/skills/` | `AGENTS.md` |
| `opencode` | `.agents/skills/` | `AGENTS.md` |

- **One folder per package version.** `<version>` is the installed package's version (`0.1.0`). A run on a newer version gets a new folder, so a live run on the old version is not disturbed.
- **Checked at every run start.** If the folder is missing, or its files differ from the package's, the run builds a fresh copy in a temporary folder beside it and swaps it into place. If the files match, the run uses the folder as is. A released version's files never change, so for an installed user the folder is written once. The check matters for contributors: an editable install keeps version `0.1.0` while its skills are edited, and the check carries each edit into the next run.
- **Skills and instructions only.** No `.mcp.json` or `.claude/settings.json`: loop sessions get their own MCP config per run and never read those.
- **No clean-up.** Old version folders stay. Each is about 2 MB.

## 6. Where agent sessions run

- **Claude Code**: every session's working folder is `<state>/agents/claude/<version>/`, so `.claude/skills/` and `CLAUDE.md` load from there.
- **Codex and OpenCode**: each session still runs in its own folder under the run's scratch, because that folder is their fence. Each one gets a link `.agents/skills` to `<state>/agents/<cli>/<version>/.agents/skills/` and a copy of that folder's `AGENTS.md`. Codex works this way today. OpenCode is new to it: today it finds skills and instructions through the git root above its session folder, which is the checkout, and an installed copy has no git root.

This refines design point 3: only Claude sessions start in the agents folder. Codex and OpenCode sessions link to it.

## 7. Gateway and skill installer

Both MCP servers a run starts are launched as `<python> -m <module>` with the interpreter running the tuner. That is the same code as the `harness-tuner-gateway-mcp` and `harness-tuner-skill-installer-mcp` entry points, and the way the gateway hook already starts. No `uv` and no checkout are needed at run time. `uv` stops being a requirement on the `PATH` the agent CLI inherits.

The two console scripts stay in the package for people who start a server by hand.

With `uvx`, the interpreter lives in uv's cache. Cleaning that cache during a run breaks the run's later sessions. The docs say so.

## 8. Fences

### 8.1 Rule

Every path the fences name moves with §4.3. Every deny stays, except the checkout-based ones, which are removed (§8.3). No read deny is added.

### 8.2 Claude Code

- **Read denies** (today → after): the run's own Kiln project (unchanged); `<checkout>/runs`, `traces`, `reports` → `<state>/runs`, `traces`, `reports` (this run's scratch is still re-opened); `~/.claude/projects` (unchanged).
- **Read allows**: this run's scratch (unchanged); the Claude skills and instructions → `<state>/agents/claude/<version>/.claude/skills/` and `CLAUDE.md`; the trace folder for the trace-reading roles (unchanged inside the run folder).
- **Bash sandbox writes**: the write deny moves from the checkout (today's working folder) to the state folder, with this run's scratch re-opened. The working folder is now the agents folder, inside the state folder.
- **Edit tool**: every role is denied the Claude agents folder. It is the new working folder, which Claude grants by default, and its files outlive the run. The read-only roles' deny on the checkout moves to the whole state folder. The other Edit denies (the project tree, the trace folder) are unchanged.

### 8.3 Checkout-based denies are removed

The tuner no longer has a checkout, so the denies built from one go, with the code that builds them:

- the scan of the checkout for other Kiln projects (`kiln_project_dirs` over the repository root);
- the deny of everything under the checkout's `examples/` (`example_denials`).

No code looks for a checkout, and no module derives a path from outside the package (`tests/test_paths.py` asserts that).

### 8.4 Codex and OpenCode

Their fences are allow-lists. Only the allowed paths change: the skills folder becomes `<state>/agents/<cli>/<version>/.agents/skills/`, and the session and trace folders move with the run folder.

### 8.5 Known gaps (unchanged, out of scope)

Claude's read fence is a deny-list, so a Claude loop agent can read files nobody named. That includes:
- other Kiln projects;
- `~/.kiln_ai/settings.yaml`, which holds Kiln's provider API keys;
- any clone of this repository, which contains its `examples/` (the demo's `posts.jsonl` holds every split's labels) and any old run state.

The first two were true before this project. The clone case was denied only for the clone the tuner ran from, and §8.3 removes that. A separate task covers all of it ("Close Claude's read-fence gaps"). `docs/limitations.md` names the clone case.

## 9. `harness-tuner-install-agents`

For people who want the three general skills (`kiln-api`, `kiln-task-setup`, `memory`) and the `call_kiln_api` tool in their own interactive agent sessions, for example to get a task ready for the tuner. A run does not use this command; it installs its own agent files (§5.3).

- `--dest` is required.
- It installs the skills in the CLI's layout (table in §5.3), replacing a skill folder of the same name and leaving other skills alone. For `claude` it also merges the `kiln_api_mcp` server into `<dest>/.mcp.json` and its switch into `<dest>/.claude/settings.json`, as today.
- It never writes an instructions file. The loop-agent instructions describe the loop and are no use in someone else's project.
- `--force` is removed: it existed only to replace an instructions file.
- The CLI is chosen with `--harness` as today, or `--agent-cli` if the rename branch has merged first (§13).

Contributors who want the skills in the repository run `uv run harness-tuner-install-agents --harness claude --dest .`. Its outputs (`/.claude/skills/`, `/.claude/settings.json`, `/.mcp.json`, `/.agents/`) are gitignored.

## 10. Packaging

- `pyproject.toml`: `name = "harness-tuner"`, the renamed scripts, version `0.1.0`, and a `Repository` URL (`https://github.com/Kiln-AI/harness-tuner`). `uv.lock` is regenerated for the new name.
- **Wheel**: the two packages as today, plus `kiln_harness_tuner/agent_files/`.
- **sdist**: only `src/`, `pyproject.toml`, `README.md`, and the license file once the launch cleanup adds it. Today's sdist ships the whole repository, including `examples/` with the labelled corpus. Tests, docs, examples and specs stay in git only.
- `uv build` (sdist, then a wheel built from it) succeeds, and `twine check` passes on both files.
- Nothing is published. Steve publishes on release day.

## 11. Docs

- **README**:
  - Requirements: drop the "`uv` on the `PATH` that `claude -p` inherits" row, and say the tuner runs on the same machine as the Kiln server.
  - Install: `uv tool install harness-tuner` or `uvx harness-tuner`, with no "it has to be a real checkout".
  - A short "From a clone" part for contributors.
  - The skills section rewritten for §9.
  - Quickstart: clone the repository to get the demo, copy `Forum Moderation/` out, run the installed command.
  - Every "writes a report under `reports/`" becomes the state folder path.
  - The badge and clone URLs fixed to `Kiln-AI/harness-tuner`.
- **`docs/install.md`**:
  - Step zero (get the repository) goes.
  - Sections 2 and 3 become one install section for `uv tool install` / `uvx`.
  - A new short section, "Where the tuner keeps its files": the state folder, the override, the layout, that old checkout state is not migrated, and that the lock is per state folder.
  - The "Verify" block updated.
- **Every other doc, the CONTRIBUTING file, and the example README**: command names, `runs/` / `traces/` / `reports/` / `.agents/` paths, `uv run --project` and `uv run kiln-harness-tuner` mentions updated.
- **`docs/limitations.md`**: the clone gap (§8.5), replacing today's text about the `examples/` deny; the per-state-folder lock (§4.5); and the uvx cache note (§7).
- **Agent-facing text** (skills, role templates) is unchanged apart from the instructions split (§5.2). Where it names a path relative to the run folder (`runs/<run_id>/scratch/traces/`, `reports/kiln-harness-tuner-<run_id>.md`), it stays correct.

## 12. CI packaging smoke test

A new CI job, beside the existing one, on the same triggers (every pull request, every push to `main`), on Python 3.11 and 3.13. The existing tests run from the checkout, so they cannot catch a package that only works from a checkout, which is today's bug. The job:

1. Builds with `uv build`: the sdist, then the wheel from it. It checks that the sdist holds only the files in §10, that the wheel holds the agent files, and that `twine check` passes.
2. Installs the wheel with `uv tool install` into temporary tool and bin folders, with `HOME` set to a temporary folder.
3. From an empty temporary folder:
   - `harness-tuner --help` and `harness-tuner-install-agents --help` print usage;
   - `uvx --from <wheel> harness-tuner --help` prints usage;
   - `harness-tuner-install-agents --harness claude --dest <tmp>` installs the skills and the MCP config and writes no `CLAUDE.md`.
4. **Dry pre-flight**: a real run, with the following in place.
   - **Fake `claude`:** a small script on `PATH` stands in for Claude Code. It records the folder it was started in, answers the start-up check prompt, and, when given an MCP config, starts the gateway from it and lists its tools.
   - **Project:** a copy of the moderation example's Kiln project.
   - **Kiln server:** `--kiln-host` points at a closed port, so no server answers.
   - **State folder:** `KILN_HARNESS_TUNER_HOME` is set to a temporary folder.

   The run gets through the agent-file install, the agent CLI check, the lock, the run folder, the fence and MCP configs and the split check. It stops at the pre-flight with exit code 1, because no Kiln server answers. The job checks:
   - the exit code and message;
   - the Claude agents folder exists under the override, and every fake-`claude` call ran in it;
   - the run folder and its config files are under the override;
   - the rendered gateway command uses the installed interpreter, and the gateway it started listed its tools;
   - nothing was written to the current folder, under `HOME` outside the override, or in the checkout.
5. Repeats step 4 without the override, and checks that the state lands in `$HOME/.kiln_ai/harness_tuner/`.

No product code exists for the test's sake. No new flag.

## 13. Coordination with other branches

- **The "harness" → "agent CLI" rename** (`claude/loving-mccarthy-esry86`), not merged yet. It renames `--harness` to `--agent-cli` and `docs/harness-seam.md` to `docs/agent-cli.md`, and edits `install_agents.py` and docs. This spec is written against `main`. If the rename merges first, rebase onto it and use its names.
- **The launch cleanup** (license and docs). Its license file joins the sdist (§10). The README's relative links breaking on PyPI is its concern, not this project's.

## 14. Errors and edge cases

Every start-up refusal below is one message with a `fix:` line and exit code 1, like today's start-up errors. They happen before the agent CLI check unless noted.

| Case | Behaviour |
|---|---|
| State folder cannot be created or written | Refuse: name the path and the OS error; fix: set `KILN_HARNESS_TUNER_HOME` to a writable folder. |
| State folder and the run's Kiln project overlap (either inside the other) | Refuse: the fences would contradict each other (the project is read-denied whole, scratch must be writable). |
| Agent files missing from the installed package | Refuse: name the path; fix: reinstall `harness-tuner`. |
| Building the agents folder fails (disk full, permissions) | Refuse with the OS error; nothing half-built is left in place (the build happens in a temporary folder). |
| Two runs start at once on the same version | Both find the folder missing or matching; a missing one is built by each in its own temporary folder, and the second swap replaces the first with identical files. |
| A newer version starts while an older run is live | Each uses its own `<version>` folder; the older run is not disturbed. |
| A contributor edits a skill while a run from the same clone is live, then starts a second run | The second run's check finds different files and swaps in a fresh copy; the live run's later sessions see the edited skills. Accepted: it only happens to contributors. |
| `sys.executable` is empty | Refuse: the MCP servers cannot be started. |
| `KILN_HARNESS_TUNER_HOME` set to `""` | Treated as unset. |
| Old `runs/`, `traces/`, `reports/` in a checkout | Left in place and no longer denied (§8.5); the docs say to delete or move them. |
| `--force` passed to `harness-tuner-install-agents` | argparse refuses the unknown option. |
| `harness-tuner-install-agents` without `--dest` | argparse refuses: `--dest` is required. |

## 15. Out of scope

- The Claude read-fence gaps in §8.5 (separate task).
- Migrating old checkout state.
- Renaming the Python package, the product, file names, skill folders, gateway tool names or env vars.
- A `--dry-run` flag.
- Moving run state or traces into the Kiln API or database.
- Windows support.
- Any change to how the Kiln project is read or written on disk.
- Removing `kiln_api_mcp`.
- Publishing, and README links on PyPI (launch cleanup).

## 16. Done when

- The existing suite and the new smoke test job pass in CI.
- The final summary lists every path that moved (§4.3, updated if the build changes anything).
- Steve does a full live run from an installed wheel on a clean machine. Checklist:
  1. Build the wheel (`uv build`) and install it on a machine with no clone: `uv tool install dist/harness_tuner-0.1.0-py3-none-any.whl`. Kiln is running, and the demo project is copied out of a clone.
  2. Run the quickstart from any folder. Check:
     - the start line names a run folder under `~/.kiln_ai/harness_tuner/runs/`;
     - the pre-flight passes, which proves the gateway lane;
     - at least one wave completes;
     - the report path printed at the end exists;
     - `traces/` has the run's transcripts;
     - a transcript shows a skill being read, which proves the agents folder works;
     - nothing was written to the current folder or the clone.
  3. Optional: the same run with `--agent-cli 'codex exec …'` and `--agent-cli 'opencode run …'`, whose skill loading is the most changed (§6).
  4. `uvx --from <wheel> harness-tuner --help` works.
