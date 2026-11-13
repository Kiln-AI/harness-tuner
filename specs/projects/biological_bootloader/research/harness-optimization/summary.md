# Research: Harness optimization

Research date: 2026-11-10. Baseline: `specs/projects/biological_bootloader/project_overview.md`.
Six subtopics, all complete. Most sources are from 2026.

## Bottom Line

The research supports the overview's direction on almost every lever. It also makes four corrections. First, many "almost always" statements in the overview are only conditional: prompt to skill, tool search, sub-agents, code execution and deep skill hierarchies each have a measured case where they lose. Write the skill as conditions plus experiments, not as defaults. Second, the overview names one lever where sources measure several: tool-result clearing apart from compaction, the advisor pattern, confidence cascades, tool removal, error-message design, reranking, and the (model, effort, provider) triple as the unit of model choice. Third, the Kiln gap is wider and different from the overview's picture. Jev and OpenEnv worlds are already on Kiln main. Kiln skills cannot run code. Sub-agents, MCP servers, RAG and worlds are attach-only for loop agents, and RAG is a full gap. Input templates and output modes can be set, but no skill teaches them. Fourth, red flags are mostly plain code. The tuner already counts most raw trace data, and LLMs are weak at locating failures in long traces (11% on TRAIL). For the spec: build the knowledge skill around conditional guidance with a date on each item, add gateway operations for sub-agents and RAG as the largest reach gaps, teach input templates and output modes, and make the red-flag report a code step that the planner gets before wave 1.

## Key Findings

- **Model and harness are a pair, and the candidate is a triple.** Four 2026 studies show model rankings reverse across harnesses (one moves Claude against GPT by 38 points). Effort level changes cost and latency more than sampling settings. The provider changes tool-call quality and structured-output behaviour. This confirms "lock the model, tune, re-scan", plus a short re-tune after a family change. ([model](./model-and-provider-selection/summary.md))
- **Sampling settings are mostly not levers on current Claude models.** A non-default temperature or top_p returns HTTP 400 on Claude 5.x, and Kiln always sends both. The tuner still teaches a `params` arm. ([model](./model-and-provider-selection/summary.md), [Kiln gap](./kiln-capability-gap/summary.md))
- **Skills help only when focused and opened.** Curated skills gave +16.6 pp on average. Comprehensive skills gave −2.9 pp. Skills the agent writes itself gave −8 to −12 pp. In one test the agent never opened the skill in 56% of cases. An always-loaded index scored 100%, against 79% for a skill. One level of disclosure is enough: a second level "never helps". ([instructions](./instructions-and-context/summary.md), [architecture](./architecture-levers/summary.md))
- **The tool surface is the strongest measured area.** Cap and shape results, merge call chains, cut tool count and overlap, and write errors as prompts. Tool count costs accuracy (2–5 pp at 40 tools against 13). Removing tools can win (Vercel: 18 tools to 2, 80% to 100%). Description fixes give +5.85 pp but +67% steps and a regression in 1 case in 6. ([tools](./tool-design-for-agents/summary.md))
- **Single agent by default.** Sub-agents help for context isolation. Above about 45% single-agent success they have negative returns, and they cost 3–15× tokens. The sub-agent hand-off caused 41.8% of failures in one study. ([architecture](./architecture-levers/summary.md))
- **Context clearing is a separate lever from compaction, and cheaper.** Clearing old tool results gave +29% (+39% with memory) and 84% fewer tokens. Compaction summaries cause measurable failures. ([architecture](./architecture-levers/summary.md))
- **RAG size is measured in tokens, not documents.** Below about 200k tokens, put the corpus in a cached prompt. Reranking was positive in all 240 cells of a factorial study. Chunking and hybrid fusion change sign with the dataset. ([architecture](./architecture-levers/summary.md))
- **Decision models are real, very cheap and less accurate.** Jev: $0.042 per million input tokens, 70–500 ms, 67.8% agreement with frontier labels against 74.1% for a frontier LLM (vendor evals). Decomposed sub-questions reached 95.0%. Jev is shipped on Kiln main as provider `typesafe`. ([architecture](./architecture-levers/summary.md), [Kiln gap](./kiln-capability-gap/summary.md))
- **Code finds the place, an LLM names the cause.** Code can compute oversize results, error classes, loops, context growth, cache hit rate, unused tools and call chains. Published thresholds exist only as vendor product limits (for example 10k/25k tokens per MCP result; OpenHands and Gemini CLI loop counts). ([traces](./trace-diagnostics-and-red-flags/summary.md))
- **Machine-readable model data exists** (Artificial Analysis API, OpenRouter `/models` and `/endpoints`, models.dev, Kiln's model list). The gateway gives loop agents only names, thinking levels and an output mode. ([model](./model-and-provider-selection/summary.md))

## Implications

### (a) Where the research contradicts or qualifies the overview

| Overview | Finding | Subtopic |
|---|---|---|
| 6.2 Long prompt into a skill is "usually the right direction" | Conditional on how often runs need the content and on trigger rate. Index-in-prompt often wins. | instructions |
| 6.2 Small models need progressive disclosure | They need *focused* context, but are weak at navigating. Code-side routing may be better. | instructions |
| 6.2 / 6.3 Several-level skill trees, leaf per tool | Anthropic and a controlled study: one level is enough; deeper levels can hurt. | instructions, architecture |
| 6.2 Skill reads miss the cache because of read order | Main reason is position: the skill body follows the per-run user message. | instructions |
| 6.2 Possibly code files in skills | Kiln skills cannot run code. Code goes in code tools. | Kiln gap |
| 6.3 A tool-listing skill is "almost always better" than tool search | Untested. Tool search beats loading a large catalog (49% to 74%). It is not worth it under ~10 tools. | tools |
| 6.3 Wrappers and code are the fix | They win on data-heavy and chained tasks. Direct tools win on small clean sets; some model families lose under code mode. | tools |
| 6.4 Temperature, top-p as settings to scan | Locked on current Claude models (400). Open models recommend 1.0. The DeepSeek low-temperature case was not confirmed. | model |
| 6.4 Gemini much better at PDFs | Contested; confidence intervals overlap. | model |
| 6.4 Opus best at coding | Partly; the Fable tier ranks above, and the order depends on the harness. | model |
| 7.1 A designed hierarchy beats chunks | The size measure is tokens; below ~200k put it in the prompt. Deep hierarchies hurt. | architecture |
| 7.4 Decision models "capable"; more uses "not shipped" | Quality gap on vendor evals. Jev is shipped on Kiln main. Only Jev has a Kiln provider. | architecture, Kiln gap |
| 7.7 OpenEnv not all merged | Data model, tool ids, routes and runner are on main. The unmerged part is unknown. | Kiln gap |
| 7.8 The tuner does not write evals | An `eval_build` role exists, off by default. It writes single-turn v1 items only. | Kiln gap |
| 6.2 Long cached prompt as a lever | Inference from code: Kiln enables Anthropic caching only in multi-turn synthetic-user drives. A single-turn Claude task may get no caching. | instructions |
| 3 "Give it a very large amount of context first" | Anthropic: start minimal. Generic context costs ~20% with no gain. The skill must pick a default and say why. | instructions |

### (b) Levers and capabilities missing from the overview

- **Instructions:** skill trigger rate as the first measurement; the index-in-prompt pattern; code-side routing; prompt debt after a model change; cache hygiene rules (static first, no per-run values, stable tools, per-model minimum length); self-written skill content as a known failure; rationale next to each rule for the maintenance skill.
- **Tools:** tool count and overlap as a smell; tool removal and scoping; error-message design; opaque ids; a verbosity enum; parameter design; tool-use examples; namespacing; routing in the harness; identity binding and write tiers in wrappers.
- **Architecture:** the advisor pattern (Anthropic API beta); confidence cascades; tool-result clearing and memory; compaction-summary failures; reranking, top-k and contextual retrieval; the sub-agent return contract; dynamic few-shot; RL fine-tuning of decision models; eval-set size for RAG decisions (a 10-item result reversed at 100).
- **Model and provider:** the (model, effort, provider) triple; host-dependent quality of open models; the first-week rule; cost and time per task as measures; model- and feature-level ZDR exceptions; vendor migration guides as a source; a row type for decision models.
- **Kiln features:** the `llm` built-in tool inside code tools (a cheap reader); prompt generators, including few-shot (possible split leak, untested); chain-of-thought instructions; MCP run configs (measurable, not tunable); subject memory tools; the RAG tool's name, description and tag filter; semantic chunking; nine output modes, not three; world concurrency and versioning red flags; fine-tune `task_filter`.
- **Red flags:** repeats and cycles, unused tools, cache ratio, context growth, distributions, pass-against-fail comparison, and a start-of-run report to the planner. The tuner computes raw counts today (`gateway/trace_files.py`, `probes.py`); the overview does not say so.

### (c) Kiln gaps, grouped

- **Known and reachable:** model and provider (under policy); prompt; thinking level; temperature and top_p (but see the Claude 400 issue); code tools; skill bundles; tool attachment by id; fine-tunes and Jev as models (Jev is reachable but no skill names it).
- **Reachable but not taught (pass-through or named only):** `input_transform`; `structured_output_mode` (also not screenable, against its docstring); prompt generators and few-shot; chain-of-thought instructions; the `llm` tool in code tools (inferred); Jev.
- **Attach only:** Kiln task tools (sub-agents; each pins one run config); MCP servers; RAG search tools; world tools; memory tools.
- **Not reachable:** creating or editing sub-agents or run configs on another task; connecting MCP servers; all RAG objects (documents, extractor, chunker, embedding, search type, reranker, configs, test search); worlds API; eval inputs v2, multi-turn items, world resets; template validation; model capability flags (dropped by `list_available_models`); task `turn_mode` (dropped by `get_task`); eval builder and synthetic data; MCP run configs (refused by design); fine-tune creation and prompt optimizer (out of scope by design).
- **Not in Kiln main:** `max_tokens`, seed, `tool_choice`, `top_k`, penalties; compaction; context editing; automatic mid-conversation switch; OpenRouter `zdr` and `sort` (Kiln sets a fixed `provider.order`, which probably turns off Auto Exacto; untested).

### Other spec constraints

- `analysis.py` says "no verdict labels". Red flags conflict with that rule. The traces subtopic proposes stating each flag as a fact plus a reference number and its source.
- Volatile model and provider rows cannot go in `agent_files/` (repository rule). They must be fetched or generated per run.
- The "no Chinese models" restriction has no machine-readable source field. It needs a manual mapping.

## Conflicts and Uncertainty

### (d) Cross-subtopic conflicts and duplicates

- **Prompt caching** appears in four subtopics. They agree on mechanics: caching is a prefix match; skill reads, tool-list changes, context clearing and model switches all break it. The conflict is with Kiln: if Kiln does not cache single-turn Claude calls, the overview's cached-prompt argument and the architecture advice "below 200k tokens, put the corpus in a cached prompt" lose their cost basis, and the traces subtopic's cache-hit-rate flag reads zero. The Kiln-gap subtopic did not check caching. This is the most important open conflict.
- **Tool search.** The tools subtopic says it beats a large catalog. The traces subtopic cites Anthropic's advice to use it at 10+ tools or 10k+ tokens of definitions. Both qualify the overview's "side skill is almost always better". No source compares the two.
- **Tool descriptions.** Tools and traces both cite the same study (+5.85 pp, +67% steps). A duplicate, consistent. The red-flag check must flag both too-short and too-long descriptions.
- **Disclosure depth.** Instructions (Anthropic's one-level rule) and architecture (controlled study) agree independently. Both qualify the overview's multi-level sketch.
- **Decision models.** Architecture identifies the products and their accuracy. Kiln gap finds only Jev reachable. Model selection asks for a separate row type. Consistent. Clef and the OpenAI Decisions API are not in Kiln.
- **Stack rank.** Only the traces subtopic drafts one (environment and eval, context flood, loops, tool surface, missing context, prompt spec, output format, cache, hyperparameters). It is a synthesis; no source publishes an order. Tools ranks 17 smells on a different axis. Output format may belong at rank 1 for a structured-output classifier. The overview's model lock comes before all of it.
- **Small models.** Instructions say they need focused context and fail at navigation. Model selection says harness fixes do not transfer between small models, but a harness evolved on a weak model lifts a strong one. Not a conflict; both support "lock the model first".
- **Taught but broken.** The Kiln gap marks temperature and top_p as taught and reachable. Model selection finds they return 400 on current Claude models. The skill must state this per model family.

### Evidence quality

Many numbers come from vendors or small studies (5–30 tasks). Decision-model accuracy is almost all vendor data, in an eight-week-old category. Thresholds come from coding agents on frontier models, not cheap recurring-task agents. Several paper figures are second-hand, because Tavily was rate-limited and Exa failed for part of the run. Each subtopic marks these.

## Gaps

- **Kiln single-turn caching** is an inference from code, not confirmed by a live run or by the Kiln-gap subtopic. Close it with one traced single-turn Claude run and a check of whether LiteLLM marks the system prompt.
- **No direct comparisons** for: cached long prompt against a skill walk; a tool-listing skill against tool search; a wrapper layer against the raw API; truncate-with-notice against refuse-and-narrow. Harness Tuner's own runs are the only practical source.
- **No published stack rank or thresholds** for error rate, turns per task, context growth or prompt length. Record which red flags each promoted win removed, in the state folder's `reports/`.
- **No document-count threshold** for RAG against skill (token thresholds only). **No study of field removal** for LLM classifiers.
- **Unconfirmed Kiln items:** the unmerged OpenEnv work (shallow clone, main only); the few-shot split leak; the `llm` tool in code-tool allowlists; Jev behind a Kiln task tool; whether OpenRouter `provider.order` turns off Auto Exacto; whether `cached_tokens` is filled per provider.
- **Not run:** the Artificial Analysis, OpenRouter and models.dev APIs. Field lists come from docs.
- **Out of scope:** GEPA, DSPy and other optimisation systems. The owner will supply a separate report.

## Subtopics

- [Instructions and context](./instructions-and-context/summary.md) — prompts, skills, caching and skill maintenance; focused, opened skills help, and an always-loaded index often beats a skill.
- [Tool design for agents](./tool-design-for-agents/summary.md) — agent tools against APIs, tool count, search, code mode, SQL, 17 smells; confirms "wrap, do not rewrite" and qualifies tool search and code mode.
- [Architecture levers](./architecture-levers/summary.md) — sub-agents, compaction, RAG, decision models, input transforms, routing, fine-tuning; single agent first, plus the advisor pattern and cascades.
- [Model and provider selection](./model-and-provider-selection/summary.md) — benchmarks, live data sources, settings, providers, guide shapes; the candidate is a (model, effort, provider) triple.
- [Trace diagnostics and red flags](./trace-diagnostics-and-red-flags/summary.md) — taxonomies, error analysis, trace metrics, static checks, a draft stack rank; most red flags are plain code.
- [Kiln capability gap](./kiln-capability-gap/summary.md) — what Kiln can tune against what loop agents know and reach; sub-agents, MCP, RAG and worlds are attach-only, and Jev and worlds are on main.
