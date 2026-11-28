---
title: Tune the search type and the chunker
description: When a RAG tool helps but its searches miss, change one part at a time - the search type for the kind of query, then the chunk size with the retrieved tokens held equal.
reviewed: 2026-11-25
status: active
levers:
  - rag
applies_to: all
cost: medium
---

# Tune the search type and the chunker

## When to use

- A RAG tool is attached, and a measurement shows that it helps.
- The traces of failed items show searches that miss: the agent calls the
  tool, and the chunks it gets do not hold the fact, while a document of
  the library does.
- The kind of miss is clear. For example: a query that names an exact
  code returns chunks about something else (a search type problem), or the
  fact is cut in two across chunks (a chunk size problem).

## When not to use

- **RAG has not been shown to help.** Do that first:
  [test that RAG helps first](test-that-rag-helps-first.md).
- **The chunks hold the fact and the answer is still wrong.** The search
  works. See [answer only from the source](answer-only-from-the-source.md).
- **The right chunk is found, but too low in the list.** A reranker is the
  better first change: [add a reranker](add-a-reranker.md).
- **The train set is small.** A RAG setting that wins on a few items often
  loses on more. With under about 30 items that touch the documents, one
  change at a time is the most that the evals can tell apart.

## Alternatives

- [Add a reranker](add-a-reranker.md), when the right chunks are in the
  candidates but not at the top.
- [Size the retrieved context in tokens](size-the-retrieved-context-in-tokens.md),
  when the search finds the right chunks but returns too many or too few.

## How

Each arm is one new [RAG config](../levers/rag.md) that changes one part
of the parent's config, and keeps the others. Keep the extractor.

1. **The search type, by the kind of query.** Read the queries that the
   agent sends in the traces, and the chunks that came back.
   - Queries that name ids, codes, product names or rare terms, and get
     chunks about something near: try `lancedb_fts` or `lancedb_hybrid`.
   - Queries in other words than the documents, and get nothing useful
     from a full-text search: try `lancedb_vector` or `lancedb_hybrid`.
   A new search type on the same extractor, chunker and embedding needs
   no new index: it is cheap.
2. **The chunk size, with the retrieved tokens held equal.** When the
   chunks cut the fact in two, try a larger `chunk_size`; when each chunk
   holds much that is not relevant, try a smaller one. Change
   `similarity_top_k` at the same time so that `chunk_size` times
   `similarity_top_k` stays about the same. Otherwise the arm measures
   more context, not a better chunk.
3. **A semantic chunker** is a third choice, after a fixed size. It costs
   a second pass of the embedding model at index time, and public results
   on it are mixed.
4. **The embedding model** last. It needs a full new index of the
   documents, and the model policy must allow it.

Name the part and the reason in the hypothesis: "hybrid search: 4 of 6
failed queries name a part number".

## Check that it took effect

Call `rag_search` on the queries of the failed traces, on the parent's
config and on the new one. The new one must return the missing fact on
most of them. Then the loop measures the arm.

## Budget

Medium cost. A new search type or reranker on shared parts is cheap. A new
chunker or embedding needs a new index, which Kiln does not price. On a
small budget, do step 1 only.

## If it does not move the evals

It depends. When `rag_search` shows the new config finds the facts that
the old one missed, and the score does not move, the misses were not the
cause of the failures: keep the simpler config. When `rag_search` shows
no change, the arm tested nothing: drop it.

## Priors

- **Five public RAG benchmarks, in a full factorial study of chunkers,
  retrievers, BM25 fusion and rerankers, with equal context budgets.**
  The better chunker depended on the retriever, and BM25 fusion helped on
  three datasets and hurt on two. One study, not a rule.
  Source: public, Vijaya Kumar et al., "RAG Components Are Not
  Independently Rankable", Workshop on Document Intelligence and
  Understanding (ACL Anthology 2026.docinsights-1.22). 2026-10-24.
- **Three BEIR datasets, a dense retriever against a BM25 and dense hybrid
  with rank fusion.** The hybrid never improved ranking quality over the
  dense model. One sample, not a rule.
  Source: public, Beconis, "Hybrid search and reranking, measured",
  sbeconis.com. 2026-10-07.

## Confidence

Medium on "one part at a time, with the retrieved tokens held equal": it
follows from the public studies above, where the effect of each part
changes sign with the dataset. Low on any one setting being better in
general: the evidence says no setting is.
