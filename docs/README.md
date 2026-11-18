# Kiln Harness Tuner documentation

This page lists every document in this folder. The
[project README](../README.md) has the overview, the quickstart and the FAQ.

## Start here

Read these documents before your first run.

- [Install](install.md): every requirement, with a check and a fix for each.
- [Your own task](your-own-task.md): what your Kiln task needs, and how the
  first run starts.
- [Concepts](concepts.md): waves, experiments, splits and the promotion gate.
  Read it before you read the report of a run.

## Reference

Use these documents when you plan or start a run.

- [CLI reference](cli.md): every command and flag, with its default.
- [Objectives and models](objectives.md): what a run improves, and which
  models it can use.
- [Costs](costs.md): how to estimate a run before you start it.
- [Limitations](limitations.md): what the tuner does not do. Read it before
  you plan a long run.

## How it is built

Read these documents to understand the tuner or to change its code.

- [How it works](how-it-works.md): the whole path of a run, with a diagram.
- [Architecture](architecture.md): the wave loop, the gate, measurement,
  memory and the sandbox.
- [Agents](agents.md): the agent roles, and what each one does.
- [Agent CLIs](agent-cli.md): how the tuner runs on Claude Code, Codex and
  OpenCode.

## Contribute

[CONTRIBUTING.md](../.github/CONTRIBUTING.md) has the setup and the checks
that CI runs.
