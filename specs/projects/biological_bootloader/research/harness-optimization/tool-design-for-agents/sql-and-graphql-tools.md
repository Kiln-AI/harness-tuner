# SQL and GraphQL tools for agents

Researched 2026-11-10. The overview (6.3, 10) says powerful general tools
need the agent to learn "the whole schema, the gotchas and how dates are
handled", that sample queries beat schemas alone, and that timestamps need
their own section. The sources confirm all three and add guard rails and
context layers.

## 1. Two design choices, and both work

There are two families. They are not exclusive.

| Choice | What the agent gets | Evidence |
|---|---|---|
| **Fixed operations** | Predefined queries as named tools (persisted queries, curated views, `analyze_sales_trends`) | Apollo persisted queries as tools "like options on an operation menu" ([Apollo tutorial](https://www.apollographql.com/tutorials/enterprise-mcp-graphql/01-mcp-graphos)); Microsoft's naming advice `execute_sql_query` → `analyze_sales_trends` ([Azure MCP guide](https://microsoft.github.io/mcp-azure-security-guide/adoption/development-best-practices/)) |
| **General query tool + docs** | One `execute` tool, plus schema search, validation and examples | Block's Linear MCP ended at two GraphQL tools ([Block](https://engineering.block.xyz/blog/blocks-playbook-for-designing-mcp-servers)); Vercel d0 ended at bash over the semantic layer plus SQL ([Vercel](https://vercel.com/blog/we-removed-80-percent-of-our-agents-tools)); Cloudflare search/execute covers a GraphQL Analytics API ([Cloudflare](https://blog.cloudflare.com/code-mode-mcp/)) |

Apollo's guidance on the trade-off is useful because it is incremental
([Every token counts, 2025-06-24](https://www.apollographql.com/blog/building-efficient-ai-agents-with-graphql-and-apollo-mcp-server)):
"Start broad: One operation that fetches multiple related data points.
Notice patterns: The LLM keeps asking follow-up questions about specific
data. Split strategically … Combine when needed: Merge operations that are
always called together." That is trace-driven tool design, the overview's
method.

Inference: for a recurring agent, the common queries in the traces are
candidates for fixed operations (cheap, safe, fast); the long tail stays on
the general tool. This is an experiment pair the tuner can run.

## 2. What context the agent needs (the "side skill" content)

Ordered by the impact the sources claim:

1. **Curated views or a semantic layer, not raw tables.** "the single most
   impactful practice. Metrics, dimensions and join paths are encoded
   explicitly" ([AI Technology Radar](https://ai-radar.aoe.com/architecture-pattern/text_to_sql/),
   2026). "If you let the model write SQL against `orders`, `order_items`,
   `refunds_v2`, `refunds_legacy` and eleven join keys, it will get the joins
   wrong" ([Syed, 2026-07-31](https://fawadhs.dev/blog/text-to-sql-dashboards-conversational-analytics)).
   Vercel's d0 worked "only because our semantic layer was already good
   documentation".
2. **Column descriptions with units, codes and meanings**: "status 3 means
   shipped, amounts are net, dates are in local time"
   ([Csorba](https://balazscsorba.com/blog/text-to-sql-llm-guardrails)).
   Apollo: a field named `date` says little; "ISO date string in yyyy-mm-dd
   format" says what to do
   ([Apollo, 2026-03-19](https://www.apollographql.com/blog/how-to-make-your-existing-graphql-api-ai-ready-with-apollo)).
3. **Sample values for low-cardinality columns**, so the model writes
   `country = 'AT'` and not `'Austria'` (Csorba).
4. **A business glossary**: what "active customer" or "revenue" means
   (Csorba; LLMversus: "'users' means signups, not MAU" →
   [reference architecture](https://llmversus.com/architecture/data-analyst-agent)).
5. **Verified example queries** (question + SQL pairs), retrieved by
   similarity to the new question; "every wrong answer a user reports
   should end up as a new verified example" (Csorba). Radar: "Curated
   few-shot examples … confirmed queries feed back into the example pool."
   LLMversus: include the five most similar past successful queries.
   **Confirms the overview's "sample queries are better than schemas
   alone".**
6. **Meaning from code and lineage.** OpenAI's internal data agent uses six
   context layers: table usage (metadata, lineage, historical queries),
   human annotations, "Codex enrichment" (deriving each table's meaning from
   the pipeline code), institutional knowledge (Slack, Docs, Notion),
   memory of corrections, and live runtime queries. Lesson: "Meaning lives
   in code … Pipeline logic captures assumptions, freshness guarantees, and
   business intent that never surface in SQL or metadata"
   ([OpenAI, 2026-01-29](https://openai.com/index/inside-our-in-house-data-agent/)).
   The memory example is a filter string defined in an experiment gate that
   no schema shows. **Missing from the overview**: memory of corrections,
   and pipeline code as a source of schema docs.
7. **Retrieve the schema, do not paste it.** "For large schemas, do not
   paste everything into the prompt. Retrieve the relevant views and
   descriptions per question" (Csorba). OpenAI retrieves embedded table
   context "instead of scanning raw metadata". Apollo's `search` tool finds
   types "with all the parent type information needed", and its
   `introspect`/`search` results can be minified
   ([Apollo docs](https://www.apollographql.com/docs/apollo-mcp-server/define-tools)).
   Block suggests a one-time `get_linear_graphql_schema` tool in place of
   the schema in the instructions.

Dates and time zones: every source that lists gotchas names dates
(local vs UTC, formats, fiscal years, freshness of the view's max date),
but we found **no dedicated published checklist for timestamps**. The
overview's planned timestamp section would be original material. Items seen
across sources: state the time zone of every timestamp column; state the
date format in descriptions; define fiscal periods in the glossary; check
freshness ("if the view's max date is stale, say so" — Syed); give the agent
today's date (Noodle Seed: "The model does not know today's date, the user's
time zone …" → [docs](https://docs.noodleseed.dev/docs/guides/tool-design)).

## 3. Guard rails

The sources agree on defence in depth, and on one hard rule: **a prompt
line is not a guard rail.**

- "'Only run SELECT' in the prompt is not a guardrail … Put every limit you
  rely on into the database or the code" (Csorba). The Radar says prompt-only
  read-only checks "have been bypassed in practice (e.g. in a popular
  Postgres MCP server)".
- **Database layer**: a role with `SELECT` on curated views only,
  `default_transaction_read_only = on`, `statement_timeout` (5–10 s),
  row-level security with the identity passed from the session, a read
  replica (Csorba; [DEV blueprint](https://dev.to/vivekdraxlr/building-a-natural-language-query-interface-for-your-database-a-developers-blueprint-cfk);
  Radar).
- **Code layer**: parse with a real parser (sqlglot); exactly one
  statement; only `SELECT`; tables on an allow-list; add a `LIMIT` if
  missing; `EXPLAIN` dry-run and refuse above a cost threshold
  ([connector.zone](https://connector.zone/guides/constraining-raw-sql-from-a-model-inside-a-tool-handler/);
  DEV blueprint; [Potureddi, 2026-09-01](https://dev.to/gowthampotureddi/guardrails-for-ai-written-sql-sandboxing-cost-caps-row-limits-approval-gates-28nj)).
- **Result size**: return at most N rows (example 500) and signal
  truncation with the N+1th row (connector.zone). This is the overview's
  "raise an error when the result would be 300,000 tokens", in its common
  form: cap and tell, or refuse and tell. Both appear; we found no
  comparison of "truncate with notice" against "refuse and force a narrower
  query". That is an experiment.
- **Errors back to the model**, written for the model, with one or two
  repair attempts (Csorba). "The first correction pass brings the largest
  gain" (Radar).
- **Result sanity checks**: an empty result or a single NULL where a number
  was expected is suspicious; flag a grain mismatch; flag out-of-range
  values (Syed; LLMversus: "Query returns 0 rows silently, agent presents
  'no data' as answer").
- **Prompt injection through rows**: free-text columns can carry
  instructions; if the agent can also send messages, that is the "lethal
  trifecta" (Csorba; Radar).

GraphQL-specific:

- Apollo Router **demand control** assigns a cost to fields and rejects
  operations above a threshold; tune with `@cost` and `@listSize`
  ([Apollo, 2026-02-26](https://www.apollographql.com/blog/connect-ai-agents-to-your-graphql-api-using-mcp-and-type-safe-tool-configuration)).
- **Contracts** (schema subsets) limit what the agent can see; persisted
  queries act as an allow-list ("Apollo Router only accepts operations from
  the manifest").
- `mutation_mode` defaults to `none`; "These modes are configuration gates,
  not human approval" ([Apollo best practices](https://www.apollographql.com/docs/apollo-mcp-server/best-practices)).
- A `validate` tool exists but "doesn't force a client to call that tool
  before `execute`" (Apollo docs). Inference: a wrapper that always
  validates before executing is stronger than a separate validate tool the
  agent may skip.
- Schema design for LLMs: describe every type, field, argument and enum;
  group mutation arguments into one input type (Apollo 2026-03-19).

## 4. How to evaluate a query tool

- Use **execution accuracy**: run the generated and reference queries and
  compare result sets, not SQL text. 50–100 questions from real requests,
  plus a slice that should be refused (Csorba). For Harness Tuner this is
  an eval-design matter, which is out of scope here; we note it because it
  decides whether SQL-tool experiments are measurable.

## 5. Against the overview

- **Confirms**: sample queries beat schemas alone; schemas as leaf nodes
  retrieved on demand; dates are a recurring gotcha; a wrapper can cap the
  result size.
- **Adds**: curated views / semantic layer as the first lever; sample
  values and a glossary; verified-query feedback loop from failures; memory
  of corrections; pipeline code as a source of meaning; guard rails in the
  database and in code, never only in the prompt; result sanity checks;
  cost limits (`EXPLAIN`, GraphQL demand control); fixed operations vs a
  general query tool as an explicit experiment pair.
- **Tension**: Vercel's result says that with a strong model and good docs,
  *removing* specialised tools in favour of one general tool won. The
  overview's wrapper approach and Vercel's subtraction approach are both
  valid; which wins depends on model strength and documentation quality.
  Inference: this is the overview's "two styles of agent" sidebar applied
  to data tools.
