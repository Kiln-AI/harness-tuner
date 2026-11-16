# Prompt caching mechanics that affect harness design

Research date: 2026-11-10. Provider docs change often; each section names
its source page. **Inference** marks this author's reasoning.

## 1. The common mechanism

All three major providers cache a **prefix**: identical tokens from the
start of the request. A change at any position invalidates everything after
it.

- Anthropic: "Prompt caching references the entire prompt: `tools`,
  `system`, and `messages` (in that order)". "Cache hits require 100 percent
  identical prompt segments"
  ([Claude prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).
- OpenAI: "Cache reuse requires the entire rendered prefix to match"
  ([OpenAI prompt caching](https://platform.openai.com/docs/guides/prompt-caching)).
- Gemini: "Implicit caching is enabled by default for all Gemini 2.5 and
  newer models" ([Gemini context caching](https://ai.google.dev/gemini-api/docs/caching)).

## 2. Provider facts (as of 2026-11-10)

### 2.1 Anthropic (Claude API)

Source: [prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).

- **Opt-in.** "Messages API: Opt in with `cache_control`, at the top level
  or on blocks." Without it, nothing is cached. (Managed Agents: always on.)
- **Price multipliers.** 5-minute write 1.25×, 1-hour write 2×, read 0.1× of
  base input. Exceptions: reads 0.05× on Opus 5.5 and Sonnet 5.5; 0.025× on
  Fable 5.1/5.2 and Mythos 5.1/5.2.
- **Lifetime.** 5 minutes by default, refreshed free on each hit. Lifetime
  is measured "from the start of the request", so a long generation eats
  into it.
- **Minimum length.** 512 tokens (Fable 5.x, Opus 5/5.5, Sonnet 5.5, Haiku
  5.5); 1,024 (Opus 4.8, Sonnet 5, Sonnet 4.6, Sonnet 4.5); 2,048 (Opus 4.7);
  4,096 (Opus 4.5, Opus 4.6, Haiku 4.5). Shorter prompts "are processed
  without caching, and no error is returned."
- **Breakpoints.** Up to 4. A write happens only at a breakpoint. Reads look
  back at most 20 blocks for an earlier write.
- **The common mistake** (quoted): a breakpoint on a block that changes
  every request ("a timestamp and the user message") gives "a fresh cache
  write on every request and never get a read." Place the breakpoint "at the
  end of the static prefix, not on the varying block." Automatic caching has
  the same problem in that layout.
- **Invalidation table.** Changing tool definitions invalidates tools,
  system and messages caches. Web search, citations or speed toggles
  invalidate system and messages. `tool_choice` and images invalidate
  messages. Thinking parameters and `output_config.effort` always invalidate
  messages and, on some models, system and tools.
- **Mid-conversation changes.** On Fable 5.x, Opus 4.8/5/5.5, Sonnet 5.5 and
  Haiku 5.5, "you can add a new system instruction partway through a
  conversation without invalidating the system or message caches" by
  appending a `{"role": "system"}` message. A beta (`inline-tools-2026-09-15`)
  lets a tool be added mid-conversation without editing `tools`.
- **Isolation.** Caches are per workspace on the Claude API (per organization
  on Bedrock and Google Cloud). Never shared across organizations.
- **Concurrency.** "a cache entry only becomes available after the first
  response starts. If you need cache hits for parallel requests, wait for the
  first response before sending subsequent requests."

### 2.2 OpenAI

Source: [OpenAI prompt caching](https://platform.openai.com/docs/guides/prompt-caching).

- On by default. Minimum 1,024 tokens for GPT-5.6 and later.
- GPT-5.6+: write 1.25× for the default 30-minute lifetime; 2.5× for the
  12-hour lifetime; read 0.1× (0.05× on GPT-6.1 Sol and Terra).
- Implicit or explicit breakpoints, up to four cache writes per request.
- Settings in the prefix include `model`, `tools`, `parallel_tool_calls`,
  `text.format`, `reasoning.effort`, `text.verbosity`, and compaction.
- The page's worked example: "Across ten requests, one write and nine full
  reads cost 2.15× at that rate, compared with 10× without caching."

### 2.3 Gemini

Source: [Gemini context caching](https://ai.google.dev/gemini-api/docs/caching).

- Implicit caching by default on 2.5 and newer. Minimum 4,096 tokens on
  current models (Gemini 3.1 Pro Preview, 3.6/3.8 Flash, 4 Argon); 2,048 on
  2.5. Explicit caching is also offered (not read in detail).

## 3. Practitioner rules for the harness

### 3.1 Claude Code team (2026-04-30)

[Shihipar, Prompt caching is everything](https://claude.dev/blog/lessons-from-building-claude-code-prompt-caching-is-everything/):

> "we build our entire harness around prompt caching ... we run alerts on our
> prompt cache hit rate and declare SEVs if they're too low."

Order: "1. Static system prompt & Tools (globally cached) 2. CLAUDE.md
(cached within a project) 3. Session context (cached within a session) 4.
Conversation messages".

Things that broke it: "an in-depth timestamp in the static system prompt,
shuffling tool order definitions non-deterministically, and updating
parameters of tools".

Lessons: "Use messages instead of system prompt changes"; "Don't change
tools or models mid-conversation"; "Defer tool loading instead of removing
tools"; "Monitor your cache hit rate like you monitor uptime"; side
computations "(compaction, summarization, skill execution)" must share the
parent's prefix.

### 3.2 Manus (2025-07-18)

[Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus):

- "the KV-cache hit rate is the single most important metric for a
  production-stage AI agent."
- Input-to-output ratio about 100:1 in their agent.
- "Keep your prompt prefix stable", "Make your context append-only",
  deterministic JSON serialization, and "Mask, Don't Remove" tools.

### 3.3 A measured fix (practitioner, 2026-02-21)

Moving per-run values out of the system prompt into the first user message
raised one team's cache hit rate from about 85% to 98.7% and cut cost 6%
([Dan's Notebook](https://dansnotebook.com/posts/prompt-caching-is-everything)).
A single report; treat as illustrative.

## 4. What this means for "long cached prompt against skill tree"

### 4.1 Where each part of the context sits

- System prompt and tools: before the user message. Identical across runs of
  the same harness. **Can be shared across runs.**
- A skill body read by a tool call: in the message history, **after** the
  user message (Claude Code: "enters the conversation as a single message",
  [skills docs](https://code.claude.com/docs/en/skills)). The user message
  differs per run.

**Inference:** a skill fragment can be cached only inside one run (later
turns of the same conversation re-read it at 0.1×). It is never shared
across runs, because its prefix contains that run's user message. The
system prompt is shared across all runs within the cache lifetime. This is
the mechanism behind the overview's claim. The overview's stated reason
("rarely read in the exact order that hits the cache") is partly right: the
order matters inside a run, but the main reason is the position after the
per-run input.

### 4.2 An illustration (assumed numbers)

Assumptions: Claude Sonnet 4.6, $3/MTok input, $0.30 read, $3.75 5-minute
write ([pricing table on the caching page](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).
Output tokens ignored. A high-volume agent, so the cache is warm.

- **A. 20k-token cached system prompt, one model call.** Read 20k × $0.30/M
  = $0.006, plus the user message. One round trip.
- **B. 2k system prompt, agent reads 10 fragments of ~1k over 15 tool
  calls.** Context grows from ~3k to ~17k; the sum of input over 15 calls is
  about 150k tokens. With in-run caching: ~12k new tokens written ($0.045) +
  ~138k read ($0.041) ≈ $0.086. Without any caching: 150k × $3/M = $0.45.
  Fifteen round trips.

**Inference:** in this illustration A is about 14× cheaper than B with
caching, about 75× cheaper than B without caching, and has 1 round trip
against 15. A cold write of A costs $0.075, still below uncached B. The
result reverses only if A's extra 18k tokens hurt quality (context rot,
small models) or if most runs need none of it. The tuner can measure both.

### 4.3 Conditions that remove the advantage

- Low traffic: calls more than 5 minutes apart miss (1-hour lifetime costs
  2× to write). Eval runs are bursty, so evals may show cache hits that
  production at low volume will not.
- Prompt below the model's minimum (512–4,096 tokens): silently uncached.
- Per-run values (date, user id, account data) placed in the system prompt.
- A harness that edits tools or the system prompt between calls.
- Parallel cold requests: the first must start responding before others hit.
- **Opt-in providers** (Anthropic) when the harness does not send
  `cache_control`. See 5.

## 5. Kiln note (for the "Kiln capability gap" subtopic)

Read from the Kiln repository at `/home/user/kiln-ai/kiln`, commit
`3e67b49` (2026-11-10):

- `libs/core/kiln_ai/adapters/model_adapters/base_adapter.py`:
  `automatic_prompt_caching: bool = False`, documented as "a cost
  optimization and does not affect model output".
- `litellm_adapter.py`: when the flag is on, it sets
  `cache_control_injection_points = [{"location": "message", "index": -1}]`,
  that is, a breakpoint on the **last message**.
- The flag is set to `True` only in `synthetic_user/eval_drive.py` and
  `synthetic_user/runner.py` (multi-turn synthetic-user drives). No other
  non-test use was found.

**Inference, not verified by a live run:**

1. A single-turn Kiln task on an Anthropic model gets no prompt caching
   today, so the "long cached prompt" lever does not exist for Claude
   targets in Kiln. OpenAI and Gemini cache implicitly and are not affected.
2. Where caching is on, the breakpoint is on the last message. Per the
   Anthropic docs, that caches within a conversation but does not write an
   entry at the end of the system prompt, so separate runs do not share the
   system-prompt prefix.
3. Kiln run configs do not appear to expose a caching setting to the tuner.

The Kiln-gap subtopic should confirm these and decide whether a run-config
caching option (with a breakpoint after the system prompt and tools) is a
needed API. Until then, a tuner experiment that compares "long prompt"
with "skill" on a Claude model measures the uncached case.

## 6. Cache hit rate as a diagnostic

Usage fields to log: Anthropic `cache_creation_input_tokens` and
`cache_read_input_tokens`; OpenAI `cached_tokens`; Gemini
`total_cached_tokens` (pages above). Hit rate is computable by code from
traces. It belongs with the "Trace diagnostics" subtopic as a metric.
