---
title: Add a reranker
description: Put a reranker model on a RAG config - search for more candidates, keep the best few - when the right chunk is found but not near the top of the results.
reviewed: 2026-11-25
status: active
levers:
  - rag
applies_to: all
cost: low
---

# Add a reranker

## When to use

- A RAG tool is attached and helps, and its config has no reranker.
- The traces show that the right chunk is in the results but low in the
  list, or that a larger `similarity_top_k` finds it and the default does
  not.
- `list_rag_parts` shows a reranker model with `allowed: true`.

## When not to use

- **The right chunk is not in the candidates at all.** A reranker orders
  what the search found. It does not find more. See
  [tune the search type and the chunker](tune-the-search-type-and-the-chunker.md).
- **The objective is speed**, and a search is on the path of most items.
  A reranker call adds latency that the loop does not measure: the latency
  of a run counts its model calls only.
- **No reranker model is allowed** by the model policy.

## Alternatives

- [Tune the search type and the chunker](tune-the-search-type-and-the-chunker.md),
  when the search misses the right chunk entirely.
- [Size the retrieved context in tokens](size-the-retrieved-context-in-tokens.md),
  when the right chunk is in the results and the agent gets too much text
  around it.

## How

One arm, one new [RAG config](../levers/rag.md): the parent's config with
a reranker.

1. Keep the extractor, the chunker, the embedding and the search type of
   the parent's config: reuse their ids. The index then costs nothing.
2. Set a larger first search, with a new search part: `similarity_top_k`
   about three to five times the number of chunks that the agent should
   get. The reranker needs candidates to choose from.
3. Add `new_reranker` with a model that `list_rag_parts` allows, and
   `top_n` equal to the number of chunks that the agent should get. The
   retrieved tokens are then the same as the parent's, and the arm measures
   the order, not more context.
4. Test with `rag_search` on the queries of two or three failed traces:
   the right chunk must now be in the `top_n`.

## Check that it took effect

`rag_search` on the new config returns the reranked chunks. Compare them
with the parent config's chunks on the same queries: the order must
differ, and the right chunk must move up on most of them.

## Budget

Low cost: no new index, one reranker call per search. Kiln does not price
the reranker call, so it is not in any cost figure. It is a good second
RAG step on any budget, right after RAG is shown to help.

## If it does not move the evals

It depends. When the right chunk was already first on most failed items,
the order was not the problem: drop the reranker, it adds a call to each
search for nothing. When the reranker moved the right chunk up and the
score did not move, the agent does not use the chunk: see
[answer only from the source](answer-only-from-the-source.md).

## Priors

- **Five public RAG benchmarks, in a full factorial study of chunkers,
  retrievers, BM25 fusion and rerankers, with equal context budgets.**
  Reranking was positive in all 240 dataset, chunker, retrieval-family and
  reranker cells: the only part that helped in every setting. One study,
  not a rule.
  Source: public, Vijaya Kumar et al., "RAG Components Are Not
  Independently Rankable", Workshop on Document Intelligence and
  Understanding (ACL Anthology 2026.docinsights-1.22). 2026-10-24.
- **A retrieval benchmark over several knowledge domains, with contextual
  embeddings and contextual BM25.** Adding a reranker took the top-20
  retrieval failure rate from a 49% reduction to a 67% reduction (5.7% to
  1.9%). It added a small latency. One sample, not a rule.
  Source: public, Anthropic, "Introducing Contextual Retrieval". 2024-09-19.
- **Three BEIR datasets, dense retrieval with and without rerankers.**
  The largest reranker helped on one dataset and hurt on two, at more than
  100 times the latency of the dense search (about 540 ms against 4 ms per
  query). One sample, not a rule.
  Source: public, Beconis, "Hybrid search and reranking, measured",
  sbeconis.com. 2026-10-07.

## Confidence

Medium to high that a reranker helps retrieval more often than not: the
largest public study found it positive in every cell, and a vendor's
benchmark agrees. One smaller public test disagrees on two of three
datasets, so measure it. Low on its latency cost being small: it depends
on the model and the provider.
