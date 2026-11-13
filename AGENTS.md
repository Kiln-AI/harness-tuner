# Kiln Harness Tuner

Automatically improves an AI agent by tuning its harness: the prompt, model,
tools, skills and settings around it. Agents propose and build each change,
and your evals measure it. A change becomes the new baseline only when
held-out data confirms it, and the test set is never read, so your test score
stays a true measure.

In the code, the agent is a Kiln task and its harness is the task's run
config. Every agent in the loop is an agent-CLI session (`claude -p` by
default), and every measurement is a Kiln eval job on the train or val split.

This file is the contributor guide. `CLAUDE.md` loads it for Claude Code.
Loop agents do not read it: they read
`src/kiln_harness_tuner/agent_files/AGENTS.md` and the skills beside it.

## Layout

```
src/kiln_harness_tuner/
                    the orchestrator: cli, wave loop, items, analysis,
                    memory, schemas, observability
  agents/           one prompt template per agent role
  agent_files/      the agents' skills and instructions: package data, which
                    a run installs per agent CLI into the state folder
  demo_project/     the moderation example's Kiln project, shipped as
                    package data for --demo-project
  dispatch/         the dispatcher seam, and the claude -p, codex exec and
                    opencode run implementations
  gateway/          the loop gateway: the only way loop agents reach Kiln
  knowledge/        the knowledge system's code: load, check and index the
                    skill kiln-harness-tuner-knowledge (python -m
                    kiln_harness_tuner.knowledge check|build)
  onboarding/       the start before the run: checks, guided questions,
                    the summary
  skill_installer/  the MCP server that installs a skill bundle atomically
  smells/           smell detection: the detectors in code, their
                    thresholds, and the baseline review step
  tui/              the terminal app (Textual) of an interactive run: the
                    start-up and run screens, and the run model
src/kiln_api_mcp/   the vendored call_kiln_api MCP server (interactive sessions)
docs/               the reference documentation
examples/           the moderation example and a model policy
tests/              the offline suite — no live server, no model spend
  packaging/        the CI smoke test of the installed wheel
specs/              the project specs, and backlog.md: the known gaps and
                    the pending ideas of the knowledge system
.agents/skills/     the skills for coding agents that work on this
                    repository: knowledge-maintenance
```

A run writes nothing here. Its state goes in the state folder,
`~/.kiln_ai/harness_tuner/` (or `$KILN_HARNESS_TUNER_HOME`): `runs/`,
`traces/`, `reports/`, and `agents/`, the agent files a run's sessions read.

`docs/architecture.md` section 13 is the same layout in full.

## The moderation example

`examples/moderation/` is the example: a single-turn task that moderates
posts on an invented gardening forum. The Kiln project ships in the package,
at `src/kiln_harness_tuner/demo_project/Forum Moderation/`, and
`--demo-project` runs on a copy of it. `posts.jsonl` holds its 624 labelled
posts. `scorer.py` is a copy of its code eval. The packaged project is
`424242975950`, and each copy gets a new project id. The task is
`424242783061` in every copy.

Every id this repository authors is inside the invented `424242……` band, so
it can never be mistaken for a resource on a real server.

**Nothing measured from the example goes in this file or in `agent_files/`**,
for the reason the working rules below give: every agent the loop dispatches
loads `agent_files/`.

## Working rules

**Nothing measured on the target goes in `agent_files/`.** Every agent the
loop dispatches loads the instructions and the skills in `agent_files/`. So
a number, a lesson or lever advice that comes from the run's own target, or
from the moderation example, invalidates what the loop measures. Those
findings go in the state folder's `reports/`.

**The knowledge system is the place for general knowledge.** The skill
`agent_files/skills/kiln-harness-tuner-knowledge/` holds what is known
about how to improve an agent: field knowledge, public sources, our own
synthetic experiments and anonymised cases from other projects. Read
`.agents/skills/knowledge-maintenance/SKILL.md` before you change it. A
person approves a case study before it is committed, unless it is clearly
public.

**Write in ASD-STE100 Simplified Technical English.** Short sentences, one idea
each, no metaphor.

**Fence pseudo-code without a language.** `ruff format` reformats Python fenced
in Markdown, and only `src/kiln_harness_tuner/agent_files/skills/**` (and its
installed copies) and `.agents/skills/**` are excluded from it
(`pyproject.toml`, `tool.ruff.extend-exclude`). A Python-tagged fence
anywhere else in this tree — `docs/`, this file — is rewritten by the
formatter. A fence with no language is left alone.

**Run the checks before you call anything done.** CI runs the same three on
every pull request (`.github/workflows/ci.yml`). Its `package` job also builds
the package and tests the installed wheel
(`tests/packaging/smoke_installed_wheel.py`). Run that one too when you change
packaging, paths or start-up: `.github/CONTRIBUTING.md` has the commands.

```
uv run pytest
uv run ruff check
uv run ruff format --check
```
