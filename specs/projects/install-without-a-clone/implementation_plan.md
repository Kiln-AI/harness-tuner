---
status: complete
---

# Implementation Plan: Install without a clone

Each phase leaves the suite green (`uv run pytest`, `uv run ruff check`, `uv run ruff format --check`).

## Phases

- [x] Phase 1: Ship the agent files and rename the distribution.
  - `git mv .agents src/kiln_harness_tuner/agent_files`, as a commit of its own with no content change.
  - `install_agents` reads its source through `importlib.resources`. Runs still install into the checkout, as today.
  - `pyproject.toml`: name, scripts, URLs, sdist `only-include`, ruff exclude. Regenerate `uv.lock`.
  - Command names in `prog=` and in messages (architecture §8).
  - Every `.agents/` path in code, tests and doc links.
- [x] Phase 2: State folder and agent sessions.
  - `paths.py`, `install_for_run`, and the `--dest` rework (architecture §3–4).
  - The instructions split, with root `AGENTS.md` and `CLAUDE.md`.
  - `cli.py` wiring: start and exit lines, `-P -m` MCP commands, fences, and removal of `REPO_ROOT` and the checkout code (architecture §5).
  - Dispatchers (architecture §6), `.gitignore`, tests (architecture §10).
- [ ] Phase 3: Docs.
  - README, `docs/install.md`, `docs/limitations.md` and every other doc, the CONTRIBUTING file and the example README (FS §11, architecture §11).
- [ ] Phase 4: CI packaging smoke test.
  - `tests/packaging/smoke_installed_wheel.py`, `tests/packaging/fake_claude.py`, and the `package` job (architecture §9).
