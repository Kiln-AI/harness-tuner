# Model Families, Known Strengths and Model-Specific Settings

Research date: 2026-11-10. Model names are those in Kiln's model list at
commit `3e67b49` (2026-11-10) and on the web today.

## 1. Settings from model cards

### What the cards say now

| Model family | Source | Recommended settings | Constraint that matters to Kiln |
| --- | --- | --- | --- |
| Claude Opus 5.5, Sonnet 5.5, Haiku 5.5, Fable 5.x, Opus 4.7/4.8 | [Anthropic thinking docs](https://platform.claude.com/docs/en/build-with-claude/thinking); [Opus 5.5 migration guide](https://platform.claude.com/docs/en/models/opus-5-5/migration-guide) | Omit `temperature`, `top_p`, `top_k`. Control depth with `effort` (`low` … `max`). Opus 5.5 default effort is `medium` (Opus 5 was `high`) | "non-default `temperature`, `top_p`, or `top_k` values return a 400 error on every request". The `top_p` default is 0.99, so `top_p: 1` is a 400. Opus 5.5: thinking cannot be disabled; forced `tool_choice` is rejected; no assistant prefill. Sonnet 5.5 lowest setting is `between_tools` |
| DeepSeek V4.1 Flash | [HF model card, 2026-10-17](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash); [NVIDIA NIM card](https://build.nvidia.com/deepseek-ai/deepseek-v4.1-flash/modelcard) | `temperature 1.0`, `top_p 0.95` or `1.0`, `max_tokens ≥ 256K`. Reasoning effort continuous 1 to 100 | No Jinja chat template in the release; tool calls are written as DSML tags and need the `deepseek_v41` parser. A host with a wrong parser breaks tool calls |
| Kimi K3, K3.1 Pro | [HF Kimi-K3](https://huggingface.co/moonshotai/Kimi-K3); [NVIDIA NVFP4 card](https://huggingface.co/nvidia/Kimi-K3-NVFP4); [morphllm note](https://www.morphllm.com/kimi-k3) | `temperature 1.0`; `top_p 0.95` for single-step, `1.0` for agentic; penalties 0 | Moonshot's quickstart says these are fixed and to omit them; a harness that sends `temperature: 0` is misconfigured |
| Qwen 3.8 (hybrid models) | [Unsloth Qwen3.8 docs, 2026-09-23](https://unsloth.ai/docs/models/qwen3.8); [Ollama issue 17197](https://github.com/ollama/ollama/issues/17197) | Thinking: `temp 1.0, top_p 0.95, top_k 20, presence_penalty 0`. Non-thinking: `temp 0.7, top_p 0.8, top_k 20, presence_penalty 1.5`. Precise coding: `temp 0.6` | Two presets by mode. Kiln's run config cannot set `top_k` or `presence_penalty` |
| Older Kimi K2.5 on Fireworks | Kiln `ml_model_list.py` comment | Fireworks needs `top_p` exactly 0.95 (thinking) or `temperature` 0.6 (non-thinking) | Kiln commented the provider out, because "We currently always send temperature=1.0 and top_p=1.0 from run_config" |
| GPT-6.x (OpenAI) | Kiln `ml_model_list.py` comments | Thinking levels per model | Kiln note: "OpenAI rejects reasoning_effort + tools on /v1/chat/completions for gpt-5.4+", so `supports_function_calling=False` on the OpenAI lane for GPT-6.1 Sol until Kiln routes to `/v1/responses`. On OpenRouter, the bare `reasoning_effort` is "silently dropped on tool calls", so Kiln sends the reasoning object |

How independent evaluators set temperature: AA uses "0 for non-reasoning
models, 0.6 for reasoning models (unless another temperature is recommended
by the model lab)" ([AA methodology](https://artificialanalysis.ai/methodology/intelligence-benchmarking)).

### How Kiln applies sampling settings (from code)

- `KilnAgentRunConfigProperties` (`libs/core/kiln_ai/datamodel/run_config.py`)
  has `temperature` (default 1.0), `top_p` (default 1.0),
  `structured_output_mode`, `thinking_level`, `tools_config`,
  `input_transform`. There is no `top_k`, no penalty, and no `max_tokens`.
- `litellm_adapter.py` always sends `temperature` and `top_p`, with
  `drop_params=True` so LiteLLM drops what it thinks the model does not
  accept. For providers flagged `temp_top_p_exclusive` (all current Claude
  entries) it deletes a value equal to 1.0, and raises an error if both are
  custom.
- Inference from code and the Anthropic docs: on Claude 5.x a custom
  `temperature` (for example 0.2) still reaches the API and gets a 400. So
  temperature and top_p are **not levers** on current Claude models. The
  thinking level (effort) is the only sampling-type lever.

### What this means for the overview

- The overview says "one DeepSeek model needed a lower temperature for
  reliable tool calls and JSON". I could not find a current source for that.
  The current DeepSeek V4.1 Flash card recommends `temperature 1.0`. The case
  may be an older DeepSeek model or a specific host. **Unverified.**
- The overview says "most settings stay at default". Confirmed and
  strengthened: for current frontier closed models, sampling settings are
  locked by the vendor. For open-weight models, the card's preset is the
  right default, and Kiln's default (1.0 / 1.0) often matches it already.
- **Missing from the overview:** the settings that matter now are (a) the
  thinking or effort level, (b) the structured output mode per provider, and
  (c) the provider itself. Sampling is the smallest of these.

### Structured output mode is per provider, not per model

From Kiln's model list:

- Claude Fable 5.1/5.2 on OpenRouter uses `json_instruction_and_object`,
  because "OpenRouter routes Claude Fable to Bedrock/Azure backends that
  ignore response_format json_schema while reasoning is on (returns prose)".
  The same model on Anthropic direct uses `json_schema`.
- MiniMax M2.5 on OpenRouter: "accepts json_schema but M2.5 ignores the
  constraint; json_instruction_and_object works".
- Fireworks lanes for some models: "fireworks doesn't support json_schema,
  just json_mode".

So a (model, provider) pair has its own correct mode. Kiln already stores the
suggested mode per pair, and the gateway passes it to the agent. A change of
provider is also a change of structured output behaviour, and needs a
re-measure of format errors. BFCL v4 also found that tool documentation in
JSON format beats XML and Python formats for nearly all models
([BFCL format sensitivity](https://gorilla.cs.berkeley.edu/blogs/17_bfcl_v4_prompt_variation.html)).

## 2. Known strengths by family (checked against the overview's examples)

| Overview claim | What sources say (date) | Verdict |
| --- | --- | --- |
| "Opus is nearly always best at coding; GPT Sol sometimes catches up" | AA leaderboard (Oct 2026) shows Claude Opus 5.5 top on the Intelligence Index (58.4) ahead of GPT-6 Astra (56.9) and GPT-6.1 Sol (54.5) ([modelgrep, citing AA, 2026-10-29](https://modelgrep.com/leaderboard)). Kiln's `featured_rank` puts Claude Fable 5.1 first, GPT-6 Astra second, Opus 5.5 third, GPT-6.1 Sol fourth. Harness changes can reverse Claude vs GPT by 30+ points on Terminal-Bench 4 ([arXiv 2610.00917](https://arxiv.org/html/2610.00917v1)) | Partly confirmed. Anthropic models are near the top, but the Fable tier now sits above Opus, and the order depends on the harness |
| "DeepSeek-V4-1-Flash is excellent value but reasons a lot, so it is usually not fast" | AA: V4.1 Flash 1017 (max) is #2/26 on intelligence for its class, $0.60 per index task, verbosity #25/26 (174M output tokens vs median 99.3M), time to first answer token 48.47 s. At `high`: 29 points, 22.25 s. Non-reasoning: 19 points, 1.31 s ([AA pages](https://artificialanalysis.ai/models/deepseek-v4-1-flash-1017)) | Confirmed. The lever is the thinking level: high costs 4 index points and halves the wait |
| "Gemini models are much better at PDFs and documents" | GDP.pdf launch snapshot (Apr 2026): Gemini 3.1 Pro 15% [9.3, 23.3], Claude Opus 4.7 14%, GPT-5.4 11%; the authors call the top order "provisional" ([arXiv 2607.11192](https://arxiv.org/html/2607.11192v1)). Surge's later board: GPT-5.5 25%, Opus 4.8 23%, Gemini 3.1 Pro 17% ([Surge, Oct 2026](https://surgehq.ai/blog/gdp-pdf-can-100b-ai-models-master-the-documents-that-run-the-world)). OpenAI reports GPT-6.1 Sol above Opus 5.5 on GDP.pdf (vendor claim) ([Humblytics summary, 2026-09-28](https://humblytics.com/blog/best-ai-model-for-ab-testing)). Gemini is still widely called the multimodal and speed leader ([Stob.AI, 2026-07-21](https://stob.ai/blog/kimi-vs-chatgpt-vs-claude-vs-gemini)) | Contested. Gemini is a strong candidate for documents and is cheap and fast, but it is not clearly ahead on the one independent PDF benchmark. Kiln also marks Fable on OpenRouter `multimodal_requires_pdf_as_image`, a per-lane detail that changes PDF results |
| "Never use an older version when a newer one of the same line is out" | AA marks superseded models "deprecated" and benchmarks them less often; Kiln has a `deprecated` flag per provider. But new versions change behaviour: Opus 5.5 changes default effort, removes thinking-off and forced tool choice ([migration guide](https://platform.claude.com/docs/en/models/opus-5-5/migration-guide)); DeepSeek V4.1 Flash is a new architecture with a new tool format; new open models have a "first-week problem" of broken tool parsers on third-party hosts ([OpenRouter Auto Exacto, 2026-03-12](https://openrouter.ai/blog/announcements/auto-exacto)) | Confirmed as a default, with two caveats: a version change is a harness change (re-measure), and in its first week a new open model is safest on its first-party endpoint |

### Other family facts that sources support (dated)

- **Open-weight models are now competitive on tool use.** Toolathlon-Verified
  top two (Sep 2026) are GLM 5.3 Flash (78.4%) and Kimi K3 (76.5%), above
  Claude Opus 4.8 (76.2%) ([toolathlon.xyz](https://toolathlon.xyz/docs/leaderboard)).
  MCP-Atlas top ten include Qwen 3.8, GLM-5.3 and Kimi K3 within ±2 points
  of the closed leaders. This weakens any rule of the form "closed models for
  tools".
- **Open-weight results depend on the host.** See
  `providers-and-routing.md`. A 20-point τ-bench gap between first-party and
  a third-party host is reported for DeepSeek V4 Flash.
- **Reasoning-heavy open models are slow per task even at high token
  rates.** Kimi K3.1 Pro: 33 tok/s, 174.8 s to first answer token; GLM-5.3
  max: 103.9 s ([gpuwerk summary of AA data, 2026-11-07](https://gpuwerk.com/blog/reasoning-model-token-budget/)).
  Under the tuner's speed objective, latency per item, not tokens per second,
  is the right metric. The batch-selection skill already says this.
- **Decision (classification-only) models are a separate category.** Kiln
  lists Jev 1.13 (TypeSafe), which "only runs tasks whose output schema is
  enums, booleans, bounded integers, or numbers from 0 to 1". models.dev
  exposes `?type=decision`, and OpenRouter has an `/api/alpha/decisions`
  route. The decision-model lever belongs to "Architecture levers"; for this
  subtopic, the point is that the model guide needs a separate row type for
  them.
