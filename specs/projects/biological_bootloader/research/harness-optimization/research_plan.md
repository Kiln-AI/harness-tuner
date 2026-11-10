# Research Plan: Harness Optimization

## Goal

Research for the `biological_bootloader` project. It feeds Step 2, the
functional spec. The research has two purposes. The first is to collect what
other practitioners know about making agents better, as source material for
the harness-optimisation skill and its order of experiments. The second is
to find holes: techniques that work and that Kiln or Harness Tuner does not
support, and Kiln capabilities that the tuner cannot reach.

The project overview is the baseline:
`specs/projects/biological_bootloader/project_overview.md`. Every subtopic
reports what **confirms** it, what **contradicts** it, and what it
**misses**. Findings are dated, because this field changes monthly. Sources
from the last 12 months are preferred.

## Run

- Model: Opus (subtopic agents and the summary agent)
- Web tools: Tavily and Exa (search and extract), WebSearch

## Out of scope

Competitive research on other optimisation systems (GEPA, DSPy and similar)
is out of scope. This repository is about our own ideas. The owner will
supply a separate report on that subject.

## Subtopics

- [x] Instructions and context — prompts, skills, progressive disclosure, caching, context loading, and how to author and maintain skills
- [x] Tool design for agents — agent-optimised tools, wrappers, response size, tool search, code execution, tool smells
- [x] Architecture levers — sub-agents, RAG versus skills, compaction, decision models, input feature engineering, fine-tuning
- [x] Model and provider selection — current guidance, benchmarks, live data sources, provider speed and data retention
- [x] Trace diagnostics and red flags — failure taxonomies, error analysis, metrics that software can compute from a harness and its traces
- [x] Kiln capability gap — what Kiln can tune, against what Harness Tuner knows about and can reach

## Focus Details

### Instructions and context

What do the best current sources say about the instruction layer of an agent
harness? Cover system prompt design for agents, agent skills and progressive
disclosure, when a long cached prompt beats a skill tree and the reverse,
prompt caching mechanics that affect harness design, how this changes with
model size, and loading context up front instead of leaving the agent to
request it. Include a second part on **skill authoring and maintenance**:
published rules for writing good skills and agent instruction files, and how
teams keep a growing knowledge base for agents consistent as it is edited
(placement of new material, conflicts with older guidance, deprecation).
This part feeds the maintenance skill in the overview. Tools belong to "Tool
design for agents". Sub-agents and compaction belong to "Architecture
levers".

### Tool design for agents

What makes a tool good for an agent as opposed to a good software API? Cover
published guidance on writing tools for agents, MCP server design lessons,
response size and token efficiency, pagination and filtering, error messages
that steer the agent, combining call chains into one tool, wrapper layers
over fixed enterprise APIs, tool descriptions, tool count and namespacing,
tool search against alternatives, code execution or "code mode" in place of
many tool calls, and guidance for SQL and GraphQL tools (sample queries,
schema docs, guard rails). Collect a list of **tool smells** with the
evidence for each. Instruction-layer questions belong to "Instructions and
context". Detection of smells in traces by software belongs to "Trace
diagnostics and red flags".

### Architecture levers

For each structural lever, when does it help, when does it hurt, and what
evidence exists? Cover sub-agents and multi-agent designs against one
context with compaction; compaction and context editing settings; RAG
against agentic search and skills, including document-count thresholds and
which RAG parameters matter most (chunking, embedding, hybrid search,
reranking); small decision or classifier models for classification tasks
(Jev and similar "decision model" products); input transforms and feature
engineering for LLM classifiers; model or thinking-level routing during a
conversation; and when fine-tuning is worth it after harness work. Flag any
lever that sources rate highly and the overview does not mention. Model
choice itself belongs to "Model and provider selection".

### Model and provider selection

How should an optimiser choose candidate models and providers today, and how
can that knowledge stay current? Cover which benchmarks and leaderboards are
credible for agentic and tool-calling work, **machine-readable sources** the
tuner could query at run time (pricing, speed, context size, capability),
known strengths by model family and task type, model-specific settings from
model cards (temperature, structured output mode, thinking level), evidence
that the best harness differs by model size, and provider facts: inference
speed, zero data retention, routing gotchas on aggregators such as
OpenRouter. The output is a draft shape for a model guide and a provider
guide, with the sources to refresh them, not a claim of final rankings.

### Trace diagnostics and red flags

What can be detected about a weak harness from its configuration and its
traces, and how much of that can be plain software? Cover agent failure
taxonomies, error-analysis practice for LLM systems, observability metrics
for agents (tokens per tool result, tool error rates, repeated calls, loops,
turns per task, context growth, cache hit rate, unused tools and skills),
static checks on a harness (prompt length, tool description length, tool and
skill counts, skill structure), and any published thresholds. Separate what
code can compute from what needs an LLM. Collect guidance on which problems
block others, as input to the stack rank. Eval design itself is out of
scope.

### Kiln capability gap

This subtopic reads code and docs more than the web. Build a table of every
harness capability Kiln has, and for each: whether Harness Tuner's loop
agents know about it (the skills in
`src/kiln_harness_tuner/agent_files/skills/`), and whether they can reach it
(the typed gateway operations in `src/kiln_harness_tuner/gateway/`, since
loop agents have no generic Kiln API). Sources: the vendored API reference
in `agent_files/skills/kiln-api/references/api_docs/`, the Kiln docs at
docs.kiln.tech, and the Kiln repository, cloned at `/home/user/kiln-ai/kiln` (open source, main
branch). Cover at least run
config fields and model settings, structured output modes, skills (and
whether they support code files), code tools, MCP tool servers, Kiln task as
tool (sub-agents), RAG configs (extractor, chunker, embedding, search type,
reranker), input templates and feature engineering, fine-tunes as models,
decision models, OpenEnv environments, synthetic users and multi-turn evals,
and the eval builder. Also list the task-definition fields that are
**degrees of freedom** for classifying an agent (single or multi-turn,
structured output and others). Mark anything that is on an unmerged branch
or could not be confirmed.
