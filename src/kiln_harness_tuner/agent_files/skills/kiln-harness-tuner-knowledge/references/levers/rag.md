---
title: RAG configs
description: A RAG config made from parts - chunker, embedding, search type, reranker - over the project's extracted documents, indexed, and attached as a search tool on a new run config.
reviewed: 2026-11-25
gateway_ops:
  - list_documents
  - list_rag_configs
  - get_rag_config
  - list_rag_parts
  - rag_search
  - create_rag_config
  - index_rag_config
  - archive_rag_config
  - create_run_config
---

# RAG configs

## What it is

A RAG config is a record of the project. It indexes the project's
documents and gives the agent one search tool,
`kiln_tool::rag::<rag_config_id>`, with a name and a description that the
model reads. The tool takes one argument, `query`, and returns the chunks
that the search finds, as text. A RAG config has five parts:

- the **extractor**: the model that turned each document into text. The
  tuner uses an extractor config of the project. It never creates one.
- the **chunker**: `fixed_window` (`chunk_size` and `chunk_overlap`, in
  tokens) or `semantic` (it calls the embedding model to find the breaks).
- the **embedding**: the model that turns each chunk and each query into a
  vector.
- the **search type**, a vector store config: `lancedb_fts` (full text),
  `lancedb_vector` or `lancedb_hybrid`, and `similarity_top_k`, the number
  of chunks one search returns.
- the **reranker**, optional: a model that orders the chunks again and
  keeps `top_n` of them.

`tags` limits the config to the documents that carry one of the tags.
With no tags, it indexes every document of the library.

This lever is the RAG config itself: a new one, made from parts. To attach
or drop a RAG config that exists is the lever
[tool_config](tool_config.md).

## How the tuner changes it

1. Read the library: `list_documents` (names, kinds, tags),
   `list_rag_configs` and `get_rag_config` (the configs that exist, with
   their parts), and `list_rag_parts`: each part that a new config can use
   again, with its `id`; for an extractor, how many documents it has
   extracted; the embedding and reranker models, each with `allowed`.
2. Create the config with `create_rag_config`. Give the extractor config's
   id, and for the chunker, the embedding and the search type either the id
   of a config of the project or a new one (`new_chunker`, `new_embedding`,
   `new_search`). The reranker is optional (`reranker_config_id` or
   `new_reranker`). The operation creates the new parts and the RAG config,
   then runs the index and waits for it. The answer gives the `tool_id`,
   the state of the `index`, and the `document_check` line.
3. When `index.complete` is false, read `index.errors`, and call
   `index_rag_config` with the id. Kiln goes on from where it stopped.
4. Test the search with `rag_search` on two or three queries that the
   agent would make. Each search is one embedding call.
5. Create a new run config with `create_run_config`. Copy every property
   of the parent, and add the `tool_id` to `tools_config.tools`. Set
   `technique: rag` on the item.

Rules of the object:

- **A RAG config never changes.** Another chunker, search type, reranker or
  tag filter is a new RAG config. A run config that attaches the old one
  keeps it.
- **A part can be shared.** Kiln keeps the chunks and the embeddings of each
  document for each extractor, chunker and embedding config. A new config
  that uses those three again costs no index. A new search type or reranker
  on the same three parts is free to index.
- **The search tool's name and description** are what the model reads to
  decide when to search. `tool_name` is lower case with `_`, at most 64
  characters. `tool_description` is at most 128 characters.

## Limits

- **No extraction.** The tuner creates no extractor config and starts no
  extraction. A create is refused when a document to index has no
  extraction for the chosen extractor config. To add documents, or to
  extract them, is the owner's work in Kiln.
- **The model policy binds the extractor, embedding and reranker models.**
  The documents and every search query go to them. A model that the policy
  does not allow is refused, unless a RAG config that existed before the
  run uses it in the same role.
- **The index budget.** One create sends at most `index_max_bytes` of
  extracted text to the embedding model. One run creates at most
  `max_created_per_run` RAG configs. `list_rag_parts` gives both numbers.
  Kiln reports no cost for an embedding or a reranker call, so the index
  and each search are not in `--budget-usd` or in any cost that the loop
  measures. A run's latency leaves out the time of a search. So under the
  speed or the cost objective, an arm that adds or replaces a RAG tool
  ends before its measurement ("cost and latency not measured"). An arm
  that keeps its parent's RAG tools is measured.
- **The document check.** A document that holds the whole input of an eval
  item makes the create fail. The gateway also refuses to attach, drive or
  search a config of the project whose documents hold one, and code ends
  an arm that attaches one. The check names documents, never items.
- **The lever check.** An arm that attaches a new RAG tool and whose train
  traces never call it ends as "lever not applied".
- A screen candidate and a probe cannot attach a RAG tool.
- At the end of the run, code archives each RAG config of the run that no
  run config attaches. Nothing is deleted.

## See also

- The `kiln-harness-tuner-gateway` skill: the fields of each operation, and
  the full list of refusals of `create_rag_config`.
- The `kiln-harness-tuner-experiment-build` skill: the build discipline.
- The lever [tool_config](tool_config.md): attach or drop a RAG config that
  exists.
