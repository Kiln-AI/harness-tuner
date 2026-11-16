# Against the project overview (sections 9 and 10)

Research date: 2026-11-10. Baseline:
`specs/projects/biological_bootloader/project_overview.md`.

## Confirms

- **"Anything that can be code must be code"** and the four-tier cost order.
  LLMs locate errors in long traces badly: TRAIL best 11%, Who&When best
  14.2% step accuracy ([code-versus-llm.md](./code-versus-llm.md)). Gemini
  CLI ships the same order for loops: code every turn, LLM only after 30
  turns.
- **Very large tool results are a red flag.** Claude Code warns at 10,000
  tokens and caps at 25,000 tokens of MCP output; Codex's fallback truncates
  at 10,000 bytes; Anthropic's tools post gives 25,000 tokens.
- **Very long tool descriptions.** Claude Code cuts up-front tool
  descriptions at 4,096 characters, so the overview's 27,000-character case
  is mostly not seen by the model in that harness.
- **Small skills and many skills.** The Agent Skills spec puts metadata at
  "~100 tokens" per skill, always loaded. Claude Code's skill listing budget
  is 1% of the context window and drops descriptions when it overflows, and
  `/skill-doctor` flags never-invoked skills by context cost. This supports
  both overview examples (the 27-word skill; thousands of skills).
- **Some flags block everything.** Context rot (Chroma, 18 models) and the
  root-cause cascade (AgentDebug; Husain's "first failure") support the
  300,000-token example and the stack-rank idea in section 10.
- **"The tuner does not think to ask."** Anthropic: "Analyze your tool
  calling metrics. Lots of redundant tool calls … lots of tool errors for
  invalid parameters…". Practitioners compute these explicitly; they do not
  wait for the reader to think of them.

## Contradicts or qualifies

- **"Longer tool description" is not one-directional.** The overview treats
  length as a too-long flag only. 97.1% of 856 real MCP tool descriptions
  have at least one smell, mostly missing purpose and usage guidance; fixing
  them gave +5.85 points median success but +67% steps and regressions in
  16.67% of cases ([arXiv 2602.14878](https://arxiv.org/html/2602.14878v3)).
  The static check must flag too short as well as too long, and the fix is an
  experiment.
- **No source supports a prompt-length threshold.** The "100,000 characters"
  example is plausible but has no published number behind it. IFScale
  (instruction count) is the nearest evidence.
- **A zero cache hit rate is not always a flag.** Short prompts can be below
  the provider minimum (512 to 4,096 tokens on Anthropic models), and some
  providers do not report `cached_tokens`.
- **"Unused tool" can mean a poor description, not a useless tool.**
- **Exact repeated calls are not always wrong** (OpenHands excludes some
  cases). The flag must state the fact, not the verdict.

## Missing from the overview

1. **The tuner already computes most raw trace counts.**
   `gateway/trace_files.py` writes per-item and per-run-config stats (turns,
   calls, errors, per-tool result sizes and max, chars by role, system
   chars, per-turn tokens, cached tokens, latency). `probes.py` runs a code
   "reconnaissance battery" before wave 1. Section 9 should build on these,
   not start new. Missing pieces: repeats and cycles, unused tools, cache
   ratio, context growth, cross-item distributions, pass-versus-fail
   comparison, thresholds, and a report handed to the planner up front.
2. **Doctrine conflict.** `analysis.py` says "no verdict labels anywhere". A
   red flag is a label. The spec must say how they fit (proposal: a fact plus
   a reference number plus its source, with no verdict).
3. **Loops and runaway traces** are not in the overview's examples. Step
   repetition is the most frequent MAST failure (15.7%); termination
   failures are 12.4%. Published detectors give thresholds (OpenHands 4
   identical action-observation pairs, 3 identical errors; Gemini CLI 5
   repeats of a cycle).
4. **Tool error classes.** Separate environment errors (401/403/404/429/5xx,
   timeouts) from argument errors. The first blocks everything and is not a
   harness fix (overview 7.7); the second is a tool-description fix.
5. **Frequent tool-call sequences.** Code can count n-grams of tool names.
   This is the evidence for the overview's roll-up tool (6.3, 7.5) and needs
   no LLM.
6. **Cache hit rate and cache breakers** (timestamps at the start of the
   prompt, unstable JSON key order, edited history) as code checks.
7. **Pass-versus-fail comparisons.** Every metric is more useful as a
   difference between passing and failing items on the same eval.
8. **"All items fail" as an environment or eval flag**, from Anthropic's
   broken-task rule.
9. **Tool count guidance.** Anthropic: selection "degrades once you exceed
   30–50 available tools"; use tool search at "10 or more tools" or "more
   than 10k tokens" of definitions. The overview prefers a side skill to tool
   search (6.3); these numbers are the trigger for that discussion either
   way.
10. **Failure taxonomies as shared vocabulary** (MAST, TRAIL,
    AgentErrorTaxonomy). TRAIL's Low/Medium/High impact field is the only
    published per-error severity scale.
11. **Error-analysis method** (first failure, categories, counts,
    saturation) for the tier-4 LLM pass.
