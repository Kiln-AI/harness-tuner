# Draft Shape: Model Guide and Provider Guide, and How to Refresh Them

Research date: 2026-11-10. This is a proposed *shape*, not a ranking. The
example rows show the form only; their values come from the sources in the
other files of this directory and will be stale within weeks.

## Design principles (from the evidence)

1. **Split stable from volatile.** Rules about how to choose ("a provider
   switch is an experiment") change slowly. Rows ("model X is tier 1 for
   tools") change weekly. Keep them in different files, so a refresh agent
   edits rows and does not touch the rules. This matches the overview's
   maintenance-skill goal of putting each addition at the right level.
2. **A row is a (model, effort) pair, and a lane is a (model, provider)
   pair.** Benchmarks report per effort; behaviour, price, speed, format
   mode and data policy differ per provider.
3. **Tiers, not ranks.** Top scores sit within noise (±2 points on
   MCP-Atlas). Use 3 or 4 tiers per situation.
4. **Every value has a date, a source and an evidence level**
   (`vendor`, `independent`, `measured-by-us`). A row with only vendor
   evidence is marked so.
5. **Facts that code can fetch are fetched, not written.** Price, context,
   speed, uptime, deprecation come from APIs at run time
   (`machine-readable-sources.md`). The written guide holds judgement:
   situation fit, known gotchas, settings.
6. **Measured beats fetched beats written.** The tuner's paired measurement
   on the train split always decides.

## Model guide

### Situations (rows are grouped by these)

Derived from the overview's degrees of freedom and from which benchmarks
exist (`benchmarks-and-leaderboards.md`):

- single-turn classification (enum or boolean output) — includes decision
  models
- single-turn extraction / structured output from text
- single-turn document or PDF input
- single-turn with many tool calls (moderate tool set; large tool set)
- multi-turn conversational agent under policy (support-style)
- coding agent
- synthetic user (Kiln already has `suggested_for_synthetic_user`)
- judge (Kiln already has `suggested_for_evals`)

### Fields per entry

```
model_id            Kiln ModelName (join key), plus AA UUID and OpenRouter slug
family, line        e.g. claude/opus, deepseek/flash
class               frontier | mid | small | decision
effort              the effort level this row is about
open_weights        true | false
situations          list of (situation, tier 1-4, evidence level, source, date)
known_strengths     short, sourced
known_weaknesses    short, sourced (e.g. "very verbose at max effort")
settings            card-recommended sampling, effort default, locked params
                    (e.g. "Claude 5.x: no temperature/top_p; effort only")
harness_notes       vendor prompting guide link; tool-format preferences;
                    known tool-call failure modes (e.g. malformed calls)
structured_output   per lane, from Kiln
supersedes          older model ids this one replaces (for "never use older")
first_seen          release date (for the first-week rule)
retention_notes     model-level data rules (e.g. "Fable tier: 30-day
                    retention required, not ZDR by default")
fetched (not written): price, cache price, context, speed, time per task,
                    verbosity, AA indexes, deprecation
```

### Example rows (shape only; values as of the cited dates)

```
model_id: deepseek_4_1_flash_1017
class: mid    open_weights: true
effort rows:
  max:  AA v5 index 33, 174M index output tokens, TTFA 48 s  (independent, AA, read 2026-11-10)
  high: AA v5 index 29, 134M tokens, TTFA 22 s               (independent, AA, read 2026-11-10)
  none: AA v5 index 19, TTFA 1.3 s                            (independent, AA, read 2026-11-10)
known_weaknesses: very verbose at max; slow per task at max
settings: temperature 1.0, top_p 0.95 or 1.0 (HF card 2026-10-17)
harness_notes: tool calls in DSML tags; host needs deepseek_v41 parser
retention_notes: first-party DeepSeek "may train" per OpenRouter table
```

```
model_id: claude_opus_5_5
class: frontier    open_weights: false
settings: omit temperature/top_p/top_k (400 if non-default); effort low..max,
          default medium; thinking always on; no forced tool_choice; no prefill
          (Anthropic migration guide, read 2026-11-10)
harness_notes: re-run effort sweep after migrating from Opus 5 (default changed)
```

## Provider guide

### Fields per provider

```
provider_id         Kiln ModelProviderName
kind                first-party | cloud (Bedrock, Vertex, Azure) | open-weight host | aggregator | custom silicon
catalogue           broad | narrow (list) — fetched where possible
speed_profile       written summary + fetched per-model throughput and TTFT
reliability         fetched: AA error rate, OpenRouter uptime
retention           ZDR | safety-retention | N days | unknown; trains yes/no;
                    source and date; model- and feature-level exceptions
rate_limits         notes (e.g. "per-minute throttling on long runs")
quality_notes       tool-call parser issues, first-week behaviour, vendor-verifier results
kiln_notes          Kiln-specific behaviour (e.g. OpenRouter: Kiln sets provider.order;
                    lanes that need json_instruction_and_object)
```

### Example rows (shape only)

```
provider_id: cerebras
kind: custom silicon   catalogue: narrow
speed_profile: highest throughput on served models (gpt-oss-120b ~1.7-1.9k tok/s AA; vendor says ~3k)
rate_limits: per-minute throttling reported on long runs
retention: ZDR, does not train (OpenRouter table, read 2026-11-10)
```

```
provider_id: openrouter
kind: aggregator
quality_notes: same id can differ by host (20+ pt tau-bench gap reported);
               Auto Exacto on for tool requests under default routing;
               first-week variance; silent HTTP-200 failures
kiln_notes: Kiln sets provider.order on every request (bypasses default routing,
            inference); no zdr flag sent - set ZDR at account level
retention: per endpoint, see provider table
```

### Restriction checks (bridge to overview section 8)

A restriction is a filter over (provider, model, feature). Examples the
guide must be able to express:

- "ZDR only" → drop lanes whose provider is not ZDR, and drop models with
  model-level retention rules (Fable tier on Anthropic), and features that
  are not ZDR (batch).
- "OpenRouter allowed, ZDR only" → lane allowed only with `zdr: true` or an
  account setting; Kiln does not send `zdr` today.
- "No Chinese models" → needs a `creator_country` field per model family.
  None of the machine-readable sources I read exposes this directly; AA and
  models.dev give the creator, so a small mapping is needed.

The tuner already enforces a model policy in code (`examples/model-policy.json`,
`allowed_models`). The guide should feed that policy, not replace it.

## Refresh process (proposed)

| What | Source | Frequency | How |
| --- | --- | --- | --- |
| New models and lanes Kiln supports | Kiln `ml_model_list.py` / `kiln_config_v2.json` | each run (fetched) | code |
| Price, context, modalities, param support | models.dev `api.json`; OpenRouter `/api/v1/models` | each run (fetched) | code |
| Speed, time per task, verbosity, indexes | AA Data API free tier | each run, cached daily | code; attribute AA |
| Per-provider uptime, quantization, throughput | OpenRouter `/endpoints` | each run for OpenRouter lanes | code |
| Situation tiers | AA indexes; MCP-Atlas; Toolathlon-Verified; τ³-bench; GDP.pdf; AA Coding Agent Index | weekly, or on a major release | agent edits rows; one PR per refresh |
| Model-card settings, migration guides | vendor docs, HF cards | on each new model in Kiln's list | agent adds `settings` and `harness_notes` |
| Retention policies | OpenRouter provider-logging page; vendor data-retention pages | monthly | agent edits `retention` |
| Provider quality incidents | OpenRouter performance tab; vendor verifiers (Kimi Vendor Verifier) | on new model launch | agent edits `quality_notes` |
| Our own measurements | Harness Tuner runs (state folder `reports/`) | after each run | **never** into `agent_files/` (AGENTS.md rule); a separate measured layer |

Note on the repository rule: AGENTS.md forbids "measured numbers, lever
advice and what worked in an earlier run" in `agent_files/`, because every
loop agent loads it. The rule targets findings from tuner runs, but a model
guide with tiers and dated numbers is close to it, and this whole project
(a skill of lever advice) is in tension with it. Inference: the guide's
volatile rows could be delivered to the loop as fetched or generated run
input (like `allowed_models`), not as a skill file, and the stable rules can
be a skill. This is a design decision for the functional spec.
