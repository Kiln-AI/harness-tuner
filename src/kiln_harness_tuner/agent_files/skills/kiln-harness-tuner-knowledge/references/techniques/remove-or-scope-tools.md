---
title: Remove or scope tools
description: Drop the attached tools that this task does not need, or that overlap, so the agent chooses among fewer tools and reads fewer descriptions on every call.
reviewed: 2026-11-12
status: active
levers:
  - tool_config
applies_to:
  - tools
cost: low
---

# Remove or scope tools

## When to use

- The agent has many tools, and the traces show that it calls only a few
  of them on this task.
- Two or more tools do the same job, and the traces show the agent pick
  the wrong one or switch between them.
- A tool is attached for work that this task does not do: a general tool
  set, a tool of another product area.
- The tool descriptions are a large part of each call, and the cost or
  the latency per item is high.

## When not to use

- **The tool is used on some items**, even if not on most. Read the train
  traces of every eval before you drop a tool. A tool that one class of
  item needs is not noise.
- **The tool is a guard or a safe path**: a read-only variant, a tool that
  checks a rule. Dropping it can open a path that the product does not
  allow.
- **The overlapping tools each do part of the job well.** Dropping one
  leaves the agent with a worse tool for that part:
  [consolidate tools](consolidate-tools.md).
- **The agent has few tools.** There is little to gain, and each arm costs
  a measurement.

## Alternatives

- [Prune one group at a time](prune-one-group-at-a-time.md), when it is
  not known which tools help, and one measurement for each group is
  affordable.
- [Consolidate tools](consolidate-tools.md), when the tools overlap and
  each is needed for part of the work.
- [New tool description](new-tool-description.md), when the tools are all
  needed and the agent confuses two of them.

## How

The change is the list of attached tools on a new run config (the lever
[tool_config](../levers/tool_config.md)). It adds no tool and changes no
tool.

1. **Count the calls of each tool** in the train traces of every eval.
   List the tools with no call, and the pairs of tools that do one job.
2. **Choose the scope.** Keep the tools that the traces use. Drop the
   tools with no call, and, of a pair with one job, the one that the
   agent uses less or uses wrongly.
3. **One reason on each arm.** Drop the unused tools on one arm, and an
   overlapping tool on another, so that each result tells which change
   moved it.
4. **Remove each mention** of a dropped tool in the prompt and in the
   attached skills. The reference check finds a name that points at
   nothing.
5. **A wrapper keeps its own calls.** A code tool can still call a tool in
   its allowlist when that tool is not attached. Dropping a tool from the
   model's view does not remove it from a wrapper.

## Check that it took effect

Read the new run config against the parent: only the named tools are
gone. The reference check finds no dangling name. A smoke on two or three
train items shows no call to a dropped tool and no attempt to find one.

## Budget

Low cost: one run config and one measurement. A good first step on any
budget when the traces show tools that are never called, because each
later measurement is then cheaper.

## If it does not move the evals

Keep it. A smaller tool list with the same evals costs less on every call
and leaves the agent fewer wrong choices. The condition: no eval goes
down, the guard evals included.

## Priors

- **A text-to-SQL agent for internal analytics, on a frontier model.** Its
  17 specialised tools (schema lookup, query validation, planning, result
  formatting and others) were cut to the SQL tool alone, and one tool was
  added that runs shell commands over the files of a documented semantic
  layer. On 5 representative queries, success went from 4 of 5 to 5
  of 5, the agent was 3.5 times faster, and it used 37% fewer tokens. The
  author says that it worked only because the semantic layer was already
  good documentation. One sample, not a rule.
  Source: public, the Vercel post "We removed 80% of our agent's tools".
  2025-12-22.
- **A coding agent in an editor, on two models.** The default set of about
  40 built-in tools was cut to a core of 13, with routing in the harness
  that shows the other tools when they are needed. On two coding
  benchmarks, the success rate rose by 2 to 5 points. One sample, not a
  rule.
  Source: public, the GitHub post "How we're making GitHub Copilot smarter
  with fewer tools". 2025-11-19.

## Confidence

Medium, that fewer tools help when many are unused or overlap: two public
cases, and public guidance from tool vendors that agrees. In both cases the
removal came with another change (a new general tool, or routing), so the
cases do not isolate the removal. Low, for an agent with few tools: no
case here measures it.
