# Provider Facts: Speed, Data Retention and Aggregator Routing

Research date: 2026-11-10. Speed numbers are snapshots; AA reports 72-hour or
5-day medians and they change weekly.

## 1. Speed

### Key facts

- **Speed is a property of the host and the model together, not of the
  model.** On 2026-10-20 the fastest measured host served Kimi K3 at about
  422 tok/s and Moonshot's own API at about 42 tok/s, a 10× spread for the
  same weights ([Taskade, citing AA, 2026-11-07](https://www.taskade.com/blog/inference-as-a-service)).
  On Llama 3.3 70B, AA shows a 4,867% spread between fastest and slowest of
  14 providers ([AA provider page](https://artificialanalysis.ai/models/llama-3-3-instruct-70b/providers)).
- **Cerebras is the throughput leader on the models it serves.** AA measured
  gpt-oss-120b at about 1,700 to 1,900 tok/s; Cerebras lists about 3,000.
  Testers report lower sustained speed once per-minute throttling starts
  ([Morph, 2026](https://www.morphllm.com/cerebras-pricing);
  [CloudZero, 2026-09-22](https://www.cloudzero.com/blog/llm-inference-providers)).
  Its catalogue is very small: "one production model plus previews"
  (CloudZero).
- **Groq is fast and has a small catalogue.** On 2026-08-18 its production
  text models were gpt-oss-20b (about 1,000 tok/s) and gpt-oss-120b (about
  500 tok/s); in October it priced four text models
  ([BenchLM, 2026-08-18, updated 2026-10-20](https://benchlm.ai/blog/posts/groq-is-not-a-model)).
  In one AA snapshot of gpt-oss-120b, Groq (477 tok/s) was slower than
  Together AI (568 tok/s) and far behind Cerebras (1,916 tok/s)
  ([kingy.ai, citing AA](https://kingy.ai/blog/ai-inference-providers-compared/)).
- **New fast tiers exist for large models.** LithosAI "Ultra", Inco "Fast",
  OpenAI "Fast/Ultrafast" and Anthropic `speed: "fast"` sell speed at a
  higher price. Kimi K3.1 Pro: LithosAI Ultra 4.7 min per AA task vs Kimi's
  own API 37.6 min ([AA providers leaderboard](https://artificialanalysis.ai/leaderboards/providers)).
- **Cloudflare Workers AI is slow on throughput in independent data.**
  gpt-oss-120b at 116 tok/s vs 1,916 on Cerebras (kingy.ai, citing AA). AA
  lists a 24k context window for Llama 3.3 70B on Cloudflare vs 128k to 131k
  elsewhere. One practitioner saw structured-output failures and 60 to 89 s
  for four cases on several Workers AI models
  ([rdlabo.dev, 2026-09-02](https://rdlabo.dev/articles/cloudflare-workers-ai-model-benchmark);
  small sample). Counterpoint: warm time to first token at the edge is low
  (180 ms p50) ([gloss.run, 2026-05-05](https://gloss.run/post/run-an-llm-at-the-edge-on-cloudflare-workers-ai-with-real-numbers)).
- **Reliability differs by host for closed models too.** AA shows Claude
  Opus 5.5 (max) on Microsoft Azure with a 13.1% endpoint error rate and
  29.1 min per task, against 0.0% on Anthropic for other effort levels
  ([AA providers leaderboard](https://artificialanalysis.ai/leaderboards/providers)).

### Overview check

- "Groq and Cerebras offer extremely fast inference": **confirmed, with
  limits**. The catalogue is tiny, rate limits are tight, the vendor numbers
  are above independent ones, and other hosts can beat Groq on a given
  model. A fast provider is only a candidate if it serves the chosen model.
- "Cloudflare is slow": **partly confirmed** for throughput and context
  size; not for edge first-token latency.

## 2. Zero data retention (ZDR)

### By provider, as OpenRouter records them (read 2026-11-10)

From [OpenRouter provider logging](https://openrouter.ai/docs/guides/privacy/provider-logging).
This is the *default policy when called through OpenRouter*; direct contracts
can differ.

- Zero retention, does not train: Amazon Bedrock, Azure, Google Vertex, Groq,
  Cerebras, Fireworks, Together, DeepInfra, Baseten, Mistral, Moonshot AI,
  Z.ai, SiliconFlow, Cloudflare, Nebius, Parasail, SambaNova, Novita,
  Featherless, TypeSafe, and others.
- Retained 30 days, does not train: Anthropic, Claude Platform on AWS, Meta,
  Cohere, Xiaomi, SpaceXAI, Nex AGI.
- Retained 55 days: Google AI Studio (Vertex is ZDR).
- Retained for an unknown period: OpenAI, Alibaba Cloud Int., MiniMax,
  StepFun, StreamLake, Sakana, Friendli and others.
- May train on prompts: DeepSeek (first party), NVIDIA, Liquid, Thinking
  Machines.

OpenRouter: "If OpenRouter is not able to establish or ascertain a clear
policy for a provider or endpoint, we take a conservative stance and assume
that the endpoint both retains and trains on data." A new **Safety
Retention** tier (Oct 2026) retains for abuse prevention only and "still
aren't ZDR" ([OpenRouter ZDR blog](https://openrouter.ai/blog/insights/zero-data-retention)).
The `zdr` request flag is an OR with account settings: it can force ZDR, not
turn it off ([ZDR docs](https://openrouter.ai/docs/guides/features/zdr)).

### First-party fine print that changes model choice

From [Anthropic API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention) (read 2026-11-10):

- ZDR is an arrangement on a Commercial organisation, not a default.
- "Claude Fable 5.2, Claude Mythos 5.2, Claude Fable 5.1, Claude Mythos 5.1,
  Claude Fable 5, and Claude Mythos 5: These models require 30-day data
  retention and are not available under ZDR unless expressly authorized by
  Anthropic." So under a ZDR restriction, the top Anthropic tier is out by
  default.
- Not ZDR-eligible: Batch API (29-day retention), Files API, Agent Skills
  API, MCP connector, code execution and programmatic tool calling, Managed
  Agents. Structured outputs are "Yes (qualified)": the JSON schema is cached
  up to 24 hours.

Inference: a ZDR restriction is not "a list of providers". It is a set of
(provider, model, feature) triples. The guide must record model-level and
feature-level exceptions.

### Overview check

- "Providers X, Y and Z offer zero data retention": **confirmed and made
  concrete** above. **Missing from the overview:** model-level exceptions
  (Fable tier), feature-level exceptions (batch, files), and that the same
  vendor can differ by surface (Google AI Studio 55 days vs Vertex ZDR).

## 3. Aggregator routing gotchas (OpenRouter)

### How OpenRouter routes by default

From [OpenRouter provider routing docs](https://openrouter.ai/docs/guides/routing/provider-selection) (read 2026-11-10):

- Default routing weighs effective price (with real cache hit rate),
  throughput, TTFT, 429 rate, reliability, and "whether the provider's tool
  calls come back well formed". Follow-up requests in a conversation "stay
  with the same provider while its prompt cache is warm".
- "Default routing ... is bypassed when you set `sort` ..., use the `:floor`
  or `:nitro` shortcuts, set `order`, or use your own provider key."
- With `tools`, routing makes "a best effort" to pick providers that support
  tools; `require_parameters` (default `false`) is needed to make it strict.
- **Auto Exacto** (on by default for tool-calling requests since
  2026-03-12) de-ranks providers that are statistical outliers on
  throughput, tool-call telemetry (valid JSON, real tool name, schema match)
  and scheduled τ-bench/GPQA runs. Reported effect: GLM-5 tool-call errors
  down 88% (about 8% to about 1%); DeepSeek V3.2 τ-bench 69% → 74%
  ([OpenRouter, 2026-03-12](https://openrouter.ai/blog/announcements/auto-exacto)).

### Gotchas, with evidence

1. **Same model id, different model behaviour.** One practitioner's 18M
   messages: DeepSeek V4 Flash first-party about 90% GPQA and 81% τ-bench; a
   third-party host on the same id about 75% and 58%
   ([Pinggy summary of mmoustafa.com, 2026-09-14](https://pinggy.io/blog/openrouter_production_provider_routing_pitfalls)).
   Moonshot found 20+ point drops on some Kimi K2 Thinking hosts and built a
   vendor verifier ([Moonshot on X, 2025-11-10](https://x.com/Kimi_Moonshot/status/1987892275092025635);
   [K2-Vendor-Verifier](https://github.com/MoonshotAI/K2-Vendor-Verifier);
   [Kimi Vendor Verifier, Apr 2026](https://gigazine.net/gsc_news/en/20260421-kimi-vendor-verifier)).
2. **The cause is mostly tool-call parsers, not quantization.** OpenRouter:
   "We haven't seen a measurable impact on tool-call quality from
   quantization alone. The actual culprit, more often than not: tool-call
   parsers." The `quantizations` filter is what a provider *declares*, not a
   verified fact (Pinggy).
3. **The first-week problem.** Provider variance "peaks right when a model
   launches". OpenRouter observes it is "very rough in the first 5 days"
   ([OpenRouter on X, 2026-03-12](https://x.com/OpenRouter/status/2032127261709676991)).
4. **Silent failures with HTTP 200.** `content: null` with `finish_reason:
   "stop"`; missing `usage`; `reasoning.effort` accepted but ignored (same
   reasoning tokens at low and high); tool calls leaked as plain text; vision
   "no image provided" (Pinggy).
5. **Pinning a short provider list with `allow_fallbacks: false` caused a
   full outage** for one production user (Pinggy).
6. **Gateway transforms change the request.** Kiln notes that OpenRouter
   routes Claude Fable to Bedrock/Azure backends that ignore `json_schema`
   while reasoning is on, and that the bare `reasoning_effort` is dropped on
   tool calls for GPT-6.1 (`ml_model_list.py`). OpenRouter registers Opus
   5.5 as mandatory-reasoning and rejects `reasoning: {enabled: false}` with
   a 400 ([OpenRouter Claude 5.5 migration](https://openrouter.ai/docs/cookbook/evaluate-and-optimize/model-migrations/claude-5-5)).
7. **Data policy follows the endpoint, not the model.** See section 2.
   Requests without `zdr: true` or account ZDR can land on a retaining host.
8. **Price.** OpenRouter passes provider prices through "without any markup"
   and charges 5.5% on Stripe credit purchases; BYOK is 5% above $25,000 a
   month (Pinggy, citing the OpenRouter FAQ). The tuner's costing skill says
   "Direct-provider lanes are typically cheaper than aggregator lanes"; the
   gap is the purchase fee plus any routing to a dearer host, not a token
   markup.

### How Kiln uses OpenRouter today (from code)

`libs/core/kiln_ai/adapters/model_adapters/litellm_adapter.py`, `build_extra_body`:

- On **every** OpenRouter request Kiln sets
  `provider.order = ["fireworks", "parasail", "together", "deepinfra",
  "novita", "groq", "amazon-bedrock", "azure", "nebius"]`, "for more
  deterministic routing", with `allow_fallbacks` left at its default `true`.
- Special cases set `require_parameters` and `ignore: ["deepinfra"]` (R1
  models, logprobs).
- Kiln sends no `zdr`, `data_collection`, `quantizations` or `sort` field.

Inference (not tested): per the OpenRouter docs, setting `order` bypasses
default routing, so Kiln's OpenRouter requests probably do not get Auto
Exacto's tool-quality de-ranking. A ZDR restriction on an OpenRouter lane
must therefore be set at the OpenRouter account level, not by Kiln. Both
points belong in the provider guide and in the Kiln capability-gap
subtopic. This is a Kiln-gap item; I note it and do not research it further.

## 4. Practical rules for an optimiser (draft, from the evidence above)

1. Prefer the first-party endpoint for an open model in its first weeks.
2. Treat a provider switch as a real experiment: it can change quality,
   format errors and data policy, not only speed.
3. For speed, compare **time per task** or per-item latency, not tokens per
   second (AA "Time per Task"; the tuner's speed objective already does
   this).
4. Read OpenRouter's per-endpoint uptime and throughput at plan time; drop a
   degraded endpoint before a run.
5. Under ZDR, check model-level and feature-level exceptions, not only the
   provider.
