# Which problems block others: input to the stack rank

Research date: 2026-11-10.

**No source publishes a stack rank of harness fixes.** I searched for one
and found only general advice. The order below is my synthesis. Each step
cites the evidence that supports its position. Treat it as a draft for the
overview's section 10, with the same caveat: there is no universal order,
and experiments win.

## 1. Principles from sources

1. **Upstream errors cause downstream errors.** "focus on noting the first
   failure observed in a trace, as upstream errors can cause downstream
   issues" ([Husain and Shankar, 2025-06-27](https://hamel.dev/blog/posts/evals-faq/why-is-error-analysis-so-important-in-llm-evals-and-how-is-it-performed.html)).
   "a single root-cause error propagates through subsequent decisions"
   ([AgentDebug, arXiv 2509.25370](https://arxiv.org/abs/2509.25370)).
2. **Design causes show up as execution failures.** MAST: FC1 failures
   "occur during execution but often reflect flaws in pre-execution design
   choices" ([arXiv 2503.13657](https://arxiv.org/abs/2503.13657)).
3. **A broken measurement hides everything.** "a 0% pass rate across many
   trials … is most often a signal of a broken task"; CORE-Bench went from 42%
   to 95% after grading and scaffold fixes
   ([Anthropic, 2026-01-09](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)).
4. **Count, then fix the biggest category.** "80% of the failures are going
   to be explained by a small number of categories"
   ([Husain video](https://www.youtube.com/watch?v=h30mams2WaI)).
5. **Context is a finite resource with diminishing returns.** Performance
   drops as input grows, on all 18 models Chroma tested
   ([Chroma, 2025-07-14](https://www.trychroma.com/research/context-rot);
   [Anthropic, 2025-09-29](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).
   So a flooded context degrades every other behaviour, which supports the
   overview's example of a 300,000-token result blocking all small fixes.
6. **Caching changes the cost of every later experiment.** Cached input is
   0.1x the base price on Anthropic
   ([prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching));
   Manus calls cache hit rate "the single most important metric"
   ([Manus, 2025-07](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)).
   A cache breaker is cheap to fix and affects the cost objective of every
   later measurement. It does not block quality work.

## 2. Draft order (inference, with evidence per step)

| Rank | If this is broken | Do not tune yet | Detect with | Evidence |
|---|---|---|---|---|
| 0 | Environment and eval: tool calls fail on 401/403/404/5xx; every item fails a target eval; stale staging data | anything | code: error classes, 0% pass | TRAIL system-execution branch; Anthropic broken-task rule; overview 7.7 |
| 1 | Context flood: a tool result or prompt so large that it crowds or overflows the context; context-window errors | prompt wording, skills, settings | code: result size, peak input share of window, context-window errors | Chroma; Claude Code 10k/25k limits; overview section 9 |
| 2 | Loops and runaway traces: repeated identical calls, very long traces, no final reply | prompt detail, hyperparameters | code: repeats, cycles, turns p95 | MAST FM-1.3 (15.7%), FM-1.5 (12.4%); OpenHands and Gemini CLI detectors |
| 3 | Tool-surface errors: high argument-error rate; tool count or definition tokens far above guidance; unused tools; frequent fixed call chains | prompt instructions about tools; model swap | code: errors by tool, counts, n-grams; LLM: description rubric | Anthropic tools post; tool search docs (30–50 tools); MCP smell papers |
| 4 | Missing context: the agent lacks a fact that a tool could give (overview 7.5) | instruction tuning for that behaviour | LLM on failing traces at flagged positions | overview 7.5; MAST FM-2.2 |
| 5 | Task specification: prompt does not ask for what the eval wants; instruction non-compliance | settings, model scan | LLM tier 3 and 4 | MAST FM-1.1 (+9.4% from role spec alone); TRAIL output-generation errors ~42% |
| 6 | Output format: invalid JSON, schema failures, truncation | nothing else blocked, but scores are noise until fixed | code: parse, finish reason | TRAIL formatting errors; tuner recon battery |
| 7 | Cost and latency hygiene: cache breakers, low cache hit rate | nothing for quality; fix before cost/speed tuning | code | Manus; Anthropic caching docs |
| 8 | Hyperparameters | — | — | overview 6.4 step 4 |

Notes on the order:

- Rank 6 (format) may belong at rank 1 for a structured-output classifier,
  because a parse failure scores as a wrong answer on every eval. Inference.
- Rank 3 before rank 5: Anthropic says redundant calls and argument errors
  point to tool design. A prompt change cannot remove a 100,000-token list
  result. This matches overview 6.3 ("often the largest win").
- The model lock (overview 6.4 step 1) comes before all of this. A model with
  0% propensity looks like rank 0. The detectability check in Harness Tuner
  (`docs/architecture.md` section 5.1) covers part of this.

## 3. What would test the order

The order is a hypothesis. The tuner's own runs can test it: record, per
promoted win, which red flags were present on the baseline, and whether the
win removed one. That data belongs in the state folder's `reports/`, not in
`agent_files/` (repository rule in `AGENTS.md`).
