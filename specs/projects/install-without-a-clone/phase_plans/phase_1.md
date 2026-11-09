---
status: complete
---

# Phase 1: Ship the agent files and rename the distribution

## Overview

The agent files (skills, loop-agent instructions, the Claude MCP config) move
from the checkout's `.agents/` into the package, at
`src/kiln_harness_tuner/agent_files/`, so a wheel carries them.
`install_agents` reads them through `importlib.resources`. The distribution
becomes `harness-tuner`, and its commands drop the `kiln-` prefix. Runs still
install into the checkout and keep every other path as today; the state
folder is phase 2.

## Steps

1. `git mv .agents src/kiln_harness_tuner/agent_files`. Nothing else is
   staged with it, so at commit time it becomes a commit of its own with no
   content change. No file inside `agent_files/` is edited in this phase (the
   instructions split is phase 2).

2. `src/kiln_harness_tuner/install_agents.py`:
   - `source_dir() -> Path`: `importlib.resources.files("kiln_harness_tuner")
     / "agent_files"`. Not a `pathlib.Path`, or no `AGENTS.md` in it →
     `InstallError("the agent files are missing from the installed package
     (<root>).\nfix: reinstall harness-tuner.")`.
   - Delete `SOURCE`. `install(agent_cli, dest=REPO_ROOT, *, source: Path |
     None = None, force=False)`: `source` defaults to `source_dir()`, read at
     call time.
   - `REPO_ROOT` stays, only as the default `dest` (runs still install into
     the checkout; phase 2 removes it).
   - Delete the `skills_dest == skills_source` guard: the source is never an
     install target any more.
   - `MARKER` names `harness-tuner-install-agents` and
     `src/kiln_harness_tuner/agent_files/AGENTS.md`. `PREVIOUS_MARKERS` holds
     today's marker, so a checkout's already-installed `CLAUDE.md` or
     `AGENTS.md` is still replaced without `--force`.
   - `InstallError` docstring broadened (it now also covers missing package
     data).
   - `prog="harness-tuner-install-agents"`, the error prefix, the
     `description`, and the module docstring updated.

3. `src/kiln_harness_tuner/cli.py`:
   - `prog="harness-tuner"`; every `kiln-harness-tuner:` message prefix
     becomes `harness-tuner:` (install error, interrupt, handled error, run
     finished).
   - The run-lock messages say `another harness-tuner run`.
   - The rendered MCP commands name the renamed scripts:
     `uv run --project <repo> harness-tuner-gateway-mcp …` and
     `… harness-tuner-skill-installer-mcp …` (phase 2 replaces both with
     `python -P -m`).
   - The module docstring and the background-job comment name
     `harness-tuner`.

4. Other `prog=` and messages:
   - `gateway/server.py`: `prog="harness-tuner-gateway-mcp"`, docstring
     example.
   - `gateway/hook.py`: `prog="harness-tuner-gateway-hook"`.
   - `skill_installer/mcp_server.py`: add `prog="harness-tuner-skill-installer-mcp"`
     (it has none, and phase 2 starts it with `-m`, where argv[0] is not a
     command name).
   - `ui.py`: the plain-mode start line `harness-tuner: run started …`.
   - `setup.py`: every "re-invoke `kiln-harness-tuner`" becomes
     `harness-tuner`; the module docstring's `uv run harness-tuner …`.
   - Left alone (architecture §8): file names, skill folder names, server
     keys, env vars, and text that names the product or writes a persisted
     record (`seeded … by kiln-harness-tuner setup`).

5. `.agents/` paths in code comments and docstrings: `schemas.py`,
   `probes.py`, `memory.py`, `install_agents.py` point at
   `agent_files/`. The Codex and OpenCode dispatchers keep
   `<repo>/.agents/skills`: that is the Codex/OpenCode install layout, which
   a run still writes into the checkout.

6. `pyproject.toml`:
   - `name = "harness-tuner"`, `Repository` URL, the renamed scripts
     (`kiln_api_mcp` unchanged).
   - `[tool.hatch.build.targets.sdist] only-include = ["src",
     "pyproject.toml", "README.md"]` (no license file exists yet).
   - ruff `extend-exclude = ["runs/**",
     "src/kiln_harness_tuner/agent_files/skills/**", ".agents/skills/**",
     ".claude/skills/**"]`, with the comment updated.
   - `uv lock` regenerates `uv.lock`.

7. `.gitignore`: the install-targets comment names the new command and
   source, and `/.agents/` joins the list (a Codex or OpenCode run now copies
   the skills into `<checkout>/.agents/skills/`).

8. Doc links into `.agents/`: `docs/agents.md`, `docs/costs.md`,
   `docs/limitations.md`, `docs/your-own-task.md` point at
   `../src/kiln_harness_tuner/agent_files/skills/…`. Other doc prose is
   phase 3.

## Tests

- `test_install_agents.py`:
  - `test_the_package_ships_the_agent_files`: `source_dir()` is inside the
    imported package and holds `AGENTS.md`, `claude/.mcp.json` and at least
    one `skills/*/SKILL.md`.
  - `test_missing_agent_files_are_refused`: `files()` pointing at a folder
    without `AGENTS.md` → `InstallError` naming the path and
    `reinstall harness-tuner`.
  - `test_agent_files_not_on_disk_are_refused`: `files()` returning a
    `zipfile.Path` → `InstallError`.
  - `test_an_install_from_before_the_rename_is_replaced`: an instructions file
    with the old marker is replaced without `--force`.
  - The command test asserts the `harness-tuner-install-agents:` prefix on a
    refusal.
  - `test_skills_read_in_place_are_not_copied_onto_themselves` deleted with
    the guard.
- `test_paths.py`: the repo-root test drops the `.agents` assertions.
- `test_agent_templates.py`, `test_wrapper_templates.py`: `SKILLS_DIR` and
  `WRAPPERS` come from `install_agents.source_dir()`; the AGENTS.md port
  check reads `source_dir() / "AGENTS.md"`; the deeplink containment check is
  against the skills tree.
- `test_docs.py`: `ROOT_DOCS` names
  `src/kiln_harness_tuner/agent_files/AGENTS.md`.
- `test_cli.py`: the rendered gateway and installer commands name the
  renamed scripts; `build_parser().prog == "harness-tuner"`; a handled error
  prints `harness-tuner: …`.
- `test_ui.py`, `test_setup.py`: the renamed message text.
