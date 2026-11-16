# What code can do, and what needs an LLM

Research date: 2026-11-10.

## 1. The evidence that LLMs are weak at reading whole traces

| Study | Task | Best result |
|---|---|---|
| TRAIL ([Patronus, 2025-05/06](https://www.patronus.ai/research/trail)) | locate and categorise errors in 148 long traces | "Gemini-2.5-pro model scoring a mere 11%" joint accuracy; "context handling failures are almost never caught" |
| Who&When ([arXiv 2505.00212](https://arxiv.org/abs/2505.00212), ICML 2025) | name the failing agent and step | 53.5% agent, 14.2% step; "declines as context length increases" |
| MAST ([arXiv 2503.13657](https://arxiv.org/abs/2503.13657)) | classify failure modes | LLM annotator with "high agreement with human annotations" (category level, not step level) |
| Husain and Shankar ([2025-07-24](https://hamel.dev/blog/posts/evals-faq/what-parts-of-evals-can-be-automated-with-llms.html)) | practice guidance | do not hand initial open coding or root-cause analysis to an LLM |

Reading of the evidence (inference): an LLM is reasonable at "what kind of
failure is this" and poor at "where in a 200k-token trace did it start".
Code is exact at "where" for any symptom it can define. The cheap and
accurate design is code to find positions, then an LLM to name the cause at
those positions.

Gemini CLI ships this design for loops: code checks each turn, and an LLM
loop check runs only after 30 turns, with a 0.9 confidence bar and a second
model to confirm
([loopDetectionService.ts](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/loopDetectionService.ts),
read 2026-11-10).

## 2. The split

Tiers follow the overview's section 9 order of cost.

### Tier 1: code on the harness (no trace, no spend)

All checks in [static-harness-checks.md](./static-harness-checks.md)
section 3: lengths, counts, structure, missing fields, cache breakers in the
prompt prefix.

### Tier 2: code on traces in bulk (no model spend)

All metrics in [trace-metrics.md](./trace-metrics.md): result sizes, error
rates and classes, exact repeats and cycles, turns and calls with
distributions, context growth, cache hit rate, unused tools and skills,
frequent tool-call n-grams, final-output parse failures, latency.

Also code, and useful to split pass from fail:

- each metric for passing items against failing items on the same eval (the
  difference is the signal; "failed trajectories are consistently longer",
  [arXiv 2511.00197](https://arxiv.org/pdf/2511.00197));
- first position in each trace of each flag, so a later LLM pass reads only
  the window around it;
- all items fail a target eval: flag as possible task, eval or environment
  problem ([Anthropic, 2026-01-09](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents));
- tool error text classes by regex (401/403/404/429/5xx/timeout).

### Tier 3: LLM on the harness (one call per harness, cheap)

- tool description rubric (purpose, guidelines, limits, parameters, length,
  examples) as in [arXiv 2602.14878](https://arxiv.org/html/2602.14878v3);
- overlapping tools;
- prompt altitude, contradictions, and prompt-versus-eval gaps;
- skill quality against the Anthropic checklist (specific description,
  concrete examples, consistent terms).

### Tier 4: LLM on specific traces (expensive)

- open coding of failing traces, starting at the first code flag;
- axial coding into categories with counts (Husain and Shankar);
- "if it had known that, it would have done the right thing" (overview 7.5):
  needs reading;
- reasoning-action mismatch, clarification failures, verification quality
  (MAST FC2, FC3).

## 3. What code cannot see, by family

From the taxonomies ([failure-taxonomies.md](./failure-taxonomies.md)):
hallucination, tool output misinterpretation, incorrect problem
identification, goal deviation, reasoning-action mismatch, and incorrect
verification. These are TRAIL's "Reasoning" branch and MAST's FC2 and FC3.
For these, code can only point to traces worth reading (failing items,
long items, items with flags).

## 4. Doctrine conflict to resolve in the spec

`src/kiln_harness_tuner/analysis.py`: "Code computes judgment-free numbers;
agents interpret them (no verdict labels anywhere)." A red flag with a
threshold is a label. Inference: the two fit if a red flag is stated as a
fact with its reference number and source ("largest result 410,000 chars;
Claude Code caps MCP output at 25,000 tokens"), and the planner decides what
to do. The spec must decide this explicitly.
