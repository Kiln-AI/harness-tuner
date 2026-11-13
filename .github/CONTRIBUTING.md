# Contributing to Kiln Harness Tuner

Thanks for your interest. Kiln Harness Tuner is early and its behaviour is
still moving, so open an issue before you start anything larger than a bug
fix.

## Setup

You need [uv](https://docs.astral.sh/uv/) and Python 3.11 or later.

```
git clone https://github.com/Kiln-AI/harness-tuner.git
cd harness-tuner
uv sync
uv run harness-tuner --help
```

`uv sync` makes the development environment in `.venv`, with the package
installed from `src/` in editable mode. `uv run harness-tuner …` then runs the
loop from your clone. It keeps its state in the same folder as an installed
tuner, `~/.kiln_ai/harness_tuner/` (or `$KILN_HARNESS_TUNER_HOME`), and writes
nothing in the clone. A run checks its agents folder against
`src/kiln_harness_tuner/agent_files/` at every start, so the next run picks up
a skill you edit.

The root `AGENTS.md` (loaded by `CLAUDE.md`) is the contributor guide for
agent sessions in this repository.

## Running the checks

CI runs these on Python 3.11 and 3.13, and all of them must pass before you
open a pull request:

```
uv run ruff format --check
uv run ruff check
uv run pytest
```

The test suite is offline. It needs no Kiln server and no agent CLI, and it
spends no model money.

CI also builds the package and tests the installed wheel from an empty folder,
with a fake `claude` and no Kiln server. The suite runs from your clone, so
only this test finds a package that works from a clone and nowhere else. To
run it yourself (it installs into a temporary folder and needs `git`):

```
rm -rf dist
uv build
python tests/packaging/smoke_installed_wheel.py --dist dist
```

The tool is installed on the Python that runs the script, which must be 3.11
or later.

## Where things are

```
src/kiln_harness_tuner/  the orchestrator: CLI, wave loop, measurement, memory
  agents/                one prompt template per agent role
  agent_files/           the agents' skills and instructions (AGENTS.md),
                         shipped as package data
  dispatch/              the agent CLIs: claude -p, codex exec, opencode run
  gateway/               the loop gateway: the only way loop agents reach Kiln
  knowledge/             the knowledge system's code: load, check, index
  smells/                the smell detectors and the baseline review step
  tui/                   the terminal app (Textual) of an interactive run
src/kiln_api_mcp/        the call_kiln_api MCP server, for interactive sessions
docs/                    the reference documentation
examples/                the moderation example and a model policy
tests/                   the offline test suite
  packaging/             the CI smoke test of the installed wheel
```

[docs/architecture.md](../docs/architecture.md) section 13 has the layout in
full.

## Guidelines

- Keep a pull request to one change. Unrelated fixes go in their own.
- Write tests with the code. The suite stays offline: use the fakes in
  `tests/`, not a live server or a model.
- **Loop agents read `src/kiln_harness_tuner/agent_files/AGENTS.md` and every
  skill in `agent_files/skills/`.** Nothing measured on the run's own target,
  or on the moderation example, goes in them. An agent that reads the answer
  invalidates the measurement it is asked to make.
- **General knowledge goes in the knowledge system**, the skill
  `agent_files/skills/kiln-harness-tuner-knowledge/`: field knowledge, public
  sources, our own synthetic experiments and anonymised cases from other
  projects. Change it with the
  [maintenance skill](../.agents/skills/knowledge-maintenance/SKILL.md). A
  person approves a case study before it is committed, unless it is clearly
  public.
- **Write documentation in Simplified Technical English** (ASD-STE100): short
  sentences, one idea each, no metaphor. Describe what the code does now. Do
  not add dated change notes.
- **Fence pseudo-code without a language.** `ruff format` rewrites
  Python-tagged code fences in Markdown, except under
  `src/kiln_harness_tuner/agent_files/skills/`.
- **Kiln ids in this repository are invented.** Every twelve-digit id is in
  the `424242……` band, so it can never be mistaken for a resource on a real
  server.
- **No real customer data anywhere**, including in the examples.

## Licence

The project is under the MIT License. See [LICENSE](../LICENSE).

New contributors must agree to the contributor license agreement in
[.github/CLA.md](CLA.md).
