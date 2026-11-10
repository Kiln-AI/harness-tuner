# Compaction, context editing and memory

Research date: 2026-11-10. Overview baseline: 7.9 lists compaction as "known
about, not yet in Kiln". 7.2 mentions "keeping everything in context with
compaction" on frontier models.

## Summary

There are three separate primitives, and the overview names only one:

1. **Tool-result clearing (context editing).** Mechanical, no inference
   cost. Old tool results become a placeholder. Anthropic reports the
   largest measured gains for this one.
2. **Compaction.** A model summarises older turns and the summary replaces
   them. Lossy. 2026 research shows it causes repeated work, replayed
   actions and invented "facts".
3. **Memory / note files.** The agent writes state outside the context and
   reads it back.

Context rot is the reason for all three: quality falls as context grows,
on every model tested.

## Why: context rot

- Chroma, "Context Rot", July 2025, 18 models
  ([trychroma.com](https://trychroma.com/research/context-rot)): performance
  "grows increasingly unreliable as input length grows", even on simple
  tasks with complexity held constant. Distractors and low question–needle
  similarity make it worse (detail via search summary).
- Anthropic, "Effective context engineering for AI agents", 2025-09-29
  ([anthropic.com](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)):
  "Context, therefore, must be treated as a finite resource with diminishing
  marginal returns." Goal: "the smallest possible set of high-signal tokens".
  It recommends "just in time" retrieval by identifiers and a hybrid of
  up-front plus on-demand loading.

## Tool-result clearing (context editing)

Anthropic API, `clear_tool_uses_20250919`, beta header
`context-management-2025-06-27`
([docs](https://platform.claude.com/docs/en/build-with-claude/context-editing)).
Parameters and defaults (verbatim table):

| Option | Default | Meaning |
| --- | --- | --- |
| `trigger` | 100,000 input tokens | When clearing starts (`input_tokens` or `tool_uses`) |
| `keep` | 3 tool uses | Recent tool use/result pairs kept |
| `clear_at_least` | None | Minimum tokens cleared, so the cache break is worth it |
| `exclude_tools` | None | Tools never cleared |
| `clear_tool_inputs` | `false` | Also clear the call parameters |

A second strategy, `clear_thinking_20251015`, clears old thinking blocks.

Cache interaction, quoted: tool result clearing "Invalidates cached prompt
prefixes when content is cleared … Use the `clear_at_least` parameter".

Measured result, Anthropic, 2025-09-29
([anthropic.com/news/context-management](https://www.anthropic.com/news/context-management)):

> "combining the memory tool with context editing improved performance by
> 39% over baseline. Context editing alone delivered a 29% improvement. In a
> 100-turn web search evaluation, context editing enabled agents to complete
> workflows that would otherwise fail due to context exhaustion—while
> reducing token consumption by 84%."

This is a vendor internal eval. I found no independent replication.

The Claude cookbook (2026-03-20,
[link](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools))
calls clearing "the cheapest of the three primitives: no inference cost" and
notes it suits re-callable tools (file reads, searches), because the agent
can call again.

## Compaction

Anthropic server-side compaction, `compact_20260112`
([threshold docs](https://platform.claude.com/docs/en/build-with-claude/compaction-threshold),
[overview](https://platform.claude.com/docs/en/build-with-claude/compaction)):

- Default trigger 150,000 input tokens; "`value` must be at least 50,000
  tokens".
- `instructions`: "Custom summarization prompt. Completely replaces the
  default prompt when provided."
- `pause_after_compaction` lets the client re-insert recent turns.
- On-demand compaction (beta `compact-2026-09-04`) is now the recommended
  mode: "Use on-demand compaction wherever it is available." It can keep
  recent turns word for word and run in the background.
- A third-party doc notes 4.6+ models only; Haiku 4.5 is refused
  ([doc-api mirror](https://git.spboucher.ai/doc-api/blob/main/docs/anthropic/context-management.md),
  unverified secondary).
- The client-side SDK `compaction_control` is deprecated. Its known bug:
  server-side tools inflate `cache_read_input_tokens` and trigger compaction
  early.

So the levers are: **trigger threshold, compaction prompt, whether recent
turns are kept verbatim, and which model writes the summary.**

### Evidence that compaction hurts

- Min et al., "Toward Reliable Context Compression for Long-Horizon Agents",
  arXiv 2608.06503, 2026-08-06
  ([arXiv](https://arxiv.org/html/2608.06503v1)): compression "can weaken the
  influence of recent interactions, increasing blocked actions, repeated
  exploration, and instability across runs." Summaries flatten the ordered
  trajectory, so the agent may "replay completed actions, or continue acting
  beyond the terminal frontier." Their TRACE method optimises the
  compression prompt and improves AppWorld results.
- Min and Chen (reported 2026-10-19,
  [AI Weekly](https://aiweekly.co/alerts/paper-ties-83-of-agent-failures-to-compaction-summaries)):
  in a widely used structured compressor, "83% of failed runs trace back to
  an action that carries out a specific summary entry, most of which no
  environment output supports." A provenance filter (drop summary entries
  with no source) gives +2.1–8.9% cross-run reliability at no token cost.
  Secondary report; the paper itself was not read.
- Other 2026 preprints (search summaries only): KVMem (2609.04852) — the
  summary decides what to keep before future relevance is known; Parallel
  Context Compaction (2605.23296) — summary output is "largely insensitive
  to prompt changes"; CompactionRL (2607.05378) — RL-trained summarising
  helps on SWE-bench Verified and Terminal-Bench 2.0.

### Interpretation (mine)

- Prefer clearing re-fetchable tool results before compacting. It is cheap
  and does not invent content.
- When compaction is used, the compaction prompt is a tunable object: tell
  it to keep only facts with a source, the list of completed actions, and
  the exact next step. Keep the last N turns verbatim.
- Compaction is a multi-turn and long-horizon lever. A single-turn
  classifier never reaches the 50k minimum trigger.
- Each compaction or clear breaks the prompt cache. Fewer, larger edits
  (`clear_at_least`) cost less.

## Memory

Anthropic memory tool (client-side file directory, same 2025-09-29
announcement) gave the extra 10 points (29% → 39%) in the vendor eval. The
context-engineering post also names structured note-taking and sub-agents
as the three long-horizon techniques (compaction, notes, sub-agents).

## Confirms, contradicts, misses

- **Confirms:** 7.9 — compaction is a real lever, with a trigger and a
  prompt to tune.
- **Misses:** tool-result clearing as a separate, cheaper lever with the
  largest published gain; thinking-block clearing; memory files; the
  evidence that compaction summaries cause failures, which makes the
  compaction prompt a target for evals; cache interaction.
- **Nuance on 7.2:** "on frontier models there is a move towards keeping
  everything in context with compaction". Vendor docs support this
  direction (Managed Agents compaction is "always on"). The 2026 papers show
  the cost: compaction is lossy and should be measured, not assumed free.
- **Kiln note:** Kiln does not have compaction (overview 7.9). I did not
  check whether Kiln passes through provider context-editing settings; that
  belongs to the Kiln capability gap subtopic.
