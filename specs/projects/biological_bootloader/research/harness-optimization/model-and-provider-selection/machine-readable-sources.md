# Machine-Readable Sources the Tuner Could Query at Run Time

Research date: 2026-11-10.

## Summary table

| Source | URL | Auth | Gives | Does not give | Notes |
| --- | --- | --- | --- | --- | --- |
| Kiln built-in model list | `libs/core/kiln_ai/adapters/ml_model_list.py` in the Kiln repo; served live as `https://remote-config.getkiln.ai/kiln_config_v2.json` | None | Every (model, provider) pair Kiln can run; structured output mode; thinking levels and default; function-calling, vision, PDF support; `suggested_for_evals`, `suggested_for_synthetic_user`; `deprecated`; `featured_rank` and `editorial_notes` per model | Price, speed, benchmark scores | The only source that matches Kiln's own model ids. Already the ground truth for what the tuner can build |
| Kiln server `/api/available_models` | via the gateway | Kiln | Models the user has keys for | Price, speed, notes | The gateway projects it to `ModelView`: provider, model, name, thinking levels, structured output mode only |
| models.dev | `https://models.dev/api.json` (also `models.json`, `catalog.json`, `?type=decision`) | None | Per provider and model: price (input, output, cache read/write), context and output limits, modalities, tool and reasoning support, whether temperature/top_p/top_k are accepted, knowledge cutoff, open weights | Speed, quality | MIT, community-maintained TOML (about 7.4k GitHub stars). Kiln's web UI already reads it for prices (`app/web_ui/src/routes/(app)/models/price.ts`). Has a `decision` model type |
| OpenRouter models API | `GET https://openrouter.ai/api/v1/models` | Optional | id, `canonical_slug`, context length, modalities, pricing (prompt, completion, cache read/write, reasoning), `supported_parameters`, `default_parameters`, `expiration_date`, `knowledge_cutoff`, third-party `benchmarks`; server-side `sort` by price, throughput, latency, popularity, newest | Per-provider detail | Filter `?supported_parameters=tools`. `reasoning.mandatory` flag on models that cannot turn thinking off |
| OpenRouter endpoints API | `GET https://openrouter.ai/api/v1/models/{author}/{slug}/endpoints` | Key needed for latency fields | Per provider: price, context, max output, **quantization**, supported parameters, `supportsToolChoice` per value, implicit caching, uptime (5m, 30m, 1d), latency and throughput p50/p75/p90/p99 over 30 min, status | Retention policy (shown on the provider-logging page, and per endpoint in the web data) | The only live per-provider quality-adjacent data. Latency is null without a key |
| Artificial Analysis Data API | `GET https://artificialanalysis.ai/api/v2/language/models/free` | Free key (`x-api-key`), 1,000 requests/day, attribution required | Intelligence, Coding, Agentic indexes; six industry Capability Indexes; cost to run the index and cost per task; input/output/cache prices; median output speed, TTFT, time to first answer token, end-to-end time | On free tier: per-benchmark scores, context window, per-provider data, time per task | Pro adds per-benchmark scores and model detail. Commercial adds per-provider data and time series. Index version is in the response (`intelligence_index_version`). The old `/data/llms/models` endpoint is retired |
| AA web pages | `artificialanalysis.ai/models/{slug}` and `/providers` | None | Per-provider speed, error rate, cost per task, time per task, cache hit rate | — | Readable by an agent with a fetch tool; not a stable API |
| Benchmark boards | Scale MCP-Atlas, toolathlon.xyz, taubench.com, gorilla BFCL | None | Scores per model and effort | Machine-readable form is not documented for all | Fetch and parse; dates differ per board |
| Vendor model cards | Hugging Face `generation_config.json`, README "recommended sampling" tables; vendor migration guides | None | Recommended temperature, top_p, top_k, penalties, effort levels, max tokens | — | `generation_config.json` is machine-readable for open weights |

Sources: [AA Data API docs](https://artificialanalysis.ai/data-api/docs) and
[reference](https://artificialanalysis.ai/data-api/docs/reference) (read 2026-11-10);
[a third-party AA API note with the free-tier limit and attribution rule](https://github.com/tkellogg/model-selection/blob/main/references/artificial-analysis-api.md);
[OpenRouter models API](https://openrouter.ai/docs/guides/overview/models) and
[list endpoint reference](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties);
[OpenRouter PublicEndpoint schema](https://openrouter.ai/docs/client-sdks/typescript/models/publicendpoint);
[OpenRouter models skill](https://openrouter.ai/skills/openrouter-models);
[models.dev README](https://github.com/sst/models.dev);
Kiln repo at commit `3e67b49` (2026-11-10).

## What the tuner sees today

From the code in this repository:

- `gateway/ops.py: list_available_models` returns `ModelView`
  (`gateway/models.py:114`): `provider`, `model`, `name`, `thinking_levels`,
  `structured_output_mode`. It filters by the run's model policy.
- The `experiment-costing` skill says: "Model prices: from a live model-info
  lookup, never from memory". The repository does not name which lookup that
  is. I found no call to models.dev, OpenRouter or AA in `src/`.
- The batch-selection skill says a model the "start-up model preflight
  flagged" is left out of `allowed_models`.

So the loop agents get the *list* of models but not the facts needed to
choose among them: price, speed, verbosity, context size, benchmark tier,
data retention, or Kiln's own `editorial_notes`. The Kiln server's
`ModelDetails` (`app/desktop/studio_server/provider_api.py:160`) also has
`supports_function_calling`, `supports_vision`, `multimodal_mime_types`,
`suggested_for_evals`, `deprecated` and more, and the gateway drops them.
`featured_rank` and `editorial_notes` are in the remote config JSON that the
Kiln web UI reads, not in `/api/available_models`.

## A join key problem

Each source names models differently: Kiln `deepseek_4_1_flash_1017`,
OpenRouter `deepseek/...` slugs, models.dev `provider/model` TOML names, AA
slugs such as `deepseek-v4-1-flash-1017` plus a stable UUID. Kiln's provider
entries carry the provider `model_id` (for OpenRouter, the OpenRouter slug),
which is the best join key to OpenRouter and models.dev. AA advises stable
UUIDs as keys because names and slugs change. A refresh job needs a small
mapping table, kept with the guide.

## Proposed use (inference, not tested)

1. **Start-up enrichment, in code.** For each model in `allowed_models`, join
   Kiln facts + models.dev price and limits + AA index, cost per task, time
   per task, and verbosity (if a key is set) + OpenRouter endpoints for
   OpenRouter lanes (quantization, uptime, throughput). Hand this table to the
   planner. This is the overview's red-flag idea applied to model choice:
   code fetches facts, the LLM reasons on them.
2. **Cache the results** with a fetch date. AA free tier allows 1,000
   requests a day; one paged list call covers all models.
3. **Never let a fetched number override a measured one.** The tuner's own
   paired measurement on the train split wins.
4. **Attribution.** AA's free tier needs attribution when data is shared or
   published. Reports that show AA numbers must say so.
