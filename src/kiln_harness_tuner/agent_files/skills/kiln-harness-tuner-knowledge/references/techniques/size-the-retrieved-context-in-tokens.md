---
title: Size the retrieved context in tokens
description: Set how much text one search returns as a token count - chunk size times top_k, or the reranker's top_n - against the model's context and the task, not as a number of documents.
reviewed: 2026-11-25
status: active
levers:
  - rag
applies_to: all
cost: low
---

# Size the retrieved context in tokens

## When to use

- A RAG tool is attached, and the traces show one of two problems:
  - the search returns so much text that the context fills, the agent
    searches again and again, or it answers from a chunk that is near the
    question and wrong;
  - the search returns too little: the fact the answer needs is in the
    chunk after the last one returned.
- A new RAG config is planned, and its `similarity_top_k` must be set.

## When not to use

- **The chunks returned do not hold the fact at any size.** The search
  type or the chunker is the problem:
  [tune the search type and the chunker](tune-the-search-type-and-the-chunker.md).
- **Do not compare two chunkers at a different retrieved size.** The
  difference in size, not the chunker, then decides the result.

## Alternatives

- [Add a reranker](add-a-reranker.md), when the right chunk is found but
  low in the list: a reranker keeps the size and improves the order.

## How

The size of what one search returns is a token count: `chunk_size` times
`similarity_top_k`, or `chunk_size` times the reranker's `top_n` when the
config has one. A count of chunks or of documents says nothing alone.

1. **Compute the size of the parent's config** from `get_rag_config`, and
   the number of searches per item from the traces. The context that RAG
   adds is about the size times the searches.
2. **Set a target** from the model and the task. It must leave room for
   the prompt, the other tools and the answer. A small model or a short
   context window needs a smaller size. A question that needs facts from
   several places needs more chunks, not larger ones.
3. **Change one number on a new [RAG config](../levers/rag.md)**:
   `similarity_top_k` on a new search part (no new index), or `top_n` on a
   new reranker. Keep the chunk size. State the size before and after in
   tokens in the hypothesis: "from 10 chunks of 512 tokens (about 5,000
   tokens) to 20 (about 10,000 tokens)".
4. When an arm changes the chunk size for another reason, change the
   count with it, so that the size stays the same.

## Check that it took effect

`rag_search` on the new config returns the new number of chunks. In the
smoke traces, the tool results have the new length, and the number of
searches per item falls when the old size was too small.

## Budget

Low cost: no new index for a new `similarity_top_k` or `top_n`. A larger
size adds input tokens to each later model call, which the loop measures.

## If it does not move the evals

It depends. When the traces show the agent now gets the fact and the
score does not move, the size was not the cause: keep the smaller size,
which costs less. When the context still fills, the number of searches is
the problem, not their size: a prompt line on when to search is a second
arm.

## Priors

- **A retrieval benchmark over several knowledge domains.** The authors
  passed 5, 10 and 20 chunks to the model, and 20 chunks gave the best
  results of the three. One sample, not a rule: the best count depends on
  the chunk size and on the model.
  Source: public, Anthropic, "Introducing Contextual Retrieval". 2024-09-19.

## Confidence

High that the size is a token count: it is what the model's context
holds, and a public factorial study held the context budget equal for
this reason (Vijaya Kumar et al., ACL Anthology 2026.docinsights-1.22).
Low on any one target size: it depends on the model, the chunk size and
the task.
