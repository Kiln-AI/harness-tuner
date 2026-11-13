# Tool count, tool search, and code execution

Researched 2026-11-10. This file covers three linked questions: how many
tools an agent should see, how to give it more tools without loading them
all (tool search and its alternatives), and when code execution should
replace many tool calls.

## 1. Tool count: the evidence

Every source we found reports that accuracy falls as visible tool count
grows. The size of the effect and the threshold vary.

| Source | Date | Setting | Finding |
|---|---|---|---|
| [OpenAI function calling guide](https://developers.openai.com/api/docs/guides/function-calling) | current | guidance | "Aim for fewer than 20 functions available at the start of a turn at any one time, though this is just a soft suggestion." Hard limit 128 tools per request ([via SixDegree](https://sixdegree.ai/blog/mcp-tool-overload)). |
| [Claude Agent SDK tool search docs](https://code.claude.com/docs/en/agent-sdk/tool-search) | current | guidance | "Tool selection accuracy degrades with more than 30-50 tools loaded at once." "50 tools can use 10-20K tokens." |
| [GitHub Copilot, fewer tools](https://github.blog/ai-and-ml/github-copilot/how-were-making-github-copilot-smarter-with-fewer-tools/) | 2025-11-19 | SWE-Lancer, SWE-bench Verified, GPT-5 and Sonnet 4.5 | Full ~40 built-in tools cost "a 2–5 percentage point decrease in resolution rate". Cut to 13 core tools plus four "virtual" groups. 190 ms faster TTFT, 400 ms faster completion. The agent with too many tools "ends up ignoring explicit instructions, using tools incorrectly, and calling tools that are unnecessary". |
| [GitHub MCP server discussion #1182](https://github.com/github/github-mcp-server/discussions/1182) | 2025-10-06 | production | 101 tools = 64.6k tokens by default; new default 52 tools = 30.3k tokens. "LLMs struggle to choose the right tool from too many options." |
| [GitHub MCP tool-specific config](https://github.blog/changelog/2025-12-10-the-github-mcp-server-adds-support-for-tool-specific-configuration-and-more/) | 2025-12-10 | production | Loading 3–10 most-used tools gives "~60-90% reduction in context window usage". |
| [SixDegree, 150 tools](https://sixdegree.ai/blog/mcp-tool-overload) | 2026-03-22 | 6 models, 60 prompts, 25–150 tools | All models degraded; "the degradation starts between 25 and 50 tools". Drops are modest (for example Grok 4.1 Fast 86.7% → 76.7%) but larger on ambiguous prompts (Grok 4 at 50% at 100 tools). OpenAI models failed outright at 150 (the 128 limit). Similar services (Jira/Linear, Datadog/Grafana) are "a permanent source of confusion". Small vendor study. |
| [Superhuman, enterprise routing](https://arxiv.org/html/2606.17519) | 2026-06-16 | 584-tool production catalog, 3 frontier models | Routing F1 on under-specified requests drops 16–23 pp from 10 to 110 agents. With perfect retrieval the ceiling still drops 10 pp ("confusion gap"). Embedding shortlisting to k=20 recovers +10–11 pp; worth it above ~180 tools. |
| [Anthropic advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) | 2025-11-24 | internal MCP evals | With tool search on a large library: Opus 4 49% → 74%, Opus 4.5 79.5% → 88.1%. "The most common failures are wrong tool selection and incorrect parameters, especially when tools have similar names." Five common servers = 58 tools ≈ 55K tokens; Anthropic saw 134K tokens of definitions internally. |
| [RAG-MCP](https://www.emergentmind.com/papers/2505.03275) | 2025-05 | up to 11,100 tools | Full-context selection 13.62% vs retrieval 43.13%. |
| [How many tools should an agent see?](https://arxiv.org/html/2605.24660v2) | 2026-06 | BFCL, ToolBench, Sonnet 4.6 | Shorter adaptive lists (mean 2.2) gave 93.1% choice accuracy vs 87.1% with a fixed 5, when the right tool was present. |
| [Microsoft tool-space interference](https://www.microsoft.com/en-us/research/blog/tool-space-interference-in-the-mcp-era-designing-for-agent-compatibility-at-scale/) | 2025-09-11 | 1,470 servers | Most servers have four or fewer tools; outliers have 256; GitHub MCP had 91. Cites "up to 85%" performance loss for some models with large tool spaces. |
| [Skill shadowing](https://arxiv.org/html/2605.24050) | 2026-06-23 | SkillsBench, Haiku 4.5, Sonnet 4.6 | The same effect for skills: up to 21% pass-rate drop from a small helpful set to 202 skills. The cause is wrong selection ("shadowing"), not extra context. |

Notes:

- **Overlap matters more than count.** Anthropic, OpenAI's data agent,
  SixDegree and the Superhuman paper all point at *similar or overlapping*
  tools as the mechanism. OpenAI's internal data agent: "we exposed our
  full tool set to the agent, and quickly ran into problems with
  overlapping functionality … we restricted and consolidated certain tool
  calls" ([Inside OpenAI's data agent](https://openai.com/index/inside-our-in-house-data-agent/),
  2026-01-29). Anthropic: "If a human engineer can't definitively say
  which tool should be used in a given situation, an AI agent can't be
  expected to do better"
  ([Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents),
  2025-09-29).
- **Fewer, more general tools can win outright with a strong model.**
  Vercel cut its text-to-SQL agent d0 from 18 specialised tools to two (a
  bash tool over the semantic-layer files, and an SQL tool) on Claude Opus
  4.5: success 80% → 100%, 274.8 s → 77.4 s, ~102k → ~61k tokens, ~12 → ~7
  steps ([Vercel, 2025-12-22](https://vercel.com/blog/we-removed-80-percent-of-our-agents-tools)).
  Caveats: only 5 benchmark queries; Vercel says "This only worked because
  our semantic layer was already good documentation." A sales-summary agent
  went from ~$1.00 to ~$0.25 per call with the same idea
  ([Vercel](https://vercel.com/blog/how-to-build-agents-with-filesystems-and-bash)).
- **Tool removal is an experiment the overview does not list.** The
  overview frames tools as additions and wrappers. Copilot, GitHub MCP,
  OpenAI and Vercel all report gains from *removing* tools.

## 2. Tool search and its alternatives

### What exists (November 2026)

- **Anthropic Tool Search Tool** (beta 2025-11-24): mark tools
  `defer_loading: true`; a regex or BM25 search tool (or a custom one)
  returns references that expand to full definitions. Recommended when
  definitions exceed 10K tokens, when there are 10+ tools, or when
  selection is poor. "Less beneficial when: small tool library (<10
  tools); all tools used frequently in every session; tool definitions are
  compact." "Keep your three to five most-used tools always loaded, defer
  the rest." Add a system-prompt line that names the tool families
  ([source](https://www.anthropic.com/engineering/advanced-tool-use)).
  Deferred tools do not break prompt caching because they are appended,
  not prepended.
- **OpenAI `tool_search`** (gpt-5.4 and later): hosted or client-executed;
  new tools "are injected at the end of the context window" to preserve the
  cache. OpenAI's own trade-off table: deferred loading "adds a step and
  depends on finding the relevant tool"
  ([OpenAI tool search](https://developers.openai.com/api/docs/guides/tools-tool-search)).
- **Claude Code / Agent SDK**: tool search is now on by default for
  supported models; `ENABLE_TOOL_SEARCH=auto` defers only when deferrable
  definitions reach 10% of the context window; up to five tools load per
  search; "With fewer than ~10 tools … loading everything upfront is
  typically faster" ([docs](https://code.claude.com/docs/en/agent-sdk/tool-search)).
- **Virtual tool groups** (Copilot): related tools behind one expandable
  group, "think of these as directories". Plus embedding-guided routing that
  pre-loads likely tools before the model asks. Tool Use Coverage: embedding
  94.5%, LLM-based selection 87.5%, static list 69.0%
  ([GitHub, 2025-11-19](https://github.blog/ai-and-ml/github-copilot/how-were-making-github-copilot-smarter-with-fewer-tools/)).
- **Pre-inference shortlisting** by embeddings (Superhuman paper): the
  harness, not the model, picks ~20 tools per request. The paper reports
  tool-level retrieval "consistently outperforming all pack-level approaches
  including platform tool search and hierarchical LLM routing".
- **Static scoping**: GitHub MCP toolsets and the `X-MCP-Tools` header;
  OAuth-scope filtering hides tools the token cannot use.
- **Masking, not removing** (Manus): keep all definitions stable for the KV
  cache and constrain choice by logit masking or name-prefix prefill.
  "Unless absolutely necessary, avoid dynamically adding or removing tools
  mid-iteration", because it breaks the cache and confuses the model when
  history refers to a missing tool
  ([Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)).
- **A skill or document that lists the tools** (the overview's preferred
  answer). Anthropic's own advice to add "system prompt guidance so Claude
  knows what's available" is a small version of this.

### Known failure modes of model-driven tool search

These support the overview's smell ("the agent must imagine that a tool
exists, decide to search, and pick the right terms"):

- Ranking misses exact names. Claude Code issue
  [#66488](https://github.com/anthropics/claude-code/issues/66488): search
  for `find symbol` fails to surface `find_symbol`; "Claude never searches
  for more than 5 matches on its own". Codex issue
  [#21503](https://github.com/openai/codex/issues/21503): BM25 drops an
  exactly named tool; "the agent may conclude an MCP tool is unavailable
  when it is actually installed".
- Stale index. Claude Code issue
  [#66084](https://github.com/anthropics/claude-code/issues/66084): tools
  registered after connect never become searchable.
- Extra round trips and cache misses. Copilot saw the model open search
  tools, then docs tools, then git tools before the merge tool: "Each
  incorrect group lookup adds latency and overhead".
- Loaded tools fall out after compaction and must be searched again (Agent
  SDK docs).

### Verdict against the overview

- **Confirms**, in part: tool search has real, documented failure modes, and
  the vendors themselves say it is not worth it below ~10 tools or when
  tools are used in every session.
- **Contradicts** the strong form "a side skill … is almost always better":
  we found no study that compares a tool-listing skill with tool search.
  The published comparisons are tool search vs *loading everything*, and
  tool search wins large on big catalogs (Anthropic 49% → 74%). The best
  measured approaches put selection in the harness (embedding pre-routing,
  shortlisting, static scoping), not in a model's free-text search or a
  skill read.
- **Missing** from the overview: harness-side tool routing, static scoping
  per agent, keeping definitions stable for the cache, and the plain option
  of removing tools.
- Inference for Harness Tuner: for a *recurring* agent (the overview's
  second class), the tool set is known in advance. The first experiments are
  scoping (remove tools the traces never use) and consolidation, not search.
  Tool search matters when the client agent connects whole third-party MCP
  servers. Whether Kiln exposes `defer_loading` or any tool-search option in
  a run config is a question for the Kiln-gap subtopic; we found no such
  field in `kiln_ai/tools/` on a quick search.

## 3. Code execution in place of many tool calls

### The claims

- Anthropic, [Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)
  (2025-11): present MCP servers as a code API (one file per tool); the
  agent reads only the files it needs and filters data in the sandbox.
  Example: "150,000 tokens to 2,000 tokens—a time and cost saving of
  98.7%". Also privacy (PII tokenisation in the client), state in files,
  and saved functions as skills. Caveat stated by Anthropic: "Running
  agent-generated code requires a secure execution environment …
  operational overhead and security considerations that direct tool calls
  avoid."
- Cloudflare, [Code Mode](https://blog.cloudflare.com/code-mode/)
  (2025-09-26): "LLMs are better at writing code to call MCP, than at
  calling MCP directly", because they "have seen a lot of code" and few real
  tool calls. Follow-up, [Code Mode MCP server](https://blog.cloudflare.com/code-mode-mcp/)
  (2026-02-20): the whole Cloudflare API (2,594 endpoints) behind two tools,
  `search()` and `execute()`, ~1,000 tokens; native MCP would be ~1.17M
  tokens with full schemas or ~244K with required parameters only
  ([docs](https://developers.cloudflare.com/agents/model-context-protocol/cloudflare/servers-for-cloudflare/)).
  Cloudflare marks Code Mode "experimental".
- Anthropic **Programmatic Tool Calling** (beta 2025-11-24): Claude writes
  Python that calls opted-in tools; only stdout returns. Average tokens
  43,588 → 27,297 (−37%) on complex research tasks; internal knowledge
  retrieval 25.6% → 28.5%; GIA 46.5% → 51.2%. Use it for large data,
  "three or more dependent tool calls", filtering, and fan-out. "Less
  beneficial when: making simple single-tool invocations … quick lookups
  with small responses."

### Independent measurements (2026)

| Study | Setup | Result |
|---|---|---|
| [Gatana](https://www.gatana.ai/blog/code-mode-vs-direct-tool-calls-benchmark), 2026-10-05 | DeepSeek-V4.1-Flash, 3 tasks × 5 runs | Large-data tasks: direct 2/10 correct, code mode 10/10, 4–44× fewer prompt tokens, 17–51× less time. Small task: both 5/5, direct slightly faster. "The model fetched the data. It could not do the arithmetic on it." Vendor study. |
| [SOTAAZ](https://sotaaz.com/post/codemode-small-models-en), 2026-10-10 | Qwen3.5-4B/9B, Qwen3.8-27B, 30 tasks | Code mode ahead at every size (22 vs 18, 28 vs 19, 30 vs 26); only 9B significant. Gap is in join tasks. Tokens 28–45% of tool calling. Needs return types documented. |
| [tmustier code-mode-mcp](https://github.com/tmustier/code-mode-mcp/blob/main/docs/benchmarks/2026-07-direct-tools-and-code-mode.md), 2026-07 | 10-tool server, 8 task shapes, 160 runs | "No universal call-count threshold." Six independent small calls favoured direct; an 8-step chain and a 19-call fan-out favoured code. Guided hybrid (both, plus short routing guidance): −40.6% time, −73.7% cost. |
| [IJCA 2026](https://www.ijcaonline.org/archives/volume187/number136/mbat-ijca-2026-e10508249e55.pdf) | 8 models incl. Opus 4.6, GPT-5.4 | Code mode −37.6% tokens, −40.7% cost, pass 59.4% → 63.6%; but "Weaker models underperform because compiler errors … trigger retry loops", and Gemini-family models fell from ~2/3 to ~1/3 pass. |
| [npow](http://npow.github.io/posts/mcp-beats-code-generation/), 2026-03-18 | 10-tool Metaflow API, Haiku/Sonnet/Opus, 972 runs | Direct MCP 0.995 vs code generation 0.97; code mode's worst task 0.33 vs 0.83. "Tool-based failures are partial answers; code generation failures are confident wrong answers." With direct tools, Haiku within 1% of Opus at 1/12 the cost. Small effect, p=0.107. |
| [FastMCP issue #4902](https://github.com/PrefectHQ/fastmcp/issues/4902) | 12 tasks | −68% input tokens but +32% latency; with a ReAct agent 7/12 code-mode tasks scored 0 on tool accuracy. |

### Synthesis

- Code execution wins clearly when the agent must **aggregate, join, count
  or page through** data larger than it should read. Models cannot do
  arithmetic over thousands of rows in context; code can.
- It loses or ties for **small, well-designed tool sets** and simple calls,
  and it can hurt **some model families**. Model choice and orchestration
  style are a pair, which matches the overview's "model and harness are a
  pair" (6.4).
- The **hybrid** (direct tools plus a code tool, with a line of routing
  guidance) was best in the one study that tested it.
- Code mode has a cost the overview does not mention: a sandbox, and a
  security surface (Cloudflare blocks outbound fetch and keeps credentials
  out of the sandbox).
- Relation to the overview's wrapper idea (inference): a Kiln code tool
  wrapper is **author-time code mode**. The build agent writes the
  orchestration once, from traces, and the subject agent calls one tool.
  That fits the overview's "recurring task" class better than runtime code
  mode: no per-run code generation, no compile errors on cheap models, and
  deterministic behaviour. Runtime code mode is the better fit when the
  query shape varies too much to precompute. This is the same split as
  Vercel's result: for a strong model and an open-ended query space, a
  general tool plus good documentation won.
- Whether a Kiln subject agent can run model-written code at run time (a
  code tool that takes code as an argument and calls nested tools) is
  unconfirmed. Kiln's `PythonCodeTool` runs sandboxed author code that can
  call nested tools through `sandbox_bridge`; we did not check whether that
  can be pointed at model-written code. Hand to the Kiln-gap subtopic.
