---
status: complete
---

# Phase 2: State folder and agent sessions

## Overview

A run stops writing to the checkout. Its state (runs, lock, quarantine,
traces, reports) moves to one state folder, `~/.kiln_ai/harness_tuner/` or
`$KILN_HARNESS_TUNER_HOME`, and the agent files its sessions read are
installed per agent CLI and package version under `<state>/agents/`. Claude
sessions run in that folder; Codex and OpenCode sessions link to it from their
own scratch workspace. The MCP servers start as `<python> -P -m <module>`.
`cli.REPO_ROOT`, the checkout scan and the `examples/` deny go. The
instructions file splits into the short loop-agent `agent_files/AGENTS.md`
and the contributor guide at the repository root. `--dest` becomes required
and installs skills (and Claude's MCP config) only.

## Steps

1. **Instructions split** (architecture §4.1, FS §5.2).
   - `git mv src/kiln_harness_tuner/agent_files/AGENTS.md AGENTS.md`. Only
     this rename is staged, so at commit time it becomes a commit of its own.
   - New `src/kiln_harness_tuner/agent_files/AGENTS.md`: the
     `# Kiln Harness Tuner` heading and the opening paragraph only.
   - New root `CLAUDE.md`: `@AGENTS.md`.
   - Root `AGENTS.md`: Layout block names `src/kiln_harness_tuner/agent_files/`,
     the state folder and `tests/packaging/`; the fence rule names the
     `agent_files/skills/**` ruff exclude; "Findings go in" the state folder's
     `reports/`, and the "every agent loads this file" reasons point at
     `agent_files/`.

2. **`src/kiln_harness_tuner/paths.py`** (new, architecture §3):
   `STATE_HOME_ENV`, `DEFAULT_STATE_HOME`, `StateHomeError(RuntimeError)`,
   `StatePaths(root)` with `runs`, `traces`, `reports`, `agents`,
   `quarantine(project_id, task_id)`;
   `state_home(environ=None, cwd=None) -> Path`;
   `prepare_state(root) -> StatePaths` (mode 0o700 on create, the four
   subfolders, a write probe; `OSError` → `StateHomeError` with
   `fix: set KILN_HARNESS_TUNER_HOME to a writable folder.`);
   `check_no_overlap(state, project_root)`.

3. **`src/kiln_harness_tuner/install_agents.py`** (architecture §4.2–4.5):
   - Delete `REPO_ROOT`, `MARKER`, `PREVIOUS_MARKERS`, `install`, `--force`.
   - `DIST_NAME = "harness-tuner"`; `package_version()` (metadata, else
     `__version__`).
   - `expected_files(cli) -> dict[str, Path]`, `matches(target, expected)`,
     `build(tmp, expected)`, `install_for_run(cli, agents_root, version) ->
     Path` with the tmp-build-and-rename algorithm. Every file walk skips
     `__pycache__` (the test suite and a wheel install compile the wrapper
     templates in place).
   - `install_into(cli, dest) -> list[Path]`: skills (copytree ignoring
     `__pycache__`) plus, for `claude`, the MCP merge. No instructions file.
   - `main`: `--dest` required, no `--force`; prints
     `claude: skills in <dest>/.claude/skills, kiln_api_mcp in <dest>/.mcp.json`
     or `<cli>: skills in <dest>/.agents/skills`.

4. **`src/kiln_harness_tuner/cli.py`** (architecture §5):
   - Delete `REPO_ROOT`, `kiln_project_dirs`, `example_denials`.
   - `make_dispatcher(agent_cli, target, agents_dir, **kwargs)`,
     `check_agent_cli(agent_cli, target, agents_dir, models, efforts)`.
   - `python_command()`, `GATEWAY_MODULE`, `INSTALLER_MODULE`; both servers
     rendered as `[sys.executable] + ["-P", "-m", module, …]`.
   - `read_fence_for(*, state, project_root, run_dir, agents_dir, home)`.
   - `render_run_configs(run_dir, target, *, state, agents_dir, model_policy,
     max_concurrent_evals)`: `Edit(//<agents_dir>/**)` deny for every role,
     the read-only roles' first Edit deny on `state.root`,
     `denyWrite == [state.root]`.
   - `build_ctx(..., state, agents_dir)`: traces under `state.traces`,
     quarantine `state.quarantine(project, task)`.
   - `_run` order per §5.1; the start line
     `harness-tuner: run <id> — files in <run_dir>/` on stdout before logging
     and the UI; `_RUN_OUTPUT` (run folder, report path).
   - `main`: clears `_RUN_OUTPUT`; `StateHomeError` joins the handled errors;
     handled errors and interrupts add `harness-tuner: run folder: <dir>`
     (and `harness-tuner: report: <path>` for an existing report after a
     handled error) on stderr; a clean finish prints
     `harness-tuner: run finished — report: <path>`.

5. **Dispatchers** (architecture §6):
   - `claude_cli.py`: second argument `agents_dir`; `_cwd` and
     `_transcript_dir` use it; pre-flight fix text says
     `uv tool install harness-tuner`; docstrings.
   - `codex_cli.py`: `stage_workspace(workspace, skills_dir, instructions)`;
     skills and instructions from `agents_dir`; `_mcp_env` docstring.
   - `opencode_cli.py`: same attributes; `_workspace` calls
     `stage_workspace`; docstring and MCP timeout comment.

6. **`gateway/hook.py`**: `hook_command` adds `-P`.

7. **`.gitignore`**: drop `/CLAUDE.md` and `/AGENTS.md`; keep `/.agents/`,
   `/.claude/skills/`, `/.claude/settings.json`, `/.mcp.json`, `/runs/`,
   `/traces/`; add `/reports/`; rewrite the run-output and install-target
   comments.

## Tests

- `tests/conftest.py`: autouse `KILN_HARNESS_TUNER_HOME = tmp_path / "state"`;
  the `install_agents` patch goes (runs install into the per-test state).
- `tests/test_state_paths.py` (new):
  - `state_home`: unset → `~/.kiln_ai/harness_tuner`; empty → unset; `~`
    expanded; relative joined to `cwd`; result resolved.
  - `prepare_state`: creates root with mode 0700 and the four subfolders;
    an existing root's mode is kept; an unwritable parent or a file in the
    way → `StateHomeError` naming the path with the fix line.
  - `check_no_overlap`: project inside state, state inside project, equal →
    refused; disjoint → passes.
  - `StatePaths.quarantine` naming.
- `tests/test_install_agents.py` (rewritten):
  - the package ships `AGENTS.md`, `claude/.mcp.json`, skills; missing
    `AGENTS.md` and a zip import refused.
  - `package_version` equals the installed metadata, and falls back to
    `__version__`.
  - `install_into` per CLI layout, no instructions file; reinstall replaces a
    skill and keeps others; Claude MCP merge; codex/opencode no MCP config;
    non-JSON config refused; `__pycache__` not copied.
  - `install_for_run`: creates `<root>/<cli>/<version>/` with the layout and
    `CLAUDE.md`/`AGENTS.md` equal to the source; no `.mcp.json`; reuses a
    matching folder (mtime unchanged); rebuilds on an extra, a changed and a
    missing file; ignores `__pycache__` on both sides; survives a concurrent
    placement (patched `os.rename` that places the target first); a build
    failure leaves no temp folder and raises `InstallError`.
  - `main`: `--dest` required; `--force` refused; prints one line per CLI;
    refusal prefix; name or whole command accepted; unknown CLI refused.
- `tests/test_cli.py`:
  - render tests on the new signature: gateway and installer
    `command == sys.executable`, `args` start `["-P", "-m", <module>]`;
    `Edit(//<agents_dir>/**)` in every writable role's deny; read-only first
    deny on `state.root`; `denyWrite == [state.root]`; read denies on
    `state.runs/traces/reports`; allows on the agents folder's skills and
    `CLAUDE.md`; an empty `sys.executable` refused.
  - `build_ctx`: quarantine under `state.runs/.quarantine`, traces root.
  - `main` end to end in the per-test state: run folder, report dir, agents
    folder used as the Claude cwd, start line, `run finished — report:`,
    handled error prints the run folder and an existing report, interrupt
    prints the run folder, a state error and an overlap refused before any
    run folder.
  - `kiln_project_dirs` and `example_denials` tests deleted; the
    model-policy example path is `tests/`-relative.
- `tests/test_paths.py`: no module walks out of the package
  (`ROOT_DERIVING_MODULES` empty), `REPO_ROOT` test dropped.
- `tests/test_dispatch_claude_cli.py`: `cwd == agents_dir`; transcript slug
  from the agents folder; render calls updated.
- `tests/test_dispatch_codex_cli.py`: link to `agents_dir/.agents/skills`,
  `AGENTS.md` from `agents_dir`; gateway command is `sys.executable`.
- `tests/test_dispatch_opencode_cli.py`: workspace link and `AGENTS.md`
  copy; read rules with no git root; command array starts
  `[sys.executable, "-P", "-m", …]`.
- `tests/test_agent_templates.py`: the `:8757` check covers both
  instructions files. `tests/test_docs.py`: root `AGENTS.md` and `CLAUDE.md`
  in `ROOT_DOCS`. `tests/test_agent_cli.py`: new `make_dispatcher` /
  `check_agent_cli` signatures. `tests/test_gateway.py` (or wherever the
  hook command is tested): `-P`.
