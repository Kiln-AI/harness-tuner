# RAG against agentic search and skills; which RAG parameters matter

Research date: 2026-11-10. Overview baseline: section 7.1.

## Summary

- **Size threshold.** The only published vendor number: Anthropic says a
  knowledge base "smaller than 200,000 tokens (about 500 pages of material)"
  can go in the prompt with no RAG (Sep 2024, still cited). One benchmark
  reports a crossover near 128k tokens. No source gives a document-count
  threshold for skills against RAG. The overview's "no known number" is
  correct for document count.
- **Skill vs. raw navigation vs. RAG.** A July 2026 controlled study finds
  that progressive disclosure helps only when the harness navigates poorly
  or when the corpus is too large to read (many books). **One level of
  disclosure is enough; a second level never helped and sometimes broke
  accuracy.**
- **Agentic search vs. vector RAG.** Results split by task and harness.
  Grep wins on exact identifiers and conversation memory. Semantic retrieval
  wins on conceptual, cross-cutting and large-corpus queries. Best results
  usually combine both.
- **RAG parameters.** The 2026 factorial study finds that reranking is the
  only component that stays positive across upstream choices. Chunker and
  BM25 fusion effects change sign by dataset and retriever. Other ablations
  disagree on reranking, so test it.

## The threshold question

- Anthropic, "Introducing Contextual Retrieval", 2024-09-19
  ([anthropic.com](https://www.anthropic.com/engineering/contextual-retrieval)):
  if the knowledge base is "smaller than 200,000 tokens (about 500 pages of
  material)", put the whole base in the prompt. Prompt caching makes this
  cheaper. Older than 12 months, but still the reference figure.
- Secondary 2026 summaries
  ([IntuitionLabs](https://intuitionlabs.ai/articles/long-context-ai-vs-retrieval-rag),
  [HackerNoon](https://hackernoon.com/long-context-beats-rag-on-accuracyso-why-keep-retrieval)):
  a benchmark at 32k/128k/1M tokens reports corpus-in-context best below
  ~128k and RAG better above; a June 2026 paper reports long context 73.1%
  vs semantic RAG 65.4% correct at 26x the per-query tokens. I could not
  read either primary paper. Treat as unverified.

*Inference for Kiln:* a skill is a third option between "all in prompt" and
"RAG". The relevant measure is tokens, not document count. Under ~100–200k
tokens, test "all in a cached prompt" first. Above that, test a one-level
skill and a RAG config.

## Progressive disclosure (skills) vs raw navigation vs hybrid RAG

He et al., "Is Progressive Disclosure All You Need for Long-Context
Agents?", arXiv 2607.17598, July 2026
([arXiv](https://arxiv.org/pdf/2607.17598)). Three harnesses, three model
families, ∞Bench, with a hybrid retrieve-and-rerank baseline. Quotes from the
abstract:

> "On a single book, the gain depends on the harness, running large when the
> agent navigates the raw document poorly but near zero when a strong agent
> harness already locates and reads the right passages on its own. When
> scaling up to tasks that span many books, raw-document navigation collapses
> while one-level progressive disclosure degrades more slowly and pulls
> ahead. A second, deeper routing level never helps and sometimes breaks
> accuracy outright, so one level is enough. Progressive disclosure buys
> context, not intelligence".

Example: the hierarchical pack dropped En.MC from 0.9126 to 0.6398 on one
harness/model pair.

This contradicts one part of the overview: 7.1 says a top model that
"designs the progressive-disclosure hierarchy once will probably beat
arbitrary chunks and vectors". The study shows that deep hierarchies can
hurt, and that a plain one-level index captures the gain. (The
"instructions and context" subtopic owns skill design; this is the
retrieval angle.)

## Agentic search vs vector retrieval

| Source (date) | Setting | Result |
| --- | --- | --- |
| Cursor, 2025-11-06 ([blog](https://cursor.com/blog/semsearch)) | Coding agent, own embedding model, grep + semantic | "on average 12.5% higher accuracy … (6.5%–23.5% depending on the model)"; retention gain 2.6% on codebases of 1,000+ files; "the combination of these two leads to the best outcomes" |
| Claude Code (Cherny, 2025; via [officechai](https://officechai.com/ai/claude-researcher-explains-how-agentic-search-performed-better-than-rag-for-code-generation/)) | Dropped vector RAG for grep/glob | agentic search "outperformed everything else by a lot"; "mostly vibes" plus internal benchmarks |
| Sen, arXiv 2605.15184, 2026-05 ([arXiv](https://arxiv.org/html/2605.15184v1)) | LongMemEval, 116 Qs, several harnesses | inline grep beat inline vector for every harness–model pair (e.g. 86.2 vs 62.9); "changing the harness shifts the ceiling by roughly as much as swapping retrievers"; file-based (programmatic) delivery reshuffles the ranking |
| arXiv 2608.01507, 2026-08 ([arXiv](https://arxiv.org/html/2608.01507)) | SWE-QA, read-only repo questions | semantic search 65.2% vs sub-agent grep search 46.2%, at less than half the cost per correct answer |
| Ayles, 2026-04-07 ([blog](https://www.mikeayles.com/blog/rag-coding-tools/)) | 60 queries, ~200-file repo | recall a wash (0.919 vs 0.939); RAG cut tokens 23%; RAG helped cross-cutting queries and hurt refactoring breadth queries (0.889 → 0.689 on one tool) |
| Subramanian, arXiv 2602.23368 ([arXiv](https://arxiv.org/html/2602.23368)) | Doc QA, keyword-search agent vs RAG | agent reaches ">90%" of RAG metrics with no vector DB; better on FinanceBench (30.4 vs 24.2) |

Pattern (inference): exact-match queries (IDs, codes, names) favour lexical
search. Paraphrase and "find everything about X" favour semantic search. The
harness and how results are delivered matter as much as the retriever.

## Which RAG parameters matter most

1. **Reranking.** "RAG Components Are Not Independently Rankable",
   DocInsights 2026 ([ACL Anthology](https://aclanthology.org/2026.docinsights-1.22.pdf)),
   full factorial over chunking, retrieval, fusion and reranking on five
   benchmarks: "reranking is the only intervention we test that remains
   beneficial across upstream configurations … in all 240 dataset × chunker
   × retrieval-family × reranker cells". Anthropic (2024) also reports
   failure rate 5.7% → 1.9% with contextual embeddings + contextual BM25 +
   reranking (−67%), against −49% without reranking.
   **Counter-evidence:** Beconis, 2026-10-07
   ([blog](https://sbeconis.com/writing/hybrid-search-and-reranking-measured/)),
   three BEIR sets: reranking helped one, hurt two, at >100x the retrieval
   latency. A 10-question ablation that found a "double-edged" reranker
   reversed at 100 questions, where the reranker won on every metric
   ([blog.tomting.com](https://blog.tomting.com/2026/08/15/rag-ablation-bm25-chunksize-reranker/)).
   Lesson: small eval sets mislead on RAG settings.
2. **Hybrid (BM25 + dense) fusion — conditional.** The DocInsights study:
   fusion helps on three datasets and harms on two (FinanceBench −5.92,
   LegalBench-RAG −2.85); pooled +0.91 with a CI spanning zero. Beconis: RRF
   fusion "never improved ranking quality" over a strong embedding model. It
   helps when queries contain identifiers, codes or rare terms
   ([hybrid-rag-ablation](https://github.com/jie-gittttt/hybrid-rag-ablation):
   dense-only returned nothing on 3 of 8 acronym/figure questions).
3. **Embedding model.** Beconis: "A strong embedding model on its own was
   the best system on two of the three datasets". Cursor trained its own.
4. **Chunking — interacts with the retriever.** DocInsights: semantic vs
   fixed-512 chunking costs 2.6 points with dense retrieval and gains 2.7
   with late interaction. Alibaba Cloud, 2026-08-17
   ([blog](https://www.alibabacloud.com/blog/how-accurate-is-your-rag-benchmarking-18-configurations_603461)):
   chunk size "is the biggest lever" (256 → 1024 words lifted recall 0.867 →
   0.950), but notes part of this is a retrieved-token-budget artifact. A
   2025 study (arXiv 2505.08445) found fixed 1024-token chunks beat semantic
   breakpoints. tomting: a chunk-size grid gave no gain.
5. **Top-k.** Anthropic found 20 chunks best of 5/10/20. Alibaba: recall
   flattens by k=10.
6. **Contextual retrieval** (prepend an LLM-written context line to each
   chunk before embedding and BM25): −35% failures from contextual
   embeddings alone (Anthropic 2024).

**Suggested order (inference):** (a) Does RAG help at all against
all-in-prompt or agentic search? (b) Search type (dense / BM25 / hybrid) by
the query kind seen in failures. (c) Reranker on/off. (d) Top-k. (e) Chunk
size, only with the retrieved-token budget held equal. (f) Embedding model.
Extraction stays fixed (agrees with overview 7.1).

## Confirms, contradicts, misses

- **Confirms:** RAG is overused for small corpora; "first test whether RAG
  helps at all"; extraction tuned once; the reverse case (skill too large →
  RAG) — supported by the many-books result in 2607.17598.
- **Contradicts in part:** a deeper hierarchy is not better. One level of
  disclosure is enough in the only controlled study.
- **Misses:** (1) **reranking**, the most consistently positive RAG
  component, which Kiln supports (`RagConfig.reranker_config_id`,
  `RerankerConfig` in `libs/core/kiln_ai/datamodel/`); (2) top-k; (3)
  contextual retrieval; (4) the token threshold (~200k) as a better measure
  than document count; (5) lexical search tools as a third option; (6) eval
  size — RAG settings need larger eval sets than 10 items.
