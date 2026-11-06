---
status: complete
---

# Phase 4: CI packaging smoke test

## Overview

The suite runs from the checkout, so it cannot catch a package that works
only from a checkout. This phase adds a CI job, `package`, that builds the
sdist and the wheel, checks what they hold, installs the wheel as a uv tool
into temporary folders, and makes a real run from an empty folder with a fake
`claude` on `PATH` and no Kiln server (FS §12, architecture §9). The run gets
through the agent-file install, the agent CLI check, the lock, the run folder,
the rendered configs and the split check, and stops at the pre-flight with
exit code 1. The job then checks where every file went.

No product code changes. The names follow the merged agent CLI rename:
`harness-tuner-install-agents --agent-cli claude` (architecture §9.2 step 4
says `--harness`).

## Steps

1. **`tests/packaging/fake_claude.py`** (new). Run with the tool's
   interpreter, so `mcp` imports. No shebang of its own: the smoke script
   writes `#!<tool python>` above a copy of it.
   - `option(argv, name) -> str | None`: the value of `--name VALUE` or
     `--name=VALUE`; every other flag is ignored.
   - `result(text, session_id, *, is_error=False) -> dict`: the
     `claude -p --output-format json` object (`type`, `subtype`,
     `is_error`, `result`, `session_id`, `total_cost_usd: 0`, `usage`).
   - `async ping_gateway(config_path) -> tuple[list[str], str]`: reads the
     `kiln_harness_tuner_gateway` server from the MCP config, starts it with
     `stdio_client(StdioServerParameters(command, args, env=dict(os.environ)))`,
     then `initialize`, `list_tools` and `call_tool("ping", {})`. Returns
     the tool names and the ping's text.
   - `main() -> int`: reads the prompt from stdin. A prompt that holds
     `KILN-HARNESS-TUNER-READY` gets that word back. Else, with
     `--mcp-config`, the gateway is pinged and the answer is a fenced
     ```json envelope `{"status": "ok", "data": {"ping": <text>},
     "detail": null}`. Else, or when the gateway cannot be pinged, an
     `is_error` result and exit 1. One JSON line goes to `$FAKE_CLAUDE_LOG`
     per call: `cwd`, `argv`, `prompt[:200]`, plus `tools` and `ping` (or
     `error`) when it pinged.

2. **`tests/packaging/smoke_installed_wheel.py`** (new). Stdlib only, run
   with the runner's interpreter, never imports `kiln_harness_tuner`.
   `--dist DIR` names the folder `uv build` wrote. Each check raises
   `SystemExit` naming what failed (with a tail of the command's output
   where there is one) and prints one `ok:` line when it passes.
   - **sdist**: the top-level entries are a subset of `src`,
     `pyproject.toml`, `README.md`, `PKG-INFO` and `.gitignore` (hatchling
     always adds it), plus `LICENSE*`. `src/kiln_harness_tuner/agent_files/AGENTS.md`
     is present.
   - **wheel**: holds `agent_files/AGENTS.md`,
     `agent_files/skills/kiln-api/SKILL.md`, `agent_files/claude/.mcp.json`,
     and every file git tracks under `src/kiln_harness_tuner/agent_files/`
     and `src/kiln_harness_tuner/agents/`. Its top-level entries are the two
     packages and the `.dist-info` only, so no `tests/` and no `examples/`.
   - **install**: in one `TemporaryDirectory` (resolved), `uv tool install
     <wheel>` with `UV_TOOL_DIR=<tmp>/tools`, `UV_TOOL_BIN_DIR=<tmp>/bin`,
     `HOME=<tmp>/home`, `UV_CACHE_DIR` from the outer environment (from
     `uv cache dir` when it is not set) and `UV_PYTHON=sys.executable`, so
     the tool runs on the matrix Python and uv downloads none. The tool's
     interpreter `<tmp>/tools/harness-tuner/bin/python` exists.
   - **commands**, from the empty `<tmp>/cwd`: `harness-tuner --help`,
     `harness-tuner-install-agents --help` and `uvx --from <wheel>
     harness-tuner --help` exit 0 and print usage;
     `harness-tuner-install-agents --agent-cli claude --dest <tmp>/dest`
     exits 0, writes `.claude/skills/kiln-api/SKILL.md` and a `.mcp.json`
     with `kiln_api_mcp`, and writes no `CLAUDE.md` or `AGENTS.md`.
   - **dry pre-flight**, `dry_preflight(label, tmp, home, state, override)`,
     run twice: (a) `KILN_HARNESS_TUNER_HOME=<tmp>/state`, `HOME=<tmp>/home`;
     (b) no override (removed from the environment), `HOME=<tmp>/home2`, state
     `<tmp>/home2/.kiln_ai/harness_tuner`. Each writes the fake `claude`
     into `<tmp>/fakebin/`, copies `examples/moderation/Forum Moderation`
     into a fresh project folder, and runs `harness-tuner --project-path …
     --project-id 424242975950 --task-id 424242783061 --kiln-host
     http://127.0.0.1:9 --no-ui` from `<tmp>/cwd`, with
     `PATH=<tmp>/fakebin:<tmp>/bin:$PATH`, `FAKE_CLAUDE_LOG` and a 600 s
     timeout. It checks:
     - exit code 1; stdout has the start line naming `<state>/runs/`;
       stderr has `preflight failed` and `harness-tuner: run folder:`;
     - the agents folder `<state>/agents/claude/<version>/` (version from
       the wheel's file name) holds `.claude/skills/kiln-api/SKILL.md`, a
       `CLAUDE.md` equal to the wheel's `agent_files/AGENTS.md`, and no
       `.mcp.json`;
     - the fake log has at least two lines (the CLI check, the pre-flight),
       and every `cwd` is the agents folder;
     - exactly one run folder in `<state>/runs/`, with `settings.json`,
       `settings.readonly.json`, `mcp_config.json` and a non-empty
       `train_set.json`, and the lock `.lock-424242975950-424242783061`;
     - the gateway in `mcp_config.json` runs the tool interpreter with args
       that start `-P -m kiln_harness_tuner.gateway.server`, and
       `settings.json`'s `sandbox.filesystem.denyWrite` is `[<state>]`;
     - the pre-flight log line lists the gateway's tools, `ping` among them;
     - `<tmp>/cwd` is still empty; under `HOME`, the run added nothing
       outside `~/.kiln_ai/` and nothing at `~/.kiln_ai/harness_tuner`
       unless that is the state folder (Kiln may add other files under
       `~/.kiln_ai`);
     - `git status --porcelain --ignored` in the checkout is the same as at
       the start. `--ignored`, because the old run state (`runs/`,
       `traces/`, `reports/`) is git-ignored and plain status would not show
       a run that wrote it.

3. **`.github/workflows/ci.yml`**: a `package` job beside `check`, on the same
   triggers, `ubuntu-latest`, matrix `["3.11", "3.13"]`, `fail-fast: false`,
   `astral-sh/setup-uv@v5` with the matrix Python and the cache on (it sets
   `UV_CACHE_DIR` and activates a venv of that Python, which is the
   `python` the smoke script runs with). Steps: `uv build`,
   `uvx twine check dist/*`,
   `python tests/packaging/smoke_installed_wheel.py --dist dist`.

4. **Docs** (the layout notes phase 3 left for this phase):
   - `docs/architecture.md` §13: `tests/packaging/` under `tests/`. §15: one
     paragraph on the smoke test, what it proves and what it does not (no
     model, no Kiln server).
   - `.github/CONTRIBUTING.md`: `tests/packaging/` in "Where things are", and
     the three commands to run the smoke test locally under "Running the
     checks".
   - Root `AGENTS.md`: "CI runs the same three" becomes the three checks
     plus the packaging job.

## Tests

The smoke script is the test. It runs in CI, not under pytest:
`tests/packaging/` holds no `test_*.py`.

- sdist holds only the allowed top-level entries and the agent files.
- wheel holds the agent files, the role templates and the Claude MCP
  config, and no `tests/` or `examples/`.
- the wheel installs as a uv tool with an isolated `HOME`.
- `--help` works for the installed commands and through `uvx --from`.
- `harness-tuner-install-agents --dest` installs skills and the MCP config
  and no instructions file.
- a dry pre-flight run with `KILN_HARNESS_TUNER_HOME`: exit code and
  messages, agents folder, session cwd, run folder and lock, gateway
  command, denyWrite, gateway tools, nothing in the cwd, `HOME` or the
  checkout.
- the same run without the override puts the state in
  `$HOME/.kiln_ai/harness_tuner/`.

The existing suite and checks (`uv run pytest`, `uv run ruff check`,
`uv run ruff format --check`) stay green, and the smoke script passes
locally against a fresh `uv build`.
