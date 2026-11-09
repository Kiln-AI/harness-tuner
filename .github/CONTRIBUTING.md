# Contributing to Kiln Harness Tuner

Thanks for your interest. Kiln Harness Tuner is early and its behaviour is
still moving, so open an issue before you start anything larger than a bug
fix.

## Setup

You need [uv](https://docs.astral.sh/uv/) and Python 3.11 or later.

```
uv sync
```

This makes the development environment in `.venv`.

## Running the checks

CI runs these on Linux, macOS (Apple silicon) and Windows, on Python 3.11
and 3.13, and all of them must pass before you open a pull request:

```
uv run ruff format --check
uv run ruff check
uv run pytest
```

The test suite is offline. It needs no Kiln server and no agent CLI, and it
spends no model money.

## Where things are

```
src/kiln_harness_tuner/  the orchestrator: CLI, wave loop, measurement, memory
  agents/                one prompt template per agent role
  dispatch/              the agent CLIs: claude -p, codex exec, opencode run
  gateway/               the loop gateway: the only way loop agents reach Kiln
src/kiln_api_mcp/        the call_kiln_api MCP server, for interactive sessions
.agents/                 the agents' skills and instructions (AGENTS.md)
docs/                    the reference documentation
examples/                the moderation example and a model policy
tests/                   the offline test suite
```

[docs/architecture.md](../docs/architecture.md) section 13 has the layout in
full.

## Guidelines

- Keep a pull request to one change. Unrelated fixes go in their own.
- Write tests with the code. The suite stays offline: use the fakes in
  `tests/`, not a live server or a model.
- **The suite runs on every OS.** A test that cannot run on one OS gets a
  mark from `tests/platform_marks.py`, with the reason. Open text files with
  an explicit `encoding="utf-8"`: Windows does not default to UTF-8, and
  `tests/test_platforms.py` checks the source for it.
- **Loop agents read `.agents/AGENTS.md` and every skill in `.agents/skills/`.** Do
  not put measured results, lever advice or the outcome of an earlier run in
  them. An agent that reads the answer invalidates the measurement it is asked
  to make.
- **Write documentation in Simplified Technical English** (ASD-STE100): short
  sentences, one idea each, no metaphor. Describe what the code does now. Do
  not add dated change notes.
- **Fence pseudo-code without a language.** `ruff format` rewrites
  Python-tagged code fences in Markdown, except under `.agents/skills/`.
- **Kiln ids in this repository are invented.** Every twelve-digit id is in
  the `424242……` band, so it can never be mistaken for a resource on a real
  server.
- **No real customer data anywhere**, including in the examples.

## Licence

The `LICENSE` file is not in the repository yet. Ask before you invest in a
large change.
