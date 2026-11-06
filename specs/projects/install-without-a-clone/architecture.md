---
status: draft
---

# Architecture: Install without a clone

Read with [functional_spec.md](functional_spec.md). Section numbers like "FS §5.3" point there.

## 1. Overview

Today one constant, `cli.REPO_ROOT = Path(__file__).resolve().parents[2]` (and its twin in `install_agents.py`), is the root of everything: agent files, run state, the agents' working folder, the gateway launch, and the fences. The change replaces it with two roots that never depend on where the code sits:

- **The package** (`importlib.resources.files("kiln_harness_tuner")`): agent files and role templates.
- **The state folder** (`paths.StatePaths`): runs, lock, quarantine, traces, reports, installed agent files.

`cli._run` resolves both once and passes them down. No other module derives a path from `__file__` beyond the package's own directory, and `tests/test_paths.py` enforces that.

## 2. Modules

| Module | Change |
|---|---|
| `paths.py` | **New.** The state folder: location, creation, layout. |
| `install_agents.py` | **Rewritten.** The package-data source, the run install (`install_for_run`), the `--dest` install (`install_into`), the command. |
| `cli.py` | `REPO_ROOT` removed. `_run` wiring, `render_run_configs` and `read_fence_for` signatures, the MCP server commands, start and exit lines. `kiln_project_dirs` and `example_denials` deleted. |
| `dispatch/claude_cli.py` | Constructor's second argument becomes `agents_dir`. `_cwd` and `_transcript_dir` use it. Pre-flight fix text. |
| `dispatch/codex_cli.py` | Skills and instructions come from `agents_dir`. |
| `dispatch/opencode_cli.py` | Skills and instructions come from `agents_dir`. The workspace gets the link and the `AGENTS.md` copy. |
| `gateway/hook.py` | `hook_command` adds `-P`. `prog` renamed. |
| `gateway/server.py`, `skill_installer/mcp_server.py` | `prog` renamed only. |
| `report.py` | `ReportWriter.path` made public (it exists as a private property). |
| `pyproject.toml`, `uv.lock`, `.gitignore`, `.github/workflows/ci.yml` | Packaging, names, sdist, ruff exclude, ignore rules, the smoke job. |
| `tests/packaging/` | **New.** `smoke_installed_wheel.py` and `fake_claude.py`. |

## 3. `paths.py`

```
STATE_HOME_ENV = "KILN_HARNESS_TUNER_HOME"
DEFAULT_STATE_HOME = "~/.kiln_ai/harness_tuner"

class StateHomeError(RuntimeError):
    """The state folder cannot be used. A start-up error with a fix line."""

@dataclass(frozen=True)
class StatePaths:
    root: Path                       # absolute, resolved

    @property
    def runs(self) -> Path:          # root / "runs"
    @property
    def traces(self) -> Path:        # root / "traces"
    @property
    def reports(self) -> Path:       # root / "reports"
    @property
    def agents(self) -> Path:        # root / "agents"
    def quarantine(self, project_id: str, task_id: str) -> Path:
        # runs / ".quarantine" / f"{project_id}-{task_id}"  (today's naming)

def state_home(environ: Mapping[str, str] | None = None, cwd: Path | None = None) -> Path:
    """The state folder's path: $KILN_HARNESS_TUNER_HOME (empty = unset), else
    DEFAULT_STATE_HOME. `~` expanded, a relative path joined to `cwd`
    (default Path.cwd()), then resolved. Does not touch the disk."""

def prepare_state(root: Path) -> StatePaths:
    """Create the folder if missing (mode 0o700, parents with the default mode;
    an existing folder's mode is left alone), create runs/, traces/, reports/,
    agents/ inside it, and prove it is writable by creating and removing a
    temporary file. Any OSError -> StateHomeError naming the path and the
    error, with `fix: set KILN_HARNESS_TUNER_HOME to a writable folder`."""

def check_no_overlap(state: StatePaths, project_root: Path) -> None:
    """StateHomeError when either resolved path is inside the other (or they
    are equal): the project is read-denied whole and scratch must be
    writable, so the fences would contradict each other.
    fix: set KILN_HARNESS_TUNER_HOME to a folder outside the Kiln project."""
```

`mkdir(mode=0o700)` is subject to the umask. That is fine: a umask only removes bits.

## 4. Agent files

### 4.1 Source tree

Steps, in this order, so history follows the bigger file:

1. `git mv .agents/AGENTS.md AGENTS.md`. The contributor guide keeps its history.
2. `git mv .agents src/kiln_harness_tuner/agent_files`. A pure rename, committed with step 1 and nothing else.
3. Write `src/kiln_harness_tuner/agent_files/AGENTS.md` (new): today's `# Kiln Harness Tuner` heading and opening paragraph only (FS §5.2).
4. Write the root `CLAUDE.md`: one line, `@AGENTS.md`.
5. Edit the root `AGENTS.md`:
   - its Layout block gets the new paths (`src/kiln_harness_tuner/agent_files/` in place of `.agents/`, the state folder, `tests/packaging/`);
   - the "Fence pseudo-code" rule points at the new ruff exclude;
   - the "Findings go in `reports/`" rule says the state folder's `reports/`.

Resulting package tree:

```
src/kiln_harness_tuner/
  agents/*.md            role templates (unchanged, already shipped)
  agent_files/
    AGENTS.md            loop-agent instructions
    skills/<name>/...    every skill (unchanged content)
    claude/.mcp.json     the interactive kiln_api_mcp server (for --dest)
    claude/settings.json its enable switch (for --dest)
```

The wrapper templates under `agent_files/skills/kiln-harness-tuner-code-tool-authoring/references/wrappers/*.py` now sit inside the package. They have no `__init__.py`, are never imported, and ship as data. That is harmless.

### 4.2 Reading the package data

In `install_agents.py`:

```
DIST_NAME = "harness-tuner"

def source_dir() -> Path:
    """The packaged agent files on disk. `files(...)` returns a pathlib.Path
    for a package installed on disk (wheel or editable), which is every
    install uv makes. Anything else (a zip import) -> InstallError: the agent
    CLIs need real folders to read."""
    root = importlib.resources.files("kiln_harness_tuner") / "agent_files"
    if not isinstance(root, Path) or not (root / "AGENTS.md").is_file():
        raise InstallError(f"the agent files are missing from the installed package ({root}).\n"
                           "fix: reinstall harness-tuner.")
    return root

def package_version() -> str:
    """importlib.metadata.version(DIST_NAME); on PackageNotFoundError,
    kiln_harness_tuner.__version__."""
```

`importlib.resources.as_file` is not used: it gained directory support only in Python 3.12, and the floor is 3.11.

### 4.3 Layouts

```
@dataclass(frozen=True)
class Layout:
    skills: str          # relative folder for skills
    instructions: str    # relative file name for instructions

LAYOUTS = {   # `HARNESSES` today; `AGENT_CLIS` if the rename branch merged
    "claude":   Layout(".claude/skills", "CLAUDE.md"),
    "codex":    Layout(".agents/skills", "AGENTS.md"),
    "opencode": Layout(".agents/skills", "AGENTS.md"),
}
```

`MARKER`, `--force` and the "file not written by this command" check are deleted (FS §9).

### 4.4 `install_for_run(cli, agents_root, version) -> Path`

Called by `cli._run` with `state.agents` and `package_version()`. Returns `agents_root / cli / version`, the folder sessions read.

```
def expected_files(cli) -> dict[str, Path]:
    """Relative path in the installed folder -> source file:
    every file under source_dir()/skills/ mapped under layout.skills, plus
    layout.instructions -> source_dir()/AGENTS.md. Nothing else."""

def matches(target: Path, expected: dict[str, Path]) -> bool:
    """True when target holds exactly the expected relative files (no extra,
    none missing, compared over files; directories ignored) and each one's
    bytes equal its source (filecmp.cmp(shallow=False))."""

def build(tmp: Path, expected) -> None:
    """Copy every expected file into tmp (parents created). shutil.copyfile,
    not copytree: the layout renames paths."""
```

The algorithm:

1. `target = agents_root / cli / version`. If `matches(target, expected)`, return `target`.
2. `tmp = target.parent / f".tmp-{version}-{os.getpid()}-{token_hex(4)}"`; `build(tmp)`. On OSError: `rmtree(tmp, ignore_errors=True)` and raise InstallError with the OS error.
3. If `target` does not exist: `os.rename(tmp, target)`. On `OSError` (another run placed it first): `rmtree(tmp)`. Then go to step 5.
4. If `target` exists (it differs):
   - `old = target.parent / f".old-{version}-{pid}-{hex}"`;
   - `os.rename(target, old)`, then `os.rename(tmp, target)`, then `rmtree(old, ignore_errors=True)`;
   - an `OSError` on either rename (a concurrent run swapped first): `rmtree(tmp)`.
5. If `matches(target, expected)`, return `target`. Otherwise raise InstallError ("the agents folder {target} does not hold the package's files after install").

Step 4 leaves `target` missing for the time between two renames. It only runs when a same-version folder differs, which happens to contributors and after a hand edit, never to a released install (FS §14). No pruning (FS §5.3).

The folder gets skills and instructions only. `claude/.mcp.json` and `claude/settings.json` are not copied.

### 4.5 `install_into(cli, dest) -> list[Path]` and the command

`install_into` is today's `install` minus the instructions file:

- copy each `source_dir()/skills/<name>` to `dest/<layout.skills>/<name>`, replacing a same-name folder (or file or link) and leaving others alone;
- for `claude`, `_install_claude_mcp(source_dir(), dest)` as today.

It returns what was written. The `skills_dest == skills_source` guard goes: the source is never a `--dest` target any more.

`main(argv)`:
- `prog="harness-tuner-install-agents"`;
- `--harness {claude,codex,opencode,all}` (or `--agent-cli` after the rename branch);
- `--dest PATH`, now `required=True`;
- no `--force`.

It prints one line per CLI: `claude: skills in <dest>/.claude/skills, kiln_api_mcp in <dest>/.mcp.json`; for codex and opencode, `<cli>: skills in <dest>/.agents/skills`. An InstallError prints `harness-tuner-install-agents: <msg>` and exits 1.

## 5. `cli.py`

### 5.1 `_run` order

```
agent_cli = parse_agent_cli(...)
state = paths.prepare_state(paths.state_home())                # StateHomeError
project_root = Path(args.project_path).expanduser().resolve()
paths.check_no_overlap(state, project_root)                    # StateHomeError
agents_dir = install_for_run(agent_cli.kind, state.agents, package_version())  # InstallError -> SystemExit, as today
target, models, efforts = ...
await check_agent_cli(agent_cli, target, agents_dir, models, efforts)
policy = load_model_policy(...)
lock = _acquire_run_lock(state.runs, ...)
run_id = _unique_run_id(state.runs, now); run_dir = state.runs / run_id
_RUN_OUTPUT.run_dir = run_dir
_RUN_OUTPUT.report_path = state.reports / f"kiln-harness-tuner-{run_id}.md"
print(f"harness-tuner: run {run_id} — files in {run_dir}/", flush=True)   # before the UI
configure_run_logging(run_dir)
...
mcp_config_path, settings_path = render_run_configs(
    run_dir, target, state=state, agents_dir=agents_dir,
    model_policy=policy_copy, max_concurrent_evals=...)
...
ctx = build_ctx(..., state=state, agents_dir=agents_dir, ...)
report_writer = ReportWriter(tracker, run_id, state.reports)
```

- `prepare_state` runs before the agent CLI check. A state folder that can't be used fails fast and leaves nothing behind (FS §14).
- `make_dispatcher(agent_cli, target, agents_dir, **kwargs)` passes `agents_dir` as the dispatcher's second positional argument, where `REPO_ROOT` went. `check_agent_cli` takes `agents_dir` and passes it on.
- `build_ctx` gains the keyword-only `state: StatePaths` and `agents_dir: Path`. It sets `traces_root=state.traces` and `quarantine_root=state.quarantine(target.project_id, target.task_id)`.
- `_RUN_OUTPUT` is a module-level holder with two optional `Path` fields, cleared at the top of `main`, like `_stopped_by`.

### 5.2 `main` output

- **Clean finish:** `harness-tuner: run finished — report: {result}`.
- **Handled error** (the existing `except` tuple, plus `StateHomeError`): print the error line as today. If `_RUN_OUTPUT.run_dir` is set, also print `harness-tuner: run folder: {run_dir}` (its log is `kiln-harness-tuner.log`). If `_RUN_OUTPUT.report_path` exists on disk, also print `harness-tuner: report: {path}`. These go to stderr.
- **Interrupt:** the existing line, plus the run-folder line if set.
- **Prefixes:** every message prefix `kiln-harness-tuner:` becomes `harness-tuner:`, and `build_parser`'s `prog` becomes `harness-tuner`.

### 5.3 MCP server commands

```
def python_command() -> str:
    """sys.executable, refused when empty or not absolute (ConfigRenderError,
    fix: run harness-tuner from its installed command)."""

GATEWAY_MODULE = "kiln_harness_tuner.gateway.server"
INSTALLER_MODULE = "kiln_harness_tuner.skill_installer.mcp_server"

gateway_server(role) = {"type": "stdio", "command": python_command(),
                        "args": ["-P", "-m", GATEWAY_MODULE, "--role", role, ...same args as today...]}
installer = {"type": "stdio", "command": python_command(),
             "args": ["-P", "-m", INSTALLER_MODULE, "--project-path", ..., "--scratch-root", ...]}
```

**Why `-P`.** `python -m` puts the current folder first on `sys.path`. Codex and OpenCode start MCP servers in the session's workspace, which the agent can write. A planted `json.py` or `kiln_harness_tuner/` there would then run inside the gateway, outside every fence. `-P` (Python 3.11+, the floor) turns that off. A console script has no such entry, which is why the risk is new with `-m`. `gateway/hook.py`'s `hook_command` gets `-P` too.

The `uv run --project` form and its `repo_root` argument are gone. `codex_cli.MCP_ENV_PREFIXES` stays: `PYTHON*` still matters, and `UV_*` and `VIRTUAL_ENV` are harmless.

### 5.4 Fences: `read_fence_for` and `render_run_configs`

```
def read_fence_for(*, state: StatePaths, project_root: Path, run_dir: Path,
                   agents_dir: Path, home: Path) -> ReadFence:
    deny  = [project_root, state.runs, state.traces, state.reports,
             home / ".claude" / "projects"]
    allow = [run_dir / "scratch", agents_dir / ".claude" / "skills",
             agents_dir / "CLAUDE.md"]
```

The `task_id` parameter (already unused) and `repo_root` go. `kiln_project_dirs`, `example_denials` and their tests are deleted (FS §8.3). `ReadFence.rule_paths` is unchanged: it already expands `state.runs` around this run's scratch.

Changes in `render_run_configs(run_dir, target, *, state, agents_dir, model_policy=None, max_concurrent_evals=None)`:

| Setting | Today | After |
|---|---|---|
| `permissions.deny` (writable roles) | project tree, trace folder, web tools, `mcp__kiln_api_mcp`, read denies | same, plus `Edit(//<agents_dir>/**)` |
| read-only roles' first Edit deny | `Edit(//<repo_root>/**)` | `Edit(//<state.root>/**)` |
| `sandbox.filesystem.denyWrite` | `[repo_root]` | `[state.root]` |
| `sandbox.filesystem.allowWrite` | `[scratch]` | unchanged |
| `denyRead` / `allowRead` | from `read_fence_for` | unchanged |

`agents_dir` is the installed folder of the run's own agent CLI. For a Codex or OpenCode run the Claude allows name files that don't exist, and the Claude settings files go unused. That's harmless, and it's the same as today.

## 6. Dispatchers

### 6.1 `ClaudeCliDispatcher`

- `__init__(self, target, agents_dir: Path, *, ...)`: the second positional argument is renamed from `repo_root`, stored as `self._agents_dir`.
- `_cwd()` returns `self._agents_dir`.
- `_transcript_dir()` returns `claude_projects_dir / _project_slug(str(self._agents_dir))`.
- `cli.py` passes the resolved path, so the cwd string and the slug agree.
- The pre-flight `PreflightError` fix text replaces "install the tools with `uv tool install .` from this repository's root" with "reinstall with `uv tool install harness-tuner`".
- The module docstring's "cwd = the repository root" is updated.

### 6.2 `CodexCliDispatcher`

- `self._skills_dir = skills_dir or agents_dir / ".agents" / "skills"`
- `self._instructions = agents_dir / "AGENTS.md"`

`_workspace` is unchanged: it links and copies from those two.

### 6.3 `OpenCodeCliDispatcher`

- Same two attributes as Codex.
- `_workspace(label)` does what Codex's does: create the folder, link `.agents/skills` to `self._skills_dir.resolve()` if absent, and copy `self._instructions` to `AGENTS.md`. Factor the link-and-copy into one helper in `codex_cli.py` (`stage_workspace(workspace, skills_dir, instructions)`) that both call.
- The read rules are unchanged: `path_patterns` already allows `skills_dir` resolved, and the link sits inside the workspace's own patterns. With no git root, `_worktree` returns None and the patterns are absolute-from-`/`. That's already supported.
- Module docstring: "finds `.agents/skills` and `AGENTS.md` under the worktree root" becomes "in its workspace".

**Why it works**, from OpenCode's source (`packages/core/src/config/discovery.ts`, `config/plugin/skill-scan.ts`, `config/plugin/instruction.ts`, read at commit `d86331c`):
- Config discovery walks up from the session folder with no stop and collects every `.agents` folder; its `skills/` becomes a skill source.
- The scan follows symlinked entries (`fs.stat` on a `SymbolicLink`).
- `AGENTS.md` is found by walking up from the session folder to the home folder when there is no git root.

The installed OpenCode may be older than that source, so the live run (FS §16, step 3) confirms it.

## 7. Packaging

`pyproject.toml`:

```
[project]
name = "harness-tuner"
...
[project.urls]
Homepage = "https://kiln.tech"
Repository = "https://github.com/Kiln-AI/harness-tuner"

[project.scripts]
harness-tuner = "kiln_harness_tuner.cli:main"
harness-tuner-skill-installer-mcp = "kiln_harness_tuner.skill_installer.mcp_server:main"
harness-tuner-gateway-mcp = "kiln_harness_tuner.gateway.server:main"
harness-tuner-install-agents = "kiln_harness_tuner.install_agents:main"
kiln_api_mcp = "kiln_api_mcp.server:main"

[tool.hatch.build.targets.wheel]
packages = ["src/kiln_harness_tuner", "src/kiln_api_mcp"]   # unchanged; agent_files rides along

[tool.hatch.build.targets.sdist]
only-include = ["src", "pyproject.toml", "README.md"]       # + the license file when the launch cleanup adds it

[tool.ruff]
extend-exclude = ["runs/**", "src/kiln_harness_tuner/agent_files/skills/**",
                  ".agents/skills/**", ".claude/skills/**"]
```

- **`runs/**`** stays, for old checkout state.
- **`.agents/skills/**` and `.claude/skills/**`** are now only `--dest .` install targets in a contributor's checkout.
- **`uv lock`** regenerates `uv.lock` for the new name. CI's `uv sync --locked` checks it.
- **hatchling** includes every non-ignored file under a listed package, which is how `agents/*.md` ships today. `.gitignore`'s patterns are root-anchored for the installed files, so nothing under `agent_files/` matches them. The smoke test asserts the wheel contents (§9).

`.gitignore`:
- remove `/CLAUDE.md` and `/AGENTS.md` (now tracked) and add `/.agents/` (a `--dest .` target);
- keep `/.claude/skills/`, `/.claude/settings.json`, `/.mcp.json`, `/runs/`, `/traces/`;
- add `/reports/` for old checkout state, and rewrite the comment that says `reports/` is deliberately not ignored.

## 8. Names in code and text

| Kind | Rule |
|---|---|
| `prog=` and message prefixes | `harness-tuner`, `harness-tuner-install-agents`, `harness-tuner-gateway-mcp`, `harness-tuner-skill-installer-mcp`, `harness-tuner-gateway-hook` |
| Text that tells the user to run a command | the new command |
| Text that names the product | "Kiln Harness Tuner" (unchanged) |
| File names (`kiln-harness-tuner.log`, `kiln-harness-tuner-<run_id>.md`), skill folders, `kiln_harness_tuner_gateway` server key and tool names, `KILN_HARNESS_TUNER_*` env vars | unchanged |

The coding agent finds the call sites with `rg 'kiln-harness-tuner' src tests docs README.md .github examples` and sorts each hit by this table.

## 9. CI smoke test

### 9.1 Workflow

A new job, `package`, in `.github/workflows/ci.yml`: same triggers, `ubuntu-latest`, matrix `["3.11", "3.13"]`, `astral-sh/setup-uv@v5` with the cache on.

```
- run: uv build                      # sdist, then the wheel from it
- run: uvx twine check dist/*
- run: python tests/packaging/smoke_installed_wheel.py --dist dist
```

The smoke script is stdlib-only and runs with the runner's interpreter. It never imports `kiln_harness_tuner`.

### 9.2 `tests/packaging/smoke_installed_wheel.py`

Each check raises `SystemExit` with a message naming what failed. It prints one `ok:` line per passed check.

1. **sdist:** the top-level entries are a subset of `{src, pyproject.toml, README.md, PKG-INFO}` plus `LICENSE*`. `src/kiln_harness_tuner/agent_files/AGENTS.md` is present.
2. **wheel:** it contains:
   - `kiln_harness_tuner/agent_files/AGENTS.md`;
   - `kiln_harness_tuner/agent_files/skills/kiln-api/SKILL.md`;
   - `kiln_harness_tuner/agent_files/claude/.mcp.json`;
   - every `kiln_harness_tuner/agents/<role>.md`.

   It contains no `tests/` and no `examples/`.
3. **install:** in one `tempfile.TemporaryDirectory()`, run `uv tool install <wheel>` with:
   - `UV_TOOL_DIR=<tmp>/tools` and `UV_TOOL_BIN_DIR=<tmp>/bin`;
   - `HOME=<tmp>/home`;
   - `UV_CACHE_DIR` kept from the outer environment, so dependencies come from the runner's cache.

   The tool's interpreter is `<tmp>/tools/harness-tuner/bin/python`.
4. **commands:** from `<tmp>/cwd` (an empty folder):
   - `harness-tuner --help` and `harness-tuner-install-agents --help` exit 0;
   - `uvx --from <wheel> harness-tuner --help` exits 0;
   - `harness-tuner-install-agents --harness claude --dest <tmp>/dest` exits 0, writes `.claude/skills/kiln-api/SKILL.md` and `.mcp.json`, and writes no `CLAUDE.md`.
5. **dry pre-flight**, run twice:
   - **(a)** with `KILN_HARNESS_TUNER_HOME=<tmp>/state`;
   - **(b)** without it, with `HOME=<tmp>/home2`.

   Each run:
   - **Fake `claude`:** write `<tmp>/fakebin/claude`. Its first line is `#!<tool python>`, and the rest is a copy of `tests/packaging/fake_claude.py`. Mark it executable.
   - **Project:** copy `examples/moderation/Forum Moderation` to `<tmp>/project/Forum Moderation`.
   - **Command:** `harness-tuner --project-path <copy> --project-id 424242975950 --task-id 424242783061 --kiln-host http://127.0.0.1:9 --no-ui`.
     - The cwd is `<tmp>/cwd`.
     - `PATH` is `<tmp>/fakebin:<tmp>/bin:$PATH`.
     - `FAKE_CLAUDE_LOG` is `<tmp>/fake.jsonl`.
     - There's a timeout of 600 s.

   It checks:
   - the exit code is 1, and stderr contains `preflight failed` and `harness-tuner: run folder:`;
   - `<state>/agents/claude/<version>/.claude/skills/kiln-api/SKILL.md` exists, `CLAUDE.md` there equals the wheel's `agent_files/AGENTS.md`, and there is no `.mcp.json` there. `<version>` is read from the wheel's file name;
   - every line of the fake log has `cwd` equal to that folder (resolved). There are at least two lines (the CLI check and the pre-flight);
   - exactly one `<state>/runs/<run_id>/` exists, holding `settings.json`, `settings.readonly.json`, `mcp_config.json` and a non-empty `train_set.json`, and `<state>/runs/.lock-424242975950-424242783061` exists;
   - in `mcp_config.json`, the gateway's `command` is the tool interpreter, its `args` start with `["-P", "-m", "kiln_harness_tuner.gateway.server"]`, and `settings.json`'s `sandbox.filesystem.denyWrite` is `[<state>]`;
   - the fake log's pre-flight line lists the gateway's tools, including `ping`;
   - `<tmp>/cwd` is still empty. With the override, `<tmp>/home/.kiln_ai/harness_tuner` does not exist. Kiln itself may create other files under `~/.kiln_ai`;
   - `git status --porcelain` in the checkout is the same before and after.

   `<state>` is `<tmp>/state` in (a) and `<tmp>/home2/.kiln_ai/harness_tuner` in (b).

### 9.3 `tests/packaging/fake_claude.py`

Run with the tool's interpreter, so `mcp` is importable. It does the following:

1. Parse `--mcp-config` and `--session-id` from `sys.argv`, ignoring every other flag, and read the prompt from stdin.
2. Append `{"cwd": os.getcwd(), "argv": sys.argv[1:], "prompt": prompt[:200]}` to `$FAKE_CLAUDE_LOG`, adding `"tools"` and `"ping"` when step 4 runs.
3. If the prompt contains `KILN-HARNESS-TUNER-READY` (the CLI check, `CLI_CHECK_WORD`): print one JSON object, `{"type": "result", "subtype": "success", "is_error": false, "result": "KILN-HARNESS-TUNER-READY", "session_id": <id or a uuid4>, "total_cost_usd": 0, "usage": {"input_tokens": 0, "output_tokens": 0}}`, and exit 0.
4. Else, if `--mcp-config` was given:
   - take its `kiln_harness_tuner_gateway` server;
   - start it with `mcp.client.stdio.stdio_client(StdioServerParameters(command, args, env=dict(os.environ)))`;
   - `initialize`, `list_tools`, then `call_tool("ping", {})`, and record the tool names and the ping's text;
   - print the same JSON shape with `result` set to a fenced json block `{"status": "ok", "data": {"ping": <ping text>}, "detail": null}`;
   - exit 0.

   With no Kiln server, the ping text is the gateway's `{"error": …}`, so the tuner's `reply_text()` refuses it and raises `PreflightError` ("preflight failed …").
5. Else: print a result with `"is_error": true` and exit 1.

The whole start-up runs for real: install, CLI check, lock, run folder, fence rendering, split check and the train-set write. That's because `run_quarantine` runs before the pre-flight and reads only the disk, and the gateway contacts Kiln only inside an operation, not at start (`gateway/server.py` `main`). The pre-flight's MCP lane runs for real too. Only the Kiln server and the model are missing.

## 10. Tests

`tests/conftest.py` gets an autouse fixture that sets `KILN_HARNESS_TUNER_HOME` to a per-test `tmp_path / "state"`, so no test touches the real home. The `_redirect_roots` helper in `test_cli.py`, which monkeypatched `REPO_ROOT`, goes.

| File | Change |
|---|---|
| `test_paths.py` | `ROOT_DERIVING_MODULES` becomes empty: assert no module's `__file__` walk leaves the package. Drop the `cli.REPO_ROOT` import and the agree-on-root test. |
| `test_install_agents.py` | Rewrite for `source_dir`, `install_into`, `install_for_run` and `main`. Cases: the package source exists and holds `AGENTS.md` and skills; each CLI's layout under `--dest`, with no instructions file; the Claude MCP merge kept; other skills left alone; `--dest` required; `--force` refused; `install_for_run` creates `<root>/<cli>/<version>/`, reuses a matching folder (unchanged mtime), rebuilds a differing one (an extra file, a changed file, a missing file), and survives a concurrent placement (simulate with a `os.rename` patch that creates the target first); a missing source raises InstallError. |
| `test_cli.py` | New `render_run_configs` and `read_fence_for` signatures. The gateway and installer commands are `sys.executable` with `-P -m`. The Edit deny on the agents folder for every writable role. The read-only Edit deny on the state root. `denyWrite == [state.root]`. Quarantine under `state.runs/.quarantine`. Delete the `kiln_project_dirs` and `example_denials` tests. New: `state_home` (unset, empty, `~`, relative), `prepare_state` (0700 on create, unwritable gives `StateHomeError`), `check_no_overlap` (both directions), the start line, a handled error printing the run folder and an existing fallback report, and `harness-tuner:` prefixes. Keep the `examples/model-policy.json` test, using a `tests/`-relative repo root. |
| `test_dispatch_claude_cli.py` | `cwd == agents_dir`; transcript slug from the agents folder; `render_run_configs` calls updated. |
| `test_dispatch_codex_cli.py` | Link target is `agents_dir/.agents/skills`; instructions copied from `agents_dir`; the gateway command assertion is `sys.executable`, not `uv`. |
| `test_dispatch_opencode_cli.py` | The workspace gets the link and the `AGENTS.md` copy; read rules with no git root; the gateway command array starts `[sys.executable, "-P", "-m", …]`. |
| `test_agent_templates.py`, `test_wrapper_templates.py`, `test_memory.py` | `SKILLS_DIR` and similar point at `src/kiln_harness_tuner/agent_files/`. The `:8757` check covers both instructions files. The "link resolves inside the repo" check becomes "inside `agent_files/`". |
| `test_docs.py` | Root `AGENTS.md` and `CLAUDE.md` join the link-checked documents if `ROOT_DOCS` lists root files. Otherwise unchanged; the doc edits keep it green. |
| `test_dispatch_boundary.py` | Update any `repo_root` naming the rename touches. |

The suite stays offline. `tests/packaging/` holds no `test_*.py`, so pytest does not collect it.

## 11. Docs

FS §11 lists the edits. Two mechanical sweeps find the call sites:
- `rg -n '\.agents/|runs/|traces/|reports/|uv run --project|uv tool install \.|kiln-harness-tuner' README.md docs .github examples src/kiln_harness_tuner/agent_files/AGENTS.md AGENTS.md`;
- the read-fence description in `docs/limitations.md` ("The read fence is a path fence"), which loses the `examples/` and sibling-project sentences and gains the clone gap.

## 12. Error handling

| Error | Raised by | Reaches the user as |
|---|---|---|
| `StateHomeError` | `paths.prepare_state`, `paths.check_no_overlap` | `main`'s handled-error path: one line plus `fix:`, exit 1 |
| `InstallError` | `install_agents.source_dir`, `install_for_run` | `_run`'s existing `SystemExit(f"harness-tuner: {exc}")` |
| `ConfigRenderError` | `python_command` | handled-error path |
| `PreflightError` and the rest | unchanged | unchanged, plus the run-folder and report lines (§5.2) |

All new failures happen before the lock and the run folder, except `python_command`, which runs inside `render_run_configs` after the run folder exists. That's acceptable: an empty `sys.executable` is practically impossible.

## 13. Risks

- **OpenCode version drift (§6.3).** The source read is current; an older installed OpenCode may discover skills differently. Covered by the live run. The fallback, if needed, is OpenCode's `skills` config key (a list of absolute folders, seen in the same source), set in the per-session config the dispatcher already writes.
- **The rename branch.** It touches `install_agents.py` (`HARNESSES` → `AGENT_CLIS`, `--harness` → `--agent-cli`, plus a helper that maps a whole command to its first word), `cli.py` help text, and the docs. Whichever merges second rebases, keeping both sets of changes.
- **Claude Code's transcript folder slug.** Derived from the cwd string as today (`_project_slug`), verified once against a live layout in the past. The new cwd has `.` and `_` characters, which the slug maps to `-` like any other. The live run confirms it: traces are copied.
