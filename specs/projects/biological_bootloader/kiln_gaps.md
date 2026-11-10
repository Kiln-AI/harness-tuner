# Kiln gaps (ranked)

A planning document for `biological_bootloader`. It is not part of the spec
dependency chain. The functional spec decides which gaps this project
closes.

**Source:** the research in
[research/harness-optimization](./research/harness-optimization/summary.md),
mainly the
[Kiln capability gap](./research/harness-optimization/kiln-capability-gap/summary.md)
subtopic, plus the owner's decisions of 2026-11-10. Kiln was read at `main`,
commit `3e67b49`. Unmerged work was not checked.

**Rank.** The rank is a draft for the owner to correct. It is the expected
value to the tuner against the cost to close. A gap the owner asked to close
ranks above one of similar value.

**Where.** *Tuner* means this repository: a gateway operation, a view field,
or teaching in a loop skill. *Kiln* means a change in Kiln itself, which this
repository can only track.

**Status.** *Confirmed* was read in code. *Inferred* was reasoned from code
or docs and was not run.

## Ranked gaps

| # | Gap | Where | Today | To close | Status |
|---|---|---|---|---|---|
| 1 | **Input transforms** | Tuner | The run config field passes through. No loop skill teaches it. It cannot be screened. The integrity check does not read the template. No template validation. | Teach it. Expose the validation operation. Make it screenable. Make the integrity check read the template. | Confirmed |
| 2 | **Sub-agents** (Kiln task as tool) | Tuner | Attach only. The gateway is bound to one task. A loop agent cannot create a sub-agent task, its prompt or run config, or the tool that calls it. | Operations to create and change a sub-agent and its tool. Teach when to use one and how to write its return contract. | Confirmed. Owner: close. |
| 3 | **Single-turn prompt caching** | Kiln | `automatic_prompt_caching` defaults to off. It is turned on only in multi-turn synthetic-user drives. A single-turn Claude task may get no caching, so the cost that Kiln measures would be wrong and the long-cached-prompt lever would not exist. | Confirm with one traced run. Fix in Kiln. Until then, the tuner must know that measured cost may not include caching. | Inferred. Owner: track. |
| 4 | **Task shape is not visible** | Tuner | `get_task` drops `turn_mode`. `list_available_models` drops the model capability flags. | Add the fields to the views. The "understand what you are optimising" step needs them. | Confirmed |
| 5 | **RAG** | Tuner | Full gap. No operation for documents, extractor, chunker, embedding, search type, reranker, RAG configs or a test search. No loop skill teaches RAG. An existing RAG tool can be attached. | Operations to create RAG configs and run a test search. Teach RAG versus skill and the order of RAG experiments. Extraction is done once and is not tuned. | Confirmed. Owner: close. |
| 6 | **Structured-output mode** | Tuner | Nine modes. Passes through. Barely taught. Not screenable, although a docstring says it is. | Teach it. Make it screenable. | Confirmed |
| 7 | **Jev for classifier-shaped tasks** | Tuner | Reachable as a model under policy. No loop skill names it. Kiln supports it for select tasks only (classifier shape). | Teach it as a candidate when the task has the shape. Name the shape rules. | Confirmed |
| 8 | **Tool-result clearing and compaction** | Kiln | Not in Kiln. The research rates tool-result clearing as a cheap, strong lever, separate from compaction. | Track. Compaction is planned. Clearing is not yet planned. | Confirmed |
| 9 | **OpenEnv worlds** | Tuner and Kiln | The data model, routes and runner are on main. There is no UI. The gateway drives world items as eval jobs, but a loop agent cannot list, create or change a world. The vendored API reference lacks the `/worlds` routes. | Operations to read and change worlds. A red flag for evals that run against a live or staging server. Refresh the vendored reference. | Confirmed. Owner: close. |
| 10 | **Multi-turn eval inputs** | Tuner | The `eval_inputs` v2 routes, multi-turn synthetic items and world resets are not reachable. The eval-build role (off by default) writes single-turn v1 items only. | Decide what the tuner must read. The tuner does not write evals: it sends the user to the eval builder. | Confirmed |
| 11 | **The `llm` tool inside code tools** | Tuner | A code tool can probably call the built-in `llm` tool: a cheap reader inside a wrapper. Not taught. | Confirm, then teach. | Inferred |
| 12 | **Prompt generators and few-shot** | Tuner | Reachable. Not taught. A few-shot generator may copy split items into the prompt. | Confirm the leak risk first. Teach only if safe. | Inferred |
| 13 | **OpenRouter routing controls** | Kiln | Kiln sets a fixed `provider.order` and never sends `zdr` or `sort`. This probably turns off Auto Exacto, and a zero-data-retention restriction cannot be enforced through OpenRouter. | Confirm. Track for the restrictions project. | Inferred |
| 14 | **Advisor pattern and mid-conversation switching** | Kiln | Not in Kiln. A cheap model that calls a stronger model as a tool can be approximated today with a sub-agent (gap 2). | Track. | Confirmed |
| 15 | **Run config fields** | Kiln | No `max_tokens`, seed, `tool_choice`, `top_k` or penalties. Some open models recommend `top_k` and a presence penalty. | Track. Low value: settings are a late, small lever. | Confirmed |
| 16 | **Other decision-model providers** | Kiln | Only Jev has a provider. Cloudflare Clef and others do not. This matters when a restriction excludes Jev. | Track. | Confirmed |
| 17 | **Code files in skills** | Kiln | Kiln skills read text only. They cannot run code. Code goes in code tools. | No action. The knowledge must state it. | Confirmed |

## Not closing

- **MCP servers.** The tuner does not connect, change or archive MCP
  servers. It can attach an existing one. Owner decision.
- **MCP run configs.** Refused by design: the agent runs outside Kiln and
  cannot be tuned.
- **Fine-tune creation.** Existing fine-tunes are model candidates. The
  tuner does not start one.
- **Eval writing.** The tuner sends the user to the Kiln eval builder.

## Not a Kiln gap

These came out of the research and belong to other parts of the spec:

- **Red-flag metrics the tuner does not compute yet**: repeated calls and
  cycles, unused tools and skills, cache ratio, context growth, a comparison
  of passing against failing items, and a report for the planner at the
  start of a run. The raw counts exist in `gateway/trace_files.py`.
- **Model and provider data for the loop agents.** Sources exist
  (Artificial Analysis, OpenRouter, models.dev, Kiln's model list). This is
  part of the model guide and the provider guide.
- **Model settings that a provider rejects**, such as temperature on current
  Claude models. The experiment builder learns these cheaply, so they are
  neither a gap nor knowledge.

## To confirm

- Gap 3: one traced single-turn Claude run, and whether LiteLLM marks the
  system prompt for caching.
- Gap 9: which OpenEnv work is not on main.
- Gap 11, gap 12 and gap 13: run each once.
