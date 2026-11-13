# CLI reference

Every command the package installs, and every flag of the two public
commands with its default. [objectives.md](objectives.md) has the objectives
and the model policy file.

| Command | What it is |
|---|---|
| [`harness-tuner`](#harness-tuner) | The tuner. You run this one. |
| [`harness-tuner-install-agents`](#harness-tuner-install-agents) | Copies the agents' skills into a folder of your own, for interactive sessions. |
| [`kiln_api_mcp`](#internal-commands) | Internal: an MCP server for interactive Claude Code sessions. |
| [`harness-tuner-gateway-mcp`](#internal-commands) | Internal: the loop gateway. |
| [`harness-tuner-skill-installer-mcp`](#internal-commands) | Internal: the skill installer. |

`uv tool install harness-tuner` installs all five
([install.md](install.md) section 2). The first two are the public commands.
You do not need to run the other three. Each command also takes `-h` or
`--help`, which prints its usage.

**How to read the Default column.**

- A value in backticks, such as `720`, is the value the flag has when you do
  not give it.
- `off` is a switch that you did not give.
- `not set` is a flag that has no value when you do not give it. The text
  after it says what the command does then.
- `empty` is an empty text.
- `required` is a flag that the command cannot start without.

## `harness-tuner`

```
harness-tuner
harness-tuner --demo-project
harness-tuner --project-id <id> --task-id <id> \
    --guidance "what to work on, and what not to touch" \
    --timeout-mins 60 --budget-usd 20 --no-interaction
```

In a terminal, the start is **guided**: it asks for what the flags do not
give ([install.md](install.md#guided-or-not)). A flag on the command line
answers its question, even when the flag gives the default value. Without a
terminal, or with `--no-interaction`, the start asks nothing, and each flag
that you do not give takes the default in these tables. The questions, and the
values they pre-fill, are in [your-own-task.md](your-own-task.md) section 5.2.

### The project and the task

| Flag | Default | What it does |
|---|---|---|
| `--project-id` | not set: the only project in Kiln, or the one you pick from a list | The Kiln project id. When you give `--project-path` or `--task-id`, the start finds the project from it. When the start is not guided, two or more projects to choose from is an error that lists them. [your-own-task.md](your-own-task.md) section 5.1. |
| `--task-id` | not set: the only task in the project, or the one you pick from a list | The Kiln task id. Without `--project-id` or `--project-path`, it finds the project that has the task. When several projects have it (copies of the demo share their task id), a guided start asks which. The loop optimizes exactly one task and never touches another. |
| `--project-path` | not set: the folder Kiln has for the project | The on-disk Kiln project folder. When you give it, it must be the folder Kiln has, after `~` and links are resolved, or the start stops. Given alone, it also finds the project. The orchestrator's code reads it. No agent can: the read fence denies the whole tree, because it holds the eval inputs and results of every split. [your-own-task.md](your-own-task.md) section 1.5. |
| `--demo-project` | off | Copy the demo project (the moderation task of [examples/moderation](../examples/moderation/README.md)) into a new folder, `~/Kiln Projects/Forum Moderation`, import the copy into Kiln, and run on it. When that folder is taken, the copy goes to `Forum Moderation-1`, then `-2`, and so on. Nothing is overwritten. Each copy has its own project id. A guided start pre-fills the demo's guidance, and prints the command that runs on the same copy again. It cannot be combined with `--project-id`, `--task-id` or `--project-path`: that is a usage error (exit 2). [install.md](install.md#try-the-demo). |
| `--kiln-host` | `http://localhost:8757` | The running Kiln studio server, as a whole URL that starts with `http://` or `https://`. A host without it, such as `localhost:8757`, is a usage error (exit 2). Every agent's MCP configuration gets the same host, so the agents and the orchestrator always use one server. |

### The start and the display

| Flag | Default | What it does |
|---|---|---|
| `--no-interaction` | off | Never ask a question or wait. The start runs the same checks, and stops at the first failed one with its message on stderr (exit 1). A missing `--project-id` or `--task-id` with more than one choice is an error that lists them. Each option that you do not give takes its default. Without a terminal (CI, a script, a pipe, another agent), the start never asks, with or without this flag. In a terminal, the start's step lines still show in the app, and the run screen follows. It changes only the start. `--no-progress` controls the display during the run. |
| `--no-progress` | off | Plain timestamped log lines instead of the run screen during the run ([your-own-task.md](your-own-task.md) section 5.5). Use it for a CI log, or to keep the run's lines in the terminal's scrollback. In a terminal, the guided start still runs in the app, which closes when the run starts. It changes only the display during the run. `--no-interaction` controls the start. |

### What the run improves

| Flag | Default | What it does |
|---|---|---|
| `--objective` | `quality` | What the run optimizes: `quality`, `speed` or `cost`. One objective per run. It also sets the default of `--models`. [objectives.md](objectives.md#what-to-optimize---objective) has what each one holds fixed, and when a candidate is promoted. |
| `--guidance` | empty | Free text for the planner: what to work on, and what not to touch. It goes verbatim to every planner, every wave, and to the promotion gate. [your-own-task.md](your-own-task.md) section 4. |
| `--models` | not set: `keep` under `quality`, `any` under `speed` and `cost` | Which models the task may use. `keep`: the baseline's model and provider only; the thinking level, the temperature and `top_p` can still change. `any`: every model on the providers that have a key in Kiln, which can move the task to a more expensive model and raise the spend. Enforced in code. Not with `--model-policy`. [objectives.md](objectives.md#which-models-a-run-may-use) |
| `--model-policy` | not set | A JSON file of allowed and denied (provider, model) pairs for the task's run configs: the task may use the models it allows, under every objective. In place of `--models`. The guided start writes one when you pick models. [objectives.md](objectives.md#a-model-policy-file---model-policy) has the file format, the rules and an example. |

### Limits

| Flag | Default | What it does |
|---|---|---|
| `--budget-usd` | `50` | The cost ceiling for the run, in USD, on the task's model calls. It never counts the agent sessions, which run on the agent CLI's own account: the run shows their tokens for information. No wave starts past it. At the ceiling the run exits, and the wave in flight runs to completion. Within one projected wave of the ceiling, the next wave is the wrap-up. `none` runs with no ceiling. Type a plain number (`--budget-usd 12.50`). The shell expands an unquoted `$12.50` before the CLI sees it, so the CLI gets a different amount. A leading `$` works only in single quotes (`'$12.50'`). |
| `--timeout-mins` | `720` (12 hours) | The time limit in whole minutes, at least 10. Setup runs before the first wave and takes a few minutes, so the start refuses a shorter limit. The loop checks the limit at wave boundaries. No new wave starts after it. The wave in flight is not cancelled: it finishes, promotion gate included. Then one wrap-up wave merges the pooled wins, and the run writes its report. So the run can end later than this, by the wave in flight plus the wrap-up. |

**The time limit is approximate.** At the limit, no new wave starts. The
wave in flight is not cancelled: its builds, measurements, analyses and
promotion gate finish. Then one wrap-up wave merges the pooled wins, and the
run writes its report. So the run can end later than the limit, by the length
of one or two waves. How long a wave takes depends on the agent model and on
the task: the size of the evals, the number of experiments and the speed of
the task's model. `--budget-usd` stays a hard cap, and Ctrl+C stops the run
at once.

[costs.md](costs.md) helps you set both limits before you start.

### The agents

| Flag | Default | What it does |
|---|---|---|
| `--agent-cli` | `claude -p` | The agent CLI that every loop session runs, as the whole command that you type, not only its name. Also `codex exec` and `opencode run`, each with its own options, for example `'claude -p --model haiku'` for a cheaper run, `'codex exec -m gpt-6.1-sol'` or `'opencode run -m zai-coding-plan/glm-5.3-flash --variant high'`. The model and the effort in the command are the run-wide defaults under `--agent-model` and `--agent-effort`. The effort is `--effort` for Claude, `-c model_reasoning_effort=…` for Codex (for example `'codex exec -m gpt-6.1-sol -c model_reasoning_effort=xhigh'`) and `--variant` for OpenCode. A command that the tuner cannot read is a usage error (exit 2) before the start asks anything: an empty command, a CLI other than these three, an option with no value, or a Claude effort that is not a level. The CLI must already be signed in: one short test prompt goes through it before the run starts, and a CLI that does not answer stops the start with what it printed. The agents are billed to the CLI's own account, not to your Kiln provider keys. [agent-cli.md](agent-cli.md) section 9. |
| `--agent-model` | not set: `opus` for each role, and `sonnet` for `generality_review`, under `claude -p` | `ROLE=MODEL`, repeatable. A model for one agent role. A model in `--agent-cli` replaces the per-role defaults. Under `codex exec` and `opencode run` with no model, the CLI uses its own default model. The roles are `analyze_experiment_results`, `baseline_review`, `batch_plan`, `create_experiment_rc`, `describe_eval`, `eval_build`, `generality_review`, `integrity_review`, `preflight`, `probe`, `promote_best` and `report_writer`. An unknown role, or a value that is not `ROLE=MODEL`, is a usage error (exit 2) before the start asks anything. |
| `--agent-effort` | not set: the effort in `--agent-cli`, else `high` | `ROLE=EFFORT`, repeatable. A reasoning effort for one agent role. The roles are the same as for `--agent-model`, with the same check. Under Claude Code, a level other than `low`, `medium`, `high`, `xhigh` or `max` is refused when the run starts, before any agent session. This check comes after the start's questions, because a guided start can still change the agent CLI. |
| `--max-concurrent-agents` | `4` | The agent sessions in flight at once: a whole number, 0 or more. `0` is no limit. The planner is told this number, so it sizes a wave to what can run. |
| `--max-turns` | not set: no turn cap | A hard turn cap for each agent session: a whole number, at least 1. Claude Code reads `0` as no cap, so the tuner refuses `0` and below. The per-role time limits stop a session that does not end. A cap near the longest session seen cuts off a large healthy session near its end, and the session is then paid for again. Under `codex exec` it has no effect, with a warning. Under `opencode run` it is the build agent's `steps` cap ([agent-cli.md](agent-cli.md) sections 10 and 11). |

### Measurement and the promotion gate

| Flag | Default | What it does |
|---|---|---|
| `--max-concurrent-evals` | `4` | The eval jobs in flight at once, for the whole run: a whole number, 0 or more. `0` is no limit. This is the rate-limit control: the loop's own parallelism can cause the provider limit that then stops it. |
| `--coverage-floor` | `0.95` | The promotion gate's val coverage floor, a share from 0 to 1. A candidate whose val measurement is less complete than this cannot win the wave, whatever the gate decides. |
| `--gate-agent` | `auto` | `auto` or `always`. `auto` lets a code rule decide where one applies: one candidate under `quality`, any number of candidates under `speed` and `cost`. The agent decides the rest: two or more `quality` candidates, a rule that cannot decide, or a winner with integrity flags. `always` dispatches the agent for every gate. |
| `--min-effect` | not set: no smallest effect | The smallest improvement you would act on, as a share of an eval's items, above 0 and at most 1 (`0.1` is 10%). The detectability check (free) then says for each eval whether the best case can show that change on train and on val. The best case is the significance floor of 6 items over the split. Under `quality`, the check stops the run when no eval that can drive a promotion can show the change on val. [your-own-task.md](your-own-task.md) section 1.4. |
| `--allow-underpowered` | off | Run even when the detectability check stops the run because no eval can show a significant val win. The refusal still prints, and it goes in the report's Detectability appendix. The promotion agent then weighs nominal val wins. |
| `--noise-check` | `0` (off) | `N`, a whole number, 0 or more. `0` is off. Before wave 1, drive the baseline again on N train items for each eval, and judge the stored outputs again, then report how often each score changed. The drives use a clone of the baseline run config, so Kiln's item cache does not answer. The judging uses a copy of each V2 judge config, never the default. It is paid: an estimate prints first, the cost goes to the budget, and the check is skipped when the estimate does not fit `--budget-usd`. [architecture.md](architecture.md) section 5.2, [costs.md](costs.md). |

### Checks that you can turn off

| Flag | Default | What it does |
|---|---|---|
| `--no-recon` | off | Skip the setup reconnaissance battery: the code probes that find out, on your baseline, if the model calls an attached tool and reads an attached skill (only when one is attached), how it behaves in the alternate output mode, if it reasons more at a higher level (under `speed`: if it keeps its output one level down and with thinking off, and how much faster it is), if it gives valid JSON, and if it gives output on the longest inputs. The levels come from the provider's own list. Do not use this flag on a first run: the battery stops the first planner from assuming facts about your model. It drives real items on your model, so it is not free ([your-own-task.md](your-own-task.md) section 5.4). |
| `--no-baseline-audit` | off | Skip the audit of the baseline's own text. By default, once for each baseline, setup searches the baseline's prompt, skills and code tools for verbatim copies of train items (free). When the search finds none, it runs one review session. A leak becomes one `baseline_audit` issue that asks for a scrub. The audit never stops the run. [architecture.md](architecture.md) section 3.1. |
| `--no-design-review` | off | Skip the design review. By default, once for each baseline (at the start of the run and after each promotion), one read-only agent session reads the baseline's prompt, skills, tools and a sample of its train traces, and names the smells it finds. Each finding becomes one `smell` issue that the planner reads. The code smell detectors run with or without it. The review never stops the run. [architecture.md](architecture.md) section 18. |
| `--reference-check` | `warn` | `warn`, `refuse` or `off`. After each build, code looks for names that point at nothing. One is a skill that the prompt or an attached skill names and that the run config does not attach. Only the names of skills in the project or on the parent count. The other is a tool that the parent attaches, that the build dropped, and that the prompt or an attached skill still names. A dangling skill name that the parent already has is not reported. The check is free. `warn` writes the findings on the item outcome, on a `reference check:` line of the experiment record and in the analysis input, and measures the build. `refuse` gives the build one rebuild with the findings, as for a generality leak, and a second hit ends the item `dead_end`. `off` skips the check. [architecture.md](architecture.md) section 4. |
| `--skip-model-preflight` | off | Skip the model preflight. By default, before the baseline measurement, the loop makes one short test call on each model the run may use: the baseline's own model, and the allowed models the planner can pick (at most 12). It also compares the baseline's prompt and tool definitions with each model's context window, when Kiln's model list gives one. A timeout, a connection failure, a 429 or a 5xx gets one retry first. A hard failure (bad request, not found, no deployment, context too long, quota or auth) flags the model in the log and the report, and the run does not screen or measure that model. On the baseline's own model, a hard failure stops the start. Any other failure leaves the model untested, with a warning, and excludes nothing. Each call costs one short prompt and one short answer, and goes to the budget. [architecture.md](architecture.md) section 3.1. |

### Experimental

| Flag | Default | What it does |
|---|---|---|
| `--experimental-eval-building` | off | Let the loop build a new eval for an issue that no eval measures: an `eval_only` item, or a fix with `needs_eval: true`. Each one starts the `eval_build` agent. This is not well tested, and an eval that it builds can have no val split, so the promotion gate cannot use that eval ([limitations.md](limitations.md)). Without the flag, the planner is told that eval building is off. Code drops each such item before its wave runs, with one log line for each, and the rest of the wave runs. The report's "Issues with no eval" section names each issue that no eval measures: create its eval in the Kiln app, then run again. A guided start's rerun command keeps the flag. |

### Removed flags

An old command line with one of these flags stops at once, with a usage error
(exit 2) that says what to use now:

- `--no-ui` is now `--no-progress`.
- `--max-request-cost` was removed. `--model-policy` keeps costly models out,
  and the cost of each arm per request is still reported.
- `--max-request-latency` was removed. Use `--objective speed` when latency is
  the goal. The latency of each arm per request is still reported.

## `harness-tuner-install-agents`

```
harness-tuner-install-agents --agent-cli claude --dest ~/my-kiln-project
harness-tuner-install-agents --agent-cli codex --dest .
harness-tuner-install-agents --agent-cli all --dest .
```

The loop's agents follow skills. The package keeps one copy of each, in
`kiln_harness_tuner/agent_files/skills/`. This command copies them into a
folder of your own, in the layout that one agent CLI reads, for interactive
sessions. The `kiln-api`, `kiln-task-setup` and `memory` skills are useful in
any project that works with Kiln.

This command is only for that copy. A run does not call it. Before its first
session, `harness-tuner` installs the skills and the loop agents'
instructions for its own agent CLI into the state folder, at
`agents/<cli>/<version>/`, with its own code. It checks that folder at every
run start, and replaces it when its files differ from the package's
([install.md](install.md#where-the-tuner-keeps-its-files)).

| Flag | Default | What it does |
|---|---|---|
| `--agent-cli` | required | The agent CLI to install for, by name: `claude`, `codex`, `opencode`, or `all` for the three. The whole command that `harness-tuner --agent-cli` takes (`'codex exec -m gpt-6.1-sol'`) also works: its first word names the CLI, and for a path, the path's last part. |
| `--dest` | required | The folder to install into, for example your own project. `~` is expanded. |

| Agent CLI | Skills go to | Also |
|---|---|---|
| `claude` | `.claude/skills/` | The `kiln_api_mcp` server, in `.mcp.json`, and its switch, in `.claude/settings.json`. |
| `codex` | `.agents/skills/` | Nothing. |
| `opencode` | `.agents/skills/` | Nothing. |

- A skill of the same name in the folder is replaced. Other skills there are
  left alone.
- `.mcp.json` and `.claude/settings.json` are merged into what is there.
  Other servers and settings are kept. A file there that is not a JSON object
  stops the command, with exit 1. The command reads both files first, so
  then it writes nothing: no skill and no file.
- A `--dest` that is a file, or a folder that you cannot write to, stops the
  command with exit 1 and a message.
- It writes no instructions file (`CLAUDE.md`, `AGENTS.md`). The loop agents'
  instructions describe the loop, and are no use in another project.
- It prints one line for each agent CLI, with the folders it wrote, such as
  `codex: skills in /path/to/project/.agents/skills`.

## Internal commands

The package installs three more commands. You do not need to run them: a
run or an interactive session starts the server that it needs.

- **`harness-tuner-gateway-mcp`** is the loop gateway, an MCP server on
  stdio: the loop agents' only way to Kiln. It serves the operations of one
  agent role, and reads the run's train set and model policy from the run
  folder. The MCP configuration of each loop session starts one, outside the
  agent's sandbox, as `<python> -P -m kiln_harness_tuner.gateway.server`, on
  the interpreter that runs the tuner. The console script starts the same server by hand.
- **`harness-tuner-skill-installer-mcp`** is the skill installer, an MCP
  server on stdio. It checks a skill bundle that an agent wrote in its scratch
  folder, and installs it into the project's `skills/` in one step. It is the
  only way that an agent's skill gets into the project tree. Only the MCP
  configuration of the builder and probe sessions starts it, as
  `<python> -P -m kiln_harness_tuner.skill_installer.mcp_server`. The console
  script starts the same server by hand.
- **`kiln_api_mcp`** is the `call_kiln_api` MCP server, on stdio. Its one
  tool calls the Kiln REST API. The `.mcp.json` that
  `harness-tuner-install-agents --agent-cli claude` writes starts it, by bare
  name, in an interactive Claude Code session. So it must be on your `PATH`:
  install the tool, because `uvx` does not put it there. A loop session never
  gets it. The name has underscores: it is half the tool name that a session
  calls (`mcp__kiln_api_mcp__call_kiln_api`), and the server key in
  `.mcp.json`.

## Next

- [your-own-task.md](your-own-task.md): what your task needs before the loop
  can work on it, and how the first run starts.
- [install.md](install.md): every requirement, with a check and a fix for
  each.
- [costs.md](costs.md): estimate a run before you start it.
