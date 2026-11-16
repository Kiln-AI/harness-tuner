# Tool design principles: what the published guidance says

Researched 2026-11-10. Each claim links to its source. "Inference" marks a
conclusion that is ours, not the source's.

## 1. The core claim: an agent tool is not an API

All major sources agree on one point. A tool that wraps an API endpoint 1:1
is usually a poor agent tool.

- Anthropic, [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
  (2025-09-11): "A common error we've observed is tools that merely wrap
  existing software functionality or API endpoints—whether or not the tools
  are appropriate for agents." The reason given: agents have limited
  context, "whereas computer memory is cheap and abundant". A
  `list_contacts` tool that returns all contacts forces a brute-force read;
  build `search_contacts` instead.
- Jeremiah Lowin (FastMCP author), [Stop Converting Your REST APIs to MCP](https://jlowin.dev/blog/stop-converting-rest-apis-to-mcp)
  (2025-07-10): "an API built for a human will poison your AI agent".
  "Atomicity is an agent anti-pattern." Use OpenAPI conversion to bootstrap,
  never to ship. Curate: rename cryptic arguments, hide irrelevant
  parameters behind defaults. Start from an "agent story": "As an agent,
  given `{context}`, I use `{tools}` to achieve `{outcome}`."
- Block, [Playbook for designing MCP servers](https://engineering.block.xyz/blog/blocks-playbook-for-designing-mcp-servers)
  (2025-06-16, 60+ servers): "Design top-down from workflows, not bottom-up
  from API endpoints." "Don't: expose raw, granular API endpoints like
  `GET /user` or `GET /file`. These usually produce verbose, poorly scoped
  outputs."
- Phil Schmid, [MCP best practices](https://www.philschmid.de/mcp-best-practices)
  (2026-01-21): "Outcomes, Not Operations". Replace
  `get_user_by_email` + `list_orders` + `get_order_status` with
  `track_latest_order(email)`. "Do the orchestration in your code, not in
  the LLM's context window."
- Microsoft, [OWASP MCP guidance for Azure](https://microsoft.github.io/mcp-azure-security-guide/adoption/development-best-practices/)
  (undated, 2026): "The most common mistake when building MCP servers is
  treating them like REST APIs". Gives a mapping table, for example
  `create_ticket`, `add_comment`, `assign_ticket`, `notify_user` →
  `file_support_request`. Also: task-focused naming
  (`execute_sql_query` → `analyze_sales_trends`) and "return decision-ready
  data".
- OpenAI, [Function calling guide](https://developers.openai.com/api/docs/guides/function-calling)
  (current, read 2026-11-10): "Combine functions that are always called in
  sequence." "Don't make the model fill arguments you already know."
- WorkOS, [Designing an MCP server from a REST API](https://workos.com/blog/designing-mcp-server-from-rest-api)
  (undated): "MCP is a user interface, and your user is an AI agent." Gives
  a "five-tool test": if a typical goal needs five or more calls, the tools
  are too thin; if a tool needs more than four or five parameters or three
  paragraphs of description, it is too thick. Cites Workato guidance of five
  to eight tools per server, twelve as a soft ceiling (not verified at the
  Workato source).

**Confirms overview 6.3** ("a good API can be a very poor agent tool") with
strong, repeated, independent evidence.

## 2. Consolidate call chains into one tool

Guidance:

- Anthropic gives three consolidation examples: `schedule_event` in place of
  `list_users` + `list_events` + `create_event`; `search_logs` in place of
  `read_logs`; `get_customer_context` in place of `get_customer_by_id` +
  `list_transactions` + `list_notes`
  ([source](https://www.anthropic.com/engineering/writing-tools-for-agents)).
  The last one is the overview's `get_user_data` roll-up (7.5) almost word
  for word.
- Anthropic also says to *find* consolidation candidates from traces:
  "Tracking tool calls can help reveal common workflows that agents pursue
  and offer some opportunities for tools to consolidate." This matches the
  overview's "the agent often makes these seven calls in a row, so one tool
  does it".

Measured cases:

- Block's Linear server went through three versions: 30+ endpoint-shaped
  tools; then category tools such as `get_issue_info(issue_id,
  info_category)`; then two tools, `execute_readonly_query` and
  `execute_mutation_query`, that take GraphQL directly. "What originally
  took 4+ tool calls is now a single GraphQL call"
  ([source](https://engineering.block.xyz/blog/blocks-playbook-for-designing-mcp-servers)).
  Block does not publish an accuracy number. Secondary sources say
  "measured performance improvements each time"; we could not find the
  numbers at Block.
- GitHub's MCP server merged six pull-request read tools into
  `pull_request_read` with a `method` parameter, and did the same for
  issues, labels and reviews
  ([2025-10-14](https://github.blog/changelog/2025-10-14-github-mcp-server-now-supports-github-projects-and-more/),
  [2025-10-29](https://github.blog/changelog/2025-10-29-github-mcp-server-now-comes-with-server-instructions-better-tools-and-more/)).
  The Projects consolidation saved "around 23,000 tokens (50%)"
  ([2026-01-28](https://github.blog/changelog/2026-01-28-github-mcp-server-new-projects-tools-oauth-scope-filtering-and-new-features/)).

Note: two consolidation styles exist and they are different levers.

- **Workflow roll-up**: one tool runs a chain (A → B → C) and returns one
  answer. Removes LLM round trips.
- **Method multiplexing**: one tool with a `method` or `info_category` enum
  replaces several sibling tools. Reduces tool count and definition tokens
  but does not remove round trips. Block's v2 shows the limit: fewer tools,
  same number of calls.

## 3. Response size and token efficiency

- Anthropic: "implementing some combination of pagination, range selection,
  filtering, and/or truncation with sensible default parameter values for
  any tool responses that could use up lots of context. For Claude Code, we
  restrict tool responses to 25,000 tokens by default."
  ([source](https://www.anthropic.com/engineering/writing-tools-for-agents)).
- Claude Code today (docs read 2026-11-10,
  [MCP](https://code.claude.com/docs/en/mcp)): a warning above 10,000 tokens
  of MCP output; a default cap of 25,000 tokens (`MAX_MCP_OUTPUT_TOKENS`);
  text results longer than 50,000 characters are saved to a file and the
  model gets the path; a server can raise one tool's limit with
  `_meta["anthropic/maxResultSizeChars"]` up to 500,000 characters; error
  text above about 11,000 characters keeps its first and last 5,000. These
  are the clearest published thresholds we found for "too large".
- Microsoft Research surveyed 1,470 live MCP servers
  ([Tool-space interference](https://www.microsoft.com/en-us/research/blog/tool-space-interference-in-the-mcp-era-designing-for-agent-compatibility-at-scale/),
  2025-09-11). Most tools returned 98 tokens or fewer, but "the top tool
  returned an average of 557,766 tokens", and 16 tools returned more than
  128,000 tokens. It cites a study where long responses degrade performance
  "up to 91%". It recommends that server authors publish expected tokens per
  call and that clients "summarize or paginate long tool results".
- **Return high-signal fields only.** Anthropic: drop low-level identifiers
  (`uuid`, `256px_image_url`, `mime_type`) in favour of `name`, `image_url`,
  `file_type`. "Merely resolving arbitrary alphanumeric UUIDs to more
  semantically meaningful and interpretable language (or even a 0-indexed ID
  scheme) significantly improves Claude's precision in retrieval tasks by
  reducing hallucinations." No number is given.
- **A verbosity switch.** Anthropic suggests a `response_format` enum
  (`concise` / `detailed`). Their Slack example: detailed 206 tokens,
  concise 72 tokens (about one third). Concise omits IDs, so the agent asks
  for `detailed` only when it needs IDs for a next call.
- **Format of the result.** Anthropic: "Even your tool response
  structure—for example XML, JSON, or Markdown—can have an impact on
  evaluation performance: there is no one-size-fits-all solution." Benchmarks
  from 2026 disagree with each other:
  - an end-to-end agentic study (BFCL, MCP-Universe and two others, five
    open models) found TOON saves up to 18% of tokens at about a 9 pp
    accuracy cost, and breaks parallel tool calls when used for output
    ([arXiv 2605.29676](https://arxiv.org/html/2605.29676v2), 2026-06);
  - a 34-model comprehension test found YAML 83.0%, pretty JSON 81.0%,
    compact JSON 79.6%, TOON 77.4%, CSV 69.7%, with filtering and
    aggregation questions the weak spot for tabular formats
    ([toon discussion #285](https://github.com/toon-format/toon/discussions/285),
    2026-03);
  - a small Haiku benchmark found Markdown tables gave 24.67% weighted
    accuracy against about 70% for JSON compact and CSV
    ([thoeltig benchmark](https://github.com/thoeltig/file-format-token-accuracy-benchmark/),
    2025-12); another found TOON 62% fewer tokens than JSON with no loss
    ([yaml-vs-md-benchmark](https://github.com/webmaster-ramos/yaml-vs-md-benchmark),
    2026-04).
  Inference: format is a real but small and model-dependent lever. Compact
  JSON is a safe default. Pretty-printed JSON wastes tokens with no gain.
  Field projection (fewer fields) saves more than any format change.
- **Resource links.** The MCP spec lets a tool return a `resource_link`
  (a URI) in place of the content
  ([spec 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)).
  Microsoft found only 4 tools in 1,470 servers did this, and calls it
  "arguably the ideal behavior for tools that return very long,
  document-like responses". This is the protocol-level form of "return a
  handle, not the data", which is also how Claude Code now treats oversized
  results (save to file, return the path).

## 4. Pagination and filtering

- Anthropic: pagination, range selection, filtering and truncation "with
  sensible default parameter values". If you truncate, "steer agents with
  helpful instructions", for example to make many small targeted searches
  in place of one broad search.
- OpenAI: "Don't make the model fill arguments you already know." Inference:
  fix page sizes, status filters and field lists inside the tool when the
  agent does not need to vary them. The Agent Tool Facade write-up says the
  same: "Anything that can be decided in advance (result limits, status
  filters, field lists) is set inside the tool, not by the model"
  ([sol4biz, 2026-10-08](https://sol4biz.at/the-agent-tool-facade/)).
- A useful truncation signal: fetch `limit + 1` rows, return `limit`, and
  tell the model the result was cut
  ([connector.zone, 2026-02-14](https://connector.zone/guides/constraining-raw-sql-from-a-model-inside-a-tool-handler/)).
- Bound the output in the schema itself, for example `z.array(...).max(50)`
  ([Noodle Seed docs](https://docs.noodleseed.dev/docs/guides/tool-design),
  undated).
- **Page size is a cost trap in direct tool calling.** In a code-mode
  benchmark, one direct-call run chose 100 rows per page over 10,000 orders,
  made 124 calls and used 28.9 million prompt tokens "because every call
  re-sends the whole conversation"
  ([Gatana, 2026-10-05](https://www.gatana.ai/blog/code-mode-vs-direct-tool-calls-benchmark)).
  Inference: paging through large data in the LLM loop is quadratic in
  cost. Aggregation belongs in the tool or in code.
- Anthropic's metric hint: "Lots of redundant tool calls might suggest some
  rightsizing of pagination or token limit parameters is warranted."

## 5. Error messages that steer the agent

- MCP spec (2025-11-25): two error channels. Protocol errors (JSON-RPC) for
  unknown tools and malformed requests. Tool execution errors in the result
  with `isError: true` for "API failures, input validation errors (e.g.,
  date in wrong format, value out of range), business logic errors". "Tool
  Execution Errors contain actionable feedback that language models can use
  to self-correct". Example text: "Invalid departure date: must be in the
  future. Current date is 08/08/2025."
  ([spec](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)).
  Note: input validation errors are now in the tool-result channel so the
  model sees them.
- Anthropic: "prompt-engineer your error responses to clearly communicate
  specific and actionable improvements, rather than opaque error codes or
  tracebacks." Errors "can steer agents towards more token-efficient
  tool-use behaviors (using filters or pagination) or give examples of
  correctly formatted tool inputs."
- Microsoft's survey found the ecosystem is poor at this. Servers often
  returned error strings with `isError` false: "Out of 5,983 tool call
  results with no error flag, GPT-4.1 judged that 3,536 indicated errors in
  their content." Examples of bad messages: "error: job", "Please retry with
  0 or fewer IDs."
- WorkOS: write errors for the model and name the next step:
  `"recipe_id 472 not found. Use search_recipes to find available IDs."`
  Prefer "recoverable failures over hard exceptions", for example return a
  structured "which day?" response in place of throwing.
- AWS Well-Architected agentic lens: map legacy error codes to a small
  canonical taxonomy (timeout, auth failure, rate limit, unavailable) inside
  the adapter ([AGENTREL06-BP01](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentrel06-bp01.html)).
- For SQL tools: return the validator's message to the model and allow
  "one or two repair attempts, never more"; "naming the allowed views is
  often enough for it to fix the query"
  ([Csorba](https://balazscsorba.com/blog/text-to-sql-llm-guardrails), undated 2026).

**Missing from the overview:** error-message design is not listed as a lever
or a smell. The sources rate it as one of the cheapest fixes. The tuner's
`actionable_errors.py` wrapper template already covers it (see section 9).

## 6. Tool descriptions and schemas

- Anthropic calls description engineering "one of the most effective
  methods for improving tools". "Think of how you would describe your tool
  to a new hire on your team." Make implicit context explicit: "specialized
  query formats, definitions of niche terminology, relationships between
  underlying resources". Name parameters unambiguously: `user_id`, not
  `user`. They report Claude Sonnet 3.5's SWE-bench Verified result came
  after "precise refinements to tool descriptions". They also found Claude
  appending "2025" to web search queries and fixed it in the description.
- OpenAI: describe purpose, each parameter and its format, and what the
  output means; "Use the system prompt to describe when (and when not) to
  use each function"; "Use enums and object structure to prevent invalid
  states"; "Pass the intern test"; and "Include examples and edge cases …
  (Note: Adding examples may hurt performance for reasoning models.)"
  Always enable `strict` mode.
- **Usage examples in the definition.** Anthropic's Tool Use Examples
  (`input_examples`, beta 2025-11-24): "improved accuracy from 72% to 90% on
  complex parameter handling". Use 1–5 realistic examples, only where usage
  is not obvious from the schema
  ([Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)).
  A practitioner warning: "examples are contracts. If your example shows a
  list with two items, the agent will almost always return two items"
  ([Mikulski, 2026-03-09](https://mikulskibartosz.name/build-mcp-servers-the-right-way)).
  OpenAI's note that examples may hurt reasoning models is the
  counter-signal. Inference: examples are an experiment, not a default.
- **Document the return shape** when the agent will process it in code:
  Anthropic shows a description with field names, types and an ISO 8601
  timestamp note. The MCP spec has an optional `outputSchema` and
  `structuredContent`. A 2026 small-model codemode test found "Without the
  return structure, the 9B guessed a field's type wrong"
  ([SOTAAZ, 2026-10-10](https://sotaaz.com/post/codemode-small-models-en)).
- **Flat parameters.** Composio found flattening nested schemas raised
  GPT-4-era function-calling accuracy (from 0.36 to about 0.53 on their
  50-problem benchmark; their full stack of changes reached about 0.78)
  ([Composio benchmark](https://github.com/ComposioHQ/Composio-Function-Calling-Benchmark)).
  Microsoft cites this as "47%" and found input schemas nested up to 20
  levels deep in the wild. This evidence is from 2024 models; we found no
  2026 replication.
- **Empirical description studies (2026).** See
  [tool-smells.md](./tool-smells.md) for the numbers. In short: almost all
  public MCP descriptions have defects; fixing them helps on average
  (+5.85 pp task success in one study) but costs more steps (+67%) and
  regresses in about one case in six.

## 7. Naming and namespacing

- Anthropic: namespace by service (`asana_search`, `jira_search`) and by
  resource (`asana_projects_search`). "Selecting between prefix- and
  suffix-based namespacing [has] non-trivial effects on our tool-use
  evaluations. Effects vary by LLM". So naming scheme is itself an
  experiment.
- Microsoft found 775 tool-name collisions across servers. Most common:
  `search` (32 servers), `get_user` (11), `execute_query` (11). Many
  near-synonyms (`web_search`, `search_web`, `websearch` …). The MCP spec
  has no formal namespace mechanism; clients such as Claude Code prefix
  names.
- Manus uses consistent prefixes (`browser_`, `shell_`) so it can constrain
  the model to a tool group by prefilling the name prefix, without changing
  the tool list ([Manus, 2025-07-18](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)).

## 8. Wrapper layers over fixed enterprise APIs

The overview's "wrap, do not rewrite" (6.3) is now a named pattern with
several independent write-ups:

- AWS, [Retrofit, don't rebuild: agentic overlays](https://aws.amazon.com/blogs/machine-learning/retrofit-dont-rebuild-agentic-overlays-for-transforming-legacy-enterprise-services/)
  (2026-06-25): "thin wrapper layers" over unchanged REST services.
- AWS Well-Architected agentic lens: adapters translate legacy interfaces
  into tools "without the legacy side needing to change"; add error mapping
  and rate limiting in the adapter, because "legacy systems were sized for
  human-paced traffic".
- The "Agent Tool Facade" ([sol4biz, 2026-10-08](https://sol4biz.at/the-agent-tool-facade/))
  is the most complete design. It is "a BFF for agents" with a response
  shaper ("field projection, normalization, sanitization of free text, size
  budgets, provenance"), per-tool call plans ("a handler for
  `get_order_status` calls four TMF APIs"), identity bound from the session
  ("never from the model"), and read / qualify / draft risk tiers with no
  direct write tool. "Models choose better between 5 and 10 clearly
  different tools than between 50 similar ones. If two tools are hard to
  tell apart in a sentence, merge or remove one."
- VE3 on SAP ([2026-08-11](https://ve3.global/blog/your-sap-doesnt-need-to-speak-mcp-wrapping-a-legacy-estate-for-ai-agents)):
  "Do not expose a generic 'run any RFC' or 'query any table' tool." Writes
  take an approval token and an idempotency key.
- Lowin's FastMCP `Tool.from_tool()` transforms an existing tool: rename,
  hide parameters with defaults.

Inference for Kiln: these sources describe exactly what a Kiln code tool
wrapper does. None of them measures the gain of a wrapper against the raw
API on an agent eval; the measured evidence for wrapping comes from the
consolidation cases (section 2) and the code-mode benchmarks
([tool-count-search-and-code-mode.md](./tool-count-search-and-code-mode.md)).

A safety point the overview does not make: wrappers are also where
**identity and write-safety** live. Several sources say a parameter the
agent can set (customer id, tenant) is an attack surface; bind it from the
session. This touches restrictions (overview section 8).

## 9. What Harness Tuner already ships

Read from the repository on 2026-11-10, not from the web.
`src/kiln_harness_tuner/agent_files/skills/kiln-harness-tuner-code-tool-authoring/`
already has wrapper templates: `paginate_all.py`, `batch_with_errors.py`,
`normalize_inputs.py`, `actionable_errors.py`, `context_on_tool_surface.py`,
`preview_and_cap.py`, `composite_resolve_fetch.py`, `readonly_guard.py`.
So the build agent already knows *how* to write most wrappers the sources
describe. What the sources add, and the skill set does not obviously cover,
is mostly *when* to try each one (the smell → fix map in
[tool-smells.md](./tool-smells.md)), plus: ID resolution to readable names,
a verbosity enum, tool-name and namespace experiments, removal of tools,
tool-use examples, and output-format experiments. We did not audit the
skill text line by line; the Kiln-gap subtopic owns that.
