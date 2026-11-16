# Tool smells, with evidence

Researched 2026-11-10. A "smell" here is a property of a tool, or of the
tool set, that sources link to worse agent results. Each entry gives the
evidence, the usual fix to try as an experiment, and a one-line signal.
How to detect each signal in traces by software belongs to the "Trace
diagnostics and red flags" subtopic; the signals here are pointers only.

Order is our ranking (inference), by how strongly the evidence links the
smell to failure and by whether it blocks other fixes. It is a start, not a
measured order. "Blocks" means: fix this before tuning other levers,
because its effect would hide theirs.

## Description-quality studies (background for smells 8–12)

Three 2026 studies measured description defects in public MCP servers.

- Hasan et al., [MCP Tool Descriptions Are Smelly!](https://arxiv.org/abs/2602.14878)
  (arXiv 2602.14878, v. 2026-05-31; Queen's University / Huawei group).
  856 tools, 103 servers. 97.1% of descriptions have at least one smell;
  56% do not state their purpose clearly. Fixing all components: task
  success +5.85 pp median (McNemar p=0.02), partial completion +15.12%, but
  execution steps +67.46% and regressions in 16.67% of model–domain cases.
  "Compact variants of different component combinations often preserve
  behavioral reliability while reducing unnecessary token overhead."
  Glama summarises the same data as "89% fail to mention when you should or
  shouldn't use them" ([Glama, 2026-04-03](https://glama.ai/blog/2026-04-03-tool-definition-quality-score-tdqs)).
- Wang et al., [From Docs to Descriptions](https://arxiv.org/abs/2602.18914)
  (arXiv 2602.18914, 2026-02-21; UCLA / NTU). 10,831 servers, 18 smell
  categories in four dimensions. 73% have repeated tool names; 3,449 have
  wrong parameter meanings; 3,093 lack return descriptions. Effect on
  selection: functionality +11.6%, accuracy +8.8%, completeness +5.9%,
  conciseness +1.5%. A standard-compliant server was chosen 72% of the time
  among five equivalents (20% baseline).
- Paidi, "Smells in the Protocol" ([JETIR 2604862](https://www.jetir.org/papers/JETIR2604862.pdf),
  2026-04). 553 tools; reports "Incomplete Examples" in 72.4% and a 30.5 pp
  tool-selection drop. **Low confidence**: independent author, non-indexed
  venue, and the dataset mixes GSM8K into "tools". Use only as a weak
  second source.

Reading across them: description fixes help on average, but the gain is
model- and domain-dependent, and richer descriptions make agents take more
steps. Description rewrites are therefore an experiment with a cost axis,
not a free win. This matches Harness Tuner's "experiments win" rule.

## The ranked list

### 1. Huge tool results (blocks)

- **Evidence.** Microsoft: the largest tool averaged 557,766 tokens per
  call; 16 tools exceeded 128,000; long responses can degrade performance
  "up to 91%" ([Microsoft Research](https://www.microsoft.com/en-us/research/blog/tool-space-interference-in-the-mcp-era-designing-for-agent-compatibility-at-scale/)).
  Anthropic caps Claude Code tool results at 25,000 tokens and warns at
  10,000 ([docs](https://code.claude.com/docs/en/mcp)). Anthropic's
  transcript example: a meeting transcript passed twice through context
  costs ~50,000 extra tokens
  ([Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)).
- **Fix to try.** Field projection; filter or search in place of list;
  pagination with small defaults; preview and cap; return a handle (file or
  resource link) in place of content; aggregate in code.
- **Signal.** Tokens per tool result, max and p95; results above 10k / 25k
  tokens.
- **Overview.** Confirms the overview's first smell and its "blocks
  everything" note (section 9).

### 2. Model does arithmetic or aggregation over raw rows

- **Evidence.** Gatana: direct calls over 10,000 orders were right 2 of 5
  times; on word counts over 40 documents 0 of 5; "Models do not count; they
  estimate." Code mode was 10 of 10
  ([Gatana](https://www.gatana.ai/blog/code-mode-vs-direct-tool-calls-benchmark)).
  Anthropic's budget example: 2,000+ expense lines in context vs 1 KB of
  result with Programmatic Tool Calling
  ([Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)).
- **Fix.** An aggregate tool (wrapper that sums, counts, joins), or a code
  execution tool.
- **Signal.** Many paginated calls to one list tool followed by a numeric
  answer.
- **Overview.** Missing as a named smell; implied by the 4-API wrapper.

### 3. Call chains the LLM must drive (A → id → B → id → C)

- **Evidence.** Block Linear v1: 4–6 calls for one question
  ([Block](https://engineering.block.xyz/blog/blocks-playbook-for-designing-mcp-servers)).
  Phil Schmid, Lowin, Microsoft, WorkOS: "outcomes, not operations";
  "atomicity is an agent anti-pattern". OpenAI: "Combine functions that are
  always called in sequence." WorkOS five-tool test.
- **Fix.** Workflow roll-up tool; context roll-up (`get_customer_context`).
- **Signal.** Repeated n-grams of tool calls across traces where the output
  id of one is the input of the next.
- **Overview.** Confirms 6.3 and 7.5.

### 4. Too many tools, or overlapping tools (blocks at large counts)

- **Evidence.** Copilot: 2–5 pp lower resolution with ~40 tools vs 13
  ([GitHub](https://github.blog/ai-and-ml/github-copilot/how-were-making-github-copilot-smarter-with-fewer-tools/));
  Anthropic: Opus 4 49% → 74% when a large library is deferred; OpenAI soft
  limit of 20; SixDegree: degradation from 25–50 tools; OpenAI data agent:
  overlapping tools confused the agent; Superhuman: a 10 pp "confusion gap"
  even with perfect retrieval ([arXiv 2606.17519](https://arxiv.org/html/2606.17519)).
  Anthropic: "If a human engineer can't definitively say which tool should
  be used in a given situation, an AI agent can't be expected to do
  better."
- **Fix.** Remove unused tools; merge near-duplicates; scope tools per
  agent; group into virtual tools; harness-side shortlisting; tool search
  only for large third-party catalogs.
- **Signal.** Static: tool count; definition tokens; pairs of tools with
  similar names or descriptions. Traces: tools never called; wrong-tool
  calls followed by a switch.
- **Overview.** Missing as a smell (the overview has tool search, not tool
  count or overlap). Section 9 has the parallel item for skills.

### 5. Tool definitions that eat the context

- **Evidence.** GitHub MCP default 101 tools = 64.6k tokens
  ([discussion](https://github.com/github/github-mcp-server/discussions/1182));
  five common servers ≈ 55K tokens, Anthropic internal 134K
  ([Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use));
  Cloudflare API as native tools ≈ 1.17M tokens
  ([Cloudflare docs](https://developers.cloudflare.com/agents/model-context-protocol/cloudflare/servers-for-cloudflare/)).
  Claude Code defers MCP tools when they exceed 10% of the context window.
- **Fix.** As smell 4; shorten descriptions; search/execute code mode for
  very large APIs.
- **Signal.** Definition tokens as a share of the context window (10% is
  the one published threshold).
- **Overview.** Section 9 has "a very long tool description (27,000
  characters)", which is one case of this.

### 6. Raw endpoint mirror (1:1 REST or OpenAPI conversion)

- **Evidence.** Anthropic, Lowin ("Bootstrap, Don't Deploy"), Microsoft
  ("❌ Avoid: Auto-generating tools from OpenAPI specs"), Mikulski ("never
  use the converter"). This is the root cause of smells 1, 3, 4 and 7 more
  than a smell of its own.
- **Signal.** Tool names that match HTTP routes or SDK methods one to one;
  generic names (`get_data`, `search`, `execute_query`).

### 7. Opaque identifiers and noise fields in results

- **Evidence.** Anthropic: resolving UUIDs to meaningful names or 0-indexed
  ids "significantly improves Claude's precision in retrieval tasks by
  reducing hallucinations"; drop `uuid`, `mime_type` and similar; a concise
  mode used about one third of the tokens
  ([Writing tools](https://www.anthropic.com/engineering/writing-tools-for-agents)).
  Noodle Seed: pair an id-taking tool with a resolver because the user
  never says the id. Anthropic also notes models make "mistakes when
  copying data between tool calls".
- **Fix.** Resolver tool; `response_format` enum; drop fields the agent
  never uses.
- **Signal.** Share of result tokens that are ids, hashes, URLs or base64;
  fields in results never referenced in later calls or the answer.
- **Overview.** Missing (the overview names the 2,000-word text field, not
  ids).

### 8. Errors the agent cannot act on

- **Evidence.** Microsoft: 3,536 of 5,983 "success" results were really
  errors with `isError` false; messages like "error: job". MCP spec: input
  validation errors belong in the tool result with `isError: true` so the
  model can "self-correct"
  ([spec](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)).
  Anthropic: replace "opaque error codes or tracebacks" with specific,
  actionable text.
- **Fix.** Error-enrichment wrapper: name valid values, nearest matches,
  the next tool to call, the wait time.
- **Signal.** Error rate per tool; the same tool retried with small
  argument changes; tracebacks or HTTP status lines in results; errors
  inside "successful" results.
- **Overview.** Missing.

### 9. Vague purpose, no "when to use" and "when not to use"

- **Evidence.** Hasan: 56% of descriptions do not state their purpose;
  Glama summary: 89% omit when to use. Wang: functionality defects have the
  largest effect on selection (+11.6%). OpenAI: say "when (and when not)" to
  use each function. Anthropic: describe the tool as to a new hire.
- **Fix.** Rewrite the description; add the usage rule to the prompt or a
  side skill. Treat as an experiment (smells study: 1 in 6 regressions).
- **Signal.** Description length very short; no verbs of intent; no
  comparison with sibling tools. Mostly an LLM check, not code.

### 10. Ambiguous or wrong parameters, and nested schemas

- **Evidence.** Wang: 3,449 servers with wrong parameter meanings.
  Anthropic: `user_id`, not `user`; date-format and ID-convention ambiguity
  drove their Tool Use Examples feature (72% → 90% on complex parameters).
  Composio (2024 models): flattening nested schemas raised accuracy, part of
  a 0.36 → 0.78 improvement; Microsoft found schemas 20 levels deep.
  OpenAI: use enums so invalid states cannot be expressed; use `strict`.
- **Fix.** Rename; enums; flatten; examples; remove parameters the harness
  already knows (OpenAI: `submit_refund()` with no `order_id`).
- **Signal.** Static: schema depth; parameters without description or
  format; free-string parameters with a small value set. Traces: invalid
  argument errors per tool.

### 11. No documented return shape

- **Evidence.** Wang: 3,093 servers lack return descriptions. Anthropic:
  document return formats so the model "can write correct parsing logic".
  SOTAAZ: without the return structure a 9B model guessed a field type wrong
  ([SOTAAZ](https://sotaaz.com/post/codemode-small-models-en)).
- **Fix.** Return description or `outputSchema`.
- **Signal.** Static: no output schema and no "Returns" text.

### 12. Name collisions and near-synonyms across servers

- **Evidence.** Microsoft: 775 colliding names; `search` in 32 servers.
  Wang: 73% repeated tool names. Anthropic: prefix vs suffix namespacing has
  "non-trivial effects", by model. SixDegree: similar services confuse.
- **Fix.** Namespace by service and resource; rename; remove one of the
  pair.
- **Signal.** Static: exact and fuzzy name matches across the tool set.

### 13. Misleading scope (description promises more than the tool does)

- **Evidence.** Description–code inconsistency in about 13% of 10,240
  servers ([arXiv 2602.03580](https://arxiv.org/html/2602.03580)) and 9.93%
  of pairs in another study ([arXiv 2606.04769](https://arxiv.org/html/2606.04769)),
  framed mostly as a security risk. Paidi (low confidence) rates it as
  causing "hallucinated tool invocations".
- **Signal.** Mostly an LLM check of description against code.

### 14. Tool search as the only path to a needed tool

- **Evidence.** Exact-name misses in Claude Code
  ([#66488](https://github.com/anthropics/claude-code/issues/66488)) and
  Codex ([#21503](https://github.com/openai/codex/issues/21503)); stale index
  ([#66084](https://github.com/anthropics/claude-code/issues/66084)); Copilot
  saw wasted group lookups; vendors say it does not pay below ~10 tools.
  Against it: it raised Opus 4 from 49% to 74% on a large library.
- **Fix.** For a known, recurring tool set: load the needed tools directly,
  or route in the harness; keep 3–5 core tools always loaded; name tool
  families in the prompt.
- **Signal.** Search calls with no follow-up call; the agent says a tool is
  missing that is configured.
- **Overview.** Confirms the overview's second smell in a narrower form (see
  [tool-count-search-and-code-mode.md](./tool-count-search-and-code-mode.md)).

### 15. Context the agent should have been given (context-loading smell)

- **Evidence.** OpenAI's data agent: memory of a non-obvious filter fixed
  repeated errors; Noodle Seed: "Most 'the model guessed wrong' bugs are
  missing context, not a missing tool … today's date, the user's time
  zone"; Anthropic recommends a hybrid of up-front context and just-in-time
  retrieval ([Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).
- **Fix.** Roll-up tool plus "call it first" instruction, or inject the
  facts up front.
- **Overview.** Confirms 7.5.

### 16. Write tools without guard rails

- **Evidence.** SQL sources: prompt-only "SELECT only" is not a guard rail
  and has been bypassed in a popular Postgres MCP server
  ([AI Technology Radar](https://ai-radar.aoe.com/architecture-pattern/text_to_sql/)).
  Facade sources: identity from the session, never from the model; drafts
  plus approval for writes; idempotency keys
  ([sol4biz](https://sol4biz.at/the-agent-tool-facade/),
  [VE3](https://ve3.global/blog/your-sap-doesnt-need-to-speak-mcp-wrapping-a-legacy-estate-for-ai-agents)).
  MCP tool annotations disclose "which tools require open-world access or
  make destructive changes" (Anthropic), but the spec says "clients MUST
  consider tool annotations to be untrusted unless they come from trusted
  servers".
- **Note.** This is a safety smell more than a quality smell. It matters
  to Harness Tuner because a tuning loop runs the subject agent many times
  against the tools (see overview 7.7 on stateful environments).

### 17. Dynamic tool lists inside a conversation

- **Evidence.** Manus: adding or removing tools mid-loop invalidates the KV
  cache and confuses the model when history mentions a removed tool; mask,
  do not remove. Anthropic troubleshooting: "Adding a tool mid-conversation
  breaks cache" — declare it deferred from the first request.
- **Signal.** The tool list changes between turns of one trace.
- **Overview.** Missing; relevant to the cache argument in 6.2.

## Smells in tool *results* format (weak evidence, listed for completeness)

- Pretty-printed JSON costs ~1.9× compact JSON with similar accuracy
  ([thoeltig](https://github.com/thoeltig/file-format-token-accuracy-benchmark/)).
- Tabular formats (CSV, TOON) are weaker for filtering and aggregation
  questions ([toon #285](https://github.com/toon-format/toon/discussions/285)).
- Benchmarks disagree on Markdown. Anthropic says the best format "will
  vary widely by task and agent". Treat format as a late, small experiment.
