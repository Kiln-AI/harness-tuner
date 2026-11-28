---
title: Test that RAG helps first
description: Before any tuning of the chunker or the search type, build one plain RAG config from the parts that exist and measure whether a search tool helps at all.
reviewed: 2026-11-25
status: active
levers:
  - rag
applies_to: all
cost: medium
---

# Test that RAG helps first

## When to use

- The choice of form says RAG: the documents are too large for the prompt
  or for a skill ([RAG or a skill](rag-or-a-skill-for-a-knowledge-source.md)).
- The task has no RAG tool, or none over these documents.
- The project's document library holds the documents, and an extractor
  config of the project has extracted them (`list_rag_parts`,
  `extracted_documents`).

## When not to use

- **A RAG config over these documents is already attached.** RAG already
  helps or does not: read its traces, then
  [tune the search type and the chunker](tune-the-search-type-and-the-chunker.md)
  or [add a reranker](add-a-reranker.md).
- **The documents are not extracted.** The tuner starts no extraction. Ask
  the owner, in the report, to run it.
- **Do not tune the extractor.** Extraction is done once. Its cost is high
  and it is far from the answer. It is a question only for a project whose
  goal is the RAG pipeline itself.
- **Do not start with a grid** of chunkers and search types. When RAG does
  not help, every arm of the grid is spend for no result.

## Alternatives

- [Attach the knowledge source](attach-the-knowledge-source.md), when a
  RAG config over the documents exists and the run config does not attach
  it. That is a cheaper first step: no new config.
- [RAG or a skill for a knowledge source](rag-or-a-skill-for-a-knowledge-source.md),
  when the size of the documents does not settle the form yet.

## How

One arm, one new [RAG config](../levers/rag.md), made from the parts that
exist where it can be, with settings that are common defaults.

1. **Reuse what is indexed.** In `list_rag_parts`, take the extractor
   config with the most `extracted_documents`. When a chunker and an
   embedding config of the project are used by a RAG config, take them:
   the index then costs nothing.
2. **Otherwise use plain defaults.** A `fixed_window` chunker of about 512
   tokens with about 10 to 15 percent overlap. An embedding model that
   `list_rag_parts` marks `allowed` and `suggested_for_chunk_embedding`. A
   `lancedb_hybrid` search, which works for exact terms and for
   paraphrases. A `similarity_top_k` that gives a few thousand tokens of
   chunks in all ([size the retrieved context in tokens](size-the-retrieved-context-in-tokens.md)).
   No reranker yet.
3. **Index only what the task needs.** When the library holds documents of
   other topics, set `tags` to the documents of this task. A smaller index
   is cheaper, and the search has fewer wrong chunks to return.
4. **Write the tool's name and description for the model.** The name says
   what it searches (`search_care_guide`). The description says what the
   documents hold and when to call the tool, in at most 128 characters.
5. **Test the search.** Call `rag_search` with two or three questions in
   the form that the agent would ask. The chunks must hold the facts. When
   they do not, the documents or the extraction do not have the facts, and
   no setting will fix it: stop, and record why.
6. **Attach it on a new run config**, with `technique: rag`. A prompt line
   that says when to search is part of the hypothesis. Measure it against
   the parent, on the evals that the missing facts touch.

## Check that it took effect

The loop checks that the search tool is called in the train traces, and
ends the arm as "lever not applied" when it never is. Before that, smoke
on two or three train items that failed for lack of a fact: find the
search call, the chunks, and the fact in the answer.

## Budget

Medium cost: one index (free when the parts are shared) and one
measurement. It is the first RAG step on any budget. On a small budget, it
can be the only one: keep the result, and leave the tuning of the parts to
a later run.

## If it does not move the evals

It must move an eval. When the search is called, the chunks hold the
facts and the answers do not improve, the agent answers past the source:
try [answer only from the source](answer-only-from-the-source.md) on the
same config. When the chunks do not hold the facts, the problem is in the
documents, and tuning will not fix it. Record "RAG does not help" with the
reason, so that the next planner does not tune a dead lever.

## Priors

None yet.

## Confidence

Medium. The order (first whether RAG helps, then its parts) is common
field practice and is in the owner's design notes. Public factorial
studies show that the best chunker and search type change with the
dataset, so a tuned setting from another project is a weak guide. The
default values in step 2 are common starting points, not measured optima.
