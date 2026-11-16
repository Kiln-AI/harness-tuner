# Benchmarks and Leaderboards for Agentic and Tool-Calling Work

Research date: 2026-11-10. All scores below are snapshots. They are here to show
the *shape* of each source, not to rank models.

## The main rule

A public score tells the tuner which models are worth a screening run. It does
not tell the tuner which model wins on the client's task. No source reviewed
measures how well a public score predicts success on an organisation's own
tasks ([Rillor report, 2026-11-07](https://rillor.com/insights/what-agent-benchmarks-measure/report)).
This agrees with the overview's golden rule: experiments win.

## Credibility table

| Source | What it measures | Why it is credible | Known problems | Use in the tuner |
| --- | --- | --- | --- | --- |
| Artificial Analysis (AA) Intelligence Index v5.0.1 | Composite of 9 evals, 4 categories at 25% each. Agentic items: AA-Briefcase, AutomationBench-AA, FrontierCode 2.0, Terminal-Bench 4.0, Terminal-Bench-Science | Independent. Same harness and settings for all models. 60% of v5 is private or held-out test sets. Reports cost per task, time per task, tokens per task | Index changes version often (v4 Jan 2026, v4.1 Jun, v4.3 Sep, v5 Oct 2026). Scores from different major versions do not compare. One harness (Stirrup) for all models | Primary shortlist source. Also primary source of speed, price and verbosity. Machine-readable (see `machine-readable-sources.md`) |
| AA Coding Agent Index v2.0 | FrontierCode v1.1, Terminal-Bench 4.0, SWE-Atlas-QnA-AA, scored per *agent* (model + harness) | Measures model and harness together | Coding only | Use for coding-agent targets |
| τ-bench family (τ²-bench, τ³-bench banking) | Multi-turn customer-service tool use under policy, with a simulated user | Closest public test to a Kiln multi-turn support agent | Agentic Benchmark Checklist (ABC): a do-nothing agent scores 38% on τ-bench airline. Simulated user made task-critical errors in 13/100 airline conversations (τ²). 50+ tasks fixed Feb 2026. pass@4 is 17.7 to 20.4 points above pass@1 | Use pass^k figures, not pass@1, for reliability claims. Telecom is saturated (98%); banking top is about 55% |
| MCP-Atlas (Scale) | 1,000 tasks (500 private), 36 MCP servers, 220 tools, 3 to 6 calls per task | Half private. Re-scored all models in April 2026 with a new judge and a 100-call budget | Top ten sit within about ±2 points. Third-party aggregators show different numbers for the same model | Good signal for "agent with a moderate tool set" |
| Toolathlon-Verified | 108 tasks, 32 MCP servers, 604 tools | Reports pass@1, pass@3, pass^3, turns and tool calls per task. Verified rewrite June 2026 | New score series, not comparable to old Toolathlon. Small task count (±1 to ±5 points) | Good signal for "agent with a very large tool catalogue". Turns and calls per task are useful cost proxies |
| BFCL v4 (Berkeley) | Function-call accuracy: agentic (40%), multi-turn (30%), live, non-live, hallucination | Long history. Has a *format sensitivity* section | Last update 2026-04-12. Single-turn parts are near saturation | Weak for frontier ranking now. Format-sensitivity finding is still useful: JSON tool docs beat XML and Python for nearly all models |
| SWE-bench Verified | Repo bug fixes | — | OpenAI stopped reporting it (Feb 2026): ≥59.4% of 138 audited hard tasks had flawed tests, and all frontier models could reproduce gold patches | Do not use |
| SWE-bench Pro / Pro Verified | Repo bug fixes, harder | Recommended by OpenAI over Verified | By Oct 2026, 480 of 731 public tasks repaired or retired. Anti-hacking drops one model from 78.8% to 57.3% | Use with care for coding targets only |
| LMArena (Chatbot Arena) | Human preference in chat | Large vote count | "The Leaderboard Illusion" (NeurIPS 2025): private best-of-N testing, unequal sampling. Measures chat preference, not tool use | Do not use for agent selection |
| Aggregator sites (llm-stats, BenchLM, benchmarklist, llmboard) | Collect self-reported and third-party scores | Fast to update | Mix self-reported and independent numbers. Mix metrics in one column (for example, llm-stats GDP.pdf shows 81.6% for one model and 35% for the next, from different metrics). "Estimated" scores for models never tested | Discovery only. Always trace a number to its primary source |
| OpenRouter rankings and `benchmarks` field | Usage share; third-party ranks in the models API | Real traffic | Popularity is not quality | Discovery of which new models are in real use |

Sources: [AA methodology](https://artificialanalysis.ai/methodology/intelligence-benchmarking)
(v5.0.1, read 2026-11-10); [AA v5 announcement, 2026-10-27](https://artificialanalysis.ai/articles/artificial-analysis-intelligence-index-v5);
[AA v4.1, 2026-06-15](https://artificialanalysis.ai/articles/artificial-analysis-intelligence-index-v4-1);
[AA v4.3, 2026-09-07](https://artificialanalysis.ai/articles/artificial-analysis-intelligence-index-v4-3);
[AA Coding Agent Index](https://artificialanalysis.ai/methodology/coding-agents-benchmarking);
[Scale MCP-Atlas](https://labs.scale.com/leaderboard/mcp_atlas);
[Toolathlon leaderboard](https://toolathlon.xyz/docs/leaderboard);
[BFCL v4](https://gorilla.cs.berkeley.edu/leaderboard.html) and
[format sensitivity blog](https://gorilla.cs.berkeley.edu/blogs/17_bfcl_v4_prompt_variation.html);
[OpenAI, "Why we no longer evaluate SWE-bench Verified", 2026-02-23](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/);
[SWE-Bench Pro Verified, arXiv 2609.08149, 2026-09-08](https://arxiv.org/html/2609.08149v1);
[Agentic Benchmark Checklist](https://uiuc-kang-lab.github.io/agentic-benchmarks/);
[The Leaderboard Illusion, arXiv 2504.20879](https://arxiv.org/html/2504.20879);
[LLM-Agent Leaderboard Rank, arXiv 2609.07785, 2026-09-07](https://arxiv.org/html/2609.07785v1).

## Findings that change how the tuner should read a leaderboard

1. **Benchmarks lag new releases.** On 2026-09-24 the September releases
   (Claude Opus 5.5, GPT-6 Astra, Sol and Luna, Gemini 3.8 Flash) had no score
   on MCP-Atlas, τ³-bench or Toolathlon-Verified
   ([fleeceai, 2026-09-24](https://fleeceai.app/blog/best-ai-model-for-tool-calling-2026)).
   The newest model often has only the vendor's own numbers. A guide entry must
   carry an "evidence level" field: vendor-only, independent, or measured by us.
2. **Effort level is part of the model.** AA, Toolathlon and τ³ list each
   model at a named effort (`max`, `xhigh`, `high`). DeepSeek V4.1 Flash 1017
   scores 33 at max and 29 at high on AA v5, with 174M against 134M output
   tokens ([AA model page](https://artificialanalysis.ai/models/deepseek-v4-1-flash-1017)).
   A guide row is a (model, effort) pair, not a model.
3. **More reasoning effort does not always help agents.** On HAL, more effort
   gave equal or lower accuracy in 21 of 36 model-benchmark combinations, and
   two agents differed ninefold in cost for a two-point accuracy gap
   (as cited in the [Rillor report](https://rillor.com/insights/what-agent-benchmarks-measure/report)).
   This supports the tuner's existing thinking-level screen.
4. **Close ranks are noise.** On SWE-bench Verified, 39 of 45 top-10 pairs are
   underpowered comparisons ([arXiv 2609.07785](https://arxiv.org/html/2609.07785v1)).
   MCP-Atlas top ten are within about ±2 points. The guide must group models
   in tiers, not give a strict order.
5. **Model ranking reverses with the harness.** See
   `harness-depends-on-model.md`. On Terminal-Bench 4, Claude leads GPT by 7.94
   points in OpenHands and trails it by 30.16 points in PI
   ([arXiv 2610.00917](https://arxiv.org/html/2610.00917v1)). A leaderboard
   score is for the leaderboard's harness, not for a Kiln harness.
6. **Which benchmark matches which agent class (inference).**
   - Single-turn classifier or extractor: no agentic board matches well. Use
     AA "General" components and a cheap screen on the train split.
   - Single-turn with many tool calls: MCP-Atlas (moderate tool count) and
     Toolathlon-Verified (large tool count).
   - Multi-turn conversational agent under policy: τ³-bench banking.
   - Document-heavy work: GDP.pdf (part of AA v5), with wide intervals (see
     `model-families-and-settings.md`).
   - Coding agent: AA Coding Agent Index, Terminal-Bench 4.0, SWE-Bench Pro.
