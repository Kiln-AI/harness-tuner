<p align="center">
    <a href="https://kiln.tech">
        <picture>
            <source media="(prefers-color-scheme: dark)" srcset="https://github.com/user-attachments/assets/d813a09e-3dc7-4423-aa6b-bcb8c19a2c5e">
            <source media="(prefers-color-scheme: light)" srcset="https://github.com/user-attachments/assets/13cd1e73-8b58-4c95-870f-e8fc7235fe09">
            <img width="360" alt="Kiln Harness Tuner Logo" src="https://github.com/user-attachments/assets/13cd1e73-8b58-4c95-870f-e8fc7235fe09">
        </picture>
    </a>
</p>


<h3 align="center">
  Tune your agent's harness: tools, prompts, skills, model and hyperparams
</h3>

<p align="center">
  <a href="https://github.com/Kiln-AI/kiln-harness-tuner/actions/workflows/ci.yml"><img src="https://github.com/Kiln-AI/kiln-harness-tuner/actions/workflows/ci.yml/badge.svg" alt="CI: Build and Test"></a>
  <a href="https://pypi.org/project/harness-tuner/"><img src="https://img.shields.io/pypi/v/harness-tuner" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="MIT">          
</p>

<p align="center">
  <a href="https://youtu.be/ocmgJF-4xRE"><img width="420" alt="Kiln Harness Tuner: watch the demo" src="https://github.com/user-attachments/assets/1edd32c9-dd09-421e-bb23-b3dec18d3d92" /></a>
</p>

**A real production agent, after tuning:** 2.5x fewer failures, 6x cheaper, 2x lower latency. Measured on held-out data, on a smaller model. It's a multi-turn agent with over 200 tools.

Better models move the frontier. A tuned harness gets you above it.

## What it tunes

Most optimizers tune the prompt. Harness Tuner tunes everything around the model.

- **Tools.** The biggest win we see. Your tools probably wrap APIs written for code, not for agents. It rewrites them for the agent: smaller responses, better descriptions, no repeated calls. When a script should do the job, it writes a new code tool.
- **Prompts and skills.** It cuts contradictions, adds the requirements nobody wrote down, and moves long instructions into skills that load only when needed.
- **Model and settings.** It searches models and providers, then tunes thinking level, temperature and the rest for the one it picks.
- **RAG, fine-tunes and sub-agents.** It attaches, swaps or drops the ones in your project.

It tries different methods, not just different words: one big prompt or skills, RAG or a skill, many small tools or one general one.

## How it works

<!-- TODO(img): loop diagram. Start from the mermaid flowchart in docs/how-it-works.md. -->

- **You set the goal and steer.** Optimize for quality, cost or speed. When it cuts cost or latency, it holds quality. Tell it what to focus on and what not to touch, in plain English.
- **Many goals, not one number.** Bring all your evals. It tracks every one.
- **Fix one thing at a time.** Each experiment runs only the evals it targets, so trying an idea is cheap. Before anything is promoted, it's checked against every eval. Nothing regresses quietly.
- **A graph, not a line.** Experiments branch from any earlier version, often from several parents, and the winners merge back.
- **Train, val and test, enforced in code.** Agents only ever see train. Val decides promotions. Test is never read, so the number you report is one it never optimized against.
- **Several winners, your pick.** It finds the best quality, the cheapest at good quality, a balanced option and the fastest. Compare them side by side in [Kiln](https://github.com/Kiln-AI/Kiln).
- **Runs on the coding agent you already use:** Claude Code, Codex or OpenCode, on your existing subscription.
- **Works with [Kiln](https://github.com/Kiln-AI/Kiln).** Our free app tracks every experiment and shows the trade-offs side by side.

<img width="861"  alt="Screenshot of Experiment Graph" src="https://github.com/user-attachments/assets/e93e24f6-d964-4ae8-acaf-015b7de37ac6" />

## Optimize the demo project

Downloads a small moderation task into a new folder, adds it to Kiln, and tunes it: 624 labelled forum posts, and an eval. The baseline prompt doesn't know the forum's house rules, so there's plenty to find and fix.

```
uv tool install harness-tuner
harness-tuner --demo-project \
    --guidance "Improve the moderation decisions. Keep the output format." \
    --timeout-mins 60 --budget-usd 20
```

## Quickstart

```
uv tool install harness-tuner
harness-tuner
```

It walks you through the rest: starting [Kiln](https://github.com/Kiln-AI/Kiln) (or getting it from [kiln.tech](https://kiln.tech)), then picking the project and task to tune. Your agent needs to be a task in Kiln, with evals. Kiln can walk you through creating both.

It works in waves until the budget or the clock runs out, then writes a report and prints where it is. Details: [docs/your-own-task.md](docs/your-own-task.md).

## What you need

- **[Kiln](https://github.com/Kiln-AI/Kiln),** on the same machine. The CLI walks you through installing and starting it.
- **An agent, with evals.** Kiln can walk you through creating both.
- **A coding agent you already use:** Claude Code (the default), Codex or OpenCode. The tuner's agents run on your subscription.
- **Your model provider keys,** stored in Kiln. Used for running the agent being tuned, and evals.
- **[uv](https://docs.astral.sh/uv/),** to install it.

Missing something? [docs/install.md](docs/install.md) has a check and a fix for each requirement.

Harness Tuner is free and open source, but it needs LLM API calls. See [docs/costs.md](docs/costs.md) for details.

## Works with Kiln and Seahaven

Tuning an agent takes more than a loop. [Kiln](https://github.com/Kiln-AI/Kiln) and [Seahaven](https://github.com/Kiln-AI/Seahaven) have the rest:

- **[Evals](https://kiln.tech/features/evals)** that say what good looks like, as many as your product needs.
- **Synthetic users** that hold multi-turn conversations, the same for every candidate.
- **[Synthetic worlds](https://github.com/Kiln-AI/Seahaven)** for agents that change data. Every run gets a fresh copy of the world, and evals grade what the agent actually did, not what it said it did.

## FAQ

<!-- Draft answers (2026-11-08). Steve's wording where he gave it, polished. -->

<details>
<summary><b>How is this different from prompt optimization (DSPy, GEPA, etc.)?</b></summary>

Prompt optimizers tune the prompts inside a program. Harness Tuner tunes the whole agent you already have: its tools, skills, prompts, model and settings, against every eval you care about.

</details>

<details>
<summary><b>How does it compare to asking Claude Code or Codex to optimize your agent?</b></summary>

In a way, that's what it does, with the rigor and scale built in:

- a code loop runs the process, so it can't get off track
- real experiments, each one tracked
- batches of experiments planned and run in parallel
- many evals, each tracked on its own
- cost control: experiments run only the evals they target, winners merge, and ideas that aren't working stop early
- val and test sets withheld from the agents
- skills that guide your coding agent through each step

</details>

<details>
<summary><b>How does it compare to fine-tuning?</b></summary>

A tuned harness is easier to deploy (no GPUs, off-the-shelf models), easier to update when new product requirements come up, quicker to move to the latest model releases, and cheaper. Kiln supports fine-tuning too, and the tuner can use your fine-tuned models in advanced cases.

</details>

<details>
<summary><b>How does it compare to model routers?</b></summary>

A model router picks a model for each request, based on how hard it guesses the request is. Harness Tuner makes the task easier for the model, by giving your agent the tools and instructions it needs. That often lets every request go to a cheaper model, and it improves quality in ways a bigger model doesn't.

</details>

<details>
<summary><b>How does it rewrite tools without changing your code?</b></summary>

It doesn't touch your original tools. It writes wrappers: small code tools that call your existing tools and reshape what goes in and out. A wrapper can trim a response to the fields that matter, loop over a set of results, fold many calls into one, or do the math so the model doesn't have to. Your tools keep their current signature, so nothing outside Kiln changes.

</details>

<details>
<summary><b>How does it run experiments on multi-turn conversations?</b></summary>

Kiln builds you evals that use synthetic users. Each eval item sets a persona, a goal and a number of turns, and a model plays the user. The simulated user is the same for every candidate, so comparisons are fair, and its cost is tracked separately from your agent's.

</details>

<details>
<summary><b>Do the experiments need to run against production or staging?</b></summary>

Experiments call your agent's tools, so they need an environment where those tools can run. The best option is a synthetic world for your evals, built with [Kiln Seahaven](https://github.com/Kiln-AI/Seahaven). It gives you a safe, reproducible and highly parallel place to run them.

</details>

<details>
<summary><b>How do train, val and test keep the results honest?</b></summary>

The tuner's agents only ever see the train split. Val is used only to decide which candidates get promoted. Test is never read by the tuner: you run it for your final report, so the number you quote is one it never optimized against. This is enforced in code, not asked for in a prompt.

</details>

<details>
<summary><b>What about new model releases?</b></summary>

Run the tuner again with the new model available. It checks the new model against every eval, re-tunes the harness for it if needed, and builds on everything it learned in earlier runs. Upgrading becomes a routine run, not a project.

</details>

<details>
<summary><b>How does Harness Tuner use the Kiln app?</b></summary>

Harness Tuner runs on [Kiln](https://github.com/Kiln-AI/Kiln), our free app. Kiln provides:

- **Evals:** creating and running them ([Kiln evals](https://kiln.tech/features/evals)).
- **Experiment tracking:** the provenance of every experiment: what it changed, and which experiments it came from.
- **Views of the results:** a Compare view to compare experiments on evals, cost and speed, and an Experiment Lineage view to see how the tuning progressed.
- **Harness history:** every experiment's harness is kept, for future experiments and comparisons.

</details>

<details>
<summary><b>What does a run cost, and how long does it take?</b></summary>

It depends on the scope of the project. A small agent with a small eval can cost a few dollars. A large agent with hundreds of tools can cost several hundred. You set a budget and a time limit, and it stops starting new work when either runs out.

Harness Tuner does several things to keep costs down:

- **Targeted experiments:** each experiment runs only the evals it's trying to improve, and stops early when an idea isn't working.
- **No wasted runs:** if your evals can't detect a real win, it says so before spending anything.
- **Subscription agents:** the tuner's own agents run on your existing Claude Code, Codex or OpenCode subscription. It uses some of your quota, but that's usually much cheaper than per-token billing.

Evals need direct API access and can't run on a coding agent subscription, so their tokens bill to your provider keys in Kiln. Details: [docs/costs.md](docs/costs.md).

</details>

## Docs

- [How it works](docs/how-it-works.md): the whole loop, with a diagram.
- [Install](docs/install.md): every requirement, with a check and a fix for each.
- [Concepts](docs/concepts.md): waves, experiments, splits and the promotion gate. Read it before your first run's report.
- [Your own task](docs/your-own-task.md): what your task needs, and every CLI flag.
- [Objectives and model policy](docs/objectives.md): what to optimize, and which models it may use.
- [Costs](docs/costs.md): estimate a run before you start it.
- [Limitations](docs/limitations.md): what it doesn't do. Read it before planning a long run.
- [Architecture](docs/architecture.md): the wave loop, the gate, measurement, memory and the sandbox.
- [Agents](docs/agents.md): the ten agent roles and what each one does.
- [Agent CLIs](docs/agent-cli.md): how it runs on Claude Code, Codex and OpenCode.

## Contributing

See [CONTRIBUTING.md](.github/CONTRIBUTING.md).

## License

Harness Tuner is [MIT licensed](LICENSE).
