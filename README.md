# Kiln Harness Tuner

[![CI](https://github.com/Kiln-AI/kiln-harness-tuner/actions/workflows/ci.yml/badge.svg)](https://github.com/Kiln-AI/kiln-harness-tuner/actions/workflows/ci.yml)

An agentic optimize loop for Kiln tasks. You give it a task that already has
evals and a train/val/test split; it plans experiments against your baseline,
builds each one as a new run config, and measures them on the train split. It
promotes a candidate to baseline only when the val split agrees, and it never
reads test — so the number you ship on is one the loop never optimized against.

## Requirements

| What | Why |
|---|---|
| Python 3.11+ and `uv` | The package. |
| A running Kiln studio server, with your project in it | Every measurement is a Kiln eval job. This repository does not ship it: Kiln is at `https://kiln.tech`, the address `kiln-ai` declares in its own distribution metadata. |
| An agent CLI, signed in: Claude Code, Codex or OpenCode | Every agent is a `claude -p` subprocess by default. `--agent-cli 'codex exec …'` or `--agent-cli 'opencode run …'` selects another one. |
| A model provider key in Kiln | Your task's run configs call it. |
| `uv` on the `PATH` that `claude -p` inherits | Each agent session starts the loop gateway, its only Kiln access, with `uv run`. |

[docs/install.md](docs/install.md) has the check command, the failure message
and the fix for each one.

**Not on macOS x86_64.** Two locked dependencies publish no wheel for it and no
source distribution, so `uv sync` stops. Linux on either architecture, Windows
and macOS on Apple silicon are fine.
[docs/install.md](docs/install.md) has the measurement and what a fix would
take.

## Install

Clone the repository and run everything below from its root. It has to be a
real checkout rather than an installed wheel: a run reads the agents' skills
and instructions (`.agents/`) out of that directory.

```
git clone https://github.com/Kiln-AI/kiln-harness-tuner.git
cd kiln-harness-tuner
uv sync
uv tool install .
```

`uv sync` builds the development environment. `uv tool install .` puts the
console scripts on your `PATH`; `kiln_api_mcp` among them is for interactive
Claude Code sessions (loop agents reach Kiln through the loop gateway, which
each session starts with `uv run`).

Verify:

```
kiln-harness-tuner --help
kiln_api_mcp --help
```

Both print usage. If either says `command not found`, read
[docs/install.md](docs/install.md).

## Install the agents' skills for your agent CLI

The loop's agents follow skills and one instructions file. The repository
keeps one copy of each in `.agents/`: the skills in `.agents/skills/` and the
instructions in `.agents/AGENTS.md`. Each agent CLI reads them from its own
place, and `kiln-harness-tuner-install-agents` copies them there:

| `--agent-cli` | Agent CLI | Skills | Instructions |
|---|---|---|---|
| `claude` | Claude Code | `.claude/skills/` | `CLAUDE.md`, plus the `kiln_api_mcp` server in `.mcp.json` and `.claude/settings.json` |
| `codex` | Codex | `.agents/skills/` | `AGENTS.md` |
| `opencode` | OpenCode | `.agents/skills/` | `AGENTS.md` |

A run installs for its own agent CLI (the one `kiln-harness-tuner --agent-cli`
names) before the first session, so you do not need to do it by hand to run
the loop. Run it yourself to work on this repository in an agent CLI, or to
use the skills in another project:

```
kiln-harness-tuner-install-agents --agent-cli claude
kiln-harness-tuner-install-agents --agent-cli codex
kiln-harness-tuner-install-agents --agent-cli opencode
kiln-harness-tuner-install-agents --agent-cli all
kiln-harness-tuner-install-agents --agent-cli claude --dest ~/my-kiln-project
```

Here `--agent-cli` takes the CLI's name. The whole command a run takes
(`--agent-cli 'codex exec -m gpt-6.1-sol'`) also works: its first word names
the CLI.

`--dest` installs into another directory. The `kiln-api`, `kiln-task-setup`
and `memory` skills are useful in any project that works with Kiln. A skill
of the same name is replaced; other skills there are left alone. An
instructions file that the command did not write is never replaced, unless
you pass `--force`.

## Quickstart: the moderation example

[`examples/moderation/`](examples/moderation/README.md) is a small task to run
the loop on first. It moderates posts on an invented gardening forum: one post
in, `{"action": "keep" | "remove", "rule": ...}` out. It has no tools and no
state, 624 labelled posts split into train, val and test, and one code eval
that costs nothing to score. The forum's house rules are stricter than common
sense, and the baseline is a short prompt that does not state them, so it
makes mistakes the loop can find and fix.

After [Install](#install), with Kiln running and a Fireworks key in Kiln:

```
mkdir -p ~/kiln-projects
cp -R "examples/moderation/Forum Moderation" ~/kiln-projects/
curl -X POST "http://localhost:8757/api/import_project?trusted=true&project_path=$HOME/kiln-projects/Forum%20Moderation/project.kiln"
kiln-harness-tuner --project-path ~/kiln-projects/"Forum Moderation" \
          --project-id 424242975950 --task-id 424242783061 \
          --guidance "Improve the moderation decisions. Keep the output format." \
          --timeout-mins 60 --budget-usd 20
```

The loop shows its progress in the terminal and writes its report to
`reports/`. The example's README explains each step, the labelling
guidelines, and how to check the result on test yourself.

This README quotes no result yet. A score belongs to one task, one model and
one corpus, so a number from another task would not tell you what the loop
does on yours.

## Run it on your own task

```
kiln-harness-tuner --project-path "/path/to/Your Kiln Project" \
          --project-id <id> --task-id <id> \
          --guidance "what to work on, and what not to touch" \
          --timeout-mins 60 --budget-usd <your ceiling>
```

The loop shows its progress in the terminal, works in waves until the budget,
the clock or its own planner ends it, and writes a report under `reports/`.

**Start it bounded, with headroom for a whole wave.** No wave ever *starts*
past either limit, but they end a run differently: at the budget ceiling the
wave in flight runs to completion, while at the deadline every in-flight item
except one that is already analyzing is cancelled — drives paid for, no verdict
stored. A first run on an unfamiliar task is the wrong place to find out how
long a wave takes. Estimate before you start: [docs/costs.md](docs/costs.md).

What your task needs before any of this works — a run config, at least one eval,
train and val split tags, and enough inputs in each — is
[docs/your-own-task.md](docs/your-own-task.md). Setup seeds what it can and
refuses to guess the rest.

## What to optimize: `--objective`

One objective per run. Every candidate is measured on train, and the
promotion gate decides on val.

| Objective | The goal | A candidate is promoted when, on val |
|---|---|---|
| `quality` (default) | Higher eval scores. | No eval goes down beyond doubt, and at least one eval it targets goes up beyond doubt. |
| `speed` | Lower latency per request, with quality held. | It is clearly faster (a sign test on paired requests, a median ratio below 1, no significant p90 regression), and quality is equal or better. |
| `cost` | Lower provider cost per request, with quality held. | It is clearly cheaper, by the same test, and quality is equal or better. |

"Quality is equal or better" means: no eval score down significantly, net
items over all evals at zero or above, and no single eval down by more than a
few items.

- **`quality`** works on issues: one failure class of one eval. The planner
  builds a fix for an issue, such as a prompt change, a skill, a tool or a
  model change, and the fix must show on the items the baseline fails. When no
  eval can measure an issue, the loop builds that eval first.
- **`speed`** measures the sum of the LLM call latencies of one task run. A
  simulated user's calls are not counted. Before wave 1 it tries each lower
  thinking level of the baseline's model. It judges the items the baseline
  already passes first, because the question is whether a faster candidate
  keeps them.
- **`cost`** measures the task run's own `usage.cost`, and otherwise works as
  `speed` does. A model with no price in Kiln reports no cost and counts as
  $0, so check that every model the planner may try has a price. The levers
  that keep quality are fewer tokens: fewer turns, a shorter prompt, fewer
  attached tools and skills. A cheaper model usually loses some quality.

Under every objective, each experiment also reports its latency and cost
against its parent, read from the stored runs at no extra cost.
[docs/how-it-works.md](docs/how-it-works.md#the-objectives) has the stop rules
and the promotion rules in full.

## Which models a run may use: `--model-policy`

By default the planner may move the task to any model Kiln offers. A model
policy limits that. It is a JSON file of `allow`, `deny` and `exceptions`
rules over Kiln's (provider, model) ids, with `*` wildcards:

```
{
  "description": "Direct providers only. No Groq or Cerebras.",
  "allow": [
    {"provider": "fireworks_ai", "model": "*"},
    {"provider": "anthropic", "model": "*"},
    {"provider": "openrouter", "model": "claude_*"}
  ],
  "deny": [
    {"provider": "groq", "model": "*", "reason": "provider not allowed"}
  ],
  "exceptions": []
}
```

An exception (one exact pair) allows a model. Otherwise a `deny` match refuses
it, an `allow` match allows it, and anything else is refused.
[`examples/model-policy.json`](examples/model-policy.json) is a complete one.

- **Code enforces it**, not the agents: on every run config an agent creates
  or drives, before any run config is measured, and before a candidate is
  promoted.
- **The baseline is exempt.** It is your starting point. A baseline outside
  the policy is named once at start-up.
- **Judges are not covered.** An eval's judge model is part of the eval, and
  the loop does not change evals.
- **Every allowed model is tested at start-up.** One short call goes to the
  baseline's model and to each allowed model the planner may pick (at most
  12). A model that fails is left out of the run. `--skip-model-preflight`
  skips this.

The policy covers the task's models. The agents that run the loop are set
apart, with `--agent-cli` and `--agent-model`, and are billed to the agent
CLI's own account.

## Common flags

| Flag | What it does |
|---|---|
| `--guidance TEXT` | Free text for the planner: what to try first, what not to touch. |
| `--budget-usd N` | The money for the run: agent sessions and the task's model calls together. No wave starts past it. |
| `--timeout-mins N` | The time for the run. No wave starts past it, and in-flight items are cancelled at it. |
| `--objective` | `quality`, `speed` or `cost`, as above. |
| `--model-policy PATH` | The models the task may move to, as above. |
| `--agent-cli COMMAND` | The agent CLI and its model, for example `'claude -p --model haiku'` for a cheaper run. The default is `claude -p`, on Opus. `codex exec` and `opencode run` also work. |
| `--agent-model ROLE=MODEL` | One agent role on another model. Repeatable. |
| `--min-effect FRACTION` | The smallest improvement you would act on. The run stops before it spends money when no eval can show that change on val. |

[docs/your-own-task.md](docs/your-own-task.md) lists every flag.

## Where to go next

- [docs/how-it-works.md](docs/how-it-works.md) — the whole loop for a
  first-time reader, with a diagram and one section for each objective.
- [docs/install.md](docs/install.md) — every requirement, each with its check,
  its failure message and its fix.
- [docs/concepts.md](docs/concepts.md) — wave, experiment, arm, lever, split,
  promotion gate, memory, probe. Read this before the report of your first run.
- [docs/your-own-task.md](docs/your-own-task.md) — what a task needs, what setup
  does for you, what it refuses to guess, and every CLI flag.
- [docs/costs.md](docs/costs.md) — how to estimate a run before starting it.
- [docs/architecture.md](docs/architecture.md) — the wave loop, the promotion
  gate, measurement, memory, errors, the sandbox.
- [docs/agents.md](docs/agents.md) — the ten agent roles: what each is given,
  what each must return, and which one to blame when a run goes wrong.
- [docs/agent-cli.md](docs/agent-cli.md) — the dispatcher protocol,
  `--agent-cli`, the Codex and OpenCode dispatchers and how each fences a
  session, and what another agent CLI would need.
- [docs/limitations.md](docs/limitations.md) — what the loop does not do and
  what its guards cannot see. Read this before planning a long run.

## Contributing

[.github/CONTRIBUTING.md](.github/CONTRIBUTING.md) says how to set up, test and change the
repository.
