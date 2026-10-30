# Kiln Harness Tuner

An agentic optimize loop for Kiln tasks: it plans experiments against a task's
baseline run config, builds and measures each one on the train split, and
promotes a candidate only when the val split agrees. Every agent in it is an
agent-CLI session (`claude -p` by default), every measurement is a Kiln eval
job, and test is never read.

## Layout

```
src/kiln_harness_tuner/
                    the orchestrator: cli, wave loop, items, analysis,
                    memory, schemas, observability
  agents/           one prompt template per agent role
  dispatch/         the dispatcher seam, and the claude -p, codex exec and
                    opencode run implementations
  gateway/          the loop gateway: the only way loop agents reach Kiln
  skill_installer/  the MCP server that installs a skill bundle atomically
src/kiln_api_mcp/   the vendored call_kiln_api MCP server (interactive sessions)
.agents/            the agents' skills, and this file: installed per agent CLI
docs/               the reference documentation
examples/           the moderation example and a model policy
tests/              the offline suite — no live server, no model spend
```

`docs/architecture.md` section 13 is the same layout in full.

## The moderation example

`examples/moderation/` is the example: a single-turn task that moderates
posts on an invented gardening forum. The Kiln project is
`examples/moderation/Forum Moderation/`. `posts.jsonl` holds its 624 labelled
posts. `scorer.py` is a copy of its code eval. Project
`424242975950`, task `424242783061`.

Every id this repository authors is inside the invented `424242……` band, so
it can never be mistaken for a resource on a real server.

**Nothing measured from the example goes in this file**, for the reason the
working rules below give: every agent the loop dispatches loads it.

## Working rules

**Findings go in `reports/`, never in this file.** Every agent the loop
dispatches loads this file, so anything here that answers a question the loop
is supposed to measure invalidates the measurement. That includes measured
numbers, lever advice and what worked in an earlier run. This file carries what
the repository *is*, not what anyone has learned from running it.

**Write in ASD-STE100 Simplified Technical English.** Short sentences, one idea
each, no metaphor.

**Fence pseudo-code without a language.** `ruff format` reformats Python fenced
in Markdown, and only `.agents/skills/**` is excluded from it (`pyproject.toml`,
`tool.ruff.extend-exclude`). A Python-tagged fence anywhere else in this tree —
`docs/`, this file — is rewritten by the formatter. A fence with no language is
left alone.

**Run the checks before you call anything done.** CI runs the same three on
every pull request (`.github/workflows/ci.yml`).

```
uv run pytest
uv run ruff check
uv run ruff format --check
```
