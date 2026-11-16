# Decision models for classification (Jev, Clef, OpenAI Decisions, Kev and others)

Research date: 2026-11-10. Overview baseline: section 7.4. The category is
eight weeks old. Nearly all numbers come from vendors.

## Summary

- The category is real and moves fast. Jev (TypeSafe AI) launched
  2026-09-15/16. Within about three weeks: OpenAI Decisions API (GPT-6 Luna,
  preview 2026-09-29), Cloudflare Clef and Clef-flash (open source, Apache
  2.0, 2026-10-01), AWS Strands Decider 2B, Perplexity pplx-decider-v1-27b,
  Stanford/NVIDIA CLM-8B, Microsoft-Decision-1 (2026-10-09), and open clones
  (Kev, SemIf, Laya, jevlike).
- "Luna Decisions" in the overview is very probably the **OpenAI Decisions
  API on GPT-6 Luna**. I found no product with the exact name "Luna
  Decisions". "Kev" is an open-source community clone of the Jev interface
  (9B/4B/0.8B), not a vendor product.
- The **Jev API is a de facto interface.** Clef is "fully Jev-API
  compatible". A Kiln integration can target one request shape and swap
  models.
- **Accuracy is not free.** On TypeSafe's own workflow evals, Jev agrees
  67.8% with frontier-model labels against 74.1% for GPT-5.6 Sol and 73.1%
  for Claude Opus 5. Independent tests: ahead of an LLM on three of four
  300-item sets, behind on 77-way BANKING77. Speed and price gains are large
  (vendor claims 20–200x faster, 40–400x cheaper).
- **Decision models need input transforms.** The vendor's own list of weak
  points includes arithmetic, dates, indirection, irrelevant context and
  contradictions. The fixes are code-side: trim the state, compute numbers
  and dates first, one hop per question. This links 7.4 and 7.3 directly.

## The interface

From benchlm.ai, 2026-09-22
([benchlm](https://benchlm.ai/blog/posts/what-is-jev)), and the arXiv
ecosystem study ([2609.30216](https://arxiv.org/html/2609.30216v1)):

- Request = **state** (text or JSON) + typed **questions**.
- Question types: **Choice** (up to 255 options, each with written
  criteria), **Score** (a rubric of 2–10 levels, fractional positions),
  **Noul** (yes/no probability). Choice and Score also return a
  distribution and a separate confidence value.
- Single parallel pass, no text generation, schema always matched.
- Jev 1.13 limits: 64,000 tokens across state and questions; 32,000 for
  state plus the longest question; text only. (Jev 1.14 adds image "Box"
  questions, experimental, per
  [pillitteri.it](https://pasqualepillitteri.it/en/news/20652/ai-decision-models-run-locally).)
- Price: $0.042 per million input tokens, no output charge (read
  2026-09-22). DataCamp reports TypeSafe says the price may be subsidised
  (via pillitteri.it).
- Clef request example (Cloudflare,
  [blog](https://blog.cloudflare.com/clef-decision-models/)): `state` plus
  `questions` with `type: noul | choice | score`, `instructions`,
  `criteria`.

## Measured numbers

| Source | Result |
| --- | --- |
| TypeSafe launch (via benchlm) | 70–500 ms end to end vs 3–329 s for frontier models; 0.4 s/case vs 78.1 s for Sonnet 5; $0.0004/case vs $0.1761 for Opus 5. Agreement with frontier labels 67.8% (Jev) vs 74.1% (GPT-5.6 Sol). Vendor says gains are "toward the upper end" and it designed the workflows. |
| Aman Kumar (via benchlm) | Four 300-item sets: Jev ahead of GPT-5.6 Luna on three, behind on BANKING77; median latency under 1 s vs 1.4–5 s. |
| Phishing study (via benchlm) | Direct verdict 62.6% on 2,000 synthetic emails. Logistic regression on five Jev signals: 95.0%. Regex baseline: 91.8%. **Decomposition into several questions plus a small learned combiner beat the direct question.** |
| Cloudflare (vendor) | BANKING77 macro-F1: Clef 94.20, Clef-flash 90.93, Jev 79.74, Kev 9B 84.83. CLINC150+OOS: Clef 97.43, Jev 89.27. Median latency: Clef 209 ms, Clef-flash 39 ms, Jev 524 ms, Kev-9B 51 ms. Clef 64k context. |
| MindStudio hands-on, 2026-09-18 ([link](https://www.mindstudio.ai/blog/jev-system-one-model-classification)) | Eight cases. Correct negation handling (refund probability 98% → 3%). With no "other" option, an off-topic question was forced into "sales" at confidence 0.31. Resisted one simple injection. 92–214 ms model time. |

## When to use (sources and inference)

Use for: routing, intent, moderation, triage, guardrails, yes/no checks on
an LLM's output, "pick the right span from candidates my code extracted",
and map-reduce tagging over many rows (metavert.io,
[link](https://metavert.io/decision-models); benchlm). Calibrated
probabilities allow a **confidence cascade**: act above a threshold, send
the rest to an LLM or a human. Thresholds must be tested per task;
confidence is a training goal, not a guarantee.

Do not use for: free text (agrees with overview), arithmetic, date
comparison, long mostly-irrelevant state, unknown label sets.

Harness rules from the vendor's "jagged edges" list (benchlm, verbatim
column "What it costs you"):

- "Implied context is ignored. Write the criteria out"
- "Do not ask it to count, convert or compare magnitudes"
- "Parse dates in code and ask it only which span is the deadline"
- "One hop per question"
- "Trim the state before you send it"
- "Enforce permissions in code"

Always include an "other"/"unknown" option (MindStudio).

## Implications for the tuner (inference)

1. For a single-turn task with a fixed label set, a decision model is a
   candidate in the **model** search, not only a harness lever. It changes
   cost by about two to three orders of magnitude, so even a few points of
   accuracy loss can be on the frontier.
2. Its harness is different: criteria per option replace the system prompt;
   input transforms matter more; several sub-questions plus code can beat
   one direct question.
3. A hybrid is likely common: decision model first, LLM on low-confidence
   cases. That needs a confidence threshold as a tunable parameter.
4. Use larger eval sets. Vendor numbers disagree by task (Jev wins
   When2Call, loses BANKING77).
5. Kiln: the overview says "more uses are coming … not shipped". I did not
   check Kiln code for decision-model support; that is the Kiln capability
   gap subtopic. Clef is open weights and Jev-compatible, so a single
   adapter could cover several providers.

## Confirms, contradicts, misses

- **Confirms:** 7.4 — very fast, very cheap, one-pass prediction, nothing
  for free text, consider for every classification task.
- **Nuance:** "capable for the price" is right; "capable" alone is not. On
  vendor evals, frontier LLMs still agree more with reference labels.
- **Misses:** the Jev API as a cross-vendor standard; the confidence
  cascade; question decomposition plus a small combiner; the explicit
  input-transform rules; open-weight options (Clef, Kev, Strands Decider)
  for zero-data-retention or on-prem restrictions (overview section 8).
